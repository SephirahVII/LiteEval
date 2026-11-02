"""Check the report mentions the key findings."""
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--submission')
parser.add_argument('--result')
args = parser.parse_args()

result_path = Path(args.result)
result_path.parent.mkdir(parents=True, exist_ok=True)

target = Path(args.submission) / 'outputs/report.md'
if not target.is_file():
    verdict = {'schema_version': 1, 'check_id': 'report', 'outcome': 'fail', 'score': 0,
               'reason': 'outputs/report.md missing'}
else:
    text = target.read_text(encoding='utf-8', errors='replace').lower()
    hits = {'total': '185.4' in text, 'region': 'north' in text, 'product': 'beta' in text}
    ok = len(text.strip()) >= 100 and all(hits.values())
    verdict = {'schema_version': 1, 'check_id': 'report',
               'outcome': 'pass' if ok else 'fail',
               'score': sum(hits.values()) / len(hits) if len(text.strip()) >= 100 else 0,
               'reason': 'length=%d hits=%s' % (len(text.strip()), hits)}
result_path.write_text(json.dumps(verdict), encoding='utf-8')
