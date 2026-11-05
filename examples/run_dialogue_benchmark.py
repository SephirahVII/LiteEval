"""Run the three-turn Benchmark with Codex CLI and a MiniMax Responses endpoint."""

import argparse
import asyncio
import getpass
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

from liteeval.application.benchmark_exports import export_execution
from liteeval.bootstrap import benchmark_service
from liteeval.infrastructure.persistence.store import Store
from liteeval.infrastructure.sources.harness import load_harness


ROOT = Path(__file__).resolve().parents[1]


async def run(args):
    store = Store(args.db)
    project = args.project or store.create_project('Dialogue Benchmark')['id']
    service = benchmark_service(store)
    source = ROOT / 'benchmarks/dialogue_session'
    with tempfile.TemporaryDirectory() as tmp:
        if args.docker_image:
            if not re.fullmatch(r'[a-z0-9][a-z0-9._/:-]*@sha256:[a-f0-9]{64}', args.docker_image):
                raise ValueError('Use an immutable Docker image digest')
            package = Path(tmp) / 'dialogue'
            shutil.copytree(source, package)
            dataset = package / 'dataset.toml'
            digest = args.docker_image.rsplit(':', 1)[1]
            dataset.write_text(re.sub(r'^version = "([^"]+)"$', rf'version = "\g<1>-docker-{digest}"',
                                      dataset.read_text(encoding='utf-8'), flags=re.MULTILINE)
                               .replace('type = "workspace"', f'type = "docker"\nimage = "{args.docker_image}"\nnetwork = "bridge"'),
                               encoding='utf-8')
        else:
            package = source
        release = service.publish(project, package)
    spec, sha = load_harness(ROOT / 'harnesses/codex_dialogue', service.blobs)
    revision = service.repository.register(project, spec['definition']['harness']['id'], 'agent', sha, spec)
    print(f'Project ID: {project}', file=sys.stderr, flush=True)
    result = await service.run(project, release['id'], revision, trusted_local=args.trusted_local,
                               on_started=lambda run_id: print(f'Execution ID: {run_id}', file=sys.stderr, flush=True))
    export = Path(args.export).resolve()
    export.parent.mkdir(parents=True, exist_ok=True)
    export_execution(service, project, result['execution_id'], export)
    print(json.dumps({'project_id': project, 'release_id': release['id'], 'release_digest': release['digest'],
                      'harness_revision_id': revision, 'harness_digest': sha, 'export': str(export),
                      'result': result}, ensure_ascii=False, indent=2))
    return result['summary']['pass'] == result['summary']['total']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--project')
    parser.add_argument('--export', required=True)
    environment = parser.add_mutually_exclusive_group(required=True)
    environment.add_argument('--trusted-local', action='store_true')
    environment.add_argument('--docker-image')
    parser.add_argument('--read-key', action='store_true', help='Prompt securely, or read one line from redirected stdin')
    args = parser.parse_args()
    if args.read_key:
        value = getpass.getpass('MiniMax API Key: ') if sys.stdin.isatty() else sys.stdin.readline().strip()
        if not value:
            parser.error('MiniMax API Key is required')
        os.environ['MINIMAX_API_KEY'] = value
    os.environ.setdefault('LITEEVAL_CODEX_MODEL', 'MiniMax-M3.1-Flash')
    os.environ.setdefault('LITEEVAL_CODEX_BASE_URL', 'https://api.minimax.cn/v1')
    raise SystemExit(0 if asyncio.run(run(args)) else 1)


if __name__ == '__main__':
    main()
