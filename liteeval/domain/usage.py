"""Pure provider usage normalization and aggregation."""
from collections import Counter

FIELDS = ('input_tokens', 'output_tokens', 'total_tokens', 'cached_input_tokens', 'cache_write_tokens', 'reasoning_tokens')


PATHS = {
    'input_tokens': ('input_tokens', 'prompt_tokens'),
    'output_tokens': ('output_tokens', 'completion_tokens'),
    'total_tokens': ('total_tokens',),
    'cached_input_tokens': ('cached_input_tokens', 'cache_read_input_tokens', 'cache_read_tokens',
                            'input_tokens_details.cached_tokens', 'prompt_tokens_details.cached_tokens'),
    'cache_write_tokens': ('cache_write_tokens', 'cache_write_input_tokens', 'cache_creation_input_tokens', 'cache_creation_tokens'),
    'reasoning_tokens': ('reasoning_tokens', 'reasoning_output_tokens', 'output_tokens_details.reasoning_tokens', 'completion_tokens_details.reasoning_tokens'),
}


def normalize(raw):
    raw = raw if isinstance(raw, dict) else {}
    result = {};warnings = []
    for field, paths in PATHS.items():
        candidates = []
        for path in paths:
            value = raw
            for key in path.split('.'):value = value.get(key) if isinstance(value, dict) else None
            if value is None:continue
            if type(value) is not int or value < 0:
                warnings.append('invalid:'+path);continue
            candidates.append(value)
        result[field] = candidates[0] if candidates else None
        if len(set(candidates)) > 1:warnings.append('conflicting_aliases:'+field)
    if result['total_tokens'] is None and result['input_tokens'] is not None and result['output_tokens'] is not None:
        result['total_tokens'] = result['input_tokens']+result['output_tokens']
    result['warnings'] = warnings
    known = raw.get('known_turn_samples', {})
    if isinstance(known, dict) and type(raw.get('reported_turn_count')) is int:
        warnings.extend('partial_turn_coverage:'+field for field in FIELDS
                        if result[field] is not None and known.get(field, 0) < raw['reported_turn_count'])
    return result


def sum_turns(turns):
    result = {};known = {field: 0 for field in FIELDS}
    for raw in turns:
        for field, value in normalize(raw).items():
            if field in FIELDS and value is not None:
                result[field] = result.get(field, 0)+value;known[field] += 1
    result['reported_turn_count'] = len(turns)
    result['known_turn_samples'] = known
    return result


def aggregate(records):
    result = {'record_count': len(records), 'totals': {}, 'known_samples': {}, 'cost_by_currency': {}}
    for field in FIELDS:
        values = [r['normalized'][field] for r in records if r['normalized'].get(field) is not None]
        result['totals'][field] = sum(values) if values else None
        result['known_samples'][field] = len(values)
    for record in records:
        if record.get('cost') is not None and record.get('currency'):
            currency = record['currency'];bucket = result['cost_by_currency'].setdefault(currency, {'total': 0, 'known_samples': 0})
            bucket['total'] += record['cost'];bucket['known_samples'] += 1
    result['warnings'] = dict(Counter(w for r in records for w in r['normalized'].get('warnings', [])))
    return result


def summarize(records):
    return {**aggregate(records),
            'by_role': {role: aggregate([r for r in records if r['role'] == role]) for role in ('target', 'judge')},
            'by_model': [{'role': role, 'model': model, **aggregate([r for r in records if r['role'] == role and r['model'] == model])}
                         for role, model in sorted({(r['role'], r['model']) for r in records}, key=lambda pair: (pair[0], pair[1] or ''))],
            'scope': 'all recorded attempts, including retries; legacy Judge usage is not reconstructed',
            'semantics': 'provider-reported counters; cache and reasoning counters are not added to input/output; null means unknown'}
