"""Earcons: the little sound when Tora starts listening.

Holding the shortcut and hearing nothing is the moment people wonder
whether it worked. A short pop the instant she is listening says "go
ahead" without looking - Wispr Flow's is the reference. The user picks
which one (or none) in Tora > Sounds; the files are assets/sounds/*.wav,
made by assets/sounds/make_sounds.py.

QSoundEffect, not the audio stack Tora speaks through: it is preloaded,
plays PCM with no decode, and starts in a few milliseconds - a pop that
lands late feels like lag. Everything here fails quietly: no sound is
never worth an error.
"""

from __future__ import annotations

from typing import Optional

from ui import tokens

# key, the name a person sees - the first is the default.
SOUNDS = (
    ("bloop", "Bloop"),
    ("bubble", "Bubble"),
    ("select", "Select"),
    ("spark", "Spark"),
    ("chime", "Chime"),
)
OFF = "off"
VOLUME = 0.55

_effects: dict = {}


def _effect(key: str):
    if key not in _effects:
        try:
            from PyQt6.QtCore import QUrl
            from PyQt6.QtMultimedia import QSoundEffect
            path = tokens.assets_dir() / "sounds" / f"{key}.wav"
            if not path.exists():
                _effects[key] = None
            else:
                fx = QSoundEffect()
                fx.setSource(QUrl.fromLocalFile(str(path)))
                fx.setVolume(VOLUME)
                _effects[key] = fx
        except Exception:  # noqa: BLE001 - no multimedia backend: no sound
            _effects[key] = None
    return _effects[key]


def preload(key: str) -> None:
    """Load it ahead of the first press, so the first pop is on time."""
    if key and key != OFF:
        _effect(key)


def play(key: Optional[str]) -> bool:
    """Play an earcon by key; False when it is off or cannot play."""
    if not key or key == OFF:
        return False
    fx = _effect(key)
    if fx is None:
        return False
    try:
        fx.stop()
        fx.play()
        return True
    except Exception:  # noqa: BLE001
        return False
