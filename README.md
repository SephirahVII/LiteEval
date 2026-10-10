# LiteEval local backend prototype

This runnable Python implementation covers the core PRD path: **production Trace → Case → immutable Dataset snapshot → Experiment → Agent Run → Scores → paired Compare**. It has no frontend and needs Python 3.11+ with no third-party packages. SQLite stores local data.

## Start

From this directory, open three terminals:

```bash
python examples/demo_agent.py v1 8766
python -m liteeval.cli api
python -m liteeval.cli worker
```

The API listens only on `127.0.0.1:8765`; `GET /v1/health` checks it. Use the standard library test suite:

```bash
python -m unittest discover -s tests -v
```

To create an evaluation with curl (IDs returned by each request go into subsequent bodies):

```bash
curl -s localhost:8765/v1/projects -H 'Content-Type: application/json' -d '{"name":"Support"}'
curl -s localhost:8765/v1/datasets -H 'Content-Type: application/json' -d '{"project_id":"PROJECT_ID","name":"regression"}'
curl -s localhost:8765/v1/traces -H 'Content-Type: application/json' -d '{"project_id":"PROJECT_ID","input":{"question":"如何退款？"},"output":"错误回复","status":"failed"}'
curl -s localhost:8765/v1/datasets/DATASET_ID/cases/from-trace -H 'Content-Type: application/json' -d '{"trace_id":"TRACE_ID","expected":"退款请在订单页申请"}'
curl -s localhost:8765/v1/agent-versions -H 'Content-Type: application/json' -d '{"project_id":"PROJECT_ID","label":"v1","config":{"type":"http","endpoint":"http://127.0.0.1:8766/agent","output_path":"answer"}}'
curl -s localhost:8765/v1/evaluators -H 'Content-Type: application/json' -d '{"project_id":"PROJECT_ID","name":"Exact","kind":"exact_match","config":{}}'
curl -s localhost:8765/v1/experiments -H 'Content-Type: application/json' -d '{"project_id":"PROJECT_ID","dataset_id":"DATASET_ID","agent_version_id":"AGENT_ID","evaluator_ids":["EVALUATOR_ID"]}'
curl -s localhost:8765/v1/experiments/EXPERIMENT_ID
```

The worker claims queued experiments. If no worker is running, use `python -m liteeval.cli run EXPERIMENT_ID`. Start a second demo agent with `python examples/demo_agent.py v2 8767`, create a second Agent Version pointing at port 8767 and run the same dataset and evaluator again. Compare via `GET /v1/compare?left=OLD_ID&right=NEW_ID` or `python -m liteeval.cli compare OLD_ID NEW_ID`. For reproducible experiments, each Agent Version must point to a stable deployment; a label alone does not freeze external code.

## Code boundaries

| Module | Responsibility |
| --- | --- |
| `core.py` | Shared `ContentPart`, `Event`, `EvalCase`, `AgentRun`, `Score` contracts |
| `storage.py` | SQLite schema, immutable case revisions, experiment snapshots, project checks |
| `adapters.py` | HTTP Agent adapter and explicit event normalization |
| `evaluators.py` | Exact match, contains, tool called, optional LLM Judge |
| `runner.py` | Simple harness, worker, independent scoring, strict paired compare |
| `harness.py`, `environments.py` | Adapter/environment lifecycle boundary; HTTP runs use a no-op environment |
| `api.py` | Local JSON HTTP interface; calls reusable modules |
| `cli.py` | Separate API and worker entry points |

The HTTP Agent receives `{"input": case.input}` and returns `{"answer": ..., "events": [...]}`; `events` is optional. Without reported events the capture level is `output_only`, so trajectory scoring abstains. LLM Judge uses `base_url`, `model`, `rubric`, and the environment variable named by `api_key_env` (default `LITEEVAL_JUDGE_API_KEY`); it posts to an OpenAI-compatible `/chat/completions` endpoint. Supplier compatibility and judge reliability require real integration testing.

## Scope and constraints

This is a **local functional prototype**, not a production service. The API has no authentication, TLS, rate limiting, PII masking, artifact storage, retries, durable worker leases, or deployment gate. Keep it on loopback. HTTP Agent and judge endpoints are user-defined, so avoid untrusted configs. The separate process worker claims jobs atomically, but a worker crash leaves a job `running`; recovery and attempts are future work. The judge does not yet calibrate or persist raw model replies. Multimodal data contracts are reserved, while binary storage and multimodal evaluators are not implemented. No Codex or Docker adapter is included.

The existing design document describes the future architecture; this code deliberately implements only the first local end-to-end slice.
