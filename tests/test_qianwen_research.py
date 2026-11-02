"""Research submission protocol tests; no real provider calls or account login."""
import json
import unittest
from liteeval.integrations.harnesses.qianwen_research import QianwenResearchHarness


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

        result = await QianwenResearchHarness().run({'prompt': 'Fixture question'},
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
            await QianwenResearchHarness().run({'prompt': 'Fixture question'},
                {'runtime': Runtime(), 'respond': respond, 'model': 'fixture', 'emit': lambda *a: None})
        self.assertEqual(files, {})


if __name__ == '__main__': unittest.main()
