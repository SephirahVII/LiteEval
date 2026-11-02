"""Capture a local or explicitly checked-out public Harness without running it."""
import hashlib
from pathlib import Path
import re
import tomllib
from liteeval.domain.benchmark import fields
from liteeval.domain.paths import relative_path
from liteeval.domain.values import digest
from liteeval.domain.capabilities import validate_capabilities


def load_harness(path, blobs):
    root = Path(path).resolve()
    manifest = root / 'harness.toml'
    if manifest.is_symlink(): raise ValueError('Harness symlink rejected')
    conf = tomllib.loads(manifest.read_text(encoding='utf-8'))
    fields(conf, ['schema_version', 'harness', 'runtime', 'metadata', 'capabilities'], 'harness package')
    fields(conf['harness'], ['id', 'role', 'version', 'protocol'], 'harness')
    role = conf['harness'].get('role')
    protocol = {'agent': 'liteeval.agent/v1', 'evaluation': 'liteeval.evaluation/v1'}.get(role)
    if conf.get('schema_version') != 1 or not protocol or conf['harness'].get('protocol') != protocol:
        raise ValueError('Unsupported Harness role or protocol')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', conf['harness']['id']):
        raise ValueError('Invalid harness id')
    fields(conf['runtime'], ['command', 'env', 'env_optional'], 'runtime')
    fields(conf.get('capabilities', {}), ['provides'], 'harness capabilities')
    validate_capabilities(conf.get('capabilities', {}).get('provides', []))
    argv = conf['runtime']['command']
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
        raise ValueError('Harness command requires argv')
    env = conf['runtime'].get('env', [])
    env_optional = conf['runtime'].get('env_optional', [])
    for names in (env, env_optional):
        if not isinstance(names, list) or not all(isinstance(name, str) and re.fullmatch(r'[A-Z_][A-Z0-9_]{0,63}', name) for name in names):
            raise ValueError('Harness env requires uppercase variable names')
    if len(env) != len(set(env)) or len(env_optional) != len(set(env_optional)) or set(env) & set(env_optional):
        raise ValueError('Duplicate harness env names')
    resources = {}; total = 0
    for p in sorted(root.rglob('*')):
        if '.git' in p.relative_to(root).parts: continue
        if p.is_symlink(): raise ValueError('Harness symlink rejected')
        if p.is_dir(): continue
        if not p.is_file() or p.stat().st_nlink > 1 or p.stat().st_size > 4_000_000:
            raise ValueError('Harness resource exceeds limits')
        total += p.stat().st_size
        if total > 16_000_000 or len(resources) >= 1000: raise ValueError('Harness package exceeds limits')
        resources[relative_path(p.relative_to(root).as_posix())] = blobs.put(p.read_bytes())
    spec = {'kind': 'command', 'definition': conf, 'command': argv, 'resources': resources,
            'env': list(env), 'env_optional': list(env_optional)}
    return spec, digest(spec)
