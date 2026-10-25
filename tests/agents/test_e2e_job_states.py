"""Honest job states (EN-05, the Massive Direction Plan).

A rate limit waits and tries the same step again instead of spending a
replan; a sign-in waits for the user instead of failing; a refused plan is
"rejected", not "stopped"; and one rule decides every status change, so an
ended job never comes back.
"""
from __future__ import annotations

import time

from agents.core import Task, TaskStatus, ToolResult
from agents.manager import orchestrator

from tests.agents.test_manager import Boom, FakeRegistry, ListPlanner, MkDir, make_manager


class Flaky(Boom):
    """Fails once with the given error, then works."""

    def __init__(self, error: str):
        self.error, self.calls = error, 0

    async def run(self, args, ctx):
        self.calls += 1
        if self.calls == 1:
            return ToolResult(call_id="", tool_name=self.name, ok=False, error=self.error)
        return ToolResult(call_id="", tool_name=self.name, ok=True, output="worked")


def _run(tmp_path, tool, **kw):

    planner = ListPlanner([("Fetch", [("boom", {})]), ("Finish", [])])
    mgr = make_manager(tmp_path, planner=planner, tools=FakeRegistry(MkDir(), tool), **kw)
    seen: list[str] = []
    mgr.events.subscribe(lambda ev: seen.append(ev.data.get("status", "")) if ev.data else None)
    return mgr, planner, seen


def test_a_rate_limit_waits_and_retries_the_same_step(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "RETRY_WAIT_S", 0)
    tool = Flaky("429 rate limit exceeded")
    mgr, planner, seen = _run(tmp_path, tool)
    try:
        task = mgr.wait(mgr.submit_task(Task(title="t", goal="g", workspace=str(tmp_path))).id, 10)
        assert task.status is TaskStatus.COMPLETED, task.error
        assert "retry_wait" in seen and tool.calls == 2
        assert planner.replans == 0, "a passing fault must not rewrite a good plan"
    finally:
        mgr.stop()


def test_a_sign_in_waits_for_the_user_then_goes_on(tmp_path):
    tool = Flaky("this site needs a sign-in; ask the user to do this step")
    mgr, planner, seen = _run(tmp_path, tool)
    try:
        task = mgr.submit_task(Task(title="t", goal="g", workspace=str(tmp_path)))
        for _ in range(200):
            if task.status is TaskStatus.WAITING_USER:
                break
            time.sleep(0.02)
        assert task.status is TaskStatus.WAITING_USER and task.current_action.startswith("Your turn")
        assert mgr.resume(task.id), "Resume is how the user says they did it"
        task = mgr.wait(task.id, 10)
        assert task.status is TaskStatus.COMPLETED and tool.calls == 2 and planner.replans == 0
    finally:
        mgr.stop()


def test_one_rule_decides_every_change():
    S = TaskStatus
    for ended in (S.COMPLETED, S.FAILED, S.CANCELLED, S.REJECTED):
        assert ended.is_terminal and not ended.can_become(S.RUNNING)
    assert not S.RUNNING.can_become(S.PENDING), "nothing goes back to the queue"
    assert not S.RUNNING.can_become(S.REJECTED), "only a plan being decided can be rejected"
    assert not S.PAUSED.can_become(S.COMPLETED), "only a running or checking job finishes"
    for live in (S.RETRY_WAIT, S.WAITING_USER, S.NEEDS_ATTENTION, S.PAUSED):
        assert live.can_become(S.RUNNING) and live.can_become(S.CANCELLED)
