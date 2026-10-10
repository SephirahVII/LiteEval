"""SQLite repository. Immutable JSON revisions and snapshots keep comparisons fair."""

import hashlib
import json
import os
import sqlite3
import uuid
import time
import secrets
import csv
import io
from dataclasses import asdict
from .migrations import migrate
from .security import checked_url, validate_no_secrets, new_key, redact
from .core import Event
from .config import validate_agent, validate_evaluator, number
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def uid() -> str:
    return uuid.uuid4().hex


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def encoded(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(encoded(value).encode()).hexdigest()


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS traces (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), kind TEXT NOT NULL,
 run_id TEXT, input_json TEXT NOT NULL, output_json TEXT, events_json TEXT NOT NULL,
 capture_level TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS datasets (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), name TEXT NOT NULL,
 created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cases (
 id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES datasets(id), revision INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS case_revisions (
 case_id TEXT NOT NULL REFERENCES cases(id), revision INTEGER NOT NULL, content_json TEXT NOT NULL,
 content_hash TEXT NOT NULL, PRIMARY KEY(case_id, revision));
CREATE TABLE IF NOT EXISTS agent_versions (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), label TEXT NOT NULL,
 config_json TEXT NOT NULL, config_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evaluator_revisions (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), name TEXT NOT NULL,
 kind TEXT NOT NULL, config_json TEXT NOT NULL, config_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dataset_snapshots (
 id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES datasets(id), snapshot_hash TEXT NOT NULL,
 created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS snapshot_cases (
 snapshot_id TEXT NOT NULL REFERENCES dataset_snapshots(id), case_id TEXT NOT NULL REFERENCES cases(id),
 revision INTEGER NOT NULL, content_hash TEXT NOT NULL, ordinal INTEGER NOT NULL,
 PRIMARY KEY(snapshot_id, case_id));
CREATE TABLE IF NOT EXISTS experiments (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), snapshot_id TEXT NOT NULL REFERENCES dataset_snapshots(id),
 agent_version_id TEXT NOT NULL REFERENCES agent_versions(id), status TEXT NOT NULL, created_at TEXT NOT NULL,
 finished_at TEXT);
CREATE TABLE IF NOT EXISTS experiment_evaluators (
 experiment_id TEXT NOT NULL REFERENCES experiments(id), evaluator_revision_id TEXT NOT NULL REFERENCES evaluator_revisions(id),
 PRIMARY KEY(experiment_id,evaluator_revision_id));
CREATE TABLE IF NOT EXISTS runs (
 id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(id), case_id TEXT NOT NULL REFERENCES cases(id),
 case_revision INTEGER NOT NULL, status TEXT NOT NULL, output_json TEXT, trace_id TEXT, latency_ms INTEGER,
 error TEXT, UNIQUE(experiment_id,case_id,case_revision));
CREATE TABLE IF NOT EXISTS scores (
 run_id TEXT NOT NULL REFERENCES runs(id), evaluator_revision_id TEXT NOT NULL REFERENCES evaluator_revisions(id),
 outcome TEXT NOT NULL, value REAL, reason TEXT, PRIMARY KEY(run_id,evaluator_revision_id));
CREATE INDEX IF NOT EXISTS idx_traces_project ON traces(project_id);
CREATE INDEX IF NOT EXISTS idx_runs_experiment ON runs(experiment_id);
"""


class NotFound(ValueError):
    pass


class Conflict(ValueError):
    pass


class LeaseLost(RuntimeError):
    pass


class Store:
    def __init__(self, path: str | os.PathLike[str] = "data/liteeval.db", artifact_root=None):
        self.path = str(path)
        self.artifact_root = Path(artifact_root or (Path(self.path).parent / "artifacts")).resolve()
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)
            migrate(db)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA journal_mode=WAL")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def required(db: sqlite3.Connection, table: str, id_: str) -> sqlite3.Row:
        if table not in {"projects", "traces", "datasets", "cases", "agent_versions", "evaluator_revisions", "experiments", "runs", "artifacts", "api_keys"}:
            raise ValueError("Invalid table")
        row = db.execute(f"SELECT * FROM {table} WHERE id=?", (id_,)).fetchone()
        if row is None:
            raise NotFound(f"{table}: {id_}")
        return row

    def create_project(self, name: str) -> dict:
        if not isinstance(name,str) or not name.strip() or len(name)>200:raise ValueError('Project name required (max 200)')
        id_ = uid()
        with self.connect() as db:
            db.execute("INSERT INTO projects VALUES (?,?,?)", (id_, name, now()))
        return {"id": id_, "name": name}

    def create_dataset(self, project_id: str, name: str) -> dict:
        id_ = uid()
        with self.connect() as db:
            self.required(db, "projects", project_id)
            db.execute("INSERT INTO datasets VALUES (?,?,?,?)", (id_, project_id, name, now()))
        return {"id": id_, "project_id": project_id, "name": name}

    def create_trace(self, project_id: str, input: dict, output: Any = None, events: list | None = None,
                     status: str = "succeeded", capture_level: str = "output_only", kind: str = "production",
                     run_id: str | None = None, metadata: dict | None = None) -> dict:
        id_ = uid()
        with self.connect() as db:
            self.required(db, "projects", project_id)
            events = [asdict(Event.from_dict(e, i)) for i, e in enumerate(events or [])]
            db.execute("INSERT INTO traces (id,project_id,kind,run_id,input_json,output_json,events_json,capture_level,status,created_at,metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (id_, project_id, kind, run_id, encoded(input), encoded(output), encoded(events or []),
                        capture_level, status, now(), encoded(redact(metadata or {}))))
        return {"id": id_, "kind": kind, "capture_level": capture_level}

    def get_trace(self, trace_id: str) -> dict:
        with self.connect() as db:
            r = self.required(db, "traces", trace_id)
            return {**dict(r), "input": json.loads(r["input_json"]), "output": json.loads(r["output_json"]),
                    "events": json.loads(r["events_json"]), "metadata": json.loads(r["metadata_json"])}

    def add_case(self, dataset_id: str, input: dict, expected: Any = None, metadata: dict | None = None,
                 source_trace_id: str | None = None) -> dict:
        id_ = uid()
        with self.connect() as db:
            ds = self.required(db, "datasets", dataset_id)
            if source_trace_id and self.required(db, "traces", source_trace_id)["project_id"] != ds["project_id"]:
                raise ValueError("Trace and dataset belong to different projects")
            content = {"id": id_, "revision": 1, "input": input, "expected": expected,
                       "metadata": metadata or {}, "source_trace_id": source_trace_id}
            db.execute("INSERT INTO cases VALUES (?,?,?)", (id_, dataset_id, 1))
            db.execute("INSERT INTO case_revisions VALUES (?,?,?,?)", (id_, 1, encoded(content), digest(content)))
        return content

    def case_from_trace(self, dataset_id: str, trace_id: str, expected: Any = None,
                        metadata: dict | None = None) -> dict:
        trace = self.get_trace(trace_id)
        if trace["kind"] != "production":
            raise ValueError("Only production traces may become cases")
        return self.add_case(dataset_id, trace["input"], expected, metadata, trace_id)

    def revise_case(self, case_id: str, input: dict, expected: Any = None,
                    metadata: dict | None = None) -> dict:
        """Create a new immutable revision; existing snapshots still use their frozen content."""
        with self.connect() as db:
            case = self.required(db, "cases", case_id)
            current = json.loads(db.execute("SELECT content_json FROM case_revisions WHERE case_id=? AND revision=?",
                                            (case_id, case["revision"])).fetchone()[0])
            revision = case["revision"] + 1
            content = {"id": case_id, "revision": revision, "input": input, "expected": expected,
                       "metadata": metadata or {}, "source_trace_id": current["source_trace_id"]}
            db.execute("INSERT INTO case_revisions VALUES (?,?,?,?)", (case_id, revision, encoded(content), digest(content)))
            db.execute("UPDATE cases SET revision=? WHERE id=?", (revision, case_id))
        return content

    def add_agent(self, project_id: str, label: str, config: dict) -> dict:
        id_ = uid()
        validate_agent(config)
        if config.get('type') not in {'http', 'codex'}:
            raise ValueError('Agent type must be http or codex')
        if config['type'] == 'http':
            checked_url(config.get('endpoint', ''))
        if config.get('environment', {}).get('type', 'none') not in {'none', 'workspace', 'docker'}:
            raise ValueError('Unsupported environment')
        if config['type'] == 'codex' and config.get('environment', {}).get('type') not in {'workspace', 'docker'}:
            raise ValueError('Codex requires an explicit workspace or docker environment')
        with self.connect() as db:
            self.required(db, "projects", project_id)
            db.execute("INSERT INTO agent_versions VALUES (?,?,?,?,?)",
                       (id_, project_id, label, encoded(config), digest(config)))
        return {"id": id_, "label": label}

    def add_evaluator(self, project_id: str, name: str, kind: str, config: dict) -> dict:
        validate_evaluator(kind,config)
        if kind not in {"exact_match", "contains", "regex", "json_schema", "tool_called", "tool_parameter", "latency", "cost", "file_exists", "file_content", "test_passed", "llm_judge"}:
            raise ValueError("Unsupported evaluator kind")
        id_ = uid()
        with self.connect() as db:
            self.required(db, "projects", project_id)
            db.execute("INSERT INTO evaluator_revisions VALUES (?,?,?,?,?,?)",
                       (id_, project_id, name, kind, encoded(config), digest([kind, config])))
        return {"id": id_, "name": name, "kind": kind}

    def create_experiment(self, project_id: str, dataset_id: str, agent_version_id: str,
                          evaluator_ids: list[str], config: dict | None = None, idempotency_key: str | None = None) -> dict:
        if not evaluator_ids or len(set(evaluator_ids)) != len(evaluator_ids):
            raise ValueError("Provide distinct evaluator IDs")
        config = config or {}
        if set(config)-{'concurrency','max_retries','judge_retries','retry_delay_seconds'}:raise ValueError('Unsupported Experiment configuration')
        number(config.get('retry_delay_seconds',0.5),'retry_delay_seconds',0,30)
        concurrency = config.get('concurrency', 4)
        retries = config.get('max_retries', 0)
        judge_retries = config.get('judge_retries', 1)
        if type(concurrency) is not int or not 1 <= concurrency <= 32:
            raise ValueError('concurrency must be 1..32')
        if any(type(n) is not int or not 0 <= n <= 3 for n in (retries, judge_retries)):
            raise ValueError('retries must be 0..3')
        if idempotency_key is not None and (not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200):
            raise ValueError('Invalid idempotency key')
        request_hash = digest([project_id, dataset_id, agent_version_id, sorted(evaluator_ids), config])
        id_, snapshot_id = uid(), uid()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if idempotency_key:
                previous = db.execute('SELECT * FROM experiments WHERE project_id=? AND idempotency_key=?',
                                      (project_id, idempotency_key)).fetchone()
                if previous:
                    if previous['request_hash'] != request_hash:
                        raise Conflict('Idempotency key already used with different configuration')
                    return {'id': previous['id'], 'status': previous['status'], 'reused': True}
            ds, agent = self.required(db, "datasets", dataset_id), self.required(db, "agent_versions", agent_version_id)
            if ds["project_id"] != project_id or agent["project_id"] != project_id:
                raise ValueError("Dataset and agent must belong to project")
            for eid in evaluator_ids:
                if self.required(db, "evaluator_revisions", eid)["project_id"] != project_id:
                    raise ValueError("Evaluator must belong to project")
            rows = db.execute("SELECT c.id, c.revision, r.content_hash FROM cases c JOIN case_revisions r "
                              "ON r.case_id=c.id AND r.revision=c.revision WHERE c.dataset_id=? ORDER BY c.id",
                              (dataset_id,)).fetchall()
            if not rows:
                raise ValueError("Dataset has no cases")
            frozen = [(r["id"], r["revision"], r["content_hash"]) for r in rows]
            shash = digest(frozen)
            db.execute("INSERT INTO dataset_snapshots VALUES (?,?,?,?)", (snapshot_id, dataset_id, shash, now()))
            db.executemany("INSERT INTO snapshot_cases VALUES (?,?,?,?,?)",
                           [(snapshot_id, *item, idx) for idx, item in enumerate(frozen)])
            db.execute('INSERT INTO experiments (id,project_id,snapshot_id,agent_version_id,status,created_at,config_json,idempotency_key,request_hash) VALUES (?,?,?,?,?,?,?,?,?)',
                       (id_, project_id, snapshot_id, agent_version_id, 'queued', now(), encoded(config), idempotency_key, request_hash))
            db.executemany("INSERT INTO experiment_evaluators VALUES (?,?)", [(id_, eid) for eid in evaluator_ids])
        return {"id": id_, "status": "queued", "snapshot_hash": shash, "case_count": len(frozen)}

    def get_experiment(self, experiment_id: str) -> dict:
        with self.connect() as db:
            exp = self.required(db, "experiments", experiment_id)
            runs = db.execute("SELECT * FROM runs WHERE experiment_id=? ORDER BY case_id", (experiment_id,)).fetchall()
            scores = db.execute("SELECT s.* FROM scores s JOIN runs r ON s.run_id=r.id WHERE r.experiment_id=?",
                                (experiment_id,)).fetchall()
            return {**dict(exp), "summary": self.summary_from_db(db, exp), "runs": [{**dict(r), "output": json.loads(r["output_json"]) if r["output_json"] else None}
                                            for r in runs], "scores": [dict(s) for s in scores]}

    def get_run(self, run_id: str) -> dict:
        with self.connect() as db:
            run = dict(self.required(db, "runs", run_id))
            scores = db.execute("SELECT * FROM scores WHERE run_id=?", (run_id,)).fetchall()
        return {**run, "output": json.loads(run["output_json"]) if run["output_json"] else None,
                "trace": self.get_trace(run["trace_id"]) if run["trace_id"] else None,
                "scores": [dict(s) for s in scores],
                "attempts": self.list_attempts(run_id)}

    def list_attempts(self, run_id):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM attempts WHERE run_id=? ORDER BY number',(run_id,))]

    def list_items(self, table, project_id=None, limit=50, offset=0, filters=None):
        allowed = {'projects', 'datasets', 'traces', 'agent_versions', 'evaluator_revisions', 'experiments', 'artifacts'}
        if table not in allowed:
            raise ValueError('Invalid resource')
        limit, offset = int(limit), int(offset)
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError('Pagination limit must be 1..200; offset >= 0')
        clauses, values = [], []
        if project_id:
            clauses.append('id=?' if table == 'projects' else 'project_id=?')
            values.append(project_id)
        for key, value in (filters or {}).items():
            if key not in {'status', 'kind', 'name', 'label'}:
                raise ValueError('Unsupported filter')
            columns = {'projects': {'name'}, 'datasets': {'name'}, 'traces': {'kind','status'},
                       'agent_versions': {'label'}, 'evaluator_revisions': {'name','kind'},
                       'experiments': {'status'}, 'artifacts': {'kind'}}[table]
            if key not in columns:
                raise ValueError('Filter unavailable on resource')
            clauses.append(f'{key}=?'); values.append(value)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with self.connect() as db:
            total = db.execute(f'SELECT COUNT(*) FROM {table}{where}', values).fetchone()[0]
            rows = db.execute(f'SELECT * FROM {table}{where} ORDER BY id LIMIT ? OFFSET ?', values+[limit,offset]).fetchall()
        return {'items': [dict(r) for r in rows], 'total': total, 'limit': limit, 'offset': offset}

    def list_cases(self, dataset_id, limit=50, offset=0, category=None, severity=None):
        limit, offset = int(limit), int(offset)
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError('Invalid pagination')
        with self.connect() as db:
            self.required(db, 'datasets', dataset_id)
            conditions, values = ['c.dataset_id=?'], [dataset_id]
            for name, value in [('category',category), ('severity',severity)]:
                if value is not None:
                    conditions.append(f"json_extract(r.content_json,'$.metadata.{name}')=?")
                    values.append(value)
            sql = ' FROM cases c JOIN case_revisions r ON r.case_id=c.id AND r.revision=c.revision WHERE '+ ' AND '.join(conditions)
            total = db.execute('SELECT COUNT(*)'+sql, values).fetchone()[0]
            rows = db.execute('SELECT r.content_json'+sql+' ORDER BY c.id LIMIT ? OFFSET ?', values+[limit,offset])
            return {'items': [json.loads(r[0]) for r in rows], 'total': total, 'limit': limit, 'offset': offset}

    def import_cases(self, dataset_id, records=None, csv_text=None):
        if (records is None) == (csv_text is None):
            raise ValueError('Provide records or csv_text')
        if csv_text is not None:
            records = []
            for row in csv.DictReader(io.StringIO(csv_text)):
                records.append({'input': json.loads(row['input']),
                                'expected': json.loads(row['expected']) if row.get('expected') else None,
                                'metadata': json.loads(row['metadata']) if row.get('metadata') else {}})
        if not isinstance(records, list) or not 1 <= len(records) <= 10000:
            raise ValueError('Import expects 1..10000 records')
        # Validate the complete batch before opening a transaction; a bad record imports nothing.
        for row in records:
            if not isinstance(row, dict) or not isinstance(row.get('input'), dict) or not isinstance(row.get('metadata', {}), dict):
                raise ValueError('Each record needs object input and metadata')
        created = []
        with self.connect() as db:
            self.required(db, 'datasets', dataset_id)
            for row in records:
                id_ = uid()
                content = {'id': id_, 'revision': 1, 'input': row['input'],
                           'expected': row.get('expected', row.get('expected_output')), 'metadata': row.get('metadata', {}),
                           'source_trace_id': None}
                db.execute('INSERT INTO cases VALUES (?,?,?)', (id_,dataset_id,1))
                db.execute('INSERT INTO case_revisions VALUES (?,?,?,?)', (id_,1,encoded(content),digest(content)))
                created.append(id_)
        return {'imported': len(created), 'case_ids': created}

    def export_cases(self, dataset_id):
        with self.connect() as db:
            self.required(db, 'datasets', dataset_id)
            rows = db.execute('SELECT r.content_json FROM cases c JOIN case_revisions r ON c.id=r.case_id AND c.revision=r.revision WHERE c.dataset_id=? ORDER BY c.id', (dataset_id,))
            return {'records': [json.loads(r[0]) for r in rows]}

    def issue_key(self, project_id, label='client'):
        token, hashed = new_key(); id_ = uid()
        with self.connect() as db:
            self.required(db, 'projects', project_id)
            db.execute('INSERT INTO api_keys VALUES (?,?,?,?,?,?)', (id_, project_id, hashed, label, 0, now()))
        return {'id': id_, 'project_id': project_id, 'token': token}

    def authenticate(self, token):
        with self.connect() as db:
            row = db.execute('SELECT project_id FROM api_keys WHERE key_hash=? AND revoked=0',
                             (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            return row[0] if row else None

    def revoke_key(self, key_id):
        with self.connect() as db:
            self.required(db, 'api_keys', key_id)
            db.execute('UPDATE api_keys SET revoked=1 WHERE id=?', (key_id,))
        return {'revoked': True}

    def save_artifact(self, project_id, run_id, kind, content, mime_type='application/json'):
        data = content if isinstance(content, bytes) else encoded(redact(content)).encode()
        if len(data) > 16_000_000:
            raise ValueError('Artifact exceeds 16 MB')
        id_ = uid(); target = self.artifact_root / id_
        target.write_bytes(data)
        try:
            with self.connect() as db:
                self.required(db, 'projects', project_id)
                db.execute('INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?)',
                           (id_, project_id, run_id, kind, mime_type, len(data), hashlib.sha256(data).hexdigest(), now()))
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return id_

    def read_artifact(self, artifact_id):
        with self.connect() as db:
            row = dict(self.required(db, 'artifacts', artifact_id))
        data = (self.artifact_root / row['id']).read_bytes()
        if hashlib.sha256(data).hexdigest() != row['sha256']:
            raise ValueError('Artifact integrity check failed')
        return row, data

    def summary_from_db(self, db, experiment):
        from .reports import summary
        return summary(db, experiment)

    def cancel(self, experiment_id):
        with self.connect() as db:
            exp = self.required(db, 'experiments', experiment_id)
            if exp['status'] in {'queued','running'}:
                db.execute("UPDATE experiments SET cancel_requested=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END WHERE id=?", (experiment_id,))
        return {'id': experiment_id, 'cancel_requested': True}

    def claim(self, experiment_id=None, lease_seconds=15):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            sql = "SELECT * FROM experiments WHERE cancel_requested=0 AND (status='queued' OR (status='running' AND COALESCE(lease_expires,0)<?))"
            params = [time.time()]
            if experiment_id:
                sql += ' AND id=?'; params.append(experiment_id)
            row = db.execute(sql+' ORDER BY created_at LIMIT 1', params).fetchone()
            if not row:
                return None
            token = uid()
            db.execute("UPDATE experiments SET status='running',lease_token=?,lease_expires=? WHERE id=?", (token,time.time()+lease_seconds,row['id']))
            db.execute("UPDATE attempts SET status='abandoned',finished_at=? WHERE status='running' AND run_id IN (SELECT id FROM runs WHERE experiment_id=?)", (now(), row['id']))
            return {**dict(row), 'lease_token': token, 'status': 'running'}

    def owns_lease(self, db, experiment_id, token, allow_cancel=False):
        row = self.required(db, 'experiments', experiment_id)
        if row['status'] != 'running' or row['lease_token'] != token or (row['lease_expires'] or 0) < time.time():
            raise LeaseLost('Worker lease was lost')
        if row['cancel_requested'] and not allow_cancel:
            raise LeaseLost('Experiment cancellation requested')
        return row

    def heartbeat(self, experiment_id, token, lease_seconds=15):
        with self.connect() as db:
            row = self.owns_lease(db, experiment_id, token, allow_cancel=True)
            db.execute('UPDATE experiments SET lease_expires=? WHERE id=? AND lease_token=?', (time.time()+lease_seconds,experiment_id,token))
            return not row['cancel_requested']

    def start_attempt(self, experiment_id, token, case):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE'); self.owns_lease(db, experiment_id, token)
            row = db.execute('SELECT * FROM runs WHERE experiment_id=? AND case_id=? AND case_revision=?', (experiment_id,case.id,case.revision)).fetchone()
            if row and row['status'] in {'succeeded','failed','timed_out','cancelled'}:
                return None
            run_id = row['id'] if row else uid()
            if not row:
                db.execute("INSERT INTO runs (id,experiment_id,case_id,case_revision,status) VALUES (?,?,?,?,'running')", (run_id,experiment_id,case.id,case.revision))
            number = db.execute('SELECT COALESCE(MAX(number),0)+1 FROM attempts WHERE run_id=?', (run_id,)).fetchone()[0]
            attempt_id = uid()
            db.execute("INSERT INTO attempts (id,run_id,number,status,started_at) VALUES (?,?,?,'running',?)", (attempt_id,run_id,number,now()))
            db.execute("UPDATE runs SET status='running',attempt_id=? WHERE id=?", (attempt_id,run_id))
        return {'id':attempt_id,'run_id':run_id,'number':number}

    def finish_attempt(self, experiment_id, token, attempt, run, trace_id, scores, artifacts, terminal=True):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE'); self.owns_lease(db, experiment_id, token)
            row = self.required(db, 'runs', attempt['run_id'])
            if row['attempt_id'] != attempt['id']:
                raise LeaseLost('A newer attempt superseded this result')
            db.execute('UPDATE attempts SET status=?,finished_at=?,error=?,trace_id=?,artifact_ids_json=?,usage_json=?,cost=?,currency=? WHERE id=?',
                       (run.status,now(),run.error,trace_id,encoded(artifacts),encoded(run.usage),run.cost,run.currency,attempt['id']))
            db.execute('UPDATE runs SET status=?,output_json=?,trace_id=?,latency_ms=?,error=?,usage_json=?,cost=?,currency=?,final_state_json=? WHERE id=?',
                       (run.status if terminal else 'retrying',encoded(run.output),trace_id,run.latency_ms,run.error,encoded(run.usage),run.cost,run.currency,encoded(run.final_state),run.id))
            if terminal:
                db.execute('DELETE FROM scores WHERE run_id=?', (run.id,))
                for s in scores:
                    db.execute('INSERT INTO scores (run_id,evaluator_revision_id,outcome,value,reason,evidence_json,artifact_id) VALUES (?,?,?,?,?,?,?)',
                               (s.run_id,s.evaluator_revision_id,s.outcome,s.value,s.reason,encoded(s.evidence_event_ids),s.artifact_id))

    def finish_experiment(self, experiment_id, token, status, error=None):
        with self.connect() as db:
            self.owns_lease(db, experiment_id, token, allow_cancel=True)
            row = self.required(db, 'experiments', experiment_id)
            if row['cancel_requested']:
                status = 'cancelled'
                db.execute("UPDATE runs SET status='cancelled' WHERE experiment_id=? AND status IN ('running','retrying')", (experiment_id,))
                db.execute("UPDATE attempts SET status='cancelled',finished_at=? WHERE status='running' AND run_id IN (SELECT id FROM runs WHERE experiment_id=?)", (now(),experiment_id))
            db.execute('UPDATE experiments SET status=?,finished_at=?,error=?,lease_token=NULL,lease_expires=NULL WHERE id=? AND lease_token=?', (status,now(),error,experiment_id,token))

    def rerun(self, experiment_id, agent_version_id=None):
        """Reuse the original immutable snapshot even if the live Dataset changed."""
        id_ = uid()
        with self.connect() as db:
            exp = self.required(db,'experiments',experiment_id)
            agent_id=agent_version_id or exp['agent_version_id']
            if self.required(db,'agent_versions',agent_id)['project_id']!=exp['project_id']:
                raise ValueError('Agent belongs to a different project')
            db.execute('INSERT INTO experiments (id,project_id,snapshot_id,agent_version_id,status,created_at,config_json) VALUES (?,?,?,?,?,?,?)',
                       (id_,exp['project_id'],exp['snapshot_id'],agent_id,'queued',now(),exp['config_json']))
            db.execute('INSERT INTO experiment_evaluators SELECT ?,evaluator_revision_id FROM experiment_evaluators WHERE experiment_id=?',(id_,experiment_id))
        return {'id':id_,'status':'queued','source_experiment_id':experiment_id}
