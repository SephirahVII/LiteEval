from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from liteeval.interfaces.http.api import dispatch
from liteeval.application import catalog as catalog; from liteeval.application import planning as planning; from liteeval.infrastructure.execution import workers as runtime; from liteeval.infrastructure.persistence import migrations as migrations
from liteeval.domain.contracts import EvalCase
from liteeval.application.imports import validate_batch, BatchValidationError
from liteeval.interfaces.http.sessions import BrowserSessions, token_hash
from liteeval.infrastructure.persistence.store import Store, Conflict, SCHEMA, digest, encoded


class CatalogPlanningTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name)/'test.db')
        self.pid = self.store.create_project('Support')['id']
        self.did = self.store.create_dataset(self.pid, 'Cases')['id']
        self.case = self.store.add_case(self.did, {'q': 'question'}, 'ok', {'critical': True})
        self.aid = self.store.add_agent(self.pid, 'v1', {'type': 'http', 'endpoint': 'http://127.0.0.1:1'})['id']
        self.eid = self.store.add_evaluator(self.pid, 'Exact', 'exact_match', {})['id']

    def tearDown(self):self.tmp.cleanup()

    def body(self, **changes):
        return {'project_id': self.pid, 'dataset_id': self.did, 'agent_version_id': self.aid,
                'evaluator_ids': [self.eid], **changes}

    def submit(self, body, key=None):
        return dispatch(self.store, 'POST', '/v1/experiments', body,
                        headers={'Idempotency-Key': key} if key else {})

    def count(self, table):
        with self.store.connect() as db:return db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]

    def test_import_preview_and_import_share_atomic_validation(self):
        records = [{'input': {}}, {'input': 'bad'}, {'input': {}, 'metadata': {'critical': 'yes'}}]
        code, preview = dispatch(self.store, 'POST', f'/v1/datasets/{self.did}/import-preview', {'records': records})
        self.assertEqual(code, 200);self.assertFalse(preview['valid'])
        self.assertEqual((preview['valid_count'], preview['invalid_count']), (1, 2))
        self.assertEqual([e['row'] for e in preview['errors']], [2, 3])
        code, error = dispatch(self.store, 'POST', f'/v1/datasets/{self.did}/import', {'records': records})
        self.assertEqual(code, 400);self.assertEqual(error['validation'], preview)
        self.assertEqual(self.store.list_cases(self.did)['total'], 1)

    def test_csv_preview_reports_cell_and_physical_line(self):
        csv = '\ufeffinput,expected,metadata\n"{""q"":""a""}","""ok""",{}\nbad,,{}\n'
        normalized, report = validate_batch(self.store.validate_case, csv_text=csv)
        self.assertEqual(len(normalized), 1);self.assertEqual(report['errors'][0]['field'], 'input')
        self.assertEqual(report['errors'][0]['line'], 3)
        with self.assertRaises(ValueError):validate_batch(self.store.validate_case, csv_text='expected\n42\n')
        with self.assertRaises(ValueError):validate_batch(self.store.validate_case, csv_text='input,input\n{},{}\n')

    def test_import_preview_bounds_and_nonfinite_values(self):
        _, report = validate_batch(self.store.validate_case, records=[{'input': 'bad'}]*101)
        self.assertEqual(report['invalid_count'], 101);self.assertEqual(len(report['errors']), 100)
        self.assertTrue(report['errors_truncated'])
        _, exactly = validate_batch(self.store.validate_case, records=[{'input': 'bad'}]*100)
        self.assertFalse(exactly['errors_truncated'])
        with self.assertRaises(BatchValidationError):self.store.import_cases(self.did, [{'input': {}, 'expected': float('nan')}])
        self.assertEqual(self.store.list_cases(self.did)['total'], 1)

    def test_long_csv_cell_within_body_limit_is_supported(self):
        import csv
        import io
        out = io.StringIO();writer = csv.writer(out)
        writer.writerow(['input', 'expected', 'metadata'])
        writer.writerow([json.dumps({'q': 'x'*150000}), '"ok"', '{}'])
        records, report = validate_batch(self.store.validate_case, csv_text=out.getvalue())
        self.assertTrue(report['valid']);self.assertEqual(len(records[0]['input']['q']), 150000)

    def test_evaluator_revisions_preserve_old_configs_and_compare_identity(self):
        old = self.store.create_experiment(self.pid, self.did, self.aid, [self.eid])['id']
        revision = catalog.revise_evaluator(self.store, self.eid, {'output_field': '$.answer'}, 1, 'Answer')
        self.assertEqual(revision['revision'], 2);self.assertEqual(revision['evaluator_id'], self.eid)
        self.assertNotEqual(revision['id'], self.eid)
        with self.store.connect() as db:
            self.assertEqual(self.store.required(db, 'evaluator_revisions', self.eid)['config_json'], '{}')
            self.assertEqual(db.execute('SELECT evaluator_revision_id FROM experiment_evaluators WHERE experiment_id=?', (old,)).fetchone()[0], self.eid)
        items = catalog.list_revisions(self.store, revision['id'])['items']
        self.assertEqual([r['revision'] for r in items], [2, 1])
        self.assertEqual(self.store.list_items('evaluator_revisions', latest=True)['items'][0]['id'], revision['id'])
        with self.assertRaises(Conflict):catalog.revise_evaluator(self.store, self.eid, {}, 1)

    def test_concurrent_evaluator_edits_have_one_winner(self):
        def revise(i):
            try:return catalog.revise_evaluator(self.store, self.eid, {'output_field': '$.a'}, 1)['revision']
            except Conflict:return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:results = list(pool.map(revise, [1, 2]))
        self.assertCountEqual(results, [2, 'conflict'])
        self.assertEqual(catalog.list_revisions(self.store, self.eid)['total'], 2)

    def test_published_dataset_is_immutable_despite_live_edits_and_archive(self):
        version = catalog.publish_dataset(self.store, self.did, 'release-1', 'Frozen business cases')
        self.store.revise_case(self.case['id'], {}, 'changed', base_revision=1)
        self.store.update_resource('cases', self.case['id'], {'archived': True})
        self.store.add_case(self.did, {}, 'extra')
        content = catalog.version_cases(self.store, version['id'])
        self.assertEqual(content['total'], 1);self.assertEqual(content['items'][0]['expected'], 'ok')
        code, exp = self.submit(self.body(dataset_id=None, dataset_version_id=version['id']))
        self.assertEqual(code, 201);self.assertEqual(exp['case_count'], 1)
        with self.store.connect() as db:
            self.assertEqual(self.store.required(db, 'experiments', exp['id'])['snapshot_id'], version['snapshot_id'])
        self.assertEqual(exp['snapshot_hash'], version['snapshot_hash'])
        self.store.update_resource('datasets', self.did, {'archived': True})
        self.assertEqual(catalog.version_cases(self.store, version['id'])['total'], 1)
        self.assertEqual(self.submit(self.body(dataset_id=None, dataset_version_id=version['id']))[0], 409)
        self.assertEqual(self.store.rerun(exp['id'])['status'], 'queued')

    def test_duplicate_dataset_release_has_no_orphan_snapshot(self):
        def publish(i):
            try:return catalog.publish_dataset(self.store, self.did, 'same-name')['id']
            except Conflict:return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:results = list(pool.map(publish, [1, 2]))
        self.assertEqual(results.count('conflict'), 1)
        self.assertEqual(self.count('dataset_snapshots'), 1)
        self.assertEqual(catalog.list_versions(self.store, self.did)['total'], 1)

    def test_preflight_is_read_only_and_never_calls_external_endpoints(self):
        before = {t: self.count(t) for t in ('dataset_snapshots', 'experiments', 'runs', 'scores')}
        with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('Preflight must not call HTTP')):
            result = planning.preflight(self.store, self.body())
        self.assertTrue(result['ready']);self.assertEqual(result['source']['critical_count'], 1)
        self.assertEqual(result['credential_readiness']['state'], 'unknown')
        self.assertIsNone(result['estimated_cost'])
        self.assertEqual(before, {t: self.count(t) for t in before})

    def test_checked_submission_blocks_missing_expected_legacy_remains_compatible(self):
        self.store.revise_case(self.case['id'], {}, None, base_revision=1)
        body = self.body()
        report = planning.preflight(self.store, body)
        self.assertFalse(report['ready'])
        code, error = self.submit({**body, 'preflight_hash': report['preflight_hash']})
        self.assertEqual(code, 400);self.assertEqual(error['error'], 'preflight_failed')
        self.assertEqual(self.count('experiments'), 0);self.assertEqual(self.count('dataset_snapshots'), 0)
        self.assertEqual(self.submit(body)[0], 201)
        diagnostic = self.body(diagnostic=True)
        report = planning.preflight(self.store, diagnostic)
        self.assertTrue(report['submission_allowed']);self.assertFalse(report['ready'])
        code, exp = self.submit({**diagnostic, 'preflight_hash': report['preflight_hash']})
        self.assertEqual(code, 201);self.assertTrue(exp['diagnostic'])
        self.assertIs(self.store.get_experiment(exp['id'])['diagnostic'], True)
        rerun = self.store.rerun(exp['id'])['id']
        self.assertEqual(self.store.get_experiment(rerun)['diagnostic'], 1)

    def test_stale_preflight_and_idempotent_retry_after_live_dataset_edit(self):
        body = self.body(name='candidate')
        report = planning.preflight(self.store, body)
        checked = {**body, 'preflight_hash': report['preflight_hash']}
        code, exp = self.submit(checked, 'stable-key')
        self.assertEqual(code, 201)
        self.store.revise_case(self.case['id'], {}, 'new', base_revision=1)
        retry = self.submit(checked, 'stable-key')
        self.assertEqual(retry[1]['id'], exp['id']);self.assertTrue(retry[1]['reused'])
        code, error = self.submit(checked, 'new-key')
        self.assertEqual(code, 409);self.assertEqual(error['error'], 'stale_preflight')
        self.assertNotEqual(error['preflight']['preflight_hash'], report['preflight_hash'])
        self.assertEqual(self.count('dataset_snapshots'), 1)
        self.assertEqual(self.submit({**checked, 'name': 'changed'}, 'stable-key')[0], 409)

    def test_published_source_preflight_survives_unrelated_live_edits(self):
        version = catalog.publish_dataset(self.store, self.did, 'stable')
        body = self.body(dataset_id=None, dataset_version_id=version['id'])
        report = planning.preflight(self.store, body)
        self.store.revise_case(self.case['id'], {}, 'changed', base_revision=1)
        self.assertEqual(planning.preflight(self.store, body)['preflight_hash'], report['preflight_hash'])
        self.assertEqual(self.submit({**body, 'preflight_hash': report['preflight_hash']})[0], 201)
        self.assertEqual(self.submit(self.body(dataset_version_id=version['id']))[0], 400)

    def test_evidence_prechecks_and_call_upper_bounds(self):
        tool = self.store.add_evaluator(self.pid, 'Tool', 'tool_parameter', {'tool_name': 'lookup'})['id']
        report = planning.preflight(self.store, self.body(evaluator_ids=[tool]))
        self.assertEqual(report['blocking_errors'][0]['code'], 'expected_invalid')
        constant = self.store.add_evaluator(self.pid, 'Tool constant', 'tool_parameter', {'tool_name': 'lookup', 'parameters': {'id': 1}})['id']
        self.assertTrue(planning.preflight(self.store, self.body(evaluator_ids=[constant]))['ready'])
        file_eval = self.store.add_evaluator(self.pid, 'File', 'file_exists', {'path': 'answer.txt'})['id']
        self.assertEqual(planning.preflight(self.store, self.body(evaluator_ids=[file_eval]))['blocking_errors'][0]['code'], 'final_state_unavailable')
        judge = self.store.add_evaluator(self.pid, 'Judge', 'llm_judge', {'base_url': 'http://127.0.0.1:1', 'model': 'mock', 'rubric': 'correct'})['id']
        report = planning.preflight(self.store, self.body(evaluator_ids=[judge], config={'max_retries': 2, 'judge_retries': 2}))
        self.assertEqual(report['estimated_call_count'], {'agent_max': 3, 'judge_max': 3})

    def test_empty_dataset_cannot_be_bypassed_with_diagnostic_mode(self):
        self.store.update_resource('cases', self.case['id'], {'archived': True})
        body = self.body(diagnostic=True)
        report = planning.preflight(self.store, body)
        self.assertFalse(report['submission_allowed'])
        self.assertEqual(self.submit({**body, 'preflight_hash': report['preflight_hash']})[0], 400)

    def test_worker_readiness_uses_worker_report_not_api_environment(self):
        aid = self.store.add_agent(self.pid, 'auth', {'type': 'http', 'endpoint': 'http://127.0.0.1:1',
            'auth': {'type': 'bearer', 'token_env': 'TEST_WORKER_TOKEN'}})['id']
        registry = runtime.WorkerRegistry(self.store)
        with patch.dict('os.environ', {'TEST_WORKER_TOKEN': 'worker-only-test-secret'}):registry.heartbeat()
        with patch.dict('os.environ', {'TEST_WORKER_TOKEN': ''}):
            report = planning.preflight(self.store, self.body(agent_version_id=aid))
        self.assertEqual(report['credential_readiness']['state'], 'ready')
        self.assertNotIn('worker-only-test-secret', json.dumps(runtime.status(self.store)))
        second = runtime.WorkerRegistry(self.store)
        with patch.dict('os.environ', {'TEST_WORKER_TOKEN': ''}):second.heartbeat()
        self.assertEqual(planning.preflight(self.store, self.body(agent_version_id=aid))['credential_readiness']['state'], 'partial')
        registry.stop()
        self.assertEqual(planning.preflight(self.store, self.body(agent_version_id=aid))['credential_readiness']['state'], 'missing')
        second.stop()
        self.assertEqual(runtime.status(self.store)['online_workers'], 0)

    def test_worker_heartbeat_expiry_and_no_union_of_credentials(self):
        aid = self.store.add_agent(self.pid, 'auth', {'type': 'http', 'endpoint': 'http://127.0.0.1:1',
            'auth': {'type': 'bearer', 'token_env': 'TEST_WORKER_TOKEN'}})['id']
        judge = self.store.add_evaluator(self.pid, 'Judge', 'llm_judge', {'base_url': 'http://127.0.0.1:1', 'model': 'mock', 'rubric': 'correct'})['id']
        for env in ({'TEST_WORKER_TOKEN': 'present', 'LITEEVAL_JUDGE_API_KEY': ''},
                    {'TEST_WORKER_TOKEN': '', 'LITEEVAL_JUDGE_API_KEY': 'present'}):
            with patch.dict('os.environ', env):runtime.WorkerRegistry(self.store).heartbeat()
        self.assertEqual(planning.preflight(self.store, self.body(agent_version_id=aid, evaluator_ids=[judge]))['credential_readiness']['state'], 'missing')
        with patch('liteeval.infrastructure.execution.workers.time.time', return_value=time.time()+runtime.ONLINE_SECONDS+1):
            self.assertEqual(runtime.status(self.store)['online_workers'], 0)

    def test_worker_capability_checks_include_http_docker_environment(self):
        worker = {'capabilities': {'http': True, 'docker_cli': False, 'codex_cli': False}}
        self.assertTrue(runtime.supports(worker, {'type': 'http'}))
        self.assertFalse(runtime.supports(worker, {'type': 'http', 'environment': {'type': 'docker'}}))
        self.assertFalse(runtime.supports(worker, {'type': 'codex', 'environment': {'type': 'workspace'}}))
        worker['capabilities']['docker_cli'] = True
        self.assertTrue(runtime.supports(worker, {'type': 'codex', 'environment': {'type': 'docker'}}))

    def test_project_scopes_and_privileged_routes(self):
        version = catalog.publish_dataset(self.store, self.did, 'one')
        foreign_pid = self.store.create_project('Foreign')['id']
        foreign = {'admin': False, 'project_id': foreign_pid}
        local = {'admin': False, 'project_id': self.pid}
        for method, path, body in [('POST', '/v1/experiments/preflight', self.body()),
                                   ('POST', f'/v1/datasets/{self.did}/import-preview', {'records': [{'input': {}}]}),
                                   ('GET', f'/v1/dataset-versions/{version["id"]}', None),
                                   ('GET', f'/v1/dataset-versions/{version["id"]}/cases', None),
                                   ('GET', f'/v1/evaluators/{self.eid}/revisions', None)]:
            self.assertEqual(dispatch(self.store, method, path, body, foreign)[0], 403)
        self.assertEqual(dispatch(self.store, 'GET', '/v1/runtime/status', principal=local)[0], 403)
        self.assertEqual(dispatch(self.store, 'POST', f'/v1/evaluators/{self.eid}/revisions', {'config': {}, 'base_revision': 1}, local)[0], 403)
        self.assertEqual(dispatch(self.store, 'POST', f'/v1/datasets/{self.did}/versions', {'name': 'two'}, local)[0], 201)

    def test_query_validation_and_immutable_routes(self):
        version = catalog.publish_dataset(self.store, self.did, 'one')
        self.assertEqual(dispatch(self.store, 'PATCH', f'/v1/dataset-versions/{version["id"]}', {'name': 'changed'})[0], 404)
        self.assertEqual(dispatch(self.store, 'GET', f'/v1/datasets/{self.did}/versions?limit=0')[0], 400)
        self.assertEqual(dispatch(self.store, 'GET', '/v1/evaluators?latest=maybe')[0], 400)
        self.assertEqual(dispatch(self.store, 'POST', f'/v1/evaluators/{self.eid}/revisions', {'config': {}, 'base_revision': 1, 'kind': 'contains'})[0], 400)
        self.assertEqual(dispatch(self.store, 'POST', '/v1/experiments/preflight', self.body(config=[]))[0], 400)


