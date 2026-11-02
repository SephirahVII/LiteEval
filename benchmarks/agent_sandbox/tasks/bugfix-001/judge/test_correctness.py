"""Import the submitted module and run hidden numeric cases."""
import argparse
import importlib.util
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
cases = truth['cases']

failures = []
target = Path(args.submission) / 'outputs/solution.py'
if not target.is_file():
    failures.append('outputs/solution.py missing')
    module = None
else:
    spec = importlib.util.spec_from_file_location('candidate_solution', target)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        failures.append('import failed: %s' % exc)
        module = None

passed = 0
if module is not None:
    for case in cases:
        fn = getattr(module, case['function'], None)
        if not callable(fn):
            failures.append('missing function %s' % case['function'])
            continue
        try:
            got = fn(*case['args'])
        except Exception as exc:
            failures.append('%s%r raised %s' % (case['function'], tuple(case['args']), exc))
            continue
        if not isinstance(got, (int, float)) or abs(got - case['expected']) > 1e-9:
            failures.append('%s%r -> %r, expected %r' % (case['function'], tuple(case['args']), got, case['expected']))
            continue
        passed += 1

score = passed / len(cases)
verdict = {'schema_version': 1, 'check_id': 'correctness',
           'outcome': 'pass' if not failures else 'fail', 'score': score,
           'reason': 'passed %d/%d cases%s' % (passed, len(cases), (': ' + '; '.join(failures[:5])) if failures else '')}
result_path.write_text(json.dumps(verdict), encoding='utf-8')
