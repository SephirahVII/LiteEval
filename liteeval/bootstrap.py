"""Composition root: application services receive concrete I/O implementations here."""
from pathlib import Path
from liteeval.application.benchmarks import BenchmarkService
from liteeval.application import connections
from liteeval.infrastructure.persistence.benchmarks import BenchmarkRepository
from liteeval.infrastructure.artifacts.blobs import BlobStore
from liteeval.infrastructure.sources.package import DirectoryLoader
from liteeval.infrastructure.execution.task_runtime import RuntimeFactory
from liteeval.integrations.harnesses.registry import HarnessRegistry
from liteeval.integrations.models.openai import OpenAICompatibleProvider
from liteeval.infrastructure.security.secrets import redact


def benchmark_service(store):
    repository = BenchmarkRepository(store.connect)
    root = Path(store.path).resolve().parent
    blobs = BlobStore(root / 'benchmark-blobs')

    def connection(project_id, id_, probe=False, api='chat', parameters=None, timeout_seconds=180):
        row = connections.get(store, id_)
        if row['project_id'] != project_id or row['archived_at'] or row['kind'] != 'openai_compatible':
            raise ValueError('Model connection unavailable in this project')
        if probe:
            connections.api_key(store, id_)
            return {k: row[k] for k in ('id', 'endpoint', 'model', 'key_version')}
        provider = OpenAICompatibleProvider({'connection_id': id_, 'base_url': row['endpoint'], 'model': row['model'],
            'generation_parameters': parameters or {}, 'timeout_seconds': timeout_seconds}, store)
        return provider.respond if api == 'responses' else provider.generate

    return BenchmarkService(repository, DirectoryLoader(), blobs, HarnessRegistry(repository),
                            RuntimeFactory(blobs, root / 'benchmark-workspaces'), connection, redact)
