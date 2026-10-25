"""What HeyTora is taking from the PC right now, for the light that says so.

WX-05 (the Massive Direction Plan): the microphone and the screen are the
two things HeyTora captures, and the user should be able to see when either
is happening. Every capture path reports here - a microphone stream when it
opens and closes, the screen on each grab - and the flow bar draws a small
light from it. Windows shows its own microphone icon; nothing shows a
screenshot, so this is the only sign of one.

It is also how a test proves nothing is captured before the user asks: the
counters only move when a stream is opened or a picture taken.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

SCREEN_SHOWN_S = 2.0          # the screen light stays this long after a grab

_lock = threading.Lock()
_listeners: list[Callable[[bool, float], None]] = []
_mics: set[str] = set()
screen_grabs = 0
mic_opens = 0
last_screen_at = 0.0


def on_change(fn: Callable[[bool, float], None]) -> None:
    """fn(mic_open, last_screen_at) on any capture thread; marshal it to the UI."""
    with _lock:
        _listeners.append(fn)


def mic(who: str, is_open: bool) -> None:
    """A microphone stream named `who` was opened (recording) or closed."""
    global mic_opens
    with _lock:
        before = bool(_mics)
        if is_open:
            _mics.add(who)
            mic_opens += 1
        else:
            _mics.discard(who)
        changed = before != bool(_mics)
    if changed:
        _tell()


def screen() -> None:
    """A picture of the screen (or part of it) was just taken."""
    global screen_grabs, last_screen_at
    with _lock:
        screen_grabs += 1
        last_screen_at = time.monotonic()
    _tell()


def mic_open() -> bool:
    with _lock:
        return bool(_mics)


def screen_recent() -> bool:
    return bool(last_screen_at) and time.monotonic() - last_screen_at < SCREEN_SHOWN_S


def _tell() -> None:
    with _lock:
        listeners, on, at = list(_listeners), bool(_mics), last_screen_at
    for fn in listeners:
        try:
            fn(on, at)
        except Exception:  # noqa: BLE001 - a light must never break a capture
            pass


def reset() -> None:
    """Tests only."""
    global screen_grabs, mic_opens, last_screen_at
    with _lock:
        _listeners.clear()
        _mics.clear()
        screen_grabs = mic_opens = 0
        last_screen_at = 0.0
