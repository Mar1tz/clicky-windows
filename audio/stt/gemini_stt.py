"""
Dictation — speech to text via gemini-3.5-transcribe-live.

This is the cascade's ear, used when the realtime conversation route is
unavailable, and it is also what Tora uses for plain transcription jobs.
The conversation on the hotkey does not come through here: gemini-3.8-live
hears the user directly, so routing a live session's audio through a second
model would only add a hop.

The model is a Live one — transcription happens over the same websocket as
everything else rather than by uploading a file, so partial text starts
arriving while the clip is still being sent. The server's own VAD reports
ACTIVITY_END the moment it hears the utterance finish, which is a cleaner
end-of-speech signal than any local silence timer and lets this return
without waiting out a grace period.
"""

from __future__ import annotations

import asyncio
import logging

from audio.capture import apply_noise_gate, normalize_audio, trim_silence
from audio.stt.base_stt import BaseSTT
from ai.gemini import Models
from ai.gemini_live import INPUT_RATE, LiveSession
from config import cfg

_log = logging.getLogger("tora.stt")

# The server has heard the whole clip and said the speech ended; anything
# still in flight arrives well inside this.
_TAIL_GRACE_S = 1.2
# The whole clip is uploaded in one go and audioStreamEnd sent, so the server
# has everything it will ever get. If no transcript has arrived a few seconds
# after that, there was no speech in the clip. This used to be 20 s, and a
# quiet or clipped recording sat on "Thinking..." for the full twenty before
# coming back empty (seen in the log: 1.8 s of audio, 21 s to an empty
# transcript).
_NO_SPEECH_S = 3.5
_HARD_TIMEOUT_S = 12.0


def _setup() -> dict:
    setup: dict = {
        "generationConfig": {"responseModalities": ["TEXT"]},
        "inputAudioTranscription": {},
    }
    # A pinned language stops the transcriber drifting between languages on
    # code-mixed speech. Empty means auto-detect, which is the default.
    if cfg.stt_language:
        setup["inputAudioTranscription"] = {
            "languageCodes": [cfg.stt_language]
        }
    return setup


class GeminiSTT(BaseSTT):
    """Speech to text over the Live API. No local model, no download."""

    async def transcribe(self, pcm_bytes: bytes, sample_rate: int = INPUT_RATE) -> str:
        if not pcm_bytes:
            return ""

        # Same conditioning the local engines got: gate the room tone, trim
        # the dead air at both ends, and level a quiet mic.
        pcm = normalize_audio(apply_noise_gate(trim_silence(pcm_bytes)))
        if len(pcm) < 1600:  # under 50 ms of speech — nothing was said
            return ""

        try:
            return await asyncio.wait_for(
                self._run(pcm), timeout=_HARD_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            _log.warning("transcription timed out after %.0fs", _HARD_TIMEOUT_S)
            return ""

    async def _run(self, pcm: bytes) -> str:
        pieces: list[str] = []
        session = LiveSession(Models.STT, _setup())
        await session.connect()
        try:
            reader = session.events().__aiter__()

            await session.send_audio(pcm)
            await session.audio_stream_end()

            speech_ended = False
            while True:
                # Once the server says the speech is over, only wait out the
                # short tail. Before that, allow for the model still chewing
                # through a long clip.
                budget = _TAIL_GRACE_S if speech_ended else _NO_SPEECH_S
                try:
                    ev = await asyncio.wait_for(reader.__anext__(), timeout=budget)
                except (asyncio.TimeoutError, StopAsyncIteration):
                    break

                if ev.kind == "input_transcript":
                    pieces.append(ev.text)
                elif ev.kind == "text":
                    # TEXT-modality models answer in modelTurn parts too;
                    # either channel carries the transcript.
                    pieces.append(ev.text)
                elif ev.kind == "voice_activity" and ev.text == "ACTIVITY_END":
                    speech_ended = True
                elif ev.kind in ("turn_complete", "closed"):
                    break
        finally:
            await session.close()

        return "".join(pieces).strip()
