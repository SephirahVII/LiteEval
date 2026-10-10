"""Versioned contracts shared by API, SDK, adapters, runner and evaluators."""
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol
import time
import math
import uuid

SCHEMA_VERSION = 2
JSON = dict[str, Any]

@dataclass
class ContentPart:
    kind: Literal['text', 'json', 'image', 'audio', 'video', 'file']
    text: str | None = None
    json_value: Any = None
    artifact_id: str | None = None
    mime_type: str | None = None
    metadata: JSON = field(default_factory=dict)

    def __post_init__(self):
        if self.kind not in {'text','json','image','audio','video','file'}:raise ValueError('Unsupported content kind')
        if self.text is not None and not isinstance(self.text,str):raise ValueError('Content text must be a string')
        if not isinstance(self.metadata,dict):raise ValueError('Content metadata must be an object')

@dataclass
class Event:
    kind: str
    name: str
    input_parts: list[ContentPart] = field(default_factory=list)
    output_parts: list[ContentPart] = field(default_factory=list)
    attributes: JSON = field(default_factory=dict)
    sequence: int = 0
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    parent_id: str | None = None
    started_at: float | None = None
    ended_at: float | None = None
    status: str = 'succeeded'
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value, sequence=0):
        if not isinstance(value, dict) or not isinstance(value.get('kind'), str):
            raise ValueError('Each event needs a kind')
        fields = {k: v for k, v in value.items() if k in cls.__dataclass_fields__}
        fields.setdefault('name', value['kind'])
        fields.setdefault('sequence', sequence)
        for name in ('input_parts', 'output_parts'):
            parts=fields.get(name,[])
            if not isinstance(parts,list) or not all(isinstance(p,(dict,ContentPart)) for p in parts):raise ValueError('Event parts must be objects')
            fields[name] = [ContentPart(**p) if isinstance(p, dict) else p for p in parts]
        result = cls(**fields)
        if not isinstance(result.attributes,dict):raise ValueError('Event attributes must be an object')
        if not isinstance(result.name,str) or not isinstance(result.event_id,str) or not result.event_id:raise ValueError('Event name/id must be strings')
        if type(result.sequence) is not int or result.sequence<0:raise ValueError('Event sequence must be nonnegative')
        for timestamp in (result.started_at,result.ended_at):
            if timestamp is not None and (type(timestamp) not in (int,float) or not math.isfinite(timestamp)):raise ValueError('Event timestamps must be finite numbers')
        if result.started_at is not None and result.ended_at is not None and result.ended_at < result.started_at:
            raise ValueError('Event ended_at precedes started_at')
        return result

@dataclass
class EvalCase:
    id: str
    revision: int
    input: JSON
    expected: Any = None
    metadata: JSON = field(default_factory=dict)
    source_trace_id: str | None = None
    input_parts: list[ContentPart] = field(default_factory=list)

@dataclass
class AgentRun:
    id: str
    case_id: str
    status: str
    output: Any = None
    events: list[Event] = field(default_factory=list)
    trace_id: str | None = None
    latency_ms: int | None = None
    error: str | None = None
    capture_level: str = 'output_only'
    raw_response: Any = None
    usage: JSON = field(default_factory=dict)
    cost: float | None = None
    currency: str | None = None
    retryable: bool = False
    final_state: JSON = field(default_factory=dict)
    artifact_ids: list[str] = field(default_factory=list)

@dataclass
class Score:
    run_id: str
    evaluator_revision_id: str
    outcome: Literal['pass', 'fail', 'abstain', 'error']
    value: float | None = None
    reason: str | None = None
    evidence_event_ids: list[str] = field(default_factory=list)
    raw_result: Any = None
    artifact_id: str | None = None

class AgentAdapter(Protocol):
    async def invoke(self, case: EvalCase) -> AgentRun: ...

class Evaluator(Protocol):
    required_inputs: set[str]
    async def evaluate(self, case: EvalCase, run: AgentRun) -> Score: ...
