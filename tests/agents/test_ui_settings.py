"""M11: agent settings, the tray entry, and concurrency limits.

The gate is `test_saved_risk_level_changes_behaviour`: saving "safe" must
actually make a file write stop and ask. A settings page whose values are
never read is worse than no settings page, so it is tested end to end
against a real task rather than by asserting the value came back.
"""
from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from agents.bootstrap import build_agent_manager  # noqa: E402
from agents.core import EventKind, RiskLevel, Task, TaskStatus  # noqa: E402
from agents.ui.settings import AgentSettingsDialog  # noqa: E402

REQUEST = "Create a new folder called MyApp and build the foundation of a React application inside it."


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


class FakeConfig:
    """Stands in for config.cfg: same fields, same setters, no .env."""

    def __init__(self, **kw):
        self.agent_workspace = kw.get("agent_workspace", "")
        self.agent_auto_risk = kw.get("agent_auto_risk", "medium")
        self.agent_plan_approval = kw.get("agent_plan_approval", False)
        self.agent_max_concurrent = kw.get("agent_max_concurrent", 3)
        self.agent_wall_clock_s = kw.get("agent_wall_clock_s", 1800)
        self.agent_max_tokens = kw.get("agent_max_tokens", 300000)
        self.written: dict[str, object] = {}

    def set_agent_workspace(self, v): self.agent_workspace = v; self.written["workspace"] = v
    def set_agent_auto_risk(self, v): self.agent_auto_risk = v; self.written["risk"] = v
    def set_agent_plan_approval(self, v): self.agent_plan_approval = v; self.written["plan"] = v
    def set_agent_max_concurrent(self, v): self.agent_max_concurrent = v; self.written["max"] = v
    def set_agent_wall_clock_s(self, v): self.agent_wall_clock_s = v; self.written["wall"] = v
    def set_agent_max_tokens(self, v): self.agent_max_tokens = v; self.written["tokens"] = v


@pytest.fixture
def manager(tmp_path):
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True).start()
    try:
        yield mgr
    finally:
        mgr.stop()


# -- the dialog --------------------------------------------------------------

def test_dialog_shows_the_current_settings(qapp, manager, tmp_path):
    cfg = FakeConfig(agent_auto_risk="high", agent_plan_approval=True,
                     agent_max_concurrent=5, agent_workspace=str(tmp_path))
    dlg = AgentSettingsDialog(manager, config=cfg)
    assert dlg.workspace.text() == str(tmp_path)
    assert dlg.risk.currentData() == "high"
    assert dlg.plan_approval.isChecked()
    assert dlg.max_concurrent.value() == 5


def test_dialog_falls_back_to_the_managers_workspace(qapp, manager):
    dlg = AgentSettingsDialog(manager, config=FakeConfig())
    assert dlg.workspace.text() == manager.workspace_root
    assert dlg.risk.currentData() == "medium"


def test_save_persists_and_applies_live(qapp, manager, tmp_path):
    cfg = FakeConfig()
    dlg = AgentSettingsDialog(manager, config=cfg)
    new_ws = str(tmp_path / "elsewhere")
    dlg.workspace.setText(new_ws)
    dlg.risk.setCurrentIndex(0)                 # safe
    dlg.plan_approval.setChecked(True)
    dlg.max_concurrent.setValue(2)
    dlg.wall_clock.setValue(45)             # minutes
    dlg.max_tokens.setValue(120)            # thousands

    saved = []
    dlg.saved.connect(lambda: saved.append(True))
    dlg.save()

    assert saved, "saved signal not emitted"
    assert cfg.written == {"workspace": new_ws, "risk": "safe", "plan": True, "max": 2,
                           "wall": 45 * 60, "tokens": 120_000}
    assert manager.limits.wall_clock_s == 45 * 60 and manager.limits.max_tokens == 120_000
    # and the running manager changed, with no restart
    assert manager.default_policy.max_auto_risk is RiskLevel.SAFE
    assert manager.orchestrator.require_plan_approval is True
    assert manager.max_concurrent == 2
    assert manager.workspace_root == os.path.abspath(new_ws)
    assert os.path.isdir(new_ws), "the workspace should be created on save"


def test_running_agents_keep_the_rails_they_started_with(qapp, manager):
    """Tightening the ceiling must not retroactively unapprove live work."""
    task = manager.wait(manager.submit(REQUEST).id, 60)
    agent = manager.get_agent(task.agent_id)
    before = agent.spec.permissions.max_auto_risk

    AgentSettingsDialog.apply_to(manager, workspace="", risk="safe", plan=False, limit=0)
    assert manager.default_policy.max_auto_risk is RiskLevel.SAFE
    assert agent.spec.permissions.max_auto_risk is before


def test_apply_to_tolerates_no_manager():
    AgentSettingsDialog.apply_to(None, workspace="", risk="safe", plan=True, limit=1)


def test_clear_history_removes_finished_tasks_only(qapp, manager):
    done = manager.wait(manager.submit(REQUEST).id, 60)
    assert manager.store.get_task(done.id) is not None

    removed = AgentSettingsDialog.clear_history(manager)
    assert removed >= 1
    assert manager.store.get_task(done.id) is None
    assert manager.events.history() == []


