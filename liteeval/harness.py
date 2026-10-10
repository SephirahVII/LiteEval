"""The harness owns an Agent's interaction with its environment."""

from typing import Protocol

from .adapters import AgentAdapter
from .core import AgentRun, EvalCase
from .environments import EnvironmentProvider, NoEnvironment


class Harness(Protocol):
    async def run(self, case: EvalCase, adapter: AgentAdapter) -> AgentRun: ...


class SimpleHarness:
    def __init__(self, environment: EnvironmentProvider | None = None):
        self.environment = environment or NoEnvironment()

    async def run(self, case: EvalCase, adapter: AgentAdapter) -> AgentRun:
        handle = await self.environment.create(case)
        try:
            return await adapter.invoke(case)
        finally:
            await self.environment.destroy(handle)
