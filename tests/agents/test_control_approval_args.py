"""APP-01: a desktop action you approve, happens.

The field log has 12 desktop actions that the user APPROVED and that then
did nothing, because the live model named an argument its own way and the
arguments were only checked after the approval:

    windows.open_app   'app_name' for 'name'        x8
    windows.open_folder 'path' for 'folder'
    windows.volume     'level' for 'to_percent'
    windows.volume     "set it to 30%" with no direction
    windows.window     'action' for 'command'
    spotify.play       nothing to play

Now a name that can only mean one parameter is accepted, and anything that
still does not fit goes back to the model before anyone is asked.
"""
from __future__ import annotations

import asyncio

import pytest

from agents.control import AppControl, Risk, Tier, set_control
from agents.control.args import BindError, bind
from agents.control.model import Action, Param


def _real(app, action):
    from agents.control import control as live_control
    found = live_control().find(app, action)
    assert found is not None, f"{app}.{action} is not declared"
    return found


def _bind(app, action, arguments):
    found = _real(app, action)
    return bind(found.key, found.params, arguments)


# ── the field log, replayed against the real declarations ────────────────────

@pytest.mark.parametrize("app, action, sent, bound", [
    ("windows", "open_app", {"app_name": "calculator"}, {"name": "calculator"}),
    ("windows", "open_app", {"app": "calendar"}, {"name": "calendar"}),
    ("windows", "open_folder", {"path": "Downloads"}, {"folder": "Downloads"}),
    ("windows", "volume", {"level": 30}, {"to_percent": 30}),
    ("windows", "volume", {"to_percent": "30"}, {"to_percent": 30}),
    ("windows", "volume", {"percent": 0}, {"to_percent": 0}),
    ("windows", "window", {"action": "minimise", "title": "Notepad"},
     {"command": "minimise", "title": "Notepad"}),
    ("windows", "search_web", {"search": "weather"}, {"query": "weather"}),
])
def test_what_the_model_actually_sends_is_understood(app, action, sent, bound):
    assert _bind(app, action, sent) == bound


def test_play_with_nothing_to_play_is_still_a_question():
    with pytest.raises(BindError, match="needs query"):
        _bind("spotify", "play", {})


def test_volume_with_neither_direction_nor_a_level_is_refused():
    with pytest.raises(BindError) as err:
        _bind("windows", "volume", {})
    assert "direction (or to_percent)" in str(err.value)


# ── still never a guess ──────────────────────────────────────────────────────

def test_an_unknown_name_is_still_refused_with_a_suggestion():
    with pytest.raises(BindError, match="no argument called 'loudness'"):
        _bind("windows", "volume", {"loudness": 3, "direction": "up"})


def test_a_name_and_its_alias_together_are_refused():
    with pytest.raises(BindError, match="once"):
        _bind("windows", "open_app", {"app_name": "a", "name": "b"})


def test_two_aliases_for_one_argument_are_refused():
    with pytest.raises(BindError, match="once"):
        _bind("windows", "open_app", {"app": "a", "app_name": "b"})


def test_a_name_that_could_mean_two_arguments_means_neither():
    """'window_name' is an alias of title AND contains name."""
    params = (Param("name", required=True), Param("title"))
    with pytest.raises(BindError, match="no argument called 'window_name'"):
        bind("x.y", params, {"window_name": "Notepad", "name": "a"})


def test_an_alias_never_lands_on_an_argument_the_action_does_not_have():
    """'dir' is an alias of folder - but volume has no folder."""
    with pytest.raises(BindError, match="no argument called 'dir'"):
        _bind("windows", "volume", {"dir": "down"})


# ── "set the volume to 30 percent" ───────────────────────────────────────────

def _windows_volume(monkeypatch, *, endpoint=True):
    from agents.control import audio, system
    from agents.control.model import Unavailable

    state = {"level": 0.5, "taps": []}

    def level():
        if not endpoint:
            raise Unavailable("no endpoint")
        return state["level"]

    def set_level(value):
        state["level"] = value
        return value

    monkeypatch.setattr(audio, "level", level)
    monkeypatch.setattr(audio, "set_level", set_level)
    monkeypatch.setattr(audio, "describe", lambda v: f"{round(v * 100)}%")
    monkeypatch.setattr(system, "tap", lambda key, n=1: state["taps"].append((key, n)))
    return _real("windows", "volume"), state


@pytest.mark.parametrize("percent", [30, 0, 100])
def test_a_level_is_set_exactly(monkeypatch, percent):
    found, state = _windows_volume(monkeypatch)
    said = asyncio.run(found.run(to_percent=percent))
    assert state["level"] == percent / 100 and f"set to {percent}%" in said


def test_a_level_that_cannot_be_set_is_refused_never_nudged(monkeypatch):
    """Keys can only nudge; tapping 'down' for 'set it to 60' would be the
    opposite-action bug all over again."""
    from agents.control.model import Refused

    found, state = _windows_volume(monkeypatch, endpoint=False)
    with pytest.raises(Refused):
        asyncio.run(found.run(to_percent=60))
    assert state["taps"] == []


def test_up_and_down_still_work(monkeypatch):
    found, state = _windows_volume(monkeypatch)
    asyncio.run(found.run(direction="down", steps=5))
    assert state["level"] == pytest.approx(0.4)


# ── nobody is asked to approve something that cannot run ─────────────────────

class _Launcher:
    app = "launcher"
    display_name = "Launcher"
    aliases = ()

    def __init__(self):
        self.opened = []

    def available(self):
        return True

    def actions(self):
        async def open_app(name: str):
            self.opened.append(name)
            return f"opened {name}"

        return [Action("launcher", "open_app", "Open an app.", open_app, Tier.SYSTEM,
                       Risk.CHANGES, (Param("name", "Application name", True),))]


@pytest.fixture
def launcher():
    app = _Launcher()
    set_control(AppControl([app]))
    yield app
    set_control(None)


def _companion(tmp_path):
    from agents.bootstrap import build_agent_manager
    from companion_manager import CompanionManager

    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True,
                              offline=True).start()
    companion = CompanionManager.__new__(CompanionManager)
    companion._agents = mgr
    companion._realtime = None
    companion._submit = lambda coro: coro.close()
    return companion, mgr


def test_a_call_that_cannot_run_never_reaches_the_approval_dialog(launcher, tmp_path):
    companion, mgr = _companion(tmp_path)
    try:
        out = asyncio.run(companion._tool_desktop_action({
            "app": "launcher", "action": "open_app",
            "arguments_json": '{"programme_to_start": "calculator"}'}))
        assert "no argument called" in out["error"]
        assert "has not been asked" in out["note"]
        assert not mgr.pending_approvals(), "the user is never asked about it"
        assert launcher.opened == []
    finally:
        mgr.stop()


def test_the_dialog_shows_the_arguments_that_will_really_run(launcher, tmp_path):
    companion, mgr = _companion(tmp_path)
    try:
        out = asyncio.run(companion._tool_desktop_action({
            "app": "launcher", "action": "open_app",
            "arguments_json": '{"app_name": "calculator"}'}))
        assert out.get("awaiting_approval"), out
        (request,) = mgr.pending_approvals()
        args = request.call.arguments
        assert args.get("name") == "calculator" and "app_name" not in args
    finally:
        mgr.stop()
