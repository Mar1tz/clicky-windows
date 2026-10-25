"""AgentManager / Orchestrator / AgentRuntime tests with in-file fakes.

These do not depend on agents.tools / providers / workers / planning /
verification - they exercise the state machine, threading, approvals,
pause/resume/cancel and agent-state tracking through the contracts only.
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import Any, Optional

import pytest

from agents.core import (
    AgentContext, AgentProvider, AgentSpec, AgentStatus, BaseTool, BaseWorker, EventKind,
    Intent, IntentClassifier, Plan, Planner, PermissionPolicy, ProviderCapabilities,
    ProviderMessage, ProviderResponse, ProviderRouter, RiskLevel, RunControl, Step,
    StepStatus, Task, TaskKind, TaskStatus, ToolCall, ToolContext, ToolRegistry,
    ToolResult, ToolSpec, VerificationResult, Verifier, ConnectedAccount,
)
from agents.manager import AgentManager, AgentRuntime, Orchestrator


# -- fakes -------------------------------------------------------------------

class MkDir(BaseTool):
    spec = ToolSpec("create_folder", "mkdir", {"type": "object", "properties": {"path": {"type": "string"}},
                                              "required": ["path"]}, RiskLevel.LOW, "file")

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        p = ctx.resolve_path(args["path"])
        os.makedirs(p, exist_ok=True)
        return ToolResult(call_id="", tool_name=self.name, ok=True, output=f"created {p}", data={"path": p})


class Boom(BaseTool):
    spec = ToolSpec("boom", "fails", risk=RiskLevel.LOW)

    async def run(self, args, ctx):
        return ToolResult(call_id="", tool_name=self.name, ok=False, error="boom")


class Danger(BaseTool):
    spec = ToolSpec("danger", "needs approval", risk=RiskLevel.HIGH)

    async def run(self, args, ctx):
        return ToolResult(call_id="", tool_name=self.name, ok=True, output="did the dangerous thing")


class FakeRegistry(ToolRegistry):
    def __init__(self, *tools: BaseTool):
        self._t = {t.name: t for t in tools}

    def register(self, tool): self._t[tool.name] = tool
    def get(self, name): return self._t[name]
    def names(self): return list(self._t)
    def specs(self, names=None): return [t.spec for n, t in self._t.items() if names is None or n in names]

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolResult:
        if ctx.control:
            await ctx.control.checkpoint()
        tool = self._t.get(call.tool_name)
        if tool is None:
            return ToolResult.failure(call, f"unknown tool {call.tool_name}")
        if not ctx.policy.allows(tool.spec):
            from agents.core import ApprovalRequest
            ok = await ctx.approver(ApprovalRequest(ctx.task_id or "", ctx.agent_id or "", call, "risk"))
            if not ok:
                return ToolResult.failure(call, "permission denied")
        res = await tool.run(call.arguments, ctx)
        res.call_id = call.id
        if ctx.events:
            ctx.events.emit(EventKind.TOOL_RESULT, res.output or res.error, task_id=ctx.task_id,
                            agent_id=ctx.agent_id, data={"result": res.to_dict()})
        return res


class FakeProvider(AgentProvider):
    name = "fake"

    async def complete(self, messages, tools=None, *, system="", model=None, temperature=0.2, max_tokens=4096):
        return ProviderResponse(text="done")

    def capabilities(self): return ProviderCapabilities(local=True, cost_tier=0)


class FakeRouter(ProviderRouter):
    def __init__(self): self._p = FakeProvider()
    def register(self, provider, *, kinds=None, priority=0): self._p = provider
    def pick(self, kind, *, hint=None, needs_vision=False): return self._p
    def providers(self): return [self._p]
    def accounts(self): return [ConnectedAccount(provider="fake")]


class ScriptedWorker(BaseWorker):
    """Executes pre-filled tool_calls; steps without calls just succeed."""
    worker_type = "coding"

    async def execute_step(self, step: Step, ctx: AgentContext) -> Step:
        step.status = StepStatus.RUNNING
        tctx = ctx.tool_context()
        for call in step.tool_calls:
            await ctx.control.checkpoint()
            ctx.progress(f"Running {call.tool_name}")
            res = await ctx.tools.execute(call, tctx)
            step.results.append(res)
            if not res.ok:
                step.status, step.error = StepStatus.FAILED, res.error
                return step
        step.status, step.summary = StepStatus.DONE, f"{step.title} ok"
        return step


class SlowWorker(ScriptedWorker):
    async def execute_step(self, step, ctx):
        for _ in range(20):
            await ctx.control.checkpoint()
            await asyncio.sleep(0.02)
        return await super().execute_step(step, ctx)


class Workers:
    def __init__(self, w: BaseWorker): self._w = w
    def get(self, worker_type): return self._w


class ListPlanner(Planner):
    def __init__(self, steps): self._steps = steps; self.replans = 0

    async def plan(self, ctx):
        return Plan(task_id=ctx.task.id, steps=[Step(t, tool_calls=[ToolCall(*c) for c in calls])
                                                 for t, calls in self._steps], rationale="scripted")

    async def replan(self, ctx, plan, failure):
        self.replans += 1
        for s in plan.steps:
            if s.status is StepStatus.FAILED:
                s.status = StepStatus.SKIPPED
        idx = next((i for i, s in enumerate(plan.steps) if s.status is StepStatus.SKIPPED), 0)
        plan.steps.insert(idx + 1, Step(f"Fix: {failure}"))
        return plan


class AlwaysOk(Verifier):
    async def verify_step(self, step, ctx): return VerificationResult.passed("step ok")
    async def verify_task(self, ctx): return VerificationResult.passed("task ok", workspace=True)


class FailFinal(AlwaysOk):
    async def verify_task(self, ctx): return VerificationResult(ok=False, summary="missing file")


class YesClassifier(IntentClassifier):
    async def classify(self, text, *, context=None):
        return Intent(is_task="create" in text.lower(), title="Create MyApp", goal=text,
                      kind=TaskKind.CODING, workspace_hint="MyApp", reply="On it.")


def make_manager(tmp_path, *, planner=None, verifier=None, worker=None, tools=None, **kw) -> AgentManager:
    mgr = AgentManager(
        tools=tools or FakeRegistry(MkDir(), Boom(), Danger()),
        router=FakeRouter(),
        workers=Workers(worker or ScriptedWorker()),
        planner=planner or ListPlanner([("Create folder", [("create_folder", {"path": "MyApp"})]),
                                        ("Finish", [])]),
        verifier=verifier or AlwaysOk(),
        workspace_root=str(tmp_path),
        classifier=YesClassifier(),
        **kw,
    )
    return mgr.start()


# -- runtime -----------------------------------------------------------------

def test_runtime_runs_on_its_own_thread_and_stops():
    rt = AgentRuntime().start()
    assert rt.running

    async def whoami():
        return threading.current_thread().name

    assert rt.call(whoami()) == "agent-runtime"
    fut = rt.submit(asyncio.sleep(0.01, result=42))
    assert fut.result(1) == 42
    rt.stop()
    assert not rt.running


def test_runtime_call_from_loop_thread_raises():
    rt = AgentRuntime().start()

    async def inner():
        coro = asyncio.sleep(0)
        try:
            with pytest.raises(RuntimeError):
                rt.call(coro)
        finally:
            coro.close()
        return True

    assert rt.call(inner())
    rt.stop()


# -- happy path --------------------------------------------------------------

def test_submit_text_runs_to_completion_and_creates_folder(tmp_path):
    mgr = make_manager(tmp_path)
    try:
        seen: list[EventKind] = []
        mgr.events.subscribe(lambda e: seen.append(e.kind))
        task = mgr.submit("Create a new folder called MyApp")
        assert task is not None and task.metadata["subdir"] == "MyApp"
        task = mgr.wait(task.id, 10)
        assert task.status is TaskStatus.COMPLETED, task.error
        assert (tmp_path / "MyApp").is_dir()
        assert task.progress == 1.0
        assert "Completed: Create MyApp" in task.result
        for k in (EventKind.TASK_CREATED, EventKind.PLAN_CREATED, EventKind.STEP_STARTED,
                  EventKind.STEP_DONE, EventKind.VERIFICATION, EventKind.TASK_STATUS):
            assert k in seen, k
        assert [s.status for s in task.steps] == [StepStatus.DONE, StepStatus.DONE]
        agent = mgr.list_agents()[0]
        assert agent.spec.name == "Application Builder"
        assert agent.status is AgentStatus.IDLE and agent.progress == 1.0
    finally:
        mgr.stop()


def test_chat_text_is_not_a_task(tmp_path):
    mgr = make_manager(tmp_path)
    try:
        assert mgr.submit("what is a React component?") is None
        assert mgr.list_tasks() == []
    finally:
        mgr.stop()


def test_submit_task_object_directly(tmp_path):
    mgr = make_manager(tmp_path)
    try:
        t = Task(title="t", goal="g", workspace=str(tmp_path / "ws"))
        t = mgr.wait(mgr.submit(t).id, 10)
        assert t.status is TaskStatus.COMPLETED and (tmp_path / "ws").is_dir()
    finally:
        mgr.stop()


# -- failure / replan --------------------------------------------------------

def test_failed_step_triggers_one_replan_then_continues(tmp_path):
    planner = ListPlanner([("Break", [("boom", {})]), ("After", [])])
    mgr = make_manager(tmp_path, planner=planner)
    try:
        task = mgr.wait(mgr.submit_task(Task(title="t", goal="g", workspace=str(tmp_path))).id, 10)
        assert planner.replans == 1
        assert task.status is TaskStatus.COMPLETED, task.error
        titles = [(s.title, s.status) for s in task.steps]
        assert titles[0] == ("Break", StepStatus.SKIPPED)
        assert titles[1][0].startswith("Fix:") and titles[1][1] is StepStatus.DONE
        assert titles[2] == ("After", StepStatus.DONE)
    finally:
        mgr.stop()


def test_replan_budget_exhausted_fails_task(tmp_path):
    planner = ListPlanner([("Break", [("boom", {})])])
    mgr = make_manager(tmp_path, planner=planner, max_replans=0)
    try:
        task = mgr.wait(mgr.submit_task(Task(title="t", goal="g", workspace=str(tmp_path))).id, 10)
        assert task.status is TaskStatus.FAILED and "boom" in task.error
        assert mgr.list_agents()[0].status is AgentStatus.ERROR
    finally:
        mgr.stop()


def test_step_verification_failure_marks_step_failed(tmp_path):
    class StepFails(AlwaysOk):
        async def verify_step(self, step, ctx):
            return VerificationResult(ok=False, summary="folder missing")
    mgr = make_manager(tmp_path, verifier=StepFails(), max_replans=0)
    try:
        task = mgr.wait(mgr.submit_task(Task(title="t", goal="g", workspace=str(tmp_path))).id, 10)
        assert task.status is TaskStatus.FAILED and "verification failed" in task.error
    finally:
        mgr.stop()


def test_final_verification_failure(tmp_path):
    mgr = make_manager(tmp_path, verifier=FailFinal())
    try:
        task = mgr.wait(mgr.submit_task(Task(title="t", goal="g", workspace=str(tmp_path))).id, 10)
        assert task.status is TaskStatus.FAILED and "missing file" in task.error
    finally:
        mgr.stop()


def test_worker_exception_yields_failed_not_crash(tmp_path):
    class Crashy(BaseWorker):
        worker_type = "coding"
        async def execute_step(self, step, ctx): raise ValueError("kaboom")
    mgr = make_manager(tmp_path, worker=Crashy())
    try:
        errors = []
        mgr.events.subscribe(errors.append, kinds=[EventKind.ERROR])
        task = mgr.wait(mgr.submit_task(Task(title="t", goal="g", workspace=str(tmp_path))).id, 10)
        assert task.status is TaskStatus.FAILED and "kaboom" in task.error
        assert errors and "traceback" in errors[0].data
    finally:
        mgr.stop()


# -- pause / resume / cancel -------------------------------------------------

def test_pause_resume(tmp_path):
    mgr = make_manager(tmp_path, worker=SlowWorker())
    try:
        task = mgr.submit_task(Task(title="t", goal="g", workspace=str(tmp_path)))
        time.sleep(0.05)
        assert mgr.pause(task.id)
        time.sleep(0.1)
        assert task.status is TaskStatus.PAUSED
        assert mgr.list_agents()[0].status is AgentStatus.PAUSED
        assert not mgr.resume("nope")
        assert mgr.resume(task.id)
        task = mgr.wait(task.id, 10)
        assert task.status is TaskStatus.COMPLETED
    finally:
        mgr.stop()


def test_cancel(tmp_path):
    mgr = make_manager(tmp_path, worker=SlowWorker())
    try:
        task = mgr.submit_task(Task(title="t", goal="g", workspace=str(tmp_path)))
        time.sleep(0.05)
        assert mgr.cancel(task.id)
        task = mgr.wait(task.id, 10)
        assert task.status is TaskStatus.CANCELLED
        assert mgr.list_agents()[0].status is AgentStatus.STOPPED
        assert mgr.list_tasks(active_only=True) == []
    finally:
        mgr.stop()


# -- approvals ---------------------------------------------------------------

def test_high_risk_call_waits_for_approval(tmp_path):
    planner = ListPlanner([("Danger", [("danger", {})])])
    mgr = make_manager(tmp_path, planner=planner,
                       default_policy=PermissionPolicy(max_auto_risk=RiskLevel.MEDIUM))
    try:
        reqs = []
        mgr.events.subscribe(reqs.append, kinds=[EventKind.APPROVAL_REQUIRED])
        task = mgr.submit_task(Task(title="t", goal="g", workspace=str(tmp_path)))
        for _ in range(50):
            if reqs:
                break
            time.sleep(0.02)
        assert reqs, "no approval request raised"
        assert task.status is TaskStatus.WAITING_APPROVAL
        pending = mgr.pending_approvals()
        assert len(pending) == 1 and pending[0].call.tool_name == "danger"
        assert mgr.approve(pending[0].id, True)
        task = mgr.wait(task.id, 10)
        assert task.status is TaskStatus.COMPLETED
        assert task.steps[0].results[0].output == "did the dangerous thing"
        assert mgr.pending_approvals() == []
    finally:
        mgr.stop()


def test_denied_approval_fails_step(tmp_path):
    planner = ListPlanner([("Danger", [("danger", {})])])
    mgr = make_manager(tmp_path, planner=planner, max_replans=0)
    try:
        reqs = []
        mgr.events.subscribe(reqs.append, kinds=[EventKind.APPROVAL_REQUIRED])
        task = mgr.submit_task(Task(title="t", goal="g", workspace=str(tmp_path)))
        for _ in range(50):
            if reqs:
                break
            time.sleep(0.02)
        mgr.approve(mgr.pending_approvals()[0].id, False)
        task = mgr.wait(task.id, 10)
        assert task.status is TaskStatus.FAILED and "permission denied" in task.error
    finally:
        mgr.stop()


def test_plan_approval_gate(tmp_path):
    mgr = make_manager(tmp_path, require_plan_approval=True)
    try:
        task = mgr.submit_task(Task(title="t", goal="g", workspace=str(tmp_path)))
        for _ in range(50):
            if mgr.pending_approvals():
                break
            time.sleep(0.02)
        req = mgr.pending_approvals()[0]
        assert req.call.tool_name == "approve_plan" and task.status is TaskStatus.WAITING_APPROVAL
        mgr.approve(req.id, False)
        task = mgr.wait(task.id, 10)
        assert task.status is TaskStatus.REJECTED
    finally:
        mgr.stop()


def test_cancel_while_waiting_for_approval(tmp_path):
    mgr = make_manager(tmp_path, require_plan_approval=True)
    try:
        task = mgr.submit_task(Task(title="t", goal="g", workspace=str(tmp_path)))
        for _ in range(50):
            if mgr.pending_approvals():
                break
            time.sleep(0.02)
        mgr.cancel(task.id)
        task = mgr.wait(task.id, 10)
        assert task.status is TaskStatus.CANCELLED and mgr.pending_approvals() == []
    finally:
        mgr.stop()


# -- concurrency -------------------------------------------------------------

def test_many_tasks_run_concurrently(tmp_path):
    mgr = make_manager(tmp_path, worker=SlowWorker())
    try:
        ids = [mgr.submit_task(Task(title=f"t{i}", goal="g", workspace=str(tmp_path / f"w{i}"))).id
               for i in range(5)]
        t0 = time.time()
        results = [mgr.wait(i, 10) for i in ids]
        assert all(t.status is TaskStatus.COMPLETED for t in results)
        assert time.time() - t0 < 3, "tasks ran serially"
        assert len(mgr.list_agents()) == 5
    finally:
        mgr.stop()
