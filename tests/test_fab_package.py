"""FAB conversion and rubric protocols. Fixtures here are not real model scores."""
import asyncio
from collections import Counter
import csv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from liteeval.bootstrap import benchmark_service
from liteeval.domain.rubrics import grade_rubric, parse_judge_json
from liteeval.domain.values import decoded
from liteeval.infrastructure.persistence.store import Store
from liteeval.infrastructure.sources.harness import load_harness
from liteeval.infrastructure.sources.package import DirectoryLoader
from liteeval.integrations.benchmarks.fab_package import convert_fab, package_files
from liteeval.application import connections

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'benchmarks/fab_v2_direct'
PACKAGE = ROOT / 'benchmarks/fab_v2_public'


class FabPackageTest(unittest.TestCase):
    def test_judge_json_accepts_only_complete_objects_or_single_fences(self):
        value = {'schema_version': 1, 'check_id': 'test', 'verdicts': []}
        body = json.dumps(value)
        for text in (body, '\n' + body + '\n', '```json\n' + body + '\n```', '```\r\n' + body + '\r\n```'):
            self.assertEqual(parse_judge_json(text), value)
        for text in ('[]', 'null', '{"score":NaN}', 'Explanation\n' + body,
                     '```json\n' + body, '```json\n' + body + '\n```\nextra',
                     body + body, '```json\n{}\n```\n```json\n{}\n```'):
            with self.assertRaises(ValueError): parse_judge_json(text)

    def test_all_questions_and_original_checks_are_preserved(self):
        package = DirectoryLoader().load(PACKAGE)
        with (SOURCE / 'public.csv').open(encoding='utf-8-sig', newline='') as stream:
            original = list(csv.DictReader(stream))
        self.assertEqual(len(package.tasks), 27)
        self.assertEqual(set(Counter(t.metadata['category'] for t in package.tasks).values()), {3})
        count = 0
        for index, task in enumerate(package.tasks, 1):
            row = original[index - 1]
            rubric = decoded(package.blobs[task.judge['resources']['judge/rubric.json']])
            raw = decoded(row['Rubric'])
            self.assertEqual([c['source'] for c in rubric['criteria']], raw)
            for criterion, upstream in zip(rubric['criteria'], raw):
                self.assertEqual(criterion['weight'], upstream['modifiers']['severity'])
                self.assertEqual(criterion['must_pass'], upstream['modifiers'].get('category') == 'must_pass')
            self.assertEqual(package.blobs[task.workspace['question.txt']].decode('utf-8'), row['Question'])
            self.assertIn(row['Question'], task.prompt)
            self.assertIsNone(task.judge['ground_truth_file'])
            self.assertFalse(any('rubric' in n or 'ground_truth' in n for n in task.workspace))
            self.assertEqual(task.budget['target_timeout_seconds'], 7200)
            self.assertIn('web.search', task.capabilities)
            self.assertEqual(task.metadata['metadata']['expert_time_minutes'], int(row['Expert time (mins)']))
            count += len(rubric['criteria'])
        self.assertEqual(count, 239)

    def test_conversion_is_exact_idempotent_and_does_not_overwrite_edits(self):
        expected = package_files(SOURCE)
        actual = {p.relative_to(PACKAGE).as_posix(): p.read_bytes() for p in PACKAGE.rglob('*') if p.is_file()}
        self.assertEqual(actual, expected)
        with tempfile.TemporaryDirectory() as root:
            crlf_source = Path(root) / 'crlf-source'
            shutil.copytree(SOURCE, crlf_source)
            license_path = crlf_source / 'LICENSE'
            license_path.write_bytes(license_path.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
            self.assertEqual(package_files(crlf_source), expected)
            destination = Path(root) / 'package'
            first = convert_fab(SOURCE, destination, [4, 3])
            second = convert_fab(SOURCE, destination, [3, 4])
            self.assertEqual(first['digest'], second['digest']); self.assertTrue(second['reused'])
            prompt = destination / 'tasks/fabv2-public-003/prompt.md'
            prompt.write_text('edited', encoding='utf-8')
            with self.assertRaises(ValueError): convert_fab(SOURCE, destination, [3, 4])
            self.assertEqual(prompt.read_text(), 'edited')

    def test_bad_source_selection_and_docker_profile_do_not_publish(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / 'source'; shutil.copytree(SOURCE, source)
            (source / 'public.csv').write_bytes((source / 'public.csv').read_bytes() + b'\n')
            with self.assertRaisesRegex(ValueError, 'SHA-256'): package_files(source)
            for rows in ([], [0], [28], [True], [1, 1]):
                with self.assertRaises(ValueError): package_files(SOURCE, rows)
            destination = Path(root) / 'invalid-image'
            with self.assertRaises(ValueError): convert_fab(SOURCE, destination, docker_image='python:latest')
            self.assertFalse(destination.exists())

    def test_partial_credit_gating_and_all_pass(self):
        rubric = {'schema_version': 1, 'criteria': [
            {'id': 'required', 'description': 'Core fact', 'weight': 2, 'must_pass': True},
            {'id': 'other', 'description': 'Extra fact', 'weight': 1}]}
        decisions = [{'id': 'required', 'passed': True, 'reason': 'yes'}, {'id': 'other', 'passed': False, 'reason': 'missing'}]
        grade = grade_rubric(rubric, decisions)
        self.assertAlmostEqual(grade['partial_credit'], 2 / 3); self.assertFalse(grade['all_pass'])
        decisions[0]['passed'] = False; decisions[1]['passed'] = True
        grade = grade_rubric(rubric, decisions)
        self.assertEqual(grade['score'], 0); self.assertAlmostEqual(grade['before_gate'], 1 / 3)
        decisions[0]['passed'] = True
        self.assertTrue(grade_rubric(rubric, decisions)['all_pass'])
        for bad in ([], decisions + [decisions[0]], [decisions[0], decisions[0]],
                    [{'id': 'required', 'passed': 1, 'reason': 'not boolean'}, decisions[1]],
                    [{**decisions[0], 'weight': 999}, decisions[1]]):
            with self.assertRaises(ValueError): grade_rubric(rubric, bad)

    def test_plain_model_harness_cannot_claim_research_readiness(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root) / 'db.sqlite'); project = store.create_project('FAB')['id']
            service = benchmark_service(store); release = service.publish(project, PACKAGE)
            reused = service.publish(project, PACKAGE)
            self.assertTrue(reused['reused'])
            self.assertEqual(release['manifest'], reused['manifest'])
            with self.assertRaisesRegex(ValueError, 'missing Harness capabilities: web.fetch, web.search'):
                service.preflight(project, release['id'], 'liteeval.model-json/v1', trusted_local=True)
            with store.connect() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM benchmark_executions').fetchone()[0], 0)


class FabRubricProtocolTest(unittest.IsolatedAsyncioTestCase):
    async def test_one_judge_request_per_question_and_missing_verdict_is_unscored(self):
        """Use a scripted Judge HTTP fixture and placeholder Target, never a real LLM."""
        with tempfile.TemporaryDirectory() as root:
            root = Path(root); store = Store(root / 'db.sqlite'); project = store.create_project('fixture')['id']
            convert_fab(SOURCE, root / 'package', [3, 4])
            service = benchmark_service(store); release = service.publish(project, root / 'package')
            harness = root / 'harness'; harness.mkdir()
            harness.joinpath('harness.toml').write_text('''schema_version=1
[harness]
id="fixture.research"
role="agent"
version="1"
protocol="liteeval.agent/v1"
[runtime]
command=["{python}","{harness}/agent.py"]
[capabilities]
provides=["web.search","web.fetch"]
''', encoding='utf-8')
            harness.joinpath('agent.py').write_text('''import json,sys
from pathlib import Path
request=json.load(sys.stdin)
assert 'rubric' not in request and 'ground_truth' not in request
assert not list(Path.cwd().rglob('rubric.json'))
Path('response.md').write_text('PROTOCOL FIXTURE ONLY: no financial research or answer.',encoding='utf-8')
print(json.dumps({'schema_version':1,'output':'Protocol fixture'}))
''', encoding='utf-8')
            spec, sha = load_harness(harness, service.blobs)
            agent_id = service.repository.register(project, 'fixture.research', 'agent', sha, spec)
            received = []; incomplete = [False]; fenced = [False]

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args): pass

                def do_POST(self):
                    data = decoded(self.rfile.read(int(self.headers['Content-Length'])))
                    body = decoded(data['messages'][1]['content']); received.append(body)
                    verdicts = [{'id': c['id'], 'passed': True, 'reason': 'SCRIPTED PROTOCOL FIXTURE'} for c in body['rubric']['criteria']]
                    if incomplete[0]: verdicts.pop()
                    answer = {'schema_version': 1, 'check_id': body['check_id'], 'verdicts': verdicts}
                    content = json.dumps(answer)
                    if fenced[0]: content = '```json\n' + content + '\n```'
                    raw = {'model': 'protocol-fixture', 'choices': [{'message': {'content': content}}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 2}}
                    payload = json.dumps(raw).encode()
                    self.send_response(200); self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(payload))); self.end_headers(); self.wfile.write(payload)

            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                connection = connections.create(store, {'project_id': project, 'name': 'fixture', 'kind': 'openai_compatible',
                    'endpoint': f'http://127.0.0.1:{server.server_port}/v1', 'model': 'protocol-fixture'})
                result = await service.run(project, release['id'], agent_id, judge_connection=connection['id'], trusted_local=True)
                self.assertEqual(len(received), 2)
                self.assertEqual(result['rubric_metrics']['graded_cases'], 2)
                self.assertEqual(result['usage']['record_count'], 2)
                self.assertEqual([len(t['checks'][0]['criteria']) for t in result['tasks']], [7, 4])
                self.assertTrue(all(b['ground_truth'] is None for b in received))
                fenced[0] = True
                rejudged = await service.rejudge(project, result['execution_id'], judge_connection=connection['id'])
                self.assertEqual(rejudged['rubric_metrics']['graded_cases'], 2)
                self.assertEqual(len(received), 4)
                incomplete[0] = True
                bad = await service.run(project, release['id'], agent_id, judge_connection=connection['id'], trusted_local=True)
                self.assertEqual(bad['summary']['unscored'], 2)
                self.assertIsNone(bad['rubric_metrics']['partial_credit_mean_scored'])
                self.assertEqual(bad['rubric_metrics']['scoring_coverage'], 0)
            finally:
                await asyncio.to_thread(server.shutdown); server.server_close(); thread.join(timeout=1)


if __name__ == '__main__': unittest.main()
