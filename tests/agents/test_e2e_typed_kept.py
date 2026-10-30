"""A question typed with Ctrl, Ctrl is kept, as a spoken one is (31 Oct 2026).

Reported: "I press Ctrl, Ctrl, type hello, Tora answers - but it never
shows in Chat." The live session heard no audio for a typed question, so
the turn ended with no question, and a turn without one is never written
to the journal (Chat and Home read the journal). The typed words are the
question now.
"""
from __future__ import annotations

from ai.gemini_realtime import Turn
from tests.agents.test_e2e_never_words_never_twice import _turn_manager


def _kept(mgr) -> list[tuple]:
    kept: list[tuple] = []
    mgr._record_turn = lambda question, answer: kept.append((question, answer))
    return kept


def test_a_typed_question_is_kept_with_its_answer():
    mgr, _pointed = _turn_manager()
    kept = _kept(mgr)
    mgr._on_realtime_heard("hello")              # what _typed_turn hands in
    mgr._on_realtime_turn_end(Turn(heard="", said="Hi! What can I help with?"))
    assert kept == [("hello", "Hi! What can I help with?")]


def test_a_spoken_question_keeps_what_the_session_heard():
    mgr, _pointed = _turn_manager()
    kept = _kept(mgr)
    mgr._heard_buf = ["what's ", "this"]
    mgr._on_realtime_turn_end(Turn(heard="what's this?", said="A chart of sales."))
    assert kept == [("what's this?", "A chart of sales.")]
