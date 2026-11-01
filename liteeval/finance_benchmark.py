"""Import the pinned FAB v2 public split without exposing grading data to the Agent."""
import csv
import hashlib
import io
import json
import math
from pathlib import Path
from . import catalog

PROMPT = '''Research the financial question below using primary company disclosures and filings.
Explain the calculation or reasoning and cite plain source URLs and reporting dates. Use a calculator
or code for arithmetic. Do not invent missing figures. If evidence cannot be obtained, state
the limitation. Work only on this question and files in your current workspace. Do not inspect
evaluation datasets, rubrics, expected answers, other workspaces or the parent repository.
Do not search for benchmark answers or search the full question verbatim. Use company-specific
filing searches instead. Do not delegate to other agents. Write the final answer and sources
to response.md in the current workspace and include the complete answer in your final message.

QUESTION:
{question}
'''


def validate_rubric(rubric):
    if not isinstance(rubric, list) or not rubric:raise ValueError('Nonempty rubric required')
    for item in rubric:
        if not isinstance(item, dict) or not isinstance(item.get('criteria'), str) or not item['criteria'].strip():
            raise ValueError('Each rubric item needs criteria')
        modifiers = item.get('modifiers', {})
        if not isinstance(modifiers, dict):raise ValueError('Invalid rubric modifiers')
        weight = modifiers.get('severity', 1)
        if type(weight) not in (int, float) or not math.isfinite(weight) or weight <= 0:raise ValueError('Positive finite severity required')
    return rubric


def load_bundle(directory):
    directory = Path(directory)
    manifest = json.loads((directory/'source.json').read_text(encoding='utf-8'))
    payload = (directory/'public.csv').read_bytes()
    if hashlib.sha256(payload).hexdigest() != manifest['sha256']:raise ValueError('Benchmark source SHA-256 mismatch')
    rows = list(csv.DictReader(io.StringIO(payload.decode('utf-8-sig'))))
    if len(rows) != manifest['question_count']:raise ValueError('Unexpected public question count')
    records = []
    for index, row in enumerate(rows, 1):
        question = row['Question'].strip();rubric = validate_rubric(json.loads(row['Rubric']))
        if not question or not row['Question Type']:raise ValueError('Question and category required')
        records.append({'input': {'question': question, 'prompt': PROMPT.format(question=question)},
                        'expected': {'rubric': rubric},
                        'metadata': {'external_case_id': f'fabv2-public-{index:03d}', 'source_row': index,
                                     'category': row['Question Type'], 'tags': ['finance', 'fab-v2', 'public'],
                                     'critical': any(r.get('modifiers', {}).get('category') == 'must_pass' for r in rubric),
                                     'expert_time_minutes': int(row['Expert time (mins)']),
                                     'question_sha256': hashlib.sha256(question.encode()).hexdigest(),
                                     'benchmark_source': {k: manifest[k] for k in ('benchmark_id', 'repository', 'commit', 'path', 'sha256', 'license')}}})
    return manifest, records


def publish(store, project_id, directory, rows=None):
    manifest, records = load_bundle(directory)
    selected = list(range(1, len(records)+1)) if rows is None else list(rows)
    if not selected or any(type(i) is not int or not 1 <= i <= len(records) for i in selected) or len(set(selected)) != len(selected):
        raise ValueError('Select distinct, valid one-based source rows')
    suffix = 'all' if selected == list(range(1, len(records)+1)) else '-'.join(map(str, selected))
    name = 'FAB v2 Public '+suffix+' @'+manifest['commit'][:12]
    with store.connect() as db:
        existing = db.execute('SELECT id FROM datasets WHERE project_id=? AND name=? AND archived_at IS NULL ORDER BY created_at,id', (project_id, name)).fetchone()
    if existing:
        with store.connect() as db:
            version = db.execute('SELECT id FROM dataset_versions WHERE dataset_id=? AND name=?',
                                 (existing['id'], 'public-'+manifest['commit'][:12])).fetchone()
        if version:
            frozen = catalog.version_cases(store, version['id'])['items']
            actual = {r['metadata']['external_case_id']: {k:r[k] for k in ('input','expected','metadata')} for r in frozen}
            expected = {records[i-1]['metadata']['external_case_id']: records[i-1] for i in selected}
            if actual != expected:raise ValueError('Existing release does not match the pinned benchmark bundle')
            return {'dataset_id': existing['id'], 'version': catalog.get_version(store,version['id']), 'source': manifest, 'rows': selected, 'reused': True}
        raise ValueError('An incomplete import already exists; use a new project or inspect the Dataset')
    dataset = store.create_dataset(project_id, name)
    store.import_cases(dataset['id'], [records[i-1] for i in selected])
    version = catalog.publish_dataset(store, dataset['id'], 'public-'+manifest['commit'][:12],
                                      'Public FAB v2; upstream commit '+manifest['commit']+'; rows '+suffix)
    return {'dataset_id': dataset['id'], 'version': version, 'source': manifest, 'rows': selected, 'reused': False}


def grade_checks(rubric, verdicts):
    """Deterministic gating after local per-check judgments, not the official jury."""
    validate_rubric(rubric)
    if not isinstance(verdicts, list) or len(verdicts) != len(rubric):raise ValueError('Every rubric check must be judged')
    by_index = {}
    for item in verdicts:
        if not isinstance(item, dict) or type(item.get('index')) is not int or not 0 <= item['index'] < len(rubric):raise ValueError('Invalid check index')
        if item['index'] in by_index or type(item.get('passed')) is not bool or not isinstance(item.get('reason'), str):raise ValueError('Invalid or duplicate check verdict')
        by_index[item['index']] = item
    total = sum(r.get('modifiers', {}).get('severity', 1) for r in rubric)
    earned = sum(r.get('modifiers', {}).get('severity', 1) for i, r in enumerate(rubric) if by_index[i]['passed'])
    gate = all(by_index[i]['passed'] for i, r in enumerate(rubric) if r.get('modifiers', {}).get('category') == 'must_pass')
    return {'partial_credit': earned/total if gate else 0.0, 'all_pass': all(v['passed'] for v in verdicts),
            'dealbreakers_passed': gate, 'weighted_fraction_before_gate': earned/total,
            'checks': [by_index[i] for i in range(len(rubric))], 'grading_method': 'local_single_model_diagnostic'}
