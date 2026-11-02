"""Codex adapter protocol tests with a fixture, never account login."""
import asyncio
import json
import os
import unittest
from unittest.mock import patch
from liteeval.integrations.agents.codex import CodexAdapter, parse_jsonl
from liteeval.application.configuration import validate_agent
from liteeval.domain.contracts import EvalCase
from liteeval.domain.usage import normalize


class CodexAdapterTest(unittest.TestCase):
    def test_live_codex_counter_names_and_recovered_error(self):
        events=[{'type':'error','message':'Reconnecting'},
                {'type':'item.completed','item':{'id':'search','type':'web_search','query':'company annual filing'}},
                {'type':'item.completed','item':{'id':'answer','type':'agent_message','text':'answer'}},
                {'type':'turn.completed','usage':{'input_tokens':10,'cached_input_tokens':5,'cache_write_input_tokens':0,
                                                'output_tokens':4,'reasoning_output_tokens':2}}]
        parsed=parse_jsonl('\n'.join(json.dumps(e) for e in events))
        self.assertFalse(parsed['failed']);self.assertEqual(parsed['events'][1].kind,'tool')
        self.assertEqual(parsed['events'][1].name,'web_search')
        normalized=normalize(parsed['usage'])
        self.assertEqual(normalized['cache_write_tokens'],0);self.assertEqual(normalized['reasoning_tokens'],2)

    def test_codex_automation_flags_and_environment_boundary(self):
        class Environment:
            async def execute(self,args,**kwargs):
                self.args=args;self.kwargs=kwargs
                return {'stdout':json.dumps({'type':'item.completed','item':{'id':'m','type':'agent_message','text':'done'}}),
                        'stderr':'','exit_code':0,'timed_out':False}
        config={'type':'codex','environment':{'type':'workspace'},'ignore_user_config':True,'ephemeral':True,
                'web_search':'live','inherit_proxy':True}
        validate_agent(config);environment=Environment()
        with patch.dict(os.environ,{'LITEEVAL_CODEX_BINARY':'fixture.py','HTTPS_PROXY':'http://127.0.0.1:1',
                                    'UNRELATED_API_KEY':'not-forwarded','CODEX_HOME':'local-auth-home'}):
            run=asyncio.run(CodexAdapter(config,environment).invoke(EvalCase('case',1,{'prompt':'question'})))
            self.assertEqual(run.status,'succeeded')
            self.assertIn('--ignore-user-config',environment.args);self.assertIn('--ephemeral',environment.args)
            self.assertIn('web_search="live"',environment.args)
            self.assertEqual(environment.kwargs['env']['HTTPS_PROXY'],'http://127.0.0.1:1')
            self.assertNotIn('UNRELATED_API_KEY',environment.kwargs['env'])
            docker={**config,'environment':{'type':'docker','image':'example:tag'}}
            asyncio.run(CodexAdapter(docker,environment).invoke(EvalCase('case',1,{'prompt':'question'})))
            self.assertNotIn('CODEX_HOME',environment.kwargs['env']);self.assertNotIn('PATH',environment.kwargs['env'])
        for changes in ({'web_search':'unknown'},{'inherit_proxy':1},{'ephemeral':'yes'}):
            with self.assertRaises(ValueError):validate_agent({**config,**changes})


if __name__=='__main__':unittest.main()
