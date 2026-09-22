"""The live conversation survives the server retiring its session.

Gemini closes a Live session every few minutes. Before this, each
reconnect opened a blank conversation: the tools were still there, but
everything said so far was gone, so "open that one" had nothing to point
at - which looks exactly like an assistant that has lost the ability to
act. These tests hold the two halves of the fix: resume with the
server's handle, and when that is impossible, hand the new session a
recap of the last few turns.
"""
from __future__ import annotations

import asyncio

import pytest

import ai.gemini_realtime as rt
from ai.gemini_live import LiveSession


class FakeSession:
    """Stands in for LiveSession: records what setup it was given."""
    opened: list["FakeSession"] = []
    fail_with_handle = False

    def __init__(self, model, setup, resume_handle=None):
        self.model, self.setup, self.resume_handle_in = model, setup, resume_handle
        self.resume_handle = "handle-from-server"
        self.sent: list[tuple[str, bool]] = []
        self.closed = False
        FakeSession.opened.append(self)

    async def connect(self):
        if self.resume_handle_in and FakeSession.fail_with_handle:
            raise RuntimeError("resumption handle expired")

    async def events(self):
        await asyncio.sleep(3600)          # the pump just waits in these tests
        if False:
            yield None

    async def send_text(self, text, turn_complete=True):
        self.sent.append((text, turn_complete))

    async def send_tool_response(self, results):
        self.tool_responses = getattr(self, "tool_responses", [])
        self.tool_responses.extend(results)

    async def close(self):
        self.closed = True


@pytest.fixture
def realtime(monkeypatch):
    FakeSession.opened = []
    FakeSession.fail_with_handle = False
    monkeypatch.setattr(rt, "LiveSession", FakeSession)
    monkeypatch.setattr(rt.PcmStreamPlayer, "stop", lambda self: None, raising=False)
    conv = rt.RealtimeConversation(tools={}, recap=lambda: "User: open my project\nYou: opened it")
    yield conv
    asyncio.run(conv.close())


def test_the_handle_is_sent_back_so_the_conversation_continues(realtime):
    async def scenario():
        assert await realtime.connect("PROMPT")
        first = FakeSession.opened[0]
        assert first.resume_handle_in is None, "the first session has nothing to resume"

        # the server retires the session; the pump hands the handle over
        realtime._resume_handle = first.resume_handle
        realtime._session = None
        assert await realtime.connect("PROMPT")
        second = FakeSession.opened[-1]
        assert second.resume_handle_in == "handle-from-server"
        assert not second.sent, "a resumed session needs no recap"
    asyncio.run(scenario())


def test_a_refused_handle_falls_back_to_a_fresh_session_with_a_recap(realtime):
    async def scenario():
        assert await realtime.connect("PROMPT")
        realtime._resume_handle = "stale-handle"
        realtime._session = None
        FakeSession.fail_with_handle = True

        assert await realtime.connect("PROMPT"), "a refused handle must not leave us with no session"
        fresh = FakeSession.opened[-1]
        assert fresh.resume_handle_in is None
        text, turn_complete = fresh.sent[0]
        assert "open my project" in text and turn_complete is False
        assert "Do not greet the user again" in text
    asyncio.run(scenario())


def test_a_changed_system_prompt_starts_clean_but_still_recaps(realtime):
    async def scenario():
        await realtime.connect("PROMPT")
        realtime._resume_handle = "handle-from-server"
        realtime._session = None
        await realtime.connect("A DIFFERENT PROMPT")
        latest = FakeSession.opened[-1]
        assert latest.resume_handle_in is None, "a new instruction means a new session"
        assert latest.sent and "open my project" in latest.sent[0][0]
    asyncio.run(scenario())


def test_live_session_puts_the_handle_in_its_setup():
    with_handle = LiveSession("m", {"tools": []}, resume_handle="abc123")
    assert with_handle.setup_for_test["sessionResumption"] == {"handle": "abc123"}
    fresh = LiveSession("m", {"tools": []})
    assert fresh.setup_for_test["sessionResumption"] == {}
    assert fresh.resume_handle is None


# -- the tool budget is per request, not per session ----------------------------------

class ScriptedSession(FakeSession):
    """Replays a list of LiveEvents to the pump, then closes."""
    script: list = []

    async def events(self):
        for ev in list(ScriptedSession.script):
            yield ev
        yield rt_event("closed")


def rt_event(kind, **kw):
    from ai.gemini_live import LiveEvent
    return LiveEvent(kind=kind, **kw)


def _call(name="desktop_action"):
    from ai.gemini_live import ToolCall
    return ToolCall(id="1", name=name, args={})


def test_a_long_conversation_does_not_use_up_the_tool_budget(monkeypatch):
    """Field report: actions worked early in a chat and stopped later. The
    six-round budget was only reset by a turn that SPOKE, so tool-only
    turns and barge-ins spent it permanently."""
    ran: list[str] = []
    refused: list[str] = []

    async def handler(args):
        ran.append("ran")
        return {"ok": True}

    script = []
    for _ in range(5):                         # five earlier requests, tools only
        script += [rt_event("input_transcript", text="do a thing"),
                   rt_event("tool_call", calls=[_call()]),
                   rt_event("turn_complete", produced_output=False)]
    script += [rt_event("input_transcript", text="now open spotify"),
               rt_event("tool_call", calls=[_call()]),
               rt_event("turn_complete", produced_output=True)]

    ScriptedSession.script = script
    ScriptedSession.opened = []
    monkeypatch.setattr(rt, "LiveSession", ScriptedSession)
    monkeypatch.setattr(rt.PcmStreamPlayer, "stop", lambda self: None, raising=False)
    conv = rt.RealtimeConversation(tools={"desktop_action": handler})

    async def scenario():
        await conv.connect("PROMPT")
        for _ in range(200):
            if conv._session is None:          # the pump reached "closed"
                break
            await asyncio.sleep(0.01)
        sent = [t for s in ScriptedSession.opened for t in s.sent]
        assert len(ran) == 6, f"every request's tool call should run, got {len(ran)}"
        assert not refused
        assert conv._tool_rounds <= 1
        del sent
    asyncio.run(scenario())


def test_a_runaway_tool_chain_in_one_request_is_still_capped(monkeypatch):
    calls = 0

    async def handler(args):
        nonlocal calls
        calls += 1
        return {"ok": True}

    script = [rt_event("input_transcript", text="go")]
    script += [rt_event("tool_call", calls=[_call()]) for _ in range(9)]
    script += [rt_event("turn_complete", produced_output=True)]
    ScriptedSession.script = script
    ScriptedSession.opened = []
    monkeypatch.setattr(rt, "LiveSession", ScriptedSession)
    monkeypatch.setattr(rt.PcmStreamPlayer, "stop", lambda self: None, raising=False)
    conv = rt.RealtimeConversation(tools={"desktop_action": handler})

    async def scenario():
        await conv.connect("PROMPT")
        for _ in range(200):
            if conv._session is None:
                break
            await asyncio.sleep(0.01)
        assert calls == rt.MAX_TOOL_ROUNDS, "one request still cannot loop forever"
    asyncio.run(scenario())
