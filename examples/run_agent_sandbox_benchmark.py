"""Run the agent-sandbox benchmark with a real coding agent inside Docker sandboxes.

Usage:
    python examples/run_agent_sandbox_benchmark.py --harness claude   # harnesses/claude_code
    python examples/run_agent_sandbox_benchmark.py --harness dsh     # harnesses/dsh

Prerequisites:
- Sandbox image built and pinned: images/agent-sandbox/build.sh (writes the
  repo digest into benchmarks/agent_sandbox/dataset.toml)
- Docker daemon reachable from this Worker
- The harness's API key in the environment (ANTHROPIC_API_KEY or
  DEEPSEEK_API_KEY; never persisted by the platform)
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from liteeval.bootstrap import benchmark_service
from liteeval.infrastructure.persistence.store import Store
from liteeval.infrastructure.sources.harness import load_harness
from liteeval.application.benchmark_exports import export_execution

HARNESS_DIRS = {'claude': 'harnesses/claude_code', 'dsh': 'harnesses/dsh'}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--harness', choices=sorted(HARNESS_DIRS), required=True)
    args = parser.parse_args()
    harness_dir = ROOT / HARNESS_DIRS[args.harness]

    directory = ROOT / ('data/agent-sandbox-' + args.harness)
    directory.mkdir(parents=True, exist_ok=True)
    store = Store(directory / 'liteeval.db')
    project = store.create_project('Agent sandbox benchmark (%s)' % args.harness)
    service = benchmark_service(store)
    release = service.publish(project['id'], ROOT / 'benchmarks/agent_sandbox')
    spec, sha = load_harness(harness_dir, service.blobs)
    harness_id = service.repository.register(project['id'], spec['definition']['harness']['id'], 'agent', sha, spec)
    # Preflight enforces the harness-declared environment before any container starts.
    result = await service.run(project['id'], release['id'], harness_id)
    archive = directory / (result['execution_id'] + '.zip')
    export_execution(service, project['id'], result['execution_id'], archive)
    report = {'kind': 'agent_sandbox_docker', 'harness': args.harness, 'project_id': project['id'],
              'release_id': release['id'], 'harness_revision_id': harness_id,
              'result': result, 'export': str(archive)}
    (directory / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
