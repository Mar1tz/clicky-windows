"""Honest job states after a crash (EN-05 to EN-07, the Massive Direction Plan).

A job used to come back from a crash paused, and Resume re-ran the step that
was running "because tools are idempotent". A command, a push or a click is
not: re-running it can do it twice. Now the step's history (the TOOL_CALL
event, committed before the tool runs) says whether a call that could change
something never answered; such a job comes back as "needs attention", naming
the tool, and only the user decides to go on. A job that has ended stays
ended, whatever a late event says.
"""
from __future__ import annotations

from agents.core import Event, EventKind, StepStatus, TaskStatus
from tests.agents.test_resume import REQUEST, _manager, _stage_interruption


def _crash_mid_call(tmp_path, tool: str, arguments: dict):
    first = _manager(tmp_path).start()
    try:
        task = first.wait(first.submit(REQUEST).id, 60)
        assert task.status is TaskStatus.COMPLETED
        _stage_interruption(first.store, task, done=2)
        step = task.steps[2]
        first.store.append_event(Event(kind=EventKind.TOOL_CALL, message=f"{tool}", task_id=task.id,
                                       data={"call": {"id": "c-unanswered", "tool_name": tool, "arguments": arguments}},
                                       timestamp=(step.started_at or task.created_at) + 0.5))
    finally:
        first.stop()
    return task


def test_a_command_in_flight_comes_back_as_needs_attention(tmp_path):
    task = _crash_mid_call(tmp_path, "run_command", {"command": "npm install"})
    second = _manager(tmp_path).start()
    try:
        back = second.get_task(task.id)
        assert back.status is TaskStatus.NEEDS_ATTENTION
        assert back.metadata["outcome_unknown"]["tool"] == "run_command"
        assert back.metadata["outcome_unknown"]["arguments"] == {"command": "npm install"}
        assert "may have run before HeyTora stopped" in back.current_action
        assert second.resume(task.id), "they checked, and chose to go on"
        done = second.wait(task.id, 60)
        assert done.status is TaskStatus.COMPLETED and "outcome_unknown" not in done.metadata
    finally:
        second.stop()


def test_a_read_in_flight_is_simply_paused(tmp_path):
    task = _crash_mid_call(tmp_path, "read_file", {"path": "package.json"})
    second = _manager(tmp_path).start()
    try:
        back = second.get_task(task.id)
        assert back.status is TaskStatus.PAUSED and "outcome_unknown" not in back.metadata
    finally:
        second.stop()


def test_an_ended_job_is_never_brought_back():
    from types import SimpleNamespace
    from agents.core import Task
    from agents.manager.orchestrator import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    orch.store = None
    task = Task(title="t", goal="g", workspace=".")
    task.status = TaskStatus.CANCELLED
    sent = []
    ctx = SimpleNamespace(task=task, agent=SimpleNamespace(id="a"),
                          events=SimpleNamespace(emit=lambda *a, **k: sent.append(a)))
    orch._save = lambda c: None
    orch._set_status(ctx, TaskStatus.RUNNING)
    assert task.status is TaskStatus.CANCELLED and sent == []


def test_the_page_says_needs_you_and_offers_resume():
    from ui import app_pages  # noqa: F401 - the app imports the pages first (agents_page imports back)
    from ui.agents_page import status_words
    assert status_words("needs_attention")[0] == "Needs you"
    assert StepStatus.RUNNING                               # (imported for the staged step above)


def test_what_may_run_again_is_said_by_the_tool_not_guessed_from_risk(tmp_path):
    """OR-31: run_tests is MEDIUM but running it twice changes nothing - a
    job cut off mid-run is simply paused; run_command stays "needs you"."""
    task = _crash_mid_call(tmp_path, "run_tests", {})
    second = _manager(tmp_path).start()
    try:
        back = second.get_task(task.id)
        assert back.status is TaskStatus.PAUSED and "outcome_unknown" not in back.metadata
        tools = second.tools
        assert tools.get("run_tests").spec.safe_to_replay() and tools.get("open_app").spec.safe_to_replay()
        assert not tools.get("type_text").spec.safe_to_replay() and not tools.get("git_push").spec.safe_to_replay()
        assert tools.get("read_file").spec.safe_to_replay(), "SAFE and LOW: judged by risk, as before"
    finally:
        second.stop()
