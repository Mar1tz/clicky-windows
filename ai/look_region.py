"""Where a screen look looks, and where what it saw lands (UD-02, UD-03).

A look used to get the whole monitor and draw wherever its box came out.
Two free things make it better (docs/SHOW_THE_NEXT_MOVE.md):

  * The window in front, not the monitor (UD-02). The thing asked about is
    in the window being worked in; grabbed on its own it reaches the model
    sharper for the same tokens. The mouse goes in as a hint - people hover
    over what they mean by "this", "here" and "which one".
  * The real control, not the model's guess (UD-03). Windows knows every
    button's exact rectangle; a box drawn round one is snapped to it.

Coordinates: PHYSICAL pixels (what Windows and the capture use), and the
0-1000 boxes the vision model speaks - [ymin, xmin, ymax, xmax] - on the
monitor (what the drawing code expects) or on the cropped window (what the
model saw). Rects are (left, top, right, bottom); frames are
(left, top, width, height).
"""
from __future__ import annotations

import math
import re
from typing import Optional

Box = tuple[int, int, int, int]          # ymin, xmin, ymax, xmax - 0-1000
Rect = tuple[float, float, float, float]  # left, top, right, bottom - physical px
Frame = tuple[int, int, int, int]        # left, top, width, height - physical px

MIN_W, MIN_H = 240, 160
WHOLE_SCREEN = 0.85                      # a window this much of the monitor is the monitor
_DEICTIC = re.compile(r"\b(this|that|these|those|here|it|which|one)\b", re.IGNORECASE)


def window_region(window: Optional[Rect], monitor: Frame) -> Optional[Frame]:
    """The part of the monitor the window in front covers - or None when it
    is (nearly) the whole monitor, too small to be worth it, or off it."""
    if not window:
        return None
    wl, wt, wr, wb = window
    ml, mt, mw, mh = monitor
    left, top = max(wl, ml), max(wt, mt)
    right, bottom = min(wr, ml + mw), min(wb, mt + mh)
    if right - left < MIN_W or bottom - top < MIN_H:
        return None
    if (right - left) * (bottom - top) >= WHOLE_SCREEN * mw * mh:
        return None
    return int(left), int(top), int(right - left), int(bottom - top)


def to_monitor_box(box: Box, region: Frame, monitor: Frame) -> Box:
    """A 0-1000 box on the cropped window's image -> the same box on the monitor."""
    ymin, xmin, ymax, xmax = box
    rl, rt, rw, rh = region
    ml, mt, mw, mh = monitor

    def x(n: float) -> int:
        return max(0, min(1000, int(round(((rl - ml) + n / 1000 * rw) / mw * 1000))))

    def y(n: float) -> int:
        return max(0, min(1000, int(round(((rt - mt) + n / 1000 * rh) / mh * 1000))))
    return y(ymin), x(xmin), y(ymax), x(xmax)


def box_to_rect(box: Box, monitor: Frame) -> Rect:
    ymin, xmin, ymax, xmax = box
    ml, mt, mw, mh = monitor
    return ml + xmin / 1000 * mw, mt + ymin / 1000 * mh, ml + xmax / 1000 * mw, mt + ymax / 1000 * mh


def rect_to_box(rect: Rect, monitor: Frame) -> Box:
    left, top, right, bottom = rect
    ml, mt, mw, mh = monitor

    def n(v: float, origin: int, size: int) -> int:
        return max(0, min(1000, int(round((v - origin) / size * 1000))))
    return n(top, mt, mh), n(left, ml, mw), n(bottom, mt, mh), n(right, ml, mw)


def pointer_hint(question: str, cursor: Optional[tuple[float, float]], frame: Frame) -> str:
    """One line for the look: where the mouse is on the image it sees - only
    when the question points ("this", "here", "which one") and the mouse is
    on that image. About 40 tokens, and only then."""
    if not cursor or not _DEICTIC.search(question or ""):
        return ""
    cx, cy = cursor
    fl, ft, fw, fh = frame
    if not (fl <= cx < fl + fw and ft <= cy < ft + fh):
        return ""
    nx, ny = int((cx - fl) / fw * 1000), int((cy - ft) / fh * 1000)
    return (f"The user's mouse pointer is at x={nx}, y={ny} on this image (0-1000). When they say this, "
            "here, it or which one, they most likely mean what is under or right beside it.")


def snap_rect(box: Rect, element: Optional[Rect], window: Optional[Rect] = None) -> Optional[Rect]:
    """The control's own rectangle when it is the thing the box was drawn
    round: it holds the box's centre, its size is within a factor of three
    of the box's, and it is not a pane or the window itself. Else None, and
    the box stays as the model drew it (a picture, a game, a canvas)."""
    if not element:
        return None
    bl, bt, br, bb = box
    el, et, er, eb = element
    box_area = max(1.0, (br - bl) * (bb - bt))
    area = (er - el) * (eb - et)
    if area <= 0 or not (box_area / 3 <= area <= box_area * 3):
        return None
    if window:
        wl, wt, wr, wb = window
        if area > 0.25 * max(1.0, (wr - wl) * (wb - wt)):
            return None
    cx, cy = (bl + br) / 2, (bt + bb) / 2
    if not (el <= cx <= er and et <= cy <= eb):
        return None
    return element


