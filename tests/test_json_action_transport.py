import io
import asyncio
import json
import unittest
import urllib.error
from pathlib import Path
import tempfile
from unittest.mock import patch

from harnesses.json_action.agent import post,load_messages,save_messages
from liteeval.integrations.harnesses.native import EventAgentHarness


class JsonActionTransportTest(unittest.TestCase):
    def test_large_conversation_uses_constant_size_integrity_checked_handle(self):
        with tempfile.TemporaryDirectory() as root, patch('harnesses.json_action.agent.HISTORY',Path(root)/'state/conversation.json'):
            messages=[{'role':'user','content':'x'*200000}]
            state=save_messages(messages)
            self.assertLess(len(json.dumps(state)),200)
            self.assertEqual(load_messages({'state':state}),messages)
            (Path(root)/'state/conversation.json').write_text('[]',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'digest mismatch'):
                load_messages({'state':state})

    def test_transient_failures_retry_and_emit_unknown_usage(self):
        events=[]
        with patch('harnesses.json_action.agent.urllib.request.build_opener') as factory, patch('harnesses.json_action.agent.time.sleep'), patch('harnesses.json_action.agent.send',side_effect=lambda event_type,**kw:events.append((event_type,kw))):
            factory.return_value.open.side_effect=[urllib.error.URLError('temporary'),io.BytesIO(b'{"ok":true}')]
            self.assertEqual(post(object(),'test-model'),b'{"ok":true}')
            self.assertEqual(factory.return_value.open.call_count,2)
        self.assertEqual([e[1]['record']['usage'] for e in events if e[0]=='usage'],[{}])
        self.assertEqual(sum(e[1].get('payload',{}).get('event')=='model_request_retry' for e in events),1)
        self.assertTrue(all(e[1].get('kind') in ('model_request','provider_event') for e in events if e[0]=='event'))

    def test_auth_error_does_not_retry(self):
        with patch('harnesses.json_action.agent.urllib.request.build_opener') as factory, patch('harnesses.json_action.agent.send'):
            factory.return_value.open.side_effect=urllib.error.HTTPError('https://example.test',401,'denied',{},None)
            with self.assertRaisesRegex(RuntimeError,'401'):
                post(object(),'test-model')
            self.assertEqual(factory.return_value.open.call_count,1)

    def test_attempts_are_bounded(self):
        with patch('harnesses.json_action.agent.urllib.request.build_opener') as factory, patch('harnesses.json_action.agent.time.sleep'), patch('harnesses.json_action.agent.send'):
            factory.return_value.open.side_effect=urllib.error.URLError('temporary')
            with self.assertRaisesRegex(RuntimeError,'3 attempt'):
                post(object(),'test-model')
            self.assertEqual(factory.return_value.open.call_count,3)

    def test_retry_messages_are_accepted_by_platform_protocol(self):
        messages=[]
        with patch('harnesses.json_action.agent.urllib.request.build_opener') as factory, patch('harnesses.json_action.agent.time.sleep'), patch('harnesses.json_action.agent.send',side_effect=lambda event_type,**kw:messages.append({'schema_version':1,'type':event_type,**kw})):
            factory.return_value.open.side_effect=[urllib.error.URLError('temporary'),io.BytesIO(b'{}')]
            post(object(),'test-model')
        messages.append({'schema_version':1,'type':'result','result':{'schema_version':1,'output':{'action':'wait'}}})
        adapter=EventAgentHarness()
        async def execute(public,context,receive):
            for message in messages:
                receive('stdout',json.dumps(message)+'\n')
        adapter.execute=execute
        events=[]
        result=asyncio.run(adapter.run({}, {'emit':lambda *a:events.append(a),'observe_usage':lambda _:None}))
        self.assertEqual(result['output'],{'action':'wait'})
        self.assertTrue(any(k=='provider_event' for k,_ in events))
