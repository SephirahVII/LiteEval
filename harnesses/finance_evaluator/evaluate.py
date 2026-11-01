"""Example custom evaluation runner. Native command checks are not invoked."""
import hashlib
import json
from pathlib import Path
import sys

request = json.load(sys.stdin)
reference = json.loads(Path(request['ground_truth_file']).read_text(encoding='utf-8'))['value']
submission = Path('submission')
candidate = json.loads((submission / 'outputs/result.json').read_text(encoding='utf-8'))
passed = abs(candidate['growth_rate'] - reference['growth_rate']) < 1e-9
passed = passed and hashlib.sha256((submission / 'outputs/source-copy.bin').read_bytes()).hexdigest() == reference['source_sha256']
checks = [{'check_id': c['id'], 'outcome': 'pass' if passed else 'fail', 'score': int(passed),
           'reason': 'Independent custom Evaluation Harness verified numeric and binary outputs'} for c in request['checks']]
print(json.dumps({'schema_version': 1, 'checks': checks}))
