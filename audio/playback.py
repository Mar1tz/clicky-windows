"""
Shared audio playback — everything Tora says comes out through here.

Three ways in, depending on where the audio came from:

  play_pcm_async()   one finished clip of raw PCM16. The cascade voice
                     (gemini-3.1-flash-tts-preview) returns
                     "audio/l16; rate=24000" — headerless samples, so there
                     is nothing to decode and playback starts immediately.

  PcmStreamPlayer    audio that is still arriving. The realtime conversation
                     speaks in websocket frames while it is still thinking of
                     the rest of the sentence; this opens the output device
                     once and feeds frames into it as they land, so the first
                     word is heard about a second before the last one exists.

  play_mp3_async()   compressed audio, decoded via PyAV.

All three honour stop_audio(), a module-level Event the manager flips on Esc
or on the next hotkey press, so a cancelled answer goes quiet within ~50 ms
instead of playing on to the end of its buffer.
"""

import asyncio
import io
import threading
import time
from typing import Optional

import numpy as np
import sounddevice as sd
import av


# Single global flag — flipping this stops every active playback in this process
_stop_event = threading.Event()


def stop_audio() -> None:
    """Cancel any in-progress playback immediately. Safe to call from any thread."""
    _stop_event.set()
    try:
        sd.stop()                # boots the underlying PortAudio stream
    except Exception:
        pass


def _arm_audio() -> None:
    """Reset the stop flag at the start of a new playback."""
    _stop_event.clear()


def decode_mp3_to_pcm(mp3_bytes: bytes) -> tuple[np.ndarray, int]:
    """Decode MP3 (or any container PyAV supports) to float32 mono PCM."""
    container = av.open(io.BytesIO(mp3_bytes))
    stream = container.streams.audio[0]
    sample_rate = stream.rate

    chunks = []
    resampler = av.audio.resampler.AudioResampler(
        format="flt", layout="mono", rate=sample_rate
    )

    for frame in container.decode(stream):
        resampled = resampler.resample(frame)
        for rf in resampled:
            arr = rf.to_ndarray().flatten()
            chunks.append(arr)

    container.close()

    if not chunks:
        return np.zeros(0, dtype=np.float32), sample_rate

    pcm = np.concatenate(chunks).astype(np.float32)
    return pcm, sample_rate


def _blocking_play_chunked(pcm: np.ndarray, sr: int) -> None:
    """Play PCM through an OutputStream, polling _stop_event between blocks
    so cancellation takes effect within ~50 ms instead of 'when the buffer
    runs out'."""
    if pcm.size == 0:
        return

    block = max(1, int(sr * 0.05))    # 50 ms blocks
    pcm = pcm.reshape(-1, 1) if pcm.ndim == 1 else pcm

    try:
        with sd.OutputStream(samplerate=sr, channels=1, dtype="float32") as stream:
            i = 0
            while i < len(pcm):
                if _stop_event.is_set():
                    return
                end = min(i + block, len(pcm))
                stream.write(pcm[i:end])
                i = end
    except Exception:
        # Fallback: play everything in one shot. Less responsive to stop, but
        # never silently fails on weird devices.
        try:
            sd.play(pcm.flatten(), samplerate=sr)
            # Poll the stop event during wait
            while sd.get_stream().active:
                if _stop_event.is_set():
                    sd.stop()
                    return
                sd.sleep(50)
        except Exception:
            pass


async def play_pcm_async(pcm_bytes: bytes, sample_rate: int = 24000) -> None:
    """Play raw PCM16 mono. Cancellable via stop_audio().

    No decode step — the samples go straight to the device, which is the
    whole point of asking the TTS model for l16 rather than a container.
    """
    if not pcm_bytes:
        return

    _arm_audio()
    pcm = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    if pcm.size == 0:
        return

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _blocking_play_chunked, pcm, sample_rate)


class PcmStreamPlayer:
    """An open output device that plays PCM16 frames as they arrive.

    The realtime model sends speech in pieces over the websocket. Waiting for
    the last piece before playing the first would throw away the latency the
    Live API exists to save, so this keeps one stream open and a callback
    pulls from a buffer that fills underneath it.

    Underruns are filled with silence rather than treated as the end: the
    gap between two websocket frames is normal and must not close the device
    mid-sentence.
    """

    def __init__(self, sample_rate: int = 24000, block: int = 1200):
        self._rate = sample_rate
        self._block = block
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._stream: Optional[sd.RawOutputStream] = None
        self._done = threading.Event()

    def start(self) -> None:
        # Re-arm every time a turn's playback begins, even when the device is
        # already open. The stop flag is a process-wide Event: the manager
        # sets it (via stop_audio) at the start of every capture to kill any
        # leftover speech. On the FIRST turn the stream is opened here and
        # _arm_audio clears it, so audio plays. On later turns the stream is
        # already open, so an early return would skip the clear — the flag
        # stays set from that capture and the callback silently outputs
        # silence for the whole answer. That was "turn 1 speaks, every turn
        # after shows its text but says nothing." Clearing it here fixes it.
        _arm_audio()
        self._done.clear()
        if self._stream is not None:
            return
        try:
            self._stream = sd.RawOutputStream(
                samplerate=self._rate, channels=1, dtype="int16",
                blocksize=self._block, callback=self._callback,
            )
            self._stream.start()
        except Exception:
            self._stream = None

    def _callback(self, outdata, frames, time_info, status) -> None:
        need = frames * 2      # int16 mono
        with self._lock:
            have = min(need, len(self._buf))
            chunk = bytes(self._buf[:have])
            del self._buf[:have]
        if _stop_event.is_set():
            outdata[:] = bytes(need)
            self._done.set()
            return
        outdata[:have] = chunk
        if have < need:
            outdata[have:] = bytes(need - have)
            self._done.set()
        else:
            self._done.clear()

    def feed(self, pcm_bytes: bytes) -> None:
        if not pcm_bytes:
            return
        if self._stream is None:
            self.start()
        with self._lock:
            self._buf.extend(pcm_bytes)
        self._done.clear()

    def clear(self) -> None:
        """Drop everything queued but keep the device open.

        This is what a barge-in needs: the user has started talking over the
        answer, so the rest of that answer must stop immediately while the
        session stays live for the reply.
        """
        with self._lock:
            self._buf.clear()
        self._done.set()

    @property
    def queued_seconds(self) -> float:
        with self._lock:
            return len(self._buf) / (self._rate * 2)

    async def drain(self, timeout: float = 120.0) -> None:
        """Wait until the buffer has played out (or stop_audio fires)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if _stop_event.is_set():
                return
            with self._lock:
                empty = not self._buf
            if empty:
                # Let the device finish the block it is already holding.
                await asyncio.sleep(self._block / self._rate)
                with self._lock:
                    if not self._buf:
                        return
            await asyncio.sleep(0.02)

    def stop(self) -> None:
        self.clear()
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.abort()
                stream.close()
            except Exception:
                pass


async def play_mp3_async(mp3_bytes: bytes) -> None:
    """Decode and play MP3 audio asynchronously. Cancellable via stop_audio()."""
    if not mp3_bytes:
        return

    _arm_audio()

    loop = asyncio.get_running_loop()
    pcm, sr = await loop.run_in_executor(None, decode_mp3_to_pcm, mp3_bytes)
    if pcm.size == 0:
        return

    if _stop_event.is_set():
        return  # cancelled while we were decoding

    await loop.run_in_executor(None, _blocking_play_chunked, pcm, sr)
