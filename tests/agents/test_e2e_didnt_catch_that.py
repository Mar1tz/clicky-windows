"""FS-13 (docs/FAST_AND_RIGHT.md): "I didn't catch that" in seconds, not 22.

5 Oct, 11:07: a 1.1 s question got nothing back, and Tora sat on "Thinking..."
for 22 s before saying so. Seven presses followed in ten seconds - each cancel
restarting the session - and none of them was answered.
"""
from __future__ import annotations

import asyncio
import time

import companion_manager as cm


def _manager(heard=(), tool_running=False):
    m = cm.CompanionManager.__new__(cm.CompanionManager)
    m._state = cm.AppState.THINKING
    m._turn_seq = 1
    m._heard_buf = list(heard)
    m._tools_in_flight = 1 if tool_running else 0
    m._tool_done_at = 0.0
    m._NOTHING_HEARD_S, m._HEARD_NO_ANSWER_S, m._TURN_WATCHDOG_MAX_S = 0.5, 1.0, 2.0   # the same rules, faster
    m.said, m.dropped = [], []
    m._activity = lambda what: None

    def emit(state):
        m._state = state
    m._emit_state = emit
    m._drop_realtime = lambda: m.dropped.append(1)

    async def reply(text):
        m.said.append((round(time.monotonic() - m.t0, 1), text))
    m._reply_local = reply
    return m


def _run(m):
    m.t0 = time.monotonic()
    asyncio.run(m._realtime_turn_watchdog(1))
    return m


def test_no_words_heard_is_said_quickly_and_keeps_the_session():
    m = _run(_manager())
    assert m.said and m.said[0][0] < 0.9, "after NOTHING_HEARD_S, not the long wait"
    assert "didn't catch that" in m.said[0][1]
    assert m.dropped == [], "the session did nothing wrong"


def test_heard_but_unanswered_waits_longer_and_replaces_the_session():
    m = _run(_manager(heard=["what's the weather"]))
    assert 0.9 <= m.said[0][0] < 1.5
    assert m.dropped == [1]


def test_a_running_tool_still_gets_its_time():
    m = _run(_manager(heard=["best move?"], tool_running=True))
    assert m.said[0][0] >= 1.9, "only the hard ceiling ends a turn with a tool at work"


def test_an_answer_ends_the_watch():
    m = _manager()
    m.t0 = time.monotonic()

    async def go():
        watch = asyncio.ensure_future(m._realtime_turn_watchdog(1))
        await asyncio.sleep(0.2)
        m._state = cm.AppState.SPEAKING                     # she started answering
        await watch
    asyncio.run(go())
    assert m.said == []


def test_the_real_timings():
    assert cm.CompanionManager._NOTHING_HEARD_S == 2.5 and cm.CompanionManager._HEARD_NO_ANSWER_S == 12.0
    assert cm.CompanionManager._TURN_WATCHDOG_MAX_S == 75.0


# -- FX-01 (18 Oct 2026): an unanswered question is asked the slow way ----------------------------------------
#
# 18 "Sorry - I didn't catch that" in a fortnight; on 16 Oct a session that
# had gone deaf heard nothing of five questions in a row.

QUESTION = b"q" * 32000                                     # a second of it


def _rescuing(heard=(), tool_running=False):
    m = _manager(heard, tool_running)
    m.asked = []

    async def cascade(pcm):
        m.asked.append((round(time.monotonic() - m.t0, 1), pcm))
    m._cascade_turn = cascade
    return m


def _run_with(m, pcm=QUESTION):
    m.t0 = time.monotonic()
    asyncio.run(m._realtime_turn_watchdog(1, pcm))
    return m


def test_a_question_nobody_heard_is_asked_the_slow_way():
    cm.net_quality.reset()
    m = _run_with(_rescuing())
    assert m.asked and m.asked[0][1] == QUESTION and m.asked[0][0] < 0.9
    assert m.said == [], "an answer, not an apology"
    assert m.dropped == [1], "a session that heard nothing of it is not kept"


def test_heard_and_unanswered_is_asked_the_slow_way_too():
    m = _run_with(_rescuing(heard=["what's the weather"]))
    assert m.asked and 0.9 <= m.asked[0][0] < 1.5 and m.said == []
    assert m.dropped == [1]


def test_a_weak_connection_gets_longer_before_the_slow_way(monkeypatch):
    monkeypatch.setattr(cm.net_quality, "slow", lambda: True)
    m = _run_with(_rescuing())
    assert 0.9 <= m.asked[0][0] < 1.5, "twice NOTHING_HEARD_S: the first word is slower to come back"


def test_a_tool_that_runs_out_the_ceiling_is_not_asked_again():
    m = _run_with(_rescuing(heard=["best move?"], tool_running=True))
    assert m.asked == [] and "didn't catch that" in m.said[0][1]


def test_a_press_with_nothing_kept_still_says_so():
    m = _run_with(_rescuing(), pcm=b"")
    assert m.asked == [] and "didn't catch that" in m.said[0][1]
