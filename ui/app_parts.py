"""The pieces a page is built from (DESIGN.md §3C).

Every page in the app had the same shape - a stack of outlined cards with
outlined rows and outlined tiles inside them - so nothing on a page stood
out and everything looked like a settings screen. These are what replace
it, after Wispr Flow's pages:

* SideCard    - big numbers that are true, and one thing under them.
* ListBox / HoverRow - one container, hairline rows, the row under the
                pointer tinted, its actions only while it is.
* empty()     - a written empty state.

One level of container per page: nothing here goes inside a card.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ui import app_theme as T
from ui.app_widgets import icon, label


# ── side card ─────────────────────────────────────────────────────────────────

class SideCard(QFrame):
    """Numbers down the side of a hero: "12 questions", "3 day streak".
    `foot` is added under a hairline."""

    W = 260

    def __init__(self, stats: list[tuple[str, str]], foot: Optional[QWidget] = None):
        super().__init__()
        self.setObjectName("sideCard")
        self.setFixedWidth(self.W)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        top = QWidget()
        col = QVBoxLayout(top)
        col.setContentsMargins(24, 22, 24, 20)
        col.setSpacing(10)
        for value, words in stats:
            row = QHBoxLayout()
            row.setSpacing(9)
            row.addWidget(label(value, "statValue"), 0, Qt.AlignmentFlag.AlignBaseline)
            row.addWidget(label(words, "statLabel"), 1, Qt.AlignmentFlag.AlignBaseline)
            col.addLayout(row)
        lay.addWidget(top)
        if foot is not None:
            line = QFrame()
            line.setObjectName("hr")
            line.setFixedHeight(1)
            lay.addWidget(line)
            wrap = QWidget()
            fl = QVBoxLayout(wrap)
            fl.setContentsMargins(24, 18, 24, 20)
            fl.addWidget(foot)
            lay.addWidget(wrap)
        lay.addStretch(1)


# ── lists ─────────────────────────────────────────────────────────────────────

def section_head(text: str, trailing: Optional[QWidget] = None) -> QWidget:
    """"TODAY" over a list - outside the container, as Wispr does."""
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(2, 10, 2, 0)
    lay.addWidget(label(text.upper(), "sectionTitle"))
    lay.addStretch(1)
    if trailing is not None:
        lay.addWidget(trailing)
    return row


class ListBox(QFrame):
    """One container for a list: rows inside it, hairlines between them."""

    def __init__(self):
        super().__init__()
        self.setObjectName("listBox")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(0)

    def add(self, row: QWidget) -> QWidget:
        if self.lay.count():
            line = QFrame()
            line.setObjectName("hr")
            line.setFixedHeight(1)
            self.lay.addWidget(line)
        self.lay.addWidget(row)
        return row


class HoverRow(QFrame):
    """A row that tints under the pointer and shows `actions` only then.
    Clicking it emits `clicked` (a history row opens its full answer)."""

    clicked = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("hoverRow")
        self.actions = QWidget()
        self.actions.setObjectName("rowActions")
        self.actions_lay = QHBoxLayout(self.actions)
        self.actions_lay.setContentsMargins(0, 0, 0, 0)
        self.actions_lay.setSpacing(2)
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _show_actions(self, on: bool) -> None:
        # The column keeps its width either way (fixed by the row); only the
        # buttons in it come and go - so a hover never re-wraps the text.
        for child in self.actions.findChildren(QPushButton):
            child.setVisible(on)

    def enterEvent(self, e) -> None:
        self._hover = True
        self._show_actions(True)
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self._hover = False
        self._show_actions(False)
        self.update()
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def paintEvent(self, e) -> None:
        if self._hover:
            # The tint follows the list's rounded outline where the row
            # touches it (the first and last rows), square elsewhere.
            from PyQt6.QtCore import QRectF
            from PyQt6.QtGui import QPainterPath
            from ui import tokens
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            r = QRectF(self.rect())
            box = self.parentWidget()
            rad = tokens.RADIUS_LG - 1
            top = self.y() <= 1
            bottom = box is not None and self.geometry().bottom() >= box.height() - 2
            path = QPainterPath()
            path.setFillRule(Qt.FillRule.WindingFill)     # overlaps add, never cancel
            path.addRoundedRect(r, rad, rad)
            if not top:
                path.addRect(QRectF(r.left(), r.top(), r.width(), rad))
            if not bottom:
                path.addRect(QRectF(r.left(), r.bottom() - rad, r.width(), rad))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(T.SURFACE_HOVER))
            p.drawPath(path.simplified())
            p.end()
        super().paintEvent(e)


class RowAction(QPushButton):
    """A small icon button on a hover row: copy, replay, save."""

    def __init__(self, name: str, tip: str, action: Callable):
        super().__init__()
        self.setObjectName("rowAction")
        self.setToolTip(tip)
        self.setAccessibleName(tip)
        self.setFixedSize(30, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        from PyQt6.QtGui import QIcon
        self.setIcon(QIcon(icon(name, T.TEXT_DIM, 16)))
        self.clicked.connect(lambda _c=False: action())


# Tora's own windows: a question asked with one of these in front was not
# about another app, so the row does not name one.
OWN_WINDOWS = ("ai companion", "heytora")


def history_row(entry: dict, opened: bool, on_toggle: Callable[[], None]) -> HoverRow:
    """One thing asked: when, the question, her answer (two lines, or all of
    it once opened) and the app it was about; copy on hover.

    Wispr's row: the time and the actions centred on the row's height, and
    the actions in a column of their own that is there whether or not they
    show, so hovering never reflows the text."""
    from tutor_features.journal import WALKTHROUGH_TAG
    row = HoverRow()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(18, 16, 14, 16)
    lay.setSpacing(18)
    centre = Qt.AlignmentFlag.AlignVCenter
    when = label(time.strftime("%I:%M%p", time.localtime(entry["created_at"])).lstrip("0").lower(), "rowTime")
    when.setFixedWidth(62)
    lay.addWidget(when, 0, centre)
    text = QVBoxLayout()
    text.setSpacing(4)
    text.addWidget(label((entry.get("question") or "").strip(), "rowTitle", wrap=True))
    answer = (entry.get("answer") or "").strip()
    walk = WALKTHROUGH_TAG in (entry.get("tags") or "")
    if walk:
        shown = f"Walkthrough · {len(answer.splitlines())} steps" if not opened else answer
    else:
        shown = answer if opened else _clip(answer, 180)
    if shown:
        text.addWidget(label(shown, "rowSub", wrap=True))
    app = (entry.get("app_key") or "").strip()
    if app and not any(own in app.lower() for own in OWN_WINDOWS):
        where = label(f"in {app}", "rowApp")
        where.setToolTip(entry.get("window_title") or "")
        text.addWidget(where)
    lay.addLayout(text, 1)
    if answer:
        row.actions_lay.addWidget(RowAction(
            "copy", "Copy answer", lambda: QApplication.clipboard().setText(answer)))
    row.actions.setFixedWidth(max(1, row.actions_lay.count()) * 32)
    row._show_actions(False)
    lay.addWidget(row.actions, 0, centre)
    row.clicked.connect(on_toggle)
    return row


def _clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def day_name(ts: float) -> str:
    """"Today", "Yesterday", "Monday 29 September"."""
    day = time.localtime(ts)
    today = time.localtime()
    days = (time.mktime((today.tm_year, today.tm_mon, today.tm_mday, 0, 0, 0, 0, 0, -1))
            - time.mktime((day.tm_year, day.tm_mon, day.tm_mday, 0, 0, 0, 0, 0, -1))) / 86400
    if round(days) <= 0:
        return "Today"
    if round(days) == 1:
        return "Yesterday"
    return time.strftime("%A %d %B", day).replace(" 0", " ")


def empty(title: str, sub: str, name: str = "") -> QWidget:
    """A written empty state: what will be here, and how to get it."""
    box = QFrame()
    box.setObjectName("listBox")
    lay = QVBoxLayout(box)
    lay.setContentsMargins(24, 34, 24, 36)
    lay.setSpacing(6)
    if name:
        glyph = QLabel()
        glyph.setPixmap(icon(name, T.TEXT_FAINT, 22))
        glyph.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(glyph)
        lay.addSpacing(4)
    head = label(title, "emptyTitle")
    head.setAlignment(Qt.AlignmentFlag.AlignHCenter)
    lay.addWidget(head)
    words = label(sub, "emptySub", wrap=True)
    words.setAlignment(Qt.AlignmentFlag.AlignHCenter)
    lay.addWidget(words)
    return box


# ── search ────────────────────────────────────────────────────────────────────

class SearchBox(QWidget):
    """A search icon that slides open into a field (Wispr's history search)
    and closes again when it is empty and left, or on Escape. `changed`
    fires a moment after typing stops, not on every key."""

    changed = pyqtSignal(str)
    SHUT_W, OPEN_W, H = 32, 300, 34

    def __init__(self, placeholder: str):
        super().__init__()
        from PyQt6.QtCore import QTimer, QVariantAnimation
        from PyQt6.QtGui import QIcon
        from PyQt6.QtWidgets import QLineEdit
        from ui import tokens
        self.setFixedSize(self.SHUT_W, self.H)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.button = RowAction("search", "Search", self.open)
        self.button.setFixedSize(self.SHUT_W, self.SHUT_W)
        lay.addWidget(self.button, 0, Qt.AlignmentFlag.AlignRight)
        self.field = QLineEdit()
        self.field.setObjectName("searchField")
        self.field.setPlaceholderText(placeholder)
        self.field.setClearButtonEnabled(True)
        self.field.addAction(QIcon(icon("search", T.TEXT_FAINT, 16)),
                             QLineEdit.ActionPosition.LeadingPosition)
        self.field.setFixedHeight(self.H)
        self.field.hide()
        self.field.installEventFilter(self)
        lay.addWidget(self.field)
        self._wait = QTimer(self)
        self._wait.setSingleShot(True)
        self._wait.setInterval(160)
        self._wait.timeout.connect(lambda: self.changed.emit(self.field.text().strip()))
        self.field.textChanged.connect(lambda _t: self._wait.start())
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(tokens.DURATION)
        self._anim.setEasingCurve(tokens.ease())
        self._anim.valueChanged.connect(lambda w: self.setFixedWidth(int(w)))

    def is_open(self) -> bool:
        return self.field.isVisible()

    def open(self) -> None:
        if self.is_open():
            return
        self.button.hide()
        self.field.show()
        self.field.setFocus()
        self._slide(self.OPEN_W)

    def close_search(self) -> None:
        if self.field.text():
            self.field.clear()
            self.changed.emit("")
        self._slide(self.SHUT_W, then=self._shut)

    def _shut(self) -> None:
        self.field.hide()
        self.button.show()

    def _slide(self, to: int, then=None) -> None:
        self._anim.stop()
        try:
            self._anim.finished.disconnect()
        except TypeError:
            pass
        if then is not None:
            self._anim.finished.connect(then)
        self._anim.setStartValue(self.width())
        self._anim.setEndValue(to)
        self._anim.start()

    def eventFilter(self, obj, event) -> bool:
        from PyQt6.QtCore import QEvent
        if obj is self.field:
            if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                self.close_search()
                return True
            if event.type() == QEvent.Type.FocusOut and not self.field.text():
                self.close_search()
        return False


# ── insights (DESIGN.md §3C) ──────────────────────────────────────────────────

class InfoCard(QFrame):
    """A filled card with a heading and, on the right of it, a small caps
    note ("TOTAL APPS · 8"). `body` takes the content."""

    def __init__(self, heading: str = "", note: str = "", *, big: bool = False):
        super().__init__()
        self.setObjectName("infoCard")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(12)
        if heading or note:
            head = QHBoxLayout()
            head.setSpacing(10)
            if heading:
                head.addWidget(label(heading, "infoBig" if big else "infoTitle"), 0,
                               Qt.AlignmentFlag.AlignVCenter)
            head.addStretch(1)
            if note:
                head.addWidget(label(note.upper(), "infoNote"), 0, Qt.AlignmentFlag.AlignVCenter)
            lay.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        lay.addLayout(self.body)
        lay.addStretch(1)


def stat_tile(value: str, caption: str, lines: list[tuple[str, str]] = (),
              chart: Optional[QWidget] = None) -> InfoCard:
    """Wispr's tile: the number, what it counts, a chart if there is one,
    then a hairline and up to two lines of detail ("this week · 12")."""
    card = InfoCard()
    card.body.setSpacing(4)
    card.body.addWidget(label(value, "tileValue"))
    card.body.addWidget(label(caption.upper(), "infoNote"))
    if chart is not None:
        card.body.addSpacing(12)
        if chart.minimumWidth() == chart.maximumWidth():      # a fixed size (the gauge): centred
            card.body.addWidget(chart, 0, Qt.AlignmentFlag.AlignHCenter)
        else:                                                 # the rest fill the tile's width
            card.body.addWidget(chart)
    if lines:
        card.body.addSpacing(10)
        line = QFrame()
        line.setObjectName("hr")
        line.setFixedHeight(1)
        card.body.addWidget(line)
        card.body.addSpacing(6)
        for left, right in lines:
            row = QHBoxLayout()
            row.addWidget(label(left, "tileLine"))
            row.addStretch(1)
            row.addWidget(label(right, "tileLineValue"))
            card.body.addLayout(row)
    return card


class Grows(QWidget):
    """A chart that draws itself `t` of the way in (0..1). It starts drawn;
    the Insights page sets it to 0 and calls play() when the page opens, so
    a chart grows in on arrival and a live redraw does not replay it."""

    GROW_MS = 950

    def __init__(self):
        super().__init__()
        self.t = 1.0

    def play(self, delay: int = 0) -> None:
        from PyQt6.QtCore import QTimer
        from ui import kit
        self.t = 0.0
        self.update()
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: kit.tween(self, "t", 1.0, self.GROW_MS))
        timer.timeout.connect(timer.deleteLater)
        timer.start(delay)


class ShareBar(Grows):
    """One app's share: a rounded bar in the accent, its % inside it."""

    H = 26

    def __init__(self, share: float, rank: int):
        super().__init__()
        self._share, self._rank = max(0.0, min(1.0, share)), rank
        self.setFixedHeight(self.H)
        self.setMinimumWidth(120)

    def paintEvent(self, _e) -> None:
        from PyQt6.QtCore import QRectF
        from ui import kit, tokens
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = max(44.0, self.width() * self._share) * self.t
        if w < 2:
            p.end()
            return
        r = QRectF(0, 0, w, self.H)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(kit.with_alpha(tokens.BLUE, (1.0, 0.82, 0.66, 0.52, 0.42)[min(self._rank, 4)]))
        p.drawRoundedRect(r, 6, 6)
        p.setOpacity(max(0.0, (self.t - 0.6) / 0.4))
        p.setPen(QColor(tokens.ON_COLOUR))
        p.setFont(kit.font(12, 700))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, f"{round(self._share * 100)}%")
        p.end()


