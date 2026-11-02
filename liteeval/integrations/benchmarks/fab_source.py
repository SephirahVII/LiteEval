"""Pinned FAB public source shared by direct runs and native package conversion."""
import csv
import hashlib
import io
from pathlib import Path
from liteeval.domain.values import decoded
from liteeval.domain.rubrics import validate_rubric


def load_source(directory):
    directory = Path(directory)
    provenance = decoded((directory / 'source.json').read_bytes())
    payload = (directory / 'public.csv').read_bytes()
    if hashlib.sha256(payload).hexdigest() != provenance['sha256']:
        raise ValueError('FAB public.csv SHA-256 mismatch')
    rows = list(csv.DictReader(io.StringIO(payload.decode('utf-8-sig'))))
    if len(rows) != provenance['question_count']:
        raise ValueError('FAB question count mismatch')
    return provenance, rows


def select_rows(rows, selected=None):
    selected = list(range(1, len(rows) + 1)) if selected is None else list(selected)
    if not selected or any(type(n) is not int or not 1 <= n <= len(rows) for n in selected) or len(selected) != len(set(selected)):
        raise ValueError('Choose distinct, valid one-based FAB rows')
    return sorted(selected)


def rubric_for(row, number, provenance):
    if not row['Question'].strip(): raise ValueError('Empty FAB question')
    upstream = decoded(row['Rubric'])
    if not isinstance(upstream, list) or not upstream: raise ValueError('Empty FAB rubric')
    criteria = []
    for index, item in enumerate(upstream, 1):
        if item.get('operator') != 'finance_agent_v2_operator': raise ValueError('Unknown FAB operator')
        modifiers = item.get('modifiers', {})
        if set(modifiers) - {'severity', 'category'} or modifiers.get('category') not in (None, 'must_pass'):
            raise ValueError('Unknown FAB rubric modifier')
        criteria.append({'id': f'criterion-{index:03d}', 'description': item['criteria'],
                         'weight': modifiers.get('severity', 1), 'must_pass': modifiers.get('category') == 'must_pass',
                         'source': item})
    return validate_rubric({'schema_version': 1, 'criteria': criteria,
                            'metadata': {'source_row': number, 'benchmark_source': provenance}})


def research_prompt(question):
    return ('Research the question below using primary company disclosures and filings. '
            'Preserve the dates, financial conventions and numeric tolerances in the question. '
            'If a date is ambiguous, state the interpretation; do not silently substitute the latest fiscal year. '
            'Use code or a calculator for arithmetic. Cite source URLs and reporting dates. '
            'Write the complete answer to response.md in your workspace. '
            'Optionally save source URLs in sources.json and calculations in calculations.json. '
            'Do not inspect benchmark answers, private grading files, other workspaces or the parent repository. '
            'Do not search the entire benchmark question verbatim. State unavailable evidence instead of inventing it.\n\n'
            'QUESTION:\n' + question + '\n')
