"""Shared data contracts for trace, case execution and scoring."""

from dataclasses import dataclass, field
from typing import Any, Literal

JSON = dict[str, Any]


@dataclass
class ContentPart:
    kind: Literal["text", "json", "image", "audio", "video", "file"]
    text: str | None = None
    json_value: Any = None
    artifact_id: str | None = None
    mime_type: str | None = None
    metadata: JSON = field(default_factory=dict)


@dataclass
class Event:
    kind: str
    name: str
    input_parts: list[ContentPart] = field(default_factory=list)
    output_parts: list[ContentPart] = field(default_factory=list)
    attributes: JSON = field(default_factory=dict)
    sequence: int = 0


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
    capture_level: str = "output_only"


@dataclass
class Score:
    run_id: str
    evaluator_revision_id: str
    outcome: Literal["pass", "fail", "abstain", "error"]
    value: float | None = None
    reason: str | None = None
    evidence_event_ids: list[str] = field(default_factory=list)
