"""Desktop control: the ladder, the asking, and the real Windows routes.

Nothing here presses a real key or opens a real app except the two tests
marked `windows_real`, which only read (what is installed, which controls
a window exposes).
"""
from __future__ import annotations

import asyncio
import os

import pytest

from agents.control import AppControl, Outcome, Risk, Tier, control, set_control
from agents.control.model import Action, Param, Refused, Unavailable
from agents.core import PermissionPolicy, RiskLevel, ToolCall, ToolContext
from agents.tools import default_registry

WINDOWS = os.name == "nt"


class FakeSpotify:
    app = "spotify"
    display_name = "Spotify"
    aliases = ("music",)

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def available(self) -> bool:
        return True

    def actions(self):
        async def next_track(times: int = 1):
            self.calls.append(("next", {"times": times}))
            return f"skipped {times} track(s)"

        async def now_playing():
            self.calls.append(("now_playing", {}))
            return "Playing: Weightless - Marconi Union (Spotify)"

        async def play_playlist(playlist: str):
            self.calls.append(("play_playlist", {"playlist": playlist}))
            if playlist.startswith("spotify:"):
                return "opened that playlist"
            raise Unavailable("only links can be played directly")

        async def share(to: str):
            self.calls.append(("share", {"to": to}))
            return f"sent to {to}"

        return [
            Action("spotify", "next", "Next track.", next_track, Tier.SYSTEM, Risk.REVERSIBLE,
                   (Param("times", "How many", type="integer"),), aliases=("skip",)),
            Action("spotify", "now_playing", "What is playing.", now_playing, Tier.SYSTEM, Risk.PASSIVE),
            Action("spotify", "play_playlist", "Play a playlist.", play_playlist, Tier.DEEP_LINK,
                   Risk.CHANGES, (Param("playlist", "Link or name", True),)),
            Action("spotify", "share", "Send the track to someone.", share, Tier.UI_FOREGROUND,
                   Risk.SENSITIVE, (Param("to", "Who", True),)),
        ]


@pytest.fixture
def fake(monkeypatch):
    spotify = FakeSpotify()
    set_control(AppControl([spotify]))
    yield spotify
    set_control(None)


def run(tool, *, policy=RiskLevel.HIGH, approve=True, workspace=".", **args):
    approvals: list = []

    async def approver(req):
        approvals.append(req)
        return approve
    ctx = ToolContext(workspace=workspace, policy=PermissionPolicy(max_auto_risk=policy),
                      approver=approver)
    result = asyncio.run(default_registry().execute(ToolCall(tool, args), ctx))
    result.said = f"{result.output} {result.error}".strip()      # what the agent reads back
    return result, approvals


# -- the catalogue -------------------------------------------------------------------

def test_the_agent_can_ask_what_this_machine_does(fake):
    result, asked = run("list_app_actions")
    assert result.ok and not asked, "looking never interrupts"
    assert "spotify.next" in result.output and "system" in result.output
    names = {a["action"] for a in result.data["actions"]}
    assert {"next", "now_playing", "play_playlist", "share"} <= names


def test_unknown_apps_and_actions_come_back_with_what_does_exist(fake):
    result, _ = run("app_action", app="winamp", action="next")
    assert not result.ok and "spotify" in result.said.lower()
    result, _ = run("app_action", app="music", action="fly")
    assert not result.ok and "next" in result.said


# -- asking, per action ----------------------------------------------------------------

def test_passive_actions_do_not_ask_and_acting_ones_do(fake):
    result, asked = run("app_action", app="spotify", action="now_playing")
    assert result.ok and not asked, "reading what is playing must not interrupt"
    assert "Marconi Union" in result.output

    result, asked = run("app_action", app="spotify", action="next")
    assert result.ok and len(asked) == 1
    assert asked[0].grant_key == "app_action:spotify.next"
    assert "next track" in asked[0].reason.lower()
    assert "without touching your screen" in asked[0].reason.lower()
    assert fake.calls[-1][0] == "next"


def test_denying_stops_it(fake):
    result, asked = run("app_action", app="spotify", action="next", approve=False)
    assert not result.ok and len(asked) == 1
    assert not [c for c in fake.calls if c[0] == "next"], "the app was never touched"


