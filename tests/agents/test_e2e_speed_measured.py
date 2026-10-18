"""Speed, measured (Wave 0 of the speed review, 17 Oct 2026).

Every number in that review was dug out of tora.log by hand. Now each
question leaves one timeline line from the key, the day keeps a scorecard,
and an agent job says how it went when it ends.
"""
from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace

import pytest

from ai import speed


@pytest.fixture(autouse=True)
def fresh_day(monkeypatch):
    monkeypatch.setattr(speed, "_today", speed._Day())
    monkeypatch.setattr(speed, "_turn", None)


def test_a_question_leaves_one_line_from_the_key(caplog):
    caplog.set_level(logging.INFO, logger="tora.speed")
    speed.begin()
    speed.mark("mic")
    speed.mark("release")
    speed.mark("sent")
    speed.mark("audio")
    speed.mark("audio")                          # the first time counts
    speed.finish("answered")
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("turn timeline"))
    for bit in ("live", "mic ", "held ", "sent +", "first word +", "answered"):
        assert bit in line
    assert speed._today.questions == 1 and len(speed._today.first_word) == 1


def test_a_question_cut_off_by_the_next_is_counted_as_interrupted():
    speed.begin()
    speed.mark("release")
    speed.begin()                                # pressed again while she answered
    assert speed._today.interrupted == 1


def test_nothing_said_is_not_a_question():
    speed.begin()
    speed.finish("nothing said")
    assert speed._today.questions == 0


def test_the_slow_route_and_unanswered_ones_are_counted():
    speed.begin()
    speed.route("slow")
    speed.finish("answered")
    speed.begin()
    speed.finish("unanswered")
    card = speed.scorecard()
    assert "2 questions" in card and "1 slow route" in card and "1 unanswered" in card


def test_the_scorecard_is_logged_every_so_many_questions(caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="tora.speed")
    monkeypatch.setattr(speed, "SCORECARD_EVERY", 2)
    for _ in range(2):
        speed.begin()
        speed.finish("answered")
    assert any(r.getMessage().startswith("speed today: 2 questions") for r in caplog.records)


def test_sessions_per_question_and_voice_ready(caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="tora.speed")
    monkeypatch.setattr(speed, "_voice_ready_told", False)
    speed.note_session()
    speed.note_session()
    speed.begin()
    speed.finish("answered")
    assert "2.0 sessions a question" in speed.scorecard()
    assert sum("voice ready" in r.getMessage() for r in caplog.records) == 1, "said once"


def test_the_loop_watcher_notes_how_late_it_woke(monkeypatch):
    monkeypatch.setattr(speed, "LAG_EVERY_S", 0.01)

    async def go():
        task = asyncio.ensure_future(speed.watch_loop(asyncio.sleep))
        await asyncio.sleep(0.05)
        time.sleep(0.08)                         # something blocks the loop
        await asyncio.sleep(0.03)
        task.cancel()
    asyncio.run(go())
    assert max(speed._today.lag_ms) >= 50


def test_process_cpu_time_is_read_from_windows():
    a = speed.process_cpu_seconds()
    assert a is None or a >= 0


def test_a_finished_job_says_how_it_went():
    from agents.manager.agent_manager import job_scorecard
    step = lambda status, calls=0, error="": SimpleNamespace(status=status, tool_calls=[1] * calls, error=error)  # noqa: E731
    task = SimpleNamespace(status=SimpleNamespace(value="failed"), title="Convert to PDF", worker_type="terminal",
                           created_at=time.time() - 85, error="",
                           steps=[step("done", 3), step("failed", 2, "verification failed: no file")])
    line = job_scorecard(task)
    assert line.startswith("failed, 'Convert to PDF' (terminal), 85s")
    assert "1 of 2 steps done, 5 tool calls - verification failed: no file" in line
