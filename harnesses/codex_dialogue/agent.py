"""Translate one Codex CLI turn, using explicit provider credentials and thread resume."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urlsplit


TOOL_ITEMS = {'command_execution', 'file_change', 'mcp_tool_call', 'web_search', 'web_search_call'}


def emit(kind, **payload):
    print(json.dumps({'schema_version': 1, 'type': kind, **payload}, ensure_ascii=False), flush=True)


def read_events(stream):
    thread_id = terminal = None
    output = ''
    for line in stream:
        if not line.strip():
            continue
        event = json.loads(line)
        kind = event.get('type')
        if kind == 'thread.started':
            if thread_id is not None:
                raise ValueError('Multiple Codex threads in one turn')
            thread_id = event['thread_id']
            emit('session', session_id=thread_id)
        elif kind in ('turn.completed', 'turn.failed'):
            if terminal is not None:
                raise ValueError('Multiple Codex results in one turn')
            terminal = event
            if kind == 'turn.failed':
                print(json.dumps(event.get('error', {}), ensure_ascii=False), file=sys.stderr, flush=True)
        elif kind in ('item.started', 'item.completed'):
            item = event['item']
            item_type = item['type']
            if item_type == 'agent_message' and kind == 'item.completed':
                output = item['text']
                emit('assistant', content=output)
            elif item_type in TOOL_ITEMS:
                emit('tool_call' if kind == 'item.started' else 'tool_result',
                     content={'name': item.get('tool') or item_type, **item})
        elif kind == 'error':
            print(event.get('message', 'Codex reported an error'), file=sys.stderr, flush=True)
    return thread_id, terminal, output


def run(request):
    codex = os.getenv('LITEEVAL_CODEX_EXECUTABLE') or shutil.which('codex')
    model = os.environ['LITEEVAL_CODEX_MODEL']
    endpoint = os.environ['LITEEVAL_CODEX_BASE_URL']
    url = urlsplit(endpoint)
    if url.scheme != 'https' or url.hostname not in {'api.minimax.cn', 'api.minimax.io'} or url.path.rstrip('/') != '/v1' or url.netloc != url.hostname or url.query or url.fragment:
        raise ValueError('Use a MiniMax official HTTPS /v1 endpoint')
    if not codex or not os.environ.get('MINIMAX_API_KEY'):
        raise RuntimeError('Codex CLI and MINIMAX_API_KEY are required')

    rollout = hashlib.sha256(request['rollout_id'].encode()).hexdigest()
    state_root = Path('/tmp') if Path.cwd() == Path('/workspace') else Path.cwd() / '.harness'
    state = state_root / ('codex-' + rollout)
    state.mkdir(parents=True, exist_ok=True)
    config = state / 'config.toml'
    allowed = {item['path']: int(item.get('max_bytes', 1_000_000)) for item in request['required_artifacts']}
    submission_command = ['-I', '-X', 'utf8', str(Path(__file__).with_name('submission.py').resolve()), json.dumps(allowed)]
    config.write_text(f'''model = {json.dumps(model)}
model_provider = "liteeval_minimax"
model_context_window = 524288
model_reasoning_effort = "low"
approval_policy = "never"
allow_login_shell = false
sandbox_mode = "workspace-write"
web_search = "disabled"

[shell_environment_policy]
exclude = ["MINIMAX_API_KEY"]

[model_providers.liteeval_minimax]
name = "MiniMax"
base_url = {json.dumps(endpoint.rstrip('/'))}
env_key = "MINIMAX_API_KEY"
wire_api = "responses"
request_max_retries = 0
stream_max_retries = 0

[windows]
sandbox = "unelevated"

[mcp_servers.liteeval_submission]
command = {json.dumps(sys.executable)}
args = {json.dumps(submission_command)}
required = true

[mcp_servers.liteeval_submission.tools.write_submission]
approval_mode = "approve"
''', encoding='utf-8')
    child_env = dict(os.environ, CODEX_HOME=str(state))
    command = [codex, 'exec']
    if request['session_id'] is not None:
        command += ['resume']
    command += ['--json', '--skip-git-repo-check', '--ignore-rules']
    if request['session_id'] is not None:
        command += [request['session_id']]
    command += ['-']
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, encoding='utf-8', env=child_env) as proc:
        try:
            proc.stdin.write(request['instruction'])
            proc.stdin.close()
            thread_id, terminal, output = read_events(proc.stdout)
        except BaseException:
            if proc.poll() is None:
                proc.kill()
            raise
        exit_code = proc.wait()
    if not thread_id or terminal is None:
        raise ValueError('Codex did not return a thread ID and terminal event')
    emit('result', session_id=thread_id, output=output, usage=terminal.get('usage') or {}, usage_scope='thread', requested_model=model,
         success=exit_code == 0 and terminal['type'] == 'turn.completed')


if __name__ == '__main__':
    try:
        run(json.load(sys.stdin))
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        print(f'Codex dialogue adapter failed ({type(exc).__name__}); inspect preceding logs', file=sys.stderr)
        sys.exit(1)