def test_a_standing_yes_is_for_one_action_not_the_whole_tool(fake):
    """'Always allow spotify.next' must not also wave through 'share'."""
    async def deny(req):
        raise AssertionError(f"should not have asked: {req.call.tool_name}")
    ctx = ToolContext(workspace=".", approver=deny,
                      policy=PermissionPolicy(max_auto_risk=RiskLevel.HIGH,
                                              always_allow=["app_action:spotify.next"]))
    result = asyncio.run(default_registry().execute(ToolCall("app_action", {"app": "spotify",
                                                                            "action": "next"}), ctx))
    assert result.ok

    asked: list = []

    async def approver(req):
        asked.append(req)
        return False
    ctx2 = ToolContext(workspace=".", approver=approver,
                       policy=PermissionPolicy(max_auto_risk=RiskLevel.HIGH,
                                               always_allow=["app_action:spotify.next"]))
    result = asyncio.run(default_registry().execute(
        ToolCall("app_action", {"app": "spotify", "action": "share", "arguments": {"to": "Sam"}}), ctx2))
    assert not result.ok and len(asked) == 1, "a different action still asks"


def test_the_reason_says_whether_the_screen_will_move(fake):
    _result, asked = run("app_action", app="spotify", action="share", arguments={"to": "Sam"})
    assert "ON YOUR SCREEN" in asked[0].reason


# -- the ladder -----------------------------------------------------------------------------

def test_the_tier_used_is_reported(fake):
    result, _ = run("app_action", app="spotify", action="next")
    assert result.data["tier"] == "system"
    result, _ = run("app_action", app="spotify", action="play_playlist",
                    arguments={"playlist": "spotify:playlist:abc"})
    assert result.ok and result.data["tier"] == "deep link"


def test_a_route_that_is_not_available_says_so_instead_of_clicking(fake):
    result, _ = run("app_action", app="spotify", action="play_playlist",
                    arguments={"playlist": "my focus mix"})
    assert not result.ok and "not available" in result.said
    assert result.data["attempts"], "the attempt is reported, so the agent can choose another way"


def test_missing_arguments_are_named(fake):
    result, _ = run("app_action", app="spotify", action="play_playlist")
    assert not result.ok and "playlist" in result.said


def test_the_kill_switch_stops_desktop_actions(fake):
    from agents.tools.computer_tools import kill_switch
    kill_switch.trip()
    try:
        result, asked = run("app_action", app="spotify", action="next")
        assert not result.ok and "stopped" in result.said.lower()
    finally:
        kill_switch.reset()


# -- one action, confirmed, with no task behind it (the voice path) ----------------------------

def test_request_confirmation_uses_the_same_dialog_and_remembers_a_yes(tmp_path):
    from agents.bootstrap import build_agent_manager
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True).start()
    try:
        seen: list = []
        mgr.events.subscribe(lambda ev: seen.append(ev), kinds=None)
        fut = mgr.request_confirmation("spotify.next - Next track (system)", grant_key="app_action:spotify.next")
        req = mgr.pending_approvals()[0]
        assert req.task_id == "" and req.grant_key == "app_action:spotify.next"
        assert mgr.approve(req.id, True)
        assert fut.result(5) is True

        mgr.allow_desktop_action("app_action:spotify.next")
        again = mgr.request_confirmation("again", grant_key="app_action:spotify.next")
        assert again.result(1) is True and not mgr.pending_approvals(), "a standing yes does not ask"

        denied = mgr.request_confirmation("spotify.share", grant_key="app_action:spotify.share")
        req = mgr.pending_approvals()[0]
        mgr.approve(req.id, False)
        assert denied.result(5) is False
    finally:
        mgr.stop()


# -- the real machine (read-only) ---------------------------------------------------------------

@pytest.mark.skipif(not WINDOWS, reason="Windows discovery")
def test_this_machine_reports_its_apps_links_and_clis():
    from agents.control.discovery import scan
    m = scan(force=True)
    assert m.apps and m.schemes, "a Windows install has Start Menu apps and URI handlers"
    assert "ms-settings" in m.schemes, "Windows always registers its own settings link"
    assert all(":" not in s for s in m.schemes), "schemes are stored without the colon"


@pytest.mark.skipif(not WINDOWS, reason="UI Automation")
def test_windows_itself_is_always_controllable():
    set_control(None)
    c = control()
    c.refresh()
    apps = {a["app"] for a in c.apps()}
    assert "windows" in apps
    actions = {a.name for a in c.actions(app="windows")}
    assert {"media", "volume", "window", "clipboard_copy", "open_folder", "open_settings"} <= actions
    out = asyncio.run(c.run("windows", "list_windows"))
    assert out.ok and out.tier is Tier.SYSTEM


