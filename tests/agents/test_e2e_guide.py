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

def _runner(steps=3, speech_s=0.0, **kw):
    """A real GuideRunner over a fake guide, with nothing wired to Windows.
    `speech_s` is how long speaking a step takes - real steps take seconds."""
    from types import SimpleNamespace

    from tutor_features.guide import GuideRunner

    spoken: list[int] = []

    async def speak(idx):
        spoken.append(idx)
        if speech_s:
            await asyncio.sleep(speech_s)

    async def never_done(step):
        return None

    async def say(text):
        pass

    guide = SimpleNamespace(steps=[SimpleNamespace(instruction=f"step {i}")
                                   for i in range(steps)])
    runner = GuideRunner(guide, speak_step=speak, check=never_done, say=say,
                         is_busy=lambda: False, on_stop=lambda: None,
                         poll_s=0.02, present_delay_s=0.0, **kw)
    return runner, spoken


def test_a_repeated_next_does_not_walk_off_the_end_of_the_guide():
    """GW-02: one move per presentation. The 1.5 s timer this replaced let
    17 of 39 calls through on 23 September, because the model in a loop
    calls every ~0.6 s. "Has the new step been spoken yet" is a fact; a
    timer is a guess about the model's retry speed."""
    runner, _spoken = _runner()
    assert runner.request("next") is True
    for _ in range(40):
        assert runner.request("next") is False
    assert runner.request("back") is False, "any move waits for the new step"
    assert runner.request("stop") is True, "stop is never refused"


def test_a_burst_during_one_step_moves_the_live_runner_exactly_once():
    """The RUNNER's guarantee: however many moves arrive while a step is
    being presented, one lands. (A stream of moves spread over several
    steps' worth of speech is the MANAGER's job - see
    test_one_thing_said_is_one_move_however_often_the_model_calls - because
    only the manager knows where one user request ends and the next
    begins. Writing this test first with sleeps between the requests showed
    exactly that: Windows' timer made the "burst" 600 ms long, and the
    runner rightly allowed one move per spoken step.)"""
    runner, spoken = _runner(steps=5, speech_s=0.5)

    async def scenario():
        await runner.start()
        for _ in range(100):
            if spoken:
                break
            await asyncio.sleep(0.01)
        accepted = sum(runner.request("next") for _ in range(40))   # one burst
        await asyncio.sleep(1.3)          # step 0 finishes, step 1 is spoken
        runner.stop()
        await asyncio.sleep(0.1)
        return accepted

    accepted = asyncio.run(scenario())
    assert accepted == 1, f"one of forty accepted, got {accepted}"
    assert spoken[:2] == [0, 1], f"expected step 0 then step 1, got {spoken}"
    assert max(spoken) == 1, "the burst moved the guide exactly one step"


def test_a_move_is_allowed_again_once_the_new_step_has_been_spoken():
    runner, _ = _runner()

    async def scenario():
        assert runner.request("next") is True
        await runner._show(1)                 # the new step gets spoken
        assert runner.request("next") is True

    asyncio.run(scenario())


def test_the_model_is_told_where_the_guide_is_going_not_where_it_was():
    """Reading `index` straight after request() named the OLD step - "step 1
    of 2" right after the model said next - so it said next again."""
    runner, _ = _runner(steps=2)
    runner._idx = 0
    runner.request("next")
    assert runner.heading == 1, "moving to the second step"
    runner2, _ = _runner(steps=2)
    runner2._idx = 1
    runner2.request("next")
    assert runner2.heading == 2, "past the last step: the guide is finishing"


def test_the_tool_result_names_the_destination_and_says_stop_calling():
    from companion_manager import CompanionManager

    runner, _ = _runner(steps=2)
    runner._idx = 0
    runner._task = type("T", (), {"done": lambda self: False})()
    companion = CompanionManager.__new__(CompanionManager)
    companion._guide_runner = runner
    companion._request_serial, companion._guide_moved_for = 1, -1
    companion._heard_buf = []

    first = asyncio.run(companion._tool_guide_step({"action": "next"}))
    assert first["where"] == "now on step 2 of 2"
    assert "Do NOT call guide_step again" in first["note"]

    second = asyncio.run(companion._tool_guide_step({"action": "next"}))
    assert "already done" in second["note"].lower()
    assert second["ok"] is True, "a refused repeat must not read as a failure to retry"


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



def test_one_thing_said_is_one_move_however_often_the_model_calls():
    """The per-presentation guard is only as long as the speech, so a model
    looping every 0.6 s would still land a move every few seconds. The
    invariant the user means is: one "next" said, one step moved."""
    from companion_manager import CompanionManager

    runner, _ = _runner(steps=6)
    runner._idx = 0
    runner._task = type("T", (), {"done": lambda self: False})()
    companion = CompanionManager.__new__(CompanionManager)
    companion._guide_runner = runner
    companion._request_serial, companion._guide_moved_for = 1, -1
    companion._heard_buf = []

    moves = 0
    for _ in range(12):
        out = asyncio.run(companion._tool_guide_step({"action": "next"}))
        moves += "already done" not in out["note"].lower()
        runner._shown += 1          # as if each step had been spoken meanwhile
    assert moves == 1, f"one request, one move - got {moves}"

    companion._request_serial = 2   # the user speaks again
    out = asyncio.run(companion._tool_guide_step({"action": "next"}))
    assert "already done" not in out["note"].lower(), "a new request may move again"


def test_stop_always_works_even_after_a_move():
    from companion_manager import CompanionManager

    runner, _ = _runner()
    runner._idx = 0
    runner._task = type("T", (), {"done": lambda self: False})()
    companion = CompanionManager.__new__(CompanionManager)
    companion._guide_runner = runner
    companion._request_serial, companion._guide_moved_for = 1, -1
    companion._heard_buf = []
    asyncio.run(companion._tool_guide_step({"action": "next"}))
    out = asyncio.run(companion._tool_guide_step({"action": "stop"}))
    assert out["action"] == "stop" and runner._pending == "stop"
