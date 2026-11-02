"""Headless Claude Code agent for liteeval.agent/v1.

Receives the public task on stdin, runs `claude -p` in the current workspace
and returns the final text on stdout. Artifacts must be real files written by
Claude into the workspace; the platform collects them afterwards.

Runs inside the task's Docker sandbox: read-only rootfs, dropped capabilities,
host-mapped uid, tmpfs at /tmp. HOME is therefore relocated to /tmp. The API
key arrives via the harness-declared environment passthrough; it is never
written to the workspace or the logs.
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

claude = shutil.which('claude')
if not claude:
    print('claude CLI not found in sandbox image', file=sys.stderr)
    sys.exit(2)
if not os.environ.get('ANTHROPIC_API_KEY'):
    print('ANTHROPIC_API_KEY missing in sandbox environment', file=sys.stderr)
    sys.exit(2)

home = Path('/tmp/claude-home')
home.mkdir(parents=True, exist_ok=True)
os.environ['HOME'] = str(home)
os.environ.setdefault('DISABLE_AUTOUPDATER', '1')
os.environ.setdefault('DISABLE_TELEMETRY', '1')
os.environ.setdefault('DISABLE_ERROR_REPORTING', '1')
os.environ.setdefault('DISABLE_NON_ESSENTIAL_MODEL_CALLS', '1')

artifacts = request.get('required_artifacts', [])
lines = ['Workspace files: ' + (', '.join(request.get('input_files', [])) or '(none)'),
         'You must produce these artifacts as real files relative to the current directory:']
for rule in artifacts:
    lines.append('- %s%s' % (rule['path'], ' (required)' if rule.get('required', True) else ' (optional)'))
briefing = request['prompt'] + '\n\n' + '\n'.join(lines) + '\nDo not ask questions; complete the task autonomously.'

command = [claude, '-p', briefing, '--output-format', 'json',
           '--permission-mode', 'bypassPermissions',
           '--max-turns', os.environ.get('CLAUDE_CODE_MAX_TURNS', '40')]
if os.environ.get('ANTHROPIC_MODEL'):
    command += ['--model', os.environ['ANTHROPIC_MODEL']]

print('running: claude -p <prompt> --output-format json', file=sys.stderr)
try:
    proc = subprocess.run(command, capture_output=True, text=True, timeout=3600)
except subprocess.TimeoutExpired:
    print('claude exceeded harness time budget', file=sys.stderr)
    sys.exit(3)
if proc.stderr:
    print(proc.stderr[-4000:], file=sys.stderr)
try:
    result = json.loads(proc.stdout)
except (json.JSONDecodeError, ValueError):
    print('claude did not return JSON (exit %d): %s' % (proc.returncode, proc.stdout[-2000:]), file=sys.stderr)
    sys.exit(3)
if proc.returncode != 0 or result.get('is_error'):
    print('claude reported an error: %s' % result.get('result', proc.returncode), file=sys.stderr)
    sys.exit(3)

usage = result.get('usage') or {}
print(json.dumps({
    'schema_version': 1,
    'output': result.get('result', ''),
    'model': result.get('modelUsage') and list(result['modelUsage']) or None,
    'num_turns': result.get('num_turns'),
    'duration_ms': result.get('duration_ms'),
    'usage': {'input_tokens': usage.get('input_tokens'), 'output_tokens': usage.get('output_tokens')},
}))
