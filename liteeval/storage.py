"""SQLite repository. Immutable JSON revisions and snapshots keep comparisons fair."""

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def uid() -> str:
    return uuid.uuid4().hex


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def encoded(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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


class Store:
    def __init__(self, path: str | os.PathLike[str] = "data/liteeval.db"):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)

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
        if table not in {"projects", "traces", "datasets", "cases", "agent_versions", "evaluator_revisions", "experiments", "runs"}:
            raise ValueError("Invalid table")
        row = db.execute(f"SELECT * FROM {table} WHERE id=?", (id_,)).fetchone()
        if row is None:
            raise NotFound(f"{table}: {id_}")
        return row

    def create_project(self, name: str) -> dict:
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
                     run_id: str | None = None) -> dict:
        id_ = uid()
        with self.connect() as db:
            self.required(db, "projects", project_id)
            db.execute("INSERT INTO traces VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (id_, project_id, kind, run_id, encoded(input), encoded(output), encoded(events or []),
                        capture_level, status, now()))
        return {"id": id_, "kind": kind, "capture_level": capture_level}

    def get_trace(self, trace_id: str) -> dict:
        with self.connect() as db:
            r = self.required(db, "traces", trace_id)
            return {**dict(r), "input": json.loads(r["input_json"]), "output": json.loads(r["output_json"]),
                    "events": json.loads(r["events_json"])}

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
        if config.get("type") != "http" or not config.get("endpoint"):
            raise ValueError("HTTP agent needs type=http and endpoint")
        with self.connect() as db:
            self.required(db, "projects", project_id)
            db.execute("INSERT INTO agent_versions VALUES (?,?,?,?,?)",
                       (id_, project_id, label, encoded(config), digest(config)))
        return {"id": id_, "label": label}

    def add_evaluator(self, project_id: str, name: str, kind: str, config: dict) -> dict:
        if kind not in {"exact_match", "contains", "tool_called", "llm_judge"}:
            raise ValueError("Unsupported evaluator kind")
        id_ = uid()
        with self.connect() as db:
            self.required(db, "projects", project_id)
            db.execute("INSERT INTO evaluator_revisions VALUES (?,?,?,?,?,?)",
                       (id_, project_id, name, kind, encoded(config), digest([kind, config])))
        return {"id": id_, "name": name, "kind": kind}

    def create_experiment(self, project_id: str, dataset_id: str, agent_version_id: str,
                          evaluator_ids: list[str]) -> dict:
        if not evaluator_ids or len(set(evaluator_ids)) != len(evaluator_ids):
            raise ValueError("Provide distinct evaluator IDs")
        id_, snapshot_id = uid(), uid()
        with self.connect() as db:
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
            db.execute("INSERT INTO experiments VALUES (?,?,?,?,?,?,?)",
                       (id_, project_id, snapshot_id, agent_version_id, "queued", now(), None))
            db.executemany("INSERT INTO experiment_evaluators VALUES (?,?)", [(id_, eid) for eid in evaluator_ids])
        return {"id": id_, "status": "queued", "snapshot_hash": shash, "case_count": len(frozen)}

    def get_experiment(self, experiment_id: str) -> dict:
        with self.connect() as db:
            exp = self.required(db, "experiments", experiment_id)
            runs = db.execute("SELECT * FROM runs WHERE experiment_id=? ORDER BY case_id", (experiment_id,)).fetchall()
            scores = db.execute("SELECT s.* FROM scores s JOIN runs r ON s.run_id=r.id WHERE r.experiment_id=?",
                                (experiment_id,)).fetchall()
            return {**dict(exp), "runs": [{**dict(r), "output": json.loads(r["output_json"]) if r["output_json"] else None}
                                            for r in runs], "scores": [dict(s) for s in scores]}

    def get_run(self, run_id: str) -> dict:
        with self.connect() as db:
            run = dict(self.required(db, "runs", run_id))
            scores = db.execute("SELECT * FROM scores WHERE run_id=?", (run_id,)).fetchall()
        return {**run, "output": json.loads(run["output_json"]) if run["output_json"] else None,
                "trace": self.get_trace(run["trace_id"]) if run["trace_id"] else None,
                "scores": [dict(s) for s in scores]}