class StreakGrid(Grows):
    """The last weeks, a square a day, deeper blue for more questions;
    months along the top, days down the side (Wispr's streak calendar)."""

    CELL, GAP, LEFT, TOP = 14, 4, 34, 20

    def __init__(self, columns: list[list[tuple]]):
        super().__init__()
        self._cols = columns
        w = self.LEFT + len(columns) * (self.CELL + self.GAP)
        self.setMinimumSize(w, self.TOP + 7 * (self.CELL + self.GAP))

    @staticmethod
    def level(n: int) -> float:
        return 0.0 if n <= 0 else 0.35 if n == 1 else 0.6 if n <= 3 else 1.0

    def paintEvent(self, _e) -> None:
        from PyQt6.QtCore import QRectF
        from ui import kit, tokens
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        step = self.CELL + self.GAP
        p.setFont(kit.font(11, 500))
        p.setPen(QColor(T.TEXT_FAINT))
        for i, name in enumerate(("Sun", "", "Tue", "", "Thu", "", "Sat")):
            if name:
                p.drawText(QRectF(0, self.TOP + i * step, self.LEFT - 6, self.CELL),
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, name)
        month = None
        for c, col in enumerate(self._cols):
            x = self.LEFT + c * step
            # A month is named on the column its first day falls in, so two
            # names never share the space of one.
            first = next((day for day, _n in col if day.day == 1), None)
            if first is not None and first.month != month:
                month = first.month
                if c < len(self._cols) - 1:
                    p.setPen(QColor(T.TEXT_FAINT))
                    p.drawText(QRectF(x, 0, step * 3, 14), Qt.AlignmentFlag.AlignLeft,
                               first.strftime("%b"))
            p.setPen(Qt.PenStyle.NoPen)
            # The weeks arrive left to right, each a short fade behind the last.
            n_cols = max(1, len(self._cols))
            shown = max(0.0, min(1.0, self.t * (n_cols + 4) / 4 - c / 4))
            for r, (_day, n) in enumerate(col):
                a = self.level(n)
                p.setOpacity(shown)
                p.setBrush(kit.with_alpha(tokens.BLUE, a) if a else QColor(T.SURFACE_HOVER))
                p.drawRoundedRect(QRectF(x, self.TOP + r * step, self.CELL, self.CELL), 3.5, 3.5)
            p.setOpacity(1.0)
        p.end()


