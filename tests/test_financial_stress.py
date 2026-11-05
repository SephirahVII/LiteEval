"""Settlement invariants and the actual LiteEval publish, run and rejudge path."""

import asyncio
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from liteeval.application.benchmark_exports import export_execution
from liteeval.bootstrap import benchmark_service
from benchmarks.financial_stress.workflow.ledger import FinancialDesk, Rejected
from benchmarks.financial_stress.workflow import controller
from cryptography.fernet import Fernet
from liteeval.application import connections
from liteeval.infrastructure.persistence.store import Store
from liteeval.infrastructure.sources.harness import load_harness

PACKAGE = Path(__file__).resolve().parents[1] / 'benchmarks/financial_stress'
CASE = PACKAGE / 'tasks/ldi-margin-001'
FINANCIAL_EVIDENCE = 'evidence/financial_stress.json'


def register(service, project, path):
    spec, sha = load_harness(path, service.blobs)
    info = spec['definition']['harness']
    return service.repository.register(project, info['id'], info['role'], sha, spec)


def inputs():
    public = {p.name: p.read_text(encoding='utf-8') for p in (CASE / 'environment/initial_workspace').iterdir()}
    scenario = json.loads((CASE / 'judge/ground_truth_1.json').read_text(encoding='utf-8'))
    return public, scenario


class FinancialLedgerTest(unittest.TestCase):
    def test_rejected_repo_does_not_credit_cash_and_draw_requires_approval(self):
        files, scenario = inputs()
        terms = {**json.loads(files['funding_terms.json']), 'opening_cash_pence': 2_000_000_000,
                 'opening_forecast_pence': 800_000_000}
        desk = FinancialDesk(terms, scenario)
        desk.advance(1800)
        amount = 3_500_000_000
        action = {'action': 'submit', 'kind': 'repo', 'idempotency_key': 'repo', 'amount_pence': amount}
        order = desk.act(action)
        self.assertEqual(desk.act(action)['id'], order['id'])
        with self.assertRaises(Rejected):
            desk.act({**action, 'amount_pence': amount + 1})
        with self.assertRaises(Rejected):
            desk.act({**action, 'idempotency_key': 'overlimit'})
        desk.advance(2520)
        self.assertEqual(order['status'], 'rejected')
        line = {'action': 'submit', 'kind': 'line', 'idempotency_key': 'line',
                'amount_pence': amount, 'approval_id': 'A1'}
        with self.assertRaises(Rejected):
            desk.act(line)
        desk.act({'action': 'approval', 'amount_pence': amount})
        desk.advance(2879)
        with self.assertRaises(Rejected):
            desk.act(line)
        desk.advance(2880)
        desk.act(line)
        desk.advance(3300)
        self.assertEqual(desk.cash, 2_000_000_000)
        desk.advance(3720)
        self.assertEqual(desk.cash, 5_500_000_000)
        desk.advance(4200)
        self.assertEqual(desk.obligations['M1']['status'], 'missed')


