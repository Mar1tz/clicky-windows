"""LG-04: a step is always spoken - in Tora's voice, or the Windows one.

24 Sep: the Gemini TTS model allows 100 requests a day on this key. At 22:41
the limit was reached ("retry in 3h18m"), and from then on every step was
silent - "heard 80.0s after it was asked for" was step three, the first line
that played. Worse, the 429 said "exceeded your current quota, please check
your plan and billing", so it was read as billing: "your credits have run
out" when they had not, and an account warning that every other model's
next success took down again, over and over.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ai import gemini

# The error the TTS model gave at 22:41, as Google wrote it.
DAILY_LIMIT = gemini.GeminiError(429, (
    '{"error": {"code": 429, "message": "You exceeded your current quota, please check '
    'your plan and billing details. For more information on this error, head to: '
    'https://ai.google.dev/gemini-api/docs/rate-limits.\\n* Quota exceeded for metric: '
    'generativelanguage.googleapis.com/generate_requests_per_model_per_day, limit: 100, '
    'model: gemini-3.1-flash-tts\\nPlease retry in 2h56m56.705398355s.", '
    '"status": "RESOURCE_EXHAUSTED"}}'), model="gemini-3.1-flash-tts-preview")
OUT_OF_CREDIT = gemini.GeminiError(429, (
    '{"error": {"code": 429, "message": "Your prepayment credits are depleted. Please go '
    'to AI Studio at https://ai.studio/projects to manage your project and billing."}}'))


# ── what the error is ────────────────────────────────────────────────────────

def test_a_daily_limit_is_a_limit_not_out_of_credit():
    assert gemini.problem_of(DAILY_LIMIT) == "quota"
    assert gemini.problem_of(OUT_OF_CREDIT) == "billing"


def test_when_it_lifts_is_read_from_the_error():
    assert gemini.retry_after(DAILY_LIMIT) == pytest.approx(2 * 3600 + 56 * 60 + 56.7, abs=0.1)
    assert gemini.retry_after("Please retry in 41s") == 41.0
    assert gemini.retry_after('{"retryDelay": "10616s"}') == 10616.0
    assert gemini.retry_after(OUT_OF_CREDIT) is None


def test_a_limit_on_one_model_raises_no_account_warning(monkeypatch):
    """So there is nothing for the next success to take down: no flicker."""
    told = []
    monkeypatch.setattr(gemini, "_problem_listener", lambda kind, detail: told.append(kind))
    monkeypatch.setattr(gemini, "_problem_active", False)
    gemini.report_problem(DAILY_LIMIT)
    assert told == []
    gemini.report_problem(OUT_OF_CREDIT)
    assert told == ["billing"], "the real thing still is one"


# ── the Windows voice takes over ─────────────────────────────────────────────

class _Player:
    fed: list = []

    def __init__(self, sample_rate=24000):
        self.rate = sample_rate

    def open(self):
        pass

    def start(self):
        pass

    def feed(self, pcm):
        _Player.fed.append(len(pcm))

    def clear(self):
        pass

    async def drain(self):
        pass

    def stop(self):
        pass


def _tts(monkeypatch, *, error=DAILY_LIMIT):
    from audio.tts import gemini_tts
    asked = []

    async def stream_audio(model, contents, *, voice, **kw):
        asked.append(contents[0]["parts"][0]["text"])
        raise error
        yield b"", 0                                   # an async generator

    _Player.fed = []
    monkeypatch.setattr(gemini_tts.gemini, "stream_audio", stream_audio)
    monkeypatch.setattr(gemini_tts, "PcmStreamPlayer", _Player)
    monkeypatch.setattr(gemini_tts, "playback_stopped", lambda: False)
    tts = gemini_tts.GeminiTTS()
    fell = []
    tts.on_fallback = lambda kind, secs: fell.append((kind, round(secs)))
    return tts, asked, fell


def test_a_refused_line_is_said_in_the_windows_voice(monkeypatch):
    tts, asked, fell = _tts(monkeypatch)
    asyncio.run(tts.speak("Click Captions in the top navigation bar."))
    assert sum(_Player.fed) == 2 * 2400, "the whole line was played - in the Windows voice"
    assert fell == [("quota", 10617)], "told once, with how long it lasts"


def test_while_refused_lines_go_straight_to_windows(monkeypatch):
    """Not a failed request per line: the limit said 'retry in 2h56m'."""
    tts, asked, fell = _tts(monkeypatch)
    for step in ("Step one.", "Step two.", "Step three."):
        asyncio.run(tts.speak(step))
    assert len(asked) == 1, "Google was asked once, not three times"
    assert len(fell) == 1, "and the user was told once"
    assert sum(_Player.fed) == 3 * 2 * 2400


def test_a_prepared_step_line_falls_back_too(monkeypatch):
    """Walkthrough steps are prepared ahead (SpeechBuffer), not spoken on
    the spot - that is the path every silent step on 24 Sep took."""
    tts, _asked, _fell = _tts(monkeypatch)

    async def run():
        buffer = tts.prepare("Type takealot.com into the address bar.")
        await asyncio.sleep(0.05)
        await buffer.play()

    asyncio.run(run())
    assert sum(_Player.fed) == 2 * 2400


def test_a_passing_hiccup_falls_back_only_briefly(monkeypatch):
    tts, _asked, fell = _tts(monkeypatch, error=ConnectionError("getaddrinfo failed"))
    asyncio.run(tts.speak("Step one."))
    assert fell == [("network", 20)]
    assert sum(_Player.fed) == 2 * 2400, "that line was still said"


# ── the notice ───────────────────────────────────────────────────────────────

def test_the_notice_says_what_happened_and_until_when():
    from companion_manager import CompanionManager
    mgr = CompanionManager.__new__(CompanionManager)
    notices = []
    mgr.sig_agent_notice = SimpleNamespace(emit=lambda title, body: notices.append((title, body)))
    mgr._voice_fell_back("quota", 3600)
    (title, body), = notices
    assert title == "Tora's voice"
    assert "daily limit" in body and "Windows voice until about" in body
    assert "credit" not in body, "a daily limit is not out of credit"
