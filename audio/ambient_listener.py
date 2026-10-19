"""
Always-on ambient audio listener.
Handles:
  - Continuous mic stream (single sounddevice input)
  - Energy-based VAD to detect speech segments
  - Wake-word detection (triggers on "tora" / "hey tora")
  - Push-to-talk buffering when hotkey is held
  - Live streaming of captured audio to the realtime conversation
  - Streams RMS level to UI (cursor waveform + panel)

On the wake word, and privacy: detection used to run a local whisper-tiny
model, so ambient mode never sent audio anywhere. It now asks the same
Gemini transcribe model everything else uses, which means that in AMBIENT
mode short clips of speech near the machine are sent to Google to be checked
for the wake word.

Since 19 Oct (HP-08, audio/wake_local.py) a keyword spotter hears each
segment on this PC first: "Hey Tora" wakes without anything being sent, a
bare name or a near miss is still checked by Google, and speech with nothing
like the name in it is never sent at all. TORA_WAKE_ENGINE=cloud restores
sending every segment.

The energy VAD in front of it still runs locally and is unchanged, so this
is speech segments only — never a continuous upload, and nothing at all
while the room is quiet. Hotkey mode (the default) does not open the
microphone until the hotkey is pressed and is unaffected.

Because every check is paid for, only the part of a segment that could hold
the wake word is sent: the opening ~1.8s, minus the silence that ended it,
and nothing at all for a segment with no real speech in it or while Tora is
already busy answering. See wake_clip() and the knobs below.

The transcriber is injected rather than imported: the check runs on a worker
thread, and the manager owns the event loop that the API client lives on.
Without one, wake-word detection is simply off.
"""

import logging
import queue
import threading
import time
from enum import Enum, auto
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

from audio.capture import resample_pcm, SAMPLE_RATE

_log = logging.getLogger("tora.wake")


class Mode(Enum):
    STANDBY       = auto()   # wake-word scanning
    RECORDING     = auto()   # actively buffering user utterance


# ── Tuning knobs ──────────────────────────────────────────────────────────────
BLOCK_MS           = 30              # mic callback granularity
FRAMES_PER_BLOCK   = int(SAMPLE_RATE * BLOCK_MS / 1000)
ENERGY_THRESHOLD   = 0.006           # lower = catches quieter speech
MIN_SPEECH_BLOCKS  = 3               # ~90ms of speech to start a segment
SILENCE_BLOCKS_END = 20              # ~600ms of silence ends a segment
MAX_SEGMENT_BLOCKS = 120             # ~3.6s max wake-word segment
PRE_ROLL_BLOCKS    = 18              # ~540ms of pre-roll for the wake word

# What is actually worth sending. Every wake check is a paid transcription,
# so the clip is cut down to the only part that can hold the wake word:
#   - the wake word is the FIRST thing said, so a long segment only needs
#     its opening — the rest is someone finishing a sentence;
#   - the trailing silence that ended the segment carries nothing;
#   - a segment with almost no voiced audio in it is a cough, a keystroke
#     or a door, and "Tora" takes about 400ms to say.
# Together these cut a talkative room's wake-word bill by about half
# without changing when Tora wakes.
WAKE_WINDOW_BLOCKS = 60              # ~1.8s: enough for "hey tora" + pre-roll
WAKE_TAIL_BLOCKS   = 4               # ~120ms of run-off kept after the speech
MIN_VOICED_BLOCKS  = 6               # ~180ms of voiced audio to be worth asking

# Wake phrases. A short name like "Tora" is mis-heard in fairly
# predictable ways — "Torah", "Tara", "Dora" — so the near-misses count
# as a match too. Better a rare false wake than a name that never works.
WAKE_WORDS = (
    "tora", "torah", "tara", "taura", "toura", "thora", "torra",
    "dora", "toro", "tauro", "tor a", "tore a",
    "hey tora", "hi tora", "ok tora", "yo tora",
    "hey torah", "hey tara", "hey dora",
)


