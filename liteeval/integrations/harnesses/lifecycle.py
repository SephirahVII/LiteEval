"""Agent/environment lifecycle including final-state validation before teardown."""
import asyncio
from typing import Protocol
from liteeval.domain.contracts import AgentAdapter, AgentRun, EvalCase
from liteeval.infrastructure.execution.environments import NoEnvironment
from liteeval.infrastructure.security.secrets import safe_error

class Harness(Protocol):
    async def run(self,case: EvalCase,adapter: AgentAdapter) -> AgentRun: ...

class SimpleHarness:
    def __init__(self,environment=None):self.environment=environment or NoEnvironment()
    async def run(self,case,adapter):
        try:
            await self.environment.create(case)
            run=await adapter.invoke(case)
            try:run.final_state=await self.environment.snapshot()
            except Exception as exc:run.final_state={'snapshot_error':safe_error(exc)}
            return run
        finally:
            await asyncio.shield(self.environment.destroy())
