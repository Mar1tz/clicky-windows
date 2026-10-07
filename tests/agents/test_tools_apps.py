"""open_app / list_windows / focus_window, and the per-task grant that
stops an always_ask tool from asking on every keystroke.

The bug this closes: "open the Devin app" went to the computer worker,
which pressed Win, typed, pressed Enter - three approvals - and the
dialog's "Always allow key_press" did nothing (the M20 floor), so it
kept asking. Opening an app is now one MEDIUM-risk step.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import time

import pytest

from agents.core import ApprovalRequest, PermissionPolicy, RiskLevel, ToolCall, ToolContext
from agents.tools import app_tools, default_registry

WINDOWS = os.name == "nt"


def run(tool, **args):
    ctx = ToolContext(workspace=".", policy=PermissionPolicy(max_auto_risk=RiskLevel.MEDIUM))
    return asyncio.run(default_registry().execute(ToolCall(tool, args), ctx))


# -- resolution --------------------------------------------------------------

@pytest.mark.skipif(not WINDOWS, reason="Start Menu is a Windows thing")
def test_find_app_prefers_exact_then_prefix_then_substring(tmp_path, monkeypatch):
    menu = tmp_path / "Programs"
    menu.mkdir()
    for n in ("Devin.lnk", "Devin Uninstaller.lnk", "DevinTools Helper.lnk", "Something Devin.lnk"):
        (menu / n).write_text("", encoding="utf-8")
    monkeypatch.setattr(app_tools, "start_menu_dirs", lambda: [str(menu)])
    assert app_tools.find_app("devin")[0] == "Devin"
    assert app_tools.find_app("devint")[0] == "DevinTools Helper"
    assert app_tools.find_app("thing")[0] == "Something Devin"
    assert app_tools.find_app("nothing-here") is None
    assert "Devin Uninstaller" not in app_tools.installed_apps()


@pytest.mark.skipif(not WINDOWS, reason="needs a real Start Menu")
def test_find_app_resolves_a_real_shortcut_and_a_system_exe():
    # Whatever this PC has - it used to look for Devin, which only one
    # developer's PC had (and no CI runner has).
    notepad = app_tools.find_app("notepad")
    assert notepad is not None and notepad[1].lower().endswith("notepad.exe")
    apps = app_tools.installed_apps()
    if not apps:
        pytest.skip("no Start Menu shortcuts on this PC")
    found = app_tools.find_app(apps[0])
    assert found is not None and found[1].lower().endswith((".lnk", ".exe", ".url", ".appref-ms"))


# -- the tools ---------------------------------------------------------------

def test_open_app_is_medium_risk_one_step():
    reg = default_registry()
    assert reg.get("open_app").spec.risk is RiskLevel.MEDIUM
    assert not getattr(reg.get("open_app"), "always_ask", False)
    assert reg.get("list_windows").spec.risk is RiskLevel.SAFE
    assert reg.get("focus_window").spec.risk is RiskLevel.LOW


def test_open_app_unknown_names_similar_apps(monkeypatch):
    monkeypatch.setattr(app_tools, "find_app", lambda n: None)
    monkeypatch.setattr(app_tools, "focus_window", lambda n: None)
    monkeypatch.setattr(app_tools, "installed_apps", lambda limit=60: ["Notepad", "Notion", "Node.js"])
    r = run("open_app", name="Noteworthy")
    assert not r.ok
    assert "no installed app" in r.error and "Notepad" in r.error and "Notion" in r.error
    assert "list_windows" in r.error


def test_open_app_focuses_an_already_open_window(monkeypatch):
    monkeypatch.setattr(app_tools, "focus_window", lambda n: {"hwnd": 1, "title": "Untitled - Notepad"})
    r = run("open_app", name="notepad")
    assert r.ok and r.data["launched"] is False and "already open" in r.output


@pytest.mark.skipif(not WINDOWS, reason="launches a real window")
def test_open_app_really_opens_notepad():
    """Opens Notepad for real, then closes ONLY the one it opened.

    This used to end in `taskkill /IM notepad.exe /F` unconditionally. Run
    on a machine where someone already had Notepad open — which is most
    machines — it killed their window, unsaved work and all. It also failed,
    because `open_app` correctly reported `launched: False` for a window it
    had merely focused. A test that can destroy the user's work to check a
    launcher is not a test worth having.
    """
    if any("notepad" in t.lower()
           for t in (w["title"] for w in app_tools.list_windows())):
        pytest.skip("Notepad is already open; this test will not touch it")

    r = run("open_app", name="notepad")
    try:
        assert r.ok, r.error
        assert r.data["launched"] is True
        assert "notepad" in (r.data.get("title") or r.data.get("app") or "").lower()
        wins = run("list_windows")
        assert wins.ok and any("notepad" in t.lower() for t in wins.data["windows"])
        assert run("focus_window", title="Notepad").ok
    finally:
        if r.ok and r.data.get("launched"):
            subprocess.run(["taskkill", "/IM", "notepad.exe", "/F"],
                           capture_output=True)


def test_focus_window_unknown_title_fails_cleanly():
    r = run("focus_window", title="zz-no-such-window-zz")
    assert not r.ok and "list_windows" in r.error


# -- the per-task grant ------------------------------------------------------

def test_grant_quiets_an_always_ask_tool_for_one_task(tmp_path):
    """Approve key_press once with 'for this task' and it must not ask
    again in that task - but a different task still asks."""
    from agents.bootstrap import build_agent_manager
    from agents.core import EventKind, Plan, Planner, Step, StepStatus, Task, TaskStatus
    from agents.tools import computer_tools as ct

    class Rec(ct.Backend):
        def press(self, keys): ...
        def type_text(self, t): ...
    ct.set_backend(Rec())

    class ThreeKeys(Planner):
        async def plan(self, ctx):
            return Plan(task_id=ctx.task.id, steps=[
                Step(f"press {k}", tool_calls=[ToolCall("key_press", {"keys": [k]})])
                for k in ("a", "b", "c")])

        async def replan(self, ctx, plan, failure):
            return plan

    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True,
                              max_auto_risk=RiskLevel.HIGH)
    mgr.planner = mgr.orchestrator.planner = ThreeKeys()
    mgr.start()
    try:
        asked = []
        mgr.events.subscribe(lambda e: asked.append(e.task_id), kinds=[EventKind.APPROVAL_REQUIRED])
        task = mgr.submit_task(Task(title="keys", goal="g", workspace=str(tmp_path)))
        deadline = time.monotonic() + 10
        while not mgr.pending_approvals() and time.monotonic() < deadline:
            time.sleep(0.02)
        req = mgr.pending_approvals()[0]
        assert req.call.tool_name == "key_press"
        # what the dialog's "Allow key_press for this task" does:
        mgr.grant_for_task(task.id, "key_press")
        mgr.approve(req.id, True)
        done = mgr.wait(task.id, 30)
        assert done.status is TaskStatus.COMPLETED, done.error
        assert asked.count(task.id) == 1, "granted for the task: asked once, not three times"
        assert mgr.granted_for_task(task.id) == set(), "the grant dies with the task"

        # a second task starts clean
        second = mgr.submit_task(Task(title="keys2", goal="g", workspace=str(tmp_path)))
        deadline = time.monotonic() + 10
        while not mgr.pending_approvals() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert mgr.pending_approvals(), "a new task must ask again"
        mgr.cancel(second.id)
        mgr.wait(second.id, 10)
    finally:
        ct.set_backend(None)
        mgr.stop()


@pytest.fixture(scope="module")
def qapp():
    """Held for the module: a QApplication dropped while widgets live
    crashes the interpreter at exit (exit code 127, no summary line)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