def legend() -> QWidget:
    """Less ▢▢▢▢ More, under the calendar."""
    from PyQt6.QtCore import QRectF
    from ui import kit, tokens

    class _Swatches(QWidget):
        def __init__(self):
            super().__init__()
            self.setFixedSize(4 * 18, 14)

        def paintEvent(self, _e):
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            for i, a in enumerate((0.0, 0.35, 0.6, 1.0)):
                p.setBrush(kit.with_alpha(tokens.BLUE, a) if a else QColor(T.SURFACE_HOVER))
                p.drawRoundedRect(QRectF(i * 18, 0, 14, 14), 3.5, 3.5)
            p.end()

    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(StreakGrid.LEFT, 4, 0, 0)
    lay.setSpacing(8)
    lay.addWidget(label("Less", "infoFaint"))
    lay.addWidget(_Swatches())
    lay.addWidget(label("More", "infoFaint"))
    lay.addStretch(1)
    return row


class DayBars(Grows):
    """Questions a day, the last few weeks: a bar a day, today's in the
    accent, the rest a paler blue; they rise in, left to right."""

    def __init__(self, values: list[int], height: int = 64):
        super().__init__()
        self._v = list(values)
        self.setFixedHeight(height)
        self.setMinimumWidth(120)
        from PyQt6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, _e) -> None:
        from PyQt6.QtCore import QRectF
        from ui import kit, tokens
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        n = max(1, len(self._v))
        gap = 4.0
        bw = max(3.0, (self.width() - gap * (n - 1)) / n)
        if bw > 26:                       # a week of bars stays slim, spread across the width
            bw = 26.0
            gap = (self.width() - bw * n) / max(1, n - 1)
        top = max(self._v) if self._v and max(self._v) > 0 else 1
        h = float(self.height())
        p.setPen(Qt.PenStyle.NoPen)
        for i, v in enumerate(self._v):
            # each bar starts a little after the one before it
            k = max(0.0, min(1.0, self.t * 1.6 - i / n * 0.6))
            full = max(3.0, (h - 2) * v / top) if v else 3.0
            bh = full * k if v else 3.0
            x = i * (bw + gap)
            last = i == n - 1
            colour = (kit.with_alpha(tokens.BLUE, 1.0) if last else
                      kit.with_alpha(tokens.BLUE, 0.38) if v else QColor(T.SURFACE_HOVER))
            p.setBrush(colour)
            p.drawRoundedRect(QRectF(x, h - bh, bw, bh), min(3.0, bw / 2), min(3.0, bw / 2))
        p.end()


