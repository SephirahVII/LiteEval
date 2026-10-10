"""Concurrent, leased, fenced evaluation execution with attempt history."""
import asyncio
from dataclasses import asdict
import json
import time
from .adapters import make_adapter
from .core import EvalCase,AgentRun,Score
from .environments import make_environment,DockerEnvironment
from .evaluators import ConfiguredEvaluator
from .harness import SimpleHarness
from .reports import compare
from .security import safe_error
from .storage import Store,LeaseLost,now

class ExperimentRunner:
    def __init__(self,store: Store,lease_seconds=15):
        self.store=store;self.lease_seconds=lease_seconds

    async def _case(self,exp,case,agent,evaluators,semaphore):
        async with semaphore:
            retries=exp['config'].get('max_retries',0)
            for retry in range(retries+1):
                attempt=self.store.start_attempt(exp['id'],exp['lease_token'],case)
                if attempt is None:return
                labels={'experiment':exp['id'],'lease':exp['lease_token'],'attempt':attempt['id']}
                environment=make_environment(agent.get('environment',{}),root=self.store.artifact_root.parent/'workspaces',labels=labels)
                adapter=make_adapter(agent,environment,attempt['run_id'])
                try:run=await SimpleHarness(environment).run(case,adapter)
                except Exception as exc:run=AgentRun(attempt['run_id'],case.id,'failed',error=safe_error(exc))
                run.id=attempt['run_id']
                artifacts=[]
                if run.raw_response is not None:
                    artifacts.append(self.store.save_artifact(exp['project_id'],run.id,'agent_raw',run.raw_response))
                    if isinstance(run.raw_response,dict) and 'stdout_jsonl' in run.raw_response:
                        artifacts.append(self.store.save_artifact(exp['project_id'],run.id,'codex_jsonl',run.raw_response['stdout_jsonl'].encode(),'application/x-ndjson'))
                artifacts.append(self.store.save_artifact(exp['project_id'],run.id,'trajectory',[asdict(e) for e in run.events]))
                if run.final_state:
                    artifacts.append(self.store.save_artifact(exp['project_id'],run.id,'final_state',run.final_state))
                    if run.final_state.get('patch_diff'):
                        artifacts.append(self.store.save_artifact(exp['project_id'],run.id,'patch',run.final_state['patch_diff'].encode(),'text/plain'))
                trace=self.store.create_trace(exp['project_id'],case.input,run.output,[asdict(e) for e in run.events],run.status,run.capture_level,
                    kind='eval',run_id=run.id,metadata={'attempt_id':attempt['id'],'agent_version_id':exp['agent_version_id'],
                     'model':agent.get('model'),'latency_ms':run.latency_ms,'usage':run.usage,'cost':run.cost,'currency':run.currency})
                will_retry=run.retryable and retry<retries
                scores=[]
                if not will_retry:
                    for evaluator in evaluators:
                        for judge_attempt in range(exp['config'].get('judge_retries',1)+1):
                            try:score=await evaluator.evaluate(case,run)
                            except Exception as exc:score=Score(run.id,evaluator.revision_id,'error',reason=safe_error(exc))
                            if score.raw_result is not None:
                                score.artifact_id=self.store.save_artifact(exp['project_id'],run.id,'judge_raw',score.raw_result)
                                artifacts.append(score.artifact_id)
                            if score.outcome!='error' or evaluator.kind!='llm_judge':break
                            if judge_attempt<exp['config'].get('judge_retries',1):await asyncio.sleep(0.1*2**judge_attempt)
                        scores.append(score)
                self.store.finish_attempt(exp['id'],exp['lease_token'],attempt,run,trace['id'],scores,artifacts,terminal=not will_retry)
                if not will_retry:return
                await asyncio.sleep(min(float(exp['config'].get('retry_delay_seconds',0.5)),30)*2**retry)

    async def run(self,experiment_id=None):
        exp=self.store.claim(experiment_id,self.lease_seconds)
        if not exp:return False
        token=exp['lease_token'];exp['config']=json.loads(exp['config_json'])
        with self.store.connect() as db:
            agent=json.loads(self.store.required(db,'agent_versions',exp['agent_version_id'])['config_json'])
            refs=list(db.execute('SELECT e.* FROM evaluator_revisions e JOIN experiment_evaluators x ON x.evaluator_revision_id=e.id WHERE x.experiment_id=?',(exp['id'],)))
            cases=[EvalCase(**json.loads(r[0])) for r in db.execute('SELECT r.content_json FROM snapshot_cases s JOIN case_revisions r ON r.case_id=s.case_id AND r.revision=s.revision WHERE s.snapshot_id=? ORDER BY s.ordinal',(exp['snapshot_id'],))]
        evaluators=[ConfiguredEvaluator(r['id'],r['kind'],json.loads(r['config_json'])) for r in refs]
        sem=asyncio.Semaphore(exp['config'].get('concurrency',4));tasks=[];lease_lost=False
        async def monitor():
            nonlocal lease_lost
            while True:
                await asyncio.sleep(self.lease_seconds/3)
                try:active=self.store.heartbeat(exp['id'],token,self.lease_seconds)
                except LeaseLost:
                    lease_lost=True;active=False
                if not active:
                    for task in tasks:task.cancel()
                    return
        watch=asyncio.create_task(monitor())
        try:
            if agent.get('environment',{}).get('type')=='docker':
                await DockerEnvironment.cleanup_stale(exp['id'])
            tasks=[asyncio.create_task(self._case(exp,case,agent,evaluators,sem)) for case in cases]
            results=await asyncio.gather(*tasks,return_exceptions=True)
            if lease_lost or any(isinstance(r,LeaseLost) for r in results):
                # Cancellation also fences new commits. Finish only if this worker still owns the lease.
                try:self.store.finish_experiment(exp['id'],token,'cancelled')
                except LeaseLost:pass
            else:
                failures=[r for r in results if isinstance(r,Exception)]
                self.store.finish_experiment(exp['id'],token,'failed' if failures else 'completed',safe_error(failures[0]) if failures else None)
            return True
        except asyncio.CancelledError:
            for t in tasks:t.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            with self.store.connect() as db:
                db.execute('UPDATE experiments SET lease_expires=0 WHERE id=? AND lease_token=?',(exp['id'],token))
            raise
        except Exception as exc:
            try:self.store.finish_experiment(exp['id'],token,'failed',safe_error(exc))
            except LeaseLost:pass
            return True
        finally:
            watch.cancel()
            await asyncio.gather(watch,return_exceptions=True)

    async def run_next(self):
        with self.store.connect() as db:
            # Reap cancellation requests whose worker died before acknowledging them.
            stale=list(db.execute("SELECT id FROM experiments WHERE status='running' AND cancel_requested=1 AND COALESCE(lease_expires,0)<?",(time.time(),)))
            for row in stale:
                db.execute("UPDATE experiments SET status='cancelled',finished_at=?,lease_token=NULL WHERE id=?",(now(),row[0]))
                db.execute("UPDATE runs SET status='cancelled' WHERE experiment_id=? AND status IN ('running','retrying')",(row[0],))
                db.execute("UPDATE attempts SET status='cancelled',finished_at=? WHERE status='running' AND run_id IN (SELECT id FROM runs WHERE experiment_id=?)",(now(),row[0]))
            row=db.execute("SELECT id FROM experiments WHERE cancel_requested=0 AND (status='queued' OR (status='running' AND COALESCE(lease_expires,0)<?)) ORDER BY created_at LIMIT 1",(time.time(),)).fetchone()
        for cancelled in stale:
            await DockerEnvironment.cleanup_stale(cancelled[0])
        if row and await self.run(row['id']):return row['id']
        return None
