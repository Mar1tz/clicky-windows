"""M9: the activity timeline.

The important test here is `test_history_survives_a_restart`: it rebuilds
the AgentManager against the same SQLite file and asserts the timeline
still reads correctly. That proves the event history is genuinely durable
rather than an in-memory ring buffer that merely looks like one.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from agents.bootstrap import build_agent_manager  # noqa: E402
from agents.core import Event, EventKind, TaskStatus  # noqa: E402
from agents.ui import ActivityTimeline  # noqa: E402
from agents.ui.timeline import describe  # noqa: E402

REQUEST = "Create a new folder called MyApp and build the foundation of a React application inside it."


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


@pytest.fixture
def finished(tmp_path, qapp):
    """A completed task, with its manager still open."""
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True).start()
    task = mgr.wait(mgr.submit(REQUEST).id, 60)
    assert task.status is TaskStatus.COMPLETED, task.error
    qapp.processEvents()
    try:
        yield mgr, task
    finally:
        mgr.stop()


# -- describe() --------------------------------------------------------------

def test_describe_names_the_file_not_the_tool():
    ev = Event(kind=EventKind.TOOL_RESULT, data={"result": {
        "tool_name": "create_file", "ok": True,
        "data": {"path": "C:/ws/MyApp/package.json"}}})
    assert describe(ev)[0] == "Created package.json"


def test_describe_handles_a_folder_and_a_failure():
    folder = Event(kind=EventKind.TOOL_RESULT, data={"result": {
        "tool_name": "create_folder", "ok": True, "data": {"path": "C:/ws/MyApp/"}}})
    assert describe(folder)[0] == "Created folder MyApp"

    bad = Event(kind=EventKind.TOOL_RESULT, data={"result": {
        "tool_name": "run_command", "ok": False, "error": "exit 1: command not found"}})
    head, detail = describe(bad)
    assert head == "run_command failed" and "command not found" in detail


def test_describe_numbers_the_plan():
    ev = Event(kind=EventKind.PLAN_CREATED, message="Plan: 2 steps", data={"plan": {
        "steps": [{"title": "Create project folder"}, {"title": "Create package.json"}]}})
    head, detail = describe(ev)
    assert head == "Plan: 2 steps"
    assert detail == "1. Create project folder\n2. Create package.json"


def test_describe_counts_verification_checks():
    ev = Event(kind=EventKind.VERIFICATION, message="ok", data={
        "scope": "step", "result": {"checks": [{"passed": True}, {"passed": True}]}})
    assert "2/2 checks" in describe(ev)[0]


def test_describe_shows_step_position():
    ev = Event(kind=EventKind.STEP_STARTED, message="Create package.json",
               data={"index": 1, "total": 10})
    assert describe(ev)[0] == "Step 2/10: Create package.json"


# -- the timeline widget -----------------------------------------------------

def test_timeline_replays_a_finished_task(qapp, finished):
    mgr, task = finished
    tl = ActivityTimeline(mgr, task.id)
    lines = tl.lines
    assert lines, "timeline is empty for a completed task"
    assert any(l.startswith("Task started") for l in lines)
    assert any("Plan:" in l or "steps" in l for l in lines)
    assert any(l == "Created package.json" for l in lines), lines
    assert any(l.startswith("Verified") for l in lines)
    assert any("completed" in l.lower() for l in lines)


def test_timeline_hides_progress_noise(qapp, finished):
    mgr, task = finished
    tl = ActivityTimeline(mgr, task.id)
    assert all(r.kind is not EventKind.TASK_PROGRESS for r in tl.rows)
    assert all(r.kind is not EventKind.AGENT_STATUS for r in tl.rows)


def test_timeline_ignores_other_tasks(qapp, finished):
    mgr, task = finished
    tl = ActivityTimeline(mgr, task.id)
    before = len(tl.rows)
    tl.on_event(Event(kind=EventKind.STEP_DONE, message="from elsewhere", task_id="other"))
    assert len(tl.rows) == before


def test_timeline_never_duplicates_a_replayed_event(qapp, finished):
    """History is loaded first, then live events arrive - an event that is
    in both must appear once."""
    mgr, task = finished
    tl = ActivityTimeline(mgr, task.id)
    before = len(tl.rows)
    for ev in mgr.store.events(task.id, limit=5):
        tl.on_event(ev)
    assert len(tl.rows) == before


def test_timeline_appends_live_rows(qapp, finished):
    mgr, task = finished
    tl = ActivityTimeline(mgr, task.id)
    before = len(tl.rows)
    mgr.events.emit(EventKind.STEP_DONE, "Ran the tests", task_id=task.id)
    qapp.processEvents()
    assert len(tl.rows) == before + 1
    assert tl.lines[-1] == "Ran the tests"


def test_timeline_empty_for_unknown_task(qapp, finished):
    mgr, _task = finished
    tl = ActivityTimeline(mgr, "no-such-task")
    assert tl.rows == []
    assert tl._empty.isVisibleTo(tl)


# -- the milestone gate ------------------------------------------------------

def test_history_survives_a_restart(tmp_path, qapp):
    """Run a task, throw the manager away, build a new one on the same DB,
    and the timeline must still show what happened."""
    db = str(tmp_path / "agents.db")

    first = build_agent_manager(workspace_root=str(tmp_path), db_path=db).start()
    task_id = first.submit(REQUEST).id
    done = first.wait(task_id, 60)
    assert done.status is TaskStatus.COMPLETED, done.error
    live_lines = ActivityTimeline(first, task_id).lines
    first.stop()

    second = build_agent_manager(workspace_root=str(tmp_path), db_path=db).start()
    try:
        assert second.get_task(task_id) is not None, "task did not persist"
        replayed = ActivityTimeline(second, task_id).lines
        assert replayed, "no history after restart"
        assert any(l == "Created package.json" for l in replayed), replayed
        assert replayed == live_lines, "replayed history differs from the live view"
    finally:
        second.stop()


def test_logs_button_opens_a_timeline(qapp, finished):
    """The M8 hook and the M9 window are actually connected."""
    from agents.ui import ActiveAgentsPanel, open_timeline
    mgr, task = finished
    panel = ActiveAgentsPanel(mgr)
    opened: list[str] = []
    panel.view_logs.connect(opened.append)
    card = next(iter(panel.cards.values()))
    card._logs.click()
    assert opened == [task.id]

    tl = open_timeline(mgr, task.id)
    try:
        assert tl.lines
    finally:
        tl.close()
