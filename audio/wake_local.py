"""
The wake word, heard on this PC (HP-08, 19 Oct 2026).

Every speech segment near the microphone used to go to Google to be
transcribed and searched for "Tora": 1,889 checks in the first half of
October, each a voice pass, a websocket and up to 3.5 s - and twice an hour
one sat 10 s and timed out. A small keyword spotter (sherpa-onnx, a 5 MB
int8 zipformer trained on 10,000 hours of English; Apache 2.0) listens
instead, in two passes over each segment:

    "Hey / Hi / Okay / OK Tora"  wake now - nothing leaves the PC. On 132
                                 clips of Windows' voices: 27 of 30 heard,
                                 no false wakes in 90 other phrases.
    "Tora", "Torah", "Tara" ...  maybe - the cloud check decides, as before
                                 (a bare name is too like "Torah" and
                                 "Toronto" to trust alone).
    nothing like it              no check at all: 73 of those 90 phrases
                                 never left the PC.

The keywords are tokenized already (assets/wake/*.txt), so the tokenizer is
not shipped. Missing package or model: `available` is False and the listener
asks the cloud as it always did. TORA_WAKE_ENGINE picks: "hybrid" (this, the
default), "local" (wake only on the two-word phrases, never the cloud) or
"cloud" (as before).
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from typing import Optional

import numpy as np

_log = logging.getLogger("tora.wake")

RATE = 16000
TAIL = np.zeros(int(0.66 * RATE), dtype=np.float32)   # the spotter needs to hear the word end


def model_dir() -> Path:
    base = getattr(sys, "_MEIPASS", None)
    return (Path(base) if base else Path(__file__).resolve().parents[1]) / "assets" / "wake"


class LocalWake:
    """A keyword spotter for "Tora". Thread-safe; loaded on first use."""

    def __init__(self, folder: Optional[Path] = None) -> None:
        self._folder = Path(folder) if folder else model_dir()
        self._kws = None
        self._maybe = ""
        self._failed = False
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        if self._failed:
            return False
        try:
            import sherpa_onnx  # noqa: F401
        except Exception:  # noqa: BLE001 - not installed: the cloud check, as before
            return False
        return (self._folder / "encoder.int8.onnx").is_file() and (self._folder / "wake.txt").is_file()

    def _spotter(self):
        if self._kws is None and not self._failed:
            try:
                import sherpa_onnx
                f = self._folder
                self._kws = sherpa_onnx.KeywordSpotter(
                    tokens=str(f / "tokens.txt"), encoder=str(f / "encoder.int8.onnx"),
                    decoder=str(f / "decoder.int8.onnx"), joiner=str(f / "joiner.int8.onnx"),
                    num_threads=1, keywords_file=str(f / "wake.txt"), provider="cpu")
                self._maybe = "/".join(line.strip() for line in
                                       (f / "maybe.txt").read_text(encoding="utf-8").splitlines() if line.strip())
            except Exception:  # noqa: BLE001 - a broken model: the cloud check, as before
                _log.warning("the local wake word could not load - using the cloud check", exc_info=True)
                self._failed = True
        return self._kws

    def hears(self, pcm16: bytes) -> Optional[str]:
        """"wake", "maybe" or None for one segment of 16 kHz mono int16."""
        with self._lock:
            kws = self._spotter()
            if kws is None:
                return "maybe"                     # cannot tell: let the cloud decide
            x = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
            if self._run(kws, x):
                return "wake"
            if self._maybe and self._run(kws, x, self._maybe):
                return "maybe"
            return None

    @staticmethod
    def _run(kws, x: np.ndarray, keywords: str = "") -> list[str]:
        stream = kws.create_stream(keywords) if keywords else kws.create_stream()
        stream.accept_waveform(RATE, x)
        stream.accept_waveform(RATE, TAIL)
        stream.input_finished()
        heard = []
        while kws.is_ready(stream):
            kws.decode_stream(stream)
            word = kws.get_result(stream)
            if word:
                heard.append(word)
                kws.reset_stream(stream)
        return heard
