"""APP-17, APP-18, APP-19: the Agents, Extensions and Accounting pages are live.

They were drawings (M29): an "Application Builder" stuck at 70%, a plan
and a timeline typed in by hand, four packs of which three do not exist,
and an accounting page of invented exceptions. Now:

  * Agents (rebuilt 5 Oct, ui/agents_page.py) takes a job typed on the
    page, answers approvals there, pauses and stops what runs, lists how
    each job went, and opens a job's plan in a drawer.
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


# ── Agents (ui/agents_page.py, DESIGN.md §3F) ─────────────────────────────────

class Doing(Listed):
    """Listed, and it remembers what the page asked of the workforce."""

    def __init__(self, wf, tasks=(), waiting=(), running=()):
        super().__init__(wf, tasks, waiting)
        self.r = list(running)
        self.asked: list[tuple] = []

    def running(self):
        return self.r

    def give_job(self, text):
        self.asked.append(("job", text))
        return "started", text

    def answer_approval(self, request_id, approved, confirmation=""):
        self.asked.append(("answer", request_id, approved, confirmation))
        return True

    def pause_job(self, tid):
        self.asked.append(("pause", tid))

    def resume_job(self, tid):
        self.asked.append(("resume", tid))

    def stop_job(self, tid):
        self.asked.append(("stop", tid))


def _running(tid="r1", status="running"):
    return {"id": tid, "title": "Florist website", "doing": "Writing the contact page",
            "step": 5, "total": 8, "progress": 0.6, "status": status, "started": time.time() - 600}


def _later(qapp):
    """Let a click's after-the-click redraw run."""
    from PyQt6.QtCore import QEventLoop
    loop = QEventLoop()
    QTimer.singleShot(20, loop.quit)
    loop.exec()


def test_stop_all_is_the_trays(qapp, wf):
    from ui.app_pages import AgentsPage
    bridge = _bridge(wf)
    page = AgentsPage(bridge)
    _buttons(page, "Stop all")[0].click()
    assert bridge.tray.stops == 1


def test_agent_settings_opens_the_settings_page(qapp, wf):
    from ui.app_window import ToraApp
    w = ToraApp(_bridge(wf))
    w.show_page("agents")
    _buttons(w._pages["agents"], "Agent settings")[0].click()
    assert not w._settings.isHidden() and w._settings.current() == "agents", "Settings opens on its Agents tab"


def test_a_job_typed_on_the_page_goes_to_the_agents(qapp, wf):
    from ui.app_pages import AgentsPage
    bridge = Doing(wf)
    page = AgentsPage(bridge)
    page._job.setPlainText("Build a one-page website for my coffee shop")
    _buttons(page, "Start")[0].click()
    assert bridge.asked == [("job", "Build a one-page website for my coffee shop")]
    assert page._job.toPlainText() == "", "started: the box is ready for the next one"


def test_the_real_bridge_says_when_a_job_is_really_a_question(qapp):
    bridge = AppBridge(FakeManager(SimpleNamespace(submit=lambda _text: None)))
    assert bridge.give_job("what is a pivot table?") == ("chat", "")


def test_approve_and_deny_are_on_the_page(qapp, wf):
    from ui.app_pages import AgentsPage
    waiting = [{"id": "r1", "tool": "delete_path", "reason": "Delete build/ and rebuild",
                "task": "Build a landing page", "sensitive": False, "confirm_word": ""},
               {"id": "r2", "tool": "release_payment", "reason": "Pay the invoice",
                "task": "Month-end", "sensitive": True, "confirm_word": "PAY"}]
    bridge = Doing(wf, waiting=waiting)
    page = AgentsPage(bridge)
    assert "Delete build/ and rebuild" in _texts(page)
    approve = _buttons(page, "Approve")
    approve[0].click()
    _buttons(page, "Deny")[1].click()
    assert bridge.asked[:2] == [("answer", "r1", True, ""), ("answer", "r2", False, "")]


