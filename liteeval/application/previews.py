"""Durable evaluator previews processed by Worker, never overwriting experiment scores."""
import asyncio
from dataclasses import asdict
import json
import math
import time

from liteeval.domain.contracts import EvalCase, AgentRun, Event
from liteeval.integrations.judges.configured import ConfiguredEvaluator
from liteeval.infrastructure.security.secrets import safe_error
from liteeval.domain.values import encoded, now, uid
from liteeval.domain.errors import Conflict, LeaseLost


def enqueue(store, body):
    allowed = {'project_id', 'evaluator_id', 'run_id', 'case', 'run'}
    if set(body) - allowed:
        raise ValueError('Unsupported preview field')
    project_id, evaluator_id = body['project_id'], body['evaluator_id']
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        evaluator = dict(store.required(db, 'evaluator_revisions', evaluator_id))
        store.active(evaluator)
        if evaluator['project_id'] != project_id:
            raise ValueError('Evaluator belongs to a different project')
        if 'run_id' in body:
            if 'case' in body or 'run' in body:
                raise ValueError('Provide run_id or synthetic case/run, not both')
            source = store.required(db, 'runs', body['run_id'])
            exp = store.required(db, 'experiments', source['experiment_id'])
            if exp['project_id'] != project_id:
                raise ValueError('Run belongs to a different project')
            if source['status'] in {'running', 'retrying'}:
                raise Conflict('Wait until the source Run finishes')
            case = json.loads(db.execute('SELECT content_json FROM case_revisions WHERE case_id=? AND revision=?',
                                        (source['case_id'], source['case_revision'])).fetchone()[0])
            trace = store.required(db, 'traces', source['trace_id']) if source['trace_id'] else None
            run = {'status': source['status'], 'output': json.loads(source['output_json'] or 'null'),
                   'latency_ms': source['latency_ms'], 'cost': source['cost'], 'currency': source['currency'],
                   'error': source['error'], 'events': json.loads(trace['events_json']) if trace else [],
                   'capture_level': trace['capture_level'] if trace else 'output_only',
                   'final_state': json.loads(source['final_state_json'])}
            source_run_id = source['id']
        else:
            sample = body['case']
            if not isinstance(sample, dict) or set(sample) - {'input', 'expected', 'metadata'}:
                raise ValueError('Synthetic case supports input, expected and metadata')
            store.validate_case(sample['input'], sample.get('metadata'))
            case = asdict(EvalCase(uid(), 1, sample['input'], sample.get('expected'), sample.get('metadata') or {}))
            run = body['run']
            if not isinstance(run, dict) or set(run) - {'status', 'output', 'events', 'capture_level', 'latency_ms', 'cost', 'currency', 'error', 'final_state'}:
                raise ValueError('Unsupported synthetic Run field')
            run = dict(run)
            if run.get('status', 'succeeded') not in {'succeeded', 'failed', 'timed_out', 'cancelled'}:
                raise ValueError('Preview needs a terminal Run status')
            if run.get('capture_level', 'output_only') not in {'output_only', 'reported_spans', 'full_harness'}:
                raise ValueError('Invalid capture_level')
            for field in ('latency_ms', 'cost'):
                value = run.get(field)
                if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                    raise ValueError(f'{field} must be a finite nonnegative number')
            if not isinstance(run.get('final_state', {}), dict) or not isinstance(run.get('events', []), list):
                raise ValueError('final_state must be an object; events must be an array')
            for field in ('currency', 'error'):
                if run.get(field) is not None and not isinstance(run[field], str):
                    raise ValueError(f'{field} must be a string')
            source_run_id = None
        run['events'] = [asdict(Event.from_dict(event, i)) for i, event in enumerate(run.get('events', []))]
        job_id = uid()
        payload = {'evaluator': evaluator, 'case': case, 'run': run, 'source_run_id': source_run_id}
        db.execute('INSERT INTO evaluator_previews (id,project_id,payload_json,created_at) VALUES (?,?,?,?)',
                   (job_id, project_id, encoded(payload), now()))
    return {'id': job_id, 'status': 'queued', 'evaluator_id': evaluator_id, 'source_run_id': source_run_id,
            'synthetic': source_run_id is None}