# SP-09: ControlFromPoint answers with the DEEPEST element at a point - in a
# browser, often the label inside a button, not the button - and the ring
# then sat on the word. The element and a few of its ancestors are read, and
# the clickable one the box was drawn round is chosen.
CLICKABLE = frozenset({
    "ButtonControl", "SplitButtonControl", "HyperlinkControl", "EditControl", "CheckBoxControl",
    "RadioButtonControl", "MenuItemControl", "ListItemControl", "TabItemControl", "ComboBoxControl",
    "TreeItemControl",
})
_CHAIN_DEPTH = 6


def control_chain_at(x: float, y: float, max_area: Optional[float] = None) -> list[tuple[Rect, str]]:
    """The accessibility tree's element at a physical point and its
    ancestors, innermost first, as (rectangle, control type). Empty when
    there is none (or no UI Automation). Ancestors only get bigger, so the
    walk stops at the first one over max_area: each step is a cross-process
    call, and six took up to 25 ms on 6 Oct."""
    out: list[tuple[Rect, str]] = []
    try:
        import uiautomation as auto
        c = auto.ControlFromPoint(int(x), int(y))
        for _ in range(_CHAIN_DEPTH):
            if c is None:
                break
            r = c.BoundingRectangle
            if r.width() > 0 and r.height() > 0:
                out.append(((float(r.left), float(r.top), float(r.right), float(r.bottom)), c.ControlTypeName))
                if max_area is not None and r.width() * r.height() > max_area:
                    break
            c = c.GetParentControl()
    except Exception:  # noqa: BLE001 - a missing tree is a reason not to snap, never an error
        pass
    return out


def best_control(box: Rect, chain: list[tuple[Rect, str]], window: Optional[Rect] = None) -> Optional[Rect]:
    """The control the box was drawn round: among the element and its
    ancestors that snap_rect accepts, the clickable one nearest the box's
    size; with none clickable, the innermost one accepted. None: leave the
    box as drawn."""
    fits = [(rect, kind) for rect, kind in chain if snap_rect(box, rect, window)]
    if not fits:
        return None
    clickable = [f for f in fits if f[1] in CLICKABLE]
    if not clickable:
        return fits[0][0]
    area = max(1.0, (box[2] - box[0]) * (box[3] - box[1]))
    return min(clickable, key=lambda f: abs(math.log(max(1.0, (f[0][2] - f[0][0]) * (f[0][3] - f[0][1])) / area)))[0]


def fingerprint(image_b64: str) -> Optional[int]:
    """A 144-bit sketch of an image (16x9 average hash): two screens that look
    the same give nearly the same sketch, whatever JPEG noise or a small
    arrow of Tora's adds. What UD-05 compares to know nothing has changed."""
    try:
        import base64
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(base64.b64decode(image_b64))).convert("L").resize((16, 9))
        px = list(im.getdata())
    except Exception:  # noqa: BLE001 - no sketch: never reuse
        return None
    mean = sum(px) / len(px)
    bits = 0
    for v in px:
        bits = (bits << 1) | (v > mean)
    return bits


def same_screen(a: Optional[int], b: Optional[int], tolerance: int = 6) -> bool:
    return a is not None and b is not None and bin(a ^ b).count("1") <= tolerance


# SP-10: words that point at a place. Narrower than _DEICTIC on purpose -
# "which one should I pick?" is about the window they are working in, not
# about where the mouse happens to rest; "what's this?" is about the mouse.
_POINTING = re.compile(r"\b(this|that|these|those|here)\b", re.IGNORECASE)


def points_at_mouse(question: str) -> bool:
    """True when the question points at a place - "what's this?", "is
    that right?", "what goes here?" - so the mouse says where."""
    return bool(_POINTING.search(question or ""))


def frame_under(cursor: Optional[tuple[float, float]], frames: list) -> Optional[int]:
    """The index of the frame (left, top, width, height) holding the mouse."""
    if not cursor:
        return None
    cx, cy = cursor
    for i, (fl, ft, fw, fh) in enumerate(frames):
        if fl <= cx < fl + fw and ft <= cy < ft + fh:
            return i
    return None


def window_at(x: float, y: float) -> Optional[Rect]:
    """The top-level window under a physical point, as its rectangle; None
    for HeyTora's own windows - the overlay lies over every monitor - and
    when there is none."""
    try:
        import ctypes
        import os
        from ctypes import wintypes
        u = ctypes.windll.user32
        u.WindowFromPoint.restype = ctypes.c_void_p
        u.WindowFromPoint.argtypes = [wintypes.POINT]
        u.GetAncestor.restype = ctypes.c_void_p
        u.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        hwnd = u.GetAncestor(u.WindowFromPoint(wintypes.POINT(int(x), int(y))), 2)    # GA_ROOT
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
        if pid.value == os.getpid():
            return None
        r = wintypes.RECT()
        if not u.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r)):
            return None
        return float(r.left), float(r.top), float(r.right), float(r.bottom)
    except Exception:  # noqa: BLE001 - no window: the whole monitor is looked at
        return None


def cursor_position() -> Optional[tuple[float, float]]:
    """Where the mouse is, in physical px (the process is per-monitor DPI aware)."""
    try:
        import ctypes
        from ctypes import wintypes
        pt = wintypes.POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            return float(pt.x), float(pt.y)
    except Exception:  # noqa: BLE001
        pass
    return None