class Schema4MigrationTest(unittest.TestCase):
    def legacy(self, path):
        db = sqlite3.connect(path)
        db.executescript(SCHEMA)
        db.executescript(migrations.DDL.split('CREATE UNIQUE INDEX')[0])
        for group in (migrations.COLUMNS, migrations.V3_COLUMNS):
            for table, columns in group.items():
                for name, definition in columns.items():db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
        db.executescript(migrations.DDL+migrations.V3_DDL)
        db.execute("INSERT INTO projects VALUES ('project','Legacy','now')")
        db.execute("INSERT INTO datasets (id,project_id,name,created_at) VALUES ('dataset','project','Cases','now')")
        for eid in ('e1', 'e2'):
            db.execute('INSERT INTO evaluator_revisions (id,project_id,name,kind,config_json,config_hash) VALUES (?,?,?,?,?,?)',
                       (eid, 'project', 'Same name', 'exact_match', '{}', digest(['exact_match', {}])))
        db.execute('INSERT INTO agent_versions VALUES (?,?,?,?,?)', ('agent', 'project', 'v1', encoded({'type': 'http', 'endpoint': 'http://127.0.0.1:1'}), 'hash'))
        db.execute("INSERT INTO dataset_snapshots VALUES ('snap','dataset','hash','now')")
        key_hash = digest(['project', 'dataset', 'agent', ['e1'], {}])
        db.execute('INSERT INTO experiments (id,project_id,snapshot_id,agent_version_id,status,created_at,idempotency_key,request_hash) VALUES (?,?,?,?,?,?,?,?)',
                   ('exp', 'project', 'snap', 'agent', 'completed', 'now', 'old-key', key_hash))
        db.execute('INSERT INTO browser_sessions VALUES (?,?,?,?,?)', (token_hash('old-session'), 'owner', time.time(), time.time(), time.time()+600))
        db.execute('PRAGMA user_version=3');db.commit();db.close()

    def test_v3_upgrade_keeps_legacy_ids_sessions_and_idempotency(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'legacy.db';self.legacy(path)
            store = Store(path)
            items = store.list_items('evaluator_revisions')['items']
            self.assertEqual({r['evaluator_id'] for r in items}, {'e1', 'e2'})
            self.assertTrue(all(r['revision']==1 for r in items))
            self.assertEqual(BrowserSessions(store).authenticate('old-session')['username'], 'owner')
            replay = store.create_experiment('project', 'dataset', 'agent', ['e1'], idempotency_key='old-key')
            self.assertEqual(replay['id'], 'exp');self.assertTrue(replay['reused'])
            self.assertEqual(Store(path).list_items('evaluator_revisions')['total'], 2)

    def test_v4_failure_rolls_back_new_columns_families_and_version(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'legacy.db';self.legacy(path)
            with patch.object(migrations, 'V4_DDL', migrations.V4_DDL+'INVALID SQL;'):
                with self.assertRaises(sqlite3.OperationalError):Store(path)
            db = sqlite3.connect(path)
            try:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 3)
                self.assertNotIn('evaluator_id', {r[1] for r in db.execute('PRAGMA table_info(evaluator_revisions)')})
                self.assertEqual(db.execute('SELECT COUNT(*) FROM evaluator_revisions').fetchone()[0], 2)
            finally:db.close()

    def test_v4_failure_after_backfill_rolls_back_all_changes(self):
        class FailAfterBackfill(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if sql.startswith('CREATE UNIQUE INDEX IF NOT EXISTS idx_evaluator_revision'):
                    raise sqlite3.OperationalError('Injected index failure')
                return super().execute(sql, parameters)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'legacy.db';self.legacy(path)
            db = sqlite3.connect(path, factory=FailAfterBackfill);db.row_factory = sqlite3.Row
            try:
                with self.assertRaises(sqlite3.OperationalError):migrations.migrate(db)
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 3)
                self.assertNotIn('evaluator_id', {r[1] for r in db.execute('PRAGMA table_info(evaluator_revisions)')})
                self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='evaluators'").fetchone())
                self.assertEqual(db.execute('SELECT COUNT(*) FROM evaluator_revisions').fetchone()[0], 2)
            finally:db.close()


if __name__ == '__main__':unittest.main()
