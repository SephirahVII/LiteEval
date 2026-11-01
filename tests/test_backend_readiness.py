import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from liteeval.api import dispatch
from liteeval.core import AgentRun, EvalCase, Score
from liteeval.migrations import COLUMNS, DDL
from liteeval.previews import enqueue, get_preview, PreviewRunner
from liteeval.storage import Store, Conflict, LeaseLost, SCHEMA, digest, encoded
from liteeval import web_queries


class BackendReadinessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name)/'test.db')
        self.pid = self.store.create_project('Support')['id']
        self.ds = self.store.create_dataset(self.pid, 'Regression')['id']
        self.aid = self.store.add_agent(self.pid, 'v1', {'type': 'http', 'endpoint': 'http://127.0.0.1:1'})['id']
        self.eid = self.store.add_evaluator(self.pid, 'Exact', 'exact_match', {})['id']

    def tearDown(self):
        self.tmp.cleanup()

    def experiment(self, n=3, evaluator_ids=None):
        for i in range(n):
            self.store.add_case(self.ds, {'question': str(i)}, 'ok', {'title': f'Case {i}'})
        return self.store.create_experiment(self.pid, self.ds, self.aid, evaluator_ids or [self.eid])['id']

    def frozen(self, exp_id):
        with self.store.connect() as db:
            exp = self.store.required(db, 'experiments', exp_id)
            return [EvalCase(**json.loads(r[0])) for r in db.execute('SELECT cr.content_json FROM snapshot_cases s JOIN case_revisions cr ON cr.case_id=s.case_id AND cr.revision=s.revision WHERE s.snapshot_id=? ORDER BY s.ordinal', (exp['snapshot_id'],))]

    def finish_case(self, claim, case, status='succeeded', outcome='pass'):
        attempt = self.store.start_attempt(claim['id'], claim['lease_token'], case)
        trace = self.store.create_trace(self.pid, case.input, 'ok', kind='eval')
        run = AgentRun(attempt['run_id'], case.id, status, output='ok', trace_id=trace['id'], latency_ms=25)
        scores = [Score(run.id, self.eid, outcome, value=1 if outcome=='pass' else 0)]
        self.store.finish_attempt(claim['id'], claim['lease_token'], attempt, run, trace['id'], scores, [])
        return run.id

    def preview_body(self):
        return {'project_id': self.pid, 'evaluator_id': self.eid,
                'case': {'input': {}, 'expected': 'ok'}, 'run': {'output': 'ok'}}

    def test_revision_conflict_is_atomic_under_concurrent_editors(self):
        case = self.store.add_case(self.ds, {}, 'old')
        def edit(value):
            try:
                return self.store.revise_case(case['id'], {}, value, base_revision=1)['revision']
            except Conflict:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(edit, ['a', 'b']))
        self.assertCountEqual(results, [2, 'conflict'])
        self.assertEqual(self.store.export_cases(self.ds)['records'][0]['revision'], 2)
        with self.assertRaises(ValueError):self.store.revise_case(case['id'], {}, base_revision=True)

    def test_archive_excludes_new_snapshots_but_retains_history_and_rerun(self):
        exp = self.experiment(2)
        old_cases = self.frozen(exp)
        self.store.update_resource('cases', old_cases[0].id, {'archived': True})
        new_exp = self.store.create_experiment(self.pid, self.ds, self.aid, [self.eid])['id']
        self.assertEqual(len(self.frozen(new_exp)), 1)
        self.assertEqual(self.store.list_cases(self.ds)['total'], 1)
        self.assertEqual(self.store.list_cases(self.ds, archived='only')['total'], 1)
        self.store.update_resource('datasets', self.ds, {'archived': True})
        self.store.update_resource('evaluator_revisions', self.eid, {'archived': True})
        with self.assertRaises(Conflict):self.store.create_experiment(self.pid, self.ds, self.aid, [self.eid])
        with self.assertRaises(Conflict):self.store.import_cases(self.ds, [{'input': {}}])
        with self.assertRaises(Conflict):self.store.revise_case(old_cases[1].id, {})
        rerun = self.store.rerun(exp)['id']
        self.assertEqual([asdict(c) for c in self.frozen(rerun)], [asdict(c) for c in old_cases])
        self.store.update_resource('datasets', self.ds, {'archived': False, 'name': 'Restored'})
        with self.assertRaises(Conflict):self.store.create_experiment(self.pid, self.ds, self.aid, [self.eid])
        self.store.update_resource('evaluator_revisions', self.eid, {'archived': False})
        self.assertEqual(self.store.create_experiment(self.pid, self.ds, self.aid, [self.eid])['case_count'], 1)

    def test_metadata_validation_and_no_partial_import(self):
        bad_values = [{'critical': 'yes'}, {'priority': 'urgent'}, {'tags': ['x'*33]}, {'title': 'x'*121}]
        for meta in bad_values:
            with self.subTest(meta=meta), self.assertRaises(ValueError):
                self.store.import_cases(self.ds, [{'input': {}}, {'input': {}, 'metadata': meta}])
        self.assertEqual(self.store.list_cases(self.ds)['total'], 0)

    def test_progress_and_paged_outcomes_match_existing_summary(self):
        exp = self.experiment()
        queued = web_queries.progress(self.store, exp)
        self.assertEqual(queued['counts']['pending'], 3)
        claim = self.store.claim(exp)
        cases = self.frozen(exp)
        self.finish_case(claim, cases[0])
        self.finish_case(claim, cases[1], outcome='fail')
        self.finish_case(claim, cases[2], status='failed', outcome='abstain')
        self.store.finish_experiment(exp, claim['lease_token'], 'completed')
        actual = web_queries.progress(self.store, exp)
        self.assertEqual(actual['counts']['terminal'], 3)
        self.assertEqual(actual['progress'], 1)
        summary = self.store.get_experiment(exp)['summary']
        rows = web_queries.paginated_runs(self.store, exp)['items']
        self.assertEqual({r['case_id']: r['outcome'] for r in rows}, {r['case_id']: r['outcome'] for r in summary['cases']})
        self.assertEqual(web_queries.paginated_runs(self.store, exp, outcome='fail')['total'], 1)
        self.assertEqual(web_queries.paginated_runs(self.store, exp, status='failed')['total'], 1)
        self.assertEqual(len(web_queries.paginated_runs(self.store, exp, limit=1, offset=1)['items']), 1)
        self.assertNotIn('output', rows[0]); self.assertNotIn('final_state_json', rows[0])
        self.assertEqual(web_queries.paginated_runs(self.store, exp, include_output=True)['items'][0]['output'], 'ok')

    def test_pending_and_cancelled_virtual_rows(self):
        exp = self.experiment(2)
        claim = self.store.claim(exp)
        self.store.start_attempt(exp, claim['lease_token'], self.frozen(exp)[0])
        rows = web_queries.paginated_runs(self.store, exp, include_output=True)
        self.assertEqual(rows['total'], 2)
        self.assertEqual(web_queries.paginated_runs(self.store, exp, status='pending')['total'], 1)
        self.store.cancel(exp)
        self.store.finish_experiment(exp, claim['lease_token'], 'cancelled')
        self.assertEqual(web_queries.paginated_runs(self.store, exp, outcome='cancelled')['total'], 2)
        result = web_queries.progress(self.store, exp)
        self.assertEqual(result['counts']['cancelled'], 2); self.assertEqual(result['counts']['terminal'], 2)

    def test_known_fail_wins_over_error_in_page_outcome(self):
        other = self.store.add_evaluator(self.pid, 'Other', 'contains', {})['id']
        exp = self.experiment(1, [self.eid, other])
        claim = self.store.claim(exp)
        run_id = self.finish_case(claim, self.frozen(exp)[0], outcome='fail')
        with self.store.connect() as db:
            db.execute('INSERT INTO scores (run_id,evaluator_revision_id,outcome) VALUES (?,?,?)', (run_id, other, 'error'))
        self.assertEqual(web_queries.paginated_runs(self.store, exp)['items'][0]['outcome'], 'fail')

    def test_new_read_endpoints_enforce_project_scope_and_pagination(self):
        exp = self.experiment(1)
        principal = {'admin': False, 'project_id': self.store.create_project('Foreign')['id']}
        for action in ('progress', 'runs'):
            self.assertEqual(dispatch(self.store, 'GET', f'/v1/experiments/{exp}/{action}', principal=principal)[0], 403)
        self.assertEqual(dispatch(self.store, 'GET', f'/v1/experiments/{exp}/runs?limit=0')[0], 400)
        self.assertEqual(dispatch(self.store, 'GET', f'/v1/experiments/{exp}/runs?include_output=maybe')[0], 400)

    def test_synthetic_preview_and_no_formal_scores_written(self):
        job = enqueue(self.store, self.preview_body())
        self.assertEqual(get_preview(self.store, job['id'])['status'], 'queued')
        self.assertEqual(asyncio.run(PreviewRunner(self.store).run_next()), job['id'])
        result = get_preview(self.store, job['id'])
        self.assertEqual(result['result']['score']['outcome'], 'pass')
        self.assertTrue(result['result']['synthetic'])
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM scores').fetchone()[0], 0)

    def test_cli_worker_once_processes_preview_queue(self):
        job = enqueue(self.store, self.preview_body())
        result = subprocess.run([sys.executable, '-m', 'liteeval.cli', '--db', self.store.path, 'worker', '--once'],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['preview_id'], job['id'])
        self.assertEqual(get_preview(self.store, job['id'])['result']['score']['outcome'], 'pass')

    def test_preview_uses_frozen_source_and_does_not_overwrite_scores(self):
        exp = self.experiment(1)
        claim = self.store.claim(exp)
        case = self.frozen(exp)[0]
        run_id = self.finish_case(claim, case)
        self.store.revise_case(case.id, case.input, 'different', base_revision=1)
        before = self.store.get_run(run_id)['scores']
        job = enqueue(self.store, {'project_id': self.pid, 'evaluator_id': self.eid, 'run_id': run_id})
        asyncio.run(PreviewRunner(self.store).run_next())
        result = get_preview(self.store, job['id'])['result']
        self.assertEqual(result['score']['outcome'], 'pass'); self.assertFalse(result['synthetic'])
        self.assertEqual(self.store.get_run(run_id)['scores'], before)

    def test_preview_evidence_validation_and_foreign_resources(self):
        tool = self.store.add_evaluator(self.pid, 'Tool', 'tool_called', {'tool_name': 'lookup'})['id']
        body = {**self.preview_body(), 'evaluator_id': tool}
        job = enqueue(self.store, body)
        asyncio.run(PreviewRunner(self.store).run_next())
        self.assertEqual(get_preview(self.store, job['id'])['result']['score']['outcome'], 'abstain')
        with self.assertRaises(ValueError):enqueue(self.store, {**body, 'run': {'events': ['bad']}})
        foreign = self.store.create_project('Foreign')['id']
        with self.assertRaises(ValueError):enqueue(self.store, {**body, 'project_id': foreign})
        principal = {'admin': False, 'project_id': foreign}
        self.assertEqual(dispatch(self.store, 'GET', f'/v1/evaluator-previews/{job["id"]}', principal=principal)[0], 403)

    def test_preview_recovery_rejects_stale_worker_and_survives_restart(self):
        job = enqueue(self.store, self.preview_body())
        old_runner = PreviewRunner(self.store)
        old = old_runner.claim()
        with self.store.connect() as db:db.execute('UPDATE evaluator_previews SET lease_expires=0')
        restarted = Store(self.store.path)
        new_runner = PreviewRunner(restarted)
        new = new_runner.claim()
        self.assertEqual(new['id'], job['id'])
        with self.assertRaises(LeaseLost):old_runner.finish(old, {'stale': True})
        new_runner.finish(new, {'fresh': True})
        self.assertEqual(get_preview(self.store, job['id'])['result'], {'fresh': True})

    def test_preview_judge_retry_retains_each_raw_response(self):
        class Judge(BaseHTTPRequestHandler):
            calls = 0
            def do_POST(inner):
                inner.rfile.read(int(inner.headers['Content-Length']))
                Judge.calls += 1
                content = 'invalid JSON' if Judge.calls == 1 else '{"score":1,"reason":"correct"}'
                data = json.dumps({'choices': [{'message': {'content': content}}]}).encode()
                inner.send_response(200);inner.send_header('Content-Length', str(len(data)));inner.end_headers();inner.wfile.write(data)
            def log_message(self, *args):pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Judge)
        thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
        try:
            eid = self.store.add_evaluator(self.pid, 'Judge', 'llm_judge', {'base_url': f'http://127.0.0.1:{server.server_port}', 'model': 'mock', 'rubric': 'correct', 'api_key_env': None})['id']
            job = enqueue(self.store, {**self.preview_body(), 'evaluator_id': eid})
            with patch.dict('os.environ', {'LITEEVAL_JUDGE_API_KEY': 'mock-only-key'}):
                asyncio.run(PreviewRunner(self.store).run_next())
            result = get_preview(self.store, job['id'])['result']
            self.assertEqual(result['score']['outcome'], 'pass');self.assertEqual(Judge.calls, 2)
            self.assertEqual(len(result['raw_artifact_ids']), 2)
            for artifact in result['raw_artifact_ids']:self.store.read_artifact(artifact)
        finally:
            server.shutdown();server.server_close();thread.join()


