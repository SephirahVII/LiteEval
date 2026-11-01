import asyncio
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from zipfile import ZipFile

from cryptography.fernet import Fernet
from liteeval import connections, migrations, planning, rollouts, runtime, usage
from liteeval.api import dispatch, BinaryResponse, make_handler
from liteeval.adapters import HTTPAdapter
from liteeval.codex import parse_jsonl
from liteeval.process import run_process
from liteeval.runner import ExperimentRunner
from liteeval.security import ACTIVE_SECRETS, register_secret
from liteeval.storage import Store, LeaseLost


class ModelService(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.calls.append((self.path, self.headers.get('Authorization'), body))
        key = self.headers.get('Authorization', '').removeprefix('Bearer ')
        if self.path == '/agent':
            result = {'answer': 'ok', 'usage': {'input_tokens': 10, 'prompt_tokens': 10, 'output_tokens': 4,
                      'input_tokens_details': {'cached_tokens': 3}, 'output_tokens_details': {'reasoning_tokens': 1}},
                      'cost': .01, 'currency': 'USD', 'debug': key,
                      'events': [{'kind': 'tool', 'name': 'search', 'started_at': 10, 'ended_at': 10.2,
                                  'attributes': {'arguments': {'q': 'hello'}, 'debug': key}}], 'trajectory_complete': True}
        else:
            judge_calls = sum(path != '/agent' for path, _, _ in self.server.calls)
            result = {'choices': [{'message': {'content': 'invalid-json' if judge_calls == 1 else json.dumps({'score': 1, 'reason': 'correct'})}}],
                      'usage': {'prompt_tokens': 6, 'completion_tokens': 2, 'completion_tokens_details': {'reasoning_tokens': 1}},
                      'cost': .02, 'currency': 'USD', 'debug': key}
        payload = json.dumps(result).encode();self.send_response(200)
        self.send_header('Content-Length', str(len(payload)));self.end_headers();self.wfile.write(payload)
    def log_message(self, *args):pass


class ConnectionsRolloutsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.path = Path(self.tmp.name)/'test.db';self.store = Store(self.path)
        self.env = patch.dict(os.environ, {'LITEEVAL_SECRET_KEY': Fernet.generate_key().decode()});self.env.start()
        self.secret_token = ACTIVE_SECRETS.set(())
        self.pid = self.store.create_project('Mine')['id'];self.other = self.store.create_project('Other')['id']
        self.principal = {'admin': False, 'project_id': self.pid}
        self.did = self.store.create_dataset(self.pid, 'Cases')['id'];self.store.add_case(self.did, {'prompt': 'hello'}, 'ok')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), ModelService);self.server.calls = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True);self.thread.start()
        self.url = 'http://127.0.0.1:'+str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
        ACTIVE_SECRETS.reset(self.secret_token);self.env.stop();self.tmp.cleanup()

    def connection(self, **changes):
        return connections.create(self.store, {'project_id': self.pid, 'name': 'Target', 'kind': 'http_agent',
                                               'endpoint': self.url+'/agent', 'api_key': 'test-private-target-value', **changes})

    def configured(self):
        target = self.connection()
        judge = self.connection(name='Judge', kind='openai_compatible', endpoint=self.url+'/v1', model='test-model', api_key='test-private-judge-value')
        agent = self.store.add_agent(self.pid, 'target', {'type': 'http', 'connection_id': target['id']})['id']
        evaluator = self.store.add_evaluator(self.pid, 'judge', 'llm_judge', {'connection_id': judge['id'], 'rubric': 'correct'})['id']
        exp = self.store.create_experiment(self.pid, self.did, agent, [evaluator], {'judge_retries': 1})['id']
        return target, judge, agent, evaluator, exp

    def test_encrypted_persistence_rotation_clear_and_wrong_master(self):
        code, result = dispatch(self.store, 'POST', '/v1/model-connections',
                                {'project_id': self.pid, 'name': 'Saved', 'kind': 'http_agent', 'endpoint': self.url+'/agent', 'api_key': 'private-persisted-value'}, self.principal)
        self.assertEqual(code, 201);self.assertTrue(result['has_api_key']);self.assertEqual(result['key_version'], 1)
        self.assertNotIn('key_ciphertext', result);self.assertNotIn('api_key', result)
        restarted = Store(self.path)
        self.assertEqual(connections.api_key(restarted, result['id']), 'private-persisted-value')
        self.assertNotIn(b'private-persisted-value', self.path.read_bytes())
        with patch.dict(os.environ, {'LITEEVAL_SECRET_KEY': Fernet.generate_key().decode()}):
            with self.assertRaisesRegex(ValueError, 'Unable to decrypt'):connections.api_key(restarted, result['id'])
        code, replaced = dispatch(restarted, 'PATCH', '/v1/model-connections/'+result['id'], {'api_key': 'replacement-private-value'}, self.principal)
        self.assertEqual(code, 200);self.assertEqual(replaced['key_version'], 2)
        self.assertEqual(connections.api_key(restarted, result['id']), 'replacement-private-value')
        cleared = connections.update(restarted, result['id'], {'api_key': None})
        self.assertFalse(cleared['has_api_key']);self.assertIsNone(connections.api_key(restarted, result['id']))

    def test_scope_and_endpoint_revision_guards(self):
        saved = self.connection()
        other = {'admin': False, 'project_id': self.other}
        for method, suffix, body in [('GET', '/'+saved['id'], {}), ('PATCH', '/'+saved['id'], {'api_key': 'foreign-key'}), ('GET', '?project_id='+self.pid, {})]:
            self.assertEqual(dispatch(self.store, method, '/v1/model-connections'+suffix, body, other)[0], 403)
        with self.assertRaisesRegex(ValueError, 'different Project'):
            self.store.add_agent(self.other, 'bad', {'type': 'http', 'connection_id': saved['id']})
        for changes in ({'endpoint': self.url+'/other'}, {'model': 'new-model'}, {'api_key': ''}):
            self.assertEqual(dispatch(self.store, 'PATCH', '/v1/model-connections/'+saved['id'], changes, self.principal)[0], 400)
        with self.assertRaises(ValueError):self.connection(endpoint=self.url+'?api_key=unsafe')
        with self.assertRaises(ValueError):self.store.add_agent(self.pid, 'bad', {'type': 'http', 'connection_id': saved['id'], 'endpoint': self.url+'/other'})
        with self.assertRaises(ValueError):self.store.add_evaluator(self.pid, 'bad', 'llm_judge', {'connection_id': saved['id'], 'rubric': 'ok'})

    def test_missing_master_rejected_without_plaintext_storage(self):
        with patch.dict(os.environ, {'LITEEVAL_SECRET_KEY': ''}):
            with self.assertRaisesRegex(ValueError, 'LITEEVAL_SECRET_KEY'):self.connection()
        self.assertEqual(connections.list_connections(self.store, self.pid)['total'], 0)

    def test_archive_blocks_new_experiments_and_restore(self):
        target, _, aid, eid, _ = self.configured()
        connections.update(self.store, target['id'], {'archived': True})
        body = {'project_id': self.pid, 'dataset_id': self.did, 'agent_version_id': aid, 'evaluator_ids': [eid]}
        self.assertEqual(dispatch(self.store, 'POST', '/v1/experiments/preflight', body)[0], 409)
        self.assertEqual(connections.list_connections(self.store, self.pid)['total'], 1)
        connections.update(self.store, target['id'], {'archived': False})
        self.assertTrue(planning.preflight(self.store, body)['submission_allowed'])

    def test_worker_reports_per_connection_decryption_status(self):
        target, _, aid, eid, _ = self.configured()
        worker = runtime.WorkerRegistry(self.store);worker.heartbeat()
        body = {'project_id': self.pid, 'dataset_id': self.did, 'agent_version_id': aid, 'evaluator_ids': [eid]}
        report = planning.preflight(self.store, body)['credential_readiness']
        self.assertEqual(report['state'], 'ready');self.assertEqual(report['required_env'], [])
        with patch.dict(os.environ, {'LITEEVAL_SECRET_KEY': Fernet.generate_key().decode()}):worker.heartbeat()
        self.assertEqual(planning.preflight(self.store, body)['credential_readiness']['state'], 'missing')
        self.assertNotIn('test-private-target-value', json.dumps(runtime.status(self.store)))

    def test_real_http_execution_counts_judge_retries_and_exports(self):
        _, _, _, _, exp = self.configured()
        asyncio.run(ExperimentRunner(Store(self.path)).run(exp))
        result = self.store.get_experiment(exp);self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['summary']['case_counts']['pass'], 1)
        self.assertEqual(len(self.server.calls), 3)
        self.assertEqual(self.server.calls[0][1], 'Bearer test-private-target-value')
        self.assertTrue(all(c[1] == 'Bearer test-private-judge-value' for c in self.server.calls[1:]))
        run_id = result['runs'][0]['id'];stats = rollouts.get_statistics(self.store, experiment_id=exp)
        self.assertEqual(stats['usage']['by_role']['target']['totals']['input_tokens'], 10)
        self.assertEqual(stats['usage']['by_role']['judge']['totals']['input_tokens'], 12)
        self.assertEqual(stats['usage']['totals']['total_tokens'], 30)
        self.assertEqual(stats['usage']['totals']['cached_input_tokens'], 3)
        self.assertEqual(stats['usage']['totals']['reasoning_tokens'], 3)
        self.assertAlmostEqual(stats['usage']['cost_by_currency']['USD']['total'], .05)
        self.assertEqual(stats['tool_calls'], 1);self.assertEqual(stats['coverage']['state'], 'finished')
        self.assertEqual(result['summary']['rollout_usage']['totals']['total_tokens'], 30)
        first = rollouts.events(self.store, run_id, limit=2)
        second = rollouts.events(self.store, run_id, after=first['next_cursor'])
        self.assertTrue(first['has_more']);self.assertTrue(all(e['id'] > first['next_cursor'] for e in second['items']))
        for format in ('json', 'jsonl', 'csv', 'zip'):
            code, response = dispatch(self.store, 'GET', f'/v1/experiments/{exp}/export?format={format}', principal=self.principal)
            self.assertEqual(code, 200);self.assertIsInstance(response, BinaryResponse)
            if format == 'json':
                exported = json.loads(response.content);self.assertEqual(len(exported['usage_records']), 3)
                self.assertEqual(len(exported['runs'][0]['attempts'][0]['trace']['events']), 1)
            elif format == 'jsonl':self.assertTrue(any(json.loads(line)['record_type'] == 'usage' for line in response.content.splitlines()))
            elif format == 'csv':
                self.assertTrue(response.content.startswith(b'\xef\xbb\xbf'))
                rows = list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))));self.assertEqual(rows[0]['input_tokens'], '22')
            else:
                with ZipFile(io.BytesIO(response.content)) as archive:
                    manifest = json.loads(archive.read('manifest.json'))
                    for item in manifest['artifacts']:
                        self.assertEqual(hashlib.sha256(archive.read(item['path'])).hexdigest(), item['sha256'])
                    response.content = b'\n'.join(archive.read(n) for n in archive.namelist())
            self.assertNotIn(b'test-private-target-value', response.content)
            self.assertNotIn(b'test-private-judge-value', response.content)
            self.assertNotIn(b'key_ciphertext', response.content)
        foreign = {'admin': False, 'project_id': self.other}
        for path in (f'/v1/experiments/{exp}/statistics', f'/v1/experiments/{exp}/export?format=zip', f'/v1/runs/{run_id}/events'):
            self.assertEqual(dispatch(self.store, 'GET', path, principal=foreign)[0], 403)

    def test_openai_compatible_connection_runs_as_target(self):
        saved = self.connection(kind='openai_compatible', endpoint=self.url+'/v1', model='target-model')
        agent = self.store.add_agent(self.pid, 'chat', {'type': 'http', 'connection_id': saved['id']})['id']
        with self.store.connect() as db:config = json.loads(self.store.required(db, 'agent_versions', agent)['config_json'])
        self.assertEqual(config['endpoint'], self.url+'/v1/chat/completions')
        self.assertEqual(config['request_template']['model'], 'target-model')
        self.assertEqual(config['output_path'], '$.choices[0].message.content')
        from liteeval.core import EvalCase
        run=asyncio.run(HTTPAdapter(config, store=self.store).invoke(EvalCase('test',1,{'prompt':'hello'})))
        self.assertEqual(run.status,'succeeded')
        self.assertEqual(self.server.calls[0][2], {'model':'target-model','messages':[{'role':'user','content':'hello'}]})

    def test_activity_is_live_redacted_and_lease_fenced(self):
        _, _, _, _, exp = self.configured();claimed = self.store.claim(exp, 30)
        from liteeval.core import EvalCase
        content = self.store.list_cases(self.did)['items'][0]
        case = EvalCase(**{k:v for k,v in content.items() if k in EvalCase.__dataclass_fields__})
        attempt = self.store.start_attempt(exp, claimed['lease_token'], case)
        secret = register_secret('split-secret-value')
        script = 'import sys,time;sys.stdout.write("split-secret-");sys.stdout.flush();time.sleep(.1);print("value");sys.stdout.flush();time.sleep(.8)'
        seen_live = []
        async def run():
            task = asyncio.create_task(run_process([sys.executable, '-u', '-c', script], on_output=lambda stream, text:
                rollouts.append(self.store, exp, claimed['lease_token'], attempt, 'log', {'stream': stream, 'text': text})))
            for _ in range(30):
                if rollouts.events(self.store, attempt['run_id'])['items']:
                    seen_live.append(not task.done());break
                await asyncio.sleep(.03)
            await task
        asyncio.run(run());self.assertEqual(seen_live, [True])
        activity = rollouts.events(self.store, attempt['run_id'])
        self.assertNotIn(secret, json.dumps(activity));self.assertIn('[REDACTED]', json.dumps(activity))
        self.assertEqual(rollouts.get_statistics(self.store, run_id=attempt['run_id'])['coverage']['state'], 'partial')
        self.store.cancel(exp)
        with self.assertRaises(LeaseLost):rollouts.append(self.store, exp, claimed['lease_token'], attempt, 'log', {})
        with self.assertRaises(LeaseLost):usage.record(self.store, exp, claimed['lease_token'], attempt, 'target', {})

    def test_normalizer_unknown_aliases_and_codex_multiturn(self):
        normal = usage.normalize({'input_tokens': 8, 'prompt_tokens': 8, 'completion_tokens': 5,
                                  'prompt_tokens_details': {'cached_tokens': 3}, 'completion_tokens_details': {'reasoning_tokens': 2}})
        self.assertEqual(normal['total_tokens'], 13);self.assertEqual(normal['cached_input_tokens'], 3)
        self.assertIsNone(usage.normalize({})['reasoning_tokens'])
        self.assertIsNone(usage.normalize({'input_tokens': -1, 'output_tokens': True})['total_tokens'])
        self.assertIn('conflicting_aliases:input_tokens', usage.normalize({'input_tokens': 2, 'prompt_tokens': 3})['warnings'])
        parsed = parse_jsonl('\n'.join(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': i, 'output_tokens': 2, 'cached_input_tokens': 1}}) for i in (5, 7)))
        self.assertEqual(parsed['usage']['input_tokens'], 12);self.assertEqual(parsed['usage']['cached_input_tokens'], 2)

    def test_zip_integrity_failure_and_export_validation(self):
        _, _, _, _, exp = self.configured();asyncio.run(ExperimentRunner(self.store).run(exp))
        with patch.object(rollouts,'EXPORT_LIMIT',32):
            for format in ('json','jsonl','csv','zip'):
                with self.assertRaisesRegex(ValueError,'Export'):rollouts.export(self.store,experiment_id=exp,format=format)
        artifact = self.store.list_items('artifacts', self.pid)['items'][0]
        (self.store.artifact_root/artifact['id']).write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'integrity'):rollouts.export(self.store, experiment_id=exp, format='zip')
        self.assertEqual(dispatch(self.store, 'GET', f'/v1/experiments/{exp}/export?format=bad')[0], 400)

    def test_v4_upgrade_and_failure_are_transactional(self):
        with self.store.connect() as db:
            for table in ('model_connections', 'rollout_events', 'usage_records'):db.execute('DROP TABLE '+table)
            db.execute('PRAGMA user_version=4')
        with patch.object(migrations, 'V5_DDL', migrations.V5_DDL+'INVALID SQL;'):
            with self.assertRaises(sqlite3.OperationalError):Store(self.path)
        with self.store.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 4)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='model_connections'").fetchone())
        upgraded = Store(self.path)
        self.assertEqual(upgraded.list_items('projects')['total'], 2)
        with upgraded.connect() as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 5)

    def test_authenticated_http_connections_and_binary_download(self):
        from urllib.request import Request, build_opener, ProxyHandler
        token = self.store.issue_key(self.pid, 'http-client')['token']
        api = ThreadingHTTPServer(('127.0.0.1',0), make_handler(self.store,'test-admin'))
        thread = threading.Thread(target=api.serve_forever,daemon=True);thread.start()
        opener = build_opener(ProxyHandler({}));base='http://127.0.0.1:'+str(api.server_port)
        try:
            body=json.dumps({'project_id':self.pid,'name':'HTTP','kind':'http_agent','endpoint':self.url+'/agent','api_key':'api-post-private-value'}).encode()
            headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}
            with opener.open(Request(base+'/v1/model-connections',body,headers),timeout=5) as response:
                value=json.load(response);self.assertTrue(value['has_api_key']);self.assertEqual(response.status,201)
            _,_,_,_,exp=self.configured();asyncio.run(ExperimentRunner(self.store).run(exp))
            with opener.open(Request(base+f'/v1/experiments/{exp}/export?format=zip',headers=headers),timeout=5) as response:
                self.assertEqual(response.headers['Content-Type'],'application/zip')
                self.assertIn('attachment;',response.headers['Content-Disposition'])
                with ZipFile(io.BytesIO(response.read())) as archive:self.assertIn('rollout.json',archive.namelist())
        finally:api.shutdown();api.server_close();thread.join()


if __name__ == '__main__':unittest.main()
