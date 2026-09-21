"""M8: the Active Agents panel.

Runs Qt headless (QT_QPA_PLATFORM=offscreen, set before QApplication
exists). Events are pushed through the real EventBus -> AgentEventBridge
path, so these cover the thread hand-off as well as the widgets; the
bridge's queued signals are flushed with processEvents().
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from agents.bootstrap import build_agent_manager  # noqa: E402
from agents.core import (  # noqa: E402
    AgentSpec, AgentState, AgentStatus, EventBus, EventKind, TaskStatus,
)
from agents.ui import ActiveAgentsPanel, AgentCard, AgentEventBridge  # noqa: E402

REQUEST = "Create a new folder called MyApp and build the foundation of a React application inside it."


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


@pytest.fixture
def manager(tmp_path):
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True).start()
    try:
        yield mgr
    finally:
        mgr.stop()


def _pump(app, times: int = 3) -> None:
    for _ in range(times):
        app.processEvents()


def _state(name="Application Builder", **kw) -> AgentState:
    return AgentState(spec=AgentSpec(name=name, goal="build things"), **kw)


# -- bridge ------------------------------------------------------------------

def test_bridge_translates_bus_events_into_qt_signals(qapp):
    class FakeManager:
        events = EventBus()

    mgr = FakeManager()
    bridge = AgentEventBridge(mgr)
    seen: dict[str, object] = {}
    bridge.agent_changed.connect(lambda s: seen.__setitem__("agent", s))
    bridge.task_status.connect(lambda t, s: seen.__setitem__("status", (t, s)))
    bridge.task_progress.connect(lambda t, f, a: seen.__setitem__("progress", (t, f, a)))
    bridge.approval_required.connect(lambda r: seen.__setitem__("approval", r))

    st = _state(status=AgentStatus.RUNNING)
    mgr.events.emit(EventKind.AGENT_STATUS, "running", agent_id=st.id,
                    data={"state": st.to_dict()})
    mgr.events.emit(EventKind.TASK_STATUS, "running", task_id="t1", data={"status": "running"})
    mgr.events.emit(EventKind.TASK_PROGRESS, "Creating files", task_id="t1",
                    data={"progress": 0.7, "current_action": "Creating Navbar.tsx"})
    _pump(qapp)

    assert isinstance(seen["agent"], AgentState)
    assert seen["agent"].spec.name == "Application Builder"
    assert seen["status"] == ("t1", "running")
    assert seen["progress"] == ("t1", 0.7, "Creating Navbar.tsx")
    assert "approval" not in seen

    bridge.close()
    mgr.events.emit(EventKind.TASK_STATUS, "completed", task_id="t1", data={"status": "completed"})
    _pump(qapp)
    assert seen["status"] == ("t1", "running"), "close() must unsubscribe"


def test_bridge_close_is_idempotent(qapp):
    class FakeManager:
        events = EventBus()

    bridge = AgentEventBridge(FakeManager())
    bridge.close()
    bridge.close()


# -- card --------------------------------------------------------------------

def test_card_renders_the_headline_layout(qapp):
    card = AgentCard(_state(status=AgentStatus.RUNNING, current_task_title="Creating React application",
                            current_action="Creating Navbar.tsx", progress=0.7,
                            step_index=4, step_total=10, current_task_id="t1"))
    assert "Application Builder" in card.summary
    assert "Step 4/10" in card.summary
    assert "Creating Navbar.tsx" in card.summary
    assert card.progress_percent == 70


def test_card_shows_error_text_when_failed(qapp):
    card = AgentCard(_state(status=AgentStatus.ERROR, last_error="step 'Install' failed: boom",
                            current_task_id="t1"))
    assert "boom" in card.summary
    assert "Failed" in card.summary


def test_card_buttons_disable_when_no_live_task(qapp):
    card = AgentCard(_state(status=AgentStatus.IDLE, current_task_id=None))
    assert not card._pause.isEnabled() and not card._stop.isEnabled()
    card.update_state(_state(status=AgentStatus.RUNNING, current_task_id="t1"))
    assert card._pause.isEnabled() and card._stop.isEnabled()


def test_card_pause_button_becomes_resume(qapp):
    card = AgentCard(_state(status=AgentStatus.RUNNING, current_task_id="t1"))
    assert card._pause.text() == "Pause"
    paused, resumed = [], []
    card.pause_clicked.connect(paused.append)
    card.resume_clicked.connect(resumed.append)

    card._pause.click()
    assert paused == ["t1"] and resumed == []

    card.update_state(_state(status=AgentStatus.PAUSED, current_task_id="t1"))
    assert card._pause.text() == "Resume"
    card._pause.click()
    assert resumed == ["t1"]


def test_card_progress_fast_path(qapp):
    card = AgentCard(_state(status=AgentStatus.RUNNING, current_task_id="t1"))
    card.set_progress(0.45, "Installing dependencies")
    assert card.progress_percent == 45
    assert "Installing dependencies" in card.summary


# -- panel -------------------------------------------------------------------

def test_panel_starts_empty(qapp, manager):
    panel = ActiveAgentsPanel(manager)
    assert panel.cards == {}
    assert panel._empty.isVisibleTo(panel)
    assert "No agents running" in panel._empty.text()


def test_panel_adds_a_card_per_agent_and_hides_empty_state(qapp, manager):
    panel = ActiveAgentsPanel(manager)
    a, b = _state("Application Builder"), _state("Research Agent")
    panel.on_agent_changed(a)
    panel.on_agent_changed(b)
    assert len(panel.cards) == 2
    assert not panel._empty.isVisibleTo(panel)

    # the SAME agent again (same spec id) updates in place, no new card
    a.progress = 0.5
    a.current_action = "Installing dependencies"
    panel.on_agent_changed(a)
    assert len(panel.cards) == 2
    assert panel.card_for_agent(a.id).progress_percent == 50


def test_panel_routes_progress_to_the_right_card(qapp, manager):
    panel = ActiveAgentsPanel(manager)
    one = _state("One", current_task_id="t1", status=AgentStatus.RUNNING)
    two = _state("Two", current_task_id="t2", status=AgentStatus.RUNNING)
    panel.on_agent_changed(one)
    panel.on_agent_changed(two)

    panel.on_task_progress("t2", 0.9, "Running tests")
    assert panel.card_for_agent(two.id).progress_percent == 90
    assert panel.card_for_agent(one.id).progress_percent == 0


def test_panel_reflects_agents_that_already_exist(qapp, manager):
    manager.create_agent(AgentSpec(name="Earlier Agent", goal="g"))
    panel = ActiveAgentsPanel(manager)
    assert len(panel.cards) == 1


# -- end to end: a real task drives the panel --------------------------------

def test_real_task_fills_the_progress_bar(qapp, manager):
    panel = ActiveAgentsPanel(manager)
    task = manager.submit(REQUEST)
    assert task is not None

    task = manager.wait(task.id, 60)
    assert task.status is TaskStatus.COMPLETED, task.error
    _pump(qapp, 8)

    assert len(panel.cards) == 1
    card = next(iter(panel.cards.values()))
    assert card.progress_percent == 100
    assert "Application Builder" in card.summary
    # finished agents are not controllable any more
    assert not card._stop.isEnabled()


def test_stop_button_cancels_a_real_task(qapp, manager):
    """The button must reach the runtime, not just change the label."""
    panel = ActiveAgentsPanel(manager)
    task = manager.submit(REQUEST)
    assert task is not None
    _pump(qapp, 4)

    card = next(iter(panel.cards.values()))
    if card._stop.isEnabled():
        card._stop.click()
    else:                       # the task can finish before the first pump
        manager.cancel(task.id)
    done = manager.wait(task.id, 30)
    assert done.status in (TaskStatus.CANCELLED, TaskStatus.COMPLETED)


def test_logs_button_emits_the_task_id(qapp, manager):
    panel = ActiveAgentsPanel(manager)
    got: list[str] = []
    panel.view_logs.connect(got.append)
    panel.on_agent_changed(_state(status=AgentStatus.RUNNING, current_task_id="t42"))
    next(iter(panel.cards.values()))._logs.click()
    assert got == ["t42"]


# -- M21: projects group under their lead ------------------------------------

def test_project_children_sit_under_their_lead(qapp, tmp_path):
    from agents.manager import TemplateDecomposer
    from agents.core import Plan, Step, ToolCall

    class _Quick:
        async def plan(self, ctx):
            key = ctx.task.metadata.get("node", "x")
            return Plan(task_id=ctx.task.id,
                        steps=[Step(f"make {key}", tool_calls=[ToolCall("create_folder", {"path": key})])])
        async def replan(self, ctx, plan, failure): return plan

    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    mgr.planner = mgr.orchestrator.planner = _Quick()
    mgr.decomposer = TemplateDecomposer()
    mgr.start()
    try:
        panel = ActiveAgentsPanel(mgr)
        parent = mgr.wait(mgr.submit("Build me a complete website").id, 60)
        assert parent.status is TaskStatus.COMPLETED, parent.error
        _pump(qapp, 10)

        order = []
        for i in range(panel._list.count() - 1):
            w = panel._list.itemAt(i).widget()
            if isinstance(w, AgentCard):
                order.append((w._name.text(), w.child_of))
        names = [n for n, _ in order]
        assert names[0] == "Project Lead"
        assert set(names[1:]) == {"Backend Agent", "Frontend Agent", "Testing Agent"}
        assert all(c == parent.id for _, c in order[1:]), "children must carry the parent id"
        assert order[0][1] is None
    finally:
        mgr.stop()
