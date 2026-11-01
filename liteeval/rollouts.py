"""Durable rollout activity, statistics and self-contained portable exports."""
import csv
import io
import json
import math
from collections import Counter
from zipfile import ZipFile, ZIP_DEFLATED
from . import usage
from .security import redact
from .storage import encoded, now, LeaseLost

EXPORT_LIMIT = 128_000_000


def append(store, experiment_id, token, attempt, kind, payload):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE');store.owns_lease(db, experiment_id, token)
        run = store.required(db, 'runs', attempt['run_id'])
        if run['attempt_id'] != attempt['id']:raise LeaseLost('Superseded rollout activity')
        db.execute('INSERT INTO rollout_events(run_id,attempt_id,kind,payload_json,created_at) VALUES (?,?,?,?,?)',
                   (attempt['run_id'], attempt['id'], kind, encoded(redact(payload)), now()))


def event_row(row):
    row = dict(row);row['payload'] = json.loads(row.pop('payload_json'));return row


def events(store, run_id, after=0, limit=200):
    after, limit = int(after), int(limit)
    if after < 0 or not 1 <= limit <= 1000:raise ValueError('after >= 0 and limit 1..1000 required')
    with store.connect() as db:
        run = store.required(db, 'runs', run_id)
        rows = list(db.execute('SELECT * FROM rollout_events WHERE run_id=? AND id>? ORDER BY id LIMIT ?', (run_id, after, limit+1)))
        items = [event_row(r) for r in rows[:limit]]
        return {'items': items, 'next_cursor': items[-1]['id'] if items else after, 'has_more': len(rows) > limit,
                'run_status': run['status'], 'capture': 'local subprocess logs arrive during execution; HTTP trajectory arrives with its response'}


def snapshot(store, experiment_id=None, run_id=None):
    with store.connect() as db:
        db.execute('BEGIN')
        if run_id:experiment_id = store.required(db, 'runs', run_id)['experiment_id']
        exp = dict(store.required(db, 'experiments', experiment_id))
        rows = db.execute('SELECT * FROM runs WHERE experiment_id=?'+(' AND id=?' if run_id else '')+' ORDER BY case_id',
                          (experiment_id, run_id) if run_id else (experiment_id,)).fetchall()
        runs = [];activity = [];artifacts = []
        for row in rows:
            run = dict(row)
            for field in ('output', 'usage', 'final_state'):
                run[field] = json.loads(run.pop(field+'_json') or 'null')
            run['scores'] = [dict(s) for s in db.execute('SELECT * FROM scores WHERE run_id=? ORDER BY evaluator_revision_id', (run['id'],))]
            attempts = []
            for item in db.execute('SELECT * FROM attempts WHERE run_id=? ORDER BY number', (run['id'],)):
                item = dict(item)
                item['usage'] = json.loads(item.pop('usage_json'));item['artifact_ids'] = json.loads(item.pop('artifact_ids_json'))
                trace = db.execute('SELECT * FROM traces WHERE id=?', (item['trace_id'],)).fetchone() if item['trace_id'] else None
                if trace:
                    trace = dict(trace)
                    for field in ('input', 'output', 'events', 'metadata'):trace[field] = json.loads(trace.pop(field+'_json') or 'null')
                item['trace'] = trace;attempts.append(item)
            run['attempts'] = attempts;runs.append(run)
            activity.extend(event_row(r) for r in db.execute('SELECT * FROM rollout_events WHERE run_id=? ORDER BY id', (run['id'],)))
            artifacts.extend(dict(r) for r in db.execute('SELECT * FROM artifacts WHERE run_id=? ORDER BY created_at,id', (run['id'],)))
        records = usage.records_from_db(db, [r['id'] for r in runs])
        agent = dict(store.required(db, 'agent_versions', exp['agent_version_id']))
        evaluators = [dict(r) for r in db.execute('SELECT e.* FROM evaluator_revisions e JOIN experiment_evaluators x ON e.id=x.evaluator_revision_id WHERE x.experiment_id=? ORDER BY e.id', (experiment_id,))]
        cases = [json.loads(r[0]) for r in db.execute('SELECT r.content_json FROM case_revisions r JOIN snapshot_cases s ON r.case_id=s.case_id AND r.revision=s.revision WHERE s.snapshot_id=? ORDER BY s.ordinal', (exp['snapshot_id'],))]
        if run_id:cases = [c for c in cases if c['id'] == runs[0]['case_id']]
    return {'export_schema': 1, 'exported_at': now(), 'scope': 'run' if run_id else 'experiment',
            'experiment': exp, 'agent_version': agent, 'evaluators': evaluators, 'cases': cases,
            'runs': runs, 'usage_records': records, 'activity': activity, 'artifacts': artifacts}


