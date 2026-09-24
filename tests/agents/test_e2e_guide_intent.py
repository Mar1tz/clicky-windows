"""GW-13: a question is not a "next".

Mid-walkthrough, almost everything a person says is not a command. When
the live model reads "what does this button do?" as "next", the guide moves
on without them. The model's reading can't be unit-tested, but the tool it
calls can check that reading against the transcript of what was actually
said — and these tests pin that check.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from tutor_features.guide_intent import guide_intent


@pytest.mark.parametrize("said, intent", [
    # real commands, generously
    ("next", "next"), ("okay next", "next"), ("done", "next"), ("I did it", "next"),
    ("keep going", "next"), ("what's next", "next"), ("necks", "next"),
    ("go back", "back"), ("previous", "back"),
    ("say that again", "repeat"), ("can you repeat that", "repeat"), ("pardon", "repeat"),
    ("stop", "stop"), ("cancel", "stop"), ("never mind", "stop"),
    ("where is it", "where"), ("I can't find it", "where"),
    ("where is the next button", "where"),          # asking where, not moving
    # acknowledgements
    ("okay", "ack"), ("got it", "ack"), ("alright", "ack"),
    # everything else: questions and remarks
    ("what does this button do?", None), ("is this the right one", None),
    ("why is it grey", None), ("hmm", None), ("", None),
    # negation takes a move or a stop off the table
    ("I am not done yet", None), ("wait", None), ("don't stop", None),
    ("hang on", None),
])
def test_what_was_said(said, intent):
    assert guide_intent(said) == intent


def test_negation_does_not_eat_a_repeat():
    assert guide_intent("I didn't hear that, say it again") == "repeat"


def test_a_word_inside_another_word_is_not_a_command():
    assert guide_intent("open nextcloud") is None


# ── the tool refuses a move nobody asked for ─────────────────────────────────

def _guide(heard: str, steps=3):
    from companion_manager import CompanionManager
    from tutor_features.guide import GuideRunner

    async def speak(i):
        pass

    async def check(s):
        return None

    async def say(t):
        pass

    plan = SimpleNamespace(goal="g", steps=[SimpleNamespace(instruction=f"s{i}", target=f"T{i}")
                                            for i in range(steps)])
    runner = GuideRunner(plan, speak_step=speak, check=check, say=say,
                         is_busy=lambda: False, on_stop=lambda: None)
    runner._idx = 0
    runner._task = SimpleNamespace(done=lambda: False)
    mgr = CompanionManager.__new__(CompanionManager)
    mgr._guide_runner = runner
    mgr._lesson_plan = plan
    mgr._request_serial, mgr._guide_moved_for = 1, -1
    mgr._heard_buf = [heard] if heard else []
    mgr._guide_stats = None
    pointed = []

    async def point(idx, step):
        pointed.append(step.target)

    mgr._point_step_target = point
    pending = []
    mgr._submit = pending.append
    mgr._pending_coros = pending
    return mgr, runner, pointed


def test_a_question_read_as_next_does_not_move_the_guide():
    mgr, runner, _ = _guide("what does this button do?")
    out = asyncio.run(mgr._tool_guide_step({"action": "next"}))
    assert out["moved"] is False and "stays exactly where it is" in out["note"]
    assert runner._pending is None, "nothing was queued"


def test_a_real_next_moves_it():
    mgr, runner, _ = _guide("okay I did it")
    out = asyncio.run(mgr._tool_guide_step({"action": "next"}))
    assert "moved" not in out and runner._pending == "next"


def test_an_acknowledgement_is_not_a_next():
    mgr, runner, _ = _guide("okay")
    out = asyncio.run(mgr._tool_guide_step({"action": "next"}))
    assert out["moved"] is False and "moves on by itself" in out["note"]


def test_where_is_it_points_again_instead_of_moving():
    mgr, runner, pointed = _guide("where is it")

    async def run():
        result = await mgr._tool_guide_step({"action": "next"})
        for coro in mgr._pending_coros:
            await coro
        return result

    out = asyncio.run(run())
    assert out["moved"] is False and "WHERE" in out["note"]
    assert pointed == ["T0"], "the arrow goes back to the current step's target"
    assert runner._pending is None


def test_the_model_picking_the_wrong_command_is_corrected():
    mgr, runner, _ = _guide("go back")
    out = asyncio.run(mgr._tool_guide_step({"action": "next"}))
    assert out["moved"] is False and "'back'" in out["note"]


def test_stop_is_never_second_guessed():
    mgr, runner, _ = _guide("don't stop")          # even when the words disagree
    asyncio.run(mgr._tool_guide_step({"action": "stop"}))
    assert runner._pending == "stop", "a refused stop is the worse mistake"


def test_no_transcript_is_not_a_reason_to_refuse():
    """The transcript can arrive after the tool call. Blocking on missing
    evidence would make "next" unreliable exactly when it must work."""
    mgr, runner, _ = _guide("")
    asyncio.run(mgr._tool_guide_step({"action": "next"}))
    assert runner._pending == "next"
