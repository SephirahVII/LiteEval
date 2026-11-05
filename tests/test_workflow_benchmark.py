import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from liteeval.bootstrap import benchmark_service
from liteeval.infrastructure.persistence.store import Store
from liteeval.infrastructure.sources.harness import load_harness


class WorkflowBenchmarkTest(unittest.IsolatedAsyncioTestCase):
    async def test_separate_controller_and_agent_with_independent_scoring(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = Store(root / 'state.sqlite')
            project = store.create_project('workflow fixture')['id']
            service = benchmark_service(store)

            def write(name, content):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding='utf-8')

            def register(folder, role, protocol):
                write(f'{folder}/harness.toml', f'''schema_version = 1
[harness]
id = "fixture.{role}"
role = "{role}"
version = "1.0.0"
protocol = "{protocol}"
[runtime]
command = ["{{python}}", "{{harness}}/entry.py"]
''')
                spec, sha = load_harness(root / folder, service.blobs)
                return service.repository.register(project, f'fixture.{role}', role, sha, spec)

            write('bench/dataset.toml', '''schema_version = 1
[benchmark]
id = "workflow-fixture"
version = "1.0.0"
[defaults]
environment_profile = "local"
[environment_profiles.local]
type = "workspace"
''')
            write('bench/tasks/simple/task.toml', '''schema_version = 1
[task]
id = "simple"
[input]
prompt_file = "prompt.md"
[workflow]
id = "fixture.workflow"
fixture_file = "scenario.json"
outputs = ["result.json"]
[[artifacts]]
path = "result.json"
[[judge.checks]]
id = "check"
type = "command"
entrypoint = "judge/check.py"
command = ["{python}", "{judge}/check.py", "{result}"]
''')
            write('bench/tasks/simple/prompt.md', 'Add one to the public value.')
            write('bench/tasks/simple/scenario.json', '{"private": "fixture-only", "initial": 3}')
            write('bench/tasks/simple/judge/check.py', '''import json, sys
from pathlib import Path
assert json.loads(Path('submission/result.json').read_text()) == [4, 5]
path = Path(sys.argv[1]); path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps({'schema_version': 1, 'check_id': 'check', 'outcome': 'pass', 'score': 1, 'reason': 'ok'}))
''')
            write('controller/entry.py', '''import json, sys
from pathlib import Path
request = json.loads(sys.stdin.readline())
assert request['fixture']['private'] == 'fixture-only'
value = request['fixture']['initial']
results = []
for _ in range(2):
    print(json.dumps({'schema_version': 1, 'type': 'agent', 'payload': {'value': value}}), flush=True)
    value = json.loads(sys.stdin.readline())['output']; results.append(value)
    print(json.dumps({'schema_version': 1, 'type': 'event', 'payload': {'value': value}}), flush=True)
Path('result.json').write_text(json.dumps(results))
print(json.dumps({'schema_version': 1, 'type': 'result', 'payload': {'output': value}}), flush=True)
''')
            write('agent/entry.py', '''import json, sys
from pathlib import Path
request = json.load(sys.stdin)
assert 'fixture' not in request and not Path('scenario.json').exists()
assert not request['required_artifacts'] and not Path('result.json').exists()
print(json.dumps({'schema_version': 1, 'type': 'result', 'result': {'schema_version': 1, 'output': request['value']+1}}))
''')
            workflow = register('controller', 'workflow', 'liteeval.workflow/v1')
            agent = register('agent', 'agent', 'liteeval.agent-events/v1')
            release = service.publish(project, root / 'bench')
            with self.assertRaisesRegex(ValueError, 'registered Workflow'):
                service.preflight(project, release['id'], agent, trusted_local=True)
            result = await service.run(project, release['id'], agent, workflow_ref=workflow, trusted_local=True)
            self.assertEqual(result['summary']['pass'], 1)
            execution = service.repository.execution(project, result['execution_id'])
            self.assertEqual([e['payload']['value'] for e in execution['events'] if e['kind'] == 'workflow_event'], [4, 5])
            rejudged = await service.rejudge(project, result['execution_id'])
            self.assertEqual(rejudged['tasks'][0]['grading']['outcome'], 'pass')
