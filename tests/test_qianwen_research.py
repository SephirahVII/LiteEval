"""Research submission protocol tests; no real provider calls or account login."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile
from cryptography.fernet import Fernet
from harnesses.responses_research.research import ResponsesResearch
from liteeval.application import connections
from liteeval.application.benchmark_exports import export_execution
from liteeval.bootstrap import benchmark_service
from liteeval.infrastructure.persistence.store import Store
from liteeval.infrastructure.sources.harness import load_harness
from liteeval.integrations.benchmarks.fab_package import convert_fab


class ResearchSubmissionTest(unittest.IsolatedAsyncioTestCase):
    async def test_requires_model_function_call_and_writes_actual_submission(self):
        files, events = {}, []

        class Runtime:
            def write(self, name, data): files[name] = data

        async def respond(body):
            self.assertIn('web_search', [t['type'] for t in body['tools']])
            self.assertIn('code_interpreter', [t['type'] for t in body['tools']])
            return {'id': 'fixture', 'model': 'fixture', 'output': [
                {'type': 'web_search_call', 'status': 'completed', 'action': {'query': 'company filing'}},
                {'type': 'function_call', 'name': 'submit_answer', 'call_id': 'call-fixture', 'arguments': json.dumps({
                    'answer_markdown': 'Protocol fixture answer', 'sources': [{'url': 'https://example.org/filing', 'title': 'Fixture'}]})}]}

        result = await ResponsesResearch().run({'prompt': 'Fixture question'},
            {'runtime': Runtime(), 'respond': respond, 'model': 'fixture', 'emit': lambda kind, data: events.append((kind, data))})
        self.assertEqual(files['response.md'], b'Protocol fixture answer')
        self.assertIn('sources.json', files)
        self.assertEqual(result['capture_level'], 'provider_tools_and_local_submission')
        self.assertTrue(any(e[1].get('capture') == 'local_tool_execution' for e in events))

    async def test_text_only_reply_does_not_fabricate_a_file(self):
        files = {}

        class Runtime:
            def write(self, name, data): files[name] = data

        async def respond(body):
            return {'id': 'fixture', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'Text-only answer'}]}]}

        with self.assertRaisesRegex(RuntimeError, 'no local file was fabricated'):
            await ResponsesResearch().run({'prompt': 'Fixture question'},
                {'runtime': Runtime(), 'respond': respond, 'model': 'fixture', 'emit': lambda *a: None})
        self.assertEqual(files, {})

    async def test_registered_harness_binds_connection_and_exports_observed_evidence(self):
        requests = []
        fixture_key = 'fixture-local-key-only'
        answer = 'PROTOCOL FIXTURE ONLY'

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((self.path, self.headers.get('Authorization'), body))
                if self.path.endswith('/responses'):
                    item = {'type': 'web_search_call', 'action': {'query': 'fixture'}}
                    raw = {'id': 'fixture-response', 'model': 'fixture-model', 'debug': fixture_key,
                           'usage': {'input_tokens': 8, 'output_tokens': 3, 'output_tokens_details': {'reasoning_tokens': 2}},
                           'output': [item, {'type': 'function_call', 'name': 'submit_answer',
                                            'arguments': json.dumps({'answer_markdown': answer, 'sources': []})}]}
                    events = [{'type': 'response.output_item.done', 'item': item},
                              {'type': 'response.completed', 'response': raw}]
                    data = ''.join('data: ' + json.dumps(event) + '\n\n' for event in events).encode()
                    content_type = 'text/event-stream'
                else:
                    request = json.loads(body['messages'][1]['content'])
                    verdict = {'schema_version': 1, 'check_id': request['check_id'], 'verdicts': [
                        {'id': row['id'], 'passed': True, 'reason': 'Protocol fixture'}
                        for row in request['rubric']['criteria']]}
                    data = json.dumps({'model': 'fixture-model', 'choices': [
                        {'message': {'content': json.dumps(verdict)}}]}).encode()
                    content_type = 'application/json'
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                    'LITEEVAL_SECRET_KEY': Fernet.generate_key().decode(), 'NO_PROXY': '127.0.0.1'}):
                root = Path(directory)
                repo = Path(__file__).resolve().parents[1]
                store = Store(root / 'liteeval.db')
                project = store.create_project('research protocol')['id']
                service = benchmark_service(store)
                convert_fab(repo / 'benchmarks/fab_v2_direct', root / 'package', [3])
                release = service.publish(project, root / 'package')
                spec, sha = load_harness(repo / 'harnesses/responses_research', service.blobs)
                agent = service.repository.register(project, spec['definition']['harness']['id'], 'agent', sha, spec)
                connection = connections.create(store, {'project_id': project, 'name': 'local fixture',
                    'kind': 'openai_compatible', 'endpoint': f'http://127.0.0.1:{server.server_port}/v1',
                    'model': 'fixture-model', 'api_key': fixture_key})['id']
                result = await service.run(project, release['id'], agent, target_connection=connection,
                                           judge_connection=connection, trusted_local=True, target_parameters={'max_tokens': 123})
                self.assertEqual(result['summary']['pass'], 1, result['tasks'])
                self.assertEqual([r[0] for r in requests], ['/v1/responses', '/v1/chat/completions'])
                self.assertEqual(requests[0][1], 'Bearer ' + fixture_key)
                self.assertEqual(requests[0][2]['max_output_tokens'], 123)
                self.assertNotIn('rubric', requests[0][2])
                task = result['tasks'][0]
                self.assertEqual(task['usage_records'][0]['normalized']['reasoning_tokens'], 2)
                path = root / 'evidence.zip'
                export_execution(service, project, result['execution_id'], path)
                with zipfile.ZipFile(path) as archive:
                    events = archive.read('events.jsonl')
                    self.assertIn(b'provider_event', events)
                    self.assertEqual(archive.read(f"submissions/{task['task_id']}/response.md"), answer.encode())
                    raws = [archive.read(name) for name in archive.namelist() if name.startswith('raw-responses/')]
                    self.assertTrue(any(b'_sse_events' in raw for raw in raws))
                    self.assertNotIn(fixture_key.encode(), events + b''.join(raws))
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join(timeout=1)


if __name__ == '__main__': unittest.main()
