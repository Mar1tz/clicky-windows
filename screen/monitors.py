"""Every monitor, in both kinds of pixel Tora meets, and the one correct way
between them.

Windows - UI Automation, mss screenshots, the mouse hook - talks in PHYSICAL
pixels across the whole desktop. Qt - the overlay, QCursor - talks in LOGICAL
pixels. With more than one monitor those are not "physical divided by the
scaling": each monitor has its own scaling, and Qt 6 keeps a monitor's
top-left corner where Windows has it and scales only from there:

    logical = the monitor's corner + (physical - the monitor's corner) / its scaling

Dividing by one scaling for the whole desktop - what the app did everywhere -
is right on a main monitor at (0, 0) and wrong on every other one. On a
second 125% monitor to the right of the main one it put Tora's arrow a
quarter of a screen to the left, on the wrong monitor; on a 100% monitor
beside a 125% one it was off by a fifth of its position.

The UI thread publishes the logical rectangles Qt really uses (publish), so
the conversion follows Qt exactly; until it has, the rule above is applied.
They are matched to monitors by their top-left corner, not by name: Qt names
a screen after the monitor model when it can ("DELL SE2426HG", "ARZOPA"),
and only the others by device (\\\\.\\DISPLAY1). Measured on 30 Sep with a
100% Dell in the middle and a 125% screen either side, Qt had exactly the
rule above. Qt-free and safe from any thread.
"""
from __future__ import annotations

import ctypes
import threading
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Monitor:
    name: str                       # GDI device name, e.g. \\.\DISPLAY1 - the same as QScreen.name()
    left: int                       # physical px, on the whole desktop
    top: int
    width: int
    height: int
    scale: float = 1.0              # its own scaling: 1.25 at 125%
    primary: bool = False
    work: tuple = ()                # physical (left, top, width, height) without the taskbar
    logical: tuple = field(default=())   # (left, top, width, height) as Qt has it

    def __post_init__(self):
        if not self.logical:
            s = self.scale or 1.0
            object.__setattr__(self, "logical", (float(self.left), float(self.top),
                                                 self.width / s, self.height / s))

    def contains(self, x: float, y: float) -> bool:
        return self.left <= x < self.left + self.width and self.top <= y < self.top + self.height

    def contains_logical(self, x: float, y: float) -> bool:
        lx, ly, lw, lh = self.logical
        return lx <= x < lx + lw and ly <= y < ly + lh

    def to_logical(self, x: float, y: float) -> tuple[float, float]:
        lx, ly, lw, lh = self.logical
        return lx + (x - self.left) * lw / self.width, ly + (y - self.top) * lh / self.height

    def to_physical(self, x: float, y: float) -> tuple[float, float]:
        lx, ly, lw, lh = self.logical
        return self.left + (x - lx) * self.width / lw, self.top + (y - ly) * self.height / lh


_lock = threading.Lock()
_published: list[tuple] = []


def publish(rects: list[tuple]) -> None:
    """The UI thread's record of Qt's own logical screen rectangles, as
    (x, y, width, height). Called at start and whenever a monitor is added,
    removed or changes."""
    with _lock:
        _published[:] = [tuple(float(v) for v in r) for r in rects]


def monitors() -> list[Monitor]:
    """The monitors now. Empty only when Windows cannot say (then every
    conversion is the identity). Tests replace this with a fixed layout."""
    with _lock:
        rects = list(_published)
    return _with_qt(_enumerate(), rects)


def _with_qt(found: list[Monitor], rects: list[tuple]) -> list[Monitor]:
    """Each monitor with Qt's rectangle for it - the one with the same
    top-left corner - or the rule when Qt has none."""
    out = []
    for m in found:
        qt = next((r for r in rects if abs(r[0] - m.left) <= 1 and abs(r[1] - m.top) <= 1), ())
        out.append(Monitor(m.name, m.left, m.top, m.width, m.height, m.scale, m.primary, m.work, qt))
    return out


