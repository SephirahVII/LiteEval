"""Benchmark state persistence, separate from execution and grading policies."""
import json
import sqlite3
from liteeval.domain.values import encoded, now, uid
from liteeval.domain.errors import Conflict, NotFound
from liteeval.infrastructure.security.secrets import redact


class BenchmarkRepository:
    def __init__(self, connect):
        self.connect = connect

    def publish(self, project_id, package, content):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM benchmark_releases WHERE project_id=? AND name=? AND version=?',
                             (project_id, package.id, package.version)).fetchone()
            if old:
                if old['digest'] != package.digest: raise Conflict('Version already published with different content')
                return dict(old) | {'manifest': json.loads(old['manifest']), 'reused': True}
            id_ = uid()
            db.execute('INSERT INTO benchmark_releases VALUES (?,?,?,?,?,?,?)',
                       (id_, project_id, package.id, package.version, package.digest, encoded(content), now()))
        return self.release(project_id, id_) | {'reused': False}

    def release(self, project_id, release_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM benchmark_releases WHERE project_id=? AND id=?', (project_id, release_id)).fetchone()
        if not row: raise NotFound('Benchmark release not found')
        result = dict(row); result['manifest'] = json.loads(result['manifest'])
        return result

    def list_releases(self, project_id):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT id,name,version,digest,created_at FROM benchmark_releases WHERE project_id=? ORDER BY created_at,id', (project_id,))]

    def register(self, project_id, name, role, digest, spec):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT id FROM harness_revisions WHERE project_id=? AND name=? AND digest=?', (project_id, name, digest)).fetchone()
            if row: return row[0]
            id_ = uid()
            db.execute('INSERT INTO harness_revisions VALUES (?,?,?,?,?,?,?)', (id_, project_id, name, role, digest, encoded(spec), now()))
            return id_

    def harness(self, project_id, id_):
        with self.connect() as db:
            row = db.execute('SELECT * FROM harness_revisions WHERE project_id=? AND id=?', (project_id, id_)).fetchone()
        if not row: raise NotFound('Harness revision not found')
        return dict(row) | {'spec': json.loads(row['spec'])}

    def start(self, project_id, plan):
        id_ = uid()
        with self.connect() as db:
            db.execute('INSERT INTO benchmark_executions VALUES (?,?,?,?,?,?,?)', (id_, project_id, encoded(plan), 'running', None, now(), None))
        return id_

    def finish(self, run_id, result):
        with self.connect() as db:
            db.execute('UPDATE benchmark_executions SET status=?,result=?,finished_at=? WHERE id=?', (result['status'], encoded(redact(result)), now(), run_id))

    def event(self, run_id, kind, payload):
        payload, created_at = redact(payload), now()
        with self.connect() as db:
            cursor = db.execute('INSERT INTO benchmark_events(execution_id,kind,payload,created_at) VALUES (?,?,?,?)',
                                (run_id, kind, encoded(payload), created_at))
            return {'id': cursor.lastrowid, 'execution_id': run_id, 'kind': kind, 'payload': payload, 'created_at': created_at}

    def execution(self, project_id, id_):
        with self.connect() as db:
            row = db.execute('SELECT * FROM benchmark_executions WHERE project_id=? AND id=?', (project_id, id_)).fetchone()
            events = [dict(r) | {'payload': json.loads(r['payload'])} for r in db.execute('SELECT * FROM benchmark_events WHERE execution_id=? ORDER BY id', (id_,))]
        if not row: raise NotFound('Execution not found')
        return dict(row) | {'plan': json.loads(row['plan']), 'result': json.loads(row['result']) if row['result'] else None, 'events': events}

    def event_page(self, project_id, execution_id, after=0, limit=25, task_id=None):
        if not 0 <= after < 2**63 or not 1 <= limit <= 100:
            raise ValueError('Invalid event cursor or limit')
        with self.connect() as db:
            run = db.execute('SELECT status FROM benchmark_executions WHERE project_id=? AND id=?',
                             (project_id, execution_id)).fetchone()
            if not run:
                raise NotFound('Execution not found')
            sql = 'SELECT * FROM benchmark_events WHERE execution_id=? AND id>?'
            params = [execution_id, after]
            if task_id is not None:
                sql += " AND json_extract(payload, '$.task_id')=?"
                params.append(task_id)
            rows = db.execute(sql + ' ORDER BY id LIMIT ?', (*params, limit + 1)).fetchall()
        events = [dict(row) | {'payload': json.loads(row['payload'])} for row in rows[:limit]]
        return {'events': events, 'status': run['status'], 'next_after': events[-1]['id'] if events else after,
                'has_more': len(rows) > limit}
