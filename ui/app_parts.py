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
