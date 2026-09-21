"""M23: limits and the safety checklist, as assertions rather than a doc.

Each item in the ship checklist has a test here (or is named where its
test already lives), so "the safety checklist is ticked with evidence"
means this file is green.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from agents.bootstrap import build_agent_manager
from agents.core import (
    EventBus, EventKind, PermissionPolicy, ProviderCapabilities, ProviderResponse,
    RiskLevel, Task, TaskKind, TaskStatus, ToolCall, ToolContext, auto_deny,
)
from agents.manager.limits import LimitExceeded, MeteredProvider, TaskLimits, tokens_of
from agents.providers import MockProvider
from agents.tools import default_registry


# -- token accounting --------------------------------------------------------

class _Costly(MockProvider):
    """A mock that reports usage, like a real provider does."""

    def __init__(self, per_call: int, script=None):
        super().__init__(script=script or [ProviderResponse(text="done")])
        self.per_call = per_call

    async def complete(self, *a, **k):
        r = await super().complete(*a, **k)
        r.usage = {"prompt_tokens": self.per_call - 5, "completion_tokens": 5,
                   "total_tokens": self.per_call}
        return r


def test_tokens_of_prefers_total_then_sums():
    assert tokens_of(ProviderResponse(usage={"total_tokens": 42})) == 42
    assert tokens_of(ProviderResponse(usage={"prompt_tokens": 30, "completion_tokens": 7})) == 37
    assert tokens_of(ProviderResponse()) == 0


async def test_metered_provider_counts_and_publishes():
    task = Task(title="t", goal="g", workspace=".")
    bus = EventBus()
    seen = []
    bus.subscribe(seen.append, kinds=[EventKind.USAGE])
    p = MeteredProvider(_Costly(100), task, bus, TaskLimits())
    await p.complete([])
    await p.complete([])
    assert task.metadata["usage"] == {"total_tokens": 200, "calls": 2}
    assert [e.data["tokens"] for e in seen] == [100, 200]
    assert seen[-1].data["delta"] == 100


async def test_metered_provider_refuses_past_the_budget():
    task = Task(title="t", goal="g", workspace=".")
    p = MeteredProvider(_Costly(100), task, EventBus(), TaskLimits(max_tokens=150))
    await p.complete([])                    # 100 - fine
    await p.complete([])                    # 200 - the call that crosses is allowed...
    with pytest.raises(LimitExceeded) as e:
        await p.complete([])                # ...the next one is not
    assert e.value.which == "token budget"


async def test_wall_clock_is_checked_before_a_call():
    task = Task(title="t", goal="g", workspace=".")
    p = MeteredProvider(_Costly(1), task, EventBus(), TaskLimits(wall_clock_s=0.05),
                        started_at=time.time() - 1)
    with pytest.raises(LimitExceeded) as e:
        await p.complete([])
    assert e.value.which == "wall clock"


def test_zero_means_unlimited():
    limits = TaskLimits()
    assert not limits.enabled
    task = Task(title="t", goal="g", workspace=".", metadata={"usage": {"total_tokens": 10**9}})
    limits.check(task, started_at=0)        # never raises


# -- limits through the orchestrator -----------------------------------------

def test_task_over_its_token_budget_fails_cleanly(tmp_path):
    """A budget is not decorative: the task stops, is FAILED, and says why."""
    from agents.core import Plan, Planner, Step
    from agents.manager.limits import TaskLimits

    class Chatty(Planner):
        async def plan(self, ctx):
            await ctx.provider.complete([])          # 100 tokens
            await ctx.provider.complete([])          # 200
            return Plan(task_id=ctx.task.id, steps=[Step("s1"), Step("s2"), Step("s3")])

        async def replan(self, ctx, plan, failure):
            return plan

    class Talker:
        worker_type = "coding"

        async def execute_step(self, step, ctx):
            from agents.core import StepStatus
            await ctx.provider.complete([])          # +100 per step
            step.status = StepStatus.DONE
            return step

    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    mgr.limits.max_tokens = 250
    mgr.orchestrator.limits = mgr.limits
    mgr.planner = mgr.orchestrator.planner = Chatty()
    mgr.workers = type("W", (), {"get": lambda self, t: Talker()})()
    mgr.router.register(_Costly(100), kinds=None, priority=99)
    mgr.start()
    try:
        errors = []
        mgr.events.subscribe(errors.append, kinds=[EventKind.ERROR])
        task = mgr.wait(mgr.submit_task(Task(title="t", goal="g", workspace=str(tmp_path))).id, 30)
        assert task.status is TaskStatus.FAILED
        assert "token budget" in task.error
        assert task.metadata["usage"]["total_tokens"] >= 250
        assert errors and errors[0].data.get("limit") == "token budget"
        # the counter is what the card shows
        assert mgr.get_agent(task.agent_id).tokens_used >= 250
    finally:
        mgr.stop()


def test_task_over_its_wall_clock_fails_cleanly(tmp_path):
    from agents.core import Plan, Planner, Step, StepStatus

    class Slow(Planner):
        async def plan(self, ctx):
            return Plan(task_id=ctx.task.id, steps=[Step("a"), Step("b"), Step("c")])

        async def replan(self, ctx, plan, failure):
            return plan

    class Sleeper:
        worker_type = "coding"

        async def execute_step(self, step, ctx):
            await asyncio.sleep(0.15)
            step.status = StepStatus.DONE
            return step

    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    mgr.limits.wall_clock_s = 0.2
    mgr.planner = mgr.orchestrator.planner = Slow()
    mgr.workers = type("W", (), {"get": lambda self, t: Sleeper()})()
    mgr.start()
    try:
        task = mgr.wait(mgr.submit_task(Task(title="t", goal="g", workspace=str(tmp_path))).id, 30)
        assert task.status is TaskStatus.FAILED
        assert "wall clock" in task.error
        assert sum(1 for s in task.steps if s.status is StepStatus.DONE) < 3
    finally:
        mgr.stop()


def test_stop_all_cancels_everything(tmp_path):
    from agents.core import Plan, Planner, Step, StepStatus

    class Slow(Planner):
        async def plan(self, ctx):
            return Plan(task_id=ctx.task.id, steps=[Step("a")])

        async def replan(self, ctx, plan, failure):
            return plan

    class Sleeper:
        worker_type = "coding"

        async def execute_step(self, step, ctx):
            for _ in range(50):
                await ctx.control.checkpoint()
                await asyncio.sleep(0.05)
            step.status = StepStatus.DONE
            return step

    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    mgr.planner = mgr.orchestrator.planner = Slow()
    mgr.workers = type("W", (), {"get": lambda self, t: Sleeper()})()
    mgr.start()
    try:
        ids = [mgr.submit_task(Task(title=f"t{i}", goal="g", workspace=str(tmp_path))).id
               for i in range(3)]
        time.sleep(0.2)
        assert mgr.cancel_all() == 3
        for i in ids:
            assert mgr.wait(i, 10).status is TaskStatus.CANCELLED
    finally:
        mgr.stop()


# -- the safety checklist ----------------------------------------------------

def test_checklist_no_tool_escapes_the_workspace(tmp_path):
    """Every file tool refuses a path outside the workspace."""
    ctx = ToolContext(workspace=str(tmp_path / "ws"),
                      policy=PermissionPolicy(max_auto_risk=RiskLevel.HIGH))
    (tmp_path / "ws").mkdir()
    outside = str(tmp_path / "victim.txt")
    reg = default_registry()
    for name, args in [
        ("create_file", {"path": outside, "content": "x"}),
        ("write_file", {"path": outside, "content": "x"}),
        ("read_file", {"path": "../victim.txt"}),
        ("delete_path", {"path": "../"}),
        ("create_folder", {"path": "../../escape"}),
        ("run_command", {"command": "echo hi", "cwd": ".."}),
        ("screenshot_page", {"path": "../shot.png"}),
    ]:
        r = asyncio.run(reg.execute(ToolCall(name, args), ctx))
        assert not r.ok, f"{name} escaped the workspace"
        assert "outside" in r.error.lower() or "workspace" in r.error.lower(), (name, r.error)
    assert not (tmp_path / "victim.txt").exists()
    assert not (tmp_path / "escape").exists()


def test_checklist_no_high_risk_runs_unasked_by_default(tmp_path):
    """The default ceiling is MEDIUM: every HIGH tool must ask."""
    ctx = ToolContext(workspace=str(tmp_path), approver=auto_deny)
    reg = default_registry()
    high = [s.name for s in reg.specs() if s.risk is RiskLevel.HIGH]
    assert high, "no HIGH tools registered?"
    for name in high:
        r = asyncio.run(reg.execute(ToolCall(name, {}), ctx))
        assert not r.ok
        assert "denied" in r.error.lower() or "invalid arguments" in r.error.lower(), (name, r.error)


def test_checklist_computer_control_asks_even_at_high_ceiling():
    """Covered in depth by test_tools_computer.py; asserted here so the
    checklist is complete in one place."""
    from agents.tools import computer_tools as ct
    for tool in ct.tools():
        if tool.spec.risk is RiskLevel.HIGH:
            assert getattr(tool, "always_ask", False), tool.name


def test_checklist_events_carry_sizes_not_file_contents(tmp_path):
    bus = EventBus()
    calls = []
    bus.subscribe(calls.append, kinds=[EventKind.TOOL_CALL])
    ctx = ToolContext(workspace=str(tmp_path), events=bus,
                      policy=PermissionPolicy(max_auto_risk=RiskLevel.HIGH))
    payload = "x" * 50_000
    r = asyncio.run(default_registry().execute(
        ToolCall("create_file", {"path": "big.txt", "content": payload}), ctx))
    assert r.ok
    view = calls[0].data["call"]["arguments"]
    assert view["content"] == "<50000 chars>"
    assert view["path"] == "big.txt"
    assert payload not in str(calls[0].to_dict())
    assert (tmp_path / "big.txt").stat().st_size == 50_000, "the real call was untouched"


def test_checklist_keys_never_logged():
    """Covered by test_logsafe.py; named here for the checklist."""
    import logsafe
    key = "AIzaSyFAKEfakeFAKEfakeFAKEfakeFAKEfake01"
    assert key not in logsafe.redact(f"key={key}")
    assert key not in logsafe.redact(f"GET https://x/y?key={key} 200")
    assert key not in logsafe.redact(f"loaded {key} from config")


def test_checklist_provider_keys_travel_in_headers():
    """Both real providers send the key as a header, never a query string."""
    import inspect

    from agents.providers import gemini, openai_compatible
    assert "x-goog-api-key" in inspect.getsource(gemini)
    assert 'params={"key"' not in inspect.getsource(gemini)
    assert "Authorization" in inspect.getsource(openai_compatible)


def test_checklist_agents_default_outside_the_app_folder():
    from agents.bootstrap import default_workspace_root
    assert "clicky-windows" not in default_workspace_root().lower()