class FinancialExecutionTest(unittest.IsolatedAsyncioTestCase):
    async def test_rule_trajectories_are_frozen_exported_and_rejudged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / 'liteeval.db')
            service = benchmark_service(store)
            project = store.create_project('finance fixture')['id']
            release = service.publish(project, PACKAGE)
            agent = register(service, project, PACKAGE.parents[1] / 'harnesses/financial_reference')
            workflow = register(service, project, PACKAGE / 'workflow')
            with self.assertRaisesRegex(ValueError, 'registered Workflow'):
                service.preflight(project, release['id'], agent, trusted_local=True)
            result = await service.run(project, release['id'], agent, workflow_ref=workflow, trusted_local=True)
            self.assertEqual(result['summary']['pass'], 3)
            self.assertEqual(result['usage']['record_count'], 0)
            metrics = [t['checks'][0]['metrics'] for t in result['tasks']]
            self.assertEqual([m['fees_pence'] for m in metrics], [9_170_000, 2_170_000, 0])
            self.assertEqual([m['payments_on_time'] for m in metrics], [2, 2, 1])
            self.assertEqual([m['detected_before_m1'] for m in metrics], [True, True, None])
            self.assertEqual([m['false_alarms'] for m in metrics], [0, 0, 0])
            again = await service.rejudge(project, result['execution_id'])
            self.assertEqual([t['grading'] for t in again['tasks']], [t['grading'] for t in result['tasks']])
            archive = root / 'evidence.zip'
            export_execution(service, project, result['execution_id'], archive)
            with zipfile.ZipFile(archive) as bundle:
                for task in result['tasks']:
                    payload = bundle.read(f"submissions/{task['task_id']}/{FINANCIAL_EVIDENCE}")
                    self.assertEqual(payload, service.blobs.get(task['submission'][FINANCIAL_EVIDENCE]['sha256']))

    async def test_model_can_only_read_public_files_and_no_actions_fail_settlement(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'liteeval.db')
            service = benchmark_service(store)
            project = store.create_project('model protocol fixture')['id']
            release = service.publish(project, PACKAGE)

            agent = register(service, project, PACKAGE.parents[1] / 'harnesses/json_action')
            workflow = register(service, project, PACKAGE / 'workflow')
            requests = []

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args): pass

                def do_POST(self):
                    body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    requests.append(body)
                    action = {'action': 'read', 'path': 'workflow-fixtures/scenario-1.json'} if len(body['messages']) == 3 else {'action': 'wait'}
                    reply = {'model': 'fixture', 'usage': {'prompt_tokens': 8},
                             'choices': [{'message': {'content': json.dumps(action)}}]}
                    data = json.dumps(reply).encode()
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)

            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.dict(os.environ, {'LITEEVAL_SECRET_KEY': Fernet.generate_key().decode(), 'NO_PROXY': '127.0.0.1'}):
                    connection = connections.create(store, {'project_id': project, 'name': 'fixture', 'kind': 'openai_compatible',
                        'endpoint': f'http://127.0.0.1:{server.server_port}/v1', 'model': 'fixture', 'api_key': 'local-fixture-only'})
                    result = await service.run(project, release['id'], agent, target_connection=connection['id'],
                                               workflow_ref=workflow, trusted_local=True)
            finally:
                await asyncio.to_thread(server.shutdown)
                server.server_close()
                thread.join(timeout=1)
            self.assertTrue(requests)
            self.assertNotIn('repo_rejected', json.dumps(requests))
            self.assertEqual(result['summary']['fail'], 3)
            self.assertGreater(result['usage']['record_count'], 0)
            for task in result['tasks']:
                self.assertEqual(task['grading']['score'], 0)
                journal = json.loads(service.blobs.get(task['submission'][FINANCIAL_EVIDENCE]['sha256']))['journal']
                first = next(row for row in journal if row['kind'] == 'action')
                self.assertFalse(first['result']['ok'])

    async def test_live_clock_advances_during_model_call_and_rejects_late_order(self):
        files, scenario = inputs()
        now = 0
        evidence = []

        async def decide(observation, previous_result, remaining):
            nonlocal now
            now += 401 if now == 0 else 1100
            return {'action': 'submit', 'kind': 'repo', 'amount_pence': 3_500_000_000,
                    'idempotency_key': 'late-repo'}

        with patch.object(controller, 'time', SimpleNamespace(monotonic=lambda: now)):
            await controller.run_shift(files, scenario, 'live', decide, lambda event: None, evidence.append)
        action = next(row for row in evidence[0]['journal'] if row['kind'] == 'action')
        self.assertEqual(action['at_s'], 2406)
        self.assertFalse(action['result']['ok'])
        self.assertEqual(evidence[0]['closing_state']['now_s'], 9000)


if __name__ == '__main__':
    unittest.main()
