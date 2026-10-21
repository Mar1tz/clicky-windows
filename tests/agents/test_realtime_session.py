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

    is_open = True

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


# -- FX-03: a handle is used once, and never past its life -----------------------------
#
# 66 refused resumes on 1-17 Oct, each a second connect before a word: a
# session resumed with a handle reported that same handle as its own until
# the server gave it a new one, so a short session handed back a spent one.

def test_a_resumed_session_does_not_hand_back_the_handle_it_was_opened_with():
    session = LiveSession("m", {"tools": []}, resume_handle="spent")
    assert session.resume_handle is None, "only a handle this session is given"


def test_an_empty_update_keeps_the_last_handle():
    import json

    class Socket:
        def __init__(self, msgs):
            self.msgs = [json.dumps(m) for m in msgs]

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.msgs:
                raise StopAsyncIteration
            return self.msgs.pop(0)

    session = LiveSession("m", {"tools": []})
    session._ws = Socket([{"sessionResumptionUpdate": {"newHandle": "h2", "resumable": True}},
                          {"sessionResumptionUpdate": {"resumable": False}}])     # a turn under way

    async def drain():
        async for _ev in session.events():
            pass
    asyncio.run(drain())
    assert session.resume_handle == "h2"


def test_a_spent_handle_is_not_tried_again(realtime):
    async def scenario():
        await realtime.connect("PROMPT")
        realtime._resume_handle = "handle-from-server"
        realtime._session = None
        await realtime.connect("PROMPT")                     # resumed: the handle is spent
        assert realtime._resume_handle is None
        realtime._session = None
        await realtime.connect("PROMPT")                     # its session was given none
        assert FakeSession.opened[-1].resume_handle_in is None, "fresh, not a refused resume"
    asyncio.run(scenario())


def test_an_expired_handle_is_not_tried(realtime, monkeypatch):
    async def scenario():
        await realtime.connect("PROMPT")
        realtime._resume_handle = "old"
        realtime._handle_at = rt.time.monotonic() - rt.RESUME_FOR_S - 1
        realtime._session = None
        await realtime.connect("PROMPT")
        latest = FakeSession.opened[-1]
        assert latest.resume_handle_in is None and latest.sent, "fresh, with the recap"
    asyncio.run(scenario())


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


# -- saying something after the conversation has moved on -----------------------------

def test_say_now_waits_for_a_gap_then_speaks(monkeypatch):
    """An approval answered ten seconds late still has to be reported. It
    must not land on top of a sentence already being spoken."""
    import ai.gemini_realtime as rt

    conv = rt.RealtimeConversation.__new__(rt.RealtimeConversation)
    session = FakeSession("m", {})
    conv._session = session
    conv._speaking = True
    conv._player = type("P", (), {"queued_seconds": 0.0})()

    async def scenario():
        async def stop_speaking():
            await asyncio.sleep(0.05)
            conv._speaking = False
        asyncio.ensure_future(stop_speaking())
        assert await conv.say_now("[it is done]", wait_s=3.0) is True

    asyncio.run(scenario())
    assert session.sent == [("[it is done]", True)], "the turn must END, or she never answers"


def test_say_now_gives_up_rather_than_talking_over_her(monkeypatch):
    import ai.gemini_realtime as rt

    conv = rt.RealtimeConversation.__new__(rt.RealtimeConversation)
    session = FakeSession("m", {})
    conv._session = session
    conv._speaking = True
    conv._player = type("P", (), {"queued_seconds": 0.0})()

    assert asyncio.run(conv.say_now("[it is done]", wait_s=0.2)) is False
    assert session.sent == []


# -- GW-01: the runaway brake, in the shape the wire actually has ---------------------

def _run_script(monkeypatch, script, handler_name="guide_step"):
    """Drive the real pump with a scripted event stream; count tool runs."""
    ran: list[str] = []

    async def handler(args):
        ran.append(handler_name)
        return {"ok": True}

    ScriptedSession.script = script
    ScriptedSession.opened = []
    monkeypatch.setattr(rt, "LiveSession", ScriptedSession)
    monkeypatch.setattr(rt.PcmStreamPlayer, "stop", lambda self: None, raising=False)
    conv = rt.RealtimeConversation(tools={handler_name: handler})

    async def scenario():
        await conv.connect("PROMPT")
        for _ in range(300):
            if conv._session is None:
                break
            await asyncio.sleep(0.01)

    asyncio.run(scenario())
    return ran, conv


