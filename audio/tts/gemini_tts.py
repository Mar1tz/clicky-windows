"""
The cascaded fallback voice — gemini-3.1-flash-tts-preview.

Not the voice normally heard. The realtime conversation speaks in its own
voice straight out of the websocket; this synthesises a finished sentence
into audio and is used when the realtime route is unavailable, when a custom
voice is wanted, or for the replies Tora produces without asking a model at
all (journal summaries, lesson steps, "that was the last step").

The model returns raw PCM16 at 24 kHz — "audio/l16; rate=24000" — so there is
no container to decode before playback starts. It is streamed, and played
from the first piece that arrives, through one output stream that stays open
between lines (GW-08).
"""

from __future__ import annotations

import asyncio
import time
import logging
import os
from typing import Optional

from ai import gemini
from ai.gemini import Models
from audio.playback import PcmStreamPlayer, playback_stopped
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

# How long the output stream stays open after the last line. Long enough to
# span the gaps inside a walkthrough; short enough that a device that went
# away (headphones switched off) is reopened, not held dead.
IDLE_CLOSE_S = 20.0


class _Output:
    """The one output stream Tora's own voice plays through.

    A newly opened stream only starts asking for audio 0.24 s after it is
    opened - 0.97 s for the first one after launch - on this machine's MME
    output. Opening one per line put that silence in front of every line
    Tora said, and closing it straight after the last piece clipped the end
    of the last word (the device still held 0.2 s). Kept open, a line starts
    one block (50 ms) after its first piece and ends when it ends.

    The lock keeps two lines from interleaving in the one buffer - they used
    to play over each other instead.
    """

    def __init__(self):
        self._player: Optional[PcmStreamPlayer] = None
        self._rate = 0
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock: Optional[asyncio.Lock] = None
        self._closer: Optional[asyncio.TimerHandle] = None

    def _bind(self) -> None:
        # A lock and a timer belong to one loop. The manager has one, but the
        # setup wizard and tests run their own, so rebind when it changes.
        loop = asyncio.get_running_loop()
        if loop is not self._loop:
            self._loop, self._lock, self._closer = loop, asyncio.Lock(), None

    def lock(self) -> asyncio.Lock:
        self._bind()
        return self._lock

    def player(self, rate: int) -> PcmStreamPlayer:
        self._bind()
        if self._closer is not None:
            self._closer.cancel()
            self._closer = None
        if self._player is None or rate != self._rate:
            if self._player is not None:
                self._player.stop()
            self._player, self._rate = PcmStreamPlayer(sample_rate=rate), rate
        return self._player

    def close_later(self) -> None:
        self._bind()
        if self._closer is not None:
            self._closer.cancel()
        self._closer = self._loop.call_later(IDLE_CLOSE_S, self.close)

    def close(self) -> None:
        self._closer = None
        if self._player is not None:
            self._player.stop()
            self._player = None


