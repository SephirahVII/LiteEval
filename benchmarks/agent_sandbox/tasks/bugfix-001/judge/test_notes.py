"""Check the fix notes are substantive."""
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--submission')
parser.add_argument('--result')
args = parser.parse_args()

result_path = Path(args.result)
result_path.parent.mkdir(parents=True, exist_ok=True)

target = Path(args.submission) / 'outputs/NOTES.md'
keywords = ('mean', 'median', 'variance', 'even', 'length', 'division', 'average')
if not target.is_file():
    ok, reason, score = False, 'outputs/NOTES.md missing', 0
else:
    text = target.read_text(encoding='utf-8', errors='replace')
    hits = [k for k in keywords if k in text.lower()]
    ok = len(text.strip()) >= 80 and len(hits) >= 2
    score = min(1.0, len(text.strip()) / 200) * min(1.0, len(hits) / 3)
    reason = 'length=%d keyword_hits=%s' % (len(text.strip()), hits)

verdict = {'schema_version': 1, 'check_id': 'notes',
           'outcome': 'pass' if ok else 'fail', 'score': score if ok else 0, 'reason': reason}
result_path.write_text(json.dumps(verdict), encoding='utf-8')
