"""M20: computer control.

Nothing here touches the real mouse or keyboard: a recording Backend is
injected and the name resolver is stubbed. The live gate (open Notepad,
type, save) is `@pytest.mark.live` because it hijacks the desktop.
"""
from __future__ import annotations

import asyncio

import pytest

from agents.core import (
    ApprovalRequest, PermissionPolicy, RiskLevel, ToolCall, ToolContext, auto_deny,
)
from agents.tools import computer_tools as ct
from agents.tools import default_registry


class Recorder(ct.Backend):
    def __init__(self):
        self.calls: list[tuple] = []
        self._pos = (0, 0)

    def move(self, x, y):
        self._pos = (x, y)
        self.calls.append(("move", x, y))

    def click(self, x, y, *, button="left", count=1):
        self._pos = (x, y)
        self.calls.append(("click", x, y, button, count))

    def type_text(self, text):
        self.calls.append(("type", text))

    def press(self, keys):
        self.calls.append(("press", tuple(keys)))

    def position(self):
        return self._pos


SCREEN = {"Save": {"x": 100, "y": 200, "label": "Save", "source": "uia", "confidence": 0.95},
          "File menu": {"x": 10, "y": 10, "label": "File", "source": "uia", "confidence": 0.9}}


@pytest.fixture
def rec():
    backend = Recorder()
    ct.set_backend(backend)
    ct.set_resolver(lambda name: SCREEN.get(name))
    ct.kill_switch.reset()
    yield backend
    ct.set_backend(None)
    ct.set_resolver(None)
    ct.kill_switch.reset()


def run(tool, ctx=None, **args):
    ctx = ctx or ToolContext(workspace=".", policy=PermissionPolicy(max_auto_risk=RiskLevel.HIGH))
    return asyncio.run(default_registry().execute(ToolCall(tool, args), ctx))


# -- name resolution ---------------------------------------------------------

def test_click_resolves_a_name_through_the_pointing_stack(rec):
    r = run("click", name="Save")
    assert r.ok, r.error
    assert rec.calls == [("click", 100, 200, "left", 1)]
    assert r.data["source"] == "uia" and r.data["label"] == "Save"
    assert "Save" in r.output


def test_unknown_name_fails_with_advice_not_a_guess(rec):
    r = run("click", name="Nonexistent Button")
    assert not r.ok
    assert "find_on_screen" in r.error
    assert rec.calls == [], "nothing must be clicked when the target is unknown"


def test_coordinates_are_a_fallback(rec):
    r = run("move_cursor", x=5, y=6)
    assert r.ok and rec.calls == [("move", 5, 6)]
    assert r.data["source"] == "coordinates"


def test_neither_name_nor_coordinates_is_an_error(rec):
    r = run("click")
    assert not r.ok and "name" in r.error


def test_find_on_screen_is_safe_and_moves_nothing(rec):
    ctx = ToolContext(workspace=".", approver=auto_deny)     # default ceiling MEDIUM
    r = run("find_on_screen", ctx=ctx, name="File menu")
    assert r.ok, r.error                                      # SAFE: no approval needed
    assert r.data["x"] == 10 and r.data["found"] is True
    assert rec.calls == []
    miss = run("find_on_screen", ctx=ctx, name="nothing")
    assert not miss.ok and miss.data["found"] is False


def test_double_and_right_click(rec):
    assert run("click", name="Save", double=True).ok
    assert run("click", name="Save", button="right").ok
    assert rec.calls == [("click", 100, 200, "left", 2), ("click", 100, 200, "right", 1)]


def test_type_and_key_press(rec):
    assert run("type_text", text="hello\nworld").ok
    assert run("key_press", keys=["ctrl", "s"]).ok
    assert rec.calls == [("type", "hello\nworld"), ("press", ("ctrl", "s"))]
    assert not run("type_text", text="").ok
    assert not run("key_press", keys=[]).ok


# -- the floor: always ask ---------------------------------------------------

def test_computer_tools_ask_even_with_a_high_ceiling(rec):
    """max_auto_risk=HIGH would auto-approve any other tool. Not these."""
    asked: list[ApprovalRequest] = []

    async def approver(req):
        asked.append(req)
        return True

    ctx = ToolContext(workspace=".", approver=approver,
                      policy=PermissionPolicy(max_auto_risk=RiskLevel.HIGH))
    assert run("click", ctx=ctx, name="Save").ok
    assert len(asked) == 1 and asked[0].call.tool_name == "click"
    assert "mouse and keyboard" in asked[0].reason