class GeminiTTS(BaseTTS):
    """Text to speech via the Gemini TTS model."""

    # How long a refusal sends lines to the Windows voice (LG-04), when
    # Google does not say. A daily limit says ("retry in 2h56m"); a key or
    # billing problem is tried again every ten minutes, so a fix is noticed.
    REFUSED_FOR_S = {"quota": 3600.0, "billing": 600.0, "key": 600.0,
                     "busy": 20.0, "network": 20.0}

    def __init__(self, voice: str = DEFAULT_VOICE):
        self._voice = voice if voice in VOICES else DEFAULT_VOICE
        self._out = _Output()
        # While Google is refusing this model, lines go straight to the
        # Windows voice instead of failing one by one.
        self._fallback_until = 0.0
        # Told once per refusal: (kind, seconds it will last). Set by the app.
        self.on_fallback = None

    def set_voice(self, voice: str) -> None:
        if voice and voice in VOICES:
            self._voice = voice

    @property
    def voice(self) -> str:
        return self._voice

    def _stream(self, text: str):
        return gemini.stream_audio(
            Models.TTS,
            [{"role": "user", "parts": [{"text": f"{_STYLE} {text}"}]}],
            voice=self._voice,
        )

    def gemini_ok(self) -> bool:
        """May the Gemini voice be asked right now?"""
        return time.monotonic() >= self._fallback_until

    def note_refused(self, error) -> None:
        """Google refused a line. Until it is likely to answer again, the
        Windows voice speaks - and the app is told once, not per line."""
        from ai import gemini
        kind = gemini.problem_of(error)
        secs = gemini.retry_after(error) if kind == "quota" else None
        secs = secs or self.REFUSED_FOR_S.get(kind, 0.0)
        if secs <= 0:
            return                      # this line only: try Gemini again next time
        fresh = self.gemini_ok()
        self._fallback_until = time.monotonic() + secs
        if fresh:
            _log.warning("Tora's voice: Google refused the speech model (%s) - "
                         "the Windows voice speaks for the next %s", kind,
                         f"{secs / 60:.0f} min" if secs >= 60 else f"{secs:.0f} s")
            if self.on_fallback is not None:
                try:
                    self.on_fallback(kind, secs)
                except Exception:  # noqa: BLE001 - a notice must not stop the line
                    _log.debug("on_fallback failed", exc_info=True)

    def warm(self, rate: int = 24000) -> None:
        """Open the output now, because a line is about to be said - a
        walkthrough was just asked for, and step one follows in seconds."""
        self._out.player(rate).open()
        self._out.close_later()

    async def synthesize(self, text: str) -> tuple[bytes, int]:
        """(pcm16, sample_rate) for `text`. (b"", 0) when there is nothing
        to say or the call failed — a silent answer beats a crash mid-lesson.

        Collected from the stream: the whole line arrives in ~2.1 s that way,
        against 3.3-4.1 s asked for in one piece (GW-08)."""
        text = (text or "").strip()
        if not text:
            return b"", 0
        from audio.tts.windows_voice import windows_voice
        if not self.gemini_ok():
            return await windows_voice().render(text)
        pcm, rate = bytearray(), 0
        try:
            async for piece, piece_rate in self._stream(text):
                pcm.extend(piece)
                rate = rate or piece_rate
        except Exception as e:
            _log.warning("TTS failed: %s", e)
            self.note_refused(e)
            return await windows_voice().render(text)
        return bytes(pcm), rate

    def prepare(self, text: str) -> "SpeechBuffer":
        """Start synthesising `text` now, to be played later - see
        SpeechBuffer. Needs a running event loop."""
        return SpeechBuffer(self, (text or "").strip())

    async def speak(self, text: str, *, on_start=None) -> None:
        """Say `text`, starting the moment the first piece of audio arrives.

        A whole clip had to be synthesised before a sound was played - 3.3 to
        4.1 s of silence before every walkthrough step and every local reply.
        Streamed, the first word is heard after ~0.9 s. stop_audio() still
        silences it at once, and ends the download too. `on_start` is called
        as the first sound goes out - which is what "how fast" is measured to.
        """
        text = (text or "").strip()
        if text:
            await self.prepare(text).play(on_start=on_start)


class SpeechBuffer:
    """A line being synthesised in the background, playable before it has
    finished arriving.

    Preparing speech early used to mean synthesising it whole, and when the
    moment to speak came first, synthesising it again on the spot - the same
    line twice. This downloads once: played early, it streams from the first
    piece; played late, it starts at once from what is already here (GW-08).
    """

    def __init__(self, tts: GeminiTTS, text: str):
        self._out = tts._out
        self._pieces: list[bytes] = []
        self._rate = 0
        self._done = not text
        self._grew = asyncio.Event()
        self._task = asyncio.ensure_future(self._fill(tts, text)) if text else None

    async def _fill(self, tts: GeminiTTS, text: str) -> None:
        try:
            if not tts.gemini_ok():
                await self._fill_windows(text)
                return
            async for piece, rate in tts._stream(text):
                self._pieces.append(piece)
                self._rate = self._rate or rate
                self._grew.set()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - silence beats a crash mid-lesson
            _log.warning("TTS failed: %s", e)
            tts.note_refused(e)
            if not self._pieces:
                # Nothing of it arrived: say the whole line another way,
                # rather than not at all (LG-04).
                await self._fill_windows(text)
        finally:
            self._done = True
            self._grew.set()

    async def _fill_windows(self, text: str) -> None:
        from audio.tts.windows_voice import windows_voice
        pcm, rate = await windows_voice().render(text)
        if pcm:
            self._rate = rate
            step = rate // 5 * 2                     # 0.2 s of PCM16 per piece
            for i in range(0, len(pcm), step):
                self._pieces.append(pcm[i:i + step])
            self._grew.set()

    def cancel(self) -> None:
        """Stop downloading - nobody is going to hear it."""
        if self._task is not None and not self._task.done():
            self._task.cancel()

    async def play(self, *, on_start=None) -> None:
        async with self._out.lock():
            player = None
            fed = 0
            try:
                while True:
                    while fed < len(self._pieces):
                        if player is None:
                            player = self._out.player(self._rate or 24000)
                            player.start()
                            if on_start is not None:
                                try:
                                    on_start()
                                except Exception:  # noqa: BLE001 - a measurement
                                    _log.debug("on_start failed", exc_info=True)
                        elif playback_stopped():
                            break
                        player.feed(self._pieces[fed])
                        fed += 1
                    if self._done or (player is not None and playback_stopped()):
                        break
                    self._grew.clear()
                    if fed < len(self._pieces):
                        continue                 # a piece landed while clearing
                    await self._grew.wait()
                if player is not None:
                    if not playback_stopped():
                        await player.drain()
                    if playback_stopped():
                        # Cut off (the user spoke): what is still queued must
                        # not play before the NEXT line, on this same stream.
                        player.clear()
            finally:
                self.cancel()        # played or abandoned: the download is done with
                self._out.close_later()
