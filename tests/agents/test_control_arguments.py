"""Wave 1 of the desktop-control roadmap: an argument is never lost.

Field report, 23 September 2026:

    "I say, please reduce the audio. It went and tried doing it, but the
     audio actually increased."

The mechanism, which these tests exist to keep dead:

  * `volume(direction="up", steps=2)` — a default that performs an action;
  * `except ValueError: arguments = {}` in the manager, so a JSON parse
    failure ran the action on that default;
  * `args.pop(k)` in the registry, so an argument the action did not
    recognise was dropped and, again, the default ran;
  * a log line that printed "ok" for every one of those.

Together they turned "turn it down" into "turn it up". Every test below is
one of those four doors, nailed shut. See docs/agents/DESKTOP_CONTROL_ROADMAP.md
(DC-01, DC-02, DC-05, DC-07, DC-08, DC-09).
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from agents.control.args import BindError, bind, read_arguments
from agents.control.model import Action, Param, Risk, Tier
from agents.control.registry import AppControl


# ── the shape of the bug ─────────────────────────────────────────────────────

class FakeVolume:
    """One app with the action that misbehaved, wired to a spy."""
    app = "windows"
    display_name = "Windows"
    aliases = ("system",)

    def __init__(self) -> None:
        self.taps: list[tuple[str, int]] = []

    def available(self) -> bool:
        return True

    def actions(self):
        async def volume(direction: str, steps: int = 2) -> str:
            self.taps.append((direction, steps))
            return f"turned the volume {direction}"

        return [Action(self.app, "volume", "Turn the volume up, down or mute.",
                       volume, Tier.SYSTEM, Risk.REVERSIBLE,
                       (Param("direction", "up, down or mute", required=True,
                              choices=("up", "down", "mute")),
                        Param("steps", "How many notches", type="integer")))]


@pytest.fixture
def volume_app():
    app = FakeVolume()
    return app, AppControl([app])


def run(coro):
    return asyncio.run(coro)


# ── DC-09: a lost argument must never become the opposite action ─────────────

def test_reduce_the_volume_never_increases_it(volume_app):
    """The whole reason this file exists."""
    app, control = volume_app
    out = run(control.run("windows", "volume", {"direction": "down"}))
    assert out.ok and app.taps == [("down", 2)]


def test_a_missing_direction_is_refused_not_guessed(volume_app):
    app, control = volume_app
    out = run(control.run("windows", "volume", {}))
    assert not out.ok
    assert "direction" in out.message
    assert app.taps == [], "nothing may be tapped when we do not know which way"


def test_no_action_may_default_its_direction():
    """A parameter with a fixed set of values decides WHAT the action does.
    Give it a default and a dropped argument silently picks one."""
    import inspect

    from agents.control import control as live_control
    offenders = []
    for action in live_control().actions():
        signature = inspect.signature(action.run)
        for param in action.params:
            if not param.choices or param.modifier:
                continue          # a modifier changes HOW, not WHAT
            arg = signature.parameters.get(param.name)
            if arg is not None and arg.default is not inspect.Parameter.empty:
                offenders.append(f"{action.key}({param.name}={arg.default!r})")
            if not param.required:
                offenders.append(f"{action.key}: {param.name} is not required")
    assert not offenders, "a choice must be asked for, never assumed: " + "; ".join(offenders)


# ── DC-07: an unrecognised name is an error, not a shrug ─────────────────────

def test_a_misnamed_argument_is_refused_with_a_suggestion(volume_app):
    app, control = volume_app
    out = run(control.run("windows", "volume", {"directoin": "down"}))
    assert not out.ok
    assert "directoin" in out.message and "direction" in out.message
    assert app.taps == []


def test_an_unknown_argument_lists_what_the_action_takes(volume_app):
    _app, control = volume_app
    out = run(control.run("windows", "volume", {"loudness": "down"}))
    assert "direction" in out.message and "steps" in out.message
    assert out.data.get("expected"), "the model needs the schema to correct itself"


# ── DC-08: choices are enforced, and the words people say are understood ─────

@pytest.mark.parametrize("said", ["down", "DOWN", "lower", "quieter", "reduce",
                                  "softer", "decrease", "down a bit"])
def test_every_way_of_saying_down_means_down(volume_app, said):
    app, control = volume_app
    out = run(control.run("windows", "volume", {"direction": said}))
    assert out.ok and app.taps[-1][0] == "down", f"{said!r} should turn it down"


@pytest.mark.parametrize("said", ["up", "louder", "raise", "increase", "more"])
def test_every_way_of_saying_up_means_up(volume_app, said):
    app, control = volume_app
    out = run(control.run("windows", "volume", {"direction": said}))
    assert out.ok and app.taps[-1][0] == "up"


def test_a_value_that_is_not_a_choice_is_refused(volume_app):
    app, control = volume_app
    out = run(control.run("windows", "volume", {"direction": "sideways"}))
    assert not out.ok and "up, down, mute" in out.message
    assert app.taps == []


def test_a_number_arrives_as_a_number(volume_app):
    app, control = volume_app
    run(control.run("windows", "volume", {"direction": "down", "steps": "4"}))
    assert app.taps[-1] == ("down", 4)


def test_a_number_that_is_not_one_is_refused(volume_app):
    app, control = volume_app
    out = run(control.run("windows", "volume", {"direction": "down", "steps": "loads"}))
    assert not out.ok and "number" in out.message
    assert app.taps == []


# ── DC-05: the model's arguments survive its own bad JSON ────────────────────

@pytest.mark.parametrize("raw, expected", [
    ('{"direction": "down"}', {"direction": "down"}),
    ("{'direction': 'down'}", {"direction": "down"}),                  # python quotes
    ('sure — {"direction": "down"}', {"direction": "down"}),           # prose around it
    ("direction=down", {"direction": "down"}),
    ("direction: down, steps: 3", {"direction": "down", "steps": "3"}),
    ("", {}),
])
def test_the_arguments_the_model_meant_are_recovered(raw, expected):
    parsed, _how = read_arguments(raw)
    assert parsed == expected


def test_a_bare_value_lands_on_the_only_required_parameter():
    parsed, how = read_arguments("down")
    assert "bare" in how
    params = (Param("direction", "", required=True, choices=("up", "down")),
              Param("steps", "", type="integer"))
    assert bind("windows.volume", params, parsed) == {"direction": "down"}


def test_a_bare_value_is_refused_when_it_could_be_either():
    parsed, _ = read_arguments("down")
    params = (Param("a", "", required=True), Param("b", "", required=True))
    with pytest.raises(BindError) as caught:
        bind("x.y", params, parsed)
    assert "does not say which argument" in str(caught.value)


def test_unreadable_arguments_are_reported_not_swallowed():
    parsed, why = read_arguments("turn it down please, thanks")
    assert parsed is None and why


# ── DC-01: the log stops claiming everything worked ──────────────────────────

def test_a_failed_tool_call_is_not_logged_as_ok():
    """desktop_action reports trouble as ok=False with a note and no
    "error" key, so the old check printed "ok" for every failure in the
    field log. There was no record that anything had gone wrong."""
    from ai.gemini_realtime import _outcome_of

    assert _outcome_of({"ok": True, "result": "turned the volume down"}) == "ok"
    assert _outcome_of({"error": "boom"}).startswith("error")
    assert _outcome_of({"ok": False, "result": "volume needs direction"}) \
        .startswith("failed")
    assert _outcome_of({"ok": False, "note": "The user did not approve it."}) \
        .startswith("denied")


# ── DC-02: the control layer says what it did ────────────────────────────────

def test_a_run_is_recorded_in_the_log(volume_app, caplog):
    _app, control = volume_app
    with caplog.at_level(logging.INFO, logger="agents.control"):
        run(control.run("windows", "volume", {"direction": "down"}))
    line = " ".join(r.getMessage() for r in caplog.records)
    assert "windows.volume" in line and "down" in line and "tier=" in line


def test_a_refused_argument_is_recorded_in_the_log(volume_app, caplog):
    _app, control = volume_app
    with caplog.at_level(logging.WARNING, logger="agents.control"):
        run(control.run("windows", "volume", {"direction": "sideways"}))
    assert any("refused its arguments" in r.getMessage() for r in caplog.records)


# ── DC-24: "ok" has to mean it happened, not that the call returned ─────────

class FakeVerified:
    """An app whose action can be made to take, or not."""
    app = "lamp"
    display_name = "Lamp"
    aliases = ()

    def __init__(self) -> None:
        self.state = "off"
        self.takes = True

    def available(self) -> bool:
        return True

    def actions(self):
        from agents.control.model import Unverified

        async def switch(to: str) -> str:
            if self.takes:
                self.state = to
            return f"asked the lamp to go {to}"

        async def verify_switch(to: str):
            if self.state != to:
                raise Unverified(f"the lamp is still {self.state}")
            return f"the lamp is {self.state}"

        async def blind(to: str) -> str:
            return f"asked the lamp to go {to}"

        async def verify_blind(to: str):
            return None                      # genuinely cannot tell

        return [
            Action(self.app, "switch", "Switch the lamp.", switch, Tier.API,
                   Risk.REVERSIBLE, (Param("to", "on or off", required=True,
                                           choices=("on", "off")),),
                   verify=verify_switch),
            Action(self.app, "blind", "Switch it with no way to check.", blind,
                   Tier.API, Risk.REVERSIBLE,
                   (Param("to", "on or off", required=True, choices=("on", "off")),),
                   verify=verify_blind),
        ]


def test_an_action_that_did_not_take_is_reported_as_failed():
    """A media key tap used to "succeed" whether or not anything heard it."""
    app = FakeVerified()
    control = AppControl([app])
    app.takes = False
    out = run(control.run("lamp", "switch", {"to": "on"}))
    assert not out.ok and "still off" in out.message
    assert out.data.get("verified") is False


def test_a_confirmed_action_reports_what_was_observed():
    """The spoken confirmation should describe the world, not the attempt."""
    app = FakeVerified()
    out = run(AppControl([app]).run("lamp", "switch", {"to": "on"}))
    assert out.ok and out.message == "the lamp is on"
    assert out.data.get("verified") is True


def test_not_being_able_to_check_is_not_a_failure():
    """Honest uncertainty: it still ran, we just cannot prove it."""
    out = run(AppControl([FakeVerified()]).run("lamp", "blind", {"to": "on"}))
    assert out.ok and out.data.get("verified") is False
    assert "asked the lamp" in out.message


def test_a_broken_check_never_fails_a_good_action():
    app = FakeVerified()
    actions = app.actions()

    async def explode(**_kw):
        raise RuntimeError("the sensor fell off")

    actions[0].verify = explode
    app.actions = lambda: actions
    out = run(AppControl([app]).run("lamp", "switch", {"to": "on"}))
    assert out.ok, "a check that breaks is not the action failing"


def test_the_volume_is_read_back_from_the_device():
    """The real reason DC-24 exists: "turn it down" can now be proven."""
    from agents.control import audio
    from agents.control.apps import windows_os

    if not audio.available():
        pytest.skip("no audio endpoint on this machine")
    before = audio.level()
    try:
        control = AppControl([windows_os.WindowsOS()])
        out = run(control.run("windows", "volume", {"direction": "down", "steps": 1}))
        assert out.ok and out.data.get("verified") is True
        assert "percent" in out.message
        assert audio.level() < before, "down must mean down"
    finally:
        audio.set_level(before)


def test_the_volume_can_be_set_to_a_number():
    from agents.control import audio
    from agents.control.apps import windows_os

    if not audio.available():
        pytest.skip("no audio endpoint on this machine")
    before = audio.level()
    try:
        control = AppControl([windows_os.WindowsOS()])
        out = run(control.run("windows", "volume",
                              {"direction": "down", "to_percent": 20}))
        assert out.ok and abs(audio.level() - 0.20) < 0.03
        assert "20 percent" in out.message
    finally:
        audio.set_level(before)


# ── DC-24 continued: proving a transport change needs a BEFORE ──────────────

class FakePlayer:
    """Spotify, as far as a window title is concerned."""
    app = "player"
    display_name = "Player"
    aliases = ()

    def __init__(self) -> None:
        self.track = "Norwegian Wood"
        self.moves = True

    def available(self) -> bool:
        return True

    def actions(self):
        from agents.control.system import NowPlaying

        def now():
            return NowPlaying(app="Player", title=self.track, playing=True)

        async def skip() -> str:
            if self.moves:
                self.track = "Michelle"
            return "skipped to the next track"

        async def snapshot():
            return now()

        async def verify(before=None):
            after = now()
            if before is not None and after.title != before.title:
                return after.describe()
            return None

        return [Action(self.app, "next", "Next track.", skip, Tier.SYSTEM,
                       Risk.REVERSIBLE, (), snapshot=snapshot, verify=verify)]


def test_next_track_is_proven_by_the_track_changing():
    player = FakePlayer()
    out = run(AppControl([player]).run("player", "next"))
    assert out.ok and out.data.get("verified") is True
    assert "Michelle" in out.message, "it says what is playing, not what it pressed"


def test_a_media_key_nothing_listened_to_is_not_a_success():
    """The honest half: a tap that changed nothing cannot claim it did."""
    player = FakePlayer()
    player.moves = False
    out = run(AppControl([player]).run("player", "next"))
    assert out.ok, "the key was pressed; we just cannot prove it landed"
    assert out.data.get("verified") is False
    assert "skipped" in out.message


def test_a_snapshot_that_breaks_does_not_stop_the_action():
    player = FakePlayer()
    actions = player.actions()

    async def explode():
        raise RuntimeError("no window list")

    actions[0].snapshot = explode
    player.actions = lambda: actions
    out = run(AppControl([player]).run("player", "next"))
    assert out.ok and player.track == "Michelle"


# ── DC-16: a standing yes outlives the session that gave it ─────────────────

def test_always_allow_survives_a_restart(tmp_path, monkeypatch):
    from agents.bootstrap import build_agent_manager

    monkeypatch.setenv("TORA_GRANTS_FILE", str(tmp_path / "grants.json"))
    first = build_agent_manager(workspace_root=str(tmp_path), in_memory=True,
                                offline=True)
    first.allow_desktop_action("app_action:spotify.next")
    assert first.desktop_granted("app_action:spotify.next")

    second = build_agent_manager(workspace_root=str(tmp_path), in_memory=True,
                                 offline=True)
    assert second.desktop_granted("app_action:spotify.next"), \
        "'always' has to mean more than 'until you restart'"
    assert second.desktop_grants() == ["app_action:spotify.next"]


def test_a_standing_yes_can_be_taken_back(tmp_path, monkeypatch):
    from agents.bootstrap import build_agent_manager

    monkeypatch.setenv("TORA_GRANTS_FILE", str(tmp_path / "grants.json"))
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    mgr.allow_desktop_action("app_action:spotify.next")
    assert mgr.revoke_desktop_action("app_action:spotify.next")
    assert not mgr.revoke_desktop_action("app_action:spotify.next")

    after = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    assert not after.desktop_granted("app_action:spotify.next")


def test_a_corrupt_grants_file_means_ask_me_again(tmp_path, monkeypatch):
    path = tmp_path / "grants.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("TORA_GRANTS_FILE", str(path))
    from agents.bootstrap import build_agent_manager
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    assert mgr.desktop_grants() == [], "failing open would grant what was never granted"
