"""Per-task limits: wall clock and token budget.

An autonomous agent that cannot finish should stop, not run until the
credit card does. Two limits, both 0 = unlimited:

  wall_clock_s   seconds from the task starting until it is failed
  max_tokens     total tokens (prompt + completion) across every model
                 call the task makes - planning, verification, the loop

`MeteredProvider` wraps whichever provider the router chose. Every call
is counted, a running total is published as a USAGE event (the panel
shows it), and a call that would start past either limit raises before
it is made. The orchestrator also checks between steps, so a task never
begins a step it has no budget for.

Tokens are what providers report; when a provider reports nothing (the
mock, or a gateway route that omits usage) the count stays honest at
zero rather than being estimated.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from agents.core import (
    AgentProvider, EventBus, EventKind, ProviderCapabilities, ProviderMessage,
    ProviderResponse, Task, ToolSpec, now,
)


class LimitExceeded(RuntimeError):
    """The task has used up a limit. Carries which one."""

    def __init__(self, which: str, detail: str):
        super().__init__(f"{which} limit exceeded: {detail}")
        self.which = which


@dataclass
class TaskLimits:
    wall_clock_s: float = 0.0
    max_tokens: int = 0
    max_cost_usd: float = 0.0              # OB-01: a job capped in money too (0 = no cap)

    @property
    def enabled(self) -> bool:
        return self.wall_clock_s > 0 or self.max_tokens > 0 or self.max_cost_usd > 0

    def deadline_for(self, started_at: float) -> Optional[float]:
        return started_at + self.wall_clock_s if self.wall_clock_s > 0 else None

    def check(self, task: Task, *, started_at: Optional[float] = None) -> None:
        """Raise LimitExceeded if the task is over either limit."""
        started = started_at if started_at is not None else task.created_at
        if self.wall_clock_s > 0:
            elapsed = time.time() - started
            if elapsed > self.wall_clock_s:
                raise LimitExceeded("wall clock",
                                    f"{elapsed:.0f}s elapsed, limit {self.wall_clock_s:.0f}s")
        if self.max_tokens > 0:
            used = int((task.metadata.get("usage") or {}).get("total_tokens", 0))
            if used > self.max_tokens:
                raise LimitExceeded("token budget", f"{used:,} tokens used, limit {self.max_tokens:,}")
        if self.max_cost_usd > 0:
            spent = float((task.metadata.get("usage") or {}).get("cost_usd", 0.0))
            if spent > self.max_cost_usd:
                raise LimitExceeded("cost", f"${spent:.2f} spent, limit ${self.max_cost_usd:.2f}")


def tokens_of(response: ProviderResponse) -> int:
    u = response.usage or {}
    total = u.get("total_tokens")
    if isinstance(total, int):
        return total
    return int(u.get("prompt_tokens", 0) or 0) + int(u.get("completion_tokens", 0) or 0)


class MeteredProvider(AgentProvider):
    """Counts every call against the task and stops at the limit."""

    def __init__(self, inner: AgentProvider, task: Task, events: EventBus,
                 limits: TaskLimits, *, started_at: Optional[float] = None):
        self.inner = inner
        self.task = task
        self.events = events
        self.limits = limits
        self.started_at = started_at if started_at is not None else now()
        self.name = getattr(inner, "name", "provider")

    async def complete(self, messages: list[ProviderMessage],
                       tools: Optional[list[ToolSpec]] = None, **kw) -> ProviderResponse:
        self.limits.check(self.task, started_at=self.started_at)
        began = time.monotonic()
        response = await self.inner.complete(messages, tools, **kw)
        u = response.usage or {}
        self.record(tokens_of(response), model=response.model or self.name,
                    prompt=int(u.get("prompt_tokens", 0) or 0), completion=int(u.get("completion_tokens", 0) or 0),
                    latency_ms=int((time.monotonic() - began) * 1000))
        return response

    def record(self, tokens: int, *, model: str = "", prompt: int = 0, completion: int = 0,
               latency_ms: int = 0) -> None:
        """Count one call: tokens, and (OB-01) what it cost - in and out
        priced apart when the provider says, else all of it at the output
        rate, so a cost is never shown lower than it was."""
        from agents.telemetry import credits, price_usd
        usage = self.task.metadata.setdefault("usage", {"total_tokens": 0, "calls": 0})
        usage["total_tokens"] = int(usage.get("total_tokens", 0)) + max(0, tokens)
        usage["calls"] = int(usage.get("calls", 0)) + 1
        cost = price_usd(model, prompt, completion) if (prompt or completion) else price_usd(model, 0, tokens)
        usage["cost_usd"] = round(float(usage.get("cost_usd", 0.0)) + cost, 6)
        self.events.emit(
            EventKind.USAGE, f"{usage['total_tokens']:,} tokens",
            task_id=self.task.id, agent_id=self.task.agent_id,
            data={"tokens": usage["total_tokens"], "delta": tokens,
                  "calls": usage["calls"], "model": model,
                  "budget": self.limits.max_tokens or None,
                  "prompt_delta": prompt, "completion_delta": completion, "latency_ms": latency_ms,
                  "cost_usd": usage["cost_usd"], "credits": credits(usage["cost_usd"])},
        )

    def capabilities(self) -> ProviderCapabilities:
        return self.inner.capabilities()

    async def health_check(self) -> bool:
        return await self.inner.health_check()

    def __getattr__(self, name):
        # Anything else (candidates(), health, aclose) is the inner's.
        return getattr(self.inner, name)