def test_a_sensitive_approval_sends_the_typed_word(qapp, wf):
    from PyQt6.QtWidgets import QLineEdit
    from ui.app_pages import AgentsPage
    bridge = Doing(wf, waiting=[{"id": "r2", "tool": "release_payment", "reason": "Pay the invoice",
                                 "task": "Month-end", "sensitive": True, "confirm_word": "PAY"}])
    page = AgentsPage(bridge)
    (word,) = [e for e in page.findChildren(QLineEdit) if "PAY" in e.placeholderText()]
    word.setText("PAY")
    _buttons(page, "Approve")[0].click()
    assert bridge.asked == [("answer", "r2", True, "PAY")]


def test_nothing_waiting_shows_no_waiting_section(qapp, wf):
    from ui.app_pages import AgentsPage
    assert "NEEDS YOUR OK" not in _texts(AgentsPage(Doing(wf)))


def test_a_running_job_can_be_paused_resumed_and_stopped(qapp, wf):
    from ui.app_pages import AgentsPage
    bridge = Doing(wf, running=[_running()])
    page = AgentsPage(bridge)
    assert "Step 5 of 8 · Writing the contact page" in _texts(page)
    _buttons(page, "Pause")[0].click()
    _later(qapp)
    bridge.r = [_running(status="paused")]
    page.refresh()
    _buttons(page, "Resume")[0].click()
    _later(qapp)
    _buttons(page, "Stop")[0].click()
    assert bridge.asked == [("pause", "r1"), ("resume", "r1"), ("stop", "r1")]


def test_history_says_how_each_job_went(qapp, wf):
    from ui.app_pages import AgentsPage
    texts = _texts(AgentsPage(Doing(wf, tasks=[
        _task("Build a landing page"),
        _task("Reconcile September", "failed", "No amounts column")])))
    assert "Build a landing page" in texts
    assert any(t.startswith("Finished") for t in texts)
    assert any(t.startswith("Didn't finish") and t.endswith("No amounts column") for t in texts)


def _saved_task(wf, tmp_path):
    """A task with a plan, in the workforce's store."""
    from agents.core import Step, StepStatus, Task
    t = Task(title="Build a landing page", goal="Build a landing page for the shop",
             workspace=str(tmp_path))
    t.steps = [Step(title="Create project folder", status=StepStatus.DONE, summary="Made site/"),
               Step(title="Write package.json")]
    wf.store.save_task(t)
    return t


def test_a_jobs_details_show_its_plan_from_the_workforce(qapp, wf, tmp_path):
    from ui.app_pages import AgentsPage
    t = _saved_task(wf, tmp_path)
    page = AgentsPage(_bridge(wf))
    page.open_job(t.id)
    drawer = page._drawer
    texts = [l.text() for l in drawer.findChildren(QLabel)]
    assert "Build a landing page" in texts and "Build a landing page for the shop" in texts
    assert "1. Create project folder" in texts and "Made site/" in texts and "2. Write package.json" in texts
    assert not drawer.isHidden()
    drawer.close_drawer()
    assert drawer.isHidden()


def test_the_bridge_reads_one_job_with_its_folder(qapp, wf, tmp_path):
    t = _saved_task(wf, tmp_path)
    job = _bridge(wf).job(t.id)
    assert job["folder"] == str(tmp_path)
    assert [st["status"] for st in job["steps"]] == ["done", "pending"]
    assert _bridge(wf).job("no-such-job") is None


def test_without_agents_the_page_says_why_then_fills_in(qapp, wf):
    from ui.app_pages import AgentsPage
    mgr = FakeManager(None)
    page = AgentsPage(AppBridge(mgr, FakeTray()))
    assert "The agents did not start" in _texts(page)
    mgr.agents = wf                              # they started after all
    page.refresh()
    assert page._parts is not None and "GIVE TORA A JOB" in _texts(page)


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
         "task": "Build the shop", "sensitive": True, "confirm_word": ""}]


