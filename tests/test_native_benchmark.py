"""Behavioral acceptance against real subprocesses and persisted directory releases."""
import asyncio
import ast
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile
import os
from liteeval.bootstrap import benchmark_service
from liteeval.infrastructure.persistence.store import Store
from liteeval.infrastructure.sources.harness import load_harness
from liteeval.domain.errors import Conflict, NotFound
from liteeval.application.benchmark_exports import export_execution

ROOT = Path(__file__).resolve().parents[1]


class NativeBenchmarkTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'db.sqlite')
        self.project = self.store.create_project('benchmark')['id']
        self.service = benchmark_service(self.store)
        self.package = self.root / 'bench'
        self.harness = self.root / 'harness'
        shutil.copytree(ROOT / 'benchmarks/native_finance', self.package)
        shutil.copytree(ROOT / 'harnesses/finance_command', self.harness)

    def tearDown(self):
        self.temp.cleanup()

    def register(self):
        spec, sha = load_harness(self.harness, self.service.blobs)
        return self.service.repository.register(self.project, spec['definition']['harness']['id'], 'agent', sha, spec)

    def publish(self):
        return self.service.publish(self.project, self.package)

    async def execute(self):
        return await self.service.run(self.project, self.publish()['id'], self.register(), trusted_local=True)

    async def test_real_process_files_binary_grading_export_and_rejudge(self):
        result = await self.execute()
        self.assertEqual(result['summary']['pass'], 2)
        self.assertEqual(result['summary']['scoring_coverage'], 1)
        records = self.service.repository.execution(self.project, result['execution_id'])
        self.assertTrue(any(e['kind'] == 'log' and 'computed growth=' in e['payload']['text'] for e in records['events']))
        for task in result['tasks']:
            meta = task['submission']['outputs/source-copy.bin']
            self.assertEqual(self.service.blobs.get(meta['sha256']), bytes(range(256)))
            self.assertEqual(task['usage_records'], [])
        archive = self.root / 'out.zip'
        export_execution(self.service, self.project, result['execution_id'], archive)
        with zipfile.ZipFile(archive) as z:
            self.assertEqual(z.read('submissions/growth-001/outputs/source-copy.bin'), bytes(range(256)))
        self.assertFalse(list((self.root / 'benchmark-workspaces').iterdir()))
        self.harness.joinpath('agent.py').write_text('raise RuntimeError("MUST NOT RERUN TARGET")', encoding='utf-8')
        rejudged = await self.service.rejudge(self.project, result['execution_id'])
        self.assertTrue(all(t['grading']['outcome'] == 'pass' for t in rejudged['tasks']))

    async def test_published_assets_survive_source_removal(self):
        release, harness = self.publish(), self.register()
        shutil.rmtree(self.package); shutil.rmtree(self.harness)
        result = await self.service.run(self.project, release['id'], harness, trusted_local=True)
        self.assertEqual(result['summary']['pass'], 2)

    def test_version_idempotence_and_content_conflict(self):
        a, b = self.publish(), self.publish()
        self.assertEqual(a['id'], b['id'])
        self.assertTrue(b['reused'])
        self.assertEqual(a['manifest'], b['manifest'])
        prompt = self.package / 'tasks/growth-001/prompt.md'
        prompt.write_text(prompt.read_text() + 'Changed prompt', encoding='utf-8')
        with self.assertRaises(Conflict): self.publish()

    async def test_wrong_answer_is_fail_not_process_failure(self):
        p = self.harness / 'agent.py'
        p.write_text(p.read_text().replace('growth = float((last - first) / first)', 'growth = 42.0'), encoding='utf-8')
        result = await self.execute()
        self.assertEqual(result['summary']['fail'], 2)
        self.assertTrue(all(t['execution_status'] == 'succeeded' for t in result['tasks']))

    async def test_judge_crash_is_unscored_not_candidate_failure(self):
        for p in self.package.glob('tasks/*/judge/test.py'):
            p.write_text('raise RuntimeError("Judge is broken")', encoding='utf-8')
        result = await self.execute()
        self.assertEqual(result['summary']['unscored'], 2)
        self.assertEqual(result['summary']['scoring_coverage'], 0)

    async def test_missing_required_artifact_fails(self):
        p = self.harness / 'agent.py'
        p.write_text(p.read_text().replace("(output / 'report.md').write_text", "(output / 'unexpected.md').write_text"), encoding='utf-8')
        result = await self.execute()
        self.assertEqual(result['summary']['fail'], 2)
        self.assertTrue(all(t['missing_required'] == ['outputs/report.md'] for t in result['tasks']))

    async def test_timeout_cleans_workspaces_and_is_unscored(self):
        self.harness.joinpath('agent.py').write_text('import time;time.sleep(30)', encoding='utf-8')
        p = self.package / 'dataset.toml'
        p.write_text(p.read_text().replace('target_timeout_seconds = 30', 'target_timeout_seconds = 0.1'), encoding='utf-8')
        result = await self.execute()
        self.assertTrue(all(t['execution_status'] == 'timed_out' for t in result['tasks']))
        self.assertEqual(result['summary']['unscored'], 2)
        self.assertFalse(list((self.root / 'benchmark-workspaces').iterdir()))

    async def test_cancel_persists_cancelled_status_and_cleans(self):
        self.harness.joinpath('agent.py').write_text('import time;time.sleep(30)', encoding='utf-8')
        release, harness = self.publish(), self.register()
        task = asyncio.create_task(self.service.run(self.project, release['id'], harness, trusted_local=True))
        for _ in range(100):
            await asyncio.sleep(.01)
            with self.store.connect() as db:
                rows = db.execute('SELECT id FROM benchmark_executions').fetchall()
            if rows: break
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        record = self.service.repository.execution(self.project, rows[0][0])
        self.assertEqual(record['status'], 'cancelled')
        self.assertFalse(list((self.root / 'benchmark-workspaces').iterdir()))

    def test_explicit_trust_and_project_scope(self):
        release, harness = self.publish(), self.register()
        with self.assertRaises(ValueError): self.service.preflight(self.project, release['id'], harness)
        other = self.store.create_project('foreign')['id']
        with self.assertRaises(NotFound): self.service.preflight(other, release['id'], harness, trusted_local=True)

    def test_unknown_fields_and_path_traversal_rejected(self):
        p = self.package / 'tasks/growth-001/task.toml'
        original = p.read_text()
        p.write_text(original.replace('prompt_file = "prompt.md"', 'prompt_file = "../judge/ground_truth.json"'), encoding='utf-8')
        with self.assertRaises(ValueError): self.publish()
        p.write_text(original.replace('[input]', '[input]\nunknown_flag = true'), encoding='utf-8')
        with self.assertRaises(ValueError): self.publish()

    def test_corrupted_resource_detected_before_execution(self):
        release, harness = self.publish(), self.register()
        sha = next(iter(release['manifest']['source_files'].values()))
        (self.service.blobs.root / sha).write_bytes(b'corrupt')
        with self.assertRaises(ValueError): self.service.preflight(self.project, release['id'], harness, trusted_local=True)

    def test_docker_requires_digest_and_has_no_workspace_fallback(self):
        p = self.package / 'dataset.toml'
        p.write_text(p.read_text().replace('type = "workspace"', 'type = "docker"\nimage = "python:latest"'), encoding='utf-8')
        with self.assertRaises(ValueError): self.publish()

    async def test_custom_evaluation_harness_replaces_native_checks(self):
        spec, sha = load_harness(ROOT / 'harnesses/finance_evaluator', self.service.blobs)
        evaluator = self.service.repository.register(self.project, spec['definition']['harness']['id'], 'evaluation', sha, spec)
        # If native checks were also called, they would crash. Custom runner must own grading.
        for p in self.package.glob('tasks/*/judge/test.py'):
            p.write_text('raise RuntimeError("Native Judge MUST NOT execute")', encoding='utf-8')
        result = await self.service.run(self.project, self.publish()['id'], self.register(),
                                        evaluation_ref=evaluator, trusted_local=True)
        self.assertEqual(result['summary']['pass'], 2)
        self.assertTrue(all('Independent custom' in t['checks'][0]['reason'] for t in result['tasks']))

    async def test_model_harness_against_real_http_protocol_server(self):
        """Protocol fixture, not a real LLM evaluation."""
        import csv
        import io
        import sys
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from liteeval.application import connections
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(body)
                messages = body['messages']
                step = sum(m['role'] == 'assistant' for m in messages)
                if step == 0: action = {'action': 'read', 'path': 'data/revenue.csv'}
                elif step == 1:
                    csv_text = json.loads(messages[-1]['content'])['tool_result']
                    rows = list(csv.DictReader(io.StringIO(csv_text)))
                    growth = (float(rows[-1]['revenue']) - float(rows[0]['revenue'])) / float(rows[0]['revenue'])
                    action = {'action': 'write', 'path': 'outputs/result.json', 'text': json.dumps({'growth_rate': growth})}
                elif step == 2: action = {'action': 'write', 'path': 'outputs/report.md', 'text': 'Computed from the provided CSV.'}
                elif step == 3: action = {'action': 'exec', 'command': [sys.executable, '-c', 'import shutil;shutil.copyfile("data/source.bin","outputs/source-copy.bin")']}
                else: action = {'action': 'final', 'answer': 'Files generated.'}
                raw = {'model': 'protocol-fixture-model', 'choices': [{'message': {'content': json.dumps(action)}}],
                       'usage': {'prompt_tokens': 100, 'completion_tokens': 10, 'prompt_tokens_details': {'cached_tokens': 20}}}
                payload = json.dumps(raw).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload))); self.end_headers(); self.wfile.write(payload)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            connection = connections.create(self.store, {'project_id': self.project, 'name': 'fixture',
                'kind': 'openai_compatible', 'endpoint': f'http://127.0.0.1:{server.server_port}/v1', 'model': 'protocol-fixture-model'})
            result = await self.service.run(self.project, self.publish()['id'], 'liteeval.model-json/v1',
                target_connection=connection['id'], trusted_local=True)
            self.assertEqual(result['summary']['pass'], 2)
            self.assertEqual(result['usage']['totals']['input_tokens'], 1000)
            self.assertEqual(result['usage']['totals']['output_tokens'], 100)
            self.assertEqual(result['usage']['totals']['cached_input_tokens'], 200)
            self.assertTrue(all('ground_truth' not in json.dumps(r) for r in requests))
        finally:
            await asyncio.to_thread(server.shutdown); server.server_close(); thread.join(timeout=1)

    def test_read_api_enforces_project_scope(self):
        from liteeval.interfaces.http.api import dispatch
        release = self.publish()
        code, body = dispatch(self.store, 'GET', '/v1/benchmark-releases', principal={'admin': False, 'project_id': self.project})
        self.assertEqual(code, 200)
        self.assertEqual(body['items'][0]['id'], release['id'])
        code, _ = dispatch(self.store, 'GET', '/v1/benchmark-releases?project_id=foreign', principal={'admin': False, 'project_id': self.project})
        self.assertEqual(code, 403)


