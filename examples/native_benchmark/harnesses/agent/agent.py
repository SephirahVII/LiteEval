"""Solve from public inputs; never read a reference answer or a grading program."""
import csv
from decimal import Decimal
import json
from pathlib import Path
import shutil
import sys

request = json.load(sys.stdin)
assert request['schema_version'] == 1
assert 'ground_truth' not in request and 'judge' not in request
root = Path.cwd()
assert not list(root.rglob('ground_truth.json')), 'Grading data leaked to Agent'
rows = list(csv.DictReader((root / 'data/revenue.csv').open(encoding='utf-8')))
first, last = Decimal(rows[0]['revenue']), Decimal(rows[-1]['revenue'])
growth = float((last - first) / first)
output = root / 'outputs'; output.mkdir()
(output / 'result.json').write_text(json.dumps({'growth_rate': growth}), encoding='utf-8')
(output / 'report.md').write_text(f'Growth: {growth}. Source: data/revenue.csv.\n', encoding='utf-8')
shutil.copyfile(root / 'data/source.bin', output / 'source-copy.bin')
print(f'computed growth={growth}', file=sys.stderr)
print(json.dumps({'schema_version': 1, 'output': f'Growth rate: {growth}', 'model': None}))
