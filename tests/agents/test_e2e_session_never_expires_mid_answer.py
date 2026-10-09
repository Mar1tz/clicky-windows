"""FS-12 (docs/FAST_AND_RIGHT.md): never ask into a session about to close.

The server closes a live session at about ten minutes. On 4-9 Oct 7 of 110
questions went into one over eight minutes old, and 4 of those 7 lost their
answer to the close - 8 Oct 07:50, "register my business", waited through two
web searches and the session closed a second before the answer. No question
on a younger session lost one.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import ai.gemini_realtime as rt
import companion_manager as cm


class FakeSession:
    opened: list = []
    is_open = True

    def __init__(self, model, setup, resume_handle=None):
        self.resume_handle_in = resume_handle
        self.resume_handle = f"handle-{len(FakeSession.opened)}"
        FakeSession.opened.append(self)

    async def connect(self):
        pass

    async def events(self):
        await asyncio.sleep(3600)
        if False:
            yield None

    async def send_text(self, text, turn_complete=True):
        pass

    async def close(self):
        self.is_open = False


def test_an_old_session_is_retired_and_the_conversation_carries_on(monkeypatch):
    FakeSession.opened = []
    monkeypatch.setattr(rt, "LiveSession", FakeSession)
    monkeypatch.setattr(rt.PcmStreamPlayer, "stop", lambda self: None, raising=False)
    conv = rt.RealtimeConversation(tools={})

    async def scenario():
        assert await conv.connect("PROMPT")
        assert not conv.aging
        conv._opened_at = time.monotonic() - 9 * 60           # nine minutes old
        assert conv.aging and conv.ready
        conv.retire()
        assert not conv.ready, "not used for the next question"
        assert await conv.connect("PROMPT")
        fresh = FakeSession.opened[-1]
        assert fresh is not FakeSession.opened[0]
        assert fresh.resume_handle_in == "handle-0", "the same conversation, resumed - not forgotten"
        assert conv.ready and not conv.aging
        await conv.close()
    asyncio.run(scenario())


def test_the_server_saying_goodbye_makes_it_old_at_once(monkeypatch):
    FakeSession.opened = []
    monkeypatch.setattr(rt, "LiveSession", FakeSession)
    conv = rt.RealtimeConversation(tools={})

    async def scenario():
        await conv.connect("PROMPT")
        conv._opened_at = 0.0                                   # what go_away does
        assert conv.aging
        await conv.close()
    asyncio.run(scenario())


def _manager(conv, idle_s=60.0):
    m = cm.CompanionManager.__new__(cm.CompanionManager)
    m._last_talk_at = time.monotonic() - idle_s
    m._state = cm.AppState.IDLE
    m._realtime = conv
    m._rt_streaming = False
    m._tools_in_flight = 0
    m._submitted = []

    def submit(coro):
        m._submitted.append(coro.__name__)
        coro.close()
    m._submit = submit
    return m


def test_a_question_on_a_nine_minute_old_session_gets_a_fresh_one(monkeypatch):
    from config import cfg
    monkeypatch.setattr(cfg, "realtime", lambda: True)
    monkeypatch.setattr(cfg, "has_key", lambda: True)
    retired = []

    class Conv(SimpleNamespace):
        @property
        def ready(self):
            return not retired

    conv = Conv(connecting=False, aging=True, age_s=9 * 60, cancel=lambda: None, retire=lambda: retired.append(1))
    m = _manager(conv)
    m._rt_audio = []
    m._listener = SimpleNamespace(set_chunk_sink=lambda _f: None, start_recording=lambda: None)
    m._emit_state = lambda _s: None
    m._get_realtime = lambda: conv
    m._request_serial = 0
    m._is_new_question = lambda: False
    m.sig_error = SimpleNamespace(emit=lambda _e: None)
    m._begin_capture()
    assert retired == [1]
    assert "_realtime_connect" in m._submitted and "_realtime_stream" in m._submitted
    assert m._rt_on_demand, "the stream waits for the new session"


def test_the_session_is_renewed_at_seven_and_a_half_minutes_at_a_quiet_moment():
    conv = SimpleNamespace(ready=True, age_s=7.6 * 60, speaking=False)
    assert _manager(conv)._renew_due()
    assert not _manager(SimpleNamespace(ready=True, age_s=5 * 60, speaking=False))._renew_due(), "too young"
    assert not _manager(SimpleNamespace(ready=True, age_s=7.6 * 60, speaking=True))._renew_due(), \
        "not while she speaks"
    busy = _manager(conv)
    busy._state = cm.AppState.THINKING
    assert not busy._renew_due(), "not in the middle of an answer"
    assert not _manager(conv, idle_s=31 * 60)._renew_due(), "nobody talking: let it close (FS-02)"
