"""Run the financial stress package and export a reproducible evidence bundle."""

import argparse
import asyncio
import json
from pathlib import Path
from liteeval.application.benchmark_exports import export_execution
from liteeval.bootstrap import benchmark_service
from liteeval.infrastructure.persistence.store import Store
from liteeval.infrastructure.sources.harness import load_harness


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('rule', 'model', 'live'), default='rule')
    parser.add_argument('--db', type=Path, default=Path('data/financial-stress/liteeval.db'))
    parser.add_argument('--project')
    parser.add_argument('--target-connection')
    parser.add_argument('--output', type=Path, default=Path('data/financial-stress'))
    args = parser.parse_args()
    if args.mode != 'rule' and not (args.project and args.target_connection):
        parser.error('Model and live modes require --project and --target-connection')
    if args.mode == 'rule' and args.target_connection:
        parser.error('Rule mode does not use a target connection')
    store = Store(args.db)
    project = args.project or store.create_project('Financial stress acceptance')['id']
    service = benchmark_service(store)
    root = Path(__file__).resolve().parents[2]
    package = root / 'benchmarks/financial_stress'
    release = service.publish(project, package)

    def register(path):
        spec, sha = load_harness(path, service.blobs)
        definition = spec['definition']['harness']
        return service.repository.register(project, definition['id'], definition['role'], sha, spec)

    workflow = register(package / 'workflow')
    agent_name = 'financial_reference' if args.mode == 'rule' else 'json_action'
    agent = register(root / 'harnesses' / agent_name)
    result = await service.run(project, release['id'], agent, target_connection=args.target_connection,
                               workflow_ref=workflow, workflow_options={'clock': 'live' if args.mode == 'live' else 'replay'},
                               trusted_local=True)
    args.output.mkdir(parents=True, exist_ok=True)
    archive = args.output / f"{result['execution_id']}.zip"
    export_execution(service, project, result['execution_id'], archive)
    report = {'mode': args.mode, 'project_id': project, 'release_id': release['id'],
              'release_digest': release['digest'], 'execution_id': result['execution_id'],
              'summary': result['summary'], 'usage': result['usage'], 'archive': str(archive.resolve()),
              'cases': [{'task_id': task['task_id'], 'grading': task['grading'], 'error': task['error'],
                         'metrics': next((c['metrics'] for c in task['checks'] if 'metrics' in c), None)}
                        for task in result['tasks']]}
    report_path = args.output / f"{result['execution_id']}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.mode == 'rule' and result['summary']['pass'] != result['summary']['total']:
        raise SystemExit('Reference policy did not pass every scenario')


if __name__ == '__main__':
    asyncio.run(main())
