import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.request import Request,urlopen
from urllib.error import HTTPError

from liteeval.api import dispatch,make_handler
from liteeval.adapters import HTTPAdapter,select
from liteeval.core import EvalCase,AgentRun,Score,Event,ContentPart
from liteeval.codex import parse_jsonl
from liteeval.environments import WorkspaceEnvironment,DockerEnvironment,snapshot_bytes
from liteeval.process import run_process
from liteeval.evaluators import ConfiguredEvaluator,validate_schema
from liteeval.runner import ExperimentRunner
from liteeval.reports import compare
from liteeval.sdk import TraceClient
from liteeval.storage import Store,Conflict,LeaseLost,SCHEMA

class Service(BaseHTTPRequestHandler):
    def do_POST(self):
        data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        server=self.server
        with server.lock:
            server.calls[self.path]=server.calls.get(self.path,0)+1
            count=server.calls[self.path]
            server.active+=1;server.peak=max(server.active,server.peak)
            server.keys.append(self.headers.get('Idempotency-Key'))
            server.auth.append(self.headers.get('Authorization'))
        try:
            if self.path in {'/slow','/concurrent'}:time.sleep(0.4 if self.path=='/slow' else 0.08)
            if self.path=='/retry' and count==1:
                self.send_response(503);self.end_headers();self.wfile.write(b'{"error":"temporary"}');return
            if self.path=='/judge/chat/completions':
                verdict={'score':0.8,'reason':'correct'}
                content='broken JSON' if server.bad_judge and count==1 else json.dumps(verdict)
                payload={'choices':[{'message':{'content':content}}],'usage':{'prompt_tokens':5,'completion_tokens':3}}
            elif self.path=='/mapped':
                payload={'result':{'answer':data['query']},'events':[{'kind':'tool','name':'lookup','input_parts':[{'kind':'json','json_value':{'id':7}}],'started_at':1,'ended_at':2}],
                         'usage':{'input_tokens':5,'output_tokens':2},'cost':0.1,'currency':'CNY','trajectory_complete':True}
            else:payload={'answer':'ok'}
            body=json.dumps(payload).encode();self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers()
            try:self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError):pass
        finally:
            with server.lock:server.active-=1
    def log_message(self,*args):pass

class MVPTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'db.sqlite')
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Service)
        self.server.calls={};self.server.keys=[];self.server.auth=[];self.server.lock=threading.Lock()
        self.server.active=0;self.server.peak=0;self.server.bad_judge=False
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'
        self.pid=self.store.create_project('Support')['id'];self.ds=self.store.create_dataset(self.pid,'Regression')['id']
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()
    def setup_exp(self,path='/agent',n=1,config=None,evaluators=None,agent_config=None):
        for i in range(n):self.store.add_case(self.ds,{'question':str(i)},'ok')
        agent=self.store.add_agent(self.pid,'v1',agent_config or {'type':'http','endpoint':self.url+path})['id']
        ids=evaluators or [self.store.add_evaluator(self.pid,'exact','exact_match',{})['id']]
        return self.store.create_experiment(self.pid,self.ds,agent,ids,config),agent,ids
    def test_atomic_import_csv_filter_export(self):
        with self.assertRaises(ValueError):self.store.import_cases(self.ds,[{'input':{}},{'input':'bad'}])
        self.assertEqual(self.store.list_cases(self.ds)['total'],0)
        text='input,expected,metadata\n"{""question"":""x""}","""ok""","{""severity"":""high""}"\n'
        self.store.import_cases(self.ds,csv_text=text)
        self.assertEqual(self.store.list_cases(self.ds,severity='high')['total'],1)
        self.assertEqual(self.store.list_cases(self.ds,severity='low')['total'],0)
        self.assertEqual(self.store.export_cases(self.ds)['records'][0]['expected'],'ok')
    def test_idempotency_and_frozen_rerun(self):
        exp,agent,ids=self.setup_exp()
        a=self.store.create_experiment(self.pid,self.ds,agent,ids,idempotency_key='click-1')
        b=self.store.create_experiment(self.pid,self.ds,agent,ids,idempotency_key='click-1')
        self.assertEqual(a['id'],b['id']);self.assertTrue(b['reused'])
        with self.assertRaises(Conflict):self.store.create_experiment(self.pid,self.ds,agent,ids,{'concurrency':2},'click-1')
        case=self.store.export_cases(self.ds)['records'][0]
        self.store.revise_case(case['id'],case['input'],'changed')
        clone=self.store.rerun(exp['id'])
        asyncio.run(ExperimentRunner(self.store).run(exp['id']));asyncio.run(ExperimentRunner(self.store).run(clone['id']))
        self.assertTrue(compare(self.store,exp['id'],clone['id'])['comparable'])
    def test_http_mapping_auth_usage_full_event(self):
        with patch.dict(os.environ,{'TEST_AGENT_TOKEN':'dummy-agent-secret'}):
            run=asyncio.run(HTTPAdapter({'endpoint':self.url+'/mapped','request_template':{'query':'$.input.question'},
                'output_path':'$.result.answer','auth':{'type':'bearer','token_env':'TEST_AGENT_TOKEN'}}).invoke(EvalCase('c',1,{'question':'mapped'})))
        self.assertEqual(run.output,'mapped');self.assertEqual(self.server.auth[-1],'Bearer dummy-agent-secret')
        self.assertEqual(run.events[0].input_parts[0].json_value,{'id':7});self.assertEqual(run.capture_level,'full_harness')
        self.assertEqual(run.usage['input_tokens'],5);self.assertEqual(run.cost,0.1)
        self.assertEqual(select({'a':[{'b':2}]},'$.a[0].b'),2)
    def test_concurrent_cases(self):
        exp,_,_=self.setup_exp('/concurrent',n=8,config={'concurrency':3})
        asyncio.run(ExperimentRunner(self.store).run(exp['id']))
        self.assertGreater(self.server.peak,1);self.assertLessEqual(self.server.peak,3)
        report=self.store.get_experiment(exp['id'])['summary']
        self.assertEqual(report['total_cases'],8);self.assertEqual(report['case_counts']['pass'],8)
        self.assertEqual(report['success_rate'],1)
    def test_retry_attempts_stable_idempotency(self):
        exp,_,_=self.setup_exp('/retry',config={'max_retries':1,'retry_delay_seconds':0})
        asyncio.run(ExperimentRunner(self.store).run(exp['id']))
        result=self.store.get_experiment(exp['id']);run=result['runs'][0]
        self.assertEqual(run['status'],'succeeded')
        self.assertEqual([a['status'] for a in self.store.list_attempts(run['id'])],['failed','succeeded'])
        self.assertEqual(self.server.keys[0],self.server.keys[1]);self.assertEqual(self.server.keys[1],run['id'])
        self.assertGreaterEqual(self.store.list_items('artifacts',self.pid)['total'],4)
    def test_case_failure_does_not_abort_experiment(self):
        exp,agent,ids=self.setup_exp('/slow',n=2,agent_config={'type':'http','endpoint':self.url+'/slow','timeout_seconds':0.05})
        asyncio.run(ExperimentRunner(self.store).run(exp['id']))
        result=self.store.get_experiment(exp['id'])
        self.assertEqual(result['status'],'completed');self.assertEqual(result['summary']['run_status_counts'],{'timed_out':2})
        self.assertEqual(result['summary']['scoring_coverage'],0);self.assertIsNone(result['summary']['scored_pass_rate'])
    def test_cancellation_running_and_queued(self):
        exp,_,_=self.setup_exp('/slow',n=4,config={'concurrency':1})
        async def work():
            task=asyncio.create_task(ExperimentRunner(self.store,lease_seconds=0.3).run(exp['id']))
            await asyncio.sleep(0.08);self.store.cancel(exp['id']);await task
        asyncio.run(work())
        self.assertEqual(self.store.get_experiment(exp['id'])['status'],'cancelled')
        self.assertEqual(self.store.get_experiment(exp['id'])['scores'],[])
        clone=self.store.rerun(exp['id']);self.store.cancel(clone['id'])
        self.assertFalse(asyncio.run(ExperimentRunner(self.store).run(clone['id'])))
    def test_expired_lease_recovery_preserves_run_id(self):
        exp,_,ids=self.setup_exp()
        claim=self.store.claim(exp['id'],1)
        content=self.store.export_cases(self.ds)['records'][0];case=EvalCase(**content)
        attempt=self.store.start_attempt(exp['id'],claim['lease_token'],case)
        with self.store.connect() as db:db.execute('UPDATE experiments SET lease_expires=0 WHERE id=?',(exp['id'],))
        asyncio.run(ExperimentRunner(self.store).run(exp['id']))
        run=self.store.get_experiment(exp['id'])['runs'][0]
        self.assertEqual(run['id'],attempt['run_id'])
        self.assertEqual([a['status'] for a in self.store.list_attempts(run['id'])],['abandoned','succeeded'])
        with self.assertRaises(LeaseLost):self.store.finish_attempt(exp['id'],claim['lease_token'],attempt,AgentRun(run['id'],case.id,'succeeded'),None,[],[])
        self.assertEqual(self.store.get_run(run['id'])['output'],'ok')
    def test_judge_retry_raw_and_no_agent_rerun(self):
        self.server.bad_judge=True
        judge=self.store.add_evaluator(self.pid,'Task','llm_judge',{'base_url':self.url+'/judge','model':'mock',
            'rubric':'Answer correctly','scale':'continuous','pass_threshold':0.7})['id']
        exp,_,_=self.setup_exp(evaluators=[judge])
        with patch.dict(os.environ,{'LITEEVAL_JUDGE_API_KEY':'mock-secret-for-tests'}):asyncio.run(ExperimentRunner(self.store).run(exp['id']))
        self.assertEqual(self.server.calls['/agent'],1);self.assertEqual(self.server.calls['/judge/chat/completions'],2)
        result=self.store.get_experiment(exp['id']);self.assertEqual(result['scores'][0]['value'],0.8)
        self.assertIsNotNone(result['scores'][0]['artifact_id'])
        self.assertEqual(len([a for a in self.store.list_items('artifacts',self.pid)['items'] if a['kind']=='judge_raw']),2)
    def test_missing_judge_credentials_is_error(self):
        with patch.dict(os.environ,{},clear=True):
            score=asyncio.run(ConfiguredEvaluator('judge','llm_judge',{'base_url':self.url,'model':'mock','rubric':'correct'}).evaluate(EvalCase('c',1,{}),AgentRun('r','c','succeeded',output='ok')))
        self.assertEqual(score.outcome,'error')
    def test_api_auth_project_scope_and_key_revocation(self):
        token='admin-test-token-at-least-24-characters'
        api=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.store,token));thread=threading.Thread(target=api.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{api.server_port}'
        key=self.store.issue_key(self.pid)
        other=self.store.create_project('Other')['id']
        def get(path,auth=None):
            request=Request(base+path,headers={'Authorization':'Bearer '+auth} if auth else {})
            try:
                with urlopen(request) as response:return response.status,json.load(response)
            except HTTPError as error:return error.code,json.load(error)
        try:
            self.assertEqual(get('/v1/health')[0],200)
            self.assertEqual(get('/v1/projects')[0],401)
            self.assertEqual(get('/v1/projects/'+other,key['token'])[0],403)
            self.assertEqual(get('/v1/projects',key['token'])[1]['total'],1)
            aid=self.store.save_artifact(other,None,'test',{'x':1})
            self.assertEqual(get('/v1/artifacts/'+aid+'/content',key['token'])[0],403)
            self.store.revoke_key(key['id']);self.assertEqual(get('/v1/projects',key['token'])[0],401)
        finally:api.shutdown();api.server_close();thread.join()
    def test_sdk_nested_and_async_spans(self):
        captured=[];client=TraceClient(self.url,self.pid,'token')
        client.send=lambda *a,**k:captured.append((a,k))
        @client.trace()
        def agent(question):
            with client.span('llm','model',{'question':question}):
                with client.span('tool','lookup',{'id':1}) as span:span.output_parts=[ContentPart('text',text='found')]
            return 'ok'
        self.assertEqual(agent('x'),'ok')
        events=captured[0][0][2];self.assertEqual(events[1]['parent_id'],events[0]['event_id'])
        self.assertIsNotNone(events[1]['ended_at'])
        @client.trace()
        async def async_agent():
            with client.span('agent','root'):
                async def child(name):
                    with client.span('tool',name):await asyncio.sleep(0)
                await asyncio.gather(child('a'),child('b'))
            return 'ok'
        self.assertEqual(asyncio.run(async_agent()),'ok')
        events=captured[1][0][2];self.assertEqual(events[1]['parent_id'],events[0]['event_id']);self.assertEqual(events[2]['parent_id'],events[0]['event_id'])
    def test_sdk_fail_open_preserves_agent_result(self):
        client=TraceClient('http://127.0.0.1:1',self.pid,'token')
        @client.trace()
        def agent():return 'ok'
        self.assertEqual(agent(),'ok');self.assertIsNotNone(client.last_error)
    def test_workspace_final_state_cleanup_and_traversal(self):
        env=WorkspaceEnvironment({'seed_files':{'answer.txt':'before'},'validation_tests':[{'name':'test','command':['python','-c','import pathlib; assert pathlib.Path("answer.txt").read_text()=="after"']}]},root=self.tmp.name)
        async def check():
            await env.create(EvalCase('c',1,{}));path=env.path
            await env.execute(['python','-c','from pathlib import Path; Path("answer.txt").write_text("after")'])
            final=await env.snapshot();await env.destroy();return path,final
        path,final=asyncio.run(check());self.assertFalse(path.exists());self.assertEqual(final['tests'][0]['exit_code'],0)
        self.assertIn('after',final['patch_diff'])
        score=asyncio.run(ConfiguredEvaluator('f','file_content',{'path':'answer.txt','expected':'after'}).evaluate(EvalCase('c',1,{}),AgentRun('r','c','succeeded',final_state=final)))
        self.assertEqual(score.outcome,'pass')
        with self.assertRaises(ValueError):asyncio.run(WorkspaceEnvironment({},root=self.tmp.name).create(EvalCase('c',1,{'files':{'../escape':'x'}})))
    def test_codex_parser_retains_unknown_and_reconciles_items(self):
        lines=[{'type':'item.started','item':{'id':'i','type':'command_execution','command':'pytest'}},
               {'type':'item.completed','item':{'id':'i','type':'command_execution','command':'pytest','exit_code':0,'aggregated_output':'passed'}},
               {'type':'future.event','data':'x'},
               {'type':'item.completed','item':{'id':'m','type':'agent_message','text':'done'}},
               {'type':'turn.completed','usage':{'input_tokens':12}}]
        parsed=parse_jsonl('\n'.join(json.dumps(x) for x in lines))
        self.assertEqual(parsed['output'],'done');self.assertEqual(len([e for e in parsed['events'] if e.kind=='command']),1)
        self.assertEqual(parsed['usage']['input_tokens'],12);self.assertFalse(parsed['partial'])
        self.assertTrue(parse_jsonl('garbled')['partial'])
    def test_codex_adapter_fixture_end_to_end_final_state(self):
        fixture=Path(self.tmp.name)/'fake-codex'
        fixture.write_text('#!/usr/bin/env python3\nimport json,sys\nfrom pathlib import Path\nsys.stdin.read()\nPath("answer.txt").write_text("42")\nprint(json.dumps({"type":"item.completed","item":{"id":"m","type":"agent_message","text":"done"}}))\nprint(json.dumps({"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":2}}))\n')
        fixture.chmod(0o755)
        checks=[self.store.add_evaluator(self.pid,'File','file_content',{'path':'answer.txt','expected':'42'})['id'],self.store.add_evaluator(self.pid,'Test','test_passed',{'test_name':'check'})['id']]
        config={'type':'codex','model':'mock','environment':{'type':'workspace','validation_tests':[{'name':'check','command':['python','-c','from pathlib import Path; assert Path("answer.txt").read_text()=="42"']}]}}
        exp,_,_=self.setup_exp(evaluators=checks,agent_config=config)
        with patch.dict(os.environ,{'LITEEVAL_CODEX_BINARY':str(fixture)}):asyncio.run(ExperimentRunner(self.store).run(exp['id']))
        result=self.store.get_experiment(exp['id'])
        self.assertEqual(result['summary']['case_counts']['pass'],1)
        artifacts=self.store.list_items('artifacts',self.pid)['items']
        self.assertTrue({'codex_jsonl','final_state','patch'}<= {a['kind'] for a in artifacts})
    def test_docker_command_restrictions(self):
        env=DockerEnvironment({'image':'example:pinned'},labels={'experiment':'test'})
        env.path=Path(self.tmp.name)
        command=env.create_command()
        self.assertIn('--read-only',command);self.assertIn('no-new-privileges',command)
        self.assertEqual(command[command.index('--network')+1],'none')
        self.assertNotIn('--privileged',command);self.assertNotIn('/var/run/docker.sock',' '.join(command))
    def test_schema_and_tool_parameters(self):
        self.assertTrue(validate_schema({'x':1},{'type':'object','required':['x'],'properties':{'x':{'type':'integer'}}}))
        self.assertFalse(validate_schema({'x':True},{'type':'object','properties':{'x':{'type':'integer'}}}))
        with self.assertRaises(ValueError):validate_schema({}, {'oneOf':[]})
        run=AgentRun('r','c','succeeded',events=[Event('tool','lookup',attributes={'arguments':{'id':7}})],capture_level='full_harness')
        score=asyncio.run(ConfiguredEvaluator('t','tool_parameter',{'tool_name':'lookup','parameters':{'id':7}}).evaluate(EvalCase('c',1,{}),run))
        self.assertEqual(score.outcome,'pass');self.assertEqual(len(score.evidence_event_ids),1)
    def test_existing_v1_database_migration(self):
        path=Path(self.tmp.name)/'old.db'
        db=sqlite3.connect(path);db.executescript(SCHEMA);db.execute('INSERT INTO projects VALUES (?,?,?)',('old','Legacy','now'));db.commit();db.close()
        migrated=Store(path)
        self.assertEqual(migrated.list_items('projects')['items'][0]['name'],'Legacy')
        with migrated.connect() as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],2)
    def test_artifact_integrity(self):
        id_=self.store.save_artifact(self.pid,None,'test',{'authorization':'secret','value':1})
        meta,data=self.store.read_artifact(id_);self.assertEqual(json.loads(data)['authorization'],'[REDACTED]')
        (self.store.artifact_root/id_).write_text('tampered')
        with self.assertRaises(ValueError):self.store.read_artifact(id_)

    def test_cancel_reaped_after_worker_crash(self):
        exp,_,_=self.setup_exp()
        claim=self.store.claim(exp['id'],1)
        case=EvalCase(**self.store.export_cases(self.ds)['records'][0])
        attempt=self.store.start_attempt(exp['id'],claim['lease_token'],case)
        self.store.cancel(exp['id'])
        with self.store.connect() as db:db.execute('UPDATE experiments SET lease_expires=0 WHERE id=?',(exp['id'],))
        asyncio.run(ExperimentRunner(self.store).run_next())
        result=self.store.get_experiment(exp['id'])
        self.assertEqual(result['status'],'cancelled')
        self.assertEqual(result['summary']['case_counts']['cancelled'],1)
        self.assertEqual(self.store.list_attempts(attempt['run_id'])[0]['status'],'cancelled')

    def test_claim_and_heartbeat_exclude_second_worker(self):
        exp,_,_=self.setup_exp()
        claim=self.store.claim(exp['id'],1)
        other=Store(self.store.path)
        self.assertIsNone(other.claim(exp['id'],1))
        self.assertTrue(self.store.heartbeat(exp['id'],claim['lease_token'],1))
        with self.assertRaises(LeaseLost):other.heartbeat(exp['id'],'obsolete-token',1)

    def test_mixed_metrics_case_regression_wins(self):
        exp,_,_=self.setup_exp()
        extra=self.store.add_evaluator(self.pid,'Other','contains',{})['id']
        # Each case changes in opposite directions; it must not be counted twice.
        with self.store.connect() as db:db.execute('INSERT INTO experiment_evaluators VALUES (?,?)',(exp['id'],extra))
        clone=self.store.rerun(exp['id'])
        asyncio.run(ExperimentRunner(self.store).run(exp['id']))
        asyncio.run(ExperimentRunner(self.store).run(clone['id']))
        a=self.store.get_experiment(exp['id'])['runs'][0]['id'];b=self.store.get_experiment(clone['id'])['runs'][0]['id']
        with self.store.connect() as db:
            db.execute("UPDATE scores SET outcome='fail',value=0 WHERE run_id=? AND evaluator_revision_id!=?",(a,extra))
            db.execute("UPDATE scores SET outcome='fail',value=0 WHERE run_id=? AND evaluator_revision_id=?",(b,extra))
        result=compare(self.store,exp['id'],clone['id'])
        self.assertEqual(result['counts'],{'improved':0,'regressed':1,'same':0,'unscored':0})
        self.assertTrue(result['details'][0]['mixed'])

    def test_process_timeout_preserves_output(self):
        result=asyncio.run(run_process(['python','-u','-c','import time; print("partial"); time.sleep(5)'],timeout=0.1))
        self.assertTrue(result['timed_out']);self.assertIn('partial',result['stdout'])

    def test_snapshot_refuses_symlink_parent_and_large_files(self):
        root=Path(self.tmp.name)/'snapshot';root.mkdir()
        outside=Path(self.tmp.name)/'outside';outside.mkdir();(outside/'secret').write_text('outside')
        (root/'link').symlink_to(outside,target_is_directory=True)
        with self.assertRaises((OSError,ValueError)):snapshot_bytes(root,'link/secret')
        (root/'big').write_text('12345')
        with self.assertRaises(ValueError):snapshot_bytes(root,'big',limit=4)

    def test_configuration_and_event_validation(self):
        with self.assertRaises(ValueError):self.store.add_evaluator(self.pid,'Schema','json_schema',{'schema':{'type':'object','properties':{'absent':{'oneOf':[]}}}})
        with self.assertRaises(ValueError):self.store.add_agent(self.pid,'Bad',{'type':'http','endpoint':self.url,'headers':{'Authorization':'secret'}})
        with self.assertRaises(ValueError):Event.from_dict({'kind':'tool','input_parts':['bad']})
        with self.assertRaises(ValueError):Event.from_dict({'kind':'tool','started_at':float('nan')})

if __name__=='__main__':unittest.main()
