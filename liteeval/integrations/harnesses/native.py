from liteeval.domain.values import decoded
"""Native agent and evaluation harnesses behind separate contracts."""
import asyncio
import json
import time
from liteeval.domain.benchmark import finite
from liteeval.domain.paths import relative_path
from liteeval.domain.values import encoded


class CommandAgentHarness:
    async def run(self, public_task, context):
        runtime = context['runtime']
        runtime.materialize(context['spec']['resources'], '.harness/')
        command = runtime.argv(context['spec']['command'], {'harness': '.harness'})
        result = await runtime.execute(command, context['timeout'], encoded(public_task),
                                       env_names=context['spec'].get('env', []) + context['spec'].get('env_optional', []))
        if result['timed_out']: raise TimeoutError('Target deadline exceeded')
        if result['exit_code'] != 0: raise RuntimeError('Target process failed: ' + result['stderr'][-2000:])
        output = decoded(result['stdout'])
        if not isinstance(output, dict) or output.get('schema_version') != 1 or 'output' not in output:
            raise ValueError('Agent must return schema_version=1 and output')
        return output


class ModelAgentHarness:
    """Explicit JSON action loop; model calls and tool invocations remain observable."""
    async def run(self, public_task, context):
        runtime, generate = context['runtime'], context['generate']
        system = ('Complete the supplied task using JSON actions. Reply with exactly one JSON object. '
                  'Actions: {"action":"read","path":"relative path"}, '
                  '{"action":"write","path":"relative path","text":"content"}, '
                  '{"action":"exec","command":["program","arg"]}, '
                  '{"action":"final","answer":"answer"}. Paths are relative to your workspace. '
                  'Produce requested output files before final. No grading materials are available.')
        messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': encoded(public_task)}]
        turns = []
        for index in range(32):
            content, raw = await generate(messages)
            turns.append(raw.get('usage', {}))
            context['emit']('model_response', {'turn': index, 'requested_model': context['model'],
                                               'provider_reported_model': raw.get('model'), 'usage': raw.get('usage')})
            action = decoded(content); name = action['action']
            messages.append({'role': 'assistant', 'content': content})
            if name == 'final':
                return {'schema_version': 1, 'output': action['answer'], 'usage_turns': turns,
                        'requested_model': context['model'], 'provider_reported_model': raw.get('model')}
            if name == 'read':
                from liteeval.infrastructure.execution.environments import snapshot_bytes
                result = snapshot_bytes(runtime.root, relative_path(action['path'])).decode('utf-8')
            elif name == 'write':
                data = action['text'].encode('utf-8')
                if len(data) > 1_000_000: raise ValueError('Write exceeds tool limit')
                # Resolve parent components before writing; do not follow Agent-created symlinks.
                path = runtime.root / relative_path(action['path'])
                path.resolve().relative_to(runtime.root.resolve())
                if any(p.is_symlink() for p in [path, *path.parents] if p != runtime.root.parent):
                    raise ValueError('Symlink write rejected')
                runtime.write(action['path'], data); result = 'written'
            elif name == 'exec':
                command = action['command']
                if not isinstance(command, list) or not command or not all(isinstance(v, str) for v in command):
                    raise ValueError('exec requires argv')
                result = await runtime.execute(command, min(120, context['timeout']))
            else:
                raise ValueError('Unknown model action')
            context['emit']('tool', {'name': name, 'arguments': action, 'result': result})
            messages.append({'role': 'user', 'content': encoded({'tool_result': result})[:100000]})
        raise RuntimeError('Agent exceeded 32 model turns')


