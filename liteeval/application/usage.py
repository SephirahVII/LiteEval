from liteeval.domain.usage import FIELDS, normalize, sum_turns, aggregate, summarize
"""Conservative usage normalization: unknown is null, aliases and subsets are not added."""
import json
from collections import Counter
from liteeval.infrastructure.security.secrets import redact
from liteeval.domain.values import encoded, now, uid









def record(store, experiment_id, token, attempt, role, raw, model=None, evaluator_id=None, latency_ms=None, cost=None, currency=None, source='reported'):
    raw = redact(raw) if isinstance(raw, dict) else {}
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE');store.owns_lease(db, experiment_id, token)
        from liteeval.domain.errors import LeaseLost
        if store.required(db, 'runs', attempt['run_id'])['attempt_id'] != attempt['id']:raise LeaseLost('Superseded usage')
        db.execute('INSERT INTO usage_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (uid(), attempt['run_id'], attempt['id'], role, evaluator_id, model, source,
                    encoded(raw), encoded(normalize(raw)), latency_ms, cost, currency, now()))


def records_from_db(db, run_ids):
    records = []
    # Per-run queries avoid SQLite parameter limits for large benchmarks.
    for run_id in run_ids:
        for row in db.execute('SELECT * FROM usage_records WHERE run_id=? ORDER BY created_at,id', (run_id,)):
            row = dict(row);row['usage'] = json.loads(row.pop('usage_json'));row['normalized'] = json.loads(row.pop('normalized_json'));records.append(row)
        known = {r['attempt_id'] for r in records if r['run_id'] == run_id and r['role'] == 'target'}
        for row in db.execute('SELECT * FROM attempts WHERE run_id=? ORDER BY number', (run_id,)):
            if row['id'] in known:continue
            raw = json.loads(row['usage_json'])
            if raw or row['cost'] is not None:
                records.append({'id': None, 'run_id': run_id, 'attempt_id': row['id'], 'role': 'target', 'evaluator_revision_id': None,
                                'model': None, 'source': 'legacy_attempt', 'usage': raw, 'normalized': normalize(raw),
                                'latency_ms': None, 'cost': row['cost'], 'currency': row['currency'], 'created_at': row['started_at']})
    return records
