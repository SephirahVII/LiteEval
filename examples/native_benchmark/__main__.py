"""Real offline subprocess acceptance, explicitly not an LLM or official FAB run."""
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from liteeval.bootstrap import benchmark_service
from liteeval.infrastructure.persistence.store import Store
from liteeval.infrastructure.sources.harness import load_harness
from liteeval.application.benchmark_exports import export_execution


async def main():
    directory = ROOT / 'data/native-benchmark'
    directory.mkdir(parents=True, exist_ok=True)
    store = Store(directory / 'liteeval.db')
    project = store.create_project('Native benchmark execution acceptance')
    service = benchmark_service(store)
    release = service.publish(project['id'], ROOT / 'examples/native_benchmark/benchmark')
    spec, sha = load_harness(ROOT / 'examples/native_benchmark/harnesses/agent', service.blobs)
    harness_id = service.repository.register(project['id'], spec['definition']['harness']['id'], 'agent', sha, spec)
    result = await service.run(project['id'], release['id'], harness_id, trusted_local=True)
    eval_spec, eval_sha = load_harness(ROOT / 'examples/native_benchmark/harnesses/evaluation', service.blobs)
    evaluator_id = service.repository.register(project['id'], eval_spec['definition']['harness']['id'], 'evaluation', eval_sha, eval_spec)
    external_result = await service.run(project['id'], release['id'], harness_id,
                                        evaluation_ref=evaluator_id, trusted_local=True)
    target_events = len(service.repository.execution(project['id'], result['execution_id'])['events'])
    rejudged = await service.rejudge(project['id'], result['execution_id'])
    assert len(service.repository.execution(project['id'], result['execution_id'])['events']) == target_events
    archive = directory / (result['execution_id'] + '.zip')
    export_execution(service, project['id'], result['execution_id'], archive)
    report = {'kind': 'deterministic_subprocess_acceptance', 'model_calls': 0, 'project_id': project['id'],
              'release_id': release['id'], 'harness_revision_id': harness_id, 'result': result,
              'custom_evaluation_execution_id': external_result['execution_id'],
              'custom_evaluation_summary': external_result['summary'],
              'rejudge_execution_id': rejudged['execution_id'], 'export': str(archive)}
    (directory / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    assert result['summary']['pass'] == 2 and result['summary']['scoring_coverage'] == 1
    assert external_result['summary']['pass'] == 2
    assert all(t['grading']['outcome'] == 'pass' for t in rejudged['tasks'])


if __name__ == '__main__': asyncio.run(main())
