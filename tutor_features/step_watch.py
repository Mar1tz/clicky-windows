"""
Step completion detected from what the user DID — not from a screen state
the planner guessed in advance.

Why this exists. The previous engine asked the planner to predict, for every
step, what the screen would look like afterwards ("opera.exe is the active
window", "title contains Visual Studio Code") and then polled for that.
The live log showed the failure plainly: those predictions were routinely
TRUE BEFORE THE USER DID ANYTHING, because they described the app the user
was already in. The runner's skip-guard correctly refused to fire on a
signal that was already true — and so the step could never complete, the
stuck timer fired, the instruction was read out again, and the user was told
to "say next". That is the whole "it repeats things" complaint.

A signal that is already true cannot tell you anything happened. So this
module watches for the two things that only happen when the user acts:

  1. A CLICK INSIDE THE POINTED TARGET. The guide already resolved the
     control's exact bounds in order to point at it. A left-button-down
     inside that box is the user doing the step. Instant, exact, free.

  2. A MATERIAL CHANGE ON SCREEN, judged by the vision model. A cheap
     fingerprint (window title, process, focused control, a 48px grayscale
     thumbnail) is taken when the step is shown and polled. Only when it
     changes and then settles is an after-screenshot taken and the fast model
     asked one question: given the step, the screen BEFORE and the screen
     AFTER, did the user do it? This covers typing, scrolling, drag, menus —
     anything a click test cannot name.

The planner's own expectation is kept as a third signal, but only the parts
of it that were FALSE when the step was shown. An expectation is several
independent signals and they do not all go stale together: a plan written
inside Chrome says "process: chrome.exe" (true before the user moves) AND
"url_contains: youtube" (the actual outcome). Discarding the whole thing
because of the first one threw away a free, instant answer on four steps out
of five and left every step polling the vision model — see
ai.verification.ArmedExpectation, which mutes the already-true signals and
keeps watching the rest.

Nothing here touches Qt. The watcher runs on the manager's asyncio loop; the
click poller is a daemon thread that does read-only Win32 queries.
"""

from __future__ import annotations

import asyncio
import collections
import ctypes
import logging
import threading
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

_log = logging.getLogger("tora.stepwatch")

# ── Tunables ─────────────────────────────────────────────────────────────────
CLICK_POLL_S      = 0.02    # GetAsyncKeyState cadence; a click is never shorter
CLICK_PAD_PX      = 6.0     # slack around the target box, logical px
CLICK_SETTLE_S    = 0.40    # let the UI react before the next step points
FP_CHEAP_EVERY_S  = 0.30    # title / process / focus
FP_THUMB_EVERY_S  = 1.00    # screen thumbnail (~68 ms each on this laptop)
THUMB_W, THUMB_H  = 48, 27
THUMB_DIFF_MIN    = 10.0    # mean abs gray delta (0-255) that counts as change
THUMB_SKIP_TOP    = 0.09    # ignore the top 9% — the caption island lives there
SETTLE_S          = 0.60    # change must hold still this long before judging
JUDGE_MIN_GAP_S   = 2.0     # never ask the model more often than this
# A step where the APP is working ("wait for the captions to generate"). A
# progress bar changes the screen every second, and every settled change was
# a vision call asking "did the user do it?". The screen only goes still when
# the work is finished, so a long settle means: ask once, at the end.
WAIT_SETTLE_S     = 3.0
WAIT_JUDGE_GAP_S  = 8.0
JUDGE_TIMEOUT_S   = 9.0
JUDGE_MIN_CONF    = 0.70
# After this many "not done yet" judgements in a row - each on a screen that
# HAD changed - the watcher is unsure: something is clearly happening, the
# vision model keeps saying it is not the step, and silence until the
# minute-long nudge is the worst response. The guide asks instead.
UNSURE_AFTER      = 2
LOCAL_EVERY_S     = 0.90

_VK_LBUTTON = 0x01


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


try:
    _user32 = ctypes.windll.user32
    _user32.GetAsyncKeyState.restype = ctypes.c_short
    _user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _user32.GetCursorPos.argtypes = [ctypes.c_void_p]
    _user32.GetForegroundWindow.restype = ctypes.c_void_p
    _user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
except Exception:          # not Windows
    _user32 = None


# ── Click poller ─────────────────────────────────────────────────────────────