def test_voice_fast_path_asks_without_going_silent(fake, tmp_path):
    """"Spotify, next song" must not spawn a task, must still ask - and must
    NOT make Tora wait for the answer.

    The live model is blocked until a tool returns, so awaiting the dialog
    meant she went mute for as long as the box was on screen: fourteen
    seconds in the field log, reported as "it just gets stuck". The call now
    comes back at once and the outcome is volunteered afterwards.
    """
    from agents.bootstrap import build_agent_manager
    from companion_manager import CompanionManager

    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True).start()
    companion = CompanionManager.__new__(CompanionManager)
    companion._agents = mgr
    companion._realtime = None
    said: list[str] = []
    background: list = []
    companion._submit = background.append

    async def remember(note):
        said.append(note)

    companion._speak_aside = remember
    try:
        async def ask_then(approve: bool):
            out = await companion._tool_desktop_action({"app": "spotify", "action": "next"})
            assert out.get("awaiting_approval") is True, "it must not block"
            assert not out.get("ok"), "and must not claim it is done"
            assert mgr.pending_approvals(), "the dialog has been raised"
            mgr.approve(mgr.pending_approvals()[0].id, approve)
            await background.pop()               # what _submit would have run
            return out

        asyncio.run(ask_then(False))
        assert not [c for c in fake.calls if c[0] == "next"], "a denial acts on nothing"
        assert said and "did not approve" in said[-1]

        asyncio.run(ask_then(True))
        assert fake.calls[-1][0] == "next", "an approval runs it, after the fact"
        assert "has now run" in said[-1] and "skipped" in said[-1]

        listing = asyncio.run(companion._tool_desktop_action({"app": "spotify", "action": "list"}))
        assert listing["ok"] and {a["action"] for a in listing["actions"]} >= {"next", "now_playing"}

        passive = asyncio.run(companion._tool_desktop_action({"app": "spotify", "action": "now_playing"}))
        assert passive["ok"] and not mgr.pending_approvals(), "reading never asks"
    finally:
        mgr.stop()


def test_an_action_already_allowed_runs_straight_away(fake, tmp_path):
    """"Always allow" has to mean no dialog AND no round trip - otherwise
    waving an action through makes it slower, not faster."""
    from agents.bootstrap import build_agent_manager
    from companion_manager import CompanionManager

    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True).start()
    companion = CompanionManager.__new__(CompanionManager)
    companion._agents = mgr
    companion._realtime = None
    companion._submit = lambda coro: pytest.fail("nothing should be deferred")
    try:
        mgr.allow_desktop_action("app_action:spotify.next")
        out = asyncio.run(companion._tool_desktop_action({"app": "spotify", "action": "next"}))
        assert out["ok"] and "skipped" in out["result"]
        assert not mgr.pending_approvals()
        assert fake.calls[-1][0] == "next"
    finally:
        mgr.stop()


def test_a_pending_confirmation_is_offered_to_the_voice(tmp_path):
    """DC-14: a voice assistant that needs a mouse click to say yes is not
    hands-free. The approve/deny machinery already existed in
    manage_agents; the model simply was not told anything was waiting."""
    from agents.bootstrap import build_agent_manager
    from companion_manager import CompanionManager

    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True).start()
    companion = CompanionManager.__new__(CompanionManager)
    companion._agents = mgr
    try:
        assert companion._pending_confirmation_line() == "", "silence when nothing waits"

        mgr.request_confirmation("spotify.next - Next track (system)",
                                 grant_key="app_action:spotify.next")
        line = companion._pending_confirmation_line()
        assert "waiting for a yes" in line and "spotify.next" in line
        assert "approve" in line and "deny" in line

        req = mgr.pending_approvals()[0]
        out = asyncio.run(companion._tool_manage_agents({"action": "approve"}))
        assert out["ok"] and not mgr.pending_approvals()
        assert req.approved is True
    finally:
        mgr.stop()