def wake_clip(blocks: list[np.ndarray], silence_blocks: int) -> list[np.ndarray]:
    """The part of a captured segment that could contain the wake word.

    Drops the silence that ended the segment (keeping a short run-off so the
    last syllable is not clipped) and then keeps only the opening window.
    Pure and block-based so it can be tested without a microphone.
    """
    keep = len(blocks) - max(0, silence_blocks - WAKE_TAIL_BLOCKS)
    return blocks[:max(1, min(keep, WAKE_WINDOW_BLOCKS))]


class AmbientListener:
    """
    Single sounddevice input stream with three outputs:
      1. level callback (always): drives cursor/panel waveform
      2. wake-word callback (standby): transcribes VAD segments and matches
      3. recording buffer (recording): full PCM buffer returned on stop_recording()
    """

    def __init__(
        self,
        on_level: Callable[[float], None],
        on_wake: Callable[[], None],
        device: Optional[int] = None,
        ambient: bool = True,
        wake_transcriber: Optional[Callable[[bytes], str]] = None,
        wake_local=None,
        wake_mode: str = "hybrid",
    ):
        self._on_level = on_level
        self._on_wake = on_wake
        self._wake_transcriber = wake_transcriber
        # HP-08 (audio/wake_local.py): heard on this PC first; the cloud
        # check only for a "maybe", or for everything in "cloud" mode.
        self._wake_local = wake_local
        self._wake_mode = wake_mode
        self._device = device       # None = system default input device
        self._stream_rate = SAMPLE_RATE   # actual rate the stream opens at
        # ambient=False → the mic stays closed until start_recording(). No
        # wake-word scanning, nothing transcribed, and no microphone
        # indicator in the system tray while Tora is idle.
        self._ambient = ambient

        self._mode: Mode = Mode.STANDBY
        self._stream: Optional[sd.InputStream] = None
        self._running = False
        # PortAudio does not promise two streams can be opened at once from
        # two threads: the start-up warm-up and a press take turns.
        self._open_lock = threading.Lock()

        # Rolling pre-roll ring buffer (small)
        self._preroll: list[np.ndarray] = []
        # Current speech segment buffer (for wake-word transcription)
        self._seg_buffer: list[np.ndarray] = []
        self._seg_speech_blocks = 0
        self._seg_silence_blocks = 0
        self._seg_voiced_blocks = 0
        self._in_segment = False

        # Recording buffer (hotkey push-to-talk OR post-wake capture)
        self._rec_buffer: list[bytes] = []
        # When set, every captured block is also handed here as it arrives —
        # this is what lets the realtime conversation hear the user while
        # they are still speaking instead of after they stop.
        self._chunk_sink: Optional[Callable[[bytes], None]] = None

        self._wake_inflight = False

        # Enable/disable toggle
        self._wake_word_enabled = True
        # Paused while Tora is thinking or speaking. A wake word is refused
        # in those states anyway (only one capture may be open at a time),
        # so scanning then means paying to transcribe Tora's own voice
        # coming back through the microphone.
        self._wake_paused = False

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self):
        """Begin listening. In hotkey mode this arms the listener without
        actually opening the microphone — that happens in start_recording()."""
        if self._running:
            return
        self._running = True
        if self._ambient:
            self._open_stream()
        else:
            self.warm_up()

    def warm_up(self) -> None:
        """Make the first press after launch as quick as every other.

        Windows' first opening of a microphone in a process is slow: 0.2-1 s
        on the owner's PC (18 Oct 2026, MME) against ~50 ms afterwards - and
        in hotkey mode the device is opened on the press, so the start of
        the first question went unheard. Creating a stream and closing it
        unstarted, in the background, pays that once; nothing is recorded.
        """
        if self._ambient:
            return                      # ambient mode keeps its stream open

        def run() -> None:
            with self._open_lock:
                if self._stream is not None:
                    return              # a press got there first
                try:
                    sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                                   blocksize=FRAMES_PER_BLOCK, device=self._device).close()
                except Exception:
                    pass                # the press reports a real problem

        threading.Thread(target=run, name="mic-warm-up", daemon=True).start()

    def _open_stream(self):
        with self._open_lock:
            if self._stream is not None:
                return
            # latency="low": the device's shortest buffer - the words reach
            # the model sooner. sounddevice's default is "high".
            try:
                self._stream = sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    channels=1,
                    dtype="int16",
                    blocksize=FRAMES_PER_BLOCK,
                    callback=self._callback,
                    device=self._device,
                    latency="low",
                )
                self._stream_rate = SAMPLE_RATE
            except Exception:
                # Device doesn't support 16kHz directly — open at its native
                # rate and resample every block to the 16kHz the API expects.
                info = sd.query_devices(self._device, "input")
                native_rate = int(info["default_samplerate"])
                self._stream_rate = native_rate
                self._stream = sd.InputStream(
                    samplerate=native_rate,
                    channels=1,
                    dtype="int16",
                    blocksize=int(native_rate * BLOCK_MS / 1000),
                    callback=self._callback,
                    device=self._device,
                    latency="low",
                )
            self._stream.start()

    def stop(self):
        self._running = False
        self._close_stream()

    def _close_stream(self):
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def start_recording(self) -> None:
        """Switch to RECORDING mode; all audio buffered for STT.

        Opens the microphone first when running in hotkey mode. Raises if the
        device can't be opened so the caller can surface a real error rather
        than silently recording nothing.
        """
        self._rec_buffer = []
        self._mode = Mode.RECORDING
        if not self._running:
            self._running = True
        self._open_stream()

    def stop_recording(self) -> bytes:
        """Return buffered PCM16 bytes and resume standby."""
        pcm = b"".join(self._rec_buffer)
        self._rec_buffer = []
        self._chunk_sink = None
        self._mode = Mode.STANDBY
        self._reset_segment()
        # Hotkey mode: release the device so the mic indicator goes away and
        # nothing is being captured between questions.
        if not self._ambient:
            self._close_stream()
        return pcm

    def set_wake_word_enabled(self, enabled: bool):
        self._wake_word_enabled = enabled

    def set_wake_paused(self, paused: bool) -> None:
        """Stop/resume wake-word scanning without touching the user's setting."""
        if paused == self._wake_paused:
            return
        self._wake_paused = paused
        if paused:
            self._reset_segment()

    def set_wake_transcriber(self, fn: Optional[Callable[[bytes], str]]) -> None:
        """Supply the speech-to-text used for wake-word checks."""
        self._wake_transcriber = fn

    def set_chunk_sink(self, fn: Optional[Callable[[bytes], None]]) -> None:
        """Receive recorded audio block by block, as it is captured.

        Set before start_recording() to stream a turn live; cleared on
        stop_recording() so a stale sink cannot pick up the next question.
        """
        self._chunk_sink = fn

    def set_ambient(self, ambient: bool) -> None:
        """Switch between always-listening and hotkey-only at runtime."""
        if ambient == self._ambient:
            return
        self._ambient = ambient
        if not self._running:
            return
        if ambient:
            self._open_stream()
        elif self._mode != Mode.RECORDING:
            self._close_stream()

    @property
    def ambient(self) -> bool:
        return self._ambient

    @property
    def wake_word_enabled(self) -> bool:
        return self._wake_word_enabled

    # ── Audio callback ────────────────────────────────────────────────────────

    def _callback(self, indata: np.ndarray, frames: int, time_info, status):
        if not self._running:
            return

        pcm_int16 = indata[:, 0] if indata.ndim == 2 else indata
        if self._stream_rate != SAMPLE_RATE:
            pcm_int16 = np.frombuffer(
                resample_pcm(pcm_int16.tobytes(), self._stream_rate, SAMPLE_RATE),
                dtype=np.int16,
            )
        pcm_float = pcm_int16.astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(pcm_float ** 2)))
        self._on_level(rms)

        if self._mode == Mode.RECORDING:
            block = pcm_int16.tobytes()
            self._rec_buffer.append(block)
            sink = self._chunk_sink
            if sink is not None:
                try:
                    sink(block)
                except Exception:
                    pass   # never let a consumer kill the audio thread
            return

        # Standby: VAD-based segment capture for wake-word
        if not self._wake_word_enabled or self._wake_paused:
            return

        # Maintain tiny pre-roll
        self._preroll.append(pcm_int16.copy())
        if len(self._preroll) > PRE_ROLL_BLOCKS:
            self._preroll.pop(0)

        is_speech = rms > ENERGY_THRESHOLD

        if not self._in_segment:
            if is_speech:
                self._seg_speech_blocks += 1
                self._seg_buffer.append(pcm_int16.copy())
                if self._seg_speech_blocks >= MIN_SPEECH_BLOCKS:
                    self._in_segment = True
                    self._seg_voiced_blocks = self._seg_speech_blocks
                    # Prepend pre-roll so we catch the start of the word
                    self._seg_buffer = list(self._preroll) + self._seg_buffer
            else:
                self._seg_speech_blocks = max(0, self._seg_speech_blocks - 1)
                if not self._seg_speech_blocks:
                    self._seg_buffer = []
            return

        # In-segment
        self._seg_buffer.append(pcm_int16.copy())
        if is_speech:
            self._seg_silence_blocks = 0
            self._seg_voiced_blocks += 1
        else:
            self._seg_silence_blocks += 1

        end = (
            self._seg_silence_blocks >= SILENCE_BLOCKS_END
            or len(self._seg_buffer) >= MAX_SEGMENT_BLOCKS
        )
        if end:
            voiced = self._seg_voiced_blocks
            clip = wake_clip(self._seg_buffer, self._seg_silence_blocks)
            self._reset_segment()
            if voiced < MIN_VOICED_BLOCKS:
                return     # a cough, a keystroke, a door: not a name
            self._dispatch_wake_check(
                np.concatenate(clip).astype(np.int16).tobytes()
            )

    def _reset_segment(self):
        self._seg_buffer = []
        self._seg_speech_blocks = 0
        self._seg_silence_blocks = 0
        self._seg_voiced_blocks = 0
        self._in_segment = False

    # ── Wake-word transcription (off the audio thread) ────────────────────────

    def _dispatch_wake_check(self, pcm: bytes):
        if self._wake_inflight:
            return
        self._wake_inflight = True
        self._wake_jobs().put(pcm)

    def _wake_jobs(self) -> "queue.SimpleQueue":
        """ST-07: one worker for every wake check, made on the first. A new
        thread per speech segment came and went all day."""
        jobs = getattr(self, "_wake_queue", None)
        if jobs is None:
            jobs = self._wake_queue = queue.SimpleQueue()
            threading.Thread(target=self._wake_worker, args=(jobs,), name="wake-check", daemon=True).start()
        return jobs

    def _wake_worker(self, jobs: "queue.SimpleQueue") -> None:
        while True:
            self._check_wake(jobs.get())

    # What became of the speech segments, said every WAKE_TALLY_S: how many
    # woke Tora here, how many went to the cloud, how many were never sent.
    WAKE_TALLY_S = 1800.0

    def _tally(self, outcome: str) -> None:
        tally = getattr(self, "_wake_tally", None)
        if tally is None:
            tally = self._wake_tally = {"since": time.monotonic()}
        tally[outcome] = tally.get(outcome, 0) + 1
        if time.monotonic() - tally["since"] >= self.WAKE_TALLY_S:
            _log.info("wake word, last %d min: %d speech segments - %d woke Tora here, %d checked in the "
                      "cloud, %d never sent", (time.monotonic() - tally["since"]) // 60,
                      sum(v for k, v in tally.items() if k != "since"), tally.get("here", 0),
                      tally.get("cloud", 0), tally.get("kept", 0))
            self._wake_tally = {"since": time.monotonic()}

    def _check_wake(self, pcm: bytes):
        try:
            local = self._wake_local
            if local is not None and self._wake_mode != "cloud" and local.available:
                heard = local.hears(pcm)
                if heard == "wake":
                    _log.info("wake word heard on this PC")
                    self._tally("here")
                    self._on_wake()
                    return
                if heard is None or self._wake_mode == "local":
                    self._tally("kept")
                    return                       # nothing like "Tora": no check sent anywhere
            transcribe = self._wake_transcriber
            if transcribe is None:
                return
            self._tally("cloud")
            text = (transcribe(pcm) or "").lower().strip()
            if text and any(w in text for w in WAKE_WORDS):
                self._on_wake()
        except Exception:
            pass
        finally:
            self._wake_inflight = False
