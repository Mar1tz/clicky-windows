"""The pieces a premium page is built from (DESIGN.md §3C).

Every page in the app had the same shape - a stack of outlined cards with
outlined rows and outlined tiles inside them - so nothing on a page stood
out and everything looked like a settings screen. These are what replace
it, after Wispr Flow's pages:

* HeroCard  - the page's one picture: its gradient family from heytora.com,
              a headline with one italic word, a line, a button, and the
              website's tour scene for that capability playing on the right.
* SideCard  - big numbers that are true, and one thing under them.
* HistoryList - one container, hairline rows, the row under the pointer
              tinted, its actions only while it is.
* empty()   - a written empty state.

One level of container per page: nothing here goes inside a card.
"""

from __future__ import annotations

import math
import time
from typing import Callable, Optional

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QRadialGradient,
    QTextDocument,
)
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

from ui import app_theme as T
from ui import kit, tokens
from ui.app_widgets import icon, label


# ── hero ──────────────────────────────────────────────────────────────────────

class HeroCard(QWidget):
    """The picture at the top of a page.

    `family` is a key of tokens.CAPABILITY (the mesh), `look` an index into
    tokens.SCENE_LOOKS (the scene's pane and the italic word's colour),
    `headline` marks its one italic word with *stars*."""

    H = 236
    SCENE_SCALE = 0.56

    def __init__(self, family: str, look: int, headline: str, sub: str = "", *,
                 button: Optional[tuple[str, Callable]] = None, scene=None):
        super().__init__()
        self.setFixedHeight(self.H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._wash, self._blobs = tokens.CAPABILITY[family]
        self._look = tokens.SCENE_LOOKS[look]
        self._headline, self._sub = headline, sub
        self._scene = scene(self._look) if scene is not None else None
        self._t0 = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self.button: Optional[QPushButton] = None
        if button is not None:
            self.button = QPushButton(button[0], self)
            self.button.setObjectName("heroButton")
            self.button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.button.setFixedHeight(tokens.BUTTON_H["md"] + 2)
            self.button.clicked.connect(button[1])
            kit.drop_shadow(self.button, "card")

    # the clock: the scene plays, ends, and plays again; the mesh drifts
    def showEvent(self, e) -> None:
        if self._scene is not None:
            self._scene.reset()
        self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e) -> None:
        self._timer.stop()
        super().hideEvent(e)

    def _tick(self) -> None:
        if self._scene is not None:
            self._scene.advance()
            if self._scene.done:
                self._scene.reset()
        self.update()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._place_button()

    def _place_button(self) -> None:
        """Under the words, wherever they end - never over them."""
        if self.button is None:
            return
        self.button.adjustSize()
        _head, _sub, bottom = self._words()
        floor = self.height() - self.PAD + 6 - self.button.height()
        self.button.move(self.PAD, int(min(floor, bottom + 18)))

    PAD = 34

    def _words(self) -> tuple[QTextDocument, Optional[QTextDocument], float]:
        """The headline and the line under it, laid out for the space left
        of the scene; and where they end."""
        pane = self._pane()
        right = (pane.left() - 28) if pane is not None else self.width() - self.PAD
        width = max(200.0, right - self.PAD)
        head = QTextDocument()
        head.setDefaultFont(kit.font(28, 700, -0.5))
        head.setDocumentMargin(0)
        head.setTextWidth(width)
        head.setHtml(f'<div style="color:{tokens.PAPER.ink}; line-height:112%">'
                     f'{_italic(self._headline, self._look[2])}</div>')
        bottom = self.TOP + head.size().height()
        sub = None
        if self._sub:
            sub = QTextDocument()
            sub.setDefaultFont(kit.font(14, 500))
            sub.setDocumentMargin(0)
            sub.setTextWidth(width)
            sub.setHtml(f'<div style="color:{tokens.PAPER.dim}">{_escape(self._sub)}</div>')
            bottom += 10 + sub.size().height()
        return head, sub, bottom

    TOP = 30

    def _pane(self) -> Optional[QRectF]:
        """Where the scene sits - right, rising out of the bottom edge - or
        None when the card is too narrow to give it room."""
        if self._scene is None or self.width() < 620:
            return None
        from ui.tour_scenes import H, W
        s = self.SCENE_SCALE
        return QRectF(self.width() - W * s - 30, 30, W * s, H * s)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        w, h = float(self.width()), float(self.height())
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, w, h), tokens.RADIUS_XL, tokens.RADIUS_XL)
        p.setClipPath(clip)

        # the mesh: the site's wash and blobs, drifting slowly
        t = time.monotonic() - self._t0
        p.fillRect(self.rect(), QColor(self._wash))
        spots = ((0.12, 0.10, 0.70), (0.92, 0.18, 0.62), (0.78, 1.05, 0.75),
                 (0.30, 1.00, 0.60), (0.60, 0.45, 0.50))
        for i, ((fx, fy, fr), colour) in enumerate(zip(spots, self._blobs)):
            fx += 0.05 * math.sin(t * 0.21 + i * 1.7)
            fy += 0.08 * math.cos(t * 0.17 + i * 2.3)
            r = max(w, h) * fr
            g = QRadialGradient(QPointF(w * fx, h * fy), r)
            c = QColor(colour)
            c.setAlpha(200)
            g.setColorAt(0.0, c)
            c.setAlpha(0)
            g.setColorAt(1.0, c)
            p.fillRect(self.rect(), QBrush(g))
        # a light veil on the words' side, so the type reads on any blob
        veil = QLinearGradient(0, 0, w * 0.62, 0)
        veil.setColorAt(0.0, kit.with_alpha(tokens.PAPER.card, 0.72))
        veil.setColorAt(1.0, kit.with_alpha(tokens.PAPER.card, 0.0))
        p.fillRect(self.rect(), QBrush(veil))

        pane = self._pane()
        if pane is not None:
            kit.soft_shadow(p, pane, tokens.RADIUS_LG, "float", 0.8)
            p.save()
            box = QPainterPath()
            box.addRoundedRect(pane, tokens.RADIUS_LG, tokens.RADIUS_LG)
            p.setClipPath(box, Qt.ClipOperation.IntersectClip)
            p.fillRect(pane, QColor(self._look[1]))
            p.translate(pane.topLeft())
            p.scale(self.SCENE_SCALE, self.SCENE_SCALE)
            self._scene.paint(p)
            p.restore()

        # the words
        head, sub, _bottom = self._words()
        p.save()
        p.translate(self.PAD, self.TOP)
        head.drawContents(p)
        p.restore()
        if sub is not None:
            p.save()
            p.translate(self.PAD, self.TOP + head.size().height() + 10)
            sub.drawContents(p)
            p.restore()
        p.end()


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _italic(text: str, colour: str) -> str:
    """"Ask about *anything*" -> the starred words italic, in `colour`."""
    parts = _escape(text).split("*")
    return "".join(f'<i style="color:{colour}">{s}</i>' if i % 2 else s
                   for i, s in enumerate(parts))


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

    def enterEvent(self, e) -> None:
        self._hover = True
        self.actions.setVisible(True)
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self._hover = False
        self.actions.setVisible(False)
        self.update()
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def paintEvent(self, e) -> None:
        if self._hover:
            p = QPainter(self)
            p.fillRect(self.rect(), QColor(T.SURFACE_HOVER))
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


