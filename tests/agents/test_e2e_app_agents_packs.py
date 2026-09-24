"""APP-17, APP-18, APP-19: the Agents, Extensions and Accounting pages are live.

They were drawings (M29): an "Application Builder" stuck at 70%, a plan
and a timeline typed in by hand, four packs of which three do not exist,
and an accounting page of invented exceptions. Now:

  * Agents embeds the workforce's own panel - built once, one listener for
    the whole page - lists the real tasks and what waits for a yes, and
    shows any task's plan and activity in the page instead of a new window.
  * Extensions lists the packs that are really there and switches them.
  * Accounting is the pack's own dashboard, or the way to turn it on.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal        # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton   # noqa: E402

from ui.app_bridge import AppBridge                             # noqa: E402

DIRECT = Qt.FindChildOption.FindDirectChildrenOnly


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def wf(tmp_path):
    from agents.bootstrap import build_agent_manager
    m = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True).start()
    yield m
    m.stop()


class FakeManager(QObject):
    sig_state_changed = pyqtSignal(object)
    sig_agent_event = pyqtSignal(object)
    sig_agents_ready = pyqtSignal()

    def __init__(self, agents=None):
        super().__init__()
        self.agents = agents


class FakeTray(QObject):
    on_stop_agents = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.stops = 0
        self.on_stop_agents.connect(self._stopped)

    def _stopped(self):
        self.stops += 1


def _bridge(wf):
    return AppBridge(FakeManager(wf), FakeTray())


class Listed(AppBridge):
    """The real bridge with the lists given, for how the page draws them."""

    def __init__(self, wf, tasks=(), waiting=()):
        super().__init__(FakeManager(wf), FakeTray())
        self.t, self.w = list(tasks), list(waiting)

    def tasks(self, limit=8):
        return self.t

    def approvals(self):
        return self.w


def _task(title, status="completed", error="", tid=""):
    return {"id": tid or title, "title": title, "status": status, "error": error,
            "when": time.time()}


def _subs(wf) -> int:
    return len(wf.events._subs)


def _texts(w):
    return [lbl.text() for lbl in w.findChildren(QLabel) if lbl.isVisibleTo(w)]


def _buttons(w, text):
    return [b for b in w.findChildren(QPushButton) if b.text() == text and b.isVisibleTo(w)]


def _card(page, heading):
    from ui.app_widgets import Card
    return next(c for c in page.findChildren(Card)
                if any(lbl.objectName() == "cardTitle" and lbl.text() == heading
                       for lbl in c.findChildren(QLabel)))


def _settle(qapp):
    for _ in range(3):
        qapp.processEvents()


# ── Agents ───────────────────────────────────────────────────────────────────

def test_the_real_agents_panel_is_embedded_once(qapp, wf):
    from agents.ui import ActiveAgentsPanel
    from ui.app_pages import AgentsPage

    before = _subs(wf)
    page = AgentsPage(_bridge(wf))
    (panel,) = page.findChildren(ActiveAgentsPanel)
    assert "Active Agents" not in _texts(page), "the card names it; its own title is hidden"
    for _ in range(3):
        page.refresh()
    assert page.findChildren(ActiveAgentsPanel) == [panel], "kept - with what was typed in it"
    assert _subs(wf) == before + 1, "one listener for the whole page"


def test_stop_all_is_the_trays(qapp, wf):
    from ui.app_pages import AgentsPage
    bridge = _bridge(wf)
    _buttons(AgentsPage(bridge), "Stop all")[0].click()
    assert bridge.tray.stops == 1


def test_agent_settings_opens_the_settings_page(qapp, wf):
    from ui.app_window import ToraApp
    w = ToraApp(_bridge(wf))
    w.show_page("agents")
    _buttons(w._pages["agents"], "Agent settings")[0].click()
    assert w._stack.currentWidget() is w._pages["settings"]


def test_recent_tasks_say_what_happened(qapp, wf):
    from ui.app_pages import AgentsPage
    texts = _texts(AgentsPage(Listed(wf, [
        _task("Build a landing page"),
        _task("Reconcile September", "failed", "No amounts column")])))
    assert "Build a landing page" in texts
    assert any(t.startswith("Done · Today") for t in texts)
    assert any(t.startswith("Failed · Today") and t.endswith("· No amounts column") for t in texts)
    assert "Application Builder" not in texts, "no made-up agent"


def test_waiting_for_you_shows_only_while_something_waits(qapp, wf):
    from ui.app_pages import AgentsPage
    bridge = Listed(wf)
    page = AgentsPage(bridge)
    card = _card(page, "Waiting for you")
    assert not card.isVisibleTo(page)
    bridge.w = [{"id": "r1", "tool": "delete_path", "reason": "Delete build/ and rebuild",
                 "task": "Build a landing page", "sensitive": True}]
    page.refresh()
    assert card.isVisibleTo(page)
    assert "Delete build/ and rebuild" in _texts(card)
    assert "Build a landing page · delete_path · needs a typed confirm word" in _texts(card)


def test_a_progress_tick_does_not_redraw_the_lists(qapp, wf):
    from ui.app_pages import AgentsPage
    from ui.app_widgets import Row
    bridge = Listed(wf, [_task("Build a landing page", "running")])
    page = AgentsPage(bridge)
    rows = _card(page, "Recent tasks").findChildren(Row)
    page.refresh()
    assert _card(page, "Recent tasks").findChildren(Row) == rows
    bridge.t = [_task("Build a landing page", "completed")]
    page.refresh()
    assert _card(page, "Recent tasks").findChildren(Row) != rows, "a new status is drawn"


def _saved_task(wf, tmp_path):
    """A task with a plan and some history, in the workforce's store."""
    from agents.core import EventKind, Step, StepStatus, Task
    t = Task(title="Build a landing page", goal="Build a landing page", workspace=str(tmp_path))
    t.steps = [Step(title="Create project folder", status=StepStatus.DONE),
               Step(title="Write package.json")]
    wf.store.save_task(t)
    wf.events.emit(EventKind.STEP_DONE, "Create project folder", task_id=t.id,
                   data={"index": 0, "title": "Create project folder"})
    return t


