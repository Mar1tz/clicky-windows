"""APP-10: the Tora app window, live - the bridge, the shell, the Home page.

The window was a drawing by request (M29): "$0.79 spent today", "Realme
Buds", "Application Builder step 4 of 10" - every number a placeholder,
every switch inert. Now it reads the running app through one bridge, and
every change it makes goes down the tray's own path.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QObject, pyqtSignal      # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel  # noqa: E402

from ui.app_bridge import AppBridge               # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class FakeManager(QObject):
    sig_state_changed = pyqtSignal(object)
    sig_account_problem = pyqtSignal(str, str)
    sig_agent_event = pyqtSignal(object)
    sig_agents_ready = pyqtSignal()
    sig_guide_progress = pyqtSignal(int, int, str)

    def __init__(self):
        super().__init__()
        self._realtime = SimpleNamespace(ready=True)
        self._problem_kind = ""
        self._guide_runner = None
        self._lesson_plan = None
        self._plan_in_progress = None
        self._quiz_mode = False
        self.agents = None


def _agent(title, action="", progress=0.5, step=2, total=4):
    """An AgentState, as far as the page reads one."""
    return SimpleNamespace(spec=SimpleNamespace(name="coder"), current_task_title=title,
                           current_action=action, progress=progress, step_index=step,
                           step_total=total, status=SimpleNamespace(value="running"))


def _texts(widget) -> list[str]:
    return [lbl.text() for lbl in widget.findChildren(QLabel)]


# ── the bridge reads the real thing ──────────────────────────────────────────

def test_the_state_follows_the_companion(qapp):
    mgr = FakeManager()
    bridge = AppBridge(mgr)
    seen = []
    bridge.changed.connect(seen.append)
    mgr.sig_state_changed.emit(SimpleNamespace(name="LISTENING"))
    assert bridge.state() == "listening" and seen == ["state"]


def test_the_running_walkthrough_is_read_from_the_runner(qapp):
    mgr = FakeManager()
    plan = SimpleNamespace(goal="set up the invoice template",
                           steps=[SimpleNamespace(instruction="Click Tools."),
                                  SimpleNamespace(instruction="Pick Classic B.")])
    mgr._lesson_plan = plan
    mgr._guide_runner = SimpleNamespace(running=True, index=1)
    walk = AppBridge(mgr).walkthrough()
    assert walk == {"goal": "set up the invoice template", "step": 2, "total": 2,
                    "instruction": "Pick Classic B."}
    mgr._plan_in_progress = plan                     # still being written (GW-08)
    assert AppBridge(mgr).walkthrough()["total"] == 0


def test_the_working_agents_are_read_from_the_workforce(qapp):
    mgr = FakeManager()
    mgr.agents = SimpleNamespace(list_agents=lambda active_only=False: [
        _agent("Florist website", "Writing index.html", 0.7, 4, 10)])
    (a,) = AppBridge(mgr).agents()
    assert a["title"] == "Florist website" and a["doing"] == "Writing index.html"
    assert a["progress"] == 0.7 and (a["step"], a["total"]) == (4, 10)


def test_today_is_counted_from_the_journal(qapp, tmp_path, monkeypatch):
    from tutor_features import journal
    monkeypatch.setattr(journal, "_db_path", lambda: tmp_path / "journal.db")
    journal.log_qa("What is a pivot table?", "A summary.")
    journal.log_walkthrough("export the video", ["Click Export."])
    assert AppBridge(FakeManager()).today() == {"questions": 1, "walkthroughs": 1, "due": 0}


def test_a_window_on_its_own_reads_nobodys_journal(qapp):
    assert AppBridge().today() == {"questions": 0, "walkthroughs": 0, "due": 0}


# ── changes go down the tray's own path ──────────────────────────────────────

def test_a_switch_is_changed_through_the_tray(qapp):
    applied = []
    tray = SimpleNamespace(apply_switch=lambda n, on: applied.append((n, on)))
    AppBridge(FakeManager(), tray).set_switch("ocr", False)
    assert applied == [("ocr", False)]


def test_the_tray_changes_a_switch_as_a_click_would(qapp):
    from ui.tray import TrayManager

    tray = TrayManager()
    try:
        emitted = []
        tray.on_toggle_privacy.connect(emitted.append)
        tray.apply_switch("privacy_guard", False)
        assert emitted == [False] and tray._privacy_enabled is False
        assert tray._privacy_action.isChecked() is False
        assert "OFF" in tray._privacy_action.text()
    finally:
        tray.hide_icon()


def test_open_panel_asks_the_tray(qapp):
    shown = []
    tray = SimpleNamespace(on_show_panel=SimpleNamespace(emit=lambda: shown.append(True)))
    AppBridge(FakeManager(), tray).show_panel()
    assert shown == [True]


# ── Home shows what is true ──────────────────────────────────────────────────

def _home(qapp, mgr, tray=None):
    from ui.app_pages import HomePage
    return HomePage(AppBridge(mgr, tray))


def test_home_shows_no_placeholder_anywhere(qapp):
    texts = " ".join(_texts(_home(qapp, FakeManager())))
    for fake in ("$0.79", "Realme", "Application Builder", "Month-end · ABC",
                 "Invoice inbox watch", "open exceptions"):
        assert fake not in texts, fake


def test_home_says_nothing_is_running_when_nothing_is(qapp):
    texts = _texts(_home(qapp, FakeManager()))
    assert "Nothing running right now" in texts
    assert "Connected" in texts


def test_home_shows_the_walkthrough_and_the_agents(qapp):
    mgr = FakeManager()
    mgr._lesson_plan = SimpleNamespace(goal="export the video",
                                       steps=[SimpleNamespace(instruction="Click Export."),
                                              SimpleNamespace(instruction="Choose MP4.")])
    mgr._guide_runner = SimpleNamespace(running=True, index=0)
    mgr.agents = SimpleNamespace(list_agents=lambda active_only=False: [
        _agent("Florist website", "Writing index.html", 0.7)])
    texts = _texts(_home(qapp, mgr))
    assert "Walkthrough · export the video" in texts
    assert "Step 1 of 2 · Click Export." in texts
    assert "Florist website" in texts and "70%" in texts
    assert "Nothing running right now" not in texts


@pytest.mark.parametrize("problem, live, pill", [
    ("billing", True, "Out of Gemini credit"), ("key", True, "API key refused"),
    ("", False, "Not connected"), ("", True, "Connected")])
def test_home_says_whether_she_can_answer(qapp, problem, live, pill):
    mgr = FakeManager()
    mgr._problem_kind = problem
    mgr._realtime = SimpleNamespace(ready=live)
    assert pill in _texts(_home(qapp, mgr))


# ── the window refreshes what it shows ───────────────────────────────────────

def test_a_change_redraws_the_page_on_screen(qapp):
    from ui.app_window import ToraApp

    mgr = FakeManager()
    w = ToraApp(AppBridge(mgr))
    w.show()
    try:
        assert "Nothing running right now" in _texts(w._pages["home"])
        mgr.agents = SimpleNamespace(list_agents=lambda active_only=False: [
            _agent("Florist website", "Writing index.html")])
        mgr.sig_agent_event.emit(object())
        assert w._refresh_timer.isActive(), "one redraw per burst, not per event"
        w._refresh_now()
        assert "Florist website" in _texts(w._pages["home"])
    finally:
        w.hide()
        w.deleteLater()


def test_the_sidebar_follows_the_companion_state(qapp):
    from ui.app_window import ToraApp

    mgr = FakeManager()
    w = ToraApp(AppBridge(mgr))
    try:
        mgr.sig_state_changed.emit(SimpleNamespace(name="SPEAKING"))
        assert w._state_label.text() == "Speaking"
    finally:
        w.deleteLater()
