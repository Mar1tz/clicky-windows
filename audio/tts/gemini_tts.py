"""
The cascaded fallback voice — gemini-3.1-flash-tts-preview.

Not the voice normally heard. The realtime conversation speaks in its own
voice straight out of the websocket; this synthesises a finished sentence
into audio and is used when the realtime route is unavailable, when a custom
voice is wanted, or for the replies Tora produces without asking a model at
all (journal summaries, lesson steps, "that was the last step").

The model returns raw PCM16 at 24 kHz — "audio/l16; rate=24000" — so there is
no container to decode before playback starts.
"""

from __future__ import annotations

import logging
import os

from ai import gemini
from ai.gemini import Models
from audio.playback import play_pcm_async
from audio.tts.base_tts import BaseTTS

_log = logging.getLogger("tora.tts")

# Gemini's prebuilt voices are language-agnostic: each one speaks whatever
# language the text is written in, so a reply in German needs no voice swap,
# only text in German. Zephyr is bright and quick, which suits a tutor.
DEFAULT_VOICE = os.getenv("GEMINI_TTS_VOICE", "").strip() or "Zephyr"

VOICES = (
    "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda",
    "Orus", "Aoede", "Callirrhoe", "Autonoe", "Enceladus", "Iapetus",
)

_STYLE = "Say this warmly and clearly, like a patient tutor, at a brisk pace:"


class GeminiTTS(BaseTTS):
    """Text to speech via the Gemini TTS model."""

    def __init__(self, voice: str = DEFAULT_VOICE):
        self._voice = voice if voice in VOICES else DEFAULT_VOICE

    def set_voice(self, voice: str) -> None:
        if voice and voice in VOICES:
            self._voice = voice

    @property
    def voice(self) -> str:
        return self._voice

    async def synthesize(self, text: str) -> tuple[bytes, int]:
        """(pcm16, sample_rate) for `text`. (b"", 0) when there is nothing
        to say or the call failed — a silent answer beats a crash mid-lesson."""
        text = (text or "").strip()
        if not text:
            return b"", 0
        try:
            resp = await gemini.generate(
                Models.TTS,
                [{"role": "user", "parts": [{"text": f"{_STYLE} {text}"}]}],
                max_tokens=8192,
                temperature=1.0,
                audio_voice=self._voice,
                timeout=60.0,
            )
        except Exception as e:
            _log.warning("TTS failed: %s", e)
            return b"", 0
        return gemini.first_audio(resp)

    async def speak(self, text: str) -> None:
        pcm, rate = await self.synthesize(text)
        if pcm:
            await play_pcm_async(pcm, rate)
