"""GW-07: every step has a finish line, and step one's is not already crossed.

The log said it on the last step of the VS Code walkthrough: "every
expectation signal (control_gone) was already true at step start". The
planner describes a step's outcome, and sometimes that outcome is already
the state of the screen — which cannot tell "the user did it" from "it was
always like that", so the signal is useless and the step falls back to
waiting for a click or asking the vision model.

Two fixes, both pinned here:

  * step one — the only step whose starting screen is known when the plan
    arrives — has its provably-true signals dropped before it is spoken;
  * a step left with nothing gets a last resort: its own target
    disappearing, from the same window. Clicking a menu item closes the
    menu; confirming a dialog closes it.
"""
from __future__ import annotations

import asyncio

import pytest

import ai.verification as verification
from ai.gemini_planner import Guide, Step, repair_first_step
from ai.verification import ADVANCE, Expectation, TargetGone


@pytest.fixture
def screen(monkeypatch):
    """A fake foreground window: its title and which controls exist."""
    state = {"title": "Visual Studio Code", "present": {"Check for Updates..."}}
    from ai import guide_state
    monkeypatch.setattr(guide_state, "control_exists",
                        lambda name, **kw: name in state["present"])
    monkeypatch.setattr(verification, "active_window_title", lambda: state["title"])
    return state


# ── the last-resort finish line ──────────────────────────────────────────────

def test_the_target_disappearing_finishes_the_step(screen):
    gone = TargetGone("Check for Updates...")
    assert gone.arm() == ["target_gone"]
    assert not gone().done, "still there"
    screen["present"].clear()                     # the menu closed
    verdict = gone()
    assert verdict.done and verdict.method == "target_gone"


def test_switching_to_another_app_is_not_the_target_disappearing(screen):
    """control_exists reads the FOREGROUND window, so without this guard
    Alt-Tab would look exactly like the step being done."""
    gone = TargetGone("Check for Updates...")
    gone.arm()
    screen["title"] = "Spotify Premium"
    screen["present"].clear()
    assert not gone().done


def test_a_target_that_is_not_on_screen_never_arms(screen):
    """Then "gone" is not a transition, just a name the planner got wrong."""
    gone = TargetGone("Some Button The Planner Imagined")
    assert gone.arm() == []
    assert not gone().done


def test_no_target_means_no_fallback(screen):
    assert TargetGone("").arm() == []


# ── the watcher reaches for it only when it has to ───────────────────────────

def _watcher(local, fallback, monkeypatch):
    from tutor_features import step_watch
    monkeypatch.setattr(step_watch, "grab_screen", lambda *a, **k: ("", None))
    monkeypatch.setattr(step_watch, "read_cheap", step_watch.Fingerprint)
    return step_watch.StepWatcher("Click Check for Updates", judge=_never,
                                  local_check=local, fallback_check=fallback)


async def _never(*_a):
    return None


def test_when_every_planner_signal_is_stale_the_fallback_takes_over(screen, monkeypatch):
    signals = {"control_gone:Help menu": 0.85}          # already true: stale
    monkeypatch.setattr(verification, "check_signal",
                        lambda kind, value: (signals.get(f"{kind}:{value}", 0.0), kind))
    armed = verification.ArmedExpectation(Expectation(control_gone="Help menu"))
    fallback = TargetGone("Check for Updates...")
    w = _watcher(armed, fallback, monkeypatch)

    async def scenario():
        await w.begin()
        assert w._local_check is fallback, "nothing of the planner's survived arming"
        w._local_at = -999
        assert await w.poll() is None
        screen["present"].clear()
        w._local_at = -999
        return await w.poll()

    assert asyncio.run(scenario()) is True
    assert "target_gone" in w.done_by


def test_a_step_with_no_signal_at_all_uses_the_fallback(screen, monkeypatch):
    fallback = TargetGone("Check for Updates...")
    w = _watcher(None, fallback, monkeypatch)
    asyncio.run(w.begin())
    assert w._local_check is fallback and w._local_armed


def test_a_live_planner_signal_is_preferred_to_the_fallback(screen, monkeypatch):
    monkeypatch.setattr(verification, "check_signal", lambda kind, value: (0.0, kind))
    armed = verification.ArmedExpectation(Expectation(url_contains="updates"))
    w = _watcher(armed, TargetGone("Check for Updates..."), monkeypatch)
    asyncio.run(w.begin())
    assert w._local_check is armed, "the planner's own, still-false signal wins"


def test_with_nothing_to_arm_the_watcher_still_watches_clicks_and_the_screen(screen, monkeypatch):
    w = _watcher(None, TargetGone("Not On Screen"), monkeypatch)
    asyncio.run(w.begin())
    assert w._local_check is None and not w._local_armed


# ── step one is repaired before it is spoken ─────────────────────────────────

def _plan(**first):
    return Guide(goal="g", steps=[Step(instruction="do it", expected=Expectation(**first)),
                                  Step(instruction="then this")])


def test_a_control_already_on_screen_is_not_step_ones_outcome():
    guide = _plan(control_appears="Help", url_contains="update")
    dropped = repair_first_step(guide, ["Help", "File"], "Visual Studio Code")
    assert dropped == ["control_appears=Help"]
    assert guide.steps[0].expected.control_appears == ""
    assert guide.steps[0].expected.url_contains == "update", "a still-false signal stays"


def test_the_window_and_the_app_the_user_is_already_in_are_not_outcomes():
    guide = _plan(window_title="Visual Studio", process="Code.exe")
    dropped = repair_first_step(guide, [], "clicky - Visual Studio Code", "code.exe")
    assert set(dropped) == {"window_title=Visual Studio", "process=Code.exe"}


def test_absence_from_a_capped_list_is_not_proof_a_control_is_gone():
    """The list is capped and the walk bounded, so a name missing from it
    may simply not have been reached. control_gone is never 'repaired' on
    that evidence."""
    guide = _plan(control_gone="Welcome")
    assert repair_first_step(guide, ["Help", "File"], "VS Code") == []
    assert guide.steps[0].expected.control_gone == "Welcome"


def test_only_step_one_is_touched():
    guide = Guide(goal="g", steps=[
        Step(instruction="a", expected=Expectation(url_contains="x")),
        Step(instruction="b", expected=Expectation(control_appears="Help"))])
    repair_first_step(guide, ["Help"], "")
    assert guide.steps[1].expected.control_appears == "Help", \
        "later steps' screens are unknown now; they are armed when they start"