class NativeEvaluationHarness:
    async def evaluate(self, task, submission, context):
        checks = []
        async with context['runtime_factory'](task['environment'], {'execution': context['execution_id']}, context['emit']) as judge_runtime:
            judge_runtime.materialize(task['judge']['resources'])
            judge_runtime.materialize({n: f['sha256'] for n, f in submission.items()}, 'submission/')
            for check in task['judge']['checks']:
                start = time.monotonic()
                try:
                    if check['type'] == 'command':
                        result_name = 'judge-output/' + check['id'] + '.json'
                        command = judge_runtime.argv(check['command'], {'judge': 'judge', 'submission': 'submission', 'result': result_name})
                        result = await judge_runtime.execute(command, check.get('timeout_seconds', task['budget']['judge_timeout_seconds']))
                        if result['timed_out'] or result['exit_code'] != 0:
                            raise RuntimeError('Judge process failed or timed out')
                        from liteeval.infrastructure.execution.environments import snapshot_bytes
                        verdict = decoded(snapshot_bytes(judge_runtime.root, result_name))
                    else:
                        verdict = await context['llm_judge'](task, check, submission)
                    if verdict.get('schema_version') != 1 or verdict.get('check_id') != check['id']:
                        raise ValueError('Judge result identity mismatch')
                    if verdict.get('outcome') not in ('pass', 'fail') or not isinstance(verdict.get('reason'), str):
                        raise ValueError('Judge result requires pass/fail and reason')
                    finite(verdict['score'], 'Judge score', 0, 1)
                    verdict = {**verdict, 'weight': check.get('weight', 1), 'must_pass': check.get('must_pass', False)}
                except Exception as exc:
                    verdict = {'check_id': check['id'], 'outcome': 'error', 'score': None, 'reason': str(exc),
                               'weight': check.get('weight', 1), 'must_pass': check.get('must_pass', False)}
                verdict['latency_ms'] = int((time.monotonic() - start) * 1000)
                checks.append(verdict); context['emit']('judge_completed', verdict)
        return checks


class CommandEvaluationHarness:
    """Isolated adapter for self-built/public evaluation runners implementing protocol v1.

    Target execution and immutable collection remain owned by the platform. The
    external runner owns its checks; native checks are not executed a second time.
    """
    async def evaluate(self, task, submission, context):
        async with context['runtime_factory'](task['environment'], {'execution': context['execution_id']}, context['emit']) as runtime:
            runtime.materialize(task['judge']['resources'])
            runtime.materialize({n: f['sha256'] for n, f in submission.items()}, 'submission/')
            runtime.materialize(self.spec['resources'], '.harness/')
            request = {'schema_version': 1, 'task_id': task['id'], 'prompt': task['prompt'],
                       'checks': task['judge']['checks'], 'ground_truth_file': task['judge']['ground_truth_file'],
                       'submission': submission}
            command = runtime.argv(self.spec['command'], {'harness': '.harness'})
            response = await runtime.execute(command, task['budget']['judge_timeout_seconds'], encoded(request))
            if response['timed_out'] or response['exit_code'] != 0:
                raise RuntimeError('Evaluation Harness failed or timed out')
            data = decoded(response['stdout'])
            if data.get('schema_version') != 1 or not isinstance(data.get('checks'), list):
                raise ValueError('Invalid Evaluation Harness response')
            expected = {c['id']: c for c in task['judge']['checks']}
            observed = set(); results = []
            for verdict in data['checks']:
                key = verdict.get('check_id')
                if key not in expected or key in observed: raise ValueError('Evaluation check identity mismatch')
                observed.add(key)
                if verdict.get('outcome') not in ('pass', 'fail') or not isinstance(verdict.get('reason'), str):
                    raise ValueError('Invalid Evaluation verdict')
                finite(verdict['score'], 'Evaluation score', 0, 1)
                rule = expected[key]
                verdict = {**verdict, 'weight': rule.get('weight', 1), 'must_pass': rule.get('must_pass', False)}
                results.append(verdict); context['emit']('judge_completed', verdict)
            if observed != set(expected): raise ValueError('Evaluation Harness omitted checks')
            return results

    def __init__(self, spec):
        self.spec = spec
