"""A registered task controller in a workspace separate from the Agent."""
import asyncio
from liteeval.domain.benchmark import finite
from liteeval.domain.values import decoded, encoded


class CommandWorkflow:
    def __init__(self, spec):
        self.spec = spec

    async def run(self, task, public, agent, context, evidence):
        runtime_factory, emit = context['runtime_factory'], context['emit']
        spec = self.spec
        outputs = [rule for rule in task['artifacts'] if rule['path'] in task['workflow']['outputs']]
        environment = {**task['environment'], 'network': 'none'}
        async with runtime_factory(environment, {'execution': context['execution_id'], 'role': 'workflow'}, emit) as runtime:
            runtime.materialize(spec['resources'], '.harness/')
            runtime.materialize(task['workspace'])
            command = runtime.argv(spec['command'], {'harness': '.harness'})
            request = {'schema_version': 1, 'task': public, 'options': context['workflow_options'],
                       'fixture': decoded(runtime.blobs.get(task['workflow']['fixture_sha256']))}
            result = None

            async def exchange(line):
                nonlocal result
                if result is not None or len(line.encode('utf-8')) > 1_000_000:
                    raise ValueError('Invalid Workflow message boundary')
                message = decoded(line)
                if not isinstance(message, dict) or message.get('schema_version') != 1:
                    raise ValueError('Invalid Workflow message schema')
                payload = message.get('payload')
                if not isinstance(payload, dict):
                    raise ValueError('Workflow message requires object payload')
                if message.get('type') == 'agent':
                    deadline = message.get('timeout_seconds', context['timeout'])
                    finite(deadline, 'Workflow decision timeout', 0.001, context['timeout'])
                    try:
                        answer = await asyncio.wait_for(agent.run({**payload, **public}, context), deadline)
                    except TimeoutError:
                        answer = {'schema_version': 1, 'timed_out': True}
                    return encoded(answer)
                if message.get('type') == 'event':
                    emit('workflow_event', payload)
                elif message.get('type') == 'result' and 'output' in payload:
                    result = payload
                else:
                    raise ValueError('Unknown Workflow message')
                return None

            try:
                process = await runtime.execute(command, context['timeout'], encoded(request), exchange=exchange)
                if process['timed_out']:
                    raise TimeoutError('Workflow deadline exceeded')
                if process['exit_code'] != 0 or result is None:
                    raise RuntimeError('Workflow failed: ' + process['stderr'][-2000:])
                return result
            finally:
                files, missing = runtime.collect(outputs)
                evidence['controller_files'] = files
                evidence['controller_missing'] = missing
