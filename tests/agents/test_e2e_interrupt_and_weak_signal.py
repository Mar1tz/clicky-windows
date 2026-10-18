"""A press always works, a weak connection is coped with, and her cursor can
be hidden (18 Oct 2026).

The owner: "it doesn't let me talk to it when there's a prompt already -
make sure it starts listening fast" (asked what a press should do while she
answers: "she stops, listens at once"); "improve this for people with bad
reception and slow Wi-Fi"; and "a shortcut to hide the HeyTora cursor and
only show the top thing - add it to onboarding too".
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import httpx  # noqa: E402
import pytest  # noqa: E402

import ai.gemini_realtime as rt  # noqa: E402
import companion_manager as cm  # noqa: E402
from ai import net_quality  # noqa: E402
from ai.gemini_live import LiveEvent, ToolCall  # noqa: E402
from audio import playback  # noqa: E402
from ui.panel import AppState  # noqa: E402
from tests.agents.test_e2e_first_launch import _Live, _sign_in, _wizard, qapp, service  # noqa: E402,F401


# -- a press while she answers: she stops and listens ---------------------------------------------------------

def _manager(state, conv=None):
    m = cm.CompanionManager.__new__(cm.CompanionManager)
    m.done = []
    m._begin_lock = threading.Lock()
    m._state = state
    m._realtime = conv if conv is not None else SimpleNamespace(interrupt=lambda: m.done.append("voice stops"))
    m._loop = None
    m._pointing_held = False
    return m


def test_a_press_while_she_answers_stops_her_and_listens():
    for state in (AppState.THINKING, AppState.SPEAKING):
        m = _manager(state)
        assert m._claim_begin(), f"a press while {state.name} is heard"
        assert m._state is AppState.LISTENING and m._question_no == 1, "a new question"
        assert m.done == ["voice stops"]


def test_a_press_while_listening_or_at_rest_stops_nothing():
    listening = _manager(AppState.LISTENING)
    assert not listening._claim_begin(), "one question at a time"
    idle = _manager(AppState.IDLE)
    assert idle._claim_begin()
    assert listening.done == idle.done == []


def test_the_slow_route_stops_and_cannot_take_the_state_back(monkeypatch):
    shown = []
    monkeypatch.setattr(cm.CompanionManager, "sig_state_changed", SimpleNamespace(emit=shown.append))

    async def go():
        m = _manager(AppState.IDLE)
        m._question_no = 1                          # the question being answered
        m._loop = asyncio.get_running_loop()
        m._listener = SimpleNamespace(set_wake_paused=lambda _p: None)
        thinking = asyncio.Event()

        async def slow_route():                     # the shape of _cascade_turn's start and end
            cm._ANSWERING.set(m._question_no)
            m._answer_task = asyncio.current_task()
            m._emit_state(AppState.THINKING)
            try:
                thinking.set()
                await asyncio.sleep(10)             # transcribing, thinking, speaking...
            finally:
                m._emit_state(AppState.IDLE)

        task = asyncio.ensure_future(slow_route())
        await thinking.wait()
        await asyncio.to_thread(m._claim_begin)     # the press, on the keyboard hook's thread
        await asyncio.sleep(0.05)
        return task, m

    task, m = asyncio.run(go())
    assert task.cancelled(), "the slow route stops"
    assert m._state is AppState.LISTENING
    assert shown == [AppState.THINKING], "its IDLE on the way out is not the new question's"


class Player:
    queued_seconds = 0.0
    latency = 0.0
    bytes_per_second = 48_000

    def __init__(self):
        self.fed = []

    def start(self): pass
    def stop(self): pass
    def feed(self, pcm): self.fed.append(pcm)
    def clear(self): pass


class Live:
    """A live session the test feeds one event at a time."""
    is_open = True
    resume_handle = None
    last = None

    def __init__(self, model, setup, resume_handle=None):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.responses = []
        Live.last = self

    async def connect(self):
        pass

    async def events(self):
        while True:
            ev = await self.queue.get()
            yield ev
            if ev.kind == "closed":
                return

    async def send_tool_response(self, results):
        self.responses.append(results)

    async def send_text(self, *_a, **_k):
        pass

    async def close(self):
        pass


async def _feed(*events):
    for ev in events:
        await Live.last.queue.put(ev)
    await asyncio.sleep(0.02)


def test_what_comes_for_the_answer_she_was_stopped_in_is_dropped(monkeypatch):
    monkeypatch.setattr(rt, "LiveSession", Live)

    async def go():
        turns, said = [], []
        conv = rt.RealtimeConversation(tools={}, on_turn_end=turns.append, on_said=said.append)
        player = conv._player = Player()
        await conv.connect("PROMPT")
        await _feed(LiveEvent("output_transcript", text="The old answer"), LiveEvent("audio", audio=b"old"))
        conv.interrupt()
        await _feed(LiveEvent("output_transcript", text=" goes on"), LiveEvent("audio", audio=b"stale"),
                    LiveEvent("turn_complete", produced_output=True))
        await _feed(LiveEvent("input_transcript", text="what about this"),
                    LiveEvent("output_transcript", text="This one."), LiveEvent("audio", audio=b"new"),
                    LiveEvent("turn_complete", produced_output=True))
        await conv.close()
        return player, turns, said

    player, turns, said = asyncio.run(go())
    assert player.fed == [b"old", b"new"], "the rest of the old answer is never heard"
    assert said == ["The old answer", "This one."]
    assert [(t.heard, t.said) for t in turns] == [("what about this", "This one.")]


def test_a_tool_running_when_she_is_stopped_is_cancelled(monkeypatch):
    monkeypatch.setattr(rt, "LiveSession", Live)
    started = []

    async def look(_args):
        started.append(1)
        await asyncio.sleep(10)
        return {"say": "too late"}

    async def go():
        conv = rt.RealtimeConversation(tools={"look_at_screen": look})
        conv._player = Player()
        await conv.connect("PROMPT")
        await _feed(LiveEvent("tool_call", calls=[ToolCall("c1", "look_at_screen", {"question": "x"})]))
        conv.interrupt()
        await asyncio.sleep(0.02)
        # asked for by the answer that was cut off: not run
        await _feed(LiveEvent("tool_call", calls=[ToolCall("c2", "look_at_screen", {"question": "y"})]))
        await conv.close()
        return Live.last.responses

    responses = asyncio.run(go())
    assert started == [1]
    assert [r[0][1] for r in responses] == [rt._STOPPED, rt._STOPPED]


def test_the_old_question_s_trailing_silence_stops_when_a_new_one_begins(monkeypatch):
    sent = []

    class Session:
        is_open = True

        async def send_audio(self, pcm):
            sent.append(pcm)

    async def go():
        conv = rt.RealtimeConversation(tools={})
        conv._session = Session()
        began = time.monotonic()
        await conv.end_user_audio(stop=lambda: time.monotonic() - began > 0.15)
    asyncio.run(go())
    assert len(sent) < rt.TAIL_SILENCE_FRAMES, "not threaded through the new question"


# -- a weak connection -----------------------------------------------------------------------------------------

def test_a_session_still_opening_is_waited_for_not_the_slow_route():
    async def go():
        m = _manager(AppState.LISTENING)
        m._question_no = 1
        m._rt_streaming = True
        conv = SimpleNamespace(ready=False, connecting=True)

        async def opens():
            await asyncio.sleep(0.3)
            conv.ready, conv.connecting = True, False
        asyncio.ensure_future(opens())
        return await m._await_session(conv), m

    ok, m = asyncio.run(go())
    assert ok and m._rt_streaming and not m._rt_awaiting, "sent on the live route once it opened"


def test_a_session_that_never_opens_sends_the_whole_question_the_slow_way():
    async def go():
        m = _manager(AppState.THINKING)
        m._question_no = 1
        m._rt_streaming = False                     # they have finished talking
        m._rt_question_pcm = b"q" * 6400
        m._turn_seq = 0
        sent = []

        async def slow_route(pcm):
            sent.append(pcm)
        m._cascade_turn = slow_route
        conv = SimpleNamespace(ready=False, connecting=True)

        async def fails():
            await asyncio.sleep(0.2)
            conv.connecting = False
        asyncio.ensure_future(fails())
        return await m._await_session(conv), sent, m

    ok, sent, m = asyncio.run(go())
    assert not ok and sent == [b"q" * 6400], "answered the slow way, all of it"
    assert m._turn_seq == 1, "the live route's watchdog stands down"


def test_the_end_of_a_question_waiting_for_its_session_stays_on_the_live_route():
    m = _manager(AppState.LISTENING)
    m._question_no = m._rt_awaiting = 3
    m._rt_streaming = True
    m._realtime = SimpleNamespace(ready=False)
    m._listener = SimpleNamespace(stop_recording=lambda: b"q" * 6400)
    m._emit_state = lambda s: setattr(m, "_state", s)
    m._activity = lambda _k: None
    m._turn_seq = 0
    m._submit = lambda coro: (m.done.append(coro.__name__), coro.close())
    asyncio.run(m._end_capture_and_process())
    assert m._rt_question_pcm == b"q" * 6400, "kept for the slow way, should it never open"
    assert m._state is AppState.THINKING and m.done == ["_realtime_turn_watchdog"]


def test_a_dropped_session_while_they_talk_does_not_lose_the_question():
    m = _manager(AppState.LISTENING)
    m._last_talk_at = time.monotonic()
    m._guide_runner = None
    m._emit_state = lambda s: m.done.append(s)
    m._submit = lambda coro: coro.close()
    m._on_realtime_lost("closed")
    assert m.done == [] and m._state is AppState.LISTENING, "the release still ends the question"


def test_a_backed_up_voice_upload_marks_the_connection_slow():
    net_quality.reset()

    class Session:
        is_open = True

        async def send_audio(self, pcm):
            pass

        def backlog_bytes(self):
            return 60_000

    conv = rt.RealtimeConversation(tools={})
    conv._session = Session()
    try:
        asyncio.run(conv.push_audio(bytes(20 * rt.CHUNK_BYTES)))
        assert not net_quality.slow(), "a question held back while the session opened is sent whole"
        asyncio.run(conv.push_audio(bytes(rt.CHUNK_BYTES)))
        assert net_quality.slow(), "a live piece behind the microphone is"
    finally:
        net_quality.reset()


def _player():
    p = playback.PcmStreamPlayer(sample_rate=24000, block=1200)
    p._stream = SimpleNamespace()                   # no device: the callback is driven by hand
    playback._arm_audio()
    out = bytearray(2400)

    def pull() -> bytes:
        p._callback(memoryview(out), 1200, None, None)
        return bytes(out)
    return p, pull


def test_the_voice_is_gathered_over_a_weak_connection():
    p, pull = _player()
    p.feed(b"\x01\x00" * 600)                       # 25 ms: too little to start on
    assert pull() == bytes(2400), "held back - silence, not a clipped word"
    p.feed(b"\x01\x00" * 2400)                      # now 125 ms
    assert pull()[:2] == b"\x01\x00", "plays once enough is here"
    pull()
    pull()                                          # ran dry mid-answer
    assert p.dry_runs == 1 and p._gathering, "a gap is gathered over too"


def test_the_gathering_grows_with_a_bad_connection_and_shrinks_again():
    p, _pull = _player()
    p.dry_runs = 2
    p.end_of_answer()
    assert p.prebuffer == pytest.approx(p.PREBUFFER_S + 0.1)
    for _ in range(10):
        p.end_of_answer()                           # answers that flowed
    assert p.prebuffer == pytest.approx(p.PREBUFFER_S)


def test_the_end_of_an_answer_plays_without_waiting():
    p, pull = _player()
    p.feed(b"\x02\x00" * 600)
    p.end_of_answer()
    assert pull()[:2] == b"\x02\x00"


def test_one_connection_serves_every_call_to_heytora_s_server():
    from account.service import Account
    ok = httpx.MockTransport(lambda _r: httpx.Response(200, json={}))
    acc = Account(transport=ok)
    assert acc._http() is acc._http(), "kept, not made for each call"
    acc._transport = httpx.MockTransport(lambda _r: httpx.Response(200, json={}))
    assert acc._http() is not None and acc._client_transport is acc._transport, "a new transport, a new client"


def test_the_microphone_is_warmed_once_at_start(monkeypatch):
    from audio import ambient_listener as al
    made = []

    class Stream:
        def __init__(self, **kw):
            made.append(kw)

        def close(self):
            made.append("closed")

    monkeypatch.setattr(al.sd, "InputStream", Stream)
    listener = al.AmbientListener(on_level=lambda _r: None, on_wake=lambda: None, ambient=False)
    listener.start()
    end = time.monotonic() + 2
    while "closed" not in made and time.monotonic() < end:
        time.sleep(0.01)
    assert made[-1] == "closed" and "callback" not in made[0], "made and closed - never started"
    assert listener._stream is None, "the microphone is not left open"


# -- her cursor, or only her bar --------------------------------------------------------------------------------

def test_hidden_she_is_drawn_only_while_she_points():
    from ui import overlay
    seen = lambda phase: overlay.CursorOverlay._buddy_visible(SimpleNamespace(_flight_phase=phase))  # noqa: E731
    try:
        overlay.set_cursor_shown(False)
        assert not seen(overlay._PHASE_FOLLOW), "not beside the mouse"
        assert seen(overlay._PHASE_FLYING) and seen(overlay._PHASE_DWELLING), "but where she points"
        assert not seen(overlay._PHASE_RETURNING)
        overlay.set_cursor_shown(True)
        assert seen(overlay._PHASE_FOLLOW)
    finally:
        overlay.set_cursor_shown(True)


def test_the_choice_and_its_shortcut_are_kept(monkeypatch):
    from config import cfg, pretty_keys
    monkeypatch.setenv("TORA_CURSOR", "shown")              # restored after the test
    monkeypatch.delenv("TORA_CURSOR_HOTKEY", raising=False)
    written = []
    monkeypatch.setattr(cfg, "_write_env", lambda k, v: written.append((k, v)))
    assert cfg.cursor_shown()
    cfg.set_cursor_shown(False)
    assert not cfg.cursor_shown() and written == [("TORA_CURSOR", "hidden")]
    assert pretty_keys(cfg.cursor_hotkey()) == "Win + Alt + H"


def test_the_tora_page_switch_follows_the_shortcut(qapp):
    from PyQt6.QtCore import QObject, pyqtSignal
    from ui.app_pages import _CursorRow

    class Bridge(QObject):
        changed = pyqtSignal(str)
        shown = True

        def cursor_shown(self):
            return self.shown

        def set_cursor_shown(self, on):
            self.shown = on
            self.changed.emit("cursor")

        def cursor_hotkey(self):
            return "Win + Alt + H"

    br = Bridge()
    row = _CursorRow(br)
    assert row.switch.isChecked()
    br.shown = False
    br.changed.emit("cursor")                               # Win + Alt + H, pressed anywhere
    assert not row.switch.isChecked()


def test_the_first_launch_asks_whether_to_show_her_cursor(qapp, service, monkeypatch):
    from config import cfg
    from ui import autostart, overlay
    monkeypatch.setattr(autostart, "set_enabled", lambda on: True)
    monkeypatch.setenv("TORA_CURSOR", "shown")              # restored after the test
    monkeypatch.setenv("TORA_CURSOR_COLOUR", "blue")
    _sign_in(service)
    w = _wizard(service.account)
    w.attach(_Live())
    w._go("personal")
    assert w.cursor_row.switch.isChecked(), "shown unless they say otherwise"
    w.cursor_row.switch.setChecked(False)
    try:
        w._next()
        assert not cfg.cursor_shown() and not overlay.cursor_shown()
    finally:
        overlay.set_cursor_shown(True)


# -- a look waits for drawings only when there may be some (SC-05) -----------------------------------------------

def test_a_look_waits_to_rub_out_drawings_only_when_there_are_some():
    m = _manager(AppState.IDLE)
    m._guide_runner = None
    assert not m._drawings_on_screen(), "nothing drawn: no 0.1 s wait"
    m._note_drawn({"kind": "arrow"})
    assert m._drawings_on_screen()
    m._drawn_at -= m.DRAWINGS_LAST_S + 1               # it has faded since
    assert not m._drawings_on_screen()
    m._guide_runner = SimpleNamespace(running=True)  # a walkthrough's drawings stay
    assert m._drawings_on_screen()
