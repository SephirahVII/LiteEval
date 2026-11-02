"""Render real trial evidence without changing answers, verdicts or original runs."""
from collections import Counter
import argparse
import json
import hashlib
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from liteeval.bootstrap import benchmark_service
from liteeval.infrastructure.persistence.store import Store
from liteeval.domain.rubrics import grade_rubric, parse_judge_json
from liteeval.domain.values import decoded


def offline_recovery(service, execution, task, task_spec):
    """Recompute from one identifiable, complete recorded Judge response; no API I/O."""
    resources = task_spec['judge']['resources']
    checks = task_spec['judge']['checks']
    if len(checks) != 1 or checks[0]['type'] != 'llm_rubric':
        raise ValueError('Offline recovery supports a single llm_rubric check')
    check = checks[0]
    rubric_sha = resources[check['rubric_file']]
    rubric = decoded(service.blobs.get(rubric_sha))
    actual = {name: service.blobs.get(task['submission'][name]['sha256']).decode('utf-8')
              for name in check.get('submission_paths', task['submission'])}
    matches = []
    for event in execution['events']:
        if event['kind'] != 'model_response_raw' or event['payload']['role'] != 'judge': continue
        sha = event['payload']['blob_sha256']
        recorded = decoded(service.blobs.get(sha))
        supplied = decoded(recorded['request']['messages'][1]['content'])
        if supplied['submission'] != actual or supplied['prompt'] != task_spec['prompt']: continue
        if supplied['rubric'] != rubric or supplied['check_id'] != check['id']:
            raise ValueError('Recorded Judge resource identity mismatch')
        raw = recorded['response']
        if raw['choices'][0].get('finish_reason') != 'stop':
            raise ValueError('Cannot recover an incomplete Judge response')
        verdict = parse_judge_json(raw['choices'][0]['message']['content'])
        if verdict.get('schema_version') != 1 or verdict.get('check_id') != check['id']:
            raise ValueError('Recorded Judge verdict identity mismatch')
        matches.append({'source_execution_id': execution['id'], 'source_blob_sha256': sha,
                        'rubric_sha256': rubric_sha, 'provider_model': raw.get('model'),
                        'original_grading': task['grading'], 'original_checks': task['checks'],
                        'parser': 'single-json-object-or-markdown-fence/v1',
                        'recovery_build_digest': service.registry.build_digest,
                        'new_model_calls': 0, 'grading': grade_rubric(rubric, verdict.get('verdicts'))})
    if len(matches) != 1: raise ValueError('Expected exactly one matching recorded Judge response')
    return matches[0]


