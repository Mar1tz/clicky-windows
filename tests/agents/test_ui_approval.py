"""M10: plan viewer and approval dialog.

The interesting tests are the two-threaded ones. A parked task waits on
the runtime thread while the dialog lives on the GUI thread, so a plain
`manager.wait()` would deadlock the test: nothing would pump Qt, the
dialog would never appear, and the approval would never resolve. Hence
`_pump_until`, which spins the Qt event loop while watching the task.
"""
from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from agents.bootstrap import build_agent_manager  # noqa: E402
from agents.core import (  # noqa: E402
    ApprovalRequest, RiskLevel, StepStatus, TaskStatus, ToolCall,
)
from agents.ui import ApprovalController, ApprovalDialog, PlanView  # noqa: E402
from agents.ui.approval import format_arguments  # noqa: E402

REQUEST = "Create a new folder called MyApp and build the foundation of a React application inside it."


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


def _pump_until(app, predicate, timeout: float = 30.0) -> bool:
    """Spin Qt until `predicate()` or the clock runs out."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return predicate()


def _auto(monkeypatch, action: str):
    """Make every dialog answer itself instead of waiting for a human."""
    def fake_exec(self):
        getattr(self, f"click_{action}")()
        return 1 if self.decision else 0
    monkeypatch.setattr(ApprovalDialog, "exec", fake_exec)


# -- plan view ---------------------------------------------------------------

@pytest.fixture
def finished(tmp_path, qapp):
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True).start()
    task = mgr.wait(mgr.submit(REQUEST).id, 60)
    assert task.status is TaskStatus.COMPLETED, task.error
    qapp.processEvents()
    try:
        yield mgr, task
    finally:
        mgr.stop()


def test_plan_view_lists_every_step_with_a_tick(qapp, finished):
    mgr, task = finished
    view = PlanView(mgr, task.id)
    lines = view.lines
    assert len(lines) == len(task.steps) == 10
    assert lines[0] == "✓ Create project folder"
    assert all(l.startswith("✓") for l in lines), lines


def test_plan_view_shows_mixed_statuses(qapp, finished):
    mgr, task = finished
    task.steps[2].status = StepStatus.RUNNING
    task.steps[3].status = StepStatus.PENDING
    task.steps[4].status = StepStatus.FAILED
    task.steps[5].status = StepStatus.SKIPPED
    view = PlanView(mgr, task.id)
    lines = view.lines
    assert lines[2].startswith("▸") and lines[3].startswith("○")
    assert lines[4].startswith("✕") and lines[5].startswith("–")


def test_plan_view_is_empty_before_planning(qapp, finished):
    mgr, _task = finished
    view = PlanView(mgr, "no-such-task")
    assert view.lines == []
    assert view._empty.isVisibleTo(view)


def test_plan_view_refreshes_from_events(qapp, finished):
    mgr, task = finished
    view = PlanView(mgr, task.id)
    task.steps = task.steps[:3]
    from agents.core import EventKind
    mgr.events.emit(EventKind.STEP_DONE, "x", task_id=task.id)
    qapp.processEvents()
    assert len(view.lines) == 3


# -- dialog ------------------------------------------------------------------

def test_format_arguments_keeps_every_key_and_truncates_bulk():
    text = format_arguments({"path": "MyApp/App.jsx", "content": "x" * 900}, max_value=50)
    assert "path: MyApp/App.jsx" in text
    assert "900 chars" in text and len(text) < 400
    assert format_arguments({}) == "(no arguments)"


def test_dialog_shows_tool_risk_and_workspace(qapp, finished):
    mgr, task = finished
    req = ApprovalRequest(task_id=task.id, agent_id=task.agent_id,
                          call=ToolCall("delete_path", {"path": "MyApp/src"}),
                          reason="delete_path is high risk")
    dlg = ApprovalDialog(mgr, req)
    from PyQt6.QtWidgets import QLabel
    shown = "\n".join(w.text() for w in dlg.findChildren(QLabel))
    assert dlg.windowTitle() == "Approve action"
    assert "delete_path" in shown                     # what it wants to run
    assert "MyApp/src" in shown                       # what it would touch
    assert "HIGH" in shown                            # how bad it could be
    assert os.path.normpath(task.workspace) in shown  # where
    assert dlg._always_btn is not None
    assert "delete_path" in dlg._always_btn.text()
    dlg.click_deny()
    assert dlg.decision is False and dlg.always is False


def test_dialog_approve_and_always(qapp, finished):
    mgr, task = finished
    agent = mgr.get_agent(task.agent_id)
    req = ApprovalRequest(task_id=task.id, agent_id=task.agent_id,
                          call=ToolCall("delete_path", {"path": "x"}), reason="risky")

    dlg = ApprovalDialog(mgr, req)
    dlg.click_approve()
    assert dlg.decision is True and dlg.always is False
    assert "delete_path" not in agent.spec.permissions.always_allow

    dlg2 = ApprovalDialog(mgr, req)
    dlg2.click_always()
    assert dlg2.decision is True and dlg2.always is True
    assert agent.spec.permissions.always_allow == ["delete_path"]


def test_plan_approval_dialog_shows_the_steps_not_arguments(qapp, finished):
    mgr, task = finished
    req = ApprovalRequest(task_id=task.id, agent_id=task.agent_id,
                          call=ToolCall("approve_plan", {"plan_id": task.plan_id}),
                          reason="Plan with 10 steps needs approval")
    dlg = ApprovalDialog(mgr, req)
    assert dlg.windowTitle() == "Approve plan"
    assert dlg._always_btn is None, "a plan is not a tool you can bless forever"
    assert len(dlg._plan.lines) == 10
    with pytest.raises(RuntimeError):
        dlg.click_always()


# -- the milestone gate: real tasks, real approvals --------------------------

def test_plan_approval_approve_runs_the_task(tmp_path, qapp, monkeypatch):
    _auto(monkeypatch, "approve")
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True,
                              require_plan_approval=True).start()
    controller = ApprovalController(mgr)
    try:
        task = mgr.submit(REQUEST)
        assert _pump_until(qapp, lambda: task.status.is_terminal), task.status
        assert task.status is TaskStatus.COMPLETED, task.error
        assert (tmp_path / "MyApp" / "package.json").is_file()
    finally:
        controller.close()
        mgr.stop()


def test_plan_approval_deny_cancels_the_task(tmp_path, qapp, monkeypatch):
    _auto(monkeypatch, "deny")
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True,
                              require_plan_approval=True).start()
    controller = ApprovalController(mgr)
    try:
        task = mgr.submit(REQUEST)
        assert _pump_until(qapp, lambda: task.status.is_terminal), task.status
        assert task.status is TaskStatus.REJECTED
        assert not (tmp_path / "MyApp").exists(), "a denied plan must build nothing"
    finally:
        controller.close()
        mgr.stop()


def test_risky_tool_raises_a_dialog_mid_run(tmp_path, qapp, monkeypatch):
    """With the ceiling at SAFE, the first file write needs a human."""
    _auto(monkeypatch, "approve")
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True,
                              max_auto_risk=RiskLevel.SAFE).start()
    controller = ApprovalController(mgr)
    seen: list[tuple[str, bool]] = []
    controller.resolved.connect(lambda rid, ok: seen.append((rid, ok)))
    try:
        task = mgr.submit(REQUEST)
        assert _pump_until(qapp, lambda: task.status.is_terminal, 60), task.status
        assert task.status is TaskStatus.COMPLETED, task.error
        assert len(seen) >= 2, "every write above the ceiling should have been asked about"
        assert all(ok for _rid, ok in seen)
    finally:
        controller.close()
        mgr.stop()


def test_always_allow_stops_the_asking(tmp_path, qapp, monkeypatch):
    """The point of the standing approval: asked once, not ten times."""
    _auto(monkeypatch, "always")
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True,
                              max_auto_risk=RiskLevel.SAFE).start()
    controller = ApprovalController(mgr)
    asked: list[str] = []
    controller.resolved.connect(lambda rid, ok: asked.append(rid))
    try:
        task = mgr.submit(REQUEST)
        assert _pump_until(qapp, lambda: task.status.is_terminal, 60), task.status
        assert task.status is TaskStatus.COMPLETED, task.error
        # create_folder once, create_file once - not once per file
        assert len(asked) <= 3, f"asked {len(asked)} times despite 'always allow'"
        policy = mgr.get_agent(task.agent_id).spec.permissions
        assert "create_file" in policy.always_allow
        assert (tmp_path / "MyApp" / "src" / "App.jsx").is_file()
    finally:
        controller.close()
        mgr.stop()


def test_denied_tool_fails_the_task_cleanly(tmp_path, qapp, monkeypatch):
    _auto(monkeypatch, "deny")
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True,
                              max_auto_risk=RiskLevel.SAFE).start()
    controller = ApprovalController(mgr)
    try:
        task = mgr.submit(REQUEST)
        assert _pump_until(qapp, lambda: task.status.is_terminal, 60), task.status
        assert task.status is TaskStatus.FAILED
        assert "permission denied" in task.error.lower()
    finally:
        controller.close()
        mgr.stop()
