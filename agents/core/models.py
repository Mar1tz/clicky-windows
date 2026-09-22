"""Data model shared by every agent package.

Plain dataclasses on purpose: cheap to construct, trivially serialisable
(see to_dict / from_dict), no framework lock-in. Every id is a short hex
string from new_id(); every timestamp is time.time().

Rules for changing this file (manager-owned):
  * add fields with defaults - never remove or rename without a sweep
  * enums are str subclasses so they JSON-serialise as their value
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from enum import Enum
from typing import Any, Optional


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def now() -> float:
    return time.time()


# -- Enums -------------------------------------------------------------------

class TaskStatus(str, Enum):
    PENDING = "pending"                    # created, not yet picked up
    PLANNING = "planning"                  # planner is producing steps
    WAITING_APPROVAL = "waiting_approval"  # plan or risky tool call needs a human
    RUNNING = "running"
    PAUSED = "paused"
    VERIFYING = "verifying"                # final verification pass
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class AgentStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


class RiskLevel(str, Enum):
    """How much damage a tool call can do. Drives auto-approval.

    SAFE    read-only (read_file, list_dir, git diff)
    LOW     writes inside the task workspace (create_file, edit_file)
    MEDIUM  runs processes / installs packages / git commit
    HIGH    deletes, git push, computer control, anything outside workspace
    """
    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        return _RISK_RANK[self]


_RISK_RANK = {RiskLevel.SAFE: 0, RiskLevel.LOW: 1, RiskLevel.MEDIUM: 2, RiskLevel.HIGH: 3}


class TaskKind(str, Enum):
    """What kind of work a task (or a single provider call) is. The
    ProviderRouter maps these to providers: coding->Claude, vision->Gemini,
    fast->local, etc."""
    CODING = "coding"
    RESEARCH = "research"
    VISION = "vision"
    BROWSER = "browser"
    FILE = "file"
    TERMINAL = "terminal"
    COMPUTER = "computer"
    FAST = "fast"
    GENERAL = "general"


class EventKind(str, Enum):
    TASK_CREATED = "task.created"
    TASK_STATUS = "task.status"              # data: {"status": TaskStatus}
    TASK_PROGRESS = "task.progress"          # data: {"progress": float, "current_action": str}
    PLAN_CREATED = "plan.created"            # data: {"plan": Plan.to_dict()}
    STEP_STARTED = "step.started"            # data: {"step": Step.to_dict(), "index": int, "total": int}
    STEP_DONE = "step.done"
    STEP_FAILED = "step.failed"
    TOOL_CALL = "tool.call"                  # data: {"call": ToolCall.to_dict()}
    TOOL_RESULT = "tool.result"              # data: {"result": ToolResult.to_dict()}
    VERIFICATION = "verification"            # data: {"result": VerificationResult.to_dict()}
    APPROVAL_REQUIRED = "approval.required"  # data: {"request": ApprovalRequest.to_dict()}
    APPROVAL_RESOLVED = "approval.resolved"
    AGENT_STATUS = "agent.status"            # data: {"state": AgentState.to_dict()}
    USAGE = "usage"                          # data: {"tokens": int, "delta": int, "budget": int|None}
    FINDING = "finding"                      # data: {"finding": Finding.to_dict()}
    LOG = "log"
    ERROR = "error"


# -- Serialisation helpers ---------------------------------------------------

class _Serialisable:
    """Mixin: dataclass <-> dict, recursing into nested dataclasses/enums."""

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]):
        kwargs = {}
        for f in fields(cls):  # type: ignore[arg-type]
            if f.name not in data:
                continue
            kwargs[f.name] = _coerce(f.type, data[f.name])
        return cls(**kwargs)  # type: ignore[call-arg]


def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, Enum):
        return obj.value
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _to_jsonable(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_to_jsonable(v) for v in obj]
    return obj


# Field-type name -> constructor for nested types that from_dict must rebuild.
# Populated at the bottom of this module once the classes exist.
_NESTED: dict[str, Any] = {}


def _coerce(type_hint: Any, value: Any) -> Any:
    hint = str(type_hint)
    if value is None:
        return None
    if hint in _NESTED:
        return _NESTED[hint](value)
    if hint.startswith("list[") and hint[5:-1] in _NESTED:
        return [_NESTED[hint[5:-1]](v) for v in value]
    if hint.startswith("Optional[") and hint[9:-1] in _NESTED:
        return _NESTED[hint[9:-1]](value)
    return value


# -- Tools -------------------------------------------------------------------

@dataclass
class ToolSpec(_Serialisable):
    """What the registry publishes and what providers see as a function
    definition. `parameters` is a JSON-schema object."""
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    risk: RiskLevel = RiskLevel.LOW
    category: str = "general"              # file | terminal | git | browser | computer | ...


@dataclass
class ToolCall(_Serialisable):
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=new_id)


@dataclass
class ToolResult(_Serialisable):
    call_id: str
    tool_name: str
    ok: bool
    output: str = ""                       # human/LLM-readable text
    data: dict[str, Any] = field(default_factory=dict)  # structured extras
    error: str = ""
    duration_ms: int = 0

    @classmethod
    def success(cls, call: "ToolCall", output: str = "", **data: Any) -> "ToolResult":
        return cls(call_id=call.id, tool_name=call.tool_name, ok=True, output=output, data=data)

    @classmethod
    def failure(cls, call: "ToolCall", error: str, output: str = "") -> "ToolResult":
        return cls(call_id=call.id, tool_name=call.tool_name, ok=False, output=output, error=error)


# -- Permissions -------------------------------------------------------------

@dataclass
class PermissionPolicy(_Serialisable):
    """What an agent may do without asking.

    A call is auto-approved when spec.risk.rank <= max_auto_risk.rank, the
    tool is not in denied_tools, and (if allowed_tools is set) it is in
    allowed_tools. Anything else raises an ApprovalRequest. File tools must
    additionally keep paths under workspace_roots.

    The four fields answer four different questions, and the order they
    are checked in is the order of authority:

      denied_tools    never, whatever else says - a hard veto
      allowed_tools   a whitelist FILTER: when set, nothing outside it runs
      always_allow    a user's standing "yes" for one tool, which lifts it
                      over max_auto_risk (but never over denied_tools)
      max_auto_risk   the ceiling everything else is judged against

    always_allow exists because "Always allow delete_path for this agent"
    cannot be expressed by adding the tool to allowed_tools: that list
    only narrows, so the risk ceiling would still block the call and the
    user would be asked again on the very next step.
    """
    max_auto_risk: RiskLevel = RiskLevel.MEDIUM
    allowed_tools: Optional[list[str]] = None       # None = all registered
    denied_tools: list[str] = field(default_factory=list)
    workspace_roots: list[str] = field(default_factory=list)
    always_allow: list[str] = field(default_factory=list)

    def allows(self, spec: "ToolSpec") -> bool:
        if spec.name in self.denied_tools:
            return False
        if self.allowed_tools is not None and spec.name not in self.allowed_tools:
            return False
        if spec.name in self.always_allow:
            return True
        return spec.risk.rank <= self.max_auto_risk.rank

    def allow_always(self, tool_name: str) -> None:
        """Record the user's standing approval for one tool."""
        if tool_name and tool_name not in self.always_allow:
            self.always_allow.append(tool_name)