def test_computer_tools_ask_even_when_always_allowed(rec):
    """'Always allow click' is not a thing you get to say."""
    asked = []

    async def approver(req):
        asked.append(req)
        return False

    policy = PermissionPolicy(max_auto_risk=RiskLevel.HIGH)
    policy.allow_always("click")
    ctx = ToolContext(workspace=".", approver=approver, policy=policy)
    r = run("click", ctx=ctx, name="Save")
    assert not r.ok and "denied" in r.error.lower()
    assert len(asked) == 1
    assert rec.calls == []


def test_other_high_tools_do_not_get_the_floor(rec, tmp_path):
    """The floor is opt-in per tool, not a change to HIGH risk generally."""
    asked = []

    async def approver(req):
        asked.append(req)
        return True

    ctx = ToolContext(workspace=str(tmp_path), approver=approver,
                      policy=PermissionPolicy(max_auto_risk=RiskLevel.HIGH))
    (tmp_path / "x.txt").write_text("x", encoding="utf-8")
    r = asyncio.run(default_registry().execute(ToolCall("delete_path", {"path": "x.txt"}), ctx))
    assert r.ok
    assert asked == [], "delete_path at a HIGH ceiling should not have asked"


# -- kill switch -------------------------------------------------------------

def test_kill_switch_stops_every_action(rec):
    ct.kill_switch.trip()
    r = run("click", name="Save")
    assert not r.ok and "kill switch" in r.error
    assert rec.calls == []
    ct.kill_switch.reset()
    assert run("click", name="Save").ok


def test_kill_switch_callbacks_fire_once_per_trip(rec):
    fired = []
    off = ct.kill_switch.on_trip(lambda: fired.append(1))
    ct.kill_switch.trip()
    ct.kill_switch.trip()                 # already tripped: no second fire
    assert fired == [1]
    off()
    ct.kill_switch.reset()
    ct.kill_switch.trip()
    assert fired == [1]                   # unsubscribed


def test_kill_switch_cancels_computer_tasks_only(tmp_path, rec):
    from agents.bootstrap import build_agent_manager
    from agents.core import Task, TaskKind, TaskStatus
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True).start()
    try:
        computer = mgr.submit_task(Task(title="c", goal="click things", workspace=str(tmp_path),
                                        kind=TaskKind.COMPUTER, worker_type="computer"))
        coding = mgr.submit_task(Task(title="k", goal="Create a folder called Keep",
                                      workspace=str(tmp_path), kind=TaskKind.CODING))
        n = mgr.cancel_all(kinds=[TaskKind.COMPUTER])
        assert n <= 1
        done_c = mgr.wait(computer.id, 30)
        done_k = mgr.wait(coding.id, 30)
        assert done_c.status in (TaskStatus.CANCELLED, TaskStatus.COMPLETED, TaskStatus.FAILED)
        assert done_k.status is TaskStatus.COMPLETED, "a coding task must not be caught by Esc"
    finally:
        mgr.stop()


# -- the live gate -----------------------------------------------------------

@pytest.mark.live
def test_live_notepad_type_and_save(tmp_path):
    """Opens Notepad, types a line, saves it - entirely through approved
    tool calls on the real desktop. Run with -m live, hands off the mouse."""
    import os
    import subprocess
    import time

    ct.set_backend(None)
    ct.set_resolver(None)
    ct.kill_switch.reset()
    target = tmp_path / "from-agent.txt"

    proc = subprocess.Popen(["notepad.exe"])
    time.sleep(1.5)
    try:
        ctx = ToolContext(workspace=str(tmp_path),
                          policy=PermissionPolicy(max_auto_risk=RiskLevel.HIGH))
        assert run("type_text", ctx=ctx, text="typed by the Tora computer worker").ok
        assert run("key_press", ctx=ctx, keys=["ctrl", "s"]).ok
        time.sleep(1.0)
        assert run("type_text", ctx=ctx, text=str(target) + "\n").ok
        time.sleep(1.0)
        assert os.path.isfile(target), "Notepad did not save where we typed"
        assert "Tora computer worker" in target.read_text(encoding="utf-8", errors="replace")
    finally:
        proc.kill()
