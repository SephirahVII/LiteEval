import asyncio
from collections import Counter
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from liteeval.application import catalog as catalog
from liteeval.integrations.agents.codex import CodexAdapter, parse_jsonl
from liteeval.application.configuration import validate_agent
from liteeval.domain.contracts import EvalCase
from liteeval.integrations.benchmarks.finance_v2 import load_bundle, publish, grade_checks
from liteeval.infrastructure.persistence.store import Store
from liteeval.application.usage import normalize

BUNDLE=Path(__file__).resolve().parents[1]/'benchmarks/finance_agent_v2'


class FinanceBenchmarkTest(unittest.TestCase):
    def test_source_provenance_categories_and_target_isolation(self):
        source,records=load_bundle(BUNDLE)
        self.assertEqual(len(records),27);self.assertEqual(source['license'],'MIT')
        self.assertEqual(len(Counter(r['metadata']['category'] for r in records)),9)
        self.assertEqual(set(Counter(r['metadata']['category'] for r in records).values()),{3})
        for index,record in enumerate(records,1):
            self.assertEqual(record['metadata']['source_row'],index)
            self.assertEqual(record['metadata']['benchmark_source']['commit'],source['commit'])
            self.assertNotIn('rubric',record['input'])
            self.assertNotIn(record['expected']['rubric'][0]['criteria'],record['input']['prompt'])

    def test_import_fixed_versions_idempotence_and_selection(self):
        with tempfile.TemporaryDirectory() as root:
            store=Store(Path(root)/'bench.db');pid=store.create_project('Bench')['id']
            full=publish(store,pid,BUNDLE);smoke=publish(store,pid,BUNDLE,[3,4])
            self.assertEqual(full['version']['case_count'],27);self.assertEqual(smoke['version']['case_count'],2)
            self.assertNotEqual(full['dataset_id'],smoke['dataset_id'])
            original=catalog.version_cases(store,smoke['version']['id'])['items']
            case=store.list_cases(smoke['dataset_id'])['items'][0]
            store.revise_case(case['id'],{'prompt':'changed'},None)
            catalog.publish_dataset(store,smoke['dataset_id'],'edited-draft')
            reused=publish(store,pid,BUNDLE,[3,4])
            self.assertTrue(reused['reused']);self.assertEqual(reused['version']['id'],smoke['version']['id'])
            self.assertEqual(catalog.version_cases(store,smoke['version']['id'])['items'],original)
            for rows in ([],[0],[28],[1,1],[True]):
                with self.assertRaises(ValueError):publish(store,pid,BUNDLE,rows)

    def test_modified_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            shutil.copy(BUNDLE/'source.json',root)
            (Path(root)/'public.csv').write_bytes((BUNDLE/'public.csv').read_bytes()+b'\n')
            with self.assertRaisesRegex(ValueError,'SHA-256'):load_bundle(root)

    def test_weighted_credit_and_dealbreakers(self):
        rubric=[{'criteria':'critical','modifiers':{'severity':2,'category':'must_pass'}},
                {'criteria':'other','modifiers':{'severity':1}}]
        checks=[{'index':0,'passed':True,'reason':'supported'},{'index':1,'passed':False,'reason':'missing'}]
        result=grade_checks(rubric,checks)
        self.assertAlmostEqual(result['partial_credit'],2/3);self.assertFalse(result['all_pass'])
        checks[0]['passed']=False;checks[1]['passed']=True
        self.assertEqual(grade_checks(rubric,checks)['partial_credit'],0)
        for bad in ([],[checks[0],checks[0]],[{'index':0,'passed':1,'reason':'invalid'},checks[1]]):
            with self.assertRaises(ValueError):grade_checks(rubric,bad)

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
