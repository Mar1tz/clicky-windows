"""The Windows voice: what Tora speaks with when Google will not (LG-04).

Every walkthrough step is spoken by the Gemini TTS model, and that model
allows 100 requests a day on a key. On 24 September the limit was reached at
22:41, and every step after it was silent - "guide step one heard 80.0s after
it was asked for" was step THREE, the first line that played. A walkthrough
whose steps cannot be heard is only half a walkthrough.

SAPI is on every Windows PC. It is local and free, and it renders a line in
about 0.1 s, after about 1 s to start the first time. It renders into
memory as 24 kHz 16-bit mono - the same audio Gemini sends - so the line
plays through the same stream as any other, and Esc or talking over it
stops it the same way. It does not sound like Tora; it is heard, which is
the point.

COM objects belong to the thread that made them, so they all live on one
worker thread of their own.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from typing import Optional

_log = logging.getLogger("tora.tts")

RATE = 24_000
_SAFT24kHz16BitMono = 26          # SpeechAudioFormatType


class WindowsVoice:
    def __init__(self) -> None:
        self._pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="windows-voice")
        self._voice = None
        self._format = None
        self._broken = False

    def _render(self, text: str) -> bytes:
        """On the voice's own thread: the line as PCM16, 24 kHz, mono."""
        import comtypes
        import comtypes.client as cc
        if self._voice is None:
            comtypes.CoInitialize()
            self._voice = cc.CreateObject("SAPI.SpVoice")
            self._format = cc.CreateObject("SAPI.SpAudioFormat")
            self._format.Type = _SAFT24kHz16BitMono
        stream = cc.CreateObject("SAPI.SpMemoryStream")
        stream.Format = self._format
        self._voice.AudioOutputStream = stream
        self._voice.Speak(text, 0)                 # 0: synchronous, to memory
        return bytes(stream.GetData())

    async def render(self, text: str) -> tuple[bytes, int]:
        """(pcm16, 24000) for `text`, or (b"", 0) if Windows cannot say it."""
        text = (text or "").strip()
        if not text or self._broken:
            return b"", 0
        loop = asyncio.get_running_loop()
        try:
            pcm = await loop.run_in_executor(self._pool, self._render, text)
        except Exception:  # noqa: BLE001 - no voice at all is the only other option
            _log.warning("the Windows voice could not speak either", exc_info=True)
            self._broken = True
            return b"", 0
        return pcm, RATE


_voice: Optional[WindowsVoice] = None


def windows_voice() -> WindowsVoice:
    global _voice
    if _voice is None:
        _voice = WindowsVoice()
    return _voice
