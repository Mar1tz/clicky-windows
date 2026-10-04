"""Turn the downloaded sound effects into the app's earcons.

The originals (Epidemic Sound, as downloaded) are MP3s and one 96 kHz
24-bit WAV with long names. The app plays sounds through QSoundEffect,
which wants plain PCM WAV and starts it with no decode delay - the whole
point of a "you're on" pop. This trims the silence in front (a pop that
starts 40 ms late feels like lag), levels them all to one peak so no
choice is louder than another, and writes 48 kHz 16-bit stereo WAVs with
plain names. Run it again after adding one:

    python assets/sounds/make_sounds.py

Needs PyAV and numpy (dev only - the app reads the WAVs).
"""

from __future__ import annotations

import wave
from pathlib import Path

import av
import numpy as np

HERE = Path(__file__).resolve().parent
RATE = 48000
PEAK = 0.5            # -6 dBFS: present, never startling
GATE = 0.01           # below this, the front is silence

# source file (as downloaded) -> the earcon's name
SOURCES = {
    "ES_User Interface, Click, UI Buttons, Bubbly, Option - Epidemic Sound.mp3": "bubble",
    "ES_User Interface, Alert, Pops, Wobble, Bloop, Pops - Epidemic Sound.mp3": "bloop",
    "ES_Notifications, User Interface 12 - Epidemic Sound.mp3": "chime",
    "ES_User Interface, Click, Digital, Video Game, Select, Positive, Notification 03 - Epidemic Sound - 0000-0329.wav": "select",
    "ES_User Interface, Click, Digital, Video Game, Select, Positive, Notification 04 - Epidemic Sound.mp3": "spark",
}


def decode(path: Path) -> np.ndarray:
    """Any file PyAV reads -> float32 stereo at RATE, shape (n, 2)."""
    with av.open(str(path)) as box:
        res = av.AudioResampler(format="flt", layout="stereo", rate=RATE)
        chunks = []
        for frame in box.decode(audio=0):
            for out in res.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1, 2))
        for out in res.resample(None):
            chunks.append(out.to_ndarray().reshape(-1, 2))
    return np.concatenate(chunks)


def tidy(x: np.ndarray) -> np.ndarray:
    loud = np.flatnonzero(np.abs(x).max(axis=1) > GATE)
    if loud.size:
        x = x[max(0, loud[0] - int(RATE * 0.002)): loud[-1] + int(RATE * 0.03)]
    fade = min(len(x), int(RATE * 0.01))                 # no click at the cut
    x[-fade:] *= np.linspace(1, 0, fade)[:, None]
    return x * (PEAK / max(1e-6, float(np.abs(x).max())))


def write(name: str, x: np.ndarray) -> None:
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(HERE / f"{name}.wav"), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())


if __name__ == "__main__":
    for source, name in SOURCES.items():
        x = tidy(decode(HERE / source))
        write(name, x)
        print(f"{name:7} {len(x) / RATE:.3f}s  <- {source[:60]}")
