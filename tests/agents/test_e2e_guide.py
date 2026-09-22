"""The walkthrough: does a step ever actually finish?

These are the regression tests for the field report of 22 September, where
"show me how" produced a walkthrough that repeated itself, skipped to the
end, and pointed at the wrong thing. The log named the cause on almost every
step:

    step expectation already satisfied at step start — ignoring it

The planner is asked for the observable OUTCOME of a step, and it answered
with the app the user was already in ("process: chrome.exe"). verify_local
accepted a process that was merely RUNNING, so the step's completion signal
was true before the user moved, the watcher threw the whole expectation
away, and every step fell back to polling the vision model. Slow, expensive
and, when the vision model said "not done yet", stuck.
"""
from __future__ import annotations

import asyncio

import pytest

from ai.verification import (
    ADVANCE, ArmedExpectation, Expectation, Verdict, verify_local,
)


@pytest.fixture
def signals(monkeypatch):
    """Control what every signal reports, without touching Windows."""
    state: dict[str, float] = {}

    def fake(kind, value):
        return state.get(f"{kind}:{value}", 0.0), f"{kind}: {value}"

    import ai.verification as v
    monkeypatch.setattr(v, "check_signal", fake)
    return state


# ── the signal that was always already true ──────────────────────────────────

def test_an_app_that_is_merely_running_cannot_finish_a_step(monkeypatch):
    """The whole walkthrough happens inside one app. If "chrome.exe exists"
    counted as doing the step, every step was born finished."""
    import ai.verification as v
    monkeypatch.setattr(v, "active_process_name", lambda: "explorer.exe")
    monkeypatch.setattr(v, "process_running", lambda name: True)

    conf, detail = v._process_signal("chrome.exe")
    assert 0 < conf < ADVANCE, "running is a hint, never proof"
    assert "not in front" in detail

    monkeypatch.setattr(v, "active_process_name", lambda: "chrome.exe")
    conf, _ = v._process_signal("chrome.exe")
    assert conf >= ADVANCE, "but bringing it to the front IS the outcome"


def test_a_weak_signal_no_longer_hides_a_strong_one_behind_it(signals):
    """verify_local returned on the FIRST satisfied signal, and process is
    checked first — so a half-true process reading masked a real one."""
    signals["process:chrome.exe"] = 0.5
    signals["url_contains:youtube"] = 0.95
    verdict = verify_local(Expectation(process="chrome.exe",
                                       url_contains="youtube"))
    assert verdict.done and verdict.method == "url_contains"


# ── arming ───────────────────────────────────────────────────────────────────

def test_only_the_signals_that_were_already_true_are_muted(signals):
    """The old code discarded the entire expectation the moment any one
    signal was already true. That is why four steps in five had nothing left
    to watch."""
    signals["process:chrome.exe"] = 0.95        # already true: user is in Chrome
    signals["control_appears:Downloads"] = 0.0  # the actual outcome

    armed = ArmedExpectation(Expectation(process="chrome.exe",
                                         control_appears="Downloads"))
    kept = armed.arm()
    assert kept == ["control_appears"]
    assert armed.armed_any

    assert not armed().done, "nothing has happened yet"
    signals["control_appears:Downloads"] = 0.90
    verdict = armed()
    assert verdict.done and verdict.method == "control_appears"


def test_a_muted_signal_comes_back_when_it_goes_false(signals):
    """"Close the panel, then reopen it" — the second step's signal starts
    true. Once the user closes it, it becomes a real transition again."""
    signals["control_appears:Sidebar"] = 0.90
    armed = ArmedExpectation(Expectation(control_appears="Sidebar"))
    assert armed.arm() == [], "nothing to watch while it is already there"
    assert not armed().done

    signals["control_appears:Sidebar"] = 0.0     # the user closed it
    assert not armed().done
    signals["control_appears:Sidebar"] = 0.90    # and opened it again
    assert armed().done, "that is the step, and it must be caught"


def test_an_expectation_with_nothing_left_says_so(signals):
    signals["process:chrome.exe"] = 0.95
    armed = ArmedExpectation(Expectation(process="chrome.exe"))
    assert armed.arm() == []
    assert not armed.armed_any


# ── the watcher uses it ──────────────────────────────────────────────────────

def test_the_watcher_keeps_the_signals_that_are_still_useful(signals, monkeypatch):
    from tutor_features import step_watch

    async def scenario():
        signals["process:chrome.exe"] = 0.95
        signals["url_contains:youtube.com"] = 0.0
        armed = ArmedExpectation(Expectation(process="chrome.exe",
                                             url_contains="youtube.com"))
        w = step_watch.StepWatcher("Go to YouTube", judge=_never,
                                   local_check=armed)
        monkeypatch.setattr(step_watch, "grab_screen", lambda *a, **k: ("", None))
        monkeypatch.setattr(step_watch, "read_cheap", step_watch.Fingerprint)
        await w.begin()
        assert w._local_check is armed, "the useful half must survive"
        assert w._local_armed

        w._local_at = -999
        assert await w.poll() is None
        signals["url_contains:youtube.com"] = 0.95
        w._local_at = -999
        assert await w.poll() is True
        assert "url_contains" in w.done_by

    asyncio.run(scenario())


