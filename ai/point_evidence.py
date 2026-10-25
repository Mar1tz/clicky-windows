"""When an exact point is earned (PT-18, the Massive Direction Plan).

An arrow on a button says "this one, exactly". That is only honest with
evidence: one control matched by name in the accessibility tree, enabled,
on screen, not under another window, found live (or from a picture taken a
moment ago). Short of that she frames the region she means - the picture's
box, or the best match among several - and when even that would be a guess
(the picture is old, nothing matched) she asks instead.

    exact    the arrow and its ring
    region   a frame round the area, no arrow
    ask      no mark; the voice asks which, or where

Each tier records what it knows in an Evidence; judge() decides. Nothing
here touches the screen except top_window_at, one EnumWindows walk.
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Optional

EXACT, REGION, ASK = "exact", "region", "ask"
FRESH_S = 2.0                      # a picture older than this is not "now"
LIVE = ("uia", "snapshot", "reference")


@dataclass
class Evidence:
    source: str = ""                       # uia | snapshot | reference | ocr | vision
    unique: bool = True                    # no runner-up close behind
    enabled: Optional[bool] = None         # None: could not tell
    visible: Optional[bool] = None
    occluded: Optional[bool] = None
    frame_age: Optional[float] = None      # seconds since the picture (ocr, vision)
    notes: list = field(default_factory=list)

    def doubts(self) -> list[str]:
        """What stands between this and an exact point, in plain words."""
        out = []
        if self.source not in LIVE:
            out.append(f"found by {self.source or 'nothing'}, not the app's own list of controls")
        if not self.unique:
            out.append("more than one thing matched")
        if self.enabled is False:
            out.append("it is greyed out")
        if self.visible is False:
            out.append("it is off screen")
        if self.occluded:
            out.append("another window is over it")
        if self.frame_age is not None and self.frame_age > FRESH_S:
            out.append(f"the picture is {self.frame_age:.0f}s old")
        return out


def judge(e: Optional[Evidence]) -> str:
    """exact | region | ask. No evidence at all is the old behaviour (an
    arrow) - only a tier that says what it knows can be doubted."""
    if e is None:
        return EXACT
    if not e.source:
        return ASK
    if e.frame_age is not None and e.frame_age > FRESH_S:
        return ASK                          # an old picture: where it was, not where it is
    if e.source in LIVE and not e.doubts():
        return EXACT
    # Covered, greyed out, one of several, or a picture's guess: frame it.
    # (Covered is a frame, not a question: a window wrongly taken for on
    # top - an invisible overlay some app leaves - must not stop pointing.)
    return REGION


# -- what is on top at a point -------------------------------------------------

_GA_ROOT = 2
_GWL_EXSTYLE = -20
_WS_EX_TRANSPARENT = 0x20
_WS_EX_TOOLWINDOW = 0x80
_DWMWA_CLOAKED = 14


def top_window_at(x: float, y: float) -> int:
    """The top-level window the user sees at a PHYSICAL point - skipping
    HeyTora's own (the overlay is on top of everything and clicks through)
    and windows that are hidden, cloaked or click-through. 0 when unknown."""
    try:
        user32 = ctypes.windll.user32
        dwm = ctypes.windll.dwmapi
    except Exception:  # noqa: BLE001 - not Windows
        return 0
    me = os.getpid()
    found = ctypes.c_void_p(0)
    point = (int(x), int(y))

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == me:
            return True
        if user32.GetWindowLongW(hwnd, _GWL_EXSTYLE) & _WS_EX_TRANSPARENT:
            return True
        cloaked = wintypes.DWORD()
        if dwm.DwmGetWindowAttribute(hwnd, _DWMWA_CLOAKED, ctypes.byref(cloaked), 4) == 0 and cloaked.value:
            return True
        r = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return True
        if r.left <= point[0] < r.right and r.top <= point[1] < r.bottom:
            found.value = hwnd
            return False
        return True

    try:
        user32.EnumWindows(visit, 0)           # top of the z-order first
    except Exception:  # noqa: BLE001
        return 0
    return int(found.value or 0)


def covered(x: float, y: float, hwnd: int) -> Optional[bool]:
    """Is the window `hwnd` (any window of the app) under another at this
    PHYSICAL point? None when it cannot be told."""
    if not hwnd:
        return None
    top = top_window_at(x, y)
    if not top:
        return None
    try:
        root = ctypes.windll.user32.GetAncestor(wintypes.HWND(hwnd), _GA_ROOT) or hwnd
    except Exception:  # noqa: BLE001
        root = hwnd
    return int(top) != int(root)
