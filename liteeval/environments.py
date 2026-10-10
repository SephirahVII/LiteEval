"""Environment lifecycle contract for future sandbox-backed Agent runs."""

from typing import Any, Protocol

from .core import EvalCase


class EnvironmentProvider(Protocol):
    async def create(self, case: EvalCase) -> Any: ...
    async def destroy(self, handle: Any) -> None: ...


class NoEnvironment:
    """Ordinary HTTP Agents manage their own environment."""

    async def create(self, case: EvalCase) -> None:
        return None

    async def destroy(self, handle: None) -> None:
        return None