class ClickPoller:
    """Records left-button-down transitions with the cursor position.

    No hook is installed: GetAsyncKeyState is a read-only query, so this can
    neither interfere with input nor be blocked by UIPI when an elevated app
    has focus. One shared instance; started on first use, idles when nothing
    is watching.
    """

    def __init__(self) -> None:
        self._events: collections.deque = collections.deque(maxlen=64)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if _user32 is None or (self._thread is not None and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="tora-clicks", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def drain(self) -> list[tuple[int, int, float]]:
        with self._lock:
            out = list(self._events)
            self._events.clear()
        return out

    def _run(self) -> None:
        was_down = False
        pt = _POINT()
        while not self._stop.is_set():
            try:
                down = bool(_user32.GetAsyncKeyState(_VK_LBUTTON) & 0x8000)
                if down and not was_down:
                    if _user32.GetCursorPos(ctypes.byref(pt)):
                        with self._lock:
                            self._events.append((pt.x, pt.y, time.monotonic()))
                was_down = down
            except Exception:
                pass
            time.sleep(CLICK_POLL_S)


_clicks = ClickPoller()


# ── Fingerprint ──────────────────────────────────────────────────────────────

@dataclass
class Fingerprint:
    title: str = ""
    pid: int = 0
    focused: str = ""
    thumb: Optional[bytes] = None       # THUMB_W*THUMB_H grayscale

    def what_changed(self, other: "Fingerprint") -> Optional[str]:
        """Name of the first material difference from `other`, or None."""
        if self.pid != other.pid and self.pid and other.pid:
            return "process"
        if self.title != other.title:
            return "window title"
        if self.focused != other.focused:
            return "focus"
        if self.thumb and other.thumb and len(self.thumb) == len(other.thumb):
            n = len(self.thumb)
            diff = sum(abs(a - b) for a, b in zip(self.thumb, other.thumb)) / n
            if diff >= THUMB_DIFF_MIN:
                return f"screen ({diff:.0f})"
        return None

    def same_as(self, other: "Fingerprint") -> bool:
        return self.what_changed(other) is None


def _foreground_pid() -> int:
    try:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return 0
        pid = ctypes.c_uint32(0)
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)
    except Exception:
        return 0


def read_cheap() -> Fingerprint:
    """Title + process + focused control. ~1-5 ms."""
    title = ""
    focused = ""
    try:
        from tutor import active_window_title
        title = active_window_title() or ""
    except Exception:
        pass
    try:
        from ai.hybrid_pointer import focused_element
        f = focused_element()
        if f:
            ftype, fname, _pw = f
            focused = f"{(ftype or '').replace('Control', '')}:{(fname or '')[:60]}".lower()
    except Exception:
        pass
    return Fingerprint(title=title, pid=_foreground_pid(), focused=focused)


def _active_monitor_index() -> int:
    """mss monitor index (1-based) under the foreground window, else 1."""
    try:
        import mss
        from tutor import foreground_window_center
        centre = foreground_window_center()
        with mss.mss() as sct:
            mons = sct.monitors[1:]
            if centre is not None:
                cx, cy = centre
                for i, m in enumerate(mons, start=1):
                    if (m["left"] <= cx < m["left"] + m["width"]
                            and m["top"] <= cy < m["top"] + m["height"]):
                        return i
    except Exception:
        pass
    return 1


def grab_screen(max_width: int = 1280, *, jpeg: bool = True) -> tuple[str, bytes]:
    """(base64 JPEG of the active monitor, grayscale thumbnail bytes).

    One capture serves both the judge and the fingerprint. jpeg=False skips
    the encode for the periodic thumbnail poll. Blocking — call via
    asyncio.to_thread.
    """
    import base64
    import io
    import mss
    from PIL import Image

    idx = _active_monitor_index()
    with mss.mss() as sct:
        mon = sct.monitors[idx] if idx < len(sct.monitors) else sct.monitors[1]
        raw = sct.grab(mon)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

    # Thumbnail first, from the full frame, with the caption strip cut off so
    # Tora's own island appearing at the top does not read as the user acting.
    w, h = img.size
    top = int(h * THUMB_SKIP_TOP)
    thumb = img.crop((0, top, w, h)).convert("L").resize(
        (THUMB_W, THUMB_H), Image.Resampling.BILINEAR)
    thumb_bytes = thumb.tobytes()

    if not jpeg:
        return "", thumb_bytes
    if w > max_width:
        img = img.resize((max_width, int(h * max_width / w)), Image.Resampling.BILINEAR)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=72)
    return base64.b64encode(buf.getvalue()).decode("ascii"), thumb_bytes


# ── The watcher ──────────────────────────────────────────────────────────────

JudgeFn = Callable[[str, str, str], Awaitable[Optional[dict]]]


