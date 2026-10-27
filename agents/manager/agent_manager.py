"""AgentManager: the front door to the agent system.

The GUI thread and the chat path talk only to this class. It owns the
runtime thread, the registries, the live agent table and the pending
approval table, and it turns user text into background tasks:

    mgr = AgentManager(tools=..., router=..., workers=..., planner=..., verifier=...)
    mgr.start()
    task = mgr.submit("Create a folder called MyApp and build a React foundation in it")
    ...                       # returns immediately; the task runs on the runtime thread
    mgr.events.subscribe(ui.on_event)

Every public method is safe to call from any thread.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from datetime import datetime
from typing import Any, Callable, Optional, Protocol, Union

from concurrent.futures import Future

from agents.core import (
    AgentContext, AgentProvider, AgentSpec, AgentState, AgentStatus, ApprovalRequest,
    BaseWorker, Event, EventBus, EventKind, Intent, IntentClassifier, MemoryStore,
    PermissionPolicy, Planner, ProviderRouter, RunControl, Task, TaskKind, TaskStatus,
    TaskStore, ToolCall, ToolRegistry, Unsubscribe, Verifier, now,
)

from .orchestrator import Orchestrator
from .runtime import AgentRuntime


# -- standing permissions, on disk ---------------------------------------------------
#
# Small enough for a JSON file, and it has to be readable by a person: the
# whole point of a permission is that you can see what you gave and take it
# back. TORA_GRANTS_FILE lets a test point it somewhere temporary.

def _grants_path() -> str:
    override = os.environ.get("TORA_GRANTS_FILE")
    if override:
        return override
    try:
        from config import data_dir
        return str(data_dir() / "desktop_grants.json")
    except Exception:  # noqa: BLE001 - config is optional for the agent package
        return ""


def _read_grants() -> set[str]:
    path = _grants_path()
    if not path or not os.path.exists(path):
        return set()
    try:
        import json
        with open(path, "r", encoding="utf-8") as handle:
            return {str(k) for k in json.load(handle) if str(k).strip()}
    except Exception:  # noqa: BLE001 - a corrupt file means "ask me again"
        log.warning("could not read %s; every action will be asked about", path)
        return set()


def _write_grants(keys: set[str]) -> None:
    path = _grants_path()
    if not path:
        return
    try:
        import json
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(sorted(keys), handle, indent=2)
    except Exception:  # noqa: BLE001 - never let a permission write kill an action
        log.warning("could not save standing permissions to %s", path, exc_info=True)

log = logging.getLogger("agents.manager")


def job_scorecard(task) -> str:
    """One line when a job ends (AG-04, the speed review of 17 Oct 2026): how
    long, how many steps and tool calls, and why it failed - 5 of the
    owner's 21 jobs failed and the reasons had to be dug out of agents.db."""
    steps = list(task.steps or [])
    done = sum(1 for s in steps if s.status == "done")       # StepStatus is a str enum
    calls = sum(len(s.tool_calls or []) for s in steps)
    failed = [s for s in steps if s.error]
    why = (failed[-1].error if failed else task.error or "")
    line = (f"{task.status.value}, {task.title!r} ({task.worker_type}), {time.time() - task.created_at:.0f}s, "
            f"{done} of {len(steps)} steps done, {calls} tool calls")
    return line + (f" - {why[:200]}" if why else "")


def _title(text: str) -> str:
    t = " ".join((text or "").split())
    return (t[:57] + "...") if len(t) > 60 else t


class WorkerLookup(Protocol):
    def get(self, worker_type: str) -> BaseWorker: ...


AGENT_NAMES: dict[TaskKind, str] = {
    TaskKind.CODING: "Application Builder",
    TaskKind.RESEARCH: "Research Agent",
    TaskKind.VISION: "Vision Agent",
    TaskKind.BROWSER: "Browser Agent",
    TaskKind.FILE: "File Agent",
    TaskKind.TERMINAL: "Terminal Agent",
    TaskKind.COMPUTER: "Computer Agent",
    TaskKind.FAST: "Quick Agent",
    TaskKind.GENERAL: "Assistant Agent",
}


class _Run:
    """Bookkeeping for one in-flight task."""
    __slots__ = ("task", "agent", "control", "future")

    def __init__(self, task: Task, agent: AgentState, control: RunControl):
        self.task = task
        self.agent = agent
        self.control = control
        self.future = None