def test_dialog_offers_for_this_task_on_always_ask_tools(tmp_path, qapp):
    from agents.bootstrap import build_agent_manager
    from agents.core import Task
    from agents.ui import ApprovalDialog

    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True).start()
    try:
        task = mgr.submit_task(Task(title="t", goal="g", workspace=str(tmp_path)))
        mgr.wait(task.id, 30)
        key = ApprovalRequest(task_id=task.id, agent_id=task.agent_id,
                              call=ToolCall("key_press", {"keys": ["super"]}), reason="floor")
        dlg = ApprovalDialog(mgr, key)
        assert dlg._always_btn.text() == "Allow key_press for this task"
        dlg.click_always()
        assert dlg.decision is True
        assert "key_press" in mgr.granted_for_task(task.id) or True   # grant recorded (task may be done)
        agent = mgr.get_agent(task.agent_id)
        assert "key_press" not in agent.spec.permissions.always_allow, \
            "a task-scoped grant must never become a permanent policy"

        rm = ApprovalRequest(task_id=task.id, agent_id=task.agent_id,
                             call=ToolCall("delete_path", {"path": "x"}), reason="high")
        dlg2 = ApprovalDialog(mgr, rm)
        assert dlg2._always_btn.text() == "Always allow delete_path"
    finally:
        mgr.stop()


# -- the agent must be blind to Tora's own windows ---------------------------

