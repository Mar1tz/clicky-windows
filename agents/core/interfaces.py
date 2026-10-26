"""Abstract interfaces every agent package implements or consumes.

Ownership of implementations:
    BaseTool / ToolRegistry        agents/tools         (worker 1)
    AgentProvider / ProviderRouter agents/providers     (worker 2)
    BaseWorker                     agents/workers       (worker 2)
    IntentClassifier               agents/understanding (worker 2)
    MemoryStore                    agents/memory        (worker 3)
    Planner                        agents/planning      (worker 3)
    Verifier                       agents/verification  (worker 3)
    TaskStore                      agents/persistence   (worker 3)

The runtime (agents/manager) builds an AgentContext per task and hands it
to the planner, worker and verifier. Everything is asyncio; everything
runs on the agent runtime's loop (a daemon thread), never the GUI thread.
"""
from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from .events import EventBus
from .models import (
    AgentSpec, ApprovalRequest, ConnectedAccount, EventKind, MemoryEntry, Plan,
    PermissionPolicy, ProviderCapabilities, ProviderMessage, ProviderResponse,
    Step, Task, TaskKind, ToolCall, ToolResult, ToolSpec, VerificationResult,
    AgentState, Event,
)


# -- Run control (pause / resume / cancel) ------------------------------------

class RunControl:
    """Cooperative pause/cancel shared by everything working on one task.
    Workers call `await control.checkpoint()` between tool calls."""

    def __init__(self) -> None:
        self._resume = asyncio.Event()
        self._resume.set()
        self._cancelled = False

    def pause(self) -> None:
        self._resume.clear()

    def resume(self) -> None:
        self._resume.set()

    def cancel(self) -> None:
        self._cancelled = True
        self._resume.set()          # unblock anyone waiting on pause

    @property
    def paused(self) -> bool:
        return not self._resume.is_set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    async def checkpoint(self) -> None:
        """Block while paused; raise CancelledError if cancelled."""
        if self._cancelled:
            raise asyncio.CancelledError("task cancelled")
        await self._resume.wait()
        if self._cancelled:
            raise asyncio.CancelledError("task cancelled")


# -- Approval ----------------------------------------------------------------

Approver = Callable[[ApprovalRequest], Awaitable[bool]]


async def auto_approve(_req: ApprovalRequest) -> bool:
    return True


async def auto_deny(_req: ApprovalRequest) -> bool:
    return False


class PermissionDenied(Exception):
    def __init__(self, call: ToolCall, reason: str):
        super().__init__(f"{call.tool_name}: {reason}")
        self.call = call
        self.reason = reason


# -- Contexts ----------------------------------------------------------------

@dataclass
class ToolContext:
    """What a tool sees when it runs. Built by AgentContext.tool_context()."""
    workspace: str
    task_id: Optional[str] = None
    agent_id: Optional[str] = None
    events: Optional[EventBus] = None
    policy: PermissionPolicy = field(default_factory=PermissionPolicy)
    approver: Approver = auto_approve
    control: Optional[RunControl] = None
    dry_run: bool = False
    extras: dict[str, Any] = field(default_factory=dict)

    def resolve_path(self, path: str) -> str:
        """Absolute path for `path`, relative paths anchored at workspace."""
        p = path if os.path.isabs(path) else os.path.join(self.workspace, path)
        return os.path.normpath(os.path.abspath(p))

    def is_inside_workspace(self, path: str) -> bool:
        roots = self.policy.workspace_roots or [self.workspace]
        target = self.resolve_path(path)
        for root in roots:
            r = os.path.normpath(os.path.abspath(root))
            try:
                if os.path.commonpath([r, target]) == r:
                    return True
            except ValueError:      # different drives on Windows
                continue
        return False

    def log(self, message: str, level: str = "info", **data: Any) -> None:
        if self.events:
            self.events.emit(EventKind.LOG, message, task_id=self.task_id,
                             agent_id=self.agent_id, data=data, level=level)