def test_a_sensitive_confirmation_is_never_approved_by_voice(tmp_path):
    """Paying a supplier is not a spoken yes."""
    from agents.bootstrap import build_agent_manager
    from agents.core import ApprovalRequest, ToolCall
    from companion_manager import CompanionManager

    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True).start()
    companion = CompanionManager.__new__(CompanionManager)
    companion._agents = mgr
    try:
        req = ApprovalRequest(task_id="", agent_id="", call=ToolCall("pay_supplier", {}),
                              reason="release a payment", sensitive=True,
                              confirm_word="CONFIRM")
        with mgr._lock:
            mgr._approvals[req.id] = (req, None)
        line = companion._pending_confirmation_line()
        assert "sensitive" in line and "CONFIRM" in line
        assert "never approve it for them" in line

        out = asyncio.run(companion._tool_manage_agents({"action": "approve"}))
        assert out["ok"] is False and "CONFIRM" in out["note"]
    finally:
        mgr.stop()


# ── "it only thinks of things on the browsers, not actual apps" ──────────────

class BrokenAdapter:
    """An app whose actions() raises — exactly what a stale module does."""
    app = "spotify"
    display_name = "Spotify"
    aliases = ("music",)

    def available(self) -> bool:
        return True

    def actions(self):
        raise TypeError("Action.__init__() got an unexpected keyword argument 'snapshot'")


def test_an_app_that_failed_to_load_says_so_instead_of_vanishing():
    """Field report: every Spotify request came back as an Edge tab.

    The cause was one adapter raising while describing itself. The registry
    caught it per-adapter — correct, so one bad app cannot hide the rest —
    but then said nothing, so the app simply had no actions and the model
    went looking for another way to do it. A browser is always another way.
    """
    from agents.control.registry import AppControl

    control = AppControl([BrokenAdapter()], providers=[])
    assert "spotify" in control.broken()
    assert "snapshot" in control.broken()["spotify"]

    message = control.suggestions("spotify", "play")
    assert "failed to load" in message
    assert "do NOT" in message and "browser" in message
    assert "It can: " not in message, "an empty list read as 'use something else'"


def test_a_broken_adapter_is_still_named_when_generic_actions_remain():
    """The launcher can open anything, so a broken adapter no longer leaves
    an app with nothing. The REASON the rest is missing still has to
    survive, or the next person debugs the symptom instead of the cause."""
    from agents.control.model import Action, Risk, Tier
    from agents.control.provider import AppProfile
    from agents.control.registry import AppControl

    class Launcher:
        name = "launcher"

        def apps(self):
            return [AppProfile("spotify", "Spotify", source="launcher")]

        def actions(self, app):
            async def go() -> str:
                return "opened it"
            return [Action("spotify", "open", "Open Spotify.", go,
                           Tier.SYSTEM, Risk.CHANGES)] if app == "spotify" else []

    control = AppControl([BrokenAdapter()], providers=[Launcher()])
    message = control.suggestions("spotify", "play")
    assert "failed to load" in message and "open" in message
    assert "restarting" in message.lower()


def test_a_browser_is_not_how_you_play_a_song():
    """open.spotify.com shows a page ABOUT a song. It does not play one."""
    from agents.control.registry import AppControl
    from agents.control.apps import browsers, media

    control = AppControl([media.Spotify(), *browsers.adapters()])
    assert control.app_for_url("https://open.spotify.com/search/Love%20Story") == "spotify"
    assert control.app_for_url("https://news.ycombinator.com") == ""
    assert control.app_for_url("") == ""


def test_opening_an_owned_url_in_a_browser_is_sent_back_to_the_app(tmp_path):
    from agents.bootstrap import build_agent_manager
    from companion_manager import CompanionManager

    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True).start()
    companion = CompanionManager.__new__(CompanionManager)
    companion._agents = mgr
    companion._realtime = None
    try:
        out = asyncio.run(companion._tool_desktop_action({
            "app": "edge", "action": "open_url",
            "arguments_json": '{"url": "https://open.spotify.com/search/Love Story"}'}))
        assert "spotify" in out.get("error", "")
        assert out.get("actions"), "and it is told what spotify can do instead"
        assert not mgr.pending_approvals(), "nothing was queued to run"
    finally:
        mgr.stop()


def test_a_wrong_action_name_comes_back_with_the_real_ones(fake, tmp_path):
    from agents.bootstrap import build_agent_manager
    from companion_manager import CompanionManager

    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True).start()
    companion = CompanionManager.__new__(CompanionManager)
    companion._agents = mgr
    companion._realtime = None
    try:
        out = asyncio.run(companion._tool_desktop_action(
            {"app": "spotify", "action": "skip_forward_two"}))
        names = {a["action"] for a in out.get("actions", [])}
        assert "next" in names, "guessing wrong must not be a dead end"
    finally:
        mgr.stop()
