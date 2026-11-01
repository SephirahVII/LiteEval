"""Versioned benchmark contracts; no filesystem, database or runner dependencies."""
from dataclasses import dataclass, field
from typing import Any
import math


@dataclass(frozen=True)
class TaskSpec:
    id: str
    prompt: str
    workspace: dict[str, str]  # relative path -> blob digest
    environment: dict
    budget: dict
    artifacts: list[dict]
    judge: dict
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Package:
    id: str
    version: str
    digest: str
    manifest: dict
    tasks: list[TaskSpec]
    blobs: dict[str, bytes] = field(repr=False)


def fields(value, allowed, name):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError(f'{name}: unsupported fields or invalid object')


def finite(value, name, low=0, high=86400):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{name}: expected finite number in {low}..{high}')
    return value


def aggregate(checks, policy):
    """No score renormalization when evidence or a Judge is missing."""
    if not checks or any(c['outcome'] == 'error' for c in checks):
        return {'outcome': 'unscored', 'score': None, 'before_gate': None}
    total = sum(c['weight'] for c in checks)
    score = sum(c['score'] * c['weight'] for c in checks) / total
    gate = all(c['outcome'] == 'pass' for c in checks if c.get('must_pass'))
    return {'outcome': 'pass' if gate and score >= policy.get('pass_threshold', 1) else 'fail',
            'score': score if gate else 0.0, 'before_gate': score}