@dataclass
class ApprovalRequest(_Serialisable):
    """Raised (as an event) when a call exceeds the policy. The runtime
    parks the task in WAITING_APPROVAL until resolve(id, approved)."""
    task_id: str
    agent_id: str
    call: ToolCall
    reason: str
    id: str = field(default_factory=new_id)
    created_at: float = field(default_factory=now)
    approved: Optional[bool] = None
    # A SENSITIVE call (release a payment, submit a return) cannot be
    # approved by a per-task grant or a voice "yes": the person must type
    # `confirm_word` in the dialog. Set by the registry from the tool.
    sensitive: bool = False
    confirm_word: str = ""


@dataclass
class Finding(_Serialisable):
    """Something a person should look at. The unit of exception-driven work.

    Agents that check things (a reconciliation, a duplicate scan, a
    month-end checklist) run dozens of checks; what the user wants back
    is the handful that need a human. A tool reports those by returning
    `data["findings"] = [Finding(...).to_dict(), ...]`; the worker lifts
    them onto the task (`task.metadata["findings"]`) and emits FINDING,
    and the final result lists them under "Needs review".

    Generic on purpose: an accounting check, a legal clause review and a
    failing test all produce the same shape.
    """
    title: str
    detail: str = ""
    severity: str = "review"               # info | review | high
    entity: str = ""                       # what it is about: "INV-0042", "Bank 1000"
    action: str = ""                       # suggested next step
    source: str = ""                       # tool that raised it
    id: str = field(default_factory=new_id)

    @property
    def key(self) -> str:
        return f"{self.title.strip().lower()}|{self.entity.strip().lower()}"


