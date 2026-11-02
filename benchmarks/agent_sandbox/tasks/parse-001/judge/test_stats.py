"""Compare the submitted stats.json against ground truth."""
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

failures = []
target = Path(args.submission) / 'outputs/stats.json'
data = None
if not target.is_file():
    failures.append('outputs/stats.json missing')
else:
    try:
        data = json.loads(target.read_text(encoding='utf-8'))
    except ValueError as exc:
        failures.append('invalid JSON: %s' % exc)

checks = 0
passed = 0
if data is not None:
    counts = data.get('counts')
    for level, expected in truth['counts'].items():
        checks += 1
        if isinstance(counts, dict) and counts.get(level) == expected:
            passed += 1
        else:
            failures.append('counts.%s: got %r expected %r' % (level, counts.get(level) if isinstance(counts, dict) else counts, expected))
    for key in ('first_timestamp', 'last_timestamp', 'most_frequent_error'):
        checks += 1
        if data.get(key) == truth[key]:
            passed += 1
        else:
            failures.append('%s: got %r expected %r' % (key, data.get(key), truth[key]))

denominator = max(checks, 1)
score = passed / denominator
verdict = {'schema_version': 1, 'check_id': 'stats',
           'outcome': 'pass' if not failures else 'fail', 'score': score,
           'reason': 'passed %d/%d values%s' % (passed, denominator, (': ' + '; '.join(failures[:5])) if failures else '')}
result_path.write_text(json.dumps(verdict), encoding='utf-8')
