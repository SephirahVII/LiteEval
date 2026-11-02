"""Harness-declared environment passthrough: loader, preflight, and execution isolation."""
import asyncio
import json
from pathlib import Path
import os
import tempfile
import unittest
from liteeval.bootstrap import benchmark_service
from liteeval.infrastructure.persistence.store import Store
from liteeval.infrastructure.sources.harness import load_harness

DECLARED = 'LITEEVAL_TEST_DECLARED_SECRET'
UNDECLARED = 'LITEEVAL_TEST_UNDECLARED_SECRET'

AGENT = '''import json, os, sys
from pathlib import Path
request = json.load(sys.stdin)
Path('outputs').mkdir()
Path('outputs/result.json').write_text(json.dumps({
    'declared': os.environ.get('%s') == 's3cr3t',
    'leaked': '%s' in os.environ,
}), encoding='utf-8')
print(json.dumps({'schema_version': 1, 'output': 'done'}))
''' % (DECLARED, UNDECLARED)

JUDGE = '''import argparse, json
from pathlib import Path
args = argparse.ArgumentParser()
args.add_argument('--submission')
args.add_argument('--result')
ns = args.parse_args()
data = json.loads((Path(ns.submission) / 'outputs/result.json').read_text(encoding='utf-8'))
ok = data['declared'] and not data['leaked']
reason = 'declared env received, undeclared env isolated' if ok else repr(data)
Path(ns.result).parent.mkdir(parents=True, exist_ok=True)
Path(ns.result).write_text(json.dumps({'schema_version': 1, 'check_id': 'env',
    'outcome': 'pass' if ok else 'fail', 'score': 1 if ok else 0, 'reason': reason}), encoding='utf-8')
'''

DATASET = '''schema_version = 1
[benchmark]
id = "env-passthrough"
version = "1.0.0"
name = "Env passthrough acceptance"
description = "Harness env passthrough"
license = "MIT"
[tasks]
include = ["tasks/*/task.toml"]
[defaults]
environment_profile = "local"
[environment_profiles.local]
type = "workspace"
'''

TASK = '''schema_version = 1
[task]
id = "env-001"
title = "Env visibility"
[input]
prompt_file = "prompt.md"
[[artifacts]]
path = "outputs/result.json"
required = true
[[judge.checks]]
id = "env"
type = "command"
entrypoint = "judge/test.py"
command = ["{python}", "/judge/test.py", "--submission", "/submission", "--result", "/judge-output/result.json"]
weight = 1
must_pass = true
[judge.aggregate]
method = "weighted_mean"
pass_threshold = 1
'''


class HarnessEnvTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'db.sqlite')
        self.project = self.store.create_project('env')['id']
        self.service = benchmark_service(self.store)
        bench = self.root / 'bench'
        task = bench / 'tasks/env-001'
        (task / 'judge').mkdir(parents=True)
        (bench / 'dataset.toml').write_text(DATASET, encoding='utf-8')
        (task / 'task.toml').write_text(TASK, encoding='utf-8')
        (task / 'prompt.md').write_text('Write the artifact.', encoding='utf-8')
        (task / 'judge/test.py').write_text(JUDGE, encoding='utf-8')
        self.release = self.service.publish(self.project, bench)
        self.harness = self.root / 'harness'
        self.harness.mkdir()
        self.harness.joinpath('agent.py').write_text(AGENT, encoding='utf-8')

    def tearDown(self):
        os.environ.pop(DECLARED, None)
        os.environ.pop(UNDECLARED, None)
        self.temp.cleanup()

    def write_manifest(self, env=None):
        env_line = 'env = [%s]\n' % ', '.join('"%s"' % name for name in env) if env else ''
        self.harness.joinpath('harness.toml').write_text('''schema_version = 1
[harness]
id = "test.env-agent"
role = "agent"
version = "1.0.0"
protocol = "liteeval.agent/v1"
[runtime]
command = ["{python}", "{harness}/agent.py"]
%s''' % env_line, encoding='utf-8')

    def register(self):
        spec, sha = load_harness(self.harness, self.service.blobs)
        return self.service.repository.register(self.project, spec['definition']['harness']['id'], 'agent', sha, spec)

    def test_loader_accepts_declared_env(self):
        self.write_manifest(env=[DECLARED])
        spec, _ = load_harness(self.harness, self.service.blobs)
        self.assertEqual(spec['env'], [DECLARED])
        self.assertEqual(spec['env_optional'], [])
        manifest = self.harness / 'harness.toml'
        manifest.write_text(manifest.read_text(encoding='utf-8') + f'env_optional = ["{UNDECLARED}"]\n', encoding='utf-8')
        spec, _ = load_harness(self.harness, self.service.blobs)
        self.assertEqual(spec['env_optional'], [UNDECLARED])

    def test_optional_env_not_required_by_preflight(self):
        os.environ.pop(DECLARED, None)
        self.write_manifest(env=[DECLARED])
        manifest = self.harness / 'harness.toml'
        manifest.write_text(manifest.read_text(encoding='utf-8') + f'env_optional = ["{UNDECLARED}"]\n', encoding='utf-8')
        harness = self.register()
        with self.assertRaises(ValueError):
            self.service.preflight(self.project, self.release['id'], harness, trusted_local=True)
        os.environ[DECLARED] = 's3cr3t'
        plan = self.service.preflight(self.project, self.release['id'], harness, trusted_local=True)
        self.assertEqual(plan['release_id'], self.release['id'])

    def test_loader_rejects_invalid_and_duplicate_env(self):
        for line in ('env = ["lowercase"]', f'env = ["{DECLARED}", "{DECLARED}"]',
                     'env = "NOT_A_LIST"', 'env = [123]'):
            self.write_manifest()
            manifest = self.harness / 'harness.toml'
            manifest.write_text(manifest.read_text(encoding='utf-8') + line + '\n', encoding='utf-8')
            with self.assertRaises(ValueError, msg=line):
                load_harness(self.harness, self.service.blobs)

    def test_preflight_requires_declared_env_on_worker(self):
        os.environ.pop(DECLARED, None)
        self.write_manifest(env=[DECLARED])
        harness = self.register()
        with self.assertRaises(ValueError) as ctx:
            self.service.preflight(self.project, self.release['id'], harness, trusted_local=True)
        self.assertIn(DECLARED, str(ctx.exception))
        os.environ[DECLARED] = 's3cr3t'
        plan = self.service.preflight(self.project, self.release['id'], harness, trusted_local=True)
        self.assertEqual(plan['release_id'], self.release['id'])

    async def test_declared_env_reaches_agent_and_undeclared_does_not(self):
        os.environ[DECLARED] = 's3cr3t'
        os.environ[UNDECLARED] = 'should-not-leak'
        self.write_manifest(env=[DECLARED])
        result = await self.service.run(self.project, self.release['id'], self.register(), trusted_local=True)
        self.assertEqual(result['summary']['pass'], 1)
        submission = result['tasks'][0]['submission']['outputs/result.json']
        data = json.loads(self.service.blobs.get(submission['sha256']))
        self.assertEqual(data, {'declared': True, 'leaked': False})


if __name__ == '__main__':
    unittest.main()
