"""Release Bench instructions in order and snapshot platform-observed evidence."""

import asyncio
import time
from liteeval.domain.usage import FIELDS, normalize
from liteeval.domain.values import encoded
from liteeval.ports.benchmark import DialogueHarness


async def run_dialogue(agent: DialogueHarness, task, public, context, blobs, emit, usage):
    runtime, spec = context['runtime'], context['spec']
    command = agent.prepare(runtime, spec)
    session_id = None
    output = ''
    event_bytes = 0
    prior_usage = None
    for index, step in enumerate(task['dialogue'], 1):
        instruction = blobs.get(step['instruction_sha256']).decode('utf-8')
        request = {**public, 'schema_version': 1, 'instruction': instruction, 'turn_index': index,
                   'session_id': session_id}

        def turn_event(kind, data):
            nonlocal event_bytes
            if kind in ('dialogue_assistant', 'dialogue_tool_call', 'dialogue_tool_result'):
                event_bytes += len(encoded(data).encode('utf-8'))
                if event_bytes > 8_000_000:
                    raise ValueError('Dialogue evidence exceeds 8 MB')
            emit(kind, {'rollout_id': public['rollout_id'], 'turn_index': index, 'session_id': session_id, **data})

        started = time.monotonic()
        turn_event('dialogue_turn_started', {'instruction': instruction, 'instruction_sha256': step['instruction_sha256']})
        try:
            response = await agent.send_turn(runtime, command, spec, request, step['timeout_seconds'], turn_event,
                                             connection_env=context.get('connection_env'))
            session_id = response['session_id']
            raw_usage = response.get('usage', {})
            normalized = normalize(raw_usage)
            scope = response.get('usage_scope', 'turn')
            if scope == 'thread':
                current = normalized
                if index > 1:
                    deltas = {key: current[key] - prior_usage[key] for key in FIELDS
                              if prior_usage and current[key] is not None and prior_usage[key] is not None
                              and current[key] >= prior_usage[key]}
                    normalized = normalize(deltas)
                prior_usage = current
            record = {'role': 'target', 'turn_index': index, 'status': 'succeeded' if response['success'] else 'error',
                      'usage': raw_usage, 'usage_scope': scope, 'normalized': normalized,
                      'latency_ms': int((time.monotonic() - started) * 1000),
                      'requested_model': response.get('requested_model'),
                      'provider_reported_model': response.get('model')}
            usage.append(record)
            if not response['success']:
                raise RuntimeError('Harness reported an unsuccessful dialogue turn')
            output = response['output']
            turn_event('dialogue_turn_completed', {'output': output, 'usage': record})
        except asyncio.CancelledError:
            turn_event('dialogue_turn_interrupted', {'reason': 'Execution cancelled or deadline exceeded'})
            raise
        except Exception as exc:
            turn_event('dialogue_turn_failed', {'reason': str(exc), 'timed_out': isinstance(exc, TimeoutError)})
            raise
    return {'schema_version': 1, 'output': output, 'session_id': session_id, 'turn_count': len(task['dialogue'])}


def snapshot_dialogue(events, blobs):
    records = (encoded(event) for event in events)
    data = ('\n'.join(records) + '\n').encode('utf-8')
    return {'sha256': blobs.put(data), 'size_bytes': len(data)}
