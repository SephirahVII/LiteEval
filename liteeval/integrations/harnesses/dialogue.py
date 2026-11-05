"""Command adapter for one turn of a resumable Agent conversation."""

import hashlib
from liteeval.domain.values import decoded, encoded
from liteeval.infrastructure.execution.environments import snapshot_bytes


class CommandDialogueHarness:
    def prepare(self, runtime, spec):
        runtime.materialize(spec['resources'], '.harness/')
        return runtime.argv(spec['command'], {'harness': '.harness'})

    async def send_turn(self, runtime, command, spec, request, timeout, emit, *, connection_env=None):
        for name, sha in spec['resources'].items():
            if hashlib.sha256(snapshot_bytes(runtime.root, '.harness/' + name, 4_000_000)).hexdigest() != sha:
                raise ValueError('Harness resource changed between dialogue turns')
        session_id = None
        result = None

        def receive(stream, chunk):
            nonlocal session_id, result
            if stream == 'stderr':
                emit('log', {'stream': stream, 'text': chunk})
                return
            for line in chunk.splitlines():
                if not line.strip():
                    continue
                if len(line.encode('utf-8')) > 256_000 or result is not None:
                    raise ValueError('Invalid dialogue output boundary')
                event = decoded(line)
                if not isinstance(event, dict) or event.get('schema_version') != 1:
                    raise ValueError('Invalid dialogue event schema')
                kind = event.get('type')
                if kind == 'session' and session_id is None:
                    reported = event.get('session_id')
                    if not isinstance(reported, str) or not reported.strip() or len(reported) > 256:
                        raise ValueError('Harness did not report a valid session ID')
                    if request['session_id'] is not None and reported != request['session_id']:
                        raise ValueError('Harness reported a different session ID')
                    session_id = reported
                    emit('dialogue_session', {'session_id': session_id})
                elif kind == 'result' and session_id is not None:
                    if (event.get('session_id') != session_id or not isinstance(event.get('output'), str)
                            or not isinstance(event.get('usage', {}), dict) or type(event.get('success')) is not bool
                            or event.get('usage_scope', 'turn') not in ('turn', 'thread')):
                        raise ValueError('Invalid dialogue result')
                    result = event
                elif kind in ('assistant', 'tool_call', 'tool_result') and session_id is not None:
                    payload = event.get('content')
                    if kind == 'assistant' and not isinstance(payload, str):
                        raise ValueError('Invalid assistant message')
                    if kind != 'assistant' and not isinstance(payload, dict):
                        raise ValueError('Invalid tool event')
                    emit('dialogue_' + kind, {'session_id': session_id, 'content': payload})
                else:
                    raise ValueError('Unexpected dialogue event or missing session event')

        process = await runtime.execute(command, timeout, encoded(request),
                                        env_names=spec.get('env', []) + spec.get('env_optional', []),
                                        env_values=connection_env, on_output=receive)
        if process['timed_out']:
            raise TimeoutError('Dialogue turn exceeded its deadline')
        if process['exit_code'] != 0:
            raise RuntimeError('Dialogue Harness process failed; see turn logs')
        if result is None:
            raise ValueError('Dialogue Harness exited without a terminal result')
        return result