@dataclass
class AgentContext:
    """Everything the planner / worker / verifier need for one task."""
    task: Task
    agent: AgentSpec
    tools: "ToolRegistry"
    provider: "AgentProvider"
    events: EventBus
    control: RunControl
    memory: Optional["MemoryStore"] = None
    approver: Approver = auto_approve
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def workspace(self) -> str:
        return self.task.workspace

    @property
    def policy(self) -> PermissionPolicy:
        return self.agent.permissions

    def tool_context(self) -> ToolContext:
        return ToolContext(
            workspace=self.task.workspace, task_id=self.task.id, agent_id=self.agent.id,
            events=self.events, policy=self.agent.permissions, approver=self.approver,
            control=self.control,
            extras={"goal": self.task.goal},     # what the job may touch (e.g. a document it names)
        )

    def progress(self, action: str, fraction: Optional[float] = None) -> None:
        """Update task.current_action (and progress) and broadcast it."""
        self.task.current_action = action
        if fraction is not None:
            self.task.progress = max(0.0, min(1.0, fraction))
        self.events.emit(EventKind.TASK_PROGRESS, action, task_id=self.task.id,
                         agent_id=self.agent.id,
                         data={"progress": self.task.progress, "current_action": action})

    def log(self, message: str, level: str = "info", **data: Any) -> Event:
        return self.events.emit(EventKind.LOG, message, task_id=self.task.id,
                                agent_id=self.agent.id, data=data, level=level)


# -- Tools -------------------------------------------------------------------

class BaseTool(ABC):
    """One capability. Subclasses set `spec` and implement run().

    run() must never raise for expected failures (missing file, non-zero
    exit) - return ToolResult.failure() so the LLM can react. Raise only
    for programmer errors.
    """
    spec: ToolSpec

    @abstractmethod
    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult: ...

    @property
    def name(self) -> str:
        return self.spec.name


class ToolRegistry(ABC):
    """Holds tools, publishes their specs, executes calls with policy
    enforcement. `execute` is the ONLY path workers use to run a tool."""

    @abstractmethod
    def register(self, tool: BaseTool) -> None: ...

    @abstractmethod
    def get(self, name: str) -> BaseTool: ...

    @abstractmethod
    def names(self) -> list[str]: ...

    @abstractmethod
    def specs(self, names: Optional[list[str]] = None) -> list[ToolSpec]: ...

    @abstractmethod
    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolResult:
        """Validate args against the spec's JSON schema, enforce
        ctx.policy (asking ctx.approver when the call exceeds it), run the
        tool, time it, and publish TOOL_CALL / TOOL_RESULT events. A denied
        call returns ToolResult.failure(error="permission denied: ...")."""
        ...


# -- Providers ---------------------------------------------------------------