def test_details_show_the_plan_and_activity_in_the_page(qapp, wf, tmp_path):
    from agents.ui import ActivityTimeline, PlanView
    from ui.app_pages import AgentsPage

    t = _saved_task(wf, tmp_path)
    page = AgentsPage(Listed(wf, [_task(t.title, "running", tid=t.id)]))
    listeners = _subs(wf)
    _buttons(page, "Details")[0].click()
    (plan,) = page.findChildren(PlanView)
    (timeline,) = page.findChildren(ActivityTimeline)
    assert len(plan.lines) == 2 and "Create project folder" in plan.lines[0]
    assert timeline.lines, "what it did, read back from the store"
    assert _card(page, "Plan and activity").isVisibleTo(page)
    assert _subs(wf) == listeners, "they share the page's one listener"
    _buttons(page, "Details")[0].click()
    assert len(page.findChildren(PlanView)) == 1, "built once per task"


def test_the_panels_logs_button_opens_the_details_here(qapp, wf, tmp_path):
    import agents.ui as agents_ui
    from agents.ui import ActiveAgentsPanel, ActivityTimeline
    from ui.app_pages import AgentsPage

    t = _saved_task(wf, tmp_path)
    page = AgentsPage(Listed(wf))
    (panel,) = page.findChildren(ActiveAgentsPanel)
    panel.view_logs.emit(t.id)
    assert page.findChildren(ActivityTimeline)
    assert t.id not in agents_ui._timelines, "in the page, not a new window"


def test_without_agents_the_page_says_why_then_fills_in(qapp, wf):
    from agents.ui import ActiveAgentsPanel
    from ui.app_pages import AgentsPage
    mgr = FakeManager(None)
    page = AgentsPage(AppBridge(mgr, FakeTray()))
    assert "The agents did not start" in _texts(page)
    mgr.agents = wf                              # they started after all
    page.refresh()
    assert page.findChildren(ActiveAgentsPanel)


def test_the_bridge_lists_top_level_tasks(qapp):
    from agents.core import Task, TaskStatus
    lead = Task(title="Build the shop", goal="g", workspace="w", status=TaskStatus.RUNNING)
    child = Task(title="Write the navbar", goal="g", workspace="w",
                 metadata={"parent_id": lead.id})
    failed = Task(title="Reconcile", goal="g", workspace="w", status=TaskStatus.FAILED,
                  error="No amounts column\nTraceback (most recent call last): ...")
    bridge = AppBridge(FakeManager(SimpleNamespace(list_tasks=lambda: [failed, child, lead])))
    tasks = bridge.tasks()
    assert [t["title"] for t in tasks] == ["Reconcile", "Build the shop"], "a project speaks for its members"
    assert tasks[0]["status"] == "failed" and tasks[0]["error"] == "No amounts column"


def test_the_bridge_lists_what_waits_for_a_yes(qapp):
    from agents.core import ApprovalRequest, ToolCall
    req = ApprovalRequest(task_id="t1", agent_id="a1", call=ToolCall("delete_path", {"path": "build"}),
                          reason="Delete build/", sensitive=True)
    workforce = SimpleNamespace(pending_approvals=lambda: [req],
                                get_task=lambda _tid: SimpleNamespace(title="Build the shop"))
    assert AppBridge(FakeManager(workforce)).approvals() == [
        {"id": req.id, "tool": "delete_path", "reason": "Delete build/",
         "task": "Build the shop", "sensitive": True}]


# ── Extensions ───────────────────────────────────────────────────────────────

def test_the_real_packs_are_listed_and_switch_on(qapp, wf):
    from ui.app_pages import ExtensionsPage
    from ui.app_widgets import Switch

    page = ExtensionsPage(_bridge(wf))
    texts = _texts(page)
    assert "Accounting" in texts and "Legal" not in texts, "only packs that exist"
    assert any("QuickBooks" in t for t in texts), "integrations by name, not id"
    (switch,) = page.findChildren(Switch)
    switch.setChecked(True)
    _settle(qapp)
    assert wf.extensions.is_enabled("accounting")
    assert "On" in _texts(page) and _buttons(page, "Open dashboard")
    (switch,) = page.findChildren(Switch)
    switch.setChecked(False)
    _settle(qapp)
    assert not wf.extensions.is_enabled("accounting")


