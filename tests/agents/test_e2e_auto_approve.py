"""APP-14: Settings > Approvals - "Approve automatically".

In three days of the field log there were 71 approval boxes and not one
"no": 56 for desktop actions asked for by voice, 11 for an agent's mouse and
keyboard, 4 for the browser agent over the risk ceiling. The existing
ceiling ("Run without asking, up to") could lift 4 of them. This switch
lets all of that go ahead - and still never covers:

  * a SENSITIVE call - money leaving, a filing - which needs a typed word,
    and a setting is not a person typing it;
  * a plan, while "Show me the plan before anything runs" is on;
  * a desktop action that sends, posts, pays or deletes.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import asyncio
import time

import pytest

from agents.core import (
    ApprovalRequest, EventKind, PermissionPolicy, RiskLevel, Task, ToolCall, ToolContext, ToolSpec,
)
from agents.tools.base import Tool


@pytest.fixture
def wf(tmp_path):
    from agents.bootstrap import build_agent_manager
    m = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True).start()
    yield m
    m.stop()


class _Tool(Tool):
    def __init__(self, name, risk=RiskLevel.LOW, *, always_ask=False, sensitive=False):
        self.spec = ToolSpec(name=name, description=name, risk=risk)
        self.always_ask = always_ask
        self.sensitive = sensitive
        self.ran = 0

    async def run(self, args, ctx):
        self.ran += 1
        return self.ok("done")


def _mouse():
    return _Tool("click", RiskLevel.HIGH, always_ask=True)       # like computer_tools


def _browser():
    return _Tool("open_page", RiskLevel.HIGH)                     # over the medium ceiling


def _payment():
    return _Tool("release_payment", RiskLevel.HIGH, always_ask=True, sensitive=True)


def _start(wf, tool, tmp_path):
    """One call through the real registry gate, with the task's real approver.
    Returns (future of the result, the task, what was published meanwhile)."""
    wf.tools.register(tool)
    task = Task(title="Build the shop", goal="g", workspace=str(tmp_path))
    seen = []
    wf.events.subscribe(seen.append)
    ctx = ToolContext(workspace=str(tmp_path), task_id=task.id, agent_id="agent-1",
                      events=wf.events, policy=PermissionPolicy(max_auto_risk=RiskLevel.MEDIUM),
                      approver=wf._approver_for(task))
    return wf.runtime.submit(wf.tools.execute(ToolCall(tool.name, {}), ctx)), task, seen


def _until(check, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.01)
    return False


def _kinds(seen):
    return [e.kind for e in seen]


# ── agents ───────────────────────────────────────────────────────────────────

def test_off_by_default_so_an_agent_still_asks(wf, tmp_path):
    tool = _mouse()
    assert wf.auto_approve is False
    fut, _task, _seen = _start(wf, tool, tmp_path)
    assert _until(lambda: wf.pending_approvals()), "the approval box is up"
    wf.approve(wf.pending_approvals()[0].id, False)
    assert not fut.result(timeout=5).ok and tool.ran == 0


@pytest.mark.parametrize("make", [_mouse, _browser], ids=["mouse and keyboard", "over the ceiling"])
def test_on_an_agent_goes_ahead_without_the_box(wf, tmp_path, make):
    wf.set_auto_approve(True)
    tool = make()
    fut, task, seen = _start(wf, tool, tmp_path)
    result = fut.result(timeout=5)
    assert result.ok and tool.ran == 1
    assert EventKind.APPROVAL_REQUIRED not in _kinds(seen), "nobody was asked"
    (mark,) = [e for e in seen if e.kind is EventKind.APPROVAL_RESOLVED]
    assert mark.task_id == task.id and mark.data["auto"] is True
    assert mark.data["tool"] == tool.name and mark.data["reason"], "the task's log says what and why"


def test_on_a_payment_still_needs_the_typed_word(wf, tmp_path):
    wf.set_auto_approve(True)
    tool = _payment()
    fut, _task, _seen = _start(wf, tool, tmp_path)
    assert _until(lambda: wf.pending_approvals()), "a payment still asks"
    (req,) = wf.pending_approvals()
    assert req.sensitive
    assert wf.approve(req.id, True) is False, "a bare yes is refused"
    wf.approve(req.id, False)
    assert not fut.result(timeout=5).ok and tool.ran == 0


def test_on_a_plan_still_waits_while_plan_first_is_on(wf, tmp_path):
    wf.set_auto_approve(True)
    task = Task(title="Build the shop", goal="g", workspace=str(tmp_path))
    req = ApprovalRequest(task_id=task.id, agent_id="agent-1",
                          call=ToolCall("approve_plan", {"plan_id": "p1"}),
                          reason="Plan with 3 steps needs approval")
    fut = wf.runtime.submit(wf._approver_for(task)(req))
    assert _until(lambda: wf.pending_approvals()), "the plan is shown and waits"
    wf.approve(req.id, False)
    assert fut.result(timeout=5) is False


def test_turning_it_on_and_off_is_on_the_record(wf):
    seen = []
    wf.events.subscribe(seen.append)
    wf.set_auto_approve(True)
    wf.set_auto_approve(True)                 # no change, nothing said
    wf.set_auto_approve(False)
    logs = [e for e in seen if e.kind is EventKind.LOG and "auto_approve" in e.data]
    assert [e.data["auto_approve"] for e in logs] == [True, False]
    assert logs[0].level == "warn"


# ── desktop actions asked for by voice ───────────────────────────────────────

class _Launcher:
    app = "launcher"
    display_name = "Launcher"
    aliases = ()

    def __init__(self):
        self.done = []

    def available(self):
        return True

    def actions(self):
        from agents.control import Risk, Tier
        from agents.control.model import Action, Param

        async def open_app(name: str):
            self.done.append(("open", name))
            return f"opened {name}"

        async def send_message(to: str):
            self.done.append(("send", to))
            return f"sent to {to}"

        return [Action("launcher", "open_app", "Open an app.", open_app, Tier.SYSTEM,
                       Risk.CHANGES, (Param("name", "Application name", True),)),
                Action("launcher", "send_message", "Send a message.", send_message,
                       Tier.SYSTEM, Risk.SENSITIVE, (Param("to", "Who to", True),))]


@pytest.fixture
def launcher():
    from agents.control import AppControl, set_control
    app = _Launcher()
    set_control(AppControl([app]))
    yield app
    set_control(None)


def _companion(wf):
    from companion_manager import CompanionManager
    companion = CompanionManager.__new__(CompanionManager)
    companion._agents = wf
    companion._realtime = None
    companion._submit = lambda coro: coro.close()
    return companion


def _say(companion, action, arguments):
    import json
    return asyncio.run(companion._tool_desktop_action(
        {"app": "launcher", "action": action, "arguments_json": json.dumps(arguments)}))


def test_voice_off_by_default_the_box_comes_up(wf, launcher):
    out = _say(_companion(wf), "open_app", {"name": "calculator"})
    assert out.get("awaiting_approval") and wf.pending_approvals()
    assert launcher.done == []


def test_voice_on_an_action_just_happens(wf, launcher):
    wf.set_auto_approve(True)
    out = _say(_companion(wf), "open_app", {"name": "calculator"})
    assert out["ok"] and launcher.done == [("open", "calculator")]
    assert not wf.pending_approvals()


def test_voice_on_sending_a_message_still_asks(wf, launcher):
    wf.set_auto_approve(True)
    out = _say(_companion(wf), "send_message", {"to": "Mom"})
    assert out.get("awaiting_approval") and wf.pending_approvals()
    assert launcher.done == [], "nothing sent on a setting"


def test_a_confirmation_is_only_auto_approved_when_the_caller_says_it_may(wf):
    """The default asks: a new caller that forgets to decide is safe."""
    wf.set_auto_approve(True)
    assert not wf.request_confirmation("something new").done()
    assert wf.request_confirmation("next song", may_auto_approve=True).result(timeout=1) is True


# ── saved, and applied at start ──────────────────────────────────────────────

def test_the_switch_is_saved_and_applies_at_start(tmp_path):
    from agents.bootstrap import build_agent_manager
    from config import cfg
    from ui.app_bridge import AppBridge

    assert cfg.switch("auto_approve") is False, "off until the user turns it on"
    assert build_agent_manager(workspace_root=str(tmp_path), in_memory=True,
                               offline=True).auto_approve is False
    bridge = AppBridge.__new__(AppBridge)
    AppBridge.__init__(bridge)                # no manager: nothing running to apply it to
    bridge.set_auto_approve(True)
    assert "TORA_AGENT_AUTO_APPROVE=1" in (tmp_path / ".env").read_text(encoding="utf-8")
    assert build_agent_manager(workspace_root=str(tmp_path), in_memory=True,
                               offline=True).auto_approve is True


# ── the Settings page ────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _bridge(wf):
    from PyQt6.QtCore import QObject, pyqtSignal
    from ui.app_bridge import AppBridge

    class Manager(QObject):
        sig_state_changed = pyqtSignal(object)

        def __init__(self):
            super().__init__()
            self.agents = wf

    manager = Manager()
    bridge = AppBridge(manager, None)
    bridge._keep = manager
    return bridge


def test_settings_opens_with_approvals_and_the_switch_works(qapp, wf):
    from PyQt6.QtWidgets import QLabel
    from config import cfg
    from ui.settings_tabs import AgentsTab as SettingsPage
    from ui.app_widgets import Card, ToggleRow

    page = SettingsPage(_bridge(wf))
    first = page.body.column.itemAt(0).widget()
    assert isinstance(first, Card)
    texts = [lbl.text() for lbl in first.findChildren(QLabel)]
    assert "Approvals" in texts and "Approve automatically" in texts
    assert any(t.startswith("Still asks, every time: anything that pays, files a return, "
                            "sends, posts or deletes") for t in texts), "its edges are on the page"
    (row,) = first.findChildren(ToggleRow)
    assert not row.switch.isChecked()
    row.switch.setChecked(True)
    assert wf.auto_approve is True and cfg.switch("auto_approve") is True
    row.switch.setChecked(False)
    assert wf.auto_approve is False and cfg.switch("auto_approve") is False


def test_settings_shows_what_the_running_agents_do(qapp, wf):
    from ui.settings_tabs import AgentsTab as SettingsPage
    from ui.app_widgets import Card, ToggleRow

    wf.set_auto_approve(True)                 # e.g. from the last launch
    page = SettingsPage(_bridge(wf))
    first = page.body.column.itemAt(0).widget()
    assert isinstance(first, Card) and first.findChildren(ToggleRow)[0].switch.isChecked()
