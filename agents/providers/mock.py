"""MockProvider — a deterministic AgentProvider for tests and Phase-1 runs.

The whole agent runtime can be exercised end to end without a network by
scripting exactly what the "LLM" says. Two modes:

  * script   a list of ProviderResponse, handed out in order; the last one
             repeats forever (so a ReAct loop that runs long still gets a
             terminating "stop" response if you end the script with one).
  * handler  a callable (messages, tools) -> ProviderResponse, for tests
             that need to branch on what the worker actually sent.

Every call is recorded in `.calls` so a test can assert on the messages,
tool specs and system prompt the worker produced — the thing that usually
regresses silently.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from agents.core import (
    AgentProvider, ProviderCapabilities, ProviderMessage, ProviderResponse,
    ToolSpec,
)


@dataclass
class MockCall:
    """One recorded invocation of complete()."""
    messages: list[ProviderMessage]
    tools: list[ToolSpec]
    system: str
    model: Optional[str] = None
    temperature: float = 0.2
    max_tokens: int = 4096


Handler = Callable[[list[ProviderMessage], list[ToolSpec]], ProviderResponse]


class MockProvider(AgentProvider):
    """A scripted tool-calling provider. `name` is "mock" so a provider_hint
    of "mock" routes here."""
    name = "mock"

    def __init__(
        self,
        script: Optional[list[ProviderResponse]] = None,
        *,
        handler: Optional[Handler] = None,
        vision: bool = False,
    ) -> None:
        self._script = list(script) if script else []
        self._handler = handler
        self._vision = vision
        self._i = 0
        self.calls: list[MockCall] = []

    async def complete(
        self,
        messages: list[ProviderMessage],
        tools: Optional[list[ToolSpec]] = None,
        *,
        system: str = "",
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> ProviderResponse:
        self.calls.append(MockCall(
            messages=list(messages), tools=list(tools or []), system=system,
            model=model, temperature=temperature, max_tokens=max_tokens,
        ))
        if self._handler is not None:
            return self._handler(messages, list(tools or []))
        if self._script:
            # Last scripted response repeats — a loop that outruns the script
            # still terminates on a trailing "stop" response.
            resp = self._script[min(self._i, len(self._script) - 1)]
            self._i += 1
            return resp
        # No script, no handler: a benign empty answer that ends any loop.
        return ProviderResponse(text="", finish_reason="stop", model=self.name)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            tools=True, vision=self._vision, streaming=False, local=True,
            cost_tier=0,
        )

    # -- test conveniences ----------------------------------------------------

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def reset(self) -> None:
        self._i = 0
        self.calls.clear()