def test_open_dashboard_goes_to_the_accounting_page(qapp, wf):
    from extensions.accounting.ui.dashboard import AccountingDashboard
    from ui.app_window import ToraApp

    wf.extensions.enable("accounting")
    w = ToraApp(_bridge(wf))
    w.show_page("extensions")
    _buttons(w._pages["extensions"], "Open dashboard")[0].click()
    assert w._stack.currentWidget() is w._pages["accounting"]
    assert w._pages["accounting"].findChildren(AccountingDashboard)


def test_a_pack_that_will_not_switch_says_why(qapp, wf):
    from ui.app_pages import ExtensionsPage
    from ui.app_widgets import Switch

    class Refusing(AppBridge):
        def set_extension(self, ext_id, on):
            return "the ledger folder is read-only"

    page = ExtensionsPage(Refusing(FakeManager(wf), FakeTray()))
    page.findChildren(Switch)[0].setChecked(True)
    _settle(qapp)
    assert "That did not work: the ledger folder is read-only" in _texts(page)
    assert not wf.extensions.is_enabled("accounting")


def test_a_pack_turned_on_anywhere_redraws_the_pages(qapp, wf):
    """By voice, from the tray's window, from here: the registry says so on
    the workforce's bus, and the bridge passes it on."""
    mgr = FakeManager(wf)
    wf.events.subscribe(mgr.sig_agent_event.emit)      # what the manager does
    bridge = AppBridge(mgr, FakeTray())
    topics = []
    bridge.changed.connect(topics.append)
    wf.extensions.enable("accounting")
    assert "extensions" in topics


# ── Accounting ───────────────────────────────────────────────────────────────

def test_accounting_off_offers_to_turn_it_on(qapp, wf):
    from extensions.accounting.ui.dashboard import AccountingDashboard
    from ui.app_pages import AccountingPage

    page = AccountingPage(_bridge(wf))
    assert "Accounting is off" in _texts(page)
    assert not _buttons(page, "Run month-end"), "no drawn buttons"
    _buttons(page, "Turn on Accounting")[0].click()
    _settle(qapp)
    assert wf.extensions.is_enabled("accounting")
    (dash,) = page.findChildren(AccountingDashboard)
    (own_title,) = [lbl for lbl in dash.findChildren(QLabel) if lbl.objectName() == "accTitle"]
    assert not own_title.isVisibleTo(page), "the page is already called Accounting"


def test_the_dashboard_is_kept_while_on_and_stopped_when_off(qapp, wf):
    from extensions.accounting.ui.dashboard import AccountingDashboard
    from ui.app_pages import AccountingPage

    wf.extensions.enable("accounting")
    page = AccountingPage(_bridge(wf))
    (dash,) = page.findChildren(AccountingDashboard)
    page.refresh()
    page.refresh()
    assert page.findChildren(AccountingDashboard) == [dash], "kept: it holds the chosen client"
    clocks = dash.findChildren(QTimer, options=DIRECT)
    wf.extensions.disable("accounting")
    page.refresh()
    assert not page.findChildren(AccountingDashboard)
    assert clocks and not any(c.isActive() for c in clocks), "its clock stops before it goes"
    assert "Accounting is off" in _texts(page)


def test_the_dashboard_clock_runs_only_while_the_page_is_seen(qapp, wf):
    from extensions.accounting.ui.dashboard import AccountingDashboard
    from ui.app_pages import AccountingPage

    wf.extensions.enable("accounting")
    page = AccountingPage(_bridge(wf))
    (dash,) = page.findChildren(AccountingDashboard)
    clocks = dash.findChildren(QTimer, options=DIRECT)
    try:
        page.show()
        _settle(qapp)
        assert clocks and all(c.isActive() for c in clocks)
        page.hide()
        _settle(qapp)
        assert not any(c.isActive() for c in clocks), "nobody is looking"
        page.show()
        _settle(qapp)
        assert all(c.isActive() for c in clocks)
    finally:
        page.hide()


def test_a_dashboard_that_will_not_build_says_why(qapp, wf):
    from ui.app_pages import AccountingPage

    class Broken(AppBridge):
        def extension_on(self, ext_id):
            return True

        def dashboard(self, ext_id, parent=None):
            raise RuntimeError("the books are locked")

    texts = _texts(AccountingPage(Broken(FakeManager(wf), FakeTray())))
    assert "The dashboard could not load" in texts and "the books are locked" in texts


def test_embedded_tabs_keep_their_ampersands(qapp):
    from PyQt6.QtWidgets import QTabWidget, QWidget
    from ui.app_pages import _tidy

    host = QWidget()
    tabs = QTabWidget(host)
    tabs.addTab(QWidget(), "Debtors & creditors")
    tabs.addTab(QWidget(), "Drafts")
    _tidy(host)
    _tidy(host)                                   # and only once
    assert [tabs.tabText(0), tabs.tabText(1)] == ["Debtors && creditors", "Drafts"]