class ArchitectureTest(unittest.TestCase):
    def test_domain_and_ports_do_not_import_io_or_application(self):
        for directory in ('domain', 'ports'):
            for p in (ROOT / 'liteeval' / directory).rglob('*.py'):
                tree = ast.parse(p.read_text(encoding='utf-8'))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module:
                        self.assertFalse(node.module.startswith(('liteeval.application', 'liteeval.infrastructure', 'liteeval.interfaces', 'liteeval.integrations')), str(p))

    def test_no_flat_compatibility_modules(self):
        self.assertEqual({p.name for p in (ROOT / 'liteeval').glob('*.py')}, {'__init__.py', 'bootstrap.py'})


@unittest.skipUnless(os.getenv('LITEEVAL_TEST_DOCKER_IMAGE'), 'Real Docker acceptance requires a prepared digest-pinned image')
class DockerBenchmarkTest(unittest.IsolatedAsyncioTestCase):
    async def test_real_container_submission_and_judge(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(ROOT / 'benchmarks/native_finance', root / 'bench')
            manifest = root / 'bench/dataset.toml'
            manifest.write_text(manifest.read_text().replace('type = "workspace"',
                'type = "docker"\nimage = "' + os.environ['LITEEVAL_TEST_DOCKER_IMAGE'] + '"'), encoding='utf-8')
            store = Store(root / 'db.sqlite'); project = store.create_project('Docker acceptance')['id']
            service = benchmark_service(store)
            spec, sha = load_harness(ROOT / 'harnesses/finance_command', service.blobs)
            harness = service.repository.register(project, spec['definition']['harness']['id'], 'agent', sha, spec)
            release = service.publish(project, root / 'bench')
            result = await service.run(project, release['id'], harness)
            self.assertEqual(result['summary']['pass'], 2, result)
            self.assertFalse(list((root / 'benchmark-workspaces').iterdir()))
