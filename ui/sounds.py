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

# The shape's own sounds, HeyClicky's way (31 Oct 2026): its own files, not
# the listening pops above (those are "her sound", Settings > Voice). Kenney's
# Interface Sounds, CC0 (assets/sounds/kenney): a swell as it grows out of
# the pill and its exact reverse as it shrinks back, a warm tap into
# Settings and a soft glass one back, and a tick between tabs and pages. Quieter than the
# listening pop - they mark a move, they don't ask for attention.
# Settings > Voice > Interface sounds (cfg switch "ui_sounds").
UI = {"open": "ui_open", "close": "ui_close", "settings": "ui_settings", "back": "ui_back",
      "tab": "ui_tab"}
UI_VOLUME = 0.28
# Opening and closing a touch under the rest (31 Oct: "the load in is too
# much"); a tab's tick only just there.
UI_VOLUMES = {"open": 0.2, "close": 0.2, "tab": 0.14}

_effects: dict = {}


def _effect(key: str, volume: float = VOLUME):
    slot = (key, volume)
    if slot not in _effects:
        try:
            from PyQt6.QtCore import QUrl
            from PyQt6.QtMultimedia import QSoundEffect
            path = tokens.assets_dir() / "sounds" / f"{key}.wav"
            if not path.exists():
                _effects[slot] = None
            else:
                fx = QSoundEffect()
                fx.setSource(QUrl.fromLocalFile(str(path)))
                fx.setVolume(volume)
                _effects[slot] = fx
        except Exception:  # noqa: BLE001 - no multimedia backend: no sound
            _effects[slot] = None
    return _effects[slot]


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


def ui(event: str) -> bool:
    """One of the shape's moves ("open", "settings"), if interface sounds
    are on. False when off or it cannot play."""
    try:
        from config import cfg
        if not cfg.switch("ui_sounds"):
            return False
    except Exception:  # noqa: BLE001
        return False
    key = UI.get(event)
    fx = _effect(key, UI_VOLUMES.get(event, UI_VOLUME)) if key else None
    if fx is None:
        return False
    try:
        fx.stop()
        fx.play()
        return True
    except Exception:  # noqa: BLE001
        return False


def preload_ui() -> None:
    """Load the shape's sounds when it is made: the first one played after
    a cold start otherwise woke the sound system in the middle of a move."""
    for event, key in UI.items():
        _effect(key, UI_VOLUMES.get(event, UI_VOLUME))