class StepWatcher:
    """One per guide step. begin() when the step is shown, poll() from the
    runner, end() when the step is over. poll() returns True once the user
    has done the step, None while watching, and latches True."""

    def __init__(
        self,
        instruction: str,
        *,
        judge: JudgeFn,
        dpi_scale: float = 1.0,
        local_check: Optional[Callable[[], object]] = None,
        target_bbox: Optional[tuple[float, float, float, float]] = None,
        fallback_check: Optional[Callable[[], object]] = None,
        waiting: bool = False,
    ) -> None:
        self.instruction = instruction
        self._judge = judge
        self._scale = dpi_scale or 1.0
        self._local_check = local_check
        # Used only if nothing in local_check survives arming - see
        # ai.verification.TargetGone.
        self._fallback_check = fallback_check
        self.waiting = waiting
        self._settle_s = WAIT_SETTLE_S if waiting else SETTLE_S
        self._judge_gap_s = WAIT_JUDGE_GAP_S if waiting else JUDGE_MIN_GAP_S
        self.target_bbox = target_bbox          # logical px; may be set later

        self._base: Optional[Fingerprint] = None
        self._base_shot: str = ""
        self._last: Optional[Fingerprint] = None
        self._stable_since = 0.0
        # When the screen last changed during this step (0: not yet). The
        # guide looks the NEXT step's target up once it settles (LG-06).
        self.changed_at = 0.0
        self._cheap_at = 0.0
        self._thumb_at = 0.0
        self._local_at = 0.0
        self._local_armed = False
        self._judge_task: Optional[asyncio.Task] = None
        self._judge_at = 0.0
        self._judge_result: Optional[bool] = None
        self._done = False
        self._done_at = 0.0
        self.done_by = ""
        self._began = False
        # How many times the vision model was asked about this step. Each is
        # a screenshot upload and two to four seconds; GW-11 wants it rare,
        # and GW-03 logs it so "rare" can be checked.
        self.judged = 0
        # Consecutive "not done yet" answers, and the last reason given.
        self.not_yet = 0
        self.last_reason = ""

    # ── lifecycle ──

    async def begin(self) -> None:
        if self._began:
            return
        self._began = True
        _clicks.start()
        _clicks.drain()                     # clicks before the step are not the step
        try:
            shot, thumb = await asyncio.to_thread(grab_screen)
        except Exception:
            _log.debug("baseline capture failed", exc_info=True)
            shot, thumb = "", None
        fp = await asyncio.to_thread(read_cheap)
        fp.thumb = thumb
        now = time.monotonic()
        self._base = fp
        self._base_shot = shot
        self._last = fp
        self._stable_since = now
        self._cheap_at = self._thumb_at = now

        # The planner's expectation is only useful if it can go false -> true.
        # An ArmedExpectation mutes the individual signals that are already
        # true and keeps the rest; anything else is all-or-nothing.
        if self._local_check is None:
            await self._arm_fallback("the step has no signal of its own")
            return
        if self._local_check is not None:
            arm = getattr(self._local_check, "arm", None)
            if callable(arm):
                try:
                    kept = await asyncio.to_thread(arm)
                except Exception:
                    kept = []
                muted = getattr(self._local_check, "muted", set())
                if kept:
                    _log.info("watching %s (%s already true at step start)",
                              ", ".join(kept), ", ".join(sorted(muted)) or "nothing")
                    self._local_armed = True
                    return
                self._local_check = None
                if await self._arm_fallback(
                        "every expectation signal (%s) was already true at step "
                        "start" % (", ".join(sorted(muted)) or "none given")):
                    return
                _log.info("every expectation signal (%s) was already true at "
                          "step start — watching clicks and the screen instead",
                          ", ".join(sorted(muted)) or "none given")
                return
            try:
                v = await asyncio.to_thread(self._local_check)
                already = bool(getattr(v, "done", False))
            except Exception:
                already = False
            if already:
                _log.info("step expectation already satisfied at step start — "
                          "ignoring it; watching clicks and screen changes instead")
                self._local_check = None
            else:
                self._local_armed = True

    async def _arm_fallback(self, why: str) -> bool:
        """Swap in the last-resort finish line, if it can arm. True if it did."""
        fallback = self._fallback_check
        if fallback is None:
            return False
        try:
            kept = await asyncio.to_thread(fallback.arm)
        except Exception:  # noqa: BLE001
            kept = []
        if not kept:
            return False
        _log.info("%s — watching the target disappear instead", why)
        self._local_check = fallback
        self._local_armed = True
        return True

    def _to_logical(self, px: float, py: float) -> tuple[float, float]:
        """A click from the mouse hook (physical px) in the overlay's logical
        px, on the monitor it was on - each has its own scaling. Only when
        Windows cannot list the monitors, the scaling this watcher was given."""
        from screen import monitors
        mons = monitors.monitors()
        if mons:
            return monitors.to_logical(px, py, mons)
        return px / self._scale, py / self._scale

    def end(self) -> None:
        t, self._judge_task = self._judge_task, None
        if t is not None and not t.done():
            t.cancel()
        # A shortcut the step registered with the keyboard hook (LG-08)
        # must not outlive the step.
        close = getattr(self._local_check, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                _log.debug("closing the step's local check failed", exc_info=True)

    # ── polling ──

    async def poll(self) -> Optional[bool]:
        if self._done:
            return True
        if not self._began:
            await self.begin()
        now = time.monotonic()

        # 1. Click inside the pointed target.
        bbox = self.target_bbox
        for px, py, t in _clicks.drain():
            if bbox is None:
                continue
            lx, ly = self._to_logical(px, py)
            l, tp, r, b = bbox
            if (l - CLICK_PAD_PX <= lx <= r + CLICK_PAD_PX
                    and tp - CLICK_PAD_PX <= ly <= b + CLICK_PAD_PX):
                if not self._done_at:
                    self._done_at = t
                    self.done_by = "click on target"
        if self._done_at and now - self._done_at >= CLICK_SETTLE_S:
            return self._finish()

        # 2. The planner's expectation, if it was false at step start.
        if now - self._local_at >= LOCAL_EVERY_S and await self._local_done(now):
            return self._finish()

        # 3. Something changed and settled -> ask the eyes, once.
        if self._judge_result is True:
            return self._finish()
        if self._done_at:
            return None                     # a click is already settling
        fp = await self._fingerprint(now)
        if fp is not None:
            if self._last is None or not fp.same_as(self._last):
                if self._last is not None:
                    self.changed_at = now
                self._last = fp
                self._stable_since = now
            elif (self._base is not None and fp.what_changed(self._base)
                    and now - self._stable_since >= self._settle_s
                    and self._judge_task is None
                    and now - self._judge_at >= self._judge_gap_s):
                # Cheapest first. A settled change is exactly when the step's
                # own signal is most likely to have just gone true - and the
                # local check only runs every LOCAL_EVERY_S, so without this
                # the vision model (settle 0.6 s) was often asked about a
                # change a free UIA read would have confirmed 0.3 s later.
                if await self._local_done(now):
                    return self._finish()
                why = fp.what_changed(self._base)
                self._judge_at = now
                self._judge_task = asyncio.create_task(self._ask(fp, why))
        return None

    async def _local_done(self, now: float) -> bool:
        """Run the step's own signal once. True if it says the step is done."""
        if not (self._local_armed and self._local_check is not None):
            return False
        self._local_at = now
        try:
            v = await asyncio.to_thread(self._local_check)
        except Exception:
            _log.debug("local check failed", exc_info=True)
            return False
        if getattr(v, "done", False):
            self.done_by = (f"expectation: {getattr(v, 'method', '')} "
                            f"({getattr(v, 'detail', '')})")
            return True
        return False

    @property
    def unsure(self) -> bool:
        """The screen keeps changing and the vision model keeps saying no."""
        return not self._done and self.not_yet >= UNSURE_AFTER

    def _finish(self) -> bool:
        self._done = True
        _log.info("step done — %s", self.done_by)
        return True

    async def _fingerprint(self, now: float) -> Optional[Fingerprint]:
        if now - self._cheap_at < FP_CHEAP_EVERY_S:
            return None
        self._cheap_at = now
        fp = await asyncio.to_thread(read_cheap)
        if now - self._thumb_at >= FP_THUMB_EVERY_S:
            self._thumb_at = now
            try:
                _shot, thumb = await asyncio.to_thread(grab_screen, 640, jpeg=False)
                fp.thumb = thumb
            except Exception:
                fp.thumb = self._last.thumb if self._last else None
        else:
            fp.thumb = self._last.thumb if self._last else None
        return fp

    async def _ask(self, fp: Fingerprint, why: str) -> None:
        """Capture the after-shot and let the model compare it with before."""
        try:
            after, thumb = await asyncio.to_thread(grab_screen)
            fp.thumb = thumb
            _log.info("screen changed (%s) — asking whether the step is done", why)
            self.judged += 1
            res = await asyncio.wait_for(
                self._judge(self.instruction, self._base_shot, after),
                timeout=JUDGE_TIMEOUT_S,
            )
            done = bool(res and res.get("done")) and \
                float((res or {}).get("confidence", 1.0)) >= JUDGE_MIN_CONF
            reason = (res or {}).get("why", "")
            if done:
                self.done_by = f"vision: {reason}"
                self._judge_result = True
                self.not_yet = 0
            else:
                self.not_yet += 1
                self.last_reason = reason
                _log.info("not done yet (%s) — rebasing", reason or "no reason given")
                # This state has been judged; do not ask about it again.
                self._base = fp
                self._base_shot = after
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _log.info("judge unavailable (%s) — rebasing", e)
            self._base = fp
        finally:
            self._judge_task = None