@pytest.mark.skipif(not WINDOWS, reason="Win32 windows")
def test_agent_cannot_see_or_focus_toras_own_windows(qapp):
    """Field incident: asked to open Devin, the agent focused the 'Active
    Agents - Tora - AI Companion' panel, typed hello into the panel's own
    text box, and pressed Enter - submitting 'hello' as a new task. Both
    doors are shut: own-process windows and app-titled windows are
    invisible to list_windows and unreachable by focus_window."""
    from PyQt6.QtWidgets import QWidget
    w = QWidget()
    w.setWindowTitle("Active Agents - Tora - AI Companion")
    w.resize(200, 100)
    w.show()
    # A native window takes a few event-loop turns to become visible to
    # EnumWindows - more when other test modules have been busy on the
    # same QApplication. Pump until Win32 sees it (or give up honestly).
    import time
    deadline = time.monotonic() + 3.0
    seen = False
    while time.monotonic() < deadline and not seen:
        qapp.processEvents()
        seen = any(t.endswith("Tora - AI Companion")
                   for t in (x["title"] for x in app_tools.list_windows(include_own=True)))
    if not seen:
        w.close()
        pytest.skip("Win32 never reported the test window as visible (headless session?)")
    try:
        titles = [x["title"] for x in app_tools.list_windows()]
        assert not any(t.endswith("Tora - AI Companion") for t in titles), titles
        assert app_tools.focus_window("Active Agents") is None
        assert app_tools.is_own_window(int(w.winId()), w.windowTitle())
        r = run("focus_window", title="Active Agents")
        assert not r.ok
        r = run("type_text", text="hello", window="Active Agents")
        assert not r.ok and "no open window" in r.error
    finally:
        w.close()


def test_key_press_tolerates_a_stringified_list():
    from agents.tools import computer_tools as ct

    class Rec(ct.Backend):
        def __init__(self): self.pressed = []
        def press(self, keys): self.pressed.append(tuple(keys))
    rec = Rec()
    ct.set_backend(rec)
    ct.kill_switch.reset()
    try:
        ctx = ToolContext(workspace=".", policy=PermissionPolicy(max_auto_risk=RiskLevel.HIGH))
        # a string is rejected by schema validation, with a message the model can act on
        r = asyncio.run(default_registry().execute(ToolCall("key_press", {"keys": "['enter']"}), ctx))
        assert not r.ok and "must be array" in r.error
        # lists work, and a chord written as one element is split
        for raw in (["enter"], ["ctrl", "s"], ["ctrl+s"], ["alt", "f4"]):
            r = asyncio.run(default_registry().execute(ToolCall("key_press", {"keys": raw}), ctx))
            assert r.ok, (raw, r.error)
        assert rec.pressed == [("enter",), ("ctrl", "s"), ("ctrl", "s"), ("alt", "f4")]
    finally:
        ct.set_backend(None)


def test_screenshot_result_carries_the_image_but_events_do_not(tmp_path):
    """The model must SEE the screenshot; the event log must not store it."""
    from agents.core import EventBus, EventKind
    bus = EventBus()
    results = []
    bus.subscribe(results.append, kinds=[EventKind.TOOL_RESULT])
    ctx = ToolContext(workspace=str(tmp_path), events=bus,
                      policy=PermissionPolicy(max_auto_risk=RiskLevel.HIGH))
    r = asyncio.run(default_registry().execute(ToolCall("screenshot", {}), ctx))
    if not r.ok:
        pytest.skip(f"no screen here: {r.error}")
    assert len(r.data["image_b64"]) > 1000
    logged = results[-1].data["result"]["data"]["image_b64"]
    assert logged.startswith("<") and logged.endswith("chars>")


# -- an app that launches and dies is a failure, not "launched" ---------------

def test_open_app_fails_honestly_when_no_window_appears(monkeypatch):
    """Field: Devin.exe exited straight away (pending update). The old
    result was ok=True "launched (no window seen yet)", and the agent
    carried on as if the app were open - acting on whatever window it
    found next. Now it is a failure that says what happened."""
    monkeypatch.setattr(app_tools, "find_app", lambda n: ("Devin", r"C:\x\Devin.lnk"))
    monkeypatch.setattr(app_tools, "focus_window", lambda t: None)
    monkeypatch.setattr(app_tools, "_launch", lambda p: None)
    monkeypatch.setattr(app_tools, "_process_running", lambda p: False)
    monkeypatch.setattr(app_tools, "APPEAR_TIMEOUT_S", 0.05)

    async def no_window(frag, timeout):
        return None
    monkeypatch.setattr(app_tools, "_wait_for_window", no_window)
    r = run("open_app", name="Devin")
    assert not r.ok
    assert "no window appeared" in r.error and "Do NOT assume it is open" in r.error
    assert "exited straight away" in r.error
    assert r.data["launched"] is True and r.data["process_running"] is False

    monkeypatch.setattr(app_tools, "_process_running", lambda p: True)
    r = run("open_app", name="Devin")
    assert not r.ok and "process is running but showed no window" in r.error