def history_row(entry: dict, opened: bool, on_toggle: Callable[[], None]) -> HoverRow:
    """One thing asked: when, the question, her answer (two lines, or all of
    it once opened), the app it was asked in; copy on hover."""
    from tutor_features.journal import WALKTHROUGH_TAG
    row = HoverRow()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(18, 14, 14, 14)
    lay.setSpacing(18)
    when = label(time.strftime("%I:%M%p", time.localtime(entry["created_at"])).lstrip("0").lower(), "rowTime")
    when.setFixedWidth(62)
    lay.addWidget(when, 0, Qt.AlignmentFlag.AlignTop)
    text = QVBoxLayout()
    text.setSpacing(4)
    question = label((entry.get("question") or "").strip(), "rowTitle", wrap=True)
    text.addWidget(question)
    answer = (entry.get("answer") or "").strip()
    walk = WALKTHROUGH_TAG in (entry.get("tags") or "")
    if walk:
        shown = f"Walkthrough · {len(answer.splitlines())} steps" if not opened else answer
    else:
        shown = answer if opened else _clip(answer, 180)
    if shown:
        text.addWidget(label(shown, "rowSub", wrap=True))
    lay.addLayout(text, 1)
    app = (entry.get("app_key") or "").strip()
    if app:
        chip = label(app, "rowApp")
        chip.setToolTip(entry.get("window_title") or "")
        lay.addWidget(chip, 0, Qt.AlignmentFlag.AlignTop)
    if answer:
        row.actions_lay.addWidget(RowAction(
            "copy", "Copy answer", lambda: QApplication.clipboard().setText(answer)))
    row.actions.setVisible(False)
    row.actions.setFixedWidth(34)
    lay.addWidget(row.actions, 0, Qt.AlignmentFlag.AlignTop)
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