class Gauge(Grows):
    """A half ring, Wispr's words-per-minute gauge: `share` of it in the
    accent, sweeping round as it grows in, the % in the middle."""

    def __init__(self, share: float, caption: str, size: int = 150):
        super().__init__()
        self._share = max(0.0, min(1.0, share))
        self._caption = caption
        self.setFixedSize(size, int(size * 0.62))

    def paintEvent(self, _e) -> None:
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QPen
        from ui import kit, tokens
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        thick = 14.0
        w = float(self.width())
        r = QRectF(thick / 2, thick / 2, w - thick, w - thick)
        pen = QPen(QColor(T.SURFACE_HOVER), thick)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(r, 180 * 16, -180 * 16)
        sweep = 180 * self._share * self.t
        if sweep > 0.5:
            pen.setColor(QColor(tokens.BLUE))
            p.setPen(pen)
            p.drawArc(r, 180 * 16, int(-sweep * 16))
        p.setPen(QColor(T.TEXT))
        p.setFont(kit.font(24, 700, -0.5))
        mid = QRectF(0, r.center().y() - 30, w, 30)
        p.drawText(mid, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                   f"{round(self._share * 100 * self.t)}%")
        p.setPen(QColor(T.TEXT_DIM))
        p.setFont(kit.font(12, 500))
        p.drawText(QRectF(0, r.center().y() + 2, w, 18),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, self._caption)
        p.end()


