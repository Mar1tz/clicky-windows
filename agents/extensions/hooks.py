"""The shapes an extension hands to the platform.

Everything here is generic. An accounting pack, a legal pack and an HR
pack all describe themselves with these same types; nothing in this file
knows what a ledger or a contract is. If a pack needs a shape that is
not here, the shape belongs here (for every pack), not in the pack.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Pattern

from agents.core import AgentSpec, PermissionPolicy, RiskLevel, Step, Task, TaskKind


# -- knowledge -------------------------------------------------------------------

@dataclass
class Skill:
    """A named block of domain guidance injected into prompts.

    Only while the extension is enabled, and only for tasks that belong
    to it (or whose worker type it owns): a coding task should never be
    told about accruals."""
    id: str
    title: str
    text: str
    always: bool = False              # inject into every task of the pack, not just matching roles
    roles: list[str] = field(default_factory=list)   # template ids; [] = every role


# -- agents ------------------------------------------------------------------------

@dataclass
class AgentTemplate:
    """A reusable specialist: what it is for, what it may touch, when it
    runs, what it remembers and what it hands back.

    `spec()` turns it into the core's AgentSpec, so a template-made agent
    runs through exactly the same manager, orchestrator and approval path
    as every other agent. Templates add meaning, never a second runtime.
    """
    id: str
    name: str
    mission: str
    worker_type: str
    tools: list[str]
    max_auto_risk: RiskLevel = RiskLevel.LOW
    triggers: list["Trigger"] = field(default_factory=list)
    memory_schema: dict[str, str] = field(default_factory=dict)   # kind -> what is remembered
    default_workflow: str = ""
    artifacts: list[str] = field(default_factory=list)
    handles: list[str] = field(default_factory=list)              # the "Handles:" bullet list
    setup_questions: list[str] = field(default_factory=list)      # what the wizard asks
    extension_id: str = ""

    def spec(self, goal: str, *, workspace: str = "", name: str = "",
             max_auto_risk: Optional[RiskLevel] = None) -> AgentSpec:
        return AgentSpec(
            name=name or self.name, goal=goal, worker_type=self.worker_type,
            tools=list(self.tools),
            permissions=PermissionPolicy(max_auto_risk=max_auto_risk or self.max_auto_risk,
                                         workspace_roots=[workspace] if workspace else []),
        )

    def describe(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "mission": self.mission,
                "handles": list(self.handles), "tools": list(self.tools),
                "artifacts": list(self.artifacts), "default_workflow": self.default_workflow,
                "triggers": [t.describe() for t in self.triggers]}


# -- triggers and workflows ---------------------------------------------------------

@dataclass
class Trigger:
    """When a workflow runs without being asked. See agents/manager/scheduler."""
    kind: str = "manual"             # manual | schedule | folder

    def describe(self) -> str:
        return self.kind


@dataclass
class Schedule(Trigger):
    """every: daily | weekdays | weekly | monthly. `at` is local HH:MM.
    weekly uses `weekday` (0=Mon); monthly uses `day` (1-28, or -1 = last
    working day's calendar equivalent: the last day of the month)."""
    kind: str = "schedule"
    every: str = "daily"
    at: str = "08:00"
    weekday: int = 0
    day: int = 1

    def describe(self) -> str:
        if self.every == "weekly":
            return f"weekly on {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][self.weekday % 7]} {self.at}"
        if self.every == "monthly":
            return f"monthly on day {'last' if self.day < 0 else self.day} {self.at}"
        return f"{self.every} at {self.at}"


@dataclass
class FolderWatch(Trigger):
    """Fire when new files matching `patterns` appear under `path` (a
    setting name like "inbox_folder" is resolved from the pack's settings)."""
    kind: str = "folder"
    path: str = ""
    patterns: list[str] = field(default_factory=lambda: ["*.pdf"])
    setting: str = ""

    def describe(self) -> str:
        return f"new {', '.join(self.patterns)} in {self.path or self.setting or 'a folder'}"


@dataclass
class Workflow:
    """A named, checklist-shaped job. `steps(metadata)` is deterministic:
    every check is a Step, most with a scripted tool call, so the model
    cannot quietly skip one. Steps that need judgement are left open (no
    tool calls) and the worker's ReAct loop handles them.

    The builder gets the task's metadata (client, period, files that
    triggered it) rather than the Task, because it runs inside the
    planner's template matching, which only sees that much."""
    id: str
    name: str
    description: str
    template_id: str
    trigger: Trigger = field(default_factory=Trigger)
    build_steps: Optional[Callable[[dict[str, Any]], list[Step]]] = None
    goal: str = ""                     # the goal text a scheduled run submits
    extension_id: str = ""

    def steps(self, metadata: dict[str, Any]) -> list[Step]:
        return list(self.build_steps(metadata)) if self.build_steps else []


# -- understanding -------------------------------------------------------------------

@dataclass
class IntentRule:
    """A phrase that means "this is a job for the pack".

    Checked before the general classifier, so "prepare month-end for ABC"
    reaches the month-end agent instead of being guessed at. `extract`
    pulls named values (client, period) out of the match."""
    pattern: Pattern[str] | str
    template_id: str
    workflow_id: str = ""
    kind: TaskKind = TaskKind.GENERAL
    reply: str = ""
    extension_id: str = ""
    # Pulls named values (client, period) out of the whole utterance; runs
    # only when the pattern matched. Its keys win over the pattern's groups.
    extract: Optional[Callable[[str], dict[str, str]]] = None

    def __post_init__(self) -> None:
        if isinstance(self.pattern, str):
            self.pattern = re.compile(self.pattern, re.I)

    def match(self, text: str) -> Optional[dict[str, str]]:
        m = self.pattern.search(text or "")   # type: ignore[union-attr]
        if not m:
            return None
        found = {k: (v or "").strip(" .,!?\"'") for k, v in m.groupdict().items() if v}
        if self.extract is not None:
            found.update({k: v for k, v in self.extract(text).items() if v})
        return found


# Context lines for a task's prompts: (task) -> list[str]
ContextProvider = Callable[[Task], list[str]]


# -- permissions --------------------------------------------------------------------

@dataclass
class PermissionProfile:
    """What the pack asks of the permission system. `levels` names the
    pack's own action levels and the core RiskLevel each maps to; `sensitive`
    tools always ask, cannot be granted for a task, and need a typed word."""
    levels: dict[str, RiskLevel] = field(default_factory=dict)
    tool_levels: dict[str, str] = field(default_factory=dict)
    sensitive: list[str] = field(default_factory=list)
    always_ask: list[str] = field(default_factory=list)
    notes: str = ""


# -- integrations, documents, UI ----------------------------------------------------------

INTEGRATION_TIERS = ("api", "oauth", "file", "browser", "uia", "vision")


@dataclass
class IntegrationSpec:
    """Something the pack can connect to. `tier` orders the fallbacks: an
    API always beats screen-clicking. `available` says whether this build
    can actually do it (Phase-2 connectors are listed but unavailable)."""
    id: str
    name: str
    tier: str = "api"
    available: bool = False
    needs: list[str] = field(default_factory=list)       # env/setting names
    notes: str = ""

    @property
    def rank(self) -> int:
        return INTEGRATION_TIERS.index(self.tier) if self.tier in INTEGRATION_TIERS else 99


@dataclass
class DocumentType:
    id: str
    name: str
    keywords: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)
    folder: str = ""                                     # where file_document puts it


@dataclass
class DashboardModule:
    """A panel the pack adds to the UI. `factory(manager, extension, parent)`
    returns a QWidget; it is only called by the UI layer, so packs can keep
    their PyQt imports inside the factory."""
    id: str
    title: str
    factory: Callable[..., Any]
    order: int = 100
    extension_id: str = ""