def main(root=None):
    root = Path(root or ROOT / 'data/fab-qianwen').resolve()
    report = json.loads((root / 'report.json').read_text(encoding='utf-8'))
    service = benchmark_service(Store(root / 'liteeval.db'))
    models = report['models']; project = report['project_id']
    lines = ['# FAB v2 public: real Qianwen API trial', '',
             'Authentication: user-provided API Key. Codex login used: false.',
             'Research: hosted web_search/web_extractor/code_interpreter; local submit_answer function writes actual artifacts.',
             'Scores: two-model diagnostic grading on public cases, not an official FAB leaderboard or three-judge jury.', '',
             '| Target | Case | Qwen Judge | GLM Judge | Diagnostic mean | Target seconds |',
             '| --- | --- | ---: | ---: | ---: | ---: |']
    evidence = []
    derived_files = []
    recoveries = []
    for model in models:
        entry = report['results'][model]
        execution = service.repository.execution(project, entry['primary_execution_id'])
        for task in entry['result']['tasks']:
            scores = []; original_scores = []; recovered = {}
            spec = next(t for t in execution['plan']['tasks'] if t['id'] == task['task_id'])
            for judge in models:
                judged = next((t for t in entry['judge_results'][judge]['tasks'] if t['task_id'] == task['task_id']), None)
                score = judged['grading']['score'] if judged else None
                original_scores.append(score)
                if judged and score is None:
                    judged_execution = service.repository.execution(project, entry['judge_results'][judge]['execution_id'])
                    recovery = offline_recovery(service, judged_execution, judged, spec)
                    recovery.update(target=model, task_id=task['task_id'], judge=judge)
                    recovered[judge] = recovery; recoveries.append(recovery)
                    score = recovery['grading']['score']
                scores.append(score)
            mean = sum(scores) / len(scores) if all(s is not None for s in scores) else None
            fmt = lambda score: 'unscored' if score is None else f'{score:.4f}'
            shown = [fmt(score) + ('*' if judge in recovered else '') for judge, score in zip(models, scores)]
            lines.append(f"| {model} | {task['task_id']} | {shown[0]} | {shown[1]} | {fmt(mean)} | {task['phases'].get('target_ms', 0)/1000:.2f} |")
            events = [e for e in execution['events'] if e['payload'].get('task_id') == task['task_id']]
            tools = [e for e in events if e['kind'] == 'tool']
            counts = Counter(e['payload']['name'] for e in tools)
            usage = task['usage_records']
            tool_counters = Counter()
            target_usage = Counter()
            for u in usage:
                if u['role'] != 'target': continue
                for name, count in u.get('normalized', {}).items():
                    if type(count) is int: target_usage[name] += count
                for name, counter in u.get('usage', {}).get('x_tools', {}).items():
                    tool_counters[name] += counter.get('count', 0)
            for meta in task['submission'].values():
                body = service.blobs.get(meta['sha256'])
                if len(body) != meta['size_bytes'] or hashlib.sha256(body).hexdigest() != meta['sha256']:
                    raise ValueError('Submission integrity mismatch')
            proof = {'target': model, 'task_id': task['task_id'], 'execution_status': task['execution_status'],
                     'collection_status': task['collection_status'], 'tool_event_counts': dict(counts),
                     'provider_tool_counters': dict(tool_counters), 'submission': task['submission'], 'phases': task['phases'],
                     'target_usage': dict(target_usage), 'original_judge_scores': dict(zip(models, original_scores)),
                     'judge_scores': dict(zip(models, scores)), 'offline_recovery': recovered,
                     'diagnostic_mean': mean, 'error': task.get('error')}
            evidence.append(proof)
            trace = [f"# {model}: {task['task_id']}", '', json.dumps(proof, ensure_ascii=False, indent=2), '', '## Ordered activity', '']
            for event in events:
                if event['kind'] not in ('tool', 'model_request', 'usage', 'submission_collected', 'judge_completed', 'task_completed'): continue
                trace.extend([f"### {event['id']} {event['created_at']} {event['kind']}", '', '```json',
                              json.dumps(event['payload'], ensure_ascii=False, indent=2), '```', ''])
            for judge in models:
                judged = next(t for t in entry['judge_results'][judge]['tasks'] if t['task_id'] == task['task_id'])
                trace.extend([f'## Judge: {judge}', '', '```json', json.dumps(judged['checks'], ensure_ascii=False, indent=2), '```', ''])
                if judge in recovered:
                    trace.extend(['### Offline parse recovery (original failure retained)', '', '```json',
                                  json.dumps(recovered[judge], ensure_ascii=False, indent=2), '```', ''])
            label = model.replace('/', '_') + '-' + task['task_id']
            path = root / (label + '-trajectory.md')
            path.write_text('\n'.join(trace), encoding='utf-8', newline='\n'); derived_files.append(path)
    lines.extend(['', '* GLM originally returned valid verdicts inside a Markdown JSON fence; the first parser marked them unscored.',
                  'The starred scores are offline recomputation from those same recorded responses after the parser fix. No new model calls; original records remain unchanged.',
                  'See offline-recovery.json for raw response hashes, original errors, parser/build identity and criterion-level verdicts.', '',
                  '| Target | Case | Input tokens | Cached input | Output | Reasoning | Search / fetch / code |',
                  '| --- | --- | ---: | ---: | ---: | ---: | --- |'])
    for proof in evidence:
        u = proof['target_usage']; c = proof['provider_tool_counters']
        lines.append(f"| {proof['target']} | {proof['task_id']} | {u.get('input_tokens')} | {u.get('cached_input_tokens')} | {u.get('output_tokens')} | {u.get('reasoning_tokens')} | {c.get('web_search',0)} / {c.get('web_extractor',0)} / {c.get('code_interpreter',0)} |")
    lines.extend(['', 'Token counters sum provider-reported research steps and continuations, not unique context length.',
                  'Cached input is a subset of input, reasoning a subset of output. This table excludes Judges and readiness probes; their actual usage is in the raw records.',
                  'Tool event counts describe provider-returned events; underlying queries may differ.',
                  'Input/output/cache/reasoning counters are provider-reported. Costs and cloud CPU/memory are unknown unless reported.',
                  'Every failure/unscored item remains in the report. No answer or rubric was edited to improve a score.', ''])
    (root / 'report.md').write_text('\n'.join(lines), encoding='utf-8', newline='\n')
    (root / 'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    (root / 'offline-recovery.json').write_text(json.dumps(recoveries, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    paths = [root / name for name in ('report.md', 'report.json', 'evidence.json', 'offline-recovery.json')]
    paths += derived_files + sorted((root / 'answers').rglob('*.md'))
    paths += [root / (model + '-primary.zip') for model in models]
    paths += [root / (model + '-judge-' + judge + '.zip') for model in models for judge in models[1:]]
    manifest = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    (root / 'integrity.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8', newline='\n')
    with zipfile.ZipFile(root / 'fab-qianwen-evidence.zip', 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for p in paths + [root / 'integrity.json']: archive.write(p, p.relative_to(root).as_posix())
    with zipfile.ZipFile(root / 'fab-qianwen-evidence.zip') as archive:
        if archive.testzip() is not None: raise ValueError('Evidence archive integrity failure')
    print(json.dumps({'markdown': str(root / 'report.md'), 'evidence': str(root / 'evidence.json')}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', default=str(ROOT / 'data/fab-qianwen'))
    main(parser.parse_args().input)
