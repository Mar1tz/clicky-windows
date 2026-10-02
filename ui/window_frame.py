"""A window that draws its own title bar and is still a real Windows window.

The shell puts its own controls where the title bar was - the sidebar
button and the profile on the left, notifications and the window buttons on
the right (DESIGN.md §3B, after Wispr Flow) - so Windows' caption has to go.

The first attempt at that (M29) was Qt's frameless flag plus a translucent
background, and it lost everything Windows does for a window: snapping,
the shadow, the rounded corners, and the strip under the hand-drawn title
bar stayed transparent on a real desktop. This keeps the window a normal
captioned, resizable window as far as Windows knows, and only answers two
of its questions differently:

* WM_NCCALCSIZE - "how much of you is frame?" None: the client area is the
  whole window, so the app paints where the caption was. (Maximised, a
  window hangs over the screen edge by its frame; that much is taken back.)
* WM_NCHITTEST - "what is under the pointer?" The edges are resize borders,
  the empty title bar is the caption (drag, double-click, snap, the system
  menu), and the maximise button is the maximise button - which is what
  makes Windows 11 offer its snap layouts on hover.

Everything here is a no-op off Windows (tests run on the offscreen platform).
"""

from __future__ import annotations

import ctypes
import sys
from typing import Callable, Optional

from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QCursor, QGuiApplication
from PyQt6.QtWidgets import QWidget

WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
WM_NCMOUSEMOVE = 0x00A0
WM_NCLBUTTONDOWN = 0x00A1
WM_NCLBUTTONUP = 0x00A2
WM_NCMOUSELEAVE = 0x02A2

HTCAPTION, HTMAXBUTTON = 2, 9
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 10, 11, 12, 13, 14
HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17

WS_CAPTION, WS_THICKFRAME, WS_SYSMENU = 0x00C00000, 0x00040000, 0x00080000
WS_MINIMIZEBOX, WS_MAXIMIZEBOX = 0x00020000, 0x00010000
SM_CXSIZEFRAME, SM_CYSIZEFRAME, SM_CXPADDEDBORDER = 32, 33, 92
SWP_FLAGS = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020   # no size/move/z/activate; frame changed


def supported() -> bool:
    return sys.platform == "win32" and QGuiApplication.platformName() == "windows"


class _MSG(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint),
                ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t),
                ("time", ctypes.c_ulong), ("x", ctypes.c_long), ("y", ctypes.c_long)]


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MARGINS(ctypes.Structure):
    _fields_ = [("left", ctypes.c_int), ("right", ctypes.c_int),
                ("top", ctypes.c_int), ("bottom", ctypes.c_int)]


class NativeFrame:
    """Installed on one top-level widget; its nativeEvent hands every
    message to handle() first.

    `is_caption(local)` says whether a point (in the window's coordinates)
    is empty title bar. `max_button` is the widget drawn as maximise, and
    `on_max_hover(bool)` lights it, since Windows - not Qt - sees the
    pointer while it is over that button."""

    RESIZE = 6       # px of edge that resizes

    def __init__(self, window: QWidget, is_caption: Callable[[QPoint], bool],
                 max_button: Optional[QWidget] = None,
                 on_max_hover: Optional[Callable[[bool], None]] = None):
        self.window = window
        self.is_caption = is_caption
        self.max_button = max_button
        self.on_max_hover = on_max_hover or (lambda _on: None)
        self._max_hot = False
        self.active = False

    def install(self) -> None:
        """Give the frameless Qt window back the styles that make Windows
        treat it as a window (snap, minimise animation, the shadow)."""
        if not supported():
            return
        try:
            user32, dwm = ctypes.windll.user32, ctypes.windll.dwmapi
            user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
            user32.GetWindowLongPtrW.argtypes = (ctypes.c_void_p, ctypes.c_int)
            user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
            user32.SetWindowLongPtrW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t)
            hwnd = ctypes.c_void_p(int(self.window.winId()))
            style = user32.GetWindowLongPtrW(hwnd, -16)
            style |= WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX
            user32.SetWindowLongPtrW(hwnd, -16, style)
            # One pixel of DWM frame keeps the shadow and, on Windows 11, the
            # rounded corners; the app paints over it.
            dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(_MARGINS(0, 0, 1, 0)))
            user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_FLAGS)
            self.active = True
        except Exception:  # noqa: BLE001 - a plain frameless window still works
            import logging
            logging.getLogger("tora").debug("native frame not installed", exc_info=True)

    # ── messages ─────────────────────────────────────────────────────────────

    def handle(self, message) -> Optional[tuple[bool, int]]:
        """(handled, result) for a message this answers, else None."""
        if not self.active:
            return None
        msg = _MSG.from_address(int(message))
        kind = msg.message
        if kind == WM_NCCALCSIZE:
            return self._calc_size(msg)
        if kind == WM_NCHITTEST:
            hit = self._hit_test()
            self._set_max_hot(hit == HTMAXBUTTON)
            return (True, hit) if hit else None
        if kind in (WM_NCMOUSELEAVE,) or (kind == WM_NCMOUSEMOVE and msg.wParam != HTMAXBUTTON):
            self._set_max_hot(False)
            return None
        if kind == WM_NCLBUTTONDOWN and msg.wParam == HTMAXBUTTON:
            return True, 0                       # the click is the button-up
        if kind == WM_NCLBUTTONUP and msg.wParam == HTMAXBUTTON:
            self._set_max_hot(False)
            w = self.window
            w.showNormal() if w.isMaximized() else w.showMaximized()
            return True, 0
        return None

    def _calc_size(self, msg: _MSG) -> tuple[bool, int]:
        if msg.wParam and self.window.isMaximized():
            # Maximised, Windows positions the window so its frame hangs off
            # the screen; without a frame, that much of the app would too.
            user32 = ctypes.windll.user32
            hwnd = ctypes.c_void_p(int(self.window.winId()))
            dpi = user32.GetDpiForWindow(hwnd) or 96
            pad = user32.GetSystemMetricsForDpi(SM_CXPADDEDBORDER, dpi)
            fx = user32.GetSystemMetricsForDpi(SM_CXSIZEFRAME, dpi) + pad
            fy = user32.GetSystemMetricsForDpi(SM_CYSIZEFRAME, dpi) + pad
            rect = _RECT.from_address(msg.lParam)          # rgrc[0] leads the struct
            rect.left += fx
            rect.top += fy
            rect.right -= fx
            rect.bottom -= fy
        return True, 0

    def _hit_test(self) -> int:
        w = self.window
        local = w.mapFromGlobal(QCursor.pos())
        x, y, width, height = local.x(), local.y(), w.width(), w.height()
        if not w.isMaximized():
            b = self.RESIZE
            left, right = x < b, x >= width - b
            top, bottom = y < b, y >= height - b
            if top and left:
                return HTTOPLEFT
            if top and right:
                return HTTOPRIGHT
            if bottom and left:
                return HTBOTTOMLEFT
            if bottom and right:
                return HTBOTTOMRIGHT
            if left:
                return HTLEFT
            if right:
                return HTRIGHT
            if top:
                return HTTOP
            if bottom:
                return HTBOTTOM
        mb = self.max_button
        if mb is not None and mb.isVisible() and mb.rect().contains(mb.mapFrom(w, local)):
            return HTMAXBUTTON
        if self.is_caption(local):
            return HTCAPTION
        return 0

    def _set_max_hot(self, on: bool) -> None:
        if on != self._max_hot:
            self._max_hot = on
            self.on_max_hover(on)
