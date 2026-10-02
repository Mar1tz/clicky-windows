"""The tray's right-click popup: a small rounded card, not a Windows menu.

The tray used to open a 30-item QMenu - every voice, model, tutor switch and
journal option, three submenus deep - drawn by Windows in its own dark
chrome. Everything in it now lives in the app window (ui/app_pages.py), so
the tray only has to answer the questions you right-click it for: is Tora
on, what is she doing, how much plan is left, open the app, stop, quit.

    ╭──────────────────────────────╮
    │ heytora               ● Idle │
    │ Free plan · 23 talk left     │
    │──────────────────────────────│
    │ Open HeyTora                 │
    │ Floating panel          (●─) │
    │ Stop talking             Esc │
    │──────────────────────────────│
    │ Agents             2 running │
    │ Web search              (●─) │
    │ Always listening        (─○) │
    │──────────────────────────────│
    │ Settings                     │
    │ Check for updates            │
    │ Quit HeyTora                 │
    ╰──────────────────────────────╯

TrayManager describes the rows (ui/tray.py, _build_menu); this module only
draws them. Colours come from app_theme, so it follows light/dark with the
rest of the app.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from PyQt6.QtCore import QPoint, QPropertyAnimation, QRect, Qt, QTimer
from PyQt6.QtGui import QColor, QCursor, QGuiApplication
from PyQt6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from ui import app_theme as T
from ui import tokens


@dataclass
class Entry:
    """One row. kind: header | notice | item | switch | sep."""
    kind: str
    text: str = ""
    action: Optional[Callable] = None
    sub: str = ""                 # header: the plan line
    trailing: str = ""            # item: "2 running", "Esc"
    on: bool = False              # switch
    enabled: bool = True
    state: str = "idle"           # header: AppState name
    extra: dict = field(default_factory=dict)


WIDTH = 288
SHADOW = 18                       # room around the card for its shadow


class _Row(QFrame):
    """A clickable row with a rounded hover plate - the "pill"."""

    def __init__(self, entry: Entry, popup: "TrayPopup"):
        super().__init__()
        self._entry = entry
        self._popup = popup
        self.setObjectName("trayRow")
        self.setProperty("notice", entry.kind == "notice")
        self.setFixedHeight(34)
        self.setEnabled(entry.enabled)
        if entry.enabled:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(8)
        text = QLabel(entry.text)
        # By name, not by `QFrame#trayRow:disabled QLabel`: Qt ignores a
        # pseudo-state on an ancestor and greyed every row with that rule.
        text.setObjectName("trayRowText" if entry.enabled else "trayRowTextOff")
        lay.addWidget(text, 1)
        self.switch = None
        if entry.kind == "switch":
            from ui.app_widgets import Switch
            self.switch = Switch(entry.on)
            # The row is the target, not the 40 px switch inside it.
            self.switch.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            lay.addWidget(self.switch)
        elif entry.trailing:
            hint = QLabel(entry.trailing)
            hint.setObjectName("trayRowHint")
            lay.addWidget(hint)

    def mouseReleaseEvent(self, event) -> None:
        if not self.isEnabled() or event.button() != Qt.MouseButton.LeftButton:
            return
        if not self.rect().contains(event.position().toPoint()):
            return
        if self.switch is not None:
            # A switch flips in place and the popup stays: you can see it
            # took, and flip the next one.
            self.switch.setChecked(not self.switch.isChecked())
            if self._entry.action is not None:
                self._entry.action(self.switch.isChecked())
            return
        self._popup.close_then(self._entry.action)


class TrayPopup(QWidget):
    """Frameless, rounded, shadowed; closes on a click anywhere else."""

    def __init__(self):
        super().__init__(None, Qt.WindowType.Popup
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setObjectName("trayPopup")
        self._entries: list[Entry] = []
        self._fade: Optional[QPropertyAnimation] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW, SHADOW, SHADOW, SHADOW)
        self._card = QFrame()
        self._card.setObjectName("trayCard")
        self._card.setFixedWidth(WIDTH)
        self._shadow = QGraphicsDropShadowEffect(self._card)
        self._shadow.setBlurRadius(36)
        self._shadow.setOffset(0, 8)
        self._card.setGraphicsEffect(self._shadow)
        outer.addWidget(self._card)
        self._body = QVBoxLayout(self._card)
        self._body.setContentsMargins(6, 6, 6, 6)
        self._body.setSpacing(1)
        self.retheme()

    # ── content ──────────────────────────────────────────────────────────────

    def set_entries(self, entries: list[Entry]) -> None:
        self._entries = list(entries)
        lay = self._body
        while lay.count():
            old = lay.takeAt(0).widget()
            if old is not None:
                old.setParent(None)
                old.deleteLater()
        for entry in self._entries:
            if entry.kind == "sep":
                line = QFrame()
                line.setObjectName("traySep")
                line.setFixedHeight(1)
                wrap = QVBoxLayout()
                wrap.setContentsMargins(10, 5, 10, 5)
                wrap.addWidget(line)
                holder = QWidget()
                holder.setLayout(wrap)
                lay.addWidget(holder)
            elif entry.kind == "header":
                lay.addWidget(self._header(entry))
            else:
                lay.addWidget(_Row(entry, self))
        self._card.adjustSize()
        self.adjustSize()

    def _header(self, entry: Entry) -> QWidget:
        from ui.app_widgets import Dot, wordmark
        head = QWidget()
        head.setObjectName("trayHead")
        v = QVBoxLayout(head)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(wordmark(18), 0, Qt.AlignmentFlag.AlignVCenter)
        top.addStretch(1)
        state = (entry.state or "idle").lower()
        # The one blue dot: lit while Tora is listening, thinking or talking.
        top.addWidget(Dot(T.ACCENT if state != "idle" else T.TEXT_FAINT, 7),
                      0, Qt.AlignmentFlag.AlignVCenter)
        words = QLabel(state.capitalize())
        words.setObjectName("trayState")
        top.addWidget(words, 0, Qt.AlignmentFlag.AlignVCenter)
        v.addLayout(top)
        if entry.sub:
            sub = QLabel(entry.sub)
            sub.setObjectName("traySub")
            sub.setWordWrap(True)
            v.addWidget(sub)
        return head

    def texts(self) -> list[str]:
        """Every row's words, in order - what a test (or a screen reader)
        reads off the popup."""
        out = []
        for e in self._entries:
            if e.kind == "header":
                out += [w for w in (e.text, e.sub) if w]
            elif e.kind != "sep":
                out.append(e.text)
        return out

    def entry(self, text: str) -> Optional[Entry]:
        return next((e for e in self._entries if e.text == text), None)

    # ── look ─────────────────────────────────────────────────────────────────

    def retheme(self) -> None:
        self._shadow.setColor(QColor(0, 0, 0, 70 if not T.is_dark() else 170))
        self.setStyleSheet(f"""
            QWidget {{ font-family: "{T.font()}", "{tokens.FALLBACK_FONT}"; }}
            QFrame#trayCard {{
                background: {T.SURFACE}; border: 1px solid {T.BORDER};
                border-radius: 14px;
            }}
            QWidget#trayHead {{ background: transparent; }}
            QLabel#trayState {{ color: {T.TEXT_DIM}; font-size: 12px; font-weight: 600; }}
            QLabel#traySub   {{ color: {T.TEXT_DIM}; font-size: 12px; }}
            QFrame#traySep   {{ background: {T.BORDER}; border: none; }}
            QFrame#trayRow   {{ background: transparent; border: none; border-radius: 8px; }}
            QFrame#trayRow:hover {{ background: {T.SURFACE_HOVER}; }}
            QFrame#trayRow[notice="true"] {{ background: {T.ACCENT_SOFT}; }}
            QFrame#trayRow[notice="true"]:hover {{ background: {T.ACCENT_PLATE}; }}
            QLabel#trayRowText {{ color: {T.TEXT}; font-size: 13px; font-weight: 500; background: transparent; }}
            QFrame#trayRow[notice="true"] QLabel#trayRowText {{ color: {T.ACCENT}; font-weight: 700; }}
            QLabel#trayRowTextOff {{ color: {T.TEXT_FAINT}; font-size: 13px; font-weight: 500; background: transparent; }}
            QLabel#trayRowHint {{ color: {T.TEXT_FAINT}; font-size: 12px; font-weight: 500; background: transparent; }}
        """)
        if self._entries:
            self.set_entries(self._entries)

    # ── showing ──────────────────────────────────────────────────────────────

    def popup_at(self, anchor: Optional[QPoint] = None) -> None:
        """Open beside the pointer, inside the screen it is on.

        The notification area is nearly always bottom-right, so the card
        opens up and to the left of the click; it flips when that would
        leave the screen (a taskbar on the top or the left)."""
        anchor = anchor or QCursor.pos()
        self.adjustSize()
        screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        area: QRect = screen.availableGeometry()
        w, h = self.width(), self.height()
        x = anchor.x() - w + SHADOW + 8
        y = anchor.y() - h + SHADOW - 4
        if y < area.top():
            y = anchor.y() - SHADOW + 4
        x = max(area.left() - SHADOW, min(x, area.right() - w + SHADOW))
        y = max(area.top() - SHADOW, min(y, area.bottom() - h + SHADOW))
        self.move(x, y)
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self.activateWindow()
        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setDuration(tokens.DURATION_FAST)
        fade.setEasingCurve(tokens.ease())
        fade.start()
        self._fade = fade

    def close_then(self, action: Optional[Callable]) -> None:
        """Close first, act after: an action that opens a window must not
        open it under a popup that is still on its way out."""
        self.hide()
        if action is not None:
            QTimer.singleShot(0, action)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)
