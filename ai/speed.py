"""
Speed, measured: one line per question from the key, and a day's scorecard.

Wave 0 of the speed review (17 Oct 2026): every number in that review had
to be dug out of tora.log by hand, and the moments before
the first transcript - the microphone opening, the session opening, the
question going out - were not in it at all. Now each question leaves

    turn timeline: live, mic 48 ms, held 2.6s, sent +0.9s, first word +1.3s
    (session waited 0.0s), answered

measured from the key coming up (the end of the question), and every 20
questions a running scorecard for the day:

    speed today: 40 questions, first word p50 1.3s p90 2.9s, look p50 3.1s
    p90 5.0s, 2 slow route, 0 unanswered, 1.2 sessions a question,
    loop lag p99 12 ms, idle cpu 0.4%

The companion marks the moments (mark); the realtime conversation counts
the sessions it opens (note_session); a watcher on the event loop reports
how late the loop wakes (loop_lag); pcinfo-style process times give the CPU.
Pure bookkeeping: nothing here can slow a question down or fail one.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

_log = logging.getLogger("tora.speed")

STARTED = time.monotonic()            # as near the process start as an import gets
SCORECARD_EVERY = 20                  # questions between scorecard lines


@dataclass
class _Turn:
    route: str = "live"
    at: dict = field(default_factory=dict)        # moment -> monotonic seconds
    notes: dict = field(default_factory=dict)


@dataclass
class _Day:
    day: date = field(default_factory=date.today)
    questions: int = 0
    first_word: list = field(default_factory=list)
    look: list = field(default_factory=list)
    slow_route: int = 0
    unanswered: int = 0
    interrupted: int = 0
    sessions: int = 0
    lag_ms: list = field(default_factory=list)
    cpu: Optional[float] = None


_lock = threading.Lock()
_turn: Optional[_Turn] = None
_today = _Day()


def _now() -> float:
    return time.monotonic()


def begin(route: str = "live") -> None:
    """The key went down (or the wake word, or a click): a new question."""
    global _turn
    with _lock:
        if _turn is not None and "done" not in _turn.at:
            _finish_locked("interrupted")
        _turn = _Turn(route=route, at={"press": _now()})


def mark(what: str, **notes) -> None:
    """A moment of this question, the first time it happens: mic, release,
    sent, heard, audio, look. Notes ride along for the line (waited=...)."""
    with _lock:
        t = _turn
        if t is None or "done" in t.at:
            return
        t.at.setdefault(what, _now())
        t.notes.update(notes)


def route(name: str) -> None:
    """The question changed route: "slow" when it went the cascade's way."""
    with _lock:
        if _turn is not None:
            _turn.route = name


def finish(outcome: str = "answered") -> None:
    """The question is over: answered, unanswered, or nothing said."""
    with _lock:
        _finish_locked(outcome)


def _finish_locked(outcome: str) -> None:
    global _turn
    t, _turn = _turn, None
    if t is None or "press" not in t.at:
        return
    t.at["done"] = _now()
    if outcome == "nothing said":
        return
    _roll_day()
    d = _today
    d.questions += 1
    if t.route == "slow":
        d.slow_route += 1
    if outcome == "unanswered":
        d.unanswered += 1
    elif outcome == "interrupted":
        d.interrupted += 1
    end = t.at.get("release", t.at["press"])
    parts = [t.route]
    if "mic" in t.at:
        parts.append("mic %d ms" % ((t.at["mic"] - t.at["press"]) * 1000))
    if "release" in t.at:
        parts.append("held %.1fs" % (t.at["release"] - t.at["press"]))
    if "sent" in t.at:
        parts.append("sent +%.1fs" % (t.at["sent"] - end))
    if "audio" in t.at:
        first = t.at["audio"] - end
        parts.append("first word +%.1fs" % first)
        d.first_word.append(first)
    if "look" in t.at:
        look = t.at["look"] - end
        parts.append("look +%.1fs" % look)
        d.look.append(look)
    for k, v in t.notes.items():
        parts.append(f"{k} {v}")
    parts.append(outcome)
    _log.info("turn timeline: %s", ", ".join(parts))
    if d.questions % SCORECARD_EVERY == 0:
        _log.info("speed today: %s", _scorecard_locked())


_voice_ready_told = False


def note_session() -> None:
    """A voice session was opened (each is a pass and a handshake). The
    first one says how long after start-up the voice was ready (ST-02)."""
    global _voice_ready_told
    with _lock:
        _roll_day()
        _today.sessions += 1
        first, _voice_ready_told = not _voice_ready_told, True
    if first:
        _log.info("voice ready %.1fs after HeyTora started", time.monotonic() - STARTED)


def note_lag(ms: float) -> None:
    with _lock:
        _roll_day()
        _today.lag_ms.append(ms)
        if len(_today.lag_ms) > 20_000:
            del _today.lag_ms[:10_000]


def note_cpu(percent: float) -> None:
    with _lock:
        _today.cpu = percent


def scorecard() -> str:
    with _lock:
        return _scorecard_locked()


def log_scorecard() -> None:
    """At shutdown: the day so far, if anything was asked."""
    with _lock:
        if _today.questions:
            _log.info("speed today: %s", _scorecard_locked())


