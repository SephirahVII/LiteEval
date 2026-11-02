"""Direct CSV fixtures; no model calls and no benchmark results."""
import json
from pathlib import Path
import tempfile
import unittest
from benchmarks.fab_v2_direct.__main__ import run_case, SOURCE
from liteeval.integrations.benchmarks.fab_source import load_source


class DirectFabTest(unittest.IsolatedAsyncioTestCase):
    async def test_files_and_fenced_judge_use_shared_criterion_protocol(self):
        provenance, rows = load_source(SOURCE)
        captured = []
        class Target:
            config = {'model': 'fixture-target'}
            async def respond(self, body, on_event):
                captured.append(body); on_event({'type': 'response.created'})
                return {'model': 'fixture-target', 'output': [{'type': 'function_call', 'name': 'submit_answer',
                    'arguments': json.dumps({'answer_markdown': 'PROTOCOL FIXTURE: no financial research.', 'sources': []})}],
                    'usage': {'input_tokens': 3, 'output_tokens': 2}}
        class Judge:
            config = {'model': 'fixture-judge'}
            async def generate(self, messages):
                request = json.loads(messages[1]['content'])
                if len(request['rubric']['criteria']) != 7: raise AssertionError('Incorrect source row')
                verdicts = [{'id': c['id'], 'passed': True, 'reason': 'SCRIPTED PROTOCOL FIXTURE'} for c in request['rubric']['criteria']]
                text = '```json\n' + json.dumps({'schema_version': 1, 'check_id': request['check_id'], 'verdicts': verdicts}) + '\n```'
                return text, {'model': 'fixture-judge', 'usage': {'input_tokens': 5, 'output_tokens': 4}}
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / 'case'
            result = await run_case(rows[2], 3, provenance, Target(), [Judge()], directory)
            self.assertEqual(result['execution_status'], 'succeeded')
            self.assertEqual(result['judges'][0]['score'], 1)
            self.assertEqual(len(result['usage']), 2)
            self.assertTrue((directory / 'sources.json').is_file())
            self.assertEqual(result['submission']['response.md']['size_bytes'], (directory / 'response.md').stat().st_size)
            self.assertTrue((directory / 'target-1.json').exists())
            self.assertTrue((directory / 'judge-1.json').exists())
        self.assertNotIn('rubric', captured[0])
        self.assertNotIn(json.loads(rows[2]['Rubric'])[0]['criteria'], captured[0]['input'])

    async def test_target_error_keeps_attempt_and_does_not_fabricate_file(self):
        provenance, rows = load_source(SOURCE)
        class Target:
            config = {'model': 'fixture-error'}
            async def respond(self, body, on_event): raise RuntimeError('Fixture transport failure')
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / 'case'
            result = await run_case(rows[2], 3, provenance, Target(), [], directory)
            self.assertEqual(result['execution_status'], 'failed')
            self.assertEqual(result['usage'][0]['status'], 'error')
            self.assertFalse((directory / 'response.md').exists())
            self.assertIn('Fixture transport failure', json.loads((directory / 'result.json').read_text())['error'])

    async def test_bad_judge_retains_answer_and_unscored_record(self):
        provenance, rows = load_source(SOURCE)
        class Target:
            config = {'model': 'fixture'}
            async def respond(self, body, on_event):
                return {'output': [{'type': 'function_call', 'name': 'submit_answer', 'arguments': json.dumps({
                    'answer_markdown': 'PROTOCOL FIXTURE', 'sources': []})}]}
        class Judge:
            config = {'model': 'fixture'}
            async def generate(self, messages): return '{}', {'usage': {}}
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / 'case'
            result = await run_case(rows[3], 4, provenance, Target(), [Judge()], directory)
            self.assertEqual(result['judges'][0]['outcome'], 'unscored')
            self.assertIsNone(result['judges'][0]['score'])
            self.assertTrue((directory / 'response.md').exists())


if __name__ == '__main__': unittest.main()