# -- Plans & steps -----------------------------------------------------------

@dataclass
class Step(_Serialisable):
    title: str
    description: str = ""
    id: str = field(default_factory=new_id)
    status: StepStatus = StepStatus.PENDING
    # Planner may pre-fill calls (scripted step); workers append what they ran.
    tool_calls: list[ToolCall] = field(default_factory=list)
    results: list[ToolResult] = field(default_factory=list)
    summary: str = ""
    error: str = ""
    started_at: Optional[float] = None
    finished_at: Optional[float] = None


@dataclass
class Plan(_Serialisable):
    task_id: str
    steps: list[Step] = field(default_factory=list)
    rationale: str = ""
    id: str = field(default_factory=new_id)
    created_at: float = field(default_factory=now)
    approved: bool = False


# -- Tasks -------------------------------------------------------------------

@dataclass
class Task(_Serialisable):
    title: str
    goal: str
    workspace: str                         # absolute dir the agent works in
    kind: TaskKind = TaskKind.GENERAL
    worker_type: str = "coding"            # key into the worker registry
    id: str = field(default_factory=new_id)
    status: TaskStatus = TaskStatus.PENDING
    steps: list[Step] = field(default_factory=list)
    plan_id: Optional[str] = None
    agent_id: Optional[str] = None
    progress: float = 0.0                  # 0..1
    current_action: str = ""
    result: str = ""
    error: str = ""
    created_at: float = field(default_factory=now)
    updated_at: float = field(default_factory=now)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def step_index(self) -> int:
        """Index of the first unfinished step (== len(steps) when all done)."""
        for i, s in enumerate(self.steps):
            if s.status in (StepStatus.PENDING, StepStatus.RUNNING):
                return i
        return len(self.steps)


# -- Agents ------------------------------------------------------------------

@dataclass
class AgentSpec(_Serialisable):
    """The recipe. AgentManager.create_agent() turns one into a live agent."""
    name: str                              # "Application Builder"
    goal: str
    worker_type: str = "coding"
    tools: list[str] = field(default_factory=list)   # tool names; [] = worker default set
    permissions: PermissionPolicy = field(default_factory=PermissionPolicy)
    memory_enabled: bool = True
    provider_hint: Optional[str] = None    # force a provider name; None = router decides
    id: str = field(default_factory=new_id)


@dataclass
class AgentState(_Serialisable):
    """Live view the UI renders. Emitted on every change via AGENT_STATUS."""
    spec: AgentSpec
    status: AgentStatus = AgentStatus.IDLE
    current_task_id: Optional[str] = None
    current_task_title: str = ""
    current_action: str = ""
    progress: float = 0.0
    step_index: int = 0
    step_total: int = 0
    started_at: Optional[float] = None
    last_error: str = ""
    tokens_used: int = 0                   # running total across the current task

    @property
    def id(self) -> str:
        return self.spec.id


# -- Verification ------------------------------------------------------------

@dataclass
class Check(_Serialisable):
    name: str
    passed: bool
    detail: str = ""


@dataclass
class VerificationResult(_Serialisable):
    ok: bool
    checks: list[Check] = field(default_factory=list)
    summary: str = ""

    @classmethod
    def passed(cls, summary: str = "ok", **checks: bool) -> "VerificationResult":
        return cls(ok=True, summary=summary, checks=[Check(k, v) for k, v in checks.items()])


