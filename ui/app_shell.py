"""The shell's own parts: the sidebar and its rows, the title bar's buttons.

The layout is Wispr Flow's (DESIGN.md §3B): one ground for the title bar and
the sidebar, the pages on a raised panel inside it, no page header bar.
The sidebar opens to icons and names and closes to icons alone; the button
for that sits top left, where the title bar used to say the app's name.

The rows are painted, not QPushButtons with icons: a stylesheet button
cannot fade its label while the sidebar closes, and every icon has to sit
on the same column whether the sidebar is open or shut.
"""

from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QAbstractButton, QSizePolicy, QVBoxLayout, QWidget

from ui import app_theme as T
from ui import kit, tokens
from ui.app_widgets import icon, wordmark_pixmap

ROW_H = 36
PAD = 10            # the sidebar's side padding
ICON = 20
ICON_X = 12         # icon's left edge inside a row: centred on the shut sidebar
TEXT_X = ICON_X + ICON + 12


class NavItem(QAbstractButton):
    """One sidebar row: a page (checkable) or an action (not)."""

    def __init__(self, key: str, text: str, openness: Callable[[], float], *,
                 page: bool = True, glyph: str = ""):
        super().__init__()
        self.key = key
        self.glyph = glyph or key            # the icon: a pack's row names its own
        self.setText(text)
        self._openness = openness
        self.setCheckable(page)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(ROW_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(text)
        self._hover = False

    def sizeHint(self) -> QSize:
        return QSize(T.SIDEBAR_SHUT - 2 * PAD, ROW_H)

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        on = self.isChecked()
        if on or self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(T.NAV_PLATE if on else T.NAV_HOVER))
            p.drawRoundedRect(r, tokens.RADIUS_SM, tokens.RADIUS_SM)
        p.drawPixmap(ICON_X, (self.height() - ICON) // 2,
                     icon(self.glyph, T.ACCENT if on else T.TEXT, ICON, self.devicePixelRatioF()))
        shown = self._openness()
        if shown > 0.02:
            p.setOpacity(shown)
            p.setPen(QColor(T.TEXT))
            p.setFont(kit.font(14, 600 if on else 500))
            p.drawText(r.adjusted(TEXT_X, 0, -4, 0),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        p.end()


class BrandRow(QWidget):
    """The wordmark while the sidebar is open, the app icon while it is shut."""

    H = 44

    def __init__(self, openness: Callable[[], float]):
        super().__init__()
        self._openness = openness
        self.setFixedHeight(self.H)
        self._mark: Optional[QPixmap] = None
        self._icon: Optional[QPixmap] = None

    def retheme(self) -> None:
        self._mark = None
        self.update()

    def _pixmaps(self) -> tuple[QPixmap, QPixmap]:
        if self._mark is None:
            self._mark = wordmark_pixmap(24, dpr=self.devicePixelRatioF())
        if self._icon is None:
            from PyQt6.QtCore import QByteArray
            from PyQt6.QtSvg import QSvgRenderer
            svg = (tokens.assets_dir() / "brand" / "heytora-icon.svg").read_bytes()
            dpr = self.devicePixelRatioF() or 1.0
            px = QPixmap(round(28 * dpr), round(28 * dpr))
            px.setDevicePixelRatio(dpr)
            px.fill(QColor(0, 0, 0, 0))
            p = QPainter(px)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            QSvgRenderer(QByteArray(svg)).render(p, QRectF(0, 0, 28, 28))
            p.end()
            self._icon = px
        return self._mark, self._icon

    def paintEvent(self, _event) -> None:
        try:
            mark, glyph = self._pixmaps()
        except Exception:  # noqa: BLE001 - no SVG module: no brand row
            return
        t = self._openness()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if t < 0.98:
            p.setOpacity(1.0 - t)
            p.drawPixmap(ICON_X - 5, (self.height() - 28) // 2, glyph)
        if t > 0.02:
            p.setOpacity(t)
            h = mark.height() / mark.devicePixelRatio()
            p.drawPixmap(ICON_X - 2, int((self.height() - h) / 2), mark)
        p.end()


class Sidebar(QWidget):
    """Open (icons and names) or shut (icons), sliding between the two."""

    def __init__(self, open_: bool = True):
        super().__init__()
        self.setObjectName("sidebar")
        self._t = 1.0 if open_ else 0.0
        self.setFixedWidth(self._width())
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(PAD, 2, PAD, 12)
        self.lay.setSpacing(2)

    def _width(self) -> int:
        return round(T.SIDEBAR_SHUT + (T.SIDEBAR_W - T.SIDEBAR_SHUT) * self._t)

    @property
    def openness(self) -> float:
        return self._t

    @openness.setter
    def openness(self, value: float) -> None:
        self._t = max(0.0, min(1.0, float(value)))
        self.setFixedWidth(self._width())
        for child in self.findChildren(QWidget):
            child.update()

    def is_open(self) -> bool:
        return self._t >= 0.5

    def set_open(self, on: bool, animate: bool = True) -> None:
        for item in self.findChildren(NavItem):
            item.setToolTip("" if on else item.text())
        if animate:
            kit.tween(self, "openness", 1.0 if on else 0.0)
        else:
            self.openness = 1.0 if on else 0.0


class IconButton(QAbstractButton):
    """A title bar button: an icon on a plate that shows under the pointer,
    and a dot for something new (the bell)."""

    def __init__(self, name: str, tip: str):
        super().__init__()
        self._name = name
        self._badge = False
        self._hover = False
        self.setFixedSize(34, 34)
        self.setToolTip(tip)
        self.setAccessibleName(tip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_badge(self, on: bool) -> None:
        self._badge = on
        self.update()

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        if self._hover or self.isDown():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(T.NAV_PLATE if self.isDown() else T.NAV_HOVER))
            p.drawRoundedRect(r, tokens.RADIUS_SM, tokens.RADIUS_SM)
        p.drawPixmap((self.width() - ICON) // 2, (self.height() - ICON) // 2,
                     icon(self._name, T.TEXT, ICON, self.devicePixelRatioF()))
        if self._badge:
            p.setPen(QPen(QColor(T.FRAME), 2))
            p.setBrush(QColor(T.ACCENT))
            p.drawEllipse(QPointF(r.right() - 9, r.top() + 9), 4, 4)
        p.end()


class WindowButton(QAbstractButton):
    """Minimise, maximise/restore, close - drawn as Windows 11 draws them:
    hairline glyphs, a grey plate under the pointer, red under it on close."""

    W = 46

    def __init__(self, kind: str):
        super().__init__()
        self.kind = kind                      # "min" | "max" | "close"
        self.hot = False                      # lit by Windows (window_frame)
        self._hover = False
        self.setFixedSize(self.W, T.TITLEBAR_H)
        self.setToolTip({"min": "Minimise", "max": "Maximise", "close": "Close"}[kind])

    def set_hot(self, on: bool) -> None:
        self.hot = on
        self.update()

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        lit = self._hover or self.hot or self.isDown()
        ink = QColor(T.TEXT)
        if lit:
            if self.kind == "close":
                p.fillRect(self.rect(), QColor(tokens.WINDOW_CLOSE))
                ink = QColor(tokens.ON_COLOUR)
            else:
                p.fillRect(self.rect(), QColor(T.NAV_HOVER))
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(ink, 1.0)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        cx, cy, s = self.width() / 2, self.height() / 2, 5.0
        if self.kind == "min":
            p.drawLine(QPointF(cx - s, cy + 0.5), QPointF(cx + s, cy + 0.5))
        elif self.kind == "close":
            p.drawLine(QPointF(cx - s, cy - s), QPointF(cx + s, cy + s))
            p.drawLine(QPointF(cx - s, cy + s), QPointF(cx + s, cy - s))
        elif self.window().isMaximized():
            p.drawRoundedRect(QRectF(cx - s, cy - s + 2, 8, 8), 1.5, 1.5)
            p.drawLine(QPointF(cx - s + 2, cy - s), QPointF(cx + s, cy - s))
            p.drawLine(QPointF(cx + s, cy - s), QPointF(cx + s, cy + s - 2))
        else:
            p.drawRoundedRect(QRectF(cx - s, cy - s, 2 * s, 2 * s), 1.5, 1.5)
        p.end()