async def _never(instruction, before, after):
    return None


# ── "next" three times is still one step ─────────────────────────────────────

def test_a_repeated_next_does_not_walk_off_the_end_of_the_guide():
    """The live model fired guide_step three times in 1.4 seconds. On a
    two-step walkthrough that ended it before the user had done anything."""
    from tutor_features.guide import GuideRunner

    runner = GuideRunner.__new__(GuideRunner)
    runner._repeat_guard_s = 1.5
    runner._last_command = None
    runner._last_command_at = 0.0
    runner._pending = None
    runner._loop = None
    runner._wake = asyncio.Event()

    assert runner.request("next") is True
    assert runner.request("next") is False
    assert runner.request("next") is False
    assert runner._pending == "next", "exactly one advance is queued"

    assert runner.request("stop") is True, "a different command is never a repeat"


# ── pointing ─────────────────────────────────────────────────────────────────

def test_ocr_confidence_arrives_as_a_string_and_must_still_work():
    """RapidOCR 1.2.x returns '0.84…', not 0.84. Comparing that to a float
    raised TypeError on the first detection, so the OCR tier never returned
    anything and every miss went to the vision model instead."""
    from ai import hybrid_pointer

    class FakeOCR:
        def __call__(self, img):
            return [[[[30.0, 81.0], [81.0, 82.0], [81.0, 92.0], [30.0, 92.0]],
                     "Exte nsions", "0.8488673567771912"]], None

    hybrid_pointer._ocr_engine = FakeOCR()
    try:
        import numpy as np
        from PIL import Image
        target = hybrid_pointer._find_via_ocr(
            "Extensions", pil_image=Image.new("RGB", (200, 120)))
    finally:
        hybrid_pointer._ocr_engine = None

    assert target is not None, "the OCR tier has to come back with the match"
    assert target.source == "ocr"


def test_an_exact_name_on_something_unclickable_is_not_the_answer():
    from ai.hybrid_pointer import _is_actionable

    assert _is_actionable("HyperlinkControl")
    assert _is_actionable("ButtonControl")
    assert not _is_actionable("TextControl"), \
        "a status-bar readout named google.com is not the link"
    assert not _is_actionable("PaneControl")


# ── the signal the planner never thinks of ───────────────────────────────────

def test_the_next_step_s_target_becomes_this_step_s_finish_line():
    """"Open the File menu" is done the moment "Save As" is on screen. The
    guide knows the whole plan, so it does not need the planner to say so."""
    from companion_manager import CompanionManager

    mgr = CompanionManager.__new__(CompanionManager)
    mgr._dpi_scale = lambda: 1.0

    step = type("S", (), {"instruction": "Open the File menu",
                          "expected": Expectation(window_title="Notepad")})()
    watcher = mgr._make_step_watcher(step, next_target="Save As")
    assert watcher._local_check.expectation.control_appears == "Save As"

    already = type("S", (), {"instruction": "x",
                             "expected": Expectation(control_appears="Print")})()
    kept = mgr._make_step_watcher(already, next_target="Save As")
    assert kept._local_check.expectation.control_appears == "Print", \
        "a signal the planner did give is never overwritten"


# ── two voices at once ───────────────────────────────────────────────────────

def test_she_is_still_speaking_while_her_voice_is_queued():
    """Gemini streams a seven-second answer in about two, so turn_complete
    lands while five seconds of it is still in the player. The walkthrough
    asks "is she busy?" before speaking a step, got False, and spoke over
    her — the user hears the same instruction from two voices."""
    import ai.gemini_realtime as rt

    conv = rt.RealtimeConversation.__new__(rt.RealtimeConversation)
    conv._speaking = True
    conv._player = type("P", (), {"queued_seconds": 4.5})()
    assert conv.speaking

    conv._speaking = False              # the server has finished generating
    assert conv.speaking, "but the speakers have not finished"

    conv._player = type("P", (), {"queued_seconds": 0.0})()
    assert not conv.speaking, "and when they have, the guide may talk"


def test_a_player_that_cannot_be_read_never_blocks_the_guide():
    import ai.gemini_realtime as rt

    class Broken:
        @property
        def queued_seconds(self):
            raise RuntimeError("device gone")

    conv = rt.RealtimeConversation.__new__(rt.RealtimeConversation)
    conv._speaking = False
    conv._player = Broken()
    assert conv.speaking is False