# ── Packs (ui/packs_page.py, DESIGN.md §3G) ─────────────────────────────────────

def _grid_switches(page):
    """The switch on each card in All packs, in the registry's order."""
    from ui.app_widgets import Switch
    return page.findChildren(Switch)


def test_every_real_pack_is_a_card_and_switches_on(qapp, wf):
    from ui.app_pages import PacksPage
    page = PacksPage(_bridge(wf))
    texts = _texts(page)
    packs = [p["id"] for p in wf.extensions.available()]
    assert "Accounting" in texts and "Freelancer" in texts and "Legal" not in texts, "only packs that exist"
    assert len(_grid_switches(page)) == len(packs), "one card - one switch - per pack, none left out"
    first = packs[0]
    _grid_switches(page)[0].setChecked(True)
    _settle(qapp)
    assert wf.extensions.is_enabled(first)


def test_no_pack_is_special(qapp, wf):
    """Every pack you turn on gets the same row and the same page."""
    from ui.app_window import ToraApp
    from ui.packs_page import PackPage
    wf.extensions.enable("students")
    wf.extensions.enable("accounting")
    w = ToraApp(_bridge(wf))
    rows = {k for k, b in w._buttons.items() if k.startswith("pack/") and not b.isHidden()}
    assert rows == {"pack/students", "pack/accounting"}
    for key in rows:
        w.show_page(key)
        assert isinstance(w._pages[key], PackPage)


def test_a_pack_that_will_not_switch_says_why(qapp, wf):
    from ui.app_pages import PacksPage

    class Refusing(AppBridge):
        def set_extension(self, ext_id, on):
            return "the ledger folder is read-only"

    page = PacksPage(Refusing(FakeManager(wf), FakeTray()))
    _grid_switches(page)[0].setChecked(True)
    _settle(qapp)
    assert "That did not work: the ledger folder is read-only" in _texts(page)


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


def test_a_kit_packs_page_is_its_own_snapshot_and_its_buttons_run_it(qapp, wf):
    from ui.packs_page import PackPage
    wf.extensions.enable("students")
    bridge = _bridge(wf)
    page = PackPage(bridge, "students")
    texts = _texts(page)
    view = bridge.pack_view("students")
    assert view is not None and view["actions"], "the pack's own buttons"
    for caption, _value in view["numbers"]:
        assert caption.upper() in texts, "its own numbers, by its own names"
    before = len(wf.list_tasks())
    _buttons(page, view["actions"][0])[0].click()
    assert len(wf.list_tasks()) == before + 1, "a button runs the pack's workflow as a job"


def test_a_pack_that_is_off_offers_to_turn_it_on(qapp, wf):
    from extensions.accounting.ui.dashboard import AccountingDashboard
    from ui.packs_page import PackPage
    page = PackPage(_bridge(wf), "accounting")
    assert "Accounting is off" in _texts(page)
    _buttons(page, "Turn on Accounting")[0].click()
    _settle(qapp)
    assert wf.extensions.is_enabled("accounting")
    assert page.findChildren(AccountingDashboard), "a pack with its own dashboard has it here"


def test_its_own_dashboard_is_kept_while_on_and_stopped_when_off(qapp, wf):
    from extensions.accounting.ui.dashboard import AccountingDashboard
    from ui.packs_page import PackPage
    wf.extensions.enable("accounting")
    page = PackPage(_bridge(wf), "accounting")
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


def test_a_dashboard_that_will_not_build_says_why(qapp, wf):
    from ui.packs_page import PackPage

    class Broken(AppBridge):
        def extension_on(self, ext_id):
            return True

        def pack_view(self, ext_id):
            return None

        def dashboard(self, ext_id, parent=None):
            raise RuntimeError("the books are locked")

    texts = _texts(PackPage(Broken(FakeManager(wf), FakeTray()), "accounting"))
    assert "This pack's page could not load" in texts and "the books are locked" in texts


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
