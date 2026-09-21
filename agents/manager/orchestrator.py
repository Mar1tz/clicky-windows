"""Orchestrator: runs ONE task through plan -> execute -> verify.

State machine (see docs/agents/ARCHITECTURE.md §1):

    PENDING -> PLANNING -> [WAITING_APPROVAL] -> RUNNING -> VERIFYING -> COMPLETED
                                                   |  \\-> FAILED (after replan budget)
                                                   \\----> CANCELLED (RunControl.cancel)

It owns no threads and no state beyond the task it is given: everything
it learns goes out on the EventBus and into the TaskStore, so a UI that
opens mid-run can rebuild the picture from either.
"""
from __future__ import annotations

import asyncio
import logging
import traceback
from typing import Optional

from agents.core import (
    AgentContext, ApprovalRequest, BaseWorker, EventKind, MemoryStore, Plan,
    Planner, Step, StepStatus, TaskStatus, TaskStore, ToolCall, Verifier, new_id, now,
)

log = logging.getLogger("agents.orchestrator")


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
    ):
        self.planner = planner
        self.verifier = verifier
        self.store = store
        self.memory = memory
        self.require_plan_approval = require_plan_approval
        self.max_replans = max_replans

    # -- public --------------------------------------------------------------

    async def run_task(self, ctx: AgentContext, worker: BaseWorker, *, resume: bool = False):
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
                    self._finish(ctx, TaskStatus.CANCELLED, "plan rejected")
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
                task.result = self._result_text(task, vr.summary)
                self._finish(ctx, TaskStatus.COMPLETED)
            else:
                self._finish(ctx, TaskStatus.FAILED, f"final verification failed: {vr.summary}")
        except asyncio.CancelledError:
            self._finish(ctx, TaskStatus.CANCELLED, "cancelled")
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
        plan = await self.planner.plan(ctx)
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
        i = 0
        while i < len(task.steps):
            await ctx.control.checkpoint()
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
        task.status = status
        task.updated_at = now()
        ctx.events.emit(EventKind.TASK_STATUS, status.value, task_id=task.id, agent_id=ctx.agent.id,
                        data={"status": status.value})
        self._save(ctx)

    def _finish(self, ctx: AgentContext, status: TaskStatus, error: str = "") -> None:
        task = ctx.task
        if error:
            task.error = error
        if status is TaskStatus.COMPLETED:
            task.progress = 1.0
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

    @staticmethod
    def _result_text(task, verification_summary: str) -> str:
        done = [s.title for s in task.steps if s.status is StepStatus.DONE]
        lines = [f"Completed: {task.title}", *[f"  ✓ {t}" for t in done]]
        if verification_summary:
            lines.append(f"Verified: {verification_summary}")
        return "\n".join(lines)
