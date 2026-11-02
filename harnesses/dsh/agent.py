"""Headless DeepSeek Harness (dsh) agent for liteeval.agent/v1.

Receives the public task on stdin, runs `dsh --profile headless` in the
current workspace and returns the final answer on stdout. Artifacts must be
real files written by the agent into the workspace; the platform collects
them afterwards.

Runs inside the task's Docker sandbox: read-only rootfs, dropped capabilities,
host-mapped uid, tmpfs at /tmp. DSH_HOME/HOME are therefore relocated to /tmp.
The API key arrives via the harness-declared environment passthrough; it is
never written to the workspace or the logs.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

request = json.load(sys.stdin)
assert request['schema_version'] == 1
assert 'ground_truth' not in request and 'judge' not in request
root = Path.cwd()
assert not list(root.rglob('ground_truth*')), 'Grading data leaked to Agent'
assert not list(root.rglob('judge')), 'Judge resources leaked to Agent'

dsh = shutil.which('dsh')
if not dsh:
    print('dsh CLI not found in sandbox image', file=sys.stderr)
    sys.exit(2)
if not os.environ.get('DEEPSEEK_API_KEY'):
    print('DEEPSEEK_API_KEY missing in sandbox environment', file=sys.stderr)
    sys.exit(2)

home = Path('/tmp/dsh-home')
home.mkdir(parents=True, exist_ok=True)
os.environ['HOME'] = str(home)
os.environ['DSH_HOME'] = str(home / 'dsh')
# dsh dlopen()s a native addon from its cache under os.tmpdir(); the sandbox
# tmpfs at /tmp cannot mmap executables, so relocate TMPDIR into the workspace
# bind mount (the only executable-writable location).
tmpdir = root / '.dsh-tmp'
tmpdir.mkdir(exist_ok=True)
os.environ['TMPDIR'] = str(tmpdir)

artifacts = request.get('required_artifacts', [])
lines = ['Workspace files: ' + (', '.join(request.get('input_files', [])) or '(none)'),
         'You must produce these artifacts as real files relative to the current directory:']
for rule in artifacts:
    lines.append('- %s%s' % (rule['path'], ' (required)' if rule.get('required', True) else ' (optional)'))
briefing = request['prompt'] + '\n\n' + '\n'.join(lines) + '\nDo not ask questions; complete the task autonomously.'

command = [dsh, '--profile', 'headless', briefing]
if os.environ.get('DSH_MODEL'):
    command[1:1] = ['--model', os.environ['DSH_MODEL']]

print('running: dsh --profile headless <prompt>', file=sys.stderr)
try:
    proc = subprocess.run(command, capture_output=True, text=True, timeout=3600)
except subprocess.TimeoutExpired:
    print('dsh exceeded harness time budget', file=sys.stderr)
    sys.exit(3)
if proc.stderr:
    print(proc.stderr[-4000:], file=sys.stderr)
if proc.returncode != 0:
    print('dsh exited %d: %s' % (proc.returncode, proc.stdout[-2000:]), file=sys.stderr)
    sys.exit(3)

# The headless profile prints the final assistant message on stdout.
print(json.dumps({'schema_version': 1, 'output': proc.stdout.strip(), 'model': os.environ.get('DSH_MODEL')}))