def _roll_day() -> None:
    global _today
    if _today.day != date.today():
        if _today.questions:
            _log.info("speed on %s: %s", _today.day.isoformat(), _scorecard_locked())
        _today = _Day()


def _pct(xs: list, q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def _scorecard_locked() -> str:
    d = _today
    bits = [f"{d.questions} questions"]
    if d.first_word:
        bits.append("first word p50 %.1fs p90 %.1fs" % (_pct(d.first_word, 0.5), _pct(d.first_word, 0.9)))
    if d.look:
        bits.append("look p50 %.1fs p90 %.1fs" % (_pct(d.look, 0.5), _pct(d.look, 0.9)))
    bits.append(f"{d.slow_route} slow route, {d.unanswered} unanswered, {d.interrupted} interrupted")
    if d.questions:
        bits.append("%.1f sessions a question" % (d.sessions / d.questions))
    if d.lag_ms:
        bits.append("loop lag p99 %d ms" % _pct(d.lag_ms, 0.99))
    if d.cpu is not None:
        bits.append("idle cpu %.1f%%" % d.cpu)
    return ", ".join(bits)


# -- the event loop and the CPU ----------------------------------------------------------------------------------

LAG_EVERY_S = 0.5
LAG_WARN_MS = 100.0


# What the loop was doing when it stalled - caught WHILE it is stuck, by a
# thread of its own: the lag above is only known once the stall is over,
# and on 19 Oct a 1.3 s stall left nothing to say what it was.
STALL_AFTER_S = LAG_EVERY_S + 0.3
STALL_CHECK_S = 0.2
_beat = 0.0
_loop_thread: Optional[int] = None
_stall_watch: Optional[threading.Thread] = None


def _stack_line(frame, depth: int = 8) -> str:
    import traceback
    frames = traceback.extract_stack(frame)[-depth:]
    return " <- ".join(f"{f.name} ({f.filename.replace(chr(92), '/').split('/')[-1]}:{f.lineno})"
                       for f in reversed(frames))


def stall_report() -> str:
    """The loop thread's stack, and every other thread's top frame."""
    import sys
    frames = sys._current_frames()
    names = {t.ident: t.name for t in threading.enumerate()}
    loop = frames.get(_loop_thread or -1)
    parts = [f"loop: {_stack_line(loop) if loop is not None else '?'}"]
    for ident, frame in frames.items():
        if ident != _loop_thread and ident != threading.get_ident():
            parts.append(f"{names.get(ident, ident)}: {_stack_line(frame, 2)}")
    return " | ".join(parts)


def _watch_stalls() -> None:
    reported = 0.0
    while True:
        time.sleep(STALL_CHECK_S)
        beat = _beat
        if beat and time.perf_counter() - beat > STALL_AFTER_S and beat != reported:
            reported = beat
            _log.warning("the event loop is stuck (%.1fs): %s", time.perf_counter() - beat, stall_report())


async def watch_loop(sleep) -> None:
    """Run on the companion's loop: how late does it wake? Anything that
    blocks the loop - disk, COM, a big encode - delays the voice, and shows
    here first. `sleep` is asyncio.sleep (passed in to keep this importable
    without a loop)."""
    global _beat, _loop_thread, _stall_watch
    _loop_thread = threading.get_ident()
    if _stall_watch is None:
        _stall_watch = threading.Thread(target=_watch_stalls, name="stall-watch", daemon=True)
        _stall_watch.start()
    try:
        while True:
            asked = time.perf_counter()
            _beat = asked
            await sleep(LAG_EVERY_S)
            late = (time.perf_counter() - asked - LAG_EVERY_S) * 1000
            note_lag(max(0.0, late))
            if late > LAG_WARN_MS:
                _log.warning("the event loop was %d ms late - something blocked it", late)
    finally:
        _beat = 0.0                         # stopped, not stuck


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


def process_cpu_seconds() -> Optional[float]:
    """This process's user + kernel CPU time, from Windows. None elsewhere."""
    try:
        k32 = ctypes.windll.kernel32
        create, exit_, kernel, user = _FileTime(), _FileTime(), _FileTime(), _FileTime()
        if not k32.GetProcessTimes(k32.GetCurrentProcess(), ctypes.byref(create), ctypes.byref(exit_),
                                   ctypes.byref(kernel), ctypes.byref(user)):
            return None
        ticks = ((kernel.high << 32) | kernel.low) + ((user.high << 32) | user.low)
        return ticks / 1e7
    except Exception:  # noqa: BLE001 - not Windows, or no answer
        return None


CPU_EVERY_S = 600.0


async def watch_cpu(sleep, idle) -> None:
    """Every CPU_EVERY_S: the share of one core this process used, counted
    only over stretches where `idle()` held at both ends - what Tora costs
    a PC while nobody is talking to her. The first minute (start-up) is
    left out."""
    await sleep(60)
    while True:
        a, was_idle = process_cpu_seconds(), idle()
        start = time.monotonic()
        await sleep(CPU_EVERY_S)
        b = process_cpu_seconds()
        if a is None or b is None or not (was_idle and idle()):
            continue
        percent = 100.0 * (b - a) / max(1e-6, time.monotonic() - start)
        note_cpu(percent)
        _log.info("idle cpu: %.1f%% of one core over %d min", percent, CPU_EVERY_S // 60)
