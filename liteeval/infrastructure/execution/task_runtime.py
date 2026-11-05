"""Per-invocation runtime. Workspace mode is trusted-local execution, not a sandbox."""
from pathlib import Path
import os
import sys
from types import SimpleNamespace
from liteeval.domain.paths import relative_path
from liteeval.infrastructure.execution.environments import make_environment, snapshot_bytes


class TaskRuntime:
    def __init__(self, spec, blobs, root, labels, emit):
        self.environment = make_environment(spec, root=root, labels=labels)
        self.environment.on_output = lambda stream, text: emit('log', {'stream': stream, 'text': text})
        self.spec, self.blobs = spec, blobs

    async def __aenter__(self):
        await self.environment.create(SimpleNamespace(input={}))
        self.root = self.environment.path
        return self

    async def __aexit__(self, *args):
        await self.environment.destroy()

    def materialize(self, manifest, prefix=''):
        for name, sha in manifest.items():
            path = self.root / relative_path(prefix + name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self.blobs.get(sha))

    def write(self, name, data):
        path = self.root / relative_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def argv(self, command, paths):
        values = {k: ('/workspace/' + v if self.spec['type'] == 'docker' else str(self.root / v)) for k, v in paths.items()}
        values['python'] = 'python' if self.spec['type'] == 'docker' else sys.executable
        values['workspace'] = '/workspace' if self.spec['type'] == 'docker' else str(self.root)
        result = []
        for item in command:
            for key, value in values.items(): item = item.replace('{' + key + '}', value)
            # Native package v1 also accepts the documented logical Judge paths.
            for logical, key in [('/judge-output/result.json', 'result'), ('/submission', 'submission'), ('/judge', 'judge')]:
                if item == logical or item.startswith(logical + '/'):
                    item = values.get(key, logical) + item[len(logical):]
                    break
            if '{' in item or '}' in item:
                raise ValueError('Unresolved command placeholder')
            result.append(item)
        return result

    async def execute(self, command, timeout, stdin=None, env_names=(), on_output=None, env_values=None, exchange=None):
        allowed = ('PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'LANG', 'PYTHONUTF8')
        env = {k: os.environ[k] for k in allowed if k in os.environ}
        # Harness-declared names resolved from the Worker environment; values never
        # cross the package boundary or appear on the host argv for Docker.
        env.update({k: os.environ[k] for k in env_names if k in os.environ})
        env.update(env_values or {})
        env['PYTHONUTF8'] = '1'
        return await self.environment.execute(command, timeout=timeout, stdin=stdin, env=env, on_output=on_output, exchange=exchange)

    def collect(self, rules):
        files, missing = {}, []
        for rule in rules:
            name = relative_path(rule['path'])
            path = self.root / name
            if not path.exists():
                if rule.get('required', True): missing.append(name)
                continue
            data = snapshot_bytes(self.root, name, int(rule.get('max_bytes', 1_000_000)))
            files[name] = {'sha256': self.blobs.put(data), 'size_bytes': len(data)}
        return files, missing


class RuntimeFactory:
    def __init__(self, blobs, root):
        self.blobs, self.root = blobs, Path(root)

    def __call__(self, spec, labels, emit):
        return TaskRuntime(spec, self.blobs, self.root, labels, emit)

    def check(self, spec):
        import shutil
        if spec['type'] == 'docker' and not shutil.which('docker'):
            raise ValueError('Docker CLI unavailable on this Worker; no workspace fallback is permitted')
