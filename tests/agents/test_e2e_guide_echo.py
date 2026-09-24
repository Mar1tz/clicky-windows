"""GW-04: Tora never hears herself say "next".

A walkthrough speaks steps like "click Next", and the nudge after a long
wait says, word for word, "Say 'next' when you've done it". If either
reached the live model as the user's voice, the guide would advance itself
- the same runaway as 23 September, started from the other end.

It cannot, by construction, and this file pins each part of the
construction so a later change cannot quietly reopen it:

  1. The microphone feeds the model only between _begin_capture and
     stop_recording.
  2. _begin_capture silences every playback BEFORE it opens that path.
  3. A step cannot START speaking while the user is being recorded.
  4. While Tora is speaking, the wake word is not listened for, so her
     own voice cannot open a capture in the first place.

Nothing here touches a real microphone or speaker.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ui.panel import AppState


class FakeListener:
    """Records the order things happen in."""

    def __init__(self, journal: list) -> None:
        self.journal = journal
        self.sink = None
        self.paused = None

    def set_chunk_sink(self, fn) -> None:
        self.journal.append("sink attached" if fn else "sink detached")
        self.sink = fn

    def start_recording(self) -> None:
        self.journal.append("mic opened")

    def stop_recording(self) -> bytes:
        self.journal.append("mic closed")
        self.sink = None
        return b""

    def set_wake_paused(self, paused: bool) -> None:
        self.paused = paused


def _manager(journal):
    from companion_manager import CompanionManager

    mgr = CompanionManager.__new__(CompanionManager)
    mgr._listener = FakeListener(journal)
    mgr._realtime = None
    mgr._rt_audio = []
    mgr._request_serial = 0
    mgr._state = AppState.IDLE
    mgr._emit_state = lambda state: setattr(mgr, "_state", state)
    mgr._submit = lambda coro: coro.close()
    mgr._reconnect_realtime = lambda *a, **k: None
    return mgr


# 1 + 2 ── the only door is opened after the room is quiet ────────────────────

def test_playback_is_silenced_before_the_microphone_opens(monkeypatch):
    journal: list[str] = []
    import audio.playback as playback
    monkeypatch.setattr(playback, "stop_audio", lambda: journal.append("playback stopped"))
    mgr = _manager(journal)
    try:
        mgr._begin_capture()
    except Exception:
        pass          # anything after the mic opens is not this test's business
    assert "playback stopped" in journal and "mic opened" in journal
    assert journal.index("playback stopped") < journal.index("sink attached") \
        < journal.index("mic opened"), \
        f"a step still playing when the mic opens is heard as the user: {journal}"


def test_the_microphone_stops_feeding_the_model_when_the_capture_ends():
    """The listener detaches the sink on stop, so nothing between questions
    - including a step being read out - is streamed."""
    from audio.ambient_listener import AmbientListener

    listener = AmbientListener(on_level=lambda rms: None, on_wake=lambda: None,
                               ambient=False)
    listener.set_chunk_sink(lambda block: None)
    listener._running = True
    listener._rec_buffer = []
    listener.stop_recording()
    assert listener._chunk_sink is None


# 3 ── a step cannot start over the user ──────────────────────────────────────

@pytest.mark.parametrize("state, busy", [
    (AppState.LISTENING, True),
    (AppState.THINKING, True),
    (AppState.IDLE, False),
])
def test_a_step_waits_while_the_user_is_talking(state, busy):
    mgr = _manager([])
    mgr._state = state
    assert mgr._guide_busy() is busy


def test_the_runner_really_waits_before_speaking_a_step():
    """Not just the predicate: the GuideRunner holds a step while busy."""
    from tutor_features.guide import GuideRunner

    busy = {"now": True}
    spoken: list[int] = []

    async def speak(idx):
        spoken.append(idx)

    async def check(step):
        return None

    async def say(_t):
        pass

    guide = SimpleNamespace(steps=[SimpleNamespace(instruction="click Next")])
    runner = GuideRunner(guide, speak_step=speak, check=check, say=say,
                         is_busy=lambda: busy["now"], on_stop=lambda: None,
                         poll_s=0.01, present_delay_s=0.0)

    async def scenario():
        await runner.start()
        await asyncio.sleep(0.3)
        held = list(spoken)
        busy["now"] = False
        await asyncio.sleep(0.5)
        runner.stop()
        await asyncio.sleep(0.05)
        return held

    held = asyncio.run(scenario())
    assert held == [], "nothing may be spoken while the user is being recorded"
    assert spoken == [0], "and it is spoken once they have finished"


# 4 ── her own voice cannot open a capture ────────────────────────────────────

@pytest.mark.parametrize("state, paused", [
    (AppState.SPEAKING, True),
    (AppState.THINKING, True),
    (AppState.LISTENING, True),
    (AppState.IDLE, False),
])
def test_the_wake_word_is_not_listened_for_while_tora_is_busy(state, paused):
    from companion_manager import CompanionManager

    mgr = CompanionManager.__new__(CompanionManager)
    mgr._listener = FakeListener([])
    mgr.sig_state_changed = SimpleNamespace(emit=lambda s: None)
    CompanionManager._emit_state(mgr, state)
    assert mgr._listener.paused is paused