def get_preview(store, preview_id):
    with store.connect() as db:
        row = db.execute('SELECT id,project_id,status,created_at,finished_at,result_json,error FROM evaluator_previews WHERE id=?', (preview_id,)).fetchone()
        if not row:
            from liteeval.domain.errors import NotFound
            raise NotFound('Preview not found')
        result = dict(row)
        result['result'] = json.loads(result.pop('result_json') or 'null')
        return result


class PreviewRunner:
    def __init__(self, store, lease_seconds=30):
        self.store = store
        self.lease_seconds = lease_seconds

    def claim(self):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM evaluator_previews WHERE status='queued' OR (status='running' AND lease_expires<?) ORDER BY created_at,id LIMIT 1", (time.time(),)).fetchone()
            if not row:
                return None
            token = uid()
            db.execute("UPDATE evaluator_previews SET status='running',lease_token=?,lease_expires=? WHERE id=?", (token, time.time()+self.lease_seconds, row['id']))
            return {**dict(row), 'lease_token': token}

    def heartbeat(self, job):
        with self.store.connect() as db:
            changed = db.execute("UPDATE evaluator_previews SET lease_expires=? WHERE id=? AND status='running' AND lease_token=? AND lease_expires>?",
                                 (time.time()+self.lease_seconds, job['id'], job['lease_token'], time.time())).rowcount
            if not changed:
                raise LeaseLost('Preview lease was lost')

    def finish(self, job, result=None, error=None):
        with self.store.connect() as db:
            changed = db.execute("UPDATE evaluator_previews SET status=?,result_json=?,error=?,finished_at=?,lease_token=NULL,lease_expires=NULL WHERE id=? AND status='running' AND lease_token=? AND lease_expires>?",
                                 ('failed' if error else 'completed', encoded(result), error, now(), job['id'], job['lease_token'], time.time())).rowcount
            if not changed:
                raise LeaseLost('Preview lease was lost')

    async def run_next(self):
        job = self.claim()
        if not job:
            return None
        async def evaluate():
            payload = json.loads(job['payload_json'])
            conf = payload['evaluator']
            case = EvalCase(**payload['case'])
            run_data = dict(payload['run'])
            run_data['events'] = [Event.from_dict(e) for e in run_data.get('events', [])]
            run_data.setdefault('status', 'succeeded')
            run = AgentRun(job['id'], case.id, **run_data)
            evaluator = ConfiguredEvaluator(conf['id'], conf['kind'], json.loads(conf['config_json']), self.store)
            raw_artifact_ids = []
            for attempt in range(2 if conf['kind'] == 'llm_judge' else 1):
                score = await evaluator.evaluate(case, run)
                if score.raw_result is not None:
                    score.artifact_id = self.store.save_artifact(job['project_id'], None, 'preview_judge_raw', score.raw_result)
                    raw_artifact_ids.append(score.artifact_id)
                if score.outcome != 'error':
                    break
                if conf['kind'] == 'llm_judge' and attempt == 0:
                    await asyncio.sleep(0.1)
            value = asdict(score)
            value.pop('raw_result', None)
            return {'score': value, 'source_run_id': payload['source_run_id'],
                    'synthetic': payload['source_run_id'] is None, 'evaluator_id': conf['id'],
                    'config_hash': conf['config_hash'], 'raw_artifact_ids': raw_artifact_ids,
                    'does_not_modify_experiment': True}
        task = asyncio.create_task(evaluate())
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=self.lease_seconds/3)
                if not task.done():
                    self.heartbeat(job)
            self.finish(job, task.result())
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            with self.store.connect() as db:
                db.execute('UPDATE evaluator_previews SET lease_expires=0 WHERE id=? AND lease_token=?', (job['id'], job['lease_token']))
            raise
        except LeaseLost:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        except Exception as exc:
            try:
                self.finish(job, error=safe_error(exc))
            except LeaseLost:
                pass
        return job['id']