class GrowMeter(Grows):
    """Meter's thin bar, filling in."""

    def __init__(self, fraction: float, colour: str, height: int = 6):
        super().__init__()
        self._f, self._colour = max(0.0, min(1.0, fraction)), colour
        self.setFixedHeight(height)

    def paintEvent(self, _e) -> None:
        from PyQt6.QtCore import QRectF
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = self.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(T.BORDER))
        p.drawRoundedRect(QRectF(0, 0, self.width(), h), h / 2, h / 2)
        f = self._f * self.t
        if f > 0:
            p.setBrush(QColor(self._colour))
            p.drawRoundedRect(QRectF(0, 0, max(h, self.width() * f), h), h / 2, h / 2)
        p.end()


class SplitBar(Grows):
    """One bar in parts - Ask / Show / Do - each a strength of the accent
    (no new colours), growing in left to right. `SHADES` is shared with the
    key under it so a part and its name match."""

    SHADES = (1.0, 0.6, 0.3)
    H = 12

    def __init__(self, parts: list[int]):
        super().__init__()
        self._parts = [max(0, int(n)) for n in parts]
        self.setFixedHeight(self.H)
        self.setMinimumWidth(120)

    def paintEvent(self, _e) -> None:
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QPainterPath
        from ui import kit, tokens
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = float(self.width()), float(self.H)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        p.setClipPath(clip)
        p.fillRect(QRectF(0, 0, w, h), QColor(T.SURFACE_HOVER))
        total = sum(self._parts)
        if total:
            x, reach = 0.0, w * self.t
            for n, shade in zip(self._parts, self.SHADES):
                seg = w * n / total
                if n and x < reach:
                    p.fillRect(QRectF(x, 0, min(seg, reach - x) - (2 if x + seg < w else 0), h),
                               kit.with_alpha(tokens.BLUE, shade))
                x += seg
        p.end()


