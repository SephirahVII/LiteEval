"""Agent adapters; external response is captured without assuming inner tool visibility."""

import asyncio
import json
import time
import urllib.error
import urllib.request
from typing import Any, Protocol

from .core import AgentRun, EvalCase, Event
from .storage import uid


def select(value: Any, path: str) -> Any:
    """Small dot-separated path subset, e.g. answer or result.text."""
    if not path or any(not token.isidentifier() for token in path.split(".")):
        raise ValueError("Invalid field path")
    for token in path.split("."):
        if not isinstance(value, dict) or token not in value:
            raise KeyError(path)
        value = value[token]
    return value


class AgentAdapter(Protocol):
    async def invoke(self, case: EvalCase) -> AgentRun: ...


class HTTPAdapter:
    def __init__(self, config: dict):
        self.endpoint = config["endpoint"]
        self.output_path = config.get("output_path", "answer")
        self.timeout = max(0.1, min(float(config.get("timeout_seconds", 30)), 300))
        if not self.endpoint.startswith(("http://", "https://")):
            raise ValueError("Agent endpoint must be HTTP(S)")

    async def invoke(self, case: EvalCase) -> AgentRun:
        started = time.monotonic()
        def call():
            body = json.dumps({"input": case.input}, ensure_ascii=False).encode()
            request = urllib.request.Request(self.endpoint, body, {"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = response.read(2_000_001)
            if len(data) > 2_000_000:
                raise ValueError("Agent response exceeds 2 MB")
            return json.loads(data)
        try:
            raw = await asyncio.wait_for(asyncio.to_thread(call), timeout=self.timeout + 1)
            output = select(raw, self.output_path)
            # Only explicitly reported events count as trajectory; no invented tool steps.
            reported = raw.get("events", []) if isinstance(raw, dict) else []
            if not isinstance(reported, list):
                reported = []
            events = [Event(kind=e.get("kind", "custom"), name=e.get("name", "reported"),
                            attributes=e.get("attributes", {}), sequence=i)
                      for i, e in enumerate(reported) if isinstance(e, dict)]
            return AgentRun(id=uid(), case_id=case.id, status="succeeded", output=output,
                            events=events, latency_ms=int((time.monotonic()-started)*1000),
                            capture_level="reported_spans" if events else "output_only")
        except Exception as exc:
            return AgentRun(id=uid(), case_id=case.id, status="failed", error=f"{type(exc).__name__}: {exc}",
                            latency_ms=int((time.monotonic()-started)*1000))
