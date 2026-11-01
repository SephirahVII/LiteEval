"""Capture a local or explicitly checked-out public Harness without running it."""
import hashlib
from pathlib import Path
import re
import tomllib
from liteeval.domain.benchmark import fields
from liteeval.domain.paths import relative_path
from liteeval.domain.values import digest


def load_harness(path, blobs):
    root = Path(path).resolve()
    manifest = root / 'harness.toml'
    if manifest.is_symlink(): raise ValueError('Harness symlink rejected')
    conf = tomllib.loads(manifest.read_text(encoding='utf-8'))
    fields(conf, ['schema_version', 'harness', 'runtime', 'metadata'], 'harness package')
    fields(conf['harness'], ['id', 'role', 'version', 'protocol'], 'harness')
    role = conf['harness'].get('role')
    protocol = {'agent': 'liteeval.agent/v1', 'evaluation': 'liteeval.evaluation/v1'}.get(role)
    if conf.get('schema_version') != 1 or not protocol or conf['harness'].get('protocol') != protocol:
        raise ValueError('Unsupported Harness role or protocol')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', conf['harness']['id']):
        raise ValueError('Invalid harness id')
    fields(conf['runtime'], ['command'], 'runtime')
    argv = conf['runtime']['command']
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
        raise ValueError('Harness command requires argv')
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
    spec = {'kind': 'command', 'definition': conf, 'command': argv, 'resources': resources}
    return spec, digest(spec)
