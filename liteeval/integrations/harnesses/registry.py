"""Explicit builtins plus immutable command Harness revisions. No dynamic imports."""
from liteeval.integrations.harnesses.native import CommandAgentHarness, ModelAgentHarness, NativeEvaluationHarness, CommandEvaluationHarness
from liteeval.domain.values import digest
import hashlib
from pathlib import Path


class HarnessRegistry:
    def __init__(self, repository):
        self.repository = repository
        root = Path(__file__).resolve().parents[2]
        self.build_digest = digest({p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                    for p in sorted(root.rglob('*.py'))})

    def resolve(self, project_id, reference, role):
        if reference == 'liteeval.native/v1' and role == 'evaluation':
            spec = {'id': reference, 'role': role, 'implementation_version': '0.4.0', 'build_digest': self.build_digest, 'protocol': 'liteeval.task/v1'}
            return NativeEvaluationHarness(), {'digest': digest(spec), 'spec': spec}
        if reference == 'liteeval.model-json/v1' and role == 'agent':
            spec = {'id': reference, 'role': role, 'implementation_version': '0.4.0', 'build_digest': self.build_digest, 'protocol': 'liteeval.agent/v1'}
            return ModelAgentHarness(), {'digest': digest(spec), 'spec': spec}
        row = self.repository.harness(project_id, reference)
        if row['role'] != role:
            raise ValueError('Harness role mismatch')
        if row['spec']['kind'] != 'command':
            raise ValueError('Unsupported Harness execution protocol')
        if role == 'evaluation':
            return CommandEvaluationHarness(row['spec']), row
        return CommandAgentHarness(), row
