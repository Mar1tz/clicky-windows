"""FS-02 (docs/FAST_AND_RIGHT.md): yesterday's conversation is over.

9 Oct 2026, 13:32: "Hello." was answered with "The screen shows your Gmail
inbox rather than the CIPC Company Registration page" - a request from the
morning before. The live session had been resumed, or re-seeded with a recap
of the last exchanges, every ten minutes all night; on a plan every one of
those reopenings is a voice pass. And a question that found no session went
down the much slower cascade.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import ai.gemini_realtime as rt
import companion_manager as cm
from ai.base_provider import Message


class FakeSession:
    opened: list = []
    is_open = True

    def __init__(self, model, setup, resume_handle=None):
        self.resume_handle_in = resume_handle
        self.resume_handle = "handle-from-server"
        self.sent = []
        FakeSession.opened.append(self)

    async def connect(self):
        pass

    async def events(self):
        await asyncio.sleep(3600)
        if False:
            yield None

    async def send_text(self, text, turn_complete=True):
        self.sent.append(text)

    async def close(self):
        self.is_open = False


def test_a_forgotten_conversation_starts_blank(monkeypatch):
    FakeSession.opened = []
    monkeypatch.setattr(rt, "LiveSession", FakeSession)
    monkeypatch.setattr(rt.PcmStreamPlayer, "stop", lambda self: None, raising=False)
    recap = ["User: register my business with CIPC\nYou: here's the page"]
    conv = rt.RealtimeConversation(tools={}, recap=lambda: recap[0])

    async def scenario():
        assert await conv.connect("PROMPT")
        conv._resume_handle = "yesterday"
        conv.forget()
        recap[0] = ""                                   # the manager cleared its history too
        assert not conv.ready, "an open session from before the silence is not used"
        assert await conv.connect("PROMPT")
        fresh = FakeSession.opened[-1]
        assert fresh.resume_handle_in is None and fresh.sent == [], "no handle, no recap"
        assert conv.ready
        await conv.close()
    asyncio.run(scenario())


def _manager(idle_s: float, guide_running: bool = False):
    m = cm.CompanionManager.__new__(cm.CompanionManager)
    m._last_talk_at = time.monotonic() - idle_s
    m._state = cm.AppState.IDLE
    m._guide_runner = SimpleNamespace(running=True) if guide_running else None
    m._submitted = []

    def submit(coro):
        m._submitted.append(coro.__name__)
        coro.close()
    m._submit = submit
    return m


def test_a_dropped_session_is_reopened_only_while_they_are_talking():
    busy = _manager(5 * 60)
    busy._on_realtime_lost("closed")
    assert busy._submitted == ["_realtime_reconnect"]
    idle = _manager(31 * 60)
    idle._on_realtime_lost("closed")
    assert idle._submitted == [], "not all night long"
    walkthrough = _manager(31 * 60, guide_running=True)
    walkthrough._on_realtime_lost("closed")
    assert walkthrough._submitted == ["_realtime_reconnect"], "a walkthrough needs her voice"


def _capture(m, conv):
    m._realtime = conv
    m._rt_audio = []
    m._listener = SimpleNamespace(set_chunk_sink=lambda _f: None, start_recording=lambda: None)
    m._emit_state = lambda _s: None
    m._get_realtime = lambda: conv
    m._request_serial = 0
    m._is_new_question = lambda: False
    m._app_memory = {"chrome": [Message(role="user", content="register my business with CIPC")]}
    m._last_look = {"at": time.monotonic()}
    m.sig_error = SimpleNamespace(emit=lambda _e: None)
    m._begin_capture()


def test_after_half_an_hour_the_next_question_is_a_new_conversation(monkeypatch):
    from config import cfg
    monkeypatch.setattr(cfg, "realtime", lambda: True)
    monkeypatch.setattr(cfg, "has_key", lambda: True)
    forgot = []
    conv = SimpleNamespace(ready=False, connecting=False, cancel=lambda: None, forget=lambda: forgot.append(1))
    m = _manager(31 * 60)
    _capture(m, conv)
    assert forgot == [1] and m._app_memory == {} and m._last_look is None, "yesterday is not this conversation"
    assert "_realtime_connect" in m._submitted and "_realtime_stream" in m._submitted, \
        "the voice opens on the question - not the cascade"
    assert m._rt_on_demand and m._rt_streaming


def test_a_question_a_few_minutes_later_is_the_same_conversation(monkeypatch):
    from config import cfg
    monkeypatch.setattr(cfg, "realtime", lambda: True)
    monkeypatch.setattr(cfg, "has_key", lambda: True)
    forgot = []
    conv = SimpleNamespace(ready=True, connecting=False, cancel=lambda: None, forget=lambda: forgot.append(1))
    m = _manager(4 * 60)
    _capture(m, conv)
    assert forgot == [] and m._app_memory, "\"that one\" still means what it meant"
    assert m._submitted == ["_realtime_stream"]
