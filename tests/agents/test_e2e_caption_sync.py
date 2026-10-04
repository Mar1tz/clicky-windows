"""The words at the top keep time with Tora's voice (2026-09-30).

Gemini Live sends an answer's text and audio together, far faster than she
speaks it - a seven-second answer arrives in about two - and says the turn
is complete while most of it is still waiting in the player. The caption
used to show the words as they arrived and leave when the turn was
complete: seconds ahead of her voice, and gone while she was still talking.
It now reveals each word as the voice reaches it and stays until the voice
stops. Nothing here plays audio or touches the network.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")

ANSWER = ("The export button is in the File menu, second item from the top. Choose it, then pick "
          "where the file should go and press Save.")


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class Voice:
    """A pretend voice: `seconds` of audio, all received at once (as the
    server sends it), heard in real time from `start`."""

    def __init__(self, seconds, start):
        self.seconds, self.start, self.now = seconds, start, start

    def __call__(self):
        heard = self.now - self.start
        return None if heard >= self.seconds else (max(0.0, heard), self.seconds)


def _island(voice):
    from ui.caption import CaptionIsland
    cap = CaptionIsland()
    cap.set_voice_clock(voice)
    return cap


def _tick(cap, voice, t):
    from PyQt6.QtCore import QRectF
    voice.now = t
    if cap.needs_screen:
        cap.set_screen_width(1920)
        cap.needs_screen = False
    if cap.active():
        cap.tick(t, QRectF(0, 0, 1920, 1040), 0.0, 0.0)


def _answer_arrives_all_at_once(cap):
    """What the live route does: every word in about a second, the turn
    complete, IDLE - while the voice has barely started."""
    cap.on_state("SPEAKING")
    for word in ANSWER.split(" "):
        cap.push(word + " ")
    cap.finish(ANSWER)
    cap.on_state("IDLE")


def test_each_word_shows_as_the_voice_reaches_it(qapp):
    from ui.caption import CAP_VOICE_LEAD_S
    t0 = time.monotonic()
    voice = Voice(8.0, t0)
    cap = _island(voice)
    _answer_arrives_all_at_once(cap)
    longest = max(len(w) for w in ANSWER.split(" ")) + 1
    for i in range(int(7.9 * 60)):
        t = t0 + i / 60.0
        _tick(cap, voice, t)
        said = min(1.0, (t - t0 + CAP_VOICE_LEAD_S) / voice.seconds) * cap._chars_in
        assert cap._chars_out <= said + longest, f"ahead of the voice at {t - t0:.2f}s"
        assert cap._chars_out >= said - longest, f"behind the voice at {t - t0:.2f}s"
    assert cap.active() and cap._state == "live", "still up while she is speaking"


def test_it_stays_until_the_voice_stops_then_lingers_and_goes(qapp):
    t0 = time.monotonic()
    voice = Voice(8.0, t0)
    cap = _island(voice)
    _answer_arrives_all_at_once(cap)
    states = {}
    for i in range(int(14 * 60)):
        t = t0 + i / 60.0
        _tick(cap, voice, t)
        states.setdefault(cap._state, t - t0)
    assert states["linger"] >= 8.0, "the linger starts when the voice stops, not when the text arrived"
    assert "hidden" in states and states["hidden"] > states["linger"] + 1.5


def test_a_long_answer_is_not_cut_off_while_she_is_still_speaking(qapp):
    t0 = time.monotonic()
    voice = Voice(26.0, t0)                  # longer than the 20 s backstop
    cap = _island(voice)
    _answer_arrives_all_at_once(cap)
    for i in range(int(25.5 * 60)):
        _tick(cap, voice, t0 + i / 60.0)
    assert cap.active() and cap._state == "live"


def test_talking_over_her_still_clears_it_at_once(qapp):
    from ui.caption import CAP_OUT_S_FAST
    t0 = time.monotonic()
    voice = Voice(8.0, t0)
    cap = _island(voice)
    _answer_arrives_all_at_once(cap)
    for i in range(120):
        _tick(cap, voice, t0 + i / 60.0)
    cap.on_state("LISTENING")
    gone = None
    for i in range(120, 240):
        _tick(cap, voice, t0 + i / 60.0)
        if not cap.active():
            gone = i / 60.0 - 2.0
            break
    assert gone is not None and gone <= CAP_OUT_S_FAST + 0.05


def test_without_her_live_voice_it_paces_itself(qapp):
    """A walkthrough line spoken by the Windows voice has no clock: the old
    words-per-second pacing, and it closes on IDLE as before."""
    t0 = time.monotonic()
    voice = Voice(0.0, t0)                   # never speaking
    cap = _island(voice)
    _answer_arrives_all_at_once(cap)
    for i in range(60):
        _tick(cap, voice, t0 + i / 60.0)
    assert cap._state in ("live", "linger") and cap._words, "revealed without waiting for a voice"


# -- the clock itself --------------------------------------------------------------------------------------

class FakePlayer:
    """Counts what is fed and plays it only when told to; never opens a device."""
    bytes_per_second = 48000
    latency = 0.0

    def __init__(self):
        self.queued = 0

    def start(self):
        pass

    def feed(self, pcm):
        self.queued += len(pcm)

    def clear(self):
        self.queued = 0

    def stop(self):
        self.queued = 0

    def play(self, seconds):
        self.queued = max(0, self.queued - int(seconds * self.bytes_per_second))

    @property
    def queued_seconds(self):
        return self.queued / self.bytes_per_second


class FakeSession:
    is_open = True
    resume_handle = None

    def __init__(self, events):
        self._events = events

    async def events(self):
        for ev in self._events:
            yield ev


def _conversation(events):
    from ai.gemini_realtime import RealtimeConversation
    conv = RealtimeConversation(tools={})
    conv._player = FakePlayer()
    conv._session = FakeSession(events)
    asyncio.run(conv._run_pump(conv._session))
    return conv


def _audio(seconds):
    return SimpleNamespace(kind="audio", audio=bytes(int(seconds * 48000)), text="")


def test_the_live_voice_reports_how_far_it_has_got():
    conv = _conversation([_audio(1.0), SimpleNamespace(kind="output_transcript", text="Hello there. "),
                          _audio(2.0), SimpleNamespace(kind="turn_complete", produced_output=True)])
    assert conv.voice_clock() == (0.0, 3.0), "the turn is complete but none of it has been heard"
    conv._player.play(1.25)
    assert conv.voice_clock() == (1.25, 3.0)
    conv._player.play(5.0)
    assert conv.voice_clock() is None, "played out: she is not speaking"


def test_a_new_answer_starts_the_clock_again():
    conv = _conversation([_audio(2.0), SimpleNamespace(kind="turn_complete", produced_output=True)])
    conv._player.play(5.0)
    conv._session = FakeSession([_audio(1.0)])
    asyncio.run(conv._run_pump(conv._session))
    assert conv.voice_clock() == (0.0, 1.0), "not 3 s: the last answer had finished"


def test_the_manager_hands_the_clock_over_and_none_without_a_voice():
    from companion_manager import CompanionManager
    mgr = SimpleNamespace(_realtime=None)
    assert CompanionManager.voice_clock(mgr) is None
    mgr._realtime = SimpleNamespace(voice_clock=lambda: (1.0, 4.0))
    assert CompanionManager.voice_clock(mgr) == (1.0, 4.0)
    mgr._realtime = SimpleNamespace(voice_clock=lambda: 1 / 0)
    assert CompanionManager.voice_clock(mgr) is None, "a caption must never break the voice"


def test_main_connects_the_clock():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "main.py").read_text(encoding="utf-8")
    # Her words are shown in the flow bar's card now (4 Oct 2026), kept in
    # time with her voice the same way; the top island is kept for later.
    assert "flow.set_voice_clock(manager.voice_clock)" in src
