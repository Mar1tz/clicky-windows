"""OR-32: waitpoints - a time, a file, an answer, each a thing of its own.

A job that waits says on what, lets go of the screen while it waits for a
time or a file, takes it back in turn, and stops waiting when it is
cancelled. One question asked by two jobs is one waitpoint.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import threading
import time

from agents.core import EventBus, EventKind, RunControl, ToolCall, ToolContext, WaitKind, WaitStatus
from agents.manager.waitpoints import Waitpoints
from agents.tools import default_registry, wait_tools


def _ctx(hub, task_id="t1"):
    return ToolContext(workspace=".", task_id=task_id, extras={"waitpoints": hub}, control=RunControl())


def test_times_of_day_mean_the_next_one():
    now = dt.datetime(2026, 10, 27, 20, 0)
    assert wait_tools.when("21:30", now) == dt.datetime(2026, 10, 27, 21, 30)
    assert wait_tools.when("9am", now) == dt.datetime(2026, 10, 28, 9, 0), "9am has passed today: tomorrow's"
    assert wait_tools.when("12am", now) == dt.datetime(2026, 10, 28, 0, 0)
    assert wait_tools.when("2026-10-30T08:15", now) == dt.datetime(2026, 10, 30, 8, 15)
    assert wait_tools.when("teatime", now) is None and wait_tools.when("25:00", now) is None


def test_a_job_gets_the_waits_its_goal_needs():
    assert wait_tools.for_goal("tidy my downloads") == ["ask_user"]
    assert "wait_until" in wait_tools.for_goal("at 9am email the summary to Lee")
    assert "wait_for_file" in wait_tools.for_goal("when the export appears, rename it")
    reg = default_registry()
    assert all(reg.get(n).spec.safe_to_replay() for n in ("wait_until", "wait_for_file", "ask_user"))


def test_a_question_waits_for_the_answer_and_two_jobs_share_it():
    hub = Waitpoints(EventBus())
    seen = []
    hub.events.subscribe(seen.append, kinds=[EventKind.WAITPOINT])

    async def go():
        reg = default_registry()
        ask = asyncio.ensure_future(reg.execute(ToolCall("ask_user", {"question": "Which month?"}), _ctx(hub)))
        await asyncio.sleep(0.05)
        twin = hub.create(WaitKind.ANSWER, "t2", prompt="Which month?", idempotency_key="t1:ask:which month?")
        assert len(hub.pending()) == 1 and twin.task_ids == ["t1", "t2"], "one question, two jobs"
        assert hub.answer("October") is not None
        return await ask
    result = asyncio.run(go())
    assert result.ok and result.output == "They answered: October"
    assert [e.data["waitpoint"]["status"] for e in seen] == ["waiting", "completed"]


def test_a_file_wait_ends_when_it_appears_or_times_out(tmp_path):
    hub = Waitpoints(EventBus())
    target = tmp_path / "export.csv"
    threading.Timer(0.2, lambda: target.write_text("a,b")).start()

    async def go(path, minutes):
        return await default_registry().execute(
            ToolCall("wait_for_file", {"path": str(path), "timeout_minutes": minutes}), _ctx(hub))
    import agents.manager.waitpoints as w
    w.POLL_S = 0.05
    try:
        assert asyncio.run(go(target, 1)).ok
        missing = asyncio.run(go(tmp_path / "never.csv", 0.002))
        assert not missing.ok and "did not appear" in missing.error
    finally:
        w.POLL_S = 1.0


def test_a_cancelled_job_stops_waiting():
    hub = Waitpoints(EventBus())
    ctx = _ctx(hub)

    async def go():
        wait = asyncio.ensure_future(default_registry().execute(
            ToolCall("wait_until", {"minutes": 60}), ctx))
        await asyncio.sleep(0.05)
        ctx.control.cancel()
        try:
            return await asyncio.wait_for(wait, 3)
        except asyncio.CancelledError:
            return None
    asyncio.run(go())
    assert hub.pending() == [], "its wait ended with it"


def test_a_job_lets_go_of_the_screen_while_it_waits(tmp_path):
    """Through the manager: WAITING while it waits for a time, the claims
    let go, RUNNING again after - and it finishes."""
    from agents.bootstrap import build_agent_manager
    from agents.core import Plan, Step, Task, TaskStatus
    import agents.manager.waitpoints as w

    class _WaitPlanner:
        async def plan(self, ctx):
            soon = (dt.datetime.now() + dt.timedelta(seconds=1)).isoformat(timespec="seconds")
            return Plan(task_id=ctx.task.id, steps=[
                Step("wait a moment", tool_calls=[ToolCall("wait_until", {"time": soon})]),
                Step("then make it", tool_calls=[ToolCall("create_folder", {"path": "later"})])])

        async def replan(self, ctx, plan, failure):
            return plan

    w.POLL_S = 0.05
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    mgr.planner = mgr.orchestrator.planner = _WaitPlanner()
    mgr.start()
    statuses = []
    mgr.events.subscribe(lambda ev: statuses.append(ev.data.get("status")), kinds=[EventKind.TASK_STATUS])
    try:
        task = Task(title="Later", goal="at the next minute make a folder", workspace=str(tmp_path))
        mgr.submit_task(task)
        seen_waiting = False
        deadline = time.time() + 10
        while time.time() < deadline and not seen_waiting:
            seen_waiting = "waiting" in statuses
            if seen_waiting:
                assert not [c for c, holder in mgr._held.items() if holder == task.id], "claims let go"
            time.sleep(0.02)
        done = mgr.wait(task.id, 20)
        assert seen_waiting and done.status is TaskStatus.COMPLETED, done.error
        assert (tmp_path / "later").is_dir()
    finally:
        w.POLL_S = 1.0
        mgr.stop()