def test_a_tool_loop_with_turn_completes_between_calls_is_still_capped(monkeypatch):
    """The 16:39 session, as the Live API delivers it: every tool round ends
    with its own turn_complete. Field fix 5 refilled the budget on each one,
    so the counter went 1, 0, 1, 0 and the brake fired zero times while the
    model called guide_step 39 times in 24 seconds.

    The test this replaces scripted nine tool calls with NO turn_completes
    between them - not what the wire does - and so it passed."""
    script = [rt_event("input_transcript", text="next")]
    for _ in range(40):
        script += [rt_event("tool_call", calls=[_call("guide_step")]),
                   rt_event("turn_complete", produced_output=False)]
    script += [rt_event("turn_complete", produced_output=True)]

    ran, _conv = _run_script(monkeypatch, script)
    assert len(ran) == rt.MAX_TOOL_ROUNDS, \
        f"one request may make {rt.MAX_TOOL_ROUNDS} tool calls, not {len(ran)}"


def test_a_question_after_a_tool_only_turn_gets_a_fresh_budget(monkeypatch):
    """What Field fix 5 was actually for, kept: a turn that only called tools
    never 'finishes', so its transcript was never cleared, and the old
    first-fragment check missed the NEXT question entirely."""
    script = []
    for _ in range(8):                       # eight separate requests
        script += [rt_event("input_transcript", text="do the thing"),
                   rt_event("tool_call", calls=[_call("guide_step")]),
                   rt_event("turn_complete", produced_output=False)]
    ran, _conv = _run_script(monkeypatch, script)
    assert len(ran) == 8, "eight requests, eight tool calls - none refused"


def test_one_long_request_and_its_follow_up_are_budgeted_separately(monkeypatch):
    script = [rt_event("input_transcript", text="first")]
    for _ in range(10):
        script += [rt_event("tool_call", calls=[_call("guide_step")]),
                   rt_event("turn_complete", produced_output=False)]
    script += [rt_event("input_transcript", text="second")]
    for _ in range(3):
        script += [rt_event("tool_call", calls=[_call("guide_step")]),
                   rt_event("turn_complete", produced_output=False)]
    ran, _conv = _run_script(monkeypatch, script)
    assert len(ran) == rt.MAX_TOOL_ROUNDS + 3


def test_the_brake_says_so_in_the_log(monkeypatch, caplog):
    import logging
    script = [rt_event("input_transcript", text="next")]
    for _ in range(9):
        script += [rt_event("tool_call", calls=[_call("guide_step")]),
                   rt_event("turn_complete", produced_output=False)]
    with caplog.at_level(logging.WARNING, logger="tora.realtime"):
        _run_script(monkeypatch, script)
    assert any("did not settle" in r.getMessage() for r in caplog.records), \
        "a brake that fires in silence is how this went unnoticed for a day"


def test_every_session_shares_one_tls_context_built_off_the_loop(monkeypatch):
    """~22 ms of a blocked loop per connection, every wake check (19 Oct)."""
    import ai.gemini_live as gl
    made, seen = [], []
    monkeypatch.setattr(gl, "_TLS", None)
    monkeypatch.setattr(gl.ssl, "create_default_context", lambda: made.append(1) or object())

    class WS:
        async def send(self, _m): pass
        async def recv(self): return '{"setupComplete": {}}'

    async def connect(address, **kw):
        seen.append(kw.get("ssl"))
        return WS()

    monkeypatch.setattr(gl.websockets, "connect", connect)

    async def go():
        for _ in range(2):
            s = LiveSession("m", {"tools": []})
            s._address = lambda: asyncio.sleep(0, "wss://example.test/ws")
            await s.connect()
    asyncio.run(go())
    assert len(made) == 1 and len(seen) == 2 and seen[0] is seen[1] is not None