# -- settings actually change behaviour (the gate) ---------------------------

def test_saved_risk_level_changes_behaviour(tmp_path, qapp):
    """Saving 'safe' must make a real file write stop and ask."""
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True,
                              max_auto_risk=RiskLevel.MEDIUM).start()
    try:
        asked: list[str] = []
        mgr.events.subscribe(lambda e: asked.append(e.message),
                             kinds=[EventKind.APPROVAL_REQUIRED])

        AgentSettingsDialog.apply_to(mgr, workspace="", risk="safe", plan=False, limit=0)

        task = mgr.submit(REQUEST)
        deadline = time.monotonic() + 15
        while not asked and time.monotonic() < deadline:
            time.sleep(0.05)
        assert asked, "a write should have needed approval at the SAFE ceiling"
        mgr.cancel(task.id)
    finally:
        mgr.stop()


def test_bootstrap_reads_the_config(tmp_path, monkeypatch):
    """build_agent_manager() with no arguments follows the saved settings."""
    import agents.bootstrap as bootstrap
    monkeypatch.setattr(bootstrap, "_configured", lambda name, fallback: {
        "agent_auto_risk": "high",
        "agent_plan_approval": True,
        "agent_max_concurrent": 7,
    }.get(name, fallback))
    mgr = bootstrap.build_agent_manager(workspace_root=str(tmp_path), in_memory=True)
    try:
        assert mgr.default_policy.max_auto_risk is RiskLevel.HIGH
        assert mgr.orchestrator.require_plan_approval is True
        assert mgr.max_concurrent == 7
    finally:
        mgr.stop()


# -- concurrency -------------------------------------------------------------

def test_tasks_beyond_the_limit_queue_then_run(tmp_path):
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True,
                              max_concurrent=1).start()
    try:
        tasks = [mgr.submit_task(Task(title=f"t{i}", goal="make a folder",
                                      workspace=str(tmp_path / f"w{i}"),
                                      metadata={"subdir": f"folder{i}"}))
                 for i in range(4)]
        assert len(mgr._active) <= 1, "more than one task started at the limit"
        for t in tasks:
            done = mgr.wait(t.id, 60)
            assert done.status is TaskStatus.COMPLETED, done.error
        assert mgr._queue == []
        for i in range(4):
            assert (tmp_path / f"w{i}" / f"folder{i}").is_dir()
    finally:
        mgr.stop()


def test_zero_means_unlimited(tmp_path):
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True,
                              max_concurrent=0).start()
    try:
        tasks = [mgr.submit_task(Task(title=f"t{i}", goal="make a folder",
                                      workspace=str(tmp_path / f"u{i}"),
                                      metadata={"subdir": "x"})) for i in range(3)]
        for t in tasks:
            assert mgr.wait(t.id, 60).status is TaskStatus.COMPLETED
        assert mgr._queue == []
    finally:
        mgr.stop()


def test_cancelling_a_queued_task_retires_it(tmp_path):
    """A queued task has no coroutine to interrupt - cancel must still work."""
    # The first task holds the only slot by waiting for plan approval; a
    # scripted folder task otherwise finishes in milliseconds and the
    # "queued" one would simply run (seen as a flake under load).
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True,
                              max_concurrent=1, require_plan_approval=True).start()
    try:
        first = mgr.submit_task(Task(title="first", goal="make a folder",
                                     workspace=str(tmp_path), metadata={"subdir": "one"}))
        queued = mgr.submit_task(Task(title="queued", goal="make a folder",
                                      workspace=str(tmp_path), metadata={"subdir": "two"}))
        assert mgr.cancel(queued.id)
        assert mgr.wait(queued.id, 10).status is TaskStatus.CANCELLED
        deadline = time.monotonic() + 10
        while not mgr.pending_approvals() and time.monotonic() < deadline:
            time.sleep(0.02)
        for req in mgr.pending_approvals():
            mgr.approve(req.id, True)
        assert mgr.wait(first.id, 30).status is TaskStatus.COMPLETED
        assert not (tmp_path / "two").exists(), "a cancelled queue entry must not run"
    finally:
        mgr.stop()


# -- tray --------------------------------------------------------------------

def test_tray_shows_the_running_count(qapp):
    from ui.tray import TrayManager
    tray = TrayManager()
    assert tray._agents_running == 0
    tray.set_agents_running(2)
    assert tray._agents_running == 2
    tray.set_agents_running(2)                  # no rebuild when unchanged
    tray.set_agents_running(0)
    assert tray._agents_running == 0


def test_main_wires_the_tray_to_the_workforce():
    import inspect

    import main
    src = inspect.getsource(main._wire_agents)
    assert "attach_approvals" in src, "approvals must be attached or tasks park forever"
    assert "on_open_agents" in src and "on_agent_settings" in src
    assert "sig_agents_ready.connect" in inspect.getsource(main.main)
    assert "_wire_agents(manager, tray)" in inspect.getsource(main.main)
