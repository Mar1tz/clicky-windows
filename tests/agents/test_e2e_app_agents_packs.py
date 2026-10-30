"""APP-17: the jobs are live - what the bridge reads from the workforce,
and a job's plan in its drawer (ui/agents_page.py JobDrawer).

The Agents page these tests began on went with the app window (revamp step
6b); giving a job, approvals, pause, stop and history are the shape's Jobs
tab now (tests/agents/test_e2e_shape.py). (The packs this file also
covered were replaced by skills, 6 Oct 2026 - tests/agents/test_e2e_skills.py.)
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QObject, pyqtSignal                    # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel                # noqa: E402

from ui.app_bridge import AppBridge                             # noqa: E402



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


def _subs(wf) -> int:
    return len(wf.events._subs)


def test_the_real_bridge_says_when_a_job_is_really_a_question(qapp):
    bridge = AppBridge(FakeManager(SimpleNamespace(submit=lambda _text: None)))
    assert bridge.give_job("what is a pivot table?") == ("chat", "")


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
    from PyQt6.QtWidgets import QWidget
    from ui.agents_page import JobDrawer
    t = _saved_task(wf, tmp_path)
    page = QWidget()
    page.resize(820, 600)
    drawer = JobDrawer(page, _bridge(wf))
    drawer.show_job(t.id)
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