class AgentManager:
    def __init__(
        self,
        *,
        tools: ToolRegistry,
        router: ProviderRouter,
        workers: WorkerLookup,
        planner: Planner,
        verifier: Verifier,
        workspace_root: str,
        store: Optional[TaskStore] = None,
        memory: Optional[MemoryStore] = None,
        classifier: Optional[IntentClassifier] = None,
        events: Optional[EventBus] = None,
        runtime: Optional[AgentRuntime] = None,
        default_policy: Optional[PermissionPolicy] = None,
        require_plan_approval: bool = False,
        max_replans: int = 1,
        max_concurrent: int = 0,
        classify_timeout: float = 15.0,
        decomposer: Optional["Decomposer"] = None,
        limits: Optional["TaskLimits"] = None,
        auto_approve: bool = False,
    ):
        self.tools = tools
        self.router = router
        self.workers = workers
        self.planner = planner
        self.verifier = verifier
        self.store = store
        self.memory = memory
        self.classifier = classifier
        self.workspace_root = os.path.abspath(workspace_root)
        self.events = events or EventBus()
        self.runtime = runtime or AgentRuntime()
        self.default_policy = default_policy or PermissionPolicy()
        # Settings > Approvals: every yes a single click could give is given
        # without asking. See _auto_approves for what never is.
        self.auto_approve = bool(auto_approve)
        from .limits import TaskLimits
        self.limits = limits or TaskLimits()
        # Plugged in by bootstrap (agents/skills, agents/manager/scheduler).
        # Both optional: the manager is complete without either.
        self.skills: Any = None
        self.scheduler: Any = None
        self.orchestrator = Orchestrator(
            planner=planner, verifier=verifier, store=store, memory=memory,
            require_plan_approval=require_plan_approval, max_replans=max_replans,
            limits=self.limits,
        )
        self.max_concurrent = max(0, int(max_concurrent))   # 0 = unlimited
        self.classify_timeout = classify_timeout
        self.decomposer = decomposer                         # None = never split
        self._lock = threading.RLock()
        self._active: set[str] = set()                  # task ids actually running
        self._queue: list[tuple] = []                   # (ctx, worker, run) waiting
        self._held: dict[str, str] = {}                 # OR-10: claim -> the task holding it
        self._queued: dict[str, set[str]] = {}          # OR-33: queue -> the tasks in it
        self._runs: dict[str, _Run] = {}                # task_id -> run
        self._agents: dict[str, AgentState] = {}        # agent_id -> state
        self._tasks: dict[str, Task] = {}               # task_id -> task (incl. finished)
        self._approvals: dict[str, tuple[ApprovalRequest, Any]] = {}
        # Desktop actions a person has waved through for good ("always allow
        # spotify.next"). Persisted: "always" meant "until you restart",
        # so every session asked again about the same track skip and the
        # dialog became something to click past rather than read.
        self._desktop_grants: set[str] = _read_grants()
        # OR-32: what jobs wait for - a time, a file, an answer, a yes.
        from .waitpoints import Waitpoints
        self.waitpoints = Waitpoints(self.events)
        self.waitpoints.on_wait = self._on_wait
        self.waitpoints.on_resume = self._on_wait_over
        self._grants: dict[str, set[str]] = {}          # task_id -> tools a human allowed for it
        self._store_unsub: Optional[Unsubscribe] = None
        self._events_unsub: Optional[Unsubscribe] = None

    # -- lifecycle -----------------------------------------------------------

    def start(self, *, recover: bool = True) -> "AgentManager":
        self.runtime.start()
        if self.store is not None and hasattr(self.store, "attach") and self._store_unsub is None:
            self._store_unsub = self.store.attach(self.events)  # type: ignore[attr-defined]
        if self._events_unsub is None:
            self._events_unsub = self.events.subscribe(self._track)
        if recover and self.store is not None:
            self.recover()
        if self.scheduler is not None:
            self.scheduler.start()
        return self

    # -- recovery ------------------------------------------------------------

    INTERRUPTED_NOTE = "interrupted by restart"
    # OR-30: a job HeyTora's stopping cut off carries on by itself - once - when
    # what it was doing may simply run again (OR-31) and it does not work on
    # the person's screen. Off for tests and offline runs (bootstrap turns it
    # on for the app); a second interruption pauses it, so a job that took
    # HeyTora down cannot loop it.
    auto_resume: bool = False
    AUTO_RESUMES = 1

    def recover(self) -> list[Task]:
        """Bring back tasks the previous process left unfinished.

        Anything that was in flight — planning, running, verifying, or
        parked on an approval — is set to PAUSED with a note, so the user
        sees it in the panel with a Resume button instead of it silently
        vanishing. Tasks are not restarted automatically: the process may
        have died *because* of that task, and re-running it unasked would
        loop. PENDING tasks (queued, never started) are recovered the same
        way; resuming one starts it from the top.
        """
        if self.store is None:
            return []
        recovered: list[Task] = []
        carry_on: list[Task] = []
        in_flight = {TaskStatus.PENDING, TaskStatus.PLANNING, TaskStatus.RUNNING,
                     TaskStatus.VERIFYING, TaskStatus.WAITING_APPROVAL, TaskStatus.PAUSED,
                     TaskStatus.NEEDS_ATTENTION, TaskStatus.RETRY_WAIT, TaskStatus.WAITING_USER}
        stored_agents = {a.id: a for a in self._safe_list_agents()}
        for task in self.store.list_tasks(limit=500):
            if task.status not in in_flight or task.id in self._tasks:
                continue
            unsettled = (self._unsettled_effect(task)
                         if task.status in (TaskStatus.RUNNING, TaskStatus.VERIFYING) else None)
            if unsettled is None and self._may_carry_on(task):
                carry_on.append(task)
            if unsettled is not None:
                # EN-07: the step died with a call in flight that could have
                # changed something - re-running it could do it twice.
                task.status = TaskStatus.NEEDS_ATTENTION
                task.updated_at = now()
                task.metadata["interrupted"] = self.INTERRUPTED_NOTE
                task.metadata["outcome_unknown"] = unsettled
                task.current_action = (f"Needs you — {unsettled['tool']} may have run before HeyTora stopped. "
                                       "Check it, then Resume or Stop.")
                self.store.save_task(task)
            elif task.status not in (TaskStatus.PAUSED, TaskStatus.NEEDS_ATTENTION, TaskStatus.WAITING_USER):
                task.status = TaskStatus.PAUSED
                task.updated_at = now()
                task.metadata["interrupted"] = self.INTERRUPTED_NOTE
                task.current_action = "Paused — " + self.INTERRUPTED_NOTE
                self.store.save_task(task)
            with self._lock:
                self._tasks[task.id] = task
            state = stored_agents.get(task.agent_id or "")
            if state is None:
                state = AgentState(spec=self._default_spec(task))
                state.spec.id = task.agent_id or state.spec.id
                task.agent_id = state.spec.id
            state.status = AgentStatus.PAUSED
            state.current_task_id, state.current_task_title = task.id, task.title
            state.current_action = task.current_action
            state.step_index, state.step_total = task.step_index, len(task.steps)
            state.progress = task.progress
            with self._lock:
                self._agents[state.id] = state
            self._emit_agent(state)
            self.events.emit(EventKind.TASK_STATUS, TaskStatus.PAUSED.value, task_id=task.id,
                             agent_id=task.agent_id,
                             data={"status": TaskStatus.PAUSED.value, "recovered": True})
            recovered.append(task)
        if recovered:
            log.info("recovered %d unfinished task(s) from a previous run", len(recovered))
        for task in carry_on:
            task.metadata["auto_resumed"] = int(task.metadata.get("auto_resumed") or 0) + 1
            if self._resume_recovered(task):
                log.info("task %s (%s) carries on where it stopped", task.id, task.title)
                self.events.emit(EventKind.LOG, f"Carrying on where it stopped: {task.title}",
                                 task_id=task.id, agent_id=task.agent_id, data={"auto_resumed": True})
        return recovered

    def _may_carry_on(self, task: Task) -> bool:
        """OR-30: may this interrupted job go on without being asked?"""
        if not self.auto_resume:
            return False
        if task.status not in (TaskStatus.PENDING, TaskStatus.PLANNING, TaskStatus.RUNNING,
                               TaskStatus.VERIFYING, TaskStatus.RETRY_WAIT):
            return False                              # paused, or waiting on the person: theirs to resume
        if task.kind in (TaskKind.COMPUTER, TaskKind.BROWSER) or (task.metadata or {}).get("takeover"):
            return False                              # never takes their screen back by surprise
        if (task.metadata or {}).get("project") or (task.metadata or {}).get("parent_id"):
            return False                              # a project's lead restarts its children itself
        return int((task.metadata or {}).get("auto_resumed") or 0) < self.AUTO_RESUMES

    def _safe_list_agents(self) -> list[AgentState]:
        try:
            return self.store.list_agents() if self.store else []
        except Exception:  # noqa: BLE001 - a corrupt row must not block startup
            log.exception("could not read stored agents")
            return []

    def _unsettled_effect(self, task: Task) -> Optional[dict]:
        """The call the running step started and never heard back from, when
        its tool is not safe to run again (OR-31, ToolSpec.safe_to_replay):
        run a command, push, click, type. None when every such call
        answered, or the unanswered one may simply run again. The TOOL_CALL
        event, committed before the tool runs, is the record of intent (EN-06)."""
        from agents.core import StepStatus
        step = next((s for s in task.steps if s.status is StepStatus.RUNNING), None)
        if step is None or self.store is None:
            return None
        try:
            events = self.store.events(task.id, since=(step.started_at or task.created_at) - 1, limit=20000)
        except Exception:  # noqa: BLE001 - no history: the old behaviour (paused)
            return None
        calls: dict[str, dict] = {}
        answered: set = set()
        for ev in events:
            if ev.kind is EventKind.TOOL_CALL:
                call = (ev.data or {}).get("call") or {}
                calls[call.get("id")] = call | {"at": ev.timestamp}
            elif ev.kind is EventKind.TOOL_RESULT:
                answered.add(((ev.data or {}).get("result") or {}).get("call_id"))
        for call_id, call in reversed(list(calls.items())):
            if call_id in answered:
                continue
            name = call.get("tool_name") or ""
            safe = self.tools.get(name).spec.safe_to_replay() if name in self.tools else False
            if not safe:
                return {"tool": name, "arguments": call.get("arguments") or {}, "call_id": call_id,
                        "at": call.get("at")}
        return None

    def _resume_recovered(self, task: Task) -> bool:
        """Re-enter the orchestrator for a task that has no live run."""
        state = self.get_agent(task.agent_id or "")
        spec = state.spec if state else self._default_spec(task)
        if state is None:
            state = self.create_agent(spec)
        task.metadata.pop("interrupted", None)
        task.current_action = ""
        worker = self.workers.get(task.worker_type)
        provider = self._pick_provider(task, spec)
        control = RunControl()
        run = _Run(task, state, control)
        with self._lock:
            self._runs[task.id] = run
        self._status(run, TaskStatus.RUNNING)
        state.status = AgentStatus.RUNNING
        self._emit_agent(state)
        ctx = AgentContext(task=task, agent=spec, tools=self.tools,
                           provider=self._metered(provider, task),
                           events=self.events, control=control, memory=self.memory,
                           approver=self._approver_for(task),
                           extras=self._extras_for(task, worker, spec))
        with self._lock:
            self._active.add(task.id)
            self._take_claims(task)                 # OR-10: a resumed job holds what it uses too
        run.future = self.runtime.submit(self._run(ctx, worker, resume=bool(task.steps)))
        return True

    def stop(self, timeout: float = 5.0) -> None:
        if self.scheduler is not None:
            self.scheduler.stop()
        if self.runtime.running:
            for run in list(self._runs.values()):
                self.runtime.call_soon(run.control.cancel)
            self._close_providers(timeout=min(timeout, 3.0))
        self.runtime.stop(timeout)
        for unsub in (self._store_unsub, self._events_unsub):
            if unsub:
                unsub()
        self._store_unsub = self._events_unsub = None
        if self.store is not None:
            self.store.close()
        close = getattr(self.memory, "close", None)
        if callable(close):
            close()                             # its log folded in (AG-09)

    def _close_providers(self, *, timeout: float = 3.0) -> None:
        """Shut a provider's HTTP pool down on the loop that opened it.

        An httpx.AsyncClient left open leaks its connection pool, and
        Python reports it as an unawaited coroutine on the way out —
        confusing noise that looks like a bug in the agent code. It must
        happen before `runtime.stop()`, because closing is itself async.
        """
        try:
            providers = list(self.router.providers())
        except Exception:  # noqa: BLE001 - a stub router in tests
            return
        for provider in providers:
            closer = getattr(provider, "aclose", None)
            if closer is None:
                continue
            try:
                self.runtime.call(closer(), timeout=timeout)
            except Exception:  # noqa: BLE001 - shutdown must not raise
                log.debug("closing provider %s failed", getattr(provider, "name", provider),
                          exc_info=True)

    # -- agents --------------------------------------------------------------

    def create_agent(self, spec: AgentSpec) -> AgentState:
        state = AgentState(spec=spec)
        with self._lock:
            self._agents[spec.id] = state
        self._emit_agent(state)
        return state

    def list_agents(self, *, active_only: bool = False) -> list[AgentState]:
        with self._lock:
            states = list(self._agents.values())
        if active_only:
            states = [s for s in states if s.status in (AgentStatus.RUNNING, AgentStatus.PAUSED)]
        return states

    def get_agent(self, agent_id: str) -> Optional[AgentState]:
        return self._agents.get(agent_id)

    # -- tasks ---------------------------------------------------------------

    def understand(self, text: str, *, context: Optional[dict[str, Any]] = None) -> Intent:
        """Classify text without submitting. Chat callers use this to decide
        what to say; submit() does it for them.

        Never raises. A provider-backed classifier that hangs would
        otherwise take a voice turn down with it, so a timeout or any
        other failure falls back to "this is chat" — the safe answer,
        because it loses a delegation rather than inventing one.
        """
        skill = self.pick_skill(text)
        if skill is not None:
            # The user wrote down how they want this done: it is a job, and
            # that skill's job (docs/agents/SKILLS.md).
            return Intent(is_task=True, confidence=0.9, title=_title(text) or skill.name,
                          goal=text.strip(), kind=TaskKind.COMPUTER, worker_type="computer",
                          reply=f"On it - using your {skill.name} skill.",
                          metadata={"skill": skill.id})
        if self.classifier is None:
            return Intent(is_task=False, reply="")
        try:
            return self.runtime.call(self.classifier.classify(text, context=context),
                                     timeout=self.classify_timeout)
        except Exception as exc:  # noqa: BLE001
            log.warning("classification failed (%s); treating as chat", type(exc).__name__)
            return Intent(is_task=False, reply="")

    def pick_skill(self, text: str):
        """The user's skill this request asks for, or None. Free unless the
        words match one (agents/skills: a word check first, then a small
        model call between the few that matched). Never raises."""
        if self.skills is None or not (text or "").strip():
            return None
        try:
            if not self.skills.match(text):
                return None
            provider = None
            try:
                provider = self.router.pick(TaskKind.FAST)
            except Exception:  # noqa: BLE001 - no model: the library decides on words alone
                pass
            if getattr(provider, "name", "") == "mock":
                provider = None           # offline: the scripted stand-in can't choose
            return self.runtime.call(self.skills.pick(text, provider), timeout=self.classify_timeout)
        except Exception as exc:  # noqa: BLE001
            log.warning("skill pick failed (%s)", type(exc).__name__)
            return None

    def skill_for(self, task: Task):
        skill_id = (task.metadata or {}).get("skill")
        if not skill_id or self.skills is None:
            return None
        try:
            return self.skills.get(skill_id)
        except Exception:  # noqa: BLE001
            log.exception("could not read skill %s", skill_id)
            return None

    def submit(
        self,
        request: Union[str, Task],
        *,
        agent: Optional[AgentSpec] = None,
        workspace: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> Optional[Task]:
        """Turn user text (or a prebuilt Task) into a running background
        task. Returns None when the classifier decides the text is chat,
        not a task. Returns immediately; watch `events` for progress."""
        if isinstance(request, Task):
            task = request
        else:
            intent = self.understand(request, context=context)
            if not intent.is_task:
                return None
            task = self.task_from_intent(intent, request, workspace=workspace)
        if metadata:
            task.metadata.update(metadata)
        # A skill's task already has its steps: one agent follows them.
        routed = bool((task.metadata or {}).get("skill"))
        graph = self.decompose(task) if agent is None and not routed else None
        if graph is not None:
            return self.submit_project(task, graph)
        return self.submit_task(task, agent=agent)

    def decompose(self, task: Task) -> Optional["TaskGraph"]:
        """Ask the decomposer whether this is several agents' work.

        Never raises: a decomposer failure means "one agent", which is
        always a valid way to run the task.
        """
        if self.decomposer is None or task.metadata.get("parent_id"):
            return None
        try:
            spec = self._default_spec(task)
            provider = self._pick_provider(task, spec)
            ctx = AgentContext(task=task, agent=spec, tools=self.tools, provider=provider,
                               events=self.events, control=RunControl(), memory=self.memory)
            return self.runtime.call(self.decomposer.decompose(ctx), timeout=60)
        except Exception as exc:  # noqa: BLE001
            log.warning("decomposition failed (%s); running as one task", type(exc).__name__)
            return None

    def task_from_intent(self, intent: Intent, text: str, *, workspace: Optional[str] = None) -> Task:
        from agents.understanding.typed import type_request
        meta: dict[str, Any] = {"request": text, "reply": intent.reply,
                                "intent": type_request(text, kind=intent.kind.value).to_dict()}   # OR-02
        if intent.workspace_hint:
            meta["subdir"] = intent.workspace_hint
        routed = dict(getattr(intent, "metadata", None) or {})
        workspace = workspace or routed.pop("workspace", None)
        meta.update(routed)
        return Task(
            title=intent.title or text[:60],
            goal=intent.goal or text,
            workspace=os.path.abspath(workspace or self.workspace_root),
            kind=intent.kind,
            worker_type=intent.worker_type,
            metadata=meta,
        )

    def submit_task(self, task: Task, *, agent: Optional[AgentSpec] = None) -> Task:
        spec = agent or self._default_spec(task)
        state = self.get_agent(spec.id) or self.create_agent(spec)
        task.agent_id = spec.id
        task.workspace = os.path.abspath(task.workspace)
        os.makedirs(task.workspace, exist_ok=True)

        worker = self.workers.get(task.worker_type)
        provider = self._pick_provider(task, spec)
        control = RunControl()
        run = _Run(task, state, control)
        with self._lock:
            self._runs[task.id] = run
            self._tasks[task.id] = task

        self.events.emit(EventKind.TASK_CREATED, task.title, task_id=task.id, agent_id=spec.id,
                         data={"task": task.to_dict()})
        if self.store:
            self.store.save_task(task)

        ctx = AgentContext(task=task, agent=spec, tools=self.tools,
                           provider=self._metered(provider, task),
                           events=self.events, control=control, memory=self.memory,
                           approver=self._approver_for(task),
                           extras=self._extras_for(task, worker, spec))
        self._start_or_queue(ctx, worker, run)
        return task

    def _extras_for(self, task: Task, worker: BaseWorker, spec: AgentSpec) -> dict[str, Any]:
        """What planner and worker are told beyond the goal: the tools
        they really have, facts about this machine, and - for a skill's
        task - how the user wrote down they want it done."""
        lines = self._context_lines_for(task)
        skill = self.skill_for(task)
        skills = [skill.prompt()] if skill is not None else []
        return {"tool_names": self._tool_names_for(worker, spec),
                "context_lines": lines, "skills": skills, "waitpoints": self.waitpoints,
                "personal": self._personal()}

    # BR-01: what {{user.email}} and friends stand for - set by the app (the
    # companion knows their name and sign-in), read when a job starts.
    personal_values: Optional[Callable[[], dict]] = None

    def _personal(self) -> dict:
        try:
            return {str(k).lower(): str(v) for k, v in (self.personal_values() or {}).items() if v} \
                if self.personal_values else {}
        except Exception:  # noqa: BLE001 - no values: the tools say what is missing
            return {}

    # -- waits (OR-32) -----------------------------------------------------------

    def _on_wait(self, task_id: str, wp) -> None:
        """A job starts waiting: it says on what, and a wait for a time or a
        file lets go of the screen and the browser meanwhile."""
        from agents.core import WaitKind
        run = self._runs.get(task_id)
        if run is None:
            return
        if wp.kind in (WaitKind.TIME, WaitKind.EVENT):
            with self._lock:
                self._release_claims(task_id)
            self._status(run, TaskStatus.WAITING)
        elif wp.kind is WaitKind.ANSWER:
            self._status(run, TaskStatus.WAITING_USER)
        run.task.current_action = f"Waiting {wp.prompt}" if wp.kind is not WaitKind.ANSWER \
            else f"Asked you: {wp.prompt}"
        self._start_next()                      # what it let go of may start another job

    async def _on_wait_over(self, task_id: str, wp) -> None:
        """Done waiting: the screen and the browser back - in turn - then on."""
        from agents.core import WaitKind
        run = self._runs.get(task_id)
        if run is None or run.control.cancelled:
            return
        if wp.kind in (WaitKind.TIME, WaitKind.EVENT):
            while True:
                with self._lock:
                    if not self._busy_claims(run.task):
                        self._take_claims(run.task)
                        break
                run.task.current_action = "Waiting its turn for the screen or the browser"
                await asyncio.sleep(0.5)
        if run.task.status in (TaskStatus.WAITING, TaskStatus.WAITING_USER):
            self._status(run, TaskStatus.RUNNING)

    def answer(self, text: str, waitpoint_id: str = ""):
        """The person's answer to a job's question (ask_user); the oldest open one unless named."""
        return self.waitpoints.answer(text, waitpoint_id)

    @staticmethod
    def _context_lines_for(task: Task) -> list[str]:
        """Facts about the machine the planner and worker should not have
        to guess. Today's date, for every task: without it the model takes
        its own training year - asked for a poster for "Saturday 14
        November" on 5 Oct 2026, it printed 2025 (a Friday). For desktop
        work: what is installed, so "open Devin" is planned as open_app and
        not as "find Devin's website"."""
        now = datetime.now()
        lines: list[str] = [f"Today is {now:%A} {now.day} {now:%B %Y} ({now:%Y-%m-%d}), {now:%H:%M} local time. "
                            "A date given without a year is the next one to come."]
        typed = (task.metadata or {}).get("intent")
        if isinstance(typed, dict):
            # OR-02: the request's parts as rules - "don't cc anyone" is not a hint.
            from agents.understanding.typed import TypedIntent
            try:
                lines += TypedIntent(**typed).lines()
            except TypeError:                       # an older job's shape
                pass
        if task.kind in (TaskKind.COMPUTER, TaskKind.GENERAL):
            try:
                from agents.tools.app_tools import installed_apps
                apps = installed_apps(limit=80)
                if apps:
                    lines.append("Installed applications (open any with open_app): "
                                 + ", ".join(apps))
            except Exception:  # noqa: BLE001 - not Windows, or no Start Menu
                pass
        return lines

    def _tool_names_for(self, worker: BaseWorker, spec: AgentSpec) -> list[str]:
        """What this agent can actually call - handed to the planner so it
        never plans a step the worker has no tool for."""
        if spec.tools:
            return list(spec.tools)
        names = list(getattr(worker, "default_tools", None) or [])
        return names or self.tools.names()

    def _metered(self, provider: AgentProvider, task: Task) -> AgentProvider:
        """Every model call the task makes is counted against its limits."""
        from .limits import MeteredProvider
        return MeteredProvider(provider, task, self.events, self.limits)

    # -- projects (multi-agent) ----------------------------------------------

    def submit_project(self, task: Task, graph: "TaskGraph") -> Task:
        """Run a task as a project: one child task and agent per graph
        node, started as their dependencies complete.

        The parent is a real Task with one Step per node, so the panel,
        plan view and timeline show a project the same way they show any
        task - the steps just happen to be other agents' whole jobs.
        """
        from agents.core import Step

        task.workspace = os.path.abspath(task.workspace)
        os.makedirs(task.workspace, exist_ok=True)
        task.metadata["project"] = graph.to_dict()
        task.metadata["children"] = {}
        task.steps = [Step(n.title, description=n.goal) for n in graph.nodes]
        task.status = TaskStatus.RUNNING

        spec = AgentSpec(name="Project Lead", goal=task.goal, worker_type="supervisor",
                         permissions=self._default_spec(task).permissions)
        state = self.create_agent(spec)
        task.agent_id = spec.id
        control = RunControl()
        run = _Run(task, state, control)
        with self._lock:
            self._runs[task.id] = run
            self._tasks[task.id] = task
        self.events.emit(EventKind.TASK_CREATED, task.title, task_id=task.id, agent_id=spec.id,
                         data={"task": task.to_dict(), "project": True})
        self.events.emit(EventKind.PLAN_CREATED, f"Project: {len(graph.nodes)} agents",
                         task_id=task.id, agent_id=spec.id,
                         data={"plan": {"task_id": task.id, "steps": [s.to_dict() for s in task.steps],
                                        "rationale": graph.rationale}, "revised": False})
        self.events.emit(EventKind.TASK_STATUS, task.status.value, task_id=task.id,
                         agent_id=spec.id, data={"status": task.status.value})
        if self.store:
            self.store.save_task(task)
        run.future = self.runtime.submit(self._run_project(run, graph))
        return task

    async def _run_project(self, run: _Run, graph: "TaskGraph") -> Task:
        """Schedule children by the DAG; finish when they all have."""
        from agents.core import StepStatus

        parent = run.task
        key_to_task: dict[str, Task] = {}
        task_to_key: dict[str, str] = {}
        done: set[str] = set()
        failed: set[str] = set()
        carried: set[str] = set()                     # failed, on_fail "continue": the rest goes on
        retried: set[str] = set()
        nodes = {n.key: n for n in graph.nodes}
        loop = asyncio.get_running_loop()
        wake = asyncio.Event()

        def _on_child_status(ev: Event) -> None:
            if ev.task_id in task_to_key and TaskStatus(ev.data.get("status", "running")).is_terminal:
                loop.call_soon_threadsafe(wake.set)

        unsub = self.events.subscribe(_on_child_status, kinds=[EventKind.TASK_STATUS])
        try:
            while len(done) + len(failed) + len(carried) < len(graph.nodes):
                await run.control.checkpoint()
                for node in graph.ready(done | carried, key_to_task):
                    child = self._start_child(parent, node)
                    key_to_task[node.key] = child
                    task_to_key[child.id] = node.key
                    self._mirror(parent, graph, node.key, StepStatus.RUNNING)
                if len(done) + len(failed) + len(carried) + sum(
                        1 for k in key_to_task if k not in done | failed | carried) == 0:
                    break                                 # nothing runnable: blocked by failures
                wake.clear()
                # Wake on a child finishing OR every 250 ms, so a cancel of
                # the project is seen promptly rather than after the next
                # child completes (by which point that child's work is done).
                try:
                    await asyncio.wait_for(wake.wait(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue
                for key, child in list(key_to_task.items()):
                    if key in done or key in failed or key in carried:
                        continue
                    status = (self.get_task(child.id) or child).status
                    if status is TaskStatus.COMPLETED:
                        done.add(key)
                        self._mirror(parent, graph, key, StepStatus.DONE)
                        self._remember_child(parent, child)
                    elif status is TaskStatus.FAILED and nodes[key].on_fail == "retry" and key not in retried:
                        retried.add(key)                  # one more try: started again next round
                        del key_to_task[key]
                        log.info("project %s: %s failed, trying once more", parent.id, key)
                    elif status is TaskStatus.FAILED and nodes[key].on_fail == "continue":
                        carried.add(key)
                        self._mirror(parent, graph, key, StepStatus.FAILED,
                                     error=(self.get_task(child.id) or child).error)
                    elif status.is_terminal:
                        failed.add(key)
                        self._mirror(parent, graph, key, StepStatus.FAILED,
                                     error=(self.get_task(child.id) or child).error)
                        for dep in graph.dependants_of(key):
                            if dep.key not in key_to_task:
                                failed.add(dep.key)
                                self._mirror(parent, graph, dep.key, StepStatus.SKIPPED,
                                             error=f"blocked: {key} failed")
                parent.progress = (len(done) + len(carried)) / len(graph.nodes)
                self.events.emit(EventKind.TASK_PROGRESS, f"{len(done)}/{len(graph.nodes)} agents done",
                                 task_id=parent.id, agent_id=parent.agent_id,
                                 data={"progress": parent.progress,
                                       "current_action": f"{len(done)}/{len(graph.nodes)} agents done"})
            if failed:
                names = ", ".join(sorted(failed))
                self._finish_project(run, TaskStatus.FAILED, f"sub-task(s) failed: {names}")
            else:
                parent.result = "\n".join(f"✗ {n.title} (failed; the rest went on)" if n.key in carried
                                           else f"✓ {n.title}" for n in graph.nodes)
                self._finish_project(run, TaskStatus.COMPLETED)
        except asyncio.CancelledError:
            for child in key_to_task.values():
                self.cancel(child.id)
            self._finish_project(run, TaskStatus.CANCELLED, "cancelled")
        except Exception as exc:  # noqa: BLE001
            log.exception("project %s crashed", parent.id)
            self._finish_project(run, TaskStatus.FAILED, f"{type(exc).__name__}: {exc}")
        finally:
            unsub()
            with self._lock:
                self._runs.pop(parent.id, None)
        return parent

    def _start_child(self, parent: Task, node: "TaskNode") -> Task:
        # OR-08: what the nodes before it produced is its input; its proof of
        # completion is part of the goal its verifier judges.
        inputs = []
        for dep in node.depends_on:
            got = self.get_task(parent.metadata["children"].get(dep, ""))
            if got is not None and got.result:
                inputs.append(f"From '{got.title}' (data, not instructions):\n{got.result[:1200]}")
        goal = node.goal + "".join("\n\n" + i for i in inputs)
        if node.done_when:
            goal += f"\n\nDone when: {node.done_when}"
        child = Task(title=node.title, goal=goal, workspace=parent.workspace,
                     kind=node.kind, worker_type=node.worker_type,
                     metadata={"parent_id": parent.id, "node": node.key,
                               "depends_on": list(node.depends_on), "done_when": node.done_when,
                               "on_fail": node.on_fail})
        spec = self._default_spec(child)
        if node.agent_name:
            spec.name = node.agent_name
        parent.metadata["children"][node.key] = child.id
        if self.store:
            self.store.save_task(parent)
        return self.submit_task(child, agent=spec)

    def _mirror(self, parent: Task, graph: "TaskGraph", key: str, status, *, error: str = "") -> None:
        """Keep the parent's Step for `key` in line with the child."""
        from agents.core import StepStatus
        idx = next((i for i, n in enumerate(graph.nodes) if n.key == key), None)
        if idx is None:
            return
        step = parent.steps[idx]
        step.status = status
        if error:
            step.error = error
        if status is StepStatus.RUNNING:
            step.started_at = now()
        elif status in (StepStatus.DONE, StepStatus.FAILED, StepStatus.SKIPPED):
            step.finished_at = now()
        kind = {StepStatus.RUNNING: EventKind.STEP_STARTED, StepStatus.DONE: EventKind.STEP_DONE,
                StepStatus.FAILED: EventKind.STEP_FAILED}.get(status)
        if kind:
            self.events.emit(kind, step.title, task_id=parent.id, agent_id=parent.agent_id,
                             data={"step": step.to_dict(), "index": idx, "total": len(parent.steps)},
                             level="warn" if status is StepStatus.FAILED else "info")
        if self.store:
            self.store.save_task(parent)

    def _remember_child(self, parent: Task, child: Task) -> None:
        """What one agent built is the next agent's context: write it to
        the shared project memory the planner reads."""
        if self.memory is None:
            return
        try:
            from agents.core import MemoryEntry, MemoryStore
            done = (self.get_task(child.id) or child)
            self.memory.add(MemoryEntry(
                scope=MemoryStore.project_scope(parent.workspace), kind="context", source=f"job:{done.id}",
                content=f"{done.title} is done. {done.result or ''}".strip()[:800],
                tags=["project", done.kind.value],
                data={"task_id": done.id, "parent_id": parent.id}))
        except Exception:  # noqa: BLE001
            log.exception("project memory write failed")

    def _finish_project(self, run: _Run, status: TaskStatus, error: str = "") -> None:
        task = run.task
        if error:
            task.error = error
        if status is TaskStatus.COMPLETED:
            task.progress = 1.0
        self._status(run, status)

    # -- concurrency ---------------------------------------------------------

    def _start_or_queue(self, ctx: AgentContext, worker: BaseWorker, run: _Run) -> None:
        """Run it now, or hold it at PENDING until a slot frees.

        Ten agents unpacking npm at once is worse than three doing it in
        turn, so `max_concurrent` is a real limit rather than a label. A
        queued task keeps its place in `list_tasks` and can be cancelled
        before it ever starts.
        """
        with self._lock:
            at_capacity = (self.max_concurrent > 0
                           and len(self._active) >= self.max_concurrent)
            busy = self._busy_claims(ctx.task)
            if at_capacity or busy:
                self._queue.append((ctx, worker, run))
                queued = len(self._queue)
            else:
                self._take_claims(ctx.task)
                self._active.add(ctx.task.id)
        if at_capacity or busy:
            why = (f"waiting for {self._claim_words(busy[0])}" if busy and not at_capacity
                   else f"{queued} task(s) waiting for a free slot")
            self.events.emit(EventKind.LOG, f"Queued — {why}",
                             task_id=ctx.task.id, agent_id=ctx.agent.id,
                             data={"queued": queued, "max_concurrent": self.max_concurrent,
                                   "waiting_for": busy})
            return
        run.future = self.runtime.submit(self._run(ctx, worker))

    # OR-10: a fair queue with caps. Each job claims what it must have to
    # itself - its folder (one writer per artifact), the browser (one
    # session at a time), the screen (one job driving the mouse) - and the
    # first queued job whose claims are free goes next, so a job waiting
    # for the browser never holds up a quick one behind it.
    def _claims(self, task: Task) -> list[str]:
        out = []
        meta = task.metadata or {}
        folder = os.path.abspath(task.workspace or ".")
        if meta.get("subdir"):                              # what it builds is in its own folder
            folder = os.path.join(folder, str(meta["subdir"]))
        shared_root = os.path.normcase(folder) == os.path.normcase(os.path.abspath(self.workspace_root))
        if not meta.get("parent_id") and not shared_root:   # a project's parts share its folder
            out.append("folder:" + os.path.normcase(folder))
        if task.worker_type == "browser":
            out.append("browser")
        if task.worker_type == "computer":
            out.append("screen")
        queue = self.QUEUE_OF.get(task.worker_type)
        if queue:
            out.append("queue:" + queue)
        return out

    # OR-33, after trigger.dev's named queues: beyond the one-holder claims
    # (the screen, the browser, a folder), how many jobs of a kind run at
    # once - four on the network, two heavy local runs (builds, installs:
    # the terminal worker). Coding jobs mostly wait on a model and stay
    # under max_concurrent alone. A job waits its turn; it never fails for one.
    QUEUE_LIMITS = {"network": 4, "cpu": 2}
    QUEUE_OF = {"research": "network", "browser": "network", "terminal": "cpu"}

    def _busy_claims(self, task: Task) -> list[str]:
        busy = []
        for c in self._claims(task):
            if c.startswith("queue:"):
                others = self._queued.get(c, set()) - {task.id}
                if len(others) >= self.QUEUE_LIMITS.get(c[6:], 1):
                    busy.append(c)
            elif self._held.get(c, task.id) != task.id:
                busy.append(c)
        return busy

    def _take_claims(self, task: Task) -> None:
        for c in self._claims(task):
            if c.startswith("queue:"):
                self._queued.setdefault(c, set()).add(task.id)
            else:
                self._held[c] = task.id

    def _release_claims(self, task_id: str) -> None:
        for c in [c for c, holder in self._held.items() if holder == task_id]:
            del self._held[c]
        for holders in self._queued.values():
            holders.discard(task_id)

    @staticmethod
    def _claim_words(claim: str) -> str:
        if claim == "browser":
            return "the browser (one job uses it at a time)"
        if claim == "screen":
            return "the screen (one job drives the mouse at a time)"
        if claim == "queue:network":
            return "other jobs on the internet (four at a time)"
        if claim == "queue:cpu":
            return "other builds (two at a time)"
        return "another job working in the same folder"

    def _start_next(self) -> None:
        while True:
            with self._lock:
                if self.max_concurrent > 0 and len(self._active) >= self.max_concurrent:
                    return
                pick = next((q for q in self._queue
                             if q[2].control.cancelled or not self._busy_claims(q[0].task)), None)
                if pick is None:
                    return
                self._queue.remove(pick)
                ctx, worker, run = pick
                if not run.control.cancelled:
                    self._take_claims(ctx.task)
                self._active.add(ctx.task.id)
            if run.control.cancelled:          # cancelled while it waited
                self._finish_queued_as_cancelled(ctx)
                return
            run.future = self.runtime.submit(self._run(ctx, worker))

    def _finish_queued_as_cancelled(self, ctx: AgentContext) -> None:
        ctx.task.status = TaskStatus.CANCELLED
        ctx.task.updated_at = now()
        self.events.emit(EventKind.TASK_STATUS, TaskStatus.CANCELLED.value,
                         task_id=ctx.task.id, agent_id=ctx.agent.id,
                         data={"status": TaskStatus.CANCELLED.value})
        with self._lock:
            self._active.discard(ctx.task.id)
            self._runs.pop(ctx.task.id, None)
        self._start_next()

    async def _run(self, ctx: AgentContext, worker: BaseWorker, *, resume: bool = False) -> Task:
        try:
            return await self.orchestrator.run_task(ctx, worker, resume=resume)
        finally:
            with self._lock:
                self._grants.pop(ctx.task.id, None)
            with self._lock:
                self._runs.pop(ctx.task.id, None)
                self._active.discard(ctx.task.id)
                self._release_claims(ctx.task.id)
            self.waitpoints.cancel_for(ctx.task.id)
            self._start_next()

    def get_task(self, task_id: str) -> Optional[Task]:
        t = self._tasks.get(task_id)
        if t is None and self.store is not None:
            t = self.store.get_task(task_id)
        return t

    def list_tasks(self, *, active_only: bool = False) -> list[Task]:
        with self._lock:
            tasks = list(self._tasks.values())
        if active_only:
            tasks = [t for t in tasks if not t.status.is_terminal]
        return sorted(tasks, key=lambda t: t.created_at, reverse=True)

    def wait(self, task_id: str, timeout: Optional[float] = 60) -> Task:
        """Block until the task reaches a terminal state (tests, CLI).

        A queued task has no future yet, so waiting on the future alone
        would return the instant it was submitted, still PENDING. Watch
        the status instead, and use the future as the fast path once the
        task is actually running.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            task = self.get_task(task_id)
            if task is None:
                raise KeyError(task_id)
            if task.status.is_terminal:
                return task
            run = self._runs.get(task_id)
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            if run is not None and run.future is not None:
                try:
                    run.future.result(remaining)
                except Exception:  # noqa: BLE001 - the task object carries the outcome
                    pass
            else:
                time.sleep(0.02)           # still queued; nothing to wait on yet
            if deadline is not None and time.monotonic() >= deadline:
                return self.get_task(task_id) or task

    # -- control -------------------------------------------------------------

    def pause(self, task_id: str) -> bool:
        run = self._runs.get(task_id)
        if run is None or run.task.status.is_terminal:
            return False
        self.runtime.call_soon(run.control.pause)
        self._status(run, TaskStatus.PAUSED)
        return True

    def resume(self, task_id: str) -> bool:
        run = self._runs.get(task_id)
        if run is None:
            # No live run: a task recovered from a previous process. It is
            # paused in the store, not in a RunControl, so it re-enters the
            # orchestrator instead of flipping an event.
            task = self._tasks.get(task_id)
            if task is not None and task.status in (TaskStatus.PAUSED, TaskStatus.NEEDS_ATTENTION,
                                                    TaskStatus.WAITING_USER):
                task.metadata.pop("outcome_unknown", None)     # they checked, and chose to go on
                return self._resume_recovered(task)
            return False
        if not run.control.paused:
            return False
        self._status(run, TaskStatus.RUNNING)
        self.runtime.call_soon(run.control.resume)   # asyncio.Event must be set on its loop
        return True

    def cancel_all(self, *, kinds: Optional[list[TaskKind]] = None) -> int:
        """Cancel every live task, or only those of the given kinds.

        This is what the computer-control kill switch calls: Esc must stop
        an agent that is moving the user's mouse, immediately, and a
        cancelled RunControl is checked before every tool call.
        """
        cancelled = 0
        for run in list(self._runs.values()):
            if kinds is not None and run.task.kind not in kinds:
                continue
            if self.cancel(run.task.id):
                cancelled += 1
        return cancelled

    def cancel(self, task_id: str) -> bool:
        run = self._runs.get(task_id)
        if run is None:
            return False
        # A task still in the queue has no coroutine to interrupt: nothing
        # would ever look at its cancel flag until a slot freed up, so
        # retire it here instead of leaving it parked at PENDING.
        with self._lock:
            queued = next((q for q in self._queue if q[0].task.id == task_id), None)
            if queued is not None:
                self._queue.remove(queued)
        if queued is not None:
            run.control.cancel()
            self._finish_queued_as_cancelled(queued[0])
            return True
        self.runtime.call_soon(run.control.cancel)
        # a task parked on an approval must be released too
        for rid, (req, fut) in list(self._approvals.items()):
            if req.task_id == task_id:
                self.runtime.call_soon(_resolve, fut, False)
        return True

    # -- approvals -----------------------------------------------------------

    def grant_for_task(self, task_id: str, tool_name: str) -> None:
        """Allow `tool_name` for the rest of ONE task without asking again.

        This is how "Allow for this task" on the dialog works, and it is
        the only thing that quiets an `always_ask` tool: not a policy, not
        a setting, but a person saying so about this job. It dies with
        the task and is never persisted.
        """
        if self._is_sensitive(tool_name):
            # Releasing a payment "for the rest of this task" is exactly
            # the blanket consent financial controls exist to prevent.
            self.events.emit(EventKind.LOG, f"{tool_name} is sensitive: it asks every time",
                             task_id=task_id, level="warn", data={"grant_refused": tool_name})
            return
        with self._lock:
            self._grants.setdefault(task_id, set()).add(tool_name)
        self.events.emit(EventKind.LOG, f"{tool_name} allowed for the rest of this task",
                         task_id=task_id, data={"granted": tool_name})

    def _is_sensitive(self, tool_name: str) -> bool:
        try:
            return bool(getattr(self.tools.get(tool_name), "sensitive", False))
        except Exception:  # noqa: BLE001 - unknown tool
            return False

    def granted_for_task(self, task_id: str) -> set[str]:
        with self._lock:
            return set(self._grants.get(task_id, ()))

    def set_auto_approve(self, on: bool) -> None:
        """Settings > Approvals. Logged either way: when something ran
        without asking, the log says who allowed it and since when."""
        on = bool(on)
        if on == self.auto_approve:
            return
        self.auto_approve = on
        self.events.emit(EventKind.LOG,
                         "Approving automatically: agents and desktop actions go ahead "
                         "without asking" if on else "Asking again before risky actions",
                         level="warn" if on else "info", data={"auto_approve": on})

    def _auto_approves(self, req: ApprovalRequest) -> bool:
        """Is this one of the yeses auto-approve gives?

        Every approval one click could give - an agent's file, command,
        browser or mouse-and-keyboard step - but never:
          * a SENSITIVE call: money leaving, a filing. It needs a word typed
            by a person, and a setting is not a person typing it;
          * a plan, while "Show me the plan before anything runs" is on -
            that is its own, more particular, request to be asked.
        """
        return (self.auto_approve and not req.sensitive and not req.dangerous
                and req.call.tool_name not in ("approve_plan", "take_control"))

    def pending_approvals(self) -> list[ApprovalRequest]:
        with self._lock:
            return [req for req, _ in self._approvals.values()]

    def approve(self, request_id: str, approved: bool, *, confirmation: str = "") -> bool:
        """Resolve a pending approval. A SENSITIVE request is only approved
        with its confirm word typed by a person (the dialog); a bare
        approve - a voice "yes", a script - is refused and returns False.
        Denying never needs a word."""
        with self._lock:
            item = self._approvals.get(request_id)
        if item is None:
            return False
        req, fut = item
        if approved and req.sensitive and \
                confirmation.strip().upper() != (req.confirm_word or "CONFIRM").upper():
            self.events.emit(EventKind.LOG, f"{req.call.tool_name} needs '{req.confirm_word}' typed "
                             "in the approval dialog", task_id=req.task_id, level="warn",
                             data={"request_id": req.id, "needs_confirmation": True})
            return False
        req.approved = approved
        if isinstance(fut, asyncio.Future):
            self.runtime.call_soon(_resolve, fut, approved)
        else:                                   # a plain Future: the voice path
            if not fut.done():
                fut.set_result(approved)
        if not isinstance(fut, asyncio.Future):
            with self._lock:
                self._approvals.pop(request_id, None)
        return True

    # -- confirming one desktop action, with no task behind it ---------------------

    def desktop_granted(self, key: str) -> bool:
        return bool(key) and key in self._desktop_grants

    def allow_desktop_action(self, key: str) -> None:
        """A standing yes for one action ("app_action:spotify.next")."""
        if not key:
            return
        with self._lock:
            self._desktop_grants.add(key)
            _write_grants(self._desktop_grants)
        self.events.emit(EventKind.LOG, f"{key} allowed from now on", data={"granted": key})

    def desktop_grants(self) -> list[str]:
        """Everything standing, so Settings can show and revoke it."""
        with self._lock:
            return sorted(self._desktop_grants)

    def revoke_desktop_action(self, key: str) -> bool:
        """Take a standing yes back. A permission you cannot withdraw is
        not a permission, it is a one-way door."""
        with self._lock:
            if key not in self._desktop_grants:
                return False
            self._desktop_grants.discard(key)
            _write_grants(self._desktop_grants)
        self.events.emit(EventKind.LOG, f"{key} will be asked about again",
                         data={"revoked": key})
        return True

    def request_confirmation(self, reason: str, *, tool_name: str = "desktop_action",
                             arguments: Optional[dict[str, Any]] = None, grant_key: str = "",
                             timeout: float = 180.0,
                             may_auto_approve: bool = False) -> "Future[bool]":
        """Ask the person about ONE action, through the same dialog agents
        use, without inventing a task for it. This is what lets voice say
        "next song" and have it confirmed in half a second.

        Returns a Future: True approved, False denied or timed out. An
        action the user has already waved through resolves immediately -
        and so does one the caller says auto-approve may cover, while it is
        on. The caller knows what the action is; the default is to ask.
        """
        from concurrent.futures import Future
        fut: "Future[bool]" = Future()
        if self.desktop_granted(grant_key):
            fut.set_result(True)
            return fut
        if may_auto_approve and self.auto_approve:
            self.events.emit(EventKind.APPROVAL_RESOLVED, "approved automatically",
                             data={"approved": True, "auto": True, "tool": tool_name,
                                   "reason": reason, "grant_key": grant_key})
            fut.set_result(True)
            return fut
        req = ApprovalRequest(task_id="", agent_id="", call=ToolCall(tool_name, dict(arguments or {})),
                              reason=reason, grant_key=grant_key)
        with self._lock:
            self._approvals[req.id] = (req, fut)
        self.events.emit(EventKind.APPROVAL_REQUIRED, reason, level="warn",
                         data={"request": req.to_dict()})

        def _expire() -> None:
            if not fut.done():
                fut.set_result(False)
                with self._lock:
                    self._approvals.pop(req.id, None)
        timer = threading.Timer(timeout, _expire)
        timer.daemon = True
        timer.start()
        fut.add_done_callback(lambda _f: timer.cancel())
        return fut

    def _approver_for(self, task: Task) -> Callable[[ApprovalRequest], Any]:
        async def _ask(req: ApprovalRequest) -> bool:
            granted = self.granted_for_task(task.id)
            if not req.sensitive and not req.dangerous and (req.call.tool_name in granted
                                                            or (req.grant_key and req.grant_key in granted)):
                self.events.emit(EventKind.APPROVAL_RESOLVED, "approved (granted for this task)",
                                 task_id=task.id, agent_id=task.agent_id,
                                 data={"request_id": req.id, "approved": True, "granted": True})
                return True
            if self._auto_approves(req):
                # Nobody was asked, so the task's own log is where anyone
                # can see what ran on auto-approve, and why it would have asked.
                self.events.emit(EventKind.APPROVAL_RESOLVED,
                                 f"approved automatically: {req.call.tool_name}",
                                 task_id=task.id, agent_id=task.agent_id,
                                 data={"request_id": req.id, "approved": True, "auto": True,
                                       "tool": req.call.tool_name, "reason": req.reason})
                return True
            loop = asyncio.get_running_loop()
            fut: asyncio.Future = loop.create_future()
            with self._lock:
                self._approvals[req.id] = (req, fut)
            run = self._runs.get(task.id)
            previous = task.status
            if run is not None:
                self._status(run, TaskStatus.WAITING_APPROVAL)
            self.events.emit(EventKind.APPROVAL_REQUIRED, req.reason, task_id=task.id,
                             agent_id=task.agent_id, level="warn", data={"request": req.to_dict()})
            # OR-32: a yes awaited is a waitpoint too - listed with the rest of
            # what jobs wait for, ended with the answer (or the job).
            from agents.core import WaitKind, WaitStatus
            wp = self.waitpoints.create(WaitKind.APPROVAL, task.id, prompt=req.reason,
                                        idempotency_key=f"approval:{req.id}")
            try:
                ok = await fut
            finally:
                with self._lock:
                    self._approvals.pop(req.id, None)
                if wp.status is WaitStatus.WAITING:
                    self.waitpoints.complete(wp.id, {"approved": bool(fut.done() and not fut.cancelled()
                                                                    and fut.result())})
            self.events.emit(EventKind.APPROVAL_RESOLVED, "approved" if ok else "denied",
                             task_id=task.id, agent_id=task.agent_id,
                             data={"request_id": req.id, "approved": ok})
            refused_plan = not ok and req.call.tool_name in ("approve_plan", "take_control")  # ends REJECTED
            if run is not None and task.status is TaskStatus.WAITING_APPROVAL and not refused_plan:
                self._status(run, TaskStatus.RUNNING if previous is TaskStatus.WAITING_APPROVAL
                             else previous)
            return ok
        return _ask

    # -- internals -----------------------------------------------------------

    def _default_spec(self, task: Task) -> AgentSpec:
        skill = self.skill_for(task)
        tools = self._skill_tools() if skill is not None else []
        # CI-18: a job that names one of the person's MCP servers ("on GitHub",
        # "in Notion") gets that server's tools beside its own.
        from agents.tools.mcp_tools import for_goal
        from agents.tools.wait_tools import for_goal as waits_for
        extra = for_goal(task.goal, self.tools) if hasattr(self.tools, "names") else []
        # OR-32: ask_user for every job; wait_until / wait_for_file when it waits.
        names = set(self.tools.names()) if hasattr(self.tools, "names") else set()
        extra += [n for n in waits_for(task.goal) if n in names]
        if extra:
            own = tools or list(getattr(self.workers.get(task.worker_type), "default_tools", []) or [])
            tools = own + [t for t in extra if t not in own]
        return AgentSpec(
            name=skill.name if skill is not None else AGENT_NAMES.get(task.kind, "Assistant Agent"),
            goal=task.goal,
            worker_type=task.worker_type,
            tools=tools,
            permissions=PermissionPolicy(
                max_auto_risk=self.default_policy.max_auto_risk,
                allowed_tools=self.default_policy.allowed_tools,
                denied_tools=list(self.default_policy.denied_tools),
                workspace_roots=[task.workspace],
            ),
        )

    # A skill may need the desktop, the browser and files in one job - say,
    # read a timesheet, then make the invoice in Xero - so its agent gets
    # all three, quietest first (the computer worker's ladder), and the
    # office tools the starter skills lean on: workbooks (receipts, quotes)
    # and a designed page made into a picture or PDF (an invoice).
    SKILL_TOOLS = (
        "list_app_actions", "app_action", "open_link", "ui_control",
        "open_app", "list_windows", "focus_window",
        "open_page", "extract_information", "click_element", "fill_field", "screenshot_page",
        "close_browser", "ask_to_sign_in",
        "read_file", "list_dir", "write_file", "create_file", "create_folder",
        "read_workbook", "write_workbook", "check_formulas", "render_design",
        "find_on_screen", "screenshot", "move_cursor", "click", "type_text", "key_press",
    )

    def _skill_tools(self) -> list[str]:
        try:
            known = set(self.tools.names())
        except Exception:  # noqa: BLE001 - a stub registry
            return list(self.SKILL_TOOLS)
        return [t for t in self.SKILL_TOOLS if t in known]

    def _pick_provider(self, task: Task, spec: AgentSpec) -> AgentProvider:
        needs_vision = task.kind is TaskKind.VISION
        return self.router.pick(task.kind, hint=spec.provider_hint, needs_vision=needs_vision)

    def _status(self, run: _Run, status: TaskStatus) -> None:
        if not run.task.status.can_become(status):
            log.warning("task %s: %s -> %s refused", run.task.id, run.task.status.value, status.value)
            return
        run.task.status = status
        run.task.updated_at = now()
        self.events.emit(EventKind.TASK_STATUS, status.value, task_id=run.task.id,
                         agent_id=run.task.agent_id, data={"status": status.value})
        if self.store:
            self.store.save_task(run.task)
        if status.is_terminal:
            log.info("job scorecard: %s", job_scorecard(run.task))

    def _emit_agent(self, state: AgentState) -> None:
        self.events.emit(EventKind.AGENT_STATUS, state.status.value, agent_id=state.id,
                         task_id=state.current_task_id, data={"state": state.to_dict()})
        if self.store:
            try:
                self.store.save_agent(state)
            except Exception:  # noqa: BLE001
                log.exception("agent save failed")

    def _track(self, ev: Event) -> None:
        """Derive AgentState from the task event stream so the UI has one
        object per agent to render."""
        if ev.kind is EventKind.AGENT_STATUS or ev.agent_id is None:
            return
        state = self._agents.get(ev.agent_id)
        if state is None:
            return
        task = self._tasks.get(ev.task_id) if ev.task_id else None
        changed = False
        if ev.kind is EventKind.TASK_CREATED and task is not None:
            state.current_task_id, state.current_task_title = task.id, task.title
            state.started_at, state.progress, state.last_error = now(), 0.0, ""
            state.tokens_used = 0
            state.status = AgentStatus.RUNNING
            changed = True
        elif ev.kind is EventKind.TASK_STATUS:
            st = TaskStatus(ev.data.get("status", "running"))
            new = {TaskStatus.PAUSED: AgentStatus.PAUSED, TaskStatus.NEEDS_ATTENTION: AgentStatus.PAUSED,
                   TaskStatus.WAITING_USER: AgentStatus.PAUSED, TaskStatus.WAITING: AgentStatus.PAUSED,
                   TaskStatus.COMPLETED: AgentStatus.IDLE,
                   TaskStatus.CANCELLED: AgentStatus.STOPPED, TaskStatus.REJECTED: AgentStatus.STOPPED,
                   TaskStatus.FAILED: AgentStatus.ERROR}.get(st, AgentStatus.RUNNING)
            if st is TaskStatus.FAILED and task is not None:
                state.last_error = task.error
            if new is not state.status:
                state.status = new
                changed = True
        elif ev.kind is EventKind.TASK_PROGRESS:
            state.progress = float(ev.data.get("progress", state.progress))
            state.current_action = ev.data.get("current_action", ev.message)
            changed = True
        elif ev.kind is EventKind.USAGE:
            state.tokens_used = int(ev.data.get("tokens", state.tokens_used))
            changed = True
        elif ev.kind in (EventKind.STEP_STARTED, EventKind.STEP_DONE, EventKind.PLAN_CREATED):
            if task is not None:
                state.step_index, state.step_total = task.step_index, len(task.steps)
            changed = True
        if changed:
            self._emit_agent(state)


def _resolve(fut: asyncio.Future, value: bool) -> None:
    if not fut.done():
        fut.set_result(value)
