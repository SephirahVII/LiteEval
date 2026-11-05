"""Run the pinned public CSV directly, without platform DB, Release or TaskSpec."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import time
from liteeval.domain.rubrics import judge_messages, grade_response
from liteeval.domain.usage import normalize
from liteeval.domain.values import encoded
from liteeval.infrastructure.security.secrets import redact, safe_error
from liteeval.integrations.benchmarks.fab_source import load_source, select_rows, rubric_for, research_prompt
from harnesses.responses_research.research import ResponsesResearch
from harnesses.responses_research.client import ResponsesClient
from liteeval.integrations.models.openai import OpenAICompatibleProvider

SOURCE = Path(__file__).resolve().parent
ENDPOINT = 'https://maas.qianwenaiapi.com/compatible-mode/v1'


def save(path, value):
    path.write_text(json.dumps(redact(value), ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')


async def run_case(row, number, provenance, target, judges, directory, timeout=7200):
    """Only Target receives the public prompt; only Judges receive the rubric."""
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=False)
    events = directory / 'events.jsonl'
    def emit(kind, payload):
        with events.open('a', encoding='utf-8') as output:
            output.write(encoded(redact({'kind': kind, 'payload': payload})) + '\n')

    class Submission:
        def write(self, name, body):
            if name not in ('response.md', 'sources.json'): raise ValueError('Unsupported submission path')
            (directory / name).write_bytes(body)

    calls, usage = [], []
    async def respond(body):
        record = {'role': 'target', 'model': target.config['model'], 'status': 'started', 'normalized': normalize({})}
        usage.append(record)
        try:
            raw = await target.respond(body, on_event=lambda event: emit('provider_event', event))
            path = directory / f'target-{len(calls)+1}.json'
            save(path, {'request': body, 'response': raw}); calls.append(path.name)
            record.update(status='succeeded', model=raw.get('model'), raw=raw.get('usage', {}), normalized=normalize(raw.get('usage', {})))
            return raw
        except BaseException:
            record['status'] = 'error'; raise

    result = {'task_id': f'fabv2-public-{number:03d}', 'execution_status': 'started', 'judges': [], 'usage': usage}
    started = time.monotonic(); prompt = research_prompt(row['Question'])
    try:
        await asyncio.wait_for(ResponsesResearch().run({'prompt': prompt}, {
            'runtime': Submission(), 'respond': respond, 'model': target.config['model'], 'emit': emit}), timeout)
        result['execution_status'] = 'succeeded'
        result['target_seconds'] = time.monotonic() - started
        actual = (directory / 'response.md').read_bytes()
        result['submission'] = {'response.md': {'sha256': hashlib.sha256(actual).hexdigest(), 'size_bytes': len(actual)}}
        rubric = rubric_for(row, number, provenance)
        for index, judge in enumerate(judges, 1):
            record = {'model': judge.config['model'], 'score': None}
            result['judges'].append(record)
            use = {'role': 'judge', 'model': judge.config['model'], 'status': 'started', 'normalized': normalize({})}
            usage.append(use)
            try:
                messages = judge_messages('fab-v2-rubric', prompt, rubric, {'response.md': actual.decode('utf-8')})
                text, raw = await asyncio.wait_for(judge.generate(messages), 180)
                save(directory / f'judge-{index}.json', {'request': {'messages': messages}, 'response': raw})
                use.update(status='succeeded', model=raw.get('model'), raw=raw.get('usage', {}), normalized=normalize(raw.get('usage', {})))
                record.update(grade_response('fab-v2-rubric', rubric, text))
            except Exception as exc:
                if use['status'] == 'started': use['status'] = 'error'
                record.update(outcome='unscored', error=safe_error(exc))
    except Exception as exc:
        result.update(execution_status='timed_out' if isinstance(exc, TimeoutError) else 'failed', error=safe_error(exc))
    result['target_seconds'] = result.get('target_seconds', time.monotonic() - started)
    result['wall_seconds'] = time.monotonic() - started
    result['target_responses'] = calls
    save(directory / 'result.json', result)
    return result


async def main(args):
    provenance, rows = load_source(SOURCE)
    selected = select_rows(rows, [int(n) for n in args.rows.split(',')])
    for number in selected: rubric_for(rows[number-1], number, provenance)
    catalog = {'mode': 'direct_csv', 'source': provenance, 'selected_rows': selected,
               'model_calls': 0, 'official_leaderboard_comparable': False}
    if not args.run:
        print(json.dumps(catalog, ensure_ascii=False, indent=2)); return
    if not os.getenv('QIANWEN_API_KEY'): raise ValueError('Set QIANWEN_API_KEY in the executing process')
    output = Path(args.output).resolve()
    if output.exists(): raise ValueError('Use a new output directory; previous direct runs are not overwritten')
    output.mkdir(parents=True)
    models = args.models.split(','); judge_models = args.judges.split(',')
    def provider(model, judge=False):
        return OpenAICompatibleProvider({'base_url': ENDPOINT, 'model': model, 'api_key_env': 'QIANWEN_API_KEY',
            'timeout_seconds': 180, 'generation_parameters': {'max_tokens': 12000, 'enable_thinking': True} if judge else {}})
    report = {**catalog, 'codex_login_used': False, 'model_calls': None, 'results': []}
    for model in models:
        for number in selected:
            target = ResponsesClient(ENDPOINT, model, os.environ['QIANWEN_API_KEY'])
            result = await run_case(rows[number-1], number, provenance, target, [provider(m, judge=True) for m in judge_models],
                                    output / model.replace('/', '_') / f'fabv2-public-{number:03d}')
            report['results'].append({'target': model, **result}); save(output / 'report.json', report)
    report['model_calls'] = sum(len(r['usage']) for r in report['results'])
    save(output / 'report.json', report)
    print(json.dumps({'report': str(output / 'report.json'), 'finished': len(report['results'])}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--rows', default='3,4')
    parser.add_argument('--models', default='qwen3.8-max-0902,glm-5.3')
    parser.add_argument('--judges', default='qwen3.8-max-0902,glm-5.3')
    parser.add_argument('--output', default='data/fab-v2-direct')
    asyncio.run(main(parser.parse_args()))