def statistics(data):
    runs = data['runs'];attempts = [a for r in runs for a in r['attempts']]
    trajectory = [e for a in attempts if a['trace'] for e in a['trace']['events']]
    def output_text(run):
        value = run['output'];return value if isinstance(value, str) else encoded(value) if value is not None else ''
    durations = sorted(r['latency_ms'] for r in runs if r['latency_ms'] is not None)
    tool_events = [e for e in trajectory if e['kind'] in {'tool', 'tool_call', 'mcp_tool_call'}]
    tool_durations = [int((e['ended_at']-e['started_at'])*1000) for e in tool_events
                      if e.get('started_at') is not None and e.get('ended_at') is not None]
    return {'run_count': len(runs), 'attempt_count': len(attempts), 'retry_count': sum(max(len(r['attempts'])-1, 0) for r in runs),
            'run_status_counts': dict(Counter(r['status'] for r in runs)), 'event_kind_counts': dict(Counter(e['kind'] for e in trajectory)),
            'tool_calls': len(tool_events), 'tool_calls_by_name': dict(Counter(e['name'] for e in tool_events)),
            'tool_latency': {'sample_count': len(tool_durations), 'total_ms': sum(tool_durations) if tool_durations else None},
            'latency': {'sample_count': len(durations), 'mean_ms': sum(durations)/len(durations) if durations else None,
                        'p50_ms': durations[math.ceil(len(durations)*.5)-1] if durations else None,
                        'p95_ms': durations[math.ceil(len(durations)*.95)-1] if durations else None},
            'output': {'characters': sum(len(output_text(r)) for r in runs), 'utf8_bytes': sum(len(output_text(r).encode()) for r in runs),
                       'scope': 'latest result per Run; includes serialized JSON output'},
            'captured_log_bytes': sum(len(e['payload'].get('text', '').encode()) for e in data['activity'] if e['kind'] == 'log'),
            'artifacts': {'count': len(data['artifacts']), 'bytes': sum(a['size_bytes'] for a in data['artifacts'])},
            'capture_levels': dict(Counter(a['trace']['capture_level'] if a['trace'] else 'pending' for a in attempts)),
            'usage': usage.summarize(data['usage_records']),
            'coverage': {'state': 'partial' if any(r['status'] in {'running', 'retrying'} for r in runs) or data['experiment']['status'] in {'queued', 'running'} else 'finished',
                         'trajectory_attempts': sum(bool(a['trace'] and a['trace']['events']) for a in attempts),
                         'notes': ['HTTP internals require upstream events; hidden CoT is unavailable',
                                   'cache/reasoning are provider-reported; TTFT, token/s and CPU/memory are not measured',
                                   'local files use bounded final-state snapshots, not a complete filesystem backup']}}


def get_statistics(store, experiment_id=None, run_id=None):
    return statistics(snapshot(store, experiment_id, run_id))


def jsonl_records(data):
    yield {'record_type': 'manifest', 'export_schema': data['export_schema'], 'exported_at': data['exported_at'], 'scope': data['scope']}
    for kind, key in [('experiment', 'experiment'), ('agent_version', 'agent_version'), ('statistics', 'statistics')]:
        yield {'record_type': kind, 'data': data[key]}
    for kind, key in [('evaluator', 'evaluators'), ('case', 'cases'), ('run', 'runs'), ('usage', 'usage_records'), ('activity', 'activity'), ('artifact', 'artifacts')]:
        for row in data[key]:yield {'record_type': kind, 'data': row}


def export(store, experiment_id=None, run_id=None, format='json'):
    if format not in {'json', 'jsonl', 'csv', 'zip'}:raise ValueError('format must be json, jsonl, csv or zip')
    data = snapshot(store, experiment_id, run_id);data['statistics'] = statistics(data)
    if format == 'csv':
        output = io.StringIO(newline='');writer = csv.writer(output)
        writer.writerow(['run_id', 'case_id', 'case_revision', 'status', 'attempts', 'latency_ms', *usage.FIELDS,
                         'judge_input_tokens', 'judge_output_tokens', 'output_json', 'scores_json'])
        for run in data['runs']:
            records = [r for r in data['usage_records'] if r['run_id'] == run['id']]
            total = usage.aggregate(records)['totals'];judge = usage.aggregate([r for r in records if r['role'] == 'judge'])['totals']
            row = [run['id'], run['case_id'], run['case_revision'], run['status'], len(run['attempts']), run['latency_ms'],
                   *(total[k] for k in usage.FIELDS), judge['input_tokens'], judge['output_tokens'], encoded(run['output']), encoded(run['scores'])]
            writer.writerow(["'"+v if isinstance(v, str) and v.lstrip().startswith(('=', '+', '-', '@')) else v for v in row])
        content = output.getvalue().encode('utf-8-sig')
        if len(content) > EXPORT_LIMIT:raise ValueError('Export exceeds 128 MB; export individual Runs')
        return content, 'text/csv; charset=utf-8'
    payload = encoded(data).encode()
    if len(payload) > EXPORT_LIMIT:raise ValueError('Export metadata exceeds 128 MB; export individual Runs')
    if format == 'json':return payload, 'application/json; charset=utf-8'
    if format == 'jsonl':
        content = ('\n'.join(encoded(r) for r in jsonl_records(data))+'\n').encode()
        if len(content) > EXPORT_LIMIT:raise ValueError('Export exceeds 128 MB; export individual Runs')
        return content, 'application/x-ndjson'
    size = 2*len(payload)+sum(a['size_bytes'] for a in data['artifacts'])
    if size > EXPORT_LIMIT:raise ValueError('Export exceeds 128 MB; export individual Runs')
    output = io.BytesIO();manifest = []
    with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
        archive.writestr('rollout.json', payload)
        archive.writestr('records.jsonl', '\n'.join(encoded(r) for r in jsonl_records(data))+'\n')
        for item in data['artifacts']:
            info, content = store.read_artifact(item['id'])
            path = 'artifacts/'+item['id'];archive.writestr(path, content)
            manifest.append({**info, 'path': path})
        archive.writestr('manifest.json', encoded({'export_schema': 1, 'artifacts': manifest,
                                                'note': 'Artifacts are the captured originals; connection credentials are excluded'}))
    return output.getvalue(), 'application/zip'