# -- Events ------------------------------------------------------------------

@dataclass
class Event(_Serialisable):
    kind: EventKind
    message: str = ""
    task_id: Optional[str] = None
    agent_id: Optional[str] = None
    data: dict[str, Any] = field(default_factory=dict)
    level: str = "info"                    # info | warn | error
    id: str = field(default_factory=new_id)
    timestamp: float = field(default_factory=now)


# -- Provider I/O ------------------------------------------------------------

@dataclass
class ProviderMessage(_Serialisable):
    role: str                              # system | user | assistant | tool
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)   # assistant turns
    tool_call_id: Optional[str] = None     # tool turns: which call this answers
    images_b64: list[str] = field(default_factory=list)        # vision


@dataclass
class ProviderResponse(_Serialisable):
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"            # stop | tool_calls | length | error
    usage: dict[str, int] = field(default_factory=dict)
    model: str = ""


@dataclass
class ProviderCapabilities(_Serialisable):
    tools: bool = True
    vision: bool = False
    streaming: bool = False
    local: bool = False
    max_context: int = 128_000
    cost_tier: int = 2                     # 0 free/local ... 3 expensive


@dataclass
class ConnectedAccount(_Serialisable):
    """A user-connected AI account (settings -> Connected Accounts).

    `base_url` and `models` describe an OpenAI-compatible endpoint, which
    in practice is what a connected account is: a gateway fronting many
    vendors, a local server, or api.openai.com. `models` is an ordered
    preference chain, not a single choice, because any real endpoint has
    routes that go down and the provider falls through to the next one.

    No secret is ever stored here. The key is read at call time from
    `TORA_<PROVIDER>_API_KEY`.
    """
    provider: str                          # claude | gemini | openai | local | custom
    label: str = ""
    auth_kind: str = "api_key"             # api_key | oauth | subscription | none
    enabled: bool = True
    default_model: str = ""
    base_url: str = ""                     # OpenAI-compatible endpoint
    models: list[str] = field(default_factory=list)   # preference chain
    kinds: list[str] = field(default_factory=list)    # TaskKind values, [] = all
    id: str = field(default_factory=new_id)

    @property
    def env_key_name(self) -> str:
        return f"TORA_{self.provider.upper()}_API_KEY"

    @property
    def env_url_name(self) -> str:
        return f"TORA_{self.provider.upper()}_BASE_URL"


# -- Memory ------------------------------------------------------------------

@dataclass
class MemoryEntry(_Serialisable):
    scope: str                             # "project:<workspace>" | "agent:<id>" | "user"
    kind: str                              # task | decision | error | solution | preference | context
    content: str
    tags: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=new_id)
    created_at: float = field(default_factory=now)


# -- nested-type registry for from_dict --------------------------------------

def _enum(e: type) -> Any:
    return lambda v: e(v) if not isinstance(v, e) else v


def _dc(c: type) -> Any:
    return lambda v: c.from_dict(v) if isinstance(v, dict) else v


_NESTED.update({
    "TaskStatus": _enum(TaskStatus), "StepStatus": _enum(StepStatus),
    "AgentStatus": _enum(AgentStatus), "RiskLevel": _enum(RiskLevel),
    "TaskKind": _enum(TaskKind), "EventKind": _enum(EventKind),
    "ToolSpec": _dc(ToolSpec), "ToolCall": _dc(ToolCall), "ToolResult": _dc(ToolResult),
    "PermissionPolicy": _dc(PermissionPolicy), "ApprovalRequest": _dc(ApprovalRequest),
    "Step": _dc(Step), "Plan": _dc(Plan), "Task": _dc(Task),
    "AgentSpec": _dc(AgentSpec), "AgentState": _dc(AgentState),
    "Check": _dc(Check), "VerificationResult": _dc(VerificationResult),
    "Event": _dc(Event), "ProviderMessage": _dc(ProviderMessage),
    "ProviderResponse": _dc(ProviderResponse), "ProviderCapabilities": _dc(ProviderCapabilities),
    "ConnectedAccount": _dc(ConnectedAccount), "MemoryEntry": _dc(MemoryEntry),
})