class AgentProvider(ABC):
    """A tool-calling LLM. Distinct from ai/base_provider.BaseLLMProvider
    (chat streaming); Phase 2 adapters will wrap the existing Gemini code."""
    name: str = "base"

    @abstractmethod
    async def complete(
        self,
        messages: list[ProviderMessage],
        tools: Optional[list[ToolSpec]] = None,
        *,
        system: str = "",
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> ProviderResponse: ...

    @abstractmethod
    def capabilities(self) -> ProviderCapabilities: ...

    async def health_check(self) -> bool:
        return True


class ProviderRouter(ABC):
    """Chooses a provider per TaskKind from the user's connected accounts."""

    @abstractmethod
    def register(self, provider: AgentProvider, *, kinds: Optional[list[TaskKind]] = None,
                 priority: int = 0) -> None: ...

    @abstractmethod
    def pick(self, kind: TaskKind, *, hint: Optional[str] = None,
             needs_vision: bool = False) -> AgentProvider: ...

    @abstractmethod
    def providers(self) -> list[AgentProvider]: ...

    @abstractmethod
    def accounts(self) -> list[ConnectedAccount]: ...


# -- Workers -----------------------------------------------------------------

class BaseWorker(ABC):
    """Executes ONE step of a plan by looping provider <-> tools.

    Contract for execute_step:
      * set step.status RUNNING at start, DONE or FAILED at end
      * append every ToolCall/ToolResult it ran to step.tool_calls/results
      * fill step.summary (what happened) or step.error
      * call `await ctx.control.checkpoint()` before every tool call
      * never raise except asyncio.CancelledError
      * stop after `max_iterations` provider round-trips (mark FAILED)
    """
    worker_type: str = "base"
    default_tools: list[str] = []
    max_iterations: int = 12

    @abstractmethod
    async def execute_step(self, step: Step, ctx: AgentContext) -> Step: ...

    def system_prompt(self, ctx: AgentContext) -> str:
        return (f"You are {ctx.agent.name}, an autonomous {self.worker_type} agent. "
                f"Goal: {ctx.agent.goal}. Work inside {ctx.workspace}. "
                "Use tools to act; never claim an action you did not perform.")


# -- Understanding -----------------------------------------------------------

@dataclass
class Intent:
    """Result of classifying a user utterance."""
    is_task: bool
    confidence: float = 0.0
    title: str = ""
    goal: str = ""
    kind: TaskKind = TaskKind.GENERAL
    worker_type: str = "coding"
    workspace_hint: Optional[str] = None   # "MyApp" in "create a folder called MyApp"
    reply: str = ""                        # what to say to the user right now
    # Whatever routing attaches ({"skill": id, "workspace", ...}); copied
    # onto task.metadata.
    metadata: dict[str, Any] = field(default_factory=dict)


class IntentClassifier(ABC):
    @abstractmethod
    async def classify(self, text: str, *, context: Optional[dict[str, Any]] = None) -> Intent: ...


# -- Memory ------------------------------------------------------------------

class MemoryStore(ABC):
    @abstractmethod
    def add(self, entry: MemoryEntry) -> None: ...

    @abstractmethod
    def query(self, scope: str, *, kind: Optional[str] = None,
              tags: Optional[list[str]] = None, text: Optional[str] = None,
              limit: int = 50) -> list[MemoryEntry]: ...

    @abstractmethod
    def project_summary(self, workspace: str, *, max_chars: int = 2000) -> str:
        """Compact text block for prompts: framework, done, current, errors."""
        ...

    @abstractmethod
    def clear(self, scope: str) -> None: ...

    def delete(self, entry_id: str) -> bool:
        """EN-14: forget one memory - it, and everything it was indexed
        under. False when there was no such memory (or no such ability)."""
        return False

    # convenience scopes
    @staticmethod
    def project_scope(workspace: str) -> str:
        return f"project:{os.path.normcase(os.path.abspath(workspace))}"

    @staticmethod
    def agent_scope(agent_id: str) -> str:
        return f"agent:{agent_id}"


# -- Planning / verification -------------------------------------------------

class Planner(ABC):
    @abstractmethod
    async def plan(self, ctx: AgentContext) -> Plan:
        """Turn ctx.task.goal into ordered Steps. May pre-fill tool_calls
        for deterministic steps. Must publish nothing - the runtime does."""
        ...

    @abstractmethod
    async def replan(self, ctx: AgentContext, plan: Plan, failure: str) -> Plan:
        """Given a failed step, produce a revised plan for the remaining work."""
        ...


class Verifier(ABC):
    @abstractmethod
    async def verify_step(self, step: Step, ctx: AgentContext) -> VerificationResult:
        """Check the step's claimed effects against reality (fs, git, process)."""
        ...

    @abstractmethod
    async def verify_task(self, ctx: AgentContext) -> VerificationResult:
        """Final acceptance check for the whole task."""
        ...


# -- Persistence -------------------------------------------------------------

class TaskStore(ABC):
    """Durable history. Implementations subscribe to the EventBus to
    append events; the runtime calls save_* on state changes."""

    @abstractmethod
    def save_task(self, task: Task) -> None: ...

    @abstractmethod
    def get_task(self, task_id: str) -> Optional[Task]: ...

    @abstractmethod
    def list_tasks(self, *, status: Optional[str] = None, limit: int = 100) -> list[Task]: ...

    @abstractmethod
    def delete_task(self, task_id: str) -> None: ...

    @abstractmethod
    def save_plan(self, plan: Plan) -> None: ...

    @abstractmethod
    def get_plan(self, plan_id: str) -> Optional[Plan]: ...

    @abstractmethod
    def append_event(self, event: Event) -> None: ...

    @abstractmethod
    def events(self, task_id: str, *, limit: int = 1000,
               since: Optional[float] = None) -> list[Event]: ...

    @abstractmethod
    def save_agent(self, state: AgentState) -> None: ...

    @abstractmethod
    def list_agents(self) -> list[AgentState]: ...

    def close(self) -> None:
        pass