class MigrationTest(unittest.TestCase):
    def v2_database(self, path):
        db = sqlite3.connect(path)
        db.executescript(SCHEMA)
        db.executescript(DDL.split('CREATE UNIQUE INDEX')[0])
        for table, columns in COLUMNS.items():
            for name, definition in columns.items():db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
        db.executescript(DDL)
        db.execute("INSERT INTO projects VALUES ('old','Legacy','now')")
        db.execute("INSERT INTO datasets VALUES ('dataset','old','Cases','now')")
        content = {'id': 'case', 'revision': 1, 'input': {}, 'expected': 'ok', 'metadata': {}, 'source_trace_id': None}
        db.execute("INSERT INTO cases VALUES ('case','dataset',1)")
        db.execute('INSERT INTO case_revisions VALUES (?,?,?,?)', ('case', 1, encoded(content), digest(content)))
        db.execute('PRAGMA user_version=2');db.commit();db.close()

    def test_v2_upgrade_preserves_content_and_can_be_reopened(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'old.db';self.v2_database(path)
            store = Store(path)
            record = store.export_cases('dataset')['records'][0]
            self.assertEqual(record['expected'], 'ok');self.assertEqual(record['revision'], 1)
            self.assertIsNone(store.list_items('datasets')['items'][0]['archived_at'])
            self.assertEqual(Store(path).export_cases('dataset')['records'], [record])

    def test_failed_migration_rolls_back_schema_and_version(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'old.db';self.v2_database(path)
            from liteeval import migrations
            with patch.object(migrations, 'V3_DDL', migrations.V3_DDL+'INVALID SQL;'):
                with self.assertRaises(sqlite3.OperationalError):Store(path)
            db = sqlite3.connect(path)
            try:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 2)
                self.assertNotIn('archived_at', {r[1] for r in db.execute('PRAGMA table_info(cases)')})
                self.assertEqual(db.execute('SELECT COUNT(*) FROM case_revisions').fetchone()[0], 1)
            finally:db.close()


if __name__ == '__main__':
    unittest.main()
