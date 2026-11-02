"""Real Qianwen API trial. No Codex login; opt-in model calls with user credentials."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from liteeval.application import connections
from liteeval.application.benchmark_exports import export_execution
from liteeval.bootstrap import benchmark_service
from liteeval.infrastructure.persistence.store import Store
from liteeval.infrastructure.network.http import NoRedirect
from liteeval.integrations.benchmarks.fab_package import convert_fab


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')


async def main(args):
    if not args.run: raise SystemExit('Use --run to authorize real provider calls; no default model calls.')
    key = os.environ.get('QIANWEN_API_KEY')
    if not key or not os.environ.get('LITEEVAL_SECRET_KEY'):
        raise SystemExit('Set QIANWEN_API_KEY and LITEEVAL_SECRET_KEY in the executing process')
    root = (ROOT / args.output).resolve(); root.mkdir(parents=True, exist_ok=True)
    store = Store(root / 'liteeval.db')
    with store.connect() as db:
        row = db.execute('SELECT id FROM projects WHERE name=? ORDER BY created_at,id', ('FAB public real Qianwen trial',)).fetchone()
    project = row[0] if row else store.create_project('FAB public real Qianwen trial')['id']
    models = args.models.split(',')
    opener = urllib.request.build_opener(NoRedirect)
    request = urllib.request.Request(args.base_url + '/models', headers={'Authorization': 'Bearer ' + key})
    with opener.open(request, timeout=30) as response: available = json.loads(response.read(4_000_000))
    write_json(root / 'available-models.json', available)
    available_ids = {m['id'] for m in available.get('data', [])}
    if any(model not in available_ids for model in models): raise ValueError('A requested model is unavailable; no model downgrade permitted')
    ids = {}
    for model in models:
        with store.connect() as db:
            row = db.execute('SELECT id FROM model_connections WHERE project_id=? AND model=? AND endpoint=? AND archived_at IS NULL', (project, model, args.base_url)).fetchone()
        if row:
            ids[model] = row[0]
            if connections.api_key(store, row[0]) != key: connections.update(store, row[0], {'api_key': key})
        else:
            ids[model] = connections.create(store, {'project_id': project, 'name': 'Qianwen ' + model,
                'kind': 'openai_compatible', 'endpoint': args.base_url, 'model': model, 'api_key': key})['id']
    package = root / ('package-rows-' + args.rows.replace(',', '-'))
    convert_fab(ROOT / 'benchmarks/finance_agent_v2', package, [int(n) for n in args.rows.split(',')])
    service = benchmark_service(store)
    release = service.publish(project, package)
    state = {'mode': 'real_provider_research', 'authentication': 'user_provided_qianwen_api_key',
             'codex_login_used': False, 'project_id': project, 'release_id': release['id'],
             'release_digest': release['digest'], 'endpoint': args.base_url, 'models': models,
             'connections': ids, 'official_leaderboard_comparable': False, 'results': {}}
    write_json(root / 'progress.json', state)

    async def run_model(model):
        print(json.dumps({'event': 'starting_target', 'model': model}, ensure_ascii=False), flush=True)
        result = await service.run(project, release['id'], 'liteeval.qianwen-research/v1', target_connection=ids[model],
            judge_connection=ids[models[0]], judge_parameters={'max_tokens': 12000, 'enable_thinking': True}, trusted_local=True)
        label = re.sub(r'[^a-zA-Z0-9._-]', '_', model)
        write_json(root / (label + '-result.json'), result)
        state['results'][model] = {'primary_execution_id': result['execution_id'], 'primary_judge': models[0], 'result': result}
        export_execution(service, project, result['execution_id'], root / (label + '-primary.zip'))
        answer_root = root / 'answers' / label; answer_root.mkdir(parents=True, exist_ok=True)
        for task in result['tasks']:
            if 'response.md' in task['submission']:
                (answer_root / (task['task_id'] + '.md')).write_bytes(service.blobs.get(task['submission']['response.md']['sha256']))
        write_json(root / 'progress.json', state)
        print(json.dumps({'event': 'target_finished', 'model': model, 'summary': result['summary']}, ensure_ascii=False), flush=True)
        return model, result

    await asyncio.gather(*(run_model(model) for model in models))
    for model in models:
        original = state['results'][model]
        original['judge_results'] = {models[0]: original['result']}
        for judge in models[1:]:
            print(json.dumps({'event': 'rejudging', 'target': model, 'judge': judge}), flush=True)
            result = await service.rejudge(project, original['primary_execution_id'], judge_connection=ids[judge])
            original['judge_results'][judge] = result
            label = re.sub(r'[^a-zA-Z0-9._-]', '_', model + '-judge-' + judge)
            write_json(root / (label + '.json'), result)
            export_execution(service, project, result['execution_id'], root / (label + '.zip'))
        write_json(root / 'progress.json', state)
    write_json(root / 'report.json', state)
    print(json.dumps({'event': 'complete', 'report': str(root / 'report.json')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--base-url', default='https://maas.qianwenaiapi.com/compatible-mode/v1')
    parser.add_argument('--models', default='qwen3.8-max-0902,glm-5.3')
    parser.add_argument('--rows', default='3,4')
    parser.add_argument('--output', default='data/fab-qianwen')
    asyncio.run(main(parser.parse_args()))
