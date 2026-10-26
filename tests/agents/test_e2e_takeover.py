"""Take over and do it (the person hands Tora their mouse and keyboard).

One yes for the job, time-boxed; then the actions that otherwise ask before
every click run without asking. A no keeps control with them and nothing
moves. Esc (the kill switch) stops it all; a sensitive action - money,
sending, a filing - still asks; auto-approve never hands over control.
"""
from __future__ import annotations

import threading
import time
from typing import Any

from agents.core import ApprovalRequest, RiskLevel, Task, TaskKind, TaskStatus, ToolCall
from agents.tools import Registry
from agents.tools.base import Tool, make_spec
from agents.tools.computer_tools import kill_switch
from tests.agents.test_manager import ListPlanner, make_manager


class Click(Tool):
    spec = make_spec("fake_click", "Click.", risk=RiskLevel.HIGH, category="computer")
    always_ask = True

    def __init__(self):
        self.clicks = 0

    async def run(self, args: dict[str, Any], ctx) -> Any:
        if kill_switch.tripped:
            return self.fail("computer control is stopped (kill switch)")
        self.clicks += 1
        return self.ok("clicked")


class Pay(Click):
    spec = make_spec("fake_pay", "Pay.", risk=RiskLevel.HIGH, category="computer")
    sensitive = True


def _run(tmp_path, answers, steps, tool=None, takeover=True):
    tool = tool or Click()
    planner = ListPlanner([(f"Step {i}", [call]) for i, call in enumerate(steps)])
    mgr = make_manager(tmp_path, planner=planner, tools=Registry([tool, Pay()]))
    asked = []
    stop = threading.Event()

    def answer():
        while not stop.is_set():
            for req in mgr.pending_approvals():
                asked.append(req.call.tool_name)
                mgr.approve(req.id, answers.get(req.call.tool_name, False))
            time.sleep(0.01)
    t = threading.Thread(target=answer, daemon=True)
    t.start()
    try:
        task = Task(title="Fill the timesheet", goal="fill the timesheet", workspace=str(tmp_path),
                    kind=TaskKind.COMPUTER, worker_type="coding",
                    metadata={"takeover": True} if takeover else {})
        task = mgr.wait(mgr.submit_task(task).id, 10)
    finally:
        stop.set()
        mgr.stop()
    return task, tool, asked


def test_one_yes_for_the_whole_job(tmp_path):
    kill_switch.reset()
    task, tool, asked = _run(tmp_path, {"take_control": True},
                             [("fake_click", {}), ("fake_click", {}), ("fake_click", {})])
    assert task.status is TaskStatus.COMPLETED and tool.clicks == 3
    assert asked == ["take_control"], "asked once - not before every click"


def test_a_no_keeps_control_with_them(tmp_path):
    kill_switch.reset()
    task, tool, asked = _run(tmp_path, {"take_control": False}, [("fake_click", {})])
    assert task.status is TaskStatus.REJECTED and tool.clicks == 0 and asked == ["take_control"]


def test_without_take_over_every_click_asks(tmp_path):
    kill_switch.reset()
    task, tool, asked = _run(tmp_path, {"fake_click": True}, [("fake_click", {}), ("fake_click", {})],
                             takeover=False)
    assert asked == ["fake_click", "fake_click"] and tool.clicks == 2


def test_paying_still_asks_while_she_is_in_control(tmp_path):
    kill_switch.reset()
    task, tool, asked = _run(tmp_path, {"take_control": True, "fake_pay": False},
                             [("fake_click", {}), ("fake_pay", {})])
    assert asked == ["take_control", "fake_pay"] and tool.clicks == 1


def test_esc_stops_her(tmp_path):
    kill_switch.reset()
    kill_switch.trip()
    try:
        task, tool, asked = _run(tmp_path, {"take_control": True, "fake_click": False}, [("fake_click", {})])
        assert tool.clicks == 0
    finally:
        kill_switch.reset()


def test_auto_approve_never_hands_over_control(tmp_path):
    mgr = make_manager(tmp_path)
    try:
        mgr.auto_approve = True
        req = ApprovalRequest("t", "a", ToolCall("take_control", {}), "use your mouse")
        assert not mgr._auto_approves(req)
    finally:
        mgr.stop()


def test_the_voice_hands_it_over_as_a_computer_job():
    import asyncio
    from types import SimpleNamespace

    import companion_manager as cm
    started = []

    def submit(task):
        started.append(task)
        task.agent_id = "a"
        return task
    agents = SimpleNamespace(list_tasks=lambda **kw: [], submit_task=submit, get_agent=lambda i: None,
                             resume=lambda i: True, skills=None, workspace_root="C:/jobs")
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    mgr._agents, mgr._trace_readings, mgr._last_ref = agents, [], None

    async def no_wait(task):
        return None
    mgr._wait_for_plan = no_wait
    asyncio.run(mgr._tool_delegate_task({"goal": "fill in my timesheet in the HR portal", "take_over": True,
                                         "minutes": 20}))
    task = started[0]
    assert task.kind is TaskKind.COMPUTER and task.metadata["takeover"] and task.metadata["takeover_minutes"] == 20
