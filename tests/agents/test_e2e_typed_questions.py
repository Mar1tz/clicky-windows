"""Typed questions (29 Oct 2026): "text input should definitely be an
addition" - HeyClicky's Ask box, in the shape's Chat.

A typed question is a question like a spoken one: it stops an answer under
way, counts as a talk message, goes to the live session (or the slow route
when there is none, or when the session never answers) and is answered
aloud with her words on the card, and kept in the journal. The shape steps
aside first, so the screen she reads is theirs.
"""
from __future__ import annotations

import asyncio

import companion_manager as cm


class _Conv:
    """The live session, as _typed_turn sees it."""

    def __init__(self, ready=True, answers=True):
        self.ready, self.connecting, self.sent = ready, False, []
        self._answers = answers

    def cancel(self):
        self.sent.append(("cancel",))

    async def send_context(self, line):
        self.sent.append(("context", line))

    async def ask_text(self, text):
        self.sent.append(("ask", text))
        return self._answers


def _manager(monkeypatch, conv=None, live=True):
    m = cm.CompanionManager.__new__(cm.CompanionManager)
    m._question_no = 1
    m._turn_seq = 0
    m._realtime = conv
    m._rt_audio = []
    m._request_serial = 0
    m.states, m.heard, m.submitted, m.slow = [], [], [], []
    m._emit_state = m.states.append
    m._idle_s = lambda: 0.0
    m._forget_conversation = lambda: None
    m._activity = lambda what: None
    m._new_talk = lambda: None
    m._on_realtime_heard = m.heard.append

    async def screen():
        return "Excel - October.xlsx"
    m._screen_context = screen

    def submit(coro):
        m.submitted.append(coro.cr_code.co_name)
        coro.close()
    m._submit = submit

    async def slow(pcm, typed=""):
        m.slow.append((pcm, typed))
    m._cascade_turn = slow
    monkeypatch.setattr(cm.cfg, "realtime", lambda: live)
    monkeypatch.setattr(cm.cfg, "has_key", lambda: True)
    monkeypatch.setattr(cm.CompanionManager, "_is_new_question", staticmethod(lambda: True))
    return m


def test_a_typed_question_goes_to_the_live_session_with_the_screen(monkeypatch):
    conv = _Conv()
    m = _manager(monkeypatch, conv)
    asyncio.run(m._typed_turn("Why won't this total add up?"))
    assert conv.sent[1:] == [("context", "Excel - October.xlsx"), ("ask", "Why won't this total add up?")]
    assert m.heard == ["Why won't this total add up?"], "counted, and kept as the journal's question"
    assert m.states == [cm.AppState.THINKING]
    assert m.submitted == ["_realtime_turn_watchdog"], "watched like any turn"
    assert m.slow == []


def test_without_a_live_session_the_slow_route_answers_it(monkeypatch):
    m = _manager(monkeypatch, live=False)                 # Settings: Classic answers
    asyncio.run(m._typed_turn("What's on my screen?"))
    assert m.slow == [(b"", "What's on my screen?")]


def test_a_session_that_refuses_the_text_sends_it_the_slow_way(monkeypatch):
    m = _manager(monkeypatch, _Conv(answers=False))
    asyncio.run(m._typed_turn("Draft a reply"))
    assert m.slow == [(b"", "Draft a reply")]


def test_a_newer_question_wins(monkeypatch):
    conv = _Conv()
    m = _manager(monkeypatch, conv)

    async def screen():
        m._question_no += 1                              # they pressed the key meanwhile
        return "line"
    m._screen_context = screen
    asyncio.run(m._typed_turn("old question"))
    assert ("ask", "old question") not in conv.sent and m.slow == []


def test_an_unanswered_typed_question_is_asked_again_the_slow_way(monkeypatch):
    m = cm.CompanionManager.__new__(cm.CompanionManager)
    m._state = cm.AppState.THINKING
    m._turn_seq = 1
    m._heard_buf = ["Why?"]
    m._tools_in_flight, m._tool_done_at = 0, 0.0
    m._NOTHING_HEARD_S, m._HEARD_NO_ANSWER_S, m._TURN_WATCHDOG_MAX_S = 0.2, 0.3, 1.0
    m._activity = lambda what: None
    m._emit_state = lambda s: None
    m.dropped, m.slow = [], []
    m._drop_realtime = lambda: m.dropped.append(1)

    async def slow(pcm, typed=""):
        m.slow.append((pcm, typed))
    m._cascade_turn = slow
    asyncio.run(m._realtime_turn_watchdog(1, typed="Why?"))
    assert m.slow == [(b"", "Why?")] and m.dropped == [1]


def test_the_slow_route_takes_typed_words_as_they_are(monkeypatch):
    """Nothing to transcribe - and a typed "stop" is the command."""
    m = cm.CompanionManager.__new__(cm.CompanionManager)
    m._question_no = 1
    m.states, m.stopped = [], []
    m._emit_state = m.states.append

    def no_stt():
        raise AssertionError("a typed question is not transcribed")
    m._get_stt = no_stt
    m.stop = lambda: m.stopped.append(1)
    asyncio.run(m._cascade_turn(b"", typed="stop"))
    assert m.stopped == [1]


def test_the_session_sends_it_as_a_finished_turn():
    from ai.gemini_realtime import RealtimeConversation, Turn

    class Session:
        is_open = True
        sent = []

        async def send_text(self, text, *, turn_complete=True):
            self.sent.append((text, turn_complete))

    c = RealtimeConversation.__new__(RealtimeConversation)
    c._session, c._forget, c._retiring = Session(), False, False
    c._superseded, c._turn, c._tool_rounds, c._awaiting_user = True, Turn(), 3, True
    assert asyncio.run(c.ask_text("What does this clause mean?"))
    assert Session.sent == [("What does this clause mean?", True)], "turnComplete: she answers it"
    assert c._superseded is False and c._tool_rounds == 0, "a new request, as a spoken one is"
    assert not asyncio.run(c.ask_text(""))