def monitor_at(x: float, y: float, mons: Optional[list[Monitor]] = None) -> Optional[Monitor]:
    """The monitor a PHYSICAL point is on, or the nearest one."""
    mons = monitors() if mons is None else mons
    if not mons:
        return None
    for m in mons:
        if m.contains(x, y):
            return m
    return min(mons, key=lambda m: _gap(x, y, m.left, m.top, m.width, m.height))


def monitor_at_logical(x: float, y: float, mons: Optional[list[Monitor]] = None) -> Optional[Monitor]:
    """The monitor a LOGICAL point is on, or the nearest one."""
    mons = monitors() if mons is None else mons
    if not mons:
        return None
    for m in mons:
        if m.contains_logical(x, y):
            return m
    return min(mons, key=lambda m: _gap(x, y, *m.logical))


def primary(mons: Optional[list[Monitor]] = None) -> Optional[Monitor]:
    mons = monitors() if mons is None else mons
    return next((m for m in mons if m.primary), mons[0] if mons else None)


def to_logical(x: float, y: float, mons: Optional[list[Monitor]] = None) -> tuple[float, float]:
    """A physical point, as the overlay draws it."""
    m = monitor_at(x, y, mons)
    return m.to_logical(x, y) if m is not None else (float(x), float(y))


def to_physical(x: float, y: float, mons: Optional[list[Monitor]] = None) -> tuple[float, float]:
    """A logical point, as Windows reports it."""
    m = monitor_at_logical(x, y, mons)
    return m.to_physical(x, y) if m is not None else (float(x), float(y))


def rect_to_logical(left: float, top: float, right: float, bottom: float,
                    mons: Optional[list[Monitor]] = None) -> tuple[float, float, float, float]:
    """A physical box, converted on ONE monitor - the one its middle is on -
    so a control on a monitor's edge keeps its shape."""
    m = monitor_at((left + right) / 2.0, (top + bottom) / 2.0, mons)
    if m is None:
        return float(left), float(top), float(right), float(bottom)
    l, t = m.to_logical(left, top)
    r, b = m.to_logical(right, bottom)
    return l, t, r, b


def _gap(x, y, left, top, width, height) -> float:
    dx = max(left - x, 0.0, x - (left + width))
    dy = max(top - y, 0.0, y - (top + height))
    return dx * dx + dy * dy


# -- Windows ----------------------------------------------------------------------------------------------

class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("rcMonitor", ctypes.c_long * 4),
                ("rcWork", ctypes.c_long * 4), ("dwFlags", ctypes.c_uint32),
                ("szDevice", ctypes.c_wchar * 32)]


def _enumerate() -> list[Monitor]:
    try:
        user32 = ctypes.windll.user32
    except AttributeError:          # not Windows
        return []
    try:
        shcore = ctypes.windll.shcore
    except OSError:                 # before Windows 8.1: one scaling for all
        shcore = None
    handles: list[int] = []
    proc_t = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                                ctypes.c_void_p, ctypes.c_void_p)

    def _collect(hmon, _hdc, _rect, _data):
        handles.append(hmon)
        return 1

    proc = proc_t(_collect)
    user32.EnumDisplayMonitors.argtypes = [ctypes.c_void_p, ctypes.c_void_p, proc_t, ctypes.c_void_p]
    user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    try:
        user32.EnumDisplayMonitors(None, None, proc, None)
    except Exception:
        return []
    system_scale = 1.0
    try:
        system_scale = max(1.0, user32.GetDpiForSystem() / 96.0)
    except Exception:
        pass
    out = []
    for hmon in handles:
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            continue
        l, t, r, b = info.rcMonitor
        wl, wt, wr, wb = info.rcWork
        scale = system_scale
        if shcore is not None:
            dx, dy = ctypes.c_uint(), ctypes.c_uint()
            try:
                if shcore.GetDpiForMonitor(ctypes.c_void_p(hmon), 0, ctypes.byref(dx), ctypes.byref(dy)) == 0:
                    scale = dx.value / 96.0      # MDT_EFFECTIVE_DPI
            except Exception:
                pass
        out.append(Monitor(info.szDevice, l, t, r - l, b - t, scale or 1.0,
                           bool(info.dwFlags & 1), (wl, wt, wr - wl, wb - wt)))
    return out
