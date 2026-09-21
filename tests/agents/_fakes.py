"""Shared test doubles for the worker-2 suites.

FakeRegistry is a minimal in-memory ToolRegistry so worker/provider tests do
not depend on worker 1's real tool package or its timing. It records every
executed call and lets a test script per-tool behaviour.
"""
from __future__ import annotations

from typing import Callable, Optional

from agents.core import (
    AgentContext, AgentSpec, EventBus, RunControl, Task, ToolCall, ToolContext,
    ToolRegistry, ToolResult, ToolSpec,
)
from agents.providers import MockProvider


class FakeRegistry(ToolRegistry):
    """In-memory registry. Register a tool with a name + a handler
    (args, ctx) -> ToolResult; unknown tools fail cleanly."""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._handlers: dict[str, Callable[[dict, ToolContext], ToolResult]] = {}
        self.executed: list[ToolCall] = []

    def add(self, name: str,
            handler: Optional[Callable[[dict, ToolContext], ToolResult]] = None,
            *, spec: Optional[ToolSpec] = None) -> None:
        self._specs[name] = spec or ToolSpec(name=name, description=f"fake {name}")
        self._handlers[name] = handler or (
            lambda args, ctx, _n=name: ToolResult.success(
                ToolCall(_n), output=f"{_n} ok")
        )

    # ToolRegistry ABC ---------------------------------------------------------

    def register(self, tool) -> None:                     # pragma: no cover
        self._specs[tool.spec.name] = tool.spec
        self._handlers[tool.spec.name] = tool.run

    def get(self, name: str):                             # pragma: no cover
        return self._handlers[name]

    def names(self) -> list[str]:
        return list(self._specs)

    def specs(self, names: Optional[list[str]] = None) -> list[ToolSpec]:
        if names is None:
            return list(self._specs.values())
        return [self._specs[n] for n in names if n in self._specs]

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolResult:
        self.executed.append(call)
        handler = self._handlers.get(call.tool_name)
        if handler is None:
            return ToolResult.failure(call, error=f"unknown tool: {call.tool_name}")
        result = handler(call.arguments, ctx)
        # Normalise the call_id so results line up with the call the worker made.
        result.call_id = call.id
        result.tool_name = call.tool_name
        return result


def make_context(
    *,
    provider: Optional[MockProvider] = None,
    tools: Optional[FakeRegistry] = None,
    workspace: str = "C:/tmp/ws",
    goal: str = "do the thing",
    agent_tools: Optional[list[str]] = None,
) -> AgentContext:
    """An AgentContext wired with a MockProvider and a FakeRegistry."""
    task = Task(title="t", goal=goal, workspace=workspace)
    agent = AgentSpec(name="Tester", goal=goal, tools=agent_tools or [])
    return AgentContext(
        task=task, agent=agent,
        tools=tools or FakeRegistry(),
        provider=provider or MockProvider(),
        events=EventBus(), control=RunControl(),
    )
