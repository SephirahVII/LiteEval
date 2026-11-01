"""Read models for Web clients without loading output or artifact payloads."""
import json
import time
from .storage import now

TERMINAL = {'succeeded', 'failed', 'timed_out', 'cancelled'}


def progress(store, experiment_id):
    with store.connect() as db:
        db.execute('BEGIN')
        exp = dict(store.required(db, 'experiments', experiment_id))
        total = db.execute('SELECT COUNT(*) FROM snapshot_cases WHERE snapshot_id=?', (exp['snapshot_id'],)).fetchone()[0]
        counts = {r[0]: r[1] for r in db.execute('SELECT status,COUNT(*) FROM runs WHERE experiment_id=? GROUP BY status', (experiment_id,))}
        pending = total - sum(counts.values())
        if exp['status'] == 'cancelled':
            counts['cancelled'] = counts.get('cancelled', 0) + pending + counts.pop('running', 0) + counts.pop('retrying', 0)
            pending = 0
    states = {key: counts.get(key, 0) for key in ('running', 'retrying', 'succeeded', 'failed', 'timed_out', 'cancelled')}
    terminal = sum(states[k] for k in TERMINAL)
    return {'experiment_id': experiment_id, 'status': exp['status'], 'cancel_requested': bool(exp['cancel_requested']),
            'counts': {'total': total, 'terminal': terminal, 'pending': pending, **states},
            'progress': terminal / total if total else None,
            'worker_state': 'active' if exp['status'] == 'running' and (exp['lease_expires'] or 0) > time.time()
                            else 'awaiting_recovery' if exp['status'] == 'running' else 'idle',
            'updated_at': now()}


def paginated_runs(store, experiment_id, limit=50, offset=0, status=None, outcome=None, include_output=False):
    limit, offset = int(limit), int(offset)
    if not 1 <= limit <= 200 or offset < 0:
        raise ValueError('Pagination limit must be 1..200; offset >= 0')
    if status is not None and status not in {'pending', 'running', 'retrying', *TERMINAL}:
        raise ValueError('Unsupported run status')
    if outcome is not None and outcome not in {'pending', 'pass', 'fail', 'unscored', 'cancelled'}:
        raise ValueError('Unsupported case outcome')
    # SQL mirrors reports.case_outcome, including known-failure precedence.
    cte = '''WITH score_counts AS (
        SELECT s.run_id,SUM(s.outcome='pass') passed,SUM(s.outcome='fail') failed
        FROM scores s JOIN runs r ON r.id=s.run_id
        JOIN experiment_evaluators e ON e.experiment_id=r.experiment_id AND e.evaluator_revision_id=s.evaluator_revision_id
        WHERE r.experiment_id=? GROUP BY s.run_id
    ), entries AS (
        SELECT s.ordinal,s.case_id,s.revision case_revision,r.id run_id,
            CASE WHEN ?='cancelled' AND (r.id IS NULL OR r.status IN ('running','retrying')) THEN 'cancelled'
                 ELSE COALESCE(r.status,'pending') END status,
            r.latency_ms,r.error,r.trace_id,r.cost,r.currency,
            CASE WHEN ?='cancelled' AND (r.id IS NULL OR r.status IN ('running','retrying')) THEN 'cancelled'
                 WHEN r.id IS NULL OR r.status IN ('running','retrying') THEN 'pending'
                 WHEN r.status='cancelled' THEN 'cancelled'
                 WHEN r.status!='succeeded' THEN 'unscored'
                 WHEN COALESCE(c.failed,0)>0 THEN 'fail'
                 WHEN COALESCE(c.passed,0)=? AND ?>0 THEN 'pass'
                 ELSE 'unscored' END outcome,
            json_extract(cr.content_json,'$.metadata') metadata_json
        FROM snapshot_cases s
        JOIN case_revisions cr ON cr.case_id=s.case_id AND cr.revision=s.revision
        LEFT JOIN runs r ON r.experiment_id=? AND r.case_id=s.case_id AND r.case_revision=s.revision
        LEFT JOIN score_counts c ON c.run_id=r.id WHERE s.snapshot_id=?
    ) '''
    with store.connect() as db:
        db.execute('BEGIN')
        exp = store.required(db, 'experiments', experiment_id)
        eids = [r[0] for r in db.execute('SELECT evaluator_revision_id FROM experiment_evaluators WHERE experiment_id=?', (experiment_id,))]
        params = [experiment_id, exp['status'], exp['status'], len(eids), len(eids), experiment_id, exp['snapshot_id']]
        filters, values = [], []
        for key, value in [('status', status), ('outcome', outcome)]:
            if value is not None:
                filters.append(key+'=?'); values.append(value)
        where = ' WHERE '+' AND '.join(filters) if filters else ''
        total = db.execute(cte+'SELECT COUNT(*) FROM entries'+where, params+values).fetchone()[0]
        rows = [dict(r) for r in db.execute(cte+'SELECT * FROM entries'+where+' ORDER BY ordinal LIMIT ? OFFSET ?', params+values+[limit, offset])]
        ids = [r['run_id'] for r in rows if r['run_id']]
        scores = {}
        if ids:
            placeholders = ','.join('?' for _ in ids)
            for score in db.execute(f'SELECT * FROM scores WHERE run_id IN ({placeholders}) ORDER BY evaluator_revision_id', ids):
                scores.setdefault(score['run_id'], []).append(dict(score))
            outputs = {r[0]: json.loads(r[1] or 'null') for r in db.execute(f'SELECT id,output_json FROM runs WHERE id IN ({placeholders})', ids)} if include_output else {}
        else:
            outputs = {}
        for row in rows:
            row['metadata'] = json.loads(row.pop('metadata_json') or '{}')
            row['scores'] = scores.get(row['run_id'], [])
            if include_output:
                row['output'] = outputs.get(row['run_id'])
    return {'items': rows, 'total': total, 'limit': limit, 'offset': offset}
