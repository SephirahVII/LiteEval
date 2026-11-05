"""Check the submission against the frozen scenario and platform transcript."""

import argparse
import json
from pathlib import Path


parser = argparse.ArgumentParser()
parser.add_argument('--result', required=True)
args = parser.parse_args()

expected = json.loads(Path('judge/ground_truth.json').read_text(encoding='utf-8'))
actual = json.loads(Path('submission/result.json').read_text(encoding='utf-8'))
records = [json.loads(line) for line in Path('evidence/trajectory.jsonl').read_text(encoding='utf-8').splitlines()]
milestones = [(record['kind'], record['payload']['turn_index']) for record in records
              if record['kind'] in ('dialogue_turn_started', 'dialogue_turn_completed')]
expected_milestones = [(kind, turn) for turn in range(1, 4)
                       for kind in ('dialogue_turn_started', 'dialogue_turn_completed')]
sessions = [record['payload']['session_id'] for record in records if record['kind'] == 'dialogue_session']
passed = actual == expected and milestones == expected_milestones and len(sessions) == 3 and len(set(sessions)) == 1
reason = 'Three ordered turns in one session and the expected final submission' if passed else 'Submission or session continuity did not match'
Path(args.result).parent.mkdir(parents=True, exist_ok=True)
Path(args.result).write_text(json.dumps({'schema_version': 1, 'check_id': 'dialogue-continuity',
                                        'outcome': 'pass' if passed else 'fail', 'score': int(passed), 'reason': reason}),
                             encoding='utf-8')
