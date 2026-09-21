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
from typing import Any, Callable, Optional, Protocol, Union

from agents.core import (
    AgentContext, AgentProvider, AgentSpec, AgentState, AgentStatus, ApprovalRequest,
    BaseWorker, Event, EventBus, EventKind, Intent, IntentClassifier, MemoryStore,
    PermissionPolicy, Planner, ProviderRouter, RunControl, Task, TaskKind, TaskStatus,
    TaskStore, ToolRegistry, Unsubscribe, Verifier, now,
)

from .orchestrator import Orchestrator
from .runtime import AgentRuntime

log = logging.getLogger("agents.manager")


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
        self.orchestrator = Orchestrator(
            planner=planner, verifier=verifier, store=store, memory=memory,
            require_plan_approval=require_plan_approval, max_replans=max_replans,
        )
        self._lock = threading.RLock()
        self._runs: dict[str, _Run] = {}                # task_id -> run
        self._agents: dict[str, AgentState] = {}        # agent_id -> state
        self._tasks: dict[str, Task] = {}               # task_id -> task (incl. finished)
        self._approvals: dict[str, tuple[ApprovalRequest, asyncio.Future]] = {}
        self._store_unsub: Optional[Unsubscribe] = None
        self._events_unsub: Optional[Unsubscribe] = None

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> "AgentManager":
        self.runtime.start()
        if self.store is not None and hasattr(self.store, "attach") and self._store_unsub is None:
            self._store_unsub = self.store.attach(self.events)  # type: ignore[attr-defined]
        if self._events_unsub is None:
            self._events_unsub = self.events.subscribe(self._track)
        return self

    def stop(self, timeout: float = 5.0) -> None:
        if self.runtime.running:
            for run in list(self._runs.values()):
                self.runtime.call_soon(run.control.cancel)
        self.runtime.stop(timeout)
        for unsub in (self._store_unsub, self._events_unsub):
            if unsub:
                unsub()
        self._store_unsub = self._events_unsub = None
        if self.store is not None:
            self.store.close()

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
        what to say; submit() does it for them."""
        if self.classifier is None:
            return Intent(is_task=False, reply="")
        return self.runtime.call(self.classifier.classify(text, context=context), timeout=30)

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
        return self.submit_task(task, agent=agent)

    def task_from_intent(self, intent: Intent, text: str, *, workspace: Optional[str] = None) -> Task:
        meta: dict[str, Any] = {"request": text, "reply": intent.reply}
        if intent.workspace_hint:
            meta["subdir"] = intent.workspace_hint
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

        ctx = AgentContext(task=task, agent=spec, tools=self.tools, provider=provider,
                           events=self.events, control=control, memory=self.memory,
                           approver=self._approver_for(task))
        run.future = self.runtime.submit(self._run(ctx, worker))
        return task

    async def _run(self, ctx: AgentContext, worker: BaseWorker) -> Task:
        try:
            return await self.orchestrator.run_task(ctx, worker)
        finally:
            with self._lock:
                self._runs.pop(ctx.task.id, None)

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
        """Block until the task reaches a terminal state (tests, CLI)."""
        run = self._runs.get(task_id)
        if run is not None and run.future is not None:
            try:
                run.future.result(timeout)
            except Exception:  # noqa: BLE001 - the task object carries the outcome
                pass
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        return task

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
        if run is None or not run.control.paused:
            return False
        self._status(run, TaskStatus.RUNNING)
        self.runtime.call_soon(run.control.resume)   # asyncio.Event must be set on its loop
        return True

    def cancel(self, task_id: str) -> bool:
        run = self._runs.get(task_id)
        if run is None:
            return False
        self.runtime.call_soon(run.control.cancel)
        # a task parked on an approval must be released too
        for rid, (req, fut) in list(self._approvals.items()):
            if req.task_id == task_id:
                self.runtime.call_soon(_resolve, fut, False)
        return True

    # -- approvals -----------------------------------------------------------

    def pending_approvals(self) -> list[ApprovalRequest]:
        with self._lock:
            return [req for req, _ in self._approvals.values()]

    def approve(self, request_id: str, approved: bool) -> bool:
        with self._lock:
            item = self._approvals.get(request_id)
        if item is None:
            return False
        req, fut = item
        req.approved = approved
        self.runtime.call_soon(_resolve, fut, approved)
        return True

    def _approver_for(self, task: Task) -> Callable[[ApprovalRequest], Any]:
        async def _ask(req: ApprovalRequest) -> bool:
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
            try:
                ok = await fut
            finally:
                with self._lock:
                    self._approvals.pop(req.id, None)
            self.events.emit(EventKind.APPROVAL_RESOLVED, "approved" if ok else "denied",
                             task_id=task.id, agent_id=task.agent_id,
                             data={"request_id": req.id, "approved": ok})
            if run is not None and task.status is TaskStatus.WAITING_APPROVAL:
                self._status(run, TaskStatus.RUNNING if previous is TaskStatus.WAITING_APPROVAL
                             else previous)
            return ok
        return _ask

    # -- internals -----------------------------------------------------------

    def _default_spec(self, task: Task) -> AgentSpec:
        return AgentSpec(
            name=AGENT_NAMES.get(task.kind, "Assistant Agent"),
            goal=task.goal,
            worker_type=task.worker_type,
            permissions=PermissionPolicy(
                max_auto_risk=self.default_policy.max_auto_risk,
                allowed_tools=self.default_policy.allowed_tools,
                denied_tools=list(self.default_policy.denied_tools),
                workspace_roots=[task.workspace],
            ),
        )

    def _pick_provider(self, task: Task, spec: AgentSpec) -> AgentProvider:
        needs_vision = task.kind is TaskKind.VISION
        return self.router.pick(task.kind, hint=spec.provider_hint, needs_vision=needs_vision)

    def _status(self, run: _Run, status: TaskStatus) -> None:
        run.task.status = status
        run.task.updated_at = now()
        self.events.emit(EventKind.TASK_STATUS, status.value, task_id=run.task.id,
                         agent_id=run.task.agent_id, data={"status": status.value})
        if self.store:
            self.store.save_task(run.task)

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
            state.status = AgentStatus.RUNNING
            changed = True
        elif ev.kind is EventKind.TASK_STATUS:
            st = TaskStatus(ev.data.get("status", "running"))
            new = {TaskStatus.PAUSED: AgentStatus.PAUSED,
                   TaskStatus.COMPLETED: AgentStatus.IDLE,
                   TaskStatus.CANCELLED: AgentStatus.STOPPED,
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
        elif ev.kind in (EventKind.STEP_STARTED, EventKind.STEP_DONE, EventKind.PLAN_CREATED):
            if task is not None:
                state.step_index, state.step_total = task.step_index, len(task.steps)
            changed = True
        if changed:
            self._emit_agent(state)


def _resolve(fut: asyncio.Future, value: bool) -> None:
    if not fut.done():
        fut.set_result(value)
