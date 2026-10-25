"""Orchestrator: runs ONE task through plan -> execute -> verify.

State machine (see docs/agents/ARCHITECTURE.md §1):

    PENDING -> PLANNING -> [WAITING_APPROVAL] -> RUNNING -> VERIFYING -> COMPLETED
                                                   |  \\-> FAILED (after replan budget)
                                                   |  \\-> RETRY_WAIT / WAITING_USER -> RUNNING
                                                   \\----> CANCELLED (RunControl.cancel)
    PLANNING / WAITING_APPROVAL -> REJECTED (the user said no to the plan)

Every change goes through TaskStatus.can_become (EN-05).

It owns no threads and no state beyond the task it is given: everything
it learns goes out on the EventBus and into the TaskStore, so a UI that
opens mid-run can rebuild the picture from either.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import traceback
from typing import Optional

from agents.core import (
    AgentContext, ApprovalRequest, BaseWorker, EventKind, MemoryStore, Plan,
    Planner, Step, StepStatus, TaskStatus, TaskStore, ToolCall, Verifier, new_id, now,
)

from .limits import LimitExceeded

log = logging.getLogger("agents.orchestrator")


def _started(ctx: AgentContext) -> float:
    """When the clock started: the metered provider knows, else creation."""
    return float(getattr(ctx.provider, "started_at", None) or ctx.task.created_at)


# EN-05: how long a step that hit a passing fault waits before its one retry.
RETRY_WAIT_S = 5.0
_PASSING = re.compile(r"\b(429|502|503|504)\b|rate.?limit|overloaded|temporarily|unavailable|"
                      r"timed out|connection (reset|aborted|error|closed)|disconnected", re.I)
# The tools' own words for "only you can do this" (browser_tools, connectors).
_FOR_USER = re.compile(r"ask the user|needs you to|captcha", re.I)
# A wrong tool name or bad arguments is the model's mistake (and lists every
# tool name after it), never a fault to wait out or a job for the user.
_MODEL_MISTAKE = ("unknown tool", "invalid arguments")


def _passing(error: str) -> bool:
    return not (error or "").startswith(_MODEL_MISTAKE) and bool(_PASSING.search(error or ""))


def _for_the_user(error: str) -> bool:
    return not (error or "").startswith(_MODEL_MISTAKE) and bool(_FOR_USER.search(error or ""))


def _reset(step: Step) -> None:
    step.status, step.error = StepStatus.PENDING, ""
    step.results.clear()


class Orchestrator:
    def __init__(
        self,
        *,
        planner: Planner,
        verifier: Verifier,
        store: Optional[TaskStore] = None,
        memory: Optional[MemoryStore] = None,
        require_plan_approval: bool = False,
        max_replans: int = 1,
        limits: Optional["TaskLimits"] = None,
    ):
        from .limits import TaskLimits
        self.limits = limits or TaskLimits()
        self.planner = planner
        self.verifier = verifier
        self.store = store
        self.memory = memory
        self.require_plan_approval = require_plan_approval
        self.max_replans = max_replans

    # -- public --------------------------------------------------------------

    async def run_task(self, ctx: AgentContext, worker: BaseWorker, *, resume: bool = False):
        """The job's id on every log line made for it (EN-18), around _run_task."""
        from .correlation import JOB, install
        install()
        token = JOB.set(ctx.task.id)
        try:
            return await self._run_task(ctx, worker, resume=resume)
        finally:
            JOB.reset(token)

    async def _run_task(self, ctx: AgentContext, worker: BaseWorker, *, resume: bool = False):
        """Run a task from the start, or pick a recovered one up where it
        stopped.

        On resume the plan is already adopted (it lives in `task.steps`),
        so planning is skipped and execution re-enters at the first
        unfinished step. A step that was RUNNING when the process died is
        reset to PENDING and re-run — which is why tools are idempotent.
        """
        task = ctx.task
        try:
            if resume and task.steps:
                for step in task.steps:
                    if step.status is StepStatus.RUNNING:
                        step.status = StepStatus.PENDING
                        step.results.clear()
                        step.error = ""
                plan = Plan(task_id=task.id, steps=task.steps, id=task.plan_id or new_id(),
                            approved=True)
                ctx.log(f"Resuming at step {task.step_index + 1}/{len(task.steps)}")
            else:
                plan = await self._plan(ctx)
                if plan is None:                   # plan rejected by the user
                    if ctx.control.cancelled:      # stopped while it waited, not refused
                        self._finish(ctx, TaskStatus.CANCELLED, "cancelled")
                    else:
                        self._finish(ctx, TaskStatus.REJECTED, "plan rejected")
                    return task

            self._set_status(ctx, TaskStatus.RUNNING)
            await self._execute(ctx, worker, plan)
            if task.status is not TaskStatus.RUNNING:   # failed inside _execute
                return task

            self._set_status(ctx, TaskStatus.VERIFYING)
            ctx.progress("Verifying result", 1.0)
            vr = await self.verifier.verify_task(ctx)
            ctx.events.emit(EventKind.VERIFICATION, vr.summary, task_id=task.id,
                            agent_id=ctx.agent.id, data={"result": vr.to_dict(), "scope": "task"},
                            level="info" if vr.ok else "warn")
            if vr.ok:
                self._finish(ctx, TaskStatus.COMPLETED, verification=vr.summary)
            else:
                self._finish(ctx, TaskStatus.FAILED, f"final verification failed: {vr.summary}")
        except asyncio.CancelledError:
            self._finish(ctx, TaskStatus.CANCELLED, "cancelled")
        except LimitExceeded as exc:
            ctx.events.emit(EventKind.ERROR, str(exc), task_id=task.id, agent_id=ctx.agent.id,
                            level="warn", data={"limit": exc.which})
            self._finish(ctx, TaskStatus.FAILED, str(exc))
        except Exception as exc:  # noqa: BLE001 - a task must always reach a terminal state
            log.exception("task %s crashed", task.id)
            ctx.events.emit(EventKind.ERROR, f"{type(exc).__name__}: {exc}", task_id=task.id,
                            agent_id=ctx.agent.id, level="error",
                            data={"traceback": traceback.format_exc()[-4000:]})
            self._finish(ctx, TaskStatus.FAILED, f"{type(exc).__name__}: {exc}")
        return task

    # -- phases --------------------------------------------------------------

    async def _plan(self, ctx: AgentContext) -> Optional[Plan]:
        task = ctx.task
        self._set_status(ctx, TaskStatus.PLANNING)
        ctx.progress("Planning", 0.0)
        started = time.monotonic()
        plan = await self.planner.plan(ctx)
        # AG-07: how long a job waits on its plan, which nothing said before.
        log.info("plan ready in %.1fs: %d step(s) for %r", time.monotonic() - started,
                 len(plan.steps), (task.title or task.goal)[:60])
        self._adopt_plan(ctx, plan, revised=False)

        if self.require_plan_approval:
            req = ApprovalRequest(task_id=task.id, agent_id=ctx.agent.id,
                                  call=ToolCall("approve_plan", {"plan_id": plan.id}),
                                  reason=f"Plan with {len(plan.steps)} steps needs approval")
            self._set_status(ctx, TaskStatus.WAITING_APPROVAL)
            if not await ctx.approver(req):
                return None
            plan.approved = True
        else:
            plan.approved = True
        if self.store:
            self.store.save_plan(plan)
        return plan

    async def _execute(self, ctx: AgentContext, worker: BaseWorker, plan: Plan) -> None:
        task = ctx.task
        replans = 0
        retried: set[str] = set()
        i = 0
        while i < len(task.steps):
            await ctx.control.checkpoint()
            self.limits.check(task, started_at=_started(ctx))    # never start a step over budget
            step = task.steps[i]
            if step.status in (StepStatus.DONE, StepStatus.SKIPPED):
                i += 1
                continue
            total = len(task.steps)
            ctx.events.emit(EventKind.STEP_STARTED, step.title, task_id=task.id, agent_id=ctx.agent.id,
                            data={"step": step.to_dict(), "index": i, "total": total})
            step.started_at = now()
            ctx.progress(step.title, i / total)

            step = await worker.execute_step(step, ctx)
            task.steps[i] = step
            step.finished_at = now()

            if step.status is StepStatus.DONE:
                vr = await self.verifier.verify_step(step, ctx)
                ctx.events.emit(EventKind.VERIFICATION, vr.summary, task_id=task.id,
                                agent_id=ctx.agent.id,
                                data={"result": vr.to_dict(), "scope": "step", "step_id": step.id},
                                level="info" if vr.ok else "warn")
                if not vr.ok:
                    step.status = StepStatus.FAILED
                    step.error = f"verification failed: {vr.summary}"

            if step.status is StepStatus.FAILED:
                ctx.events.emit(EventKind.STEP_FAILED, step.error or step.title, task_id=task.id,
                                agent_id=ctx.agent.id, level="warn",
                                data={"step": step.to_dict(), "index": i, "total": total})
                if _for_the_user(step.error):
                    # EN-05: a sign-in or a captcha is the user's to do, not
                    # a reason to replan - wait for them, then try it again.
                    task.current_action = f"Your turn - {step.error}"
                    self._set_status(ctx, TaskStatus.WAITING_USER)
                    ctx.progress(task.current_action, i / total)
                    ctx.control.pause()
                    await ctx.control.checkpoint()
                    self._set_status(ctx, TaskStatus.RUNNING)
                    _reset(step)
                    continue
                if _passing(step.error) and step.id not in retried:
                    # A rate limit or a dropped connection is gone in a few
                    # seconds; spending a replan on it rewrites a good plan.
                    retried.add(step.id)
                    self._set_status(ctx, TaskStatus.RETRY_WAIT)
                    ctx.progress(f"Trying again in {RETRY_WAIT_S:.0f}s", i / total)
                    await asyncio.sleep(RETRY_WAIT_S)
                    await ctx.control.checkpoint()
                    self._set_status(ctx, TaskStatus.RUNNING)
                    _reset(step)
                    continue
                if self.memory:
                    self._remember_error(ctx, step)
                if replans >= self.max_replans:
                    self._finish(ctx, TaskStatus.FAILED, f"step '{step.title}' failed: {step.error}")
                    return
                replans += 1
                plan = await self.planner.replan(ctx, plan, step.error)
                self._adopt_plan(ctx, plan, revised=True)
                i = task.step_index
                continue

            ctx.events.emit(EventKind.STEP_DONE, step.summary or step.title, task_id=task.id,
                            agent_id=ctx.agent.id,
                            data={"step": step.to_dict(), "index": i, "total": total})
            ctx.progress(step.title, (i + 1) / total)
            self._save(ctx)
            i += 1

    # -- helpers -------------------------------------------------------------

    def _adopt_plan(self, ctx: AgentContext, plan: Plan, *, revised: bool) -> None:
        task = ctx.task
        task.steps = plan.steps
        task.plan_id = plan.id
        if self.store:
            self.store.save_plan(plan)
        ctx.events.emit(EventKind.PLAN_CREATED,
                        ("Revised plan" if revised else "Plan") + f": {len(plan.steps)} steps",
                        task_id=task.id, agent_id=ctx.agent.id,
                        data={"plan": plan.to_dict(), "revised": revised})
        self._save(ctx)

    def _set_status(self, ctx: AgentContext, status: TaskStatus) -> None:
        task = ctx.task
        if task.status is status:
            return
        if not task.status.can_become(status):
            log.warning("task %s: %s -> %s refused", task.id, task.status.value, status.value)
            return
        task.status = status
        task.updated_at = now()
        ctx.events.emit(EventKind.TASK_STATUS, status.value, task_id=task.id, agent_id=ctx.agent.id,
                        data={"status": status.value})
        self._save(ctx)

    def _finish(self, ctx: AgentContext, status: TaskStatus, error: str = "", *,
                verification: str = "") -> None:
        task = ctx.task
        if error:
            task.error = error
        if status is TaskStatus.COMPLETED:
            task.progress = 1.0
        if task.status.can_become(status):
            # OR-15: every ending says what was verified, what did not happen
            # and what next - written before the status changes, so whoever
            # hears of the ending can read it.
            from .report import report_for
            was, task.status = task.status, status
            rep = report_for(task, verification)
            task.status = was
            task.metadata["report"] = rep.to_dict()
            if status is TaskStatus.COMPLETED:
                task.result = rep.text()
        self._set_status(ctx, status)
        ctx.progress(status.value.capitalize())
        if self.memory:
            try:
                from agents.core import MemoryEntry
                self.memory.add(MemoryEntry(
                    scope=MemoryStore.project_scope(task.workspace), kind="task",
                    content=f"{task.title}: {status.value}" + (f" ({error})" if error else ""),
                    tags=[task.kind.value, status.value],
                    data={"task_id": task.id, "goal": task.goal, "steps": [s.title for s in task.steps]},
                ))
            except Exception:  # noqa: BLE001
                log.exception("memory write failed")
        self._save(ctx)

    def _remember_error(self, ctx: AgentContext, step: Step) -> None:
        try:
            from agents.core import MemoryEntry
            self.memory.add(MemoryEntry(  # type: ignore[union-attr]
                scope=MemoryStore.project_scope(ctx.task.workspace), kind="error",
                content=f"step '{step.title}' failed: {step.error}",
                tags=[ctx.task.kind.value], data={"task_id": ctx.task.id, "step_id": step.id},
            ))
        except Exception:  # noqa: BLE001
            log.exception("memory write failed")

    def _save(self, ctx: AgentContext) -> None:
        if self.store:
            try:
                self.store.save_task(ctx.task)
            except Exception:  # noqa: BLE001
                log.exception("task save failed")

