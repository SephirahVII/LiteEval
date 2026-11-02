"""Compare the submitted summary.json against ground truth with float tolerance."""
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--ground-truth')
parser.add_argument('--submission')
parser.add_argument('--result')
args = parser.parse_args()

result_path = Path(args.result)
result_path.parent.mkdir(parents=True, exist_ok=True)
truth = json.loads(Path(args.ground_truth).read_text(encoding='utf-8'))

TOLERANCE = 0.01
failures = []
target = Path(args.submission) / 'outputs/summary.json'
data = None
if not target.is_file():
    failures.append('outputs/summary.json missing')
else:
    try:
        data = json.loads(target.read_text(encoding='utf-8'))
    except ValueError as exc:
        failures.append('invalid JSON: %s' % exc)

checks = 0
passed = 0
if data is not None:
    def close(key, actual, expected):
        global checks, passed
        checks += 1
        if isinstance(expected, float):
            ok = isinstance(actual, (int, float)) and abs(actual - expected) <= TOLERANCE
        else:
            ok = actual == expected
        if ok:
            passed += 1
        else:
            failures.append('%s: got %r expected %r' % (key, actual, expected))

    close('total_revenue', data.get('total_revenue'), truth['total_revenue'])
    regions = data.get('revenue_by_region')
    if not isinstance(regions, dict) or set(regions) != set(truth['revenue_by_region']):
        checks += 1
        failures.append('revenue_by_region keys: got %r' % (sorted(regions) if isinstance(regions, dict) else regions))
    else:
        for region, expected in truth['revenue_by_region'].items():
            close('revenue_by_region.%s' % region, regions.get(region), expected)
    close('top_product_by_units', data.get('top_product_by_units'), truth['top_product_by_units'])
    close('row_count', data.get('row_count'), truth['row_count'])

denominator = max(checks, 1)
score = passed / denominator
verdict = {'schema_version': 1, 'check_id': 'summary',
           'outcome': 'pass' if not failures else 'fail', 'score': score,
           'reason': 'passed %d/%d values%s' % (passed, denominator, (': ' + '; '.join(failures[:5])) if failures else '')}
result_path.write_text(json.dumps(verdict), encoding='utf-8')
