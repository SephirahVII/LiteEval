"""Publish the converted public FAB release; never calls a model or invents scores."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from liteeval.bootstrap import benchmark_service
from liteeval.infrastructure.persistence.store import Store


def main():
    directory = ROOT / 'data/fab-v2-native'
    directory.mkdir(parents=True, exist_ok=True)
    store = Store(directory / 'liteeval.db')
    with store.connect() as db:
        project = db.execute('SELECT id FROM projects WHERE name=? ORDER BY created_at,id', ('FAB v2 native package preparation',)).fetchone()
    project_id = project[0] if project else store.create_project('FAB v2 native package preparation')['id']
    service = benchmark_service(store)
    release = service.publish(project_id, ROOT / 'benchmarks/fab_v2_public')
    tasks = release['manifest']['tasks']
    criterion_count = sum(len(json.loads(service.blobs.get(t['judge']['resources']['judge/rubric.json']))['criteria']) for t in tasks)
    try:
        service.preflight(project_id, release['id'], 'liteeval.model-json/v1', trusted_local=True)
        raise RuntimeError('Plain file/command Harness must not silently pass research capability checks')
    except ValueError as exc:
        if 'missing Harness capabilities' not in str(exc): raise
        readiness = {'ready': False, 'reason': str(exc)}
    report = {'mode': 'prepared_only', 'project_id': project_id, 'release_id': release['id'],
              'release_digest': release['digest'], 'task_count': len(tasks), 'criterion_count': criterion_count,
              'categories': sorted({t['metadata']['category'] for t in tasks}),
              'model_calls': 0, 'graded_case_count': 0, 'readiness': readiness,
              'official_leaderboard_comparable': False}
    (directory / 'catalog.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
