import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from liteeval.application.benchmark_exports import export_execution
from liteeval.bootstrap import benchmark_service
from liteeval.infrastructure.persistence.store import Store
from liteeval.infrastructure.sources.harness import load_harness
from liteeval.interfaces.http.api import dispatch


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = r'''
import json, os, sys, time, uuid
from pathlib import Path

req = json.load(sys.stdin)
assert 'dialogue' not in req and 'prompt' not in req and 'judge' not in req
assert not Path('turns').exists() and not Path('judge').exists()
turn = req['turn_index']
state = Path('.harness/session.json')
instruction = req['instruction']
if turn == 1:
    assert req['session_id'] is None and not state.exists()
    assert '41' not in instruction and '47' not in instruction
    session = str(uuid.uuid4())
    state.write_text(json.dumps({'session': session, 'reference': instruction.split('reference ')[1].split()[0]}))
else:
    saved = json.loads(state.read_text())
    assert req['session_id'] == saved['session'] and saved['reference'] not in instruction
    session = saved['session']

def emit(kind, **data):
    print(json.dumps({'type': kind, 'schema_version': 1, **data}), flush=True)

mode = os.getenv('LITEEVAL_TEST_DIALOGUE_MODE')
emit('session', session_id='different-session' if mode == 'changed' and turn == 2 else session)
emit('assistant', content=f'Fixture reply for turn {turn}')
if turn == 2 and mode == 'timeout':
    time.sleep(2)
if turn == 2 and mode == 'missing_result':
    sys.exit(0)
if turn == 3:
    assert '47' in instruction
    emit('tool_call', content={'id': 'call-1', 'name': 'Write', 'input': {'path': 'result.json'}})
    Path('result.json').write_text(json.dumps({'reference': saved['reference'], 'currency': 'GBP', 'amount': 47}))
    emit('tool_result', content={'id': 'call-1', 'result': 'written'})
emit('result', session_id=session, output=f'Turn {turn} complete', success=True, usage_scope='thread',
     usage={'input_tokens': 10 * turn, 'output_tokens': 2 * turn})
'''


class DialogueBenchmarkTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = Store(self.root / 'db.sqlite')
        self.project = self.store.create_project('dialogue')['id']
        self.service = benchmark_service(self.store)
        self.package = self.root / 'bench'
        shutil.copytree(ROOT / 'benchmarks/dialogue_session', self.package)
        harness = self.root / 'harness'
        harness.mkdir()
        (harness / 'harness.toml').write_text('''schema_version = 1
[harness]
id = "fixture.dialogue"
role = "agent"
version = "1.0.0"
protocol = "liteeval.dialogue/v1"
[runtime]
command = ["{python}", "{harness}/agent.py"]
env_optional = ["LITEEVAL_TEST_DIALOGUE_MODE"]
''', encoding='utf-8')
        (harness / 'agent.py').write_text(FIXTURE, encoding='utf-8')
        spec, sha = load_harness(harness, self.service.blobs)
        self.harness = self.service.repository.register(self.project, 'fixture.dialogue', 'agent', sha, spec)

    async def execute(self, package=None, **options):
        release = self.service.publish(self.project, package or self.package)
        return await self.service.run(self.project, release['id'], self.harness, trusted_local=True, **options)

    async def test_parallel_sessions_trajectory_judge_rejudge_export_and_http_cursor(self):
        other_package = self.root / 'bench-other'
        shutil.copytree(self.package, other_package)
        dataset = other_package / 'dataset.toml'
        dataset.write_text(dataset.read_text().replace('1.0.2', '1.0.3'))
        for path in (other_package / 'tasks/dialogue-001').rglob('*'):
            if path.suffix in ('.md', '.json'):
                text = path.read_text(encoding='utf-8')
                path.write_text(text.replace('DX-7d1a92a6', 'DX-a4f53d21'), encoding='utf-8')
        first, second = await asyncio.gather(self.execute(), self.execute(other_package))
        self.assertEqual([run['summary']['pass'] for run in (first, second)], [1, 1])
        self.assertNotEqual(first['tasks'][0]['output']['session_id'], second['tasks'][0]['output']['session_id'])
        self.assertEqual(first['usage']['totals']['input_tokens'], 30)
        records = first['tasks'][0]['usage_records']
        self.assertEqual([r['usage']['input_tokens'] for r in records], [10, 20, 30])
        self.assertEqual([r['normalized']['input_tokens'] for r in records], [10, 10, 10])
        run_id = first['execution_id']
        route = f'/v1/benchmark-executions/{run_id}/events'
        principal = {'admin': False, 'project_id': self.project}
        events, cursor = [], 0
        while True:
            code, page = dispatch(self.store, 'GET', f'{route}?limit=2&task_id=dialogue-001&after={cursor}', principal=principal)
            self.assertEqual(code, 200)
            events.extend(page['events'])
            cursor = page['next_after']
            if not page['has_more']:
                break
        full = self.service.repository.execution(self.project, run_id)
        self.assertEqual(events, full['events'])
        self.assertEqual(page['status'], 'completed')
        self.assertEqual(sum(e['kind'] == 'dialogue_tool_result' for e in events), 1)
        foreign = self.store.create_project('foreign')['id']
        self.assertEqual(dispatch(self.store, 'GET', route, principal={'admin': False, 'project_id': foreign})[0], 404)
        self.assertEqual(dispatch(self.store, 'GET', route + '?limit=101', principal=principal)[0], 400)
        rerated = await self.service.rejudge(self.project, run_id)
        self.assertEqual(rerated['tasks'][0]['grading']['outcome'], 'pass')
        archive = self.root / 'dialogue.zip'
        export_execution(self.service, self.project, rerated['execution_id'], archive)
        with zipfile.ZipFile(archive) as exported:
            trajectory = [json.loads(line) for line in exported.read('trajectories/dialogue-001.jsonl').splitlines()]
        self.assertEqual(trajectory, [event for event in events if event['kind'].startswith('dialogue_')])

    async def test_timeout_is_visible_before_finish_and_blocks_later_turns(self):
        path = self.package / 'tasks/dialogue-001/dialogue.toml'
        text = path.read_text()
        path.write_text(text.replace('instruction_file = "turns/02-request.md"\ntimeout_seconds = 60',
                                     'instruction_file = "turns/02-request.md"\ntimeout_seconds = 1.5'))
        started = asyncio.get_running_loop().create_future()
        with patch.dict(os.environ, {'LITEEVAL_TEST_DIALOGUE_MODE': 'timeout'}):
            work = asyncio.create_task(self.execute(on_started=started.set_result))
            run_id = await asyncio.wait_for(started, 5)
            async def observe():
                while True:
                    page = self.service.repository.event_page(self.project, run_id)
                    if any(e['kind'] == 'dialogue_assistant' and e['payload']['turn_index'] == 2 for e in page['events']):
                        return page
                    await asyncio.sleep(.025)
            page = await asyncio.wait_for(observe(), 5)
            self.assertEqual(page['status'], 'running')
            result = await work
        self.assertEqual(result['tasks'][0]['execution_status'], 'timed_out')
        events = self.service.repository.execution(self.project, run_id)['events']
        self.assertIn('dialogue_turn_failed', [e['kind'] for e in events])
        self.assertEqual([e['payload']['turn_index'] for e in events if e['kind'] == 'dialogue_turn_started'], [1, 2])

    async def test_reject_session_switch_or_missing_terminal_event(self):
        for mode, message in [('changed', 'different session'), ('missing_result', 'terminal result')]:
            with self.subTest(mode=mode), patch.dict(os.environ, {'LITEEVAL_TEST_DIALOGUE_MODE': mode}):
                result = await self.execute()
                task = result['tasks'][0]
                self.assertEqual(task['execution_status'], 'failed')
                self.assertEqual(task['grading']['outcome'], 'unscored')
                self.assertIn(message, task['error'])
                events = self.service.repository.execution(self.project, result['execution_id'])['events']
                self.assertEqual([e['payload']['turn_index'] for e in events if e['kind'] == 'dialogue_turn_started'], [1, 2])

    def test_wrong_harness_or_public_future_instruction_is_rejected(self):
        release = self.service.publish(self.project, self.package)
        with self.assertRaisesRegex(ValueError, 'Harness mode'):
            self.service.preflight(self.project, release['id'], 'liteeval.model-json/v1', trusted_local=True)
        task = self.package / 'tasks/dialogue-001'
        workspace = task / 'workspace'
        workspace.mkdir()
        shutil.copyfile(task / 'turns/03-revision.md', workspace / 'revealed.md')
        path = task / 'task.toml'
        with path.open('a', encoding='utf-8') as stream:
            stream.write('\n[environment]\ninitial_workspace = "workspace"\n')
        with self.assertRaisesRegex(ValueError, 'Agent workspace'):
            self.service.loader.load(self.package)

    def test_codex_submission_tool_only_writes_declared_bounded_files(self):
        script = ROOT / 'harnesses/codex_dialogue/submission.py'
        messages = []
        for index, (path, content) in enumerate((('result.json', '{}'), ('undeclared.json', '{}'),
                                                  ('../outside.json', '{}'), ('result.json', 'x' * 11)), 1):
            messages.append(json.dumps({'jsonrpc': '2.0', 'id': index, 'method': 'tools/call',
                                        'params': {'name': 'write_submission', 'arguments': {'path': path, 'content': content}}}))
        process = subprocess.run([sys.executable, '-I', str(script), '{"result.json": 10}'], cwd=self.root,
                                 input='\n'.join(messages) + '\n', capture_output=True, text=True, check=True, timeout=10)
        self.assertEqual([json.loads(line)['result']['isError'] for line in process.stdout.splitlines()], [False, True, True, True])
        self.assertEqual((self.root / 'result.json').read_text(), '{}')
        self.assertFalse((self.root / 'undeclared.json').exists())
