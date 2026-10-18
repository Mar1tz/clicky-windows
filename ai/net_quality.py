"""
Is the connection slow right now? So Tora can go lighter on it.

Asked for on 18 Oct 2026: make HeyTora work for people with bad reception
and slow Wi-Fi. Two signs, both read from work Tora is doing anyway - never
a speed test of its own:

  * the voice's upload falls behind the microphone: bytes the websocket has
    taken but not yet sent (gemini_realtime.push_audio);
  * a voice session takes more than a few seconds to open
    (companion_manager._await_session).

Either marks the connection slow for SLOW_FOR_S. While it is, a screen look
sends a smaller picture (companion_manager._tool_look_at_screen): about half
the bytes, which on a weak uplink is the difference between one second and
several. Pure state - safe from any thread.
"""

from __future__ import annotations

import logging
import time

_log = logging.getLogger("tora.net")

SLOW_FOR_S = 180.0
# Bytes waiting in the voice socket: about 0.4 s of microphone audio (each
# 100 ms frame is ~4.4 KB once base64'd and wrapped in JSON).
SLOW_BACKLOG_BYTES = 16_000
# A voice session (pass + handshake) normally opens in ~1.5 s (1-17 Oct).
SLOW_OPEN_S = 4.0

_slow_until = 0.0


def note_slow(why: str) -> None:
    """The connection showed it is slow: go lighter for a while."""
    global _slow_until
    now = time.monotonic()
    if now >= _slow_until:
        _log.info("slow connection (%s) - lighter screen looks for %d min", why, SLOW_FOR_S // 60)
    _slow_until = now + SLOW_FOR_S


def slow() -> bool:
    return time.monotonic() < _slow_until


def reset() -> None:
    """Back to normal (tests)."""
    global _slow_until
    _slow_until = 0.0
