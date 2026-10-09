"""FS-04 (docs/FAST_AND_RIGHT.md): the question goes out at once.

_realtime_stream built the screen line - an accessibility walk, 0.2-0.5 s on
the owner's PC and seconds on a slow one - before it sent any audio, so Google
heard every question that much late. The audio flows from the first block
now; the line follows when it is ready, before the question is closed.
"""
from __future__ import annotations

import asyncio
import time

import companion_manager as cm


class Conv:
    ready = True

    def __init__(self, log):
        self.log = log

    async def send_context(self, line):
        self.log.append(("context", line, time.monotonic()))

    async def push_audio(self, pcm):
        self.log.append(("audio", pcm, time.monotonic()))

    async def end_user_audio(self):
        self.log.append(("end", None, time.monotonic()))


def _run(context_s: float, speaking_s: float = 0.4):
    m = cm.CompanionManager.__new__(cm.CompanionManager)
    log = []
    m._realtime = Conv(log)
    m._rt_audio = [b"first-words"]
    m._rt_streaming = True
    m.CONTEXT_WAIT_S = 0.3

    async def screen_context():
        await asyncio.sleep(context_s)
        return "[context] Screen: \"Checkout\""
    m._screen_context = screen_context

    async def go():
        stream = asyncio.ensure_future(m._realtime_stream())
        await asyncio.sleep(speaking_s / 2)
        m._rt_audio.append(b"more-words")
        await asyncio.sleep(speaking_s / 2)
        m._rt_streaming = False                          # the hotkey came up
        await stream
    t0 = time.monotonic()
    asyncio.run(go())
    return [(kind, round(t - t0, 2)) for kind, _what, t in log]


def test_the_voice_goes_out_before_a_slow_screen_line():
    events = _run(context_s=0.25)
    kinds = [k for k, _t in events]
    assert kinds[0] == "audio", "the first words are not held back for the screen line"
    assert kinds.index("context") < kinds.index("end"), "the line still arrives before the question closes"
    assert next(t for k, t in events if k == "audio") < 0.1, "at once"


def test_a_very_late_screen_line_is_not_waited_for():
    events = _run(context_s=5.0)
    kinds = [k for k, _t in events]
    assert "context" not in kinds and kinds[-1] == "end"
    assert events[-1][1] < 0.4 + 0.3 + 0.2, "the answer goes ahead after CONTEXT_WAIT_S"
