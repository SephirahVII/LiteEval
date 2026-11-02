import argparse, hashlib, json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--ground-truth');p.add_argument('--submission');p.add_argument('--result');a=p.parse_args()
gt=json.loads(Path(a.ground_truth).read_text(encoding='utf-8'))['value'];sub=Path(a.submission)
try:
    candidate=json.loads((sub/'outputs/result.json').read_text(encoding='utf-8'))
    passed=abs(candidate['growth_rate']-gt['growth_rate'])<1e-9 and hashlib.sha256((sub/'outputs/source-copy.bin').read_bytes()).hexdigest()==gt['source_sha256']
except (ValueError,KeyError,TypeError,OSError): passed=False
result=Path(a.result);result.parent.mkdir(parents=True,exist_ok=True)
result.write_text(json.dumps({'schema_version':1,'check_id':'calculation','outcome':'pass' if passed else 'fail','score':int(passed),'reason':'Numeric result and binary copy checked against ground truth'}),encoding='utf-8')
print('Judge checked numeric and binary artifacts')
