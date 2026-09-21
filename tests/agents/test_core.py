"""Smoke tests for agents.core contracts (models, events, contexts)."""
import asyncio
import os

import pytest

from agents.core import (
    AgentSpec, AgentState, Event, EventBus, EventKind, PermissionPolicy, Plan,
    RiskLevel, RunControl, Step, StepStatus, Task, TaskStatus, ToolCall,
    ToolContext, ToolResult, ToolSpec,
)


def test_task_roundtrip_preserves_nested_types():
    t = Task(title="x", goal="y", workspace="C:/tmp/ws",
             steps=[Step("a", tool_calls=[ToolCall("read_file", {"path": "a"})])])
    d = t.to_dict()
    assert d["status"] == "pending" and d["steps"][0]["tool_calls"][0]["tool_name"] == "read_file"
    t2 = Task.from_dict(d)
    assert isinstance(t2.steps[0], Step)
    assert isinstance(t2.steps[0].tool_calls[0], ToolCall)
    assert t2.status is TaskStatus.PENDING
    assert t2.step_index == 0


def test_step_index_skips_finished_steps():
    t = Task(title="x", goal="y", workspace=".",
             steps=[Step("a", status=StepStatus.DONE), Step("b"), Step("c")])
    assert t.step_index == 1
    t.steps[1].status = StepStatus.FAILED
    t.steps[2].status = StepStatus.SKIPPED
    assert t.step_index == 3


def test_agent_state_roundtrip():
    st = AgentState(spec=AgentSpec(name="Builder", goal="g", tools=["create_file"]))
    st2 = AgentState.from_dict(st.to_dict())
    assert isinstance(st2.spec, AgentSpec) and st2.spec.tools == ["create_file"]
    assert st2.id == st.id


def test_permission_policy():
    pol = PermissionPolicy(max_auto_risk=RiskLevel.LOW, denied_tools=["run_command"])
    assert pol.allows(ToolSpec("read_file", "r", risk=RiskLevel.SAFE))
    assert pol.allows(ToolSpec("create_file", "w", risk=RiskLevel.LOW))
    assert not pol.allows(ToolSpec("install_package", "i", risk=RiskLevel.MEDIUM))
    assert not pol.allows(ToolSpec("run_command", "x", risk=RiskLevel.SAFE))
    strict = PermissionPolicy(allowed_tools=["read_file"])
    assert not strict.allows(ToolSpec("create_file", "w", risk=RiskLevel.SAFE))


def test_always_allow_lifts_one_tool_over_the_risk_ceiling():
    pol = PermissionPolicy(max_auto_risk=RiskLevel.LOW)
    danger = ToolSpec("delete_path", "rm", risk=RiskLevel.HIGH)
    assert not pol.allows(danger)

    pol.allow_always("delete_path")
    assert pol.allows(danger)
    assert pol.allows(ToolSpec("create_file", "w", risk=RiskLevel.LOW))
    # everything else is still judged against the ceiling
    assert not pol.allows(ToolSpec("run_command", "x", risk=RiskLevel.MEDIUM))

    pol.allow_always("delete_path")          # idempotent
    assert pol.always_allow == ["delete_path"]

    # a standing yes never beats an explicit veto, nor the whitelist filter
    assert not PermissionPolicy(denied_tools=["delete_path"],
                                always_allow=["delete_path"]).allows(danger)
    assert not PermissionPolicy(allowed_tools=["read_file"],
                                always_allow=["delete_path"]).allows(danger)


def test_always_allow_survives_serialisation():
    pol = PermissionPolicy(always_allow=["delete_path"])
    assert PermissionPolicy.from_dict(pol.to_dict()).always_allow == ["delete_path"]


def test_tool_result_helpers():
    call = ToolCall("create_folder", {"path": "MyApp"})
    ok = ToolResult.success(call, "created", path="C:/x/MyApp")
    assert ok.ok and ok.call_id == call.id and ok.data["path"] == "C:/x/MyApp"
    bad = ToolResult.failure(call, "exists")
    assert not bad.ok and bad.error == "exists"


def test_tool_context_workspace_guard(tmp_path):
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    ctx = ToolContext(workspace=ws)
    assert ctx.is_inside_workspace("src/app.py")
    assert ctx.is_inside_workspace(os.path.join(ws, "a", "b"))
    assert not ctx.is_inside_workspace("..")
    assert not ctx.is_inside_workspace(str(tmp_path / "other"))
    assert ctx.resolve_path("src/app.py") == os.path.normpath(os.path.join(ws, "src/app.py"))


def test_event_bus_filters_and_history():
    bus = EventBus(history_size=10)
    seen: list[Event] = []
    off = bus.subscribe(seen.append, kinds=[EventKind.LOG], task_id="t1")
    bus.emit(EventKind.LOG, "a", task_id="t1")
    bus.emit(EventKind.LOG, "b", task_id="t2")          # wrong task
    bus.emit(EventKind.TASK_STATUS, "c", task_id="t1")  # wrong kind
    assert [e.message for e in seen] == ["a"]
    off()
    bus.emit(EventKind.LOG, "d", task_id="t1")
    assert len(seen) == 1
    assert len(bus.history()) == 4
    assert [e.message for e in bus.history(task_id="t1")] == ["a", "c", "d"]


def test_event_bus_survives_bad_subscriber():
    bus = EventBus()
    bus.subscribe(lambda e: 1 / 0)
    got = []
    bus.subscribe(got.append)
    bus.emit(EventKind.LOG, "still delivered")
    assert got and got[0].message == "still delivered"


def test_event_bus_async_queue():
    async def main():
        bus = EventBus()
        loop = asyncio.get_running_loop()
        q, off = bus.async_queue(loop, kinds=[EventKind.TOOL_RESULT])
        bus.emit(EventKind.TOOL_RESULT, "r1")
        bus.emit(EventKind.LOG, "ignored")
        ev = await asyncio.wait_for(q.get(), 1)
        assert ev.message == "r1" and q.empty()
        off()
    asyncio.run(main())


def test_run_control_pause_resume_cancel():
    async def main():
        c = RunControl()
        await c.checkpoint()                 # not paused: returns immediately
        c.pause()
        assert c.paused
        waiter = asyncio.create_task(c.checkpoint())
        await asyncio.sleep(0.01)
        assert not waiter.done()
        c.resume()
        await asyncio.wait_for(waiter, 1)
        c.cancel()
        with pytest.raises(asyncio.CancelledError):
            await c.checkpoint()
    asyncio.run(main())


def test_plan_serialises():
    p = Plan(task_id="t", steps=[Step("one"), Step("two")], rationale="because")
    p2 = Plan.from_dict(p.to_dict())
    assert [s.title for s in p2.steps] == ["one", "two"] and p2.rationale == "because"