def swatch(shade: float) -> QWidget:
    """A small square of the accent at `shade` - a SplitBar's key."""
    from ui import kit, tokens

    class _Swatch(QWidget):
        def __init__(self):
            super().__init__()
            self.setFixedSize(10, 10)

        def paintEvent(self, _e):
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(kit.with_alpha(tokens.BLUE, shade))
            p.drawRoundedRect(self.rect(), 3, 3)
            p.end()
    return _Swatch()


# ── the Tora page (DESIGN.md §3E) ─────────────────────────────────────────────

class ModeTile(QFrame):
    """One of her modes as a tile: an icon, what it does, a switch. The
    whole tile is the switch, and it fills with a pale accent while on."""

    toggled = pyqtSignal(bool)

    def __init__(self, icon_name: str, heading: str, sub: str, on: bool):
        super().__init__()
        from PyQt6.QtWidgets import QLabel
        from ui.app_widgets import Switch
        self.setObjectName("modeTile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 16)
        lay.setSpacing(6)
        top = QHBoxLayout()
        self._glyph = QLabel()
        self._icon = icon_name
        top.addWidget(self._glyph, 0, Qt.AlignmentFlag.AlignTop)
        top.addStretch(1)
        self.switch = Switch(on)
        self.switch.toggled.connect(self._changed)
        top.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(top)
        lay.addSpacing(4)
        lay.addWidget(label(heading, "modeTitle"))
        lay.addWidget(label(sub, "modeSub", wrap=True))
        lay.addStretch(1)
        self._paint(on)

    def _paint(self, on: bool) -> None:
        self.setProperty("on", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self._glyph.setPixmap(icon(self._icon, T.ACCENT if on else T.TEXT_DIM, 20))

    def _changed(self, on: bool) -> None:
        self._paint(on)
        self.toggled.emit(on)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.switch.setChecked(not self.switch.isChecked())
        super().mouseReleaseEvent(e)


def chips(options: list[tuple[str, str]], current: str, on_pick: Callable[[str], None]) -> QWidget:
    """A row of pill chips, one chosen - her sound. Picking one calls
    on_pick(key) (which plays it)."""
    from PyQt6.QtWidgets import QButtonGroup
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    group = QButtonGroup(row)
    group.setExclusive(True)
    for key, words in options:
        b = QPushButton(words)
        b.setObjectName("chip")
        b.setCheckable(True)
        b.setChecked(key == current)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(lambda _c=False, k=key: on_pick(k))
        group.addButton(b)
        lay.addWidget(b)
    lay.addStretch(1)
    return row


class TabBar(QFrame):
    """A page's own tabs, Wispr's way: words in a row on a hairline, the one
    you are on in ink with a line under it. `picked` says which."""

    picked = pyqtSignal(str)

    def __init__(self, tabs: list[tuple[str, str]], current: str):
        super().__init__()
        from PyQt6.QtWidgets import QButtonGroup
        self.setObjectName("tabBar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(26)
        group = QButtonGroup(self)
        group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        for key, words in tabs:
            b = QPushButton(words.replace("&", "&&"))      # "&" is a mnemonic to a button
            b.setObjectName("pageTab")
            b.setCheckable(True)
            b.setChecked(key == current)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False, k=key: self.picked.emit(k))
            group.addButton(b)
            lay.addWidget(b)
            self.buttons[key] = b
        lay.addStretch(1)


class InfoButton(QPushButton):
    """The small "i" beside a card's title: click it and a card opens
    saying what the thing does, where it goes and what to watch for -
    so a button nobody has used before is never a dead one."""

    def __init__(self, title: str, points: list[str]):
        super().__init__()
        from PyQt6.QtGui import QIcon
        self.setObjectName("rowAction")
        self.setFixedSize(28, 28)
        self.setIcon(QIcon(icon("info", T.TEXT_DIM, 16)))
        self.setToolTip(f"About {title.lower()}")
        self.setAccessibleName(f"About {title.lower()}")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._title, self._points = title, list(points)
        self.clicked.connect(self.open)

    def open(self) -> None:
        from PyQt6.QtCore import QPoint
        from ui.app_popover import Popover
        pop = Popover(360)
        head = label(self._title, "popTitle")
        head.setContentsMargins(12, 10, 12, 4)
        pop.add(head)
        for point in self._points:
            line = label(point, "popSub", wrap=True)
            line.setContentsMargins(12, 4, 12, 4)
            pop.add(line)
        pop.body.addSpacing(8)
        self._pop = pop
        pop.open_at(self.mapToGlobal(QPoint(self.width(), self.height() + 4)), "right")


def growing_text(text: str, placeholder: str, least: int = 84, most: int = 320):
    """A text box that grows with what is typed in it - from `least` to
    `most` px, then scrolls - instead of a fixed box that hides the words."""
    from PyQt6.QtWidgets import QPlainTextEdit

    class _Growing(QPlainTextEdit):
        def __init__(self):
            super().__init__(text)
            self.setPlaceholderText(placeholder)
            self.document().contentsChanged.connect(self._fit)
            self._fit()

        def _fit(self) -> None:
            lines, block = 0, self.document().begin()
            while block.isValid():
                lines += max(1, block.layout().lineCount())
                block = block.next()
            want = lines * self.fontMetrics().lineSpacing() + 28
            self.setFixedHeight(max(least, min(most, want)))

        def resizeEvent(self, e) -> None:
            super().resizeEvent(e)
            self._fit()           # a narrower box wraps to more lines

    return _Growing()
