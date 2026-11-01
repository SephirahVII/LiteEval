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
                return dict(old) | {'reused': True}
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
        with self.connect() as db:
            db.execute('INSERT INTO benchmark_events(execution_id,kind,payload,created_at) VALUES (?,?,?,?)', (run_id, kind, encoded(redact(payload)), now()))

    def execution(self, project_id, id_):
        with self.connect() as db:
            row = db.execute('SELECT * FROM benchmark_executions WHERE project_id=? AND id=?', (project_id, id_)).fetchone()
            events = [dict(r) | {'payload': json.loads(r['payload'])} for r in db.execute('SELECT * FROM benchmark_events WHERE execution_id=? ORDER BY id', (id_,))]
        if not row: raise NotFound('Execution not found')
        return dict(row) | {'plan': json.loads(row['plan']), 'result': json.loads(row['result']) if row['result'] else None, 'events': events}
