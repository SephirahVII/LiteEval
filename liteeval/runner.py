"""Job execution. A claim transaction prevents two workers running one experiment."""

import asyncio
import json

from .adapters import HTTPAdapter
from .core import EvalCase
from .evaluators import ConfiguredEvaluator
from .harness import SimpleHarness
from .storage import Store, encoded, now


class ExperimentRunner:
    def __init__(self, store: Store):
        self.store = store

    async def run(self, experiment_id: str) -> bool:
        with self.store.connect() as db:
            exp = self.store.required(db, "experiments", experiment_id)
            updated = db.execute("UPDATE experiments SET status='running' WHERE id=? AND status='queued'",
                                 (experiment_id,)).rowcount
            if not updated:
                return False
            agent = self.store.required(db, "agent_versions", exp["agent_version_id"])
            refs = db.execute("SELECT e.* FROM evaluator_revisions e JOIN experiment_evaluators x "
                              "ON x.evaluator_revision_id=e.id WHERE x.experiment_id=? ORDER BY e.id",
                              (experiment_id,)).fetchall()
            cases = db.execute("SELECT r.content_json FROM snapshot_cases s JOIN case_revisions r "
                               "ON r.case_id=s.case_id AND r.revision=s.revision "
                               "WHERE s.snapshot_id=? ORDER BY s.ordinal", (exp["snapshot_id"],)).fetchall()
            project_id = exp["project_id"]
        try:
            adapter = HTTPAdapter(json.loads(agent["config_json"]))
            evaluators = [ConfiguredEvaluator(r["id"], r["kind"], json.loads(r["config_json"])) for r in refs]
            harness = SimpleHarness()
            for item in cases:
                data = json.loads(item["content_json"])
                case = EvalCase(**data)
                try:
                    run = await harness.run(case, adapter)
                except Exception as exc:
                    from .core import AgentRun
                    from .storage import uid
                    run = AgentRun(uid(), case.id, "failed", error=f"{type(exc).__name__}: {exc}")
                trace = self.store.create_trace(project_id, case.input, run.output,
                                                [vars(e) for e in run.events], run.status,
                                                run.capture_level, kind="eval", run_id=run.id)
                with self.store.connect() as db:
                    db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)",
                               (run.id, experiment_id, case.id, case.revision, run.status,
                                encoded(run.output), trace["id"], run.latency_ms, run.error))
                for evaluator in evaluators:
                    try:
                        score = await evaluator.evaluate(case, run)
                    except Exception as exc:
                        from .core import Score
                        score = Score(run.id, evaluator.revision_id, "error", reason=f"{type(exc).__name__}: {exc}")
                    with self.store.connect() as db:
                        db.execute("INSERT INTO scores VALUES (?,?,?,?,?)",
                                   (score.run_id, score.evaluator_revision_id, score.outcome, score.value, score.reason))
            with self.store.connect() as db:
                db.execute("UPDATE experiments SET status='completed',finished_at=? WHERE id=?", (now(), experiment_id))
            return True
        except Exception:
            with self.store.connect() as db:
                db.execute("UPDATE experiments SET status='failed',finished_at=? WHERE id=?", (now(), experiment_id))
            raise

    async def run_next(self) -> str | None:
        with self.store.connect() as db:
            row = db.execute("SELECT id FROM experiments WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
        if row and await self.run(row["id"]):
            return row["id"]
        return None


def compare(store: Store, left_id: str, right_id: str) -> dict:
    with store.connect() as db:
        left, right = store.required(db, "experiments", left_id), store.required(db, "experiments", right_id)
        if left["project_id"] != right["project_id"]:
            raise ValueError("Experiments belong to different projects")
        hashes = [db.execute("SELECT snapshot_hash FROM dataset_snapshots WHERE id=?", (r["snapshot_id"],)).fetchone()[0]
                  for r in (left, right)]
        evals = [set(x[0] for x in db.execute("SELECT evaluator_revision_id FROM experiment_evaluators WHERE experiment_id=?",
                                            (r["id"],))) for r in (left, right)]
        if left["status"] != "completed" or right["status"] != "completed" or hashes[0] != hashes[1] or evals[0] != evals[1]:
            return {"comparable": False, "reason": "Both experiments must be complete and share dataset content and evaluator revisions"}
        def scores(id_):
            rows = db.execute("SELECT r.case_id,r.case_revision,s.evaluator_revision_id,s.outcome "
                              "FROM scores s JOIN runs r ON s.run_id=r.id WHERE r.experiment_id=?", (id_,))
            return {(r["case_id"], r["case_revision"], r["evaluator_revision_id"]): r["outcome"] for r in rows}
        old, new = scores(left_id), scores(right_id)
    details, counts = [], {"improved": 0, "regressed": 0, "same": 0, "unscored": 0}
    for key in sorted(old.keys() | new.keys()):
        a, b = old.get(key), new.get(key)
        if a not in {"pass", "fail"} or b not in {"pass", "fail"}:
            state = "unscored"
        elif a == b:
            state = "same"
        elif a == "fail":
            state = "improved"
        else:
            state = "regressed"
        counts[state] += 1
        details.append({"case_id": key[0], "case_revision": key[1], "evaluator_revision_id": key[2],
                        "left": a, "right": b, "change": state})
    return {"comparable": True, "counts": counts, "details": details}
