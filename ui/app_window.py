"""One window for the whole of Tora.

Everything Tora can do currently lives in its own floating window, reached
through the tray menu: Agents, Agent settings, Extensions, Dashboard, API
Keys, the setup wizard, plus a dozen checkboxes buried three menus deep.
Each was easy to add and, together, they are impossible to find — there is
no one place that shows what the product actually is.

This is that place: a navigation rail on the left, one page at a time on the
right, and no pop-ups. Opened from the tray with "Open Tora app".

    ┌────────────┬──────────────────────────────────────────┐
    │  TORA      │  Agents                        [Stop all]│
    │            │  Work Tora is doing for you…             │
    │  MAIN      │  ┌────────────────────────────────────┐  │
    │  ◆ Home    │  │ Application Builder                │  │
    │  ◈ Agents  │  │ Step 4 of 10 ████████░░░  70%      │  │
    │  ▣ Exten…  │  └────────────────────────────────────┘  │
    │            │                                          │
    │  ● Idle    │                                          │
    └────────────┴──────────────────────────────────────────┘

Nothing is wired to the running app yet, by request: this is the shape and
the look, so the placement can be judged before any of it is made live. The
old windows are untouched and still open from the tray, so nothing is lost
while this is being finished.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QPoint, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QSizeGrip,
    QStackedWidget, QVBoxLayout, QWidget,
)

from ui import app_theme as T
from ui.app_pages import PAGES
from ui.app_widgets import Dot, label, nav_icon


class _NavItem(QPushButton):
    """One row in the rail. Checkable so Qt draws the selected state.

    The text is escaped: a QPushButton reads "&" as a mnemonic, so
    "Voice & models" was rendering as "Voice  models" with an underlined m.
    """

    def __init__(self, kind: str, text: str):
        super().__init__("  " + text.replace("&", "&&"))
        self.setObjectName("navItem")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(38)
        self._kind = kind
        self.setIconSize(QSize(16, 16))
        self._paint_icon(False)
        self.toggled.connect(self._paint_icon)

    def _paint_icon(self, on: bool) -> None:
        self.setIcon(QIcon(nav_icon(self._kind, T.TEXT if on else T.TEXT_DIM)))


class _TitleBar(QWidget):
    """Drag to move, double-click to maximise, and the three buttons.

    The window is frameless so the sidebar can run to the very edge and the
    corners can be rounded; that means the title bar is ours to draw.
    """

    def __init__(self, window: "ToraApp"):
        super().__init__()
        self.setObjectName("titleBar")
        self.setFixedHeight(T.TITLEBAR_H)
        self._window = window
        self._press: Optional[QPoint] = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 0, 10, 0)
        lay.setSpacing(8)
        self._title = QLabel("Tora")
        self._title.setObjectName("windowTitle")
        lay.addWidget(self._title)
        lay.addStretch(1)

        for text, name, slot in (
            ("−", "winBtn", self._window.showMinimized),   # minus
            ("□", "winBtn", self._toggle_max),             # white square
            ("×", "winClose", self._window.hide),          # multiplication
        ):
            b = QPushButton(text)
            b.setObjectName(name if name != "winClose" else "winBtn")
            if name == "winClose":
                b.setObjectName("winBtn")
                b.setProperty("close", True)
                b.setStyleSheet(
                    f"QPushButton:hover {{ background: {T.DANGER}; color: white; }}")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(slot)
            lay.addWidget(b)

    def set_title(self, text: str) -> None:
        self._title.setText(f"Tora  ·  {text}")

    def _toggle_max(self) -> None:
        w = self._window
        w.showNormal() if w.isMaximized() else w.showMaximized()

    # -- dragging --
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.globalPosition().toPoint() - \
                self._window.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:
        if self._press is not None and not self._window.isMaximized():
            self._window.move(event.globalPosition().toPoint() - self._press)

    def mouseReleaseEvent(self, _event) -> None:
        self._press = None

    def mouseDoubleClickEvent(self, _event) -> None:
        self._toggle_max()


class ToraApp(QWidget):
    """The single window. `show_page("agents")` is the whole API for now."""

    page_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tora")
        self.setObjectName("appRoot")
        self.setWindowFlags(Qt.WindowType.Window |
                            Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # Qt does not paint a stylesheet background on a QWidget SUBCLASS
        # unless this is set - it does on a plain QWidget, which is why the
        # sidebar looked right and the page header did not: the header sat
        # on the translucent window with nothing painted behind it, so its
        # title and buttons came out ghosted.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.resize(T.WINDOW_W, T.WINDOW_H)
        self.setMinimumSize(T.WINDOW_MIN_W, T.WINDOW_MIN_H)
        self.setStyleSheet(T.qss())

        shell = QHBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        self._pages: dict[str, QWidget] = {}
        self._buttons: dict[str, _NavItem] = {}
        self._stack = QStackedWidget()

        shell.addWidget(self._build_sidebar())

        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(0)
        self._titlebar = _TitleBar(self)
        right_lay.addWidget(self._titlebar)
        right_lay.addWidget(self._stack, 1)

        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, 4, 4)
        grip_row.addStretch(1)
        grip_row.addWidget(QSizeGrip(self), 0,
                           Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
        right_lay.addLayout(grip_row)
        shell.addWidget(right, 1)

        self.show_page(PAGES[0][0])

    # ── sidebar ──────────────────────────────────────────────────────────────

    def _build_sidebar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("sidebar")
        bar.setFixedWidth(T.SIDEBAR_W)
        lay = QVBoxLayout(bar)
        lay.setContentsMargins(12, 18, 12, 14)
        lay.setSpacing(4)

        mark = QHBoxLayout()
        mark.setContentsMargins(8, 0, 0, 0)
        mark.setSpacing(10)
        mark.addWidget(Dot(T.ACCENT, 9, glow=True), 0,
                       Qt.AlignmentFlag.AlignVCenter)
        words = QVBoxLayout()
        words.setSpacing(0)
        words.addWidget(label("TORA", "wordmark"))
        words.addWidget(label("YOUR COMPUTER, EXPLAINED", "wordmarkSub"))
        mark.addLayout(words)
        mark.addStretch(1)
        lay.addLayout(mark)
        lay.addSpacing(22)

        group = QButtonGroup(self)
        group.setExclusive(True)
        last_section = ""
        for key, text, glyph, _page, section_name in PAGES:
            if section_name != last_section:
                if last_section:
                    lay.addSpacing(14)
                lay.addWidget(label(section_name, "navGroup"))
                lay.addSpacing(6)
                last_section = section_name
            item = _NavItem(key, text)
            item.clicked.connect(lambda _checked, k=key: self.show_page(k))
            group.addButton(item)
            lay.addWidget(item)
            self._buttons[key] = item

        lay.addStretch(1)

        status = QFrame()
        status.setObjectName("cardQuiet")
        sl = QHBoxLayout(status)
        sl.setContentsMargins(12, 9, 12, 9)
        sl.setSpacing(9)
        self._state_dot = Dot(T.STATE_COLOUR["idle"], 8)
        sl.addWidget(self._state_dot)
        self._state_label = label("Idle", "dim")
        sl.addWidget(self._state_label, 1)
        lay.addWidget(status)
        lay.addSpacing(8)
        lay.addWidget(label("Hold  Ctrl + Win  to talk", "faint"))
        return bar

    # ── navigation ───────────────────────────────────────────────────────────

    def show_page(self, key: str) -> None:
        """Switch to a page, building it the first time it is asked for.

        Lazily, because nine pages of cards is a visible pause on a cold
        start and most sessions open one of them.
        """
        if key not in self._pages:
            for k, _text, _glyph, cls, _sec in PAGES:
                if k == key:
                    widget = cls()
                    self._pages[key] = widget
                    self._stack.addWidget(widget)
                    # A page builds its labels before it is parented here, so
                    # their size hints were measured at the default 13px —
                    # which left the 24px page title rendering into a 13px
                    # box, clipped and half-transparent. Re-applying the
                    # sheet re-polishes the new subtree with the real fonts.
                    self.setStyleSheet(T.qss())
                    break
            else:
                return
        self._stack.setCurrentWidget(self._pages[key])
        button = self._buttons.get(key)
        if button is not None and not button.isChecked():
            button.setChecked(True)
        heading = next((t for k, t, *_ in PAGES if k == key), "")
        self._titlebar.set_title(heading)
        self.page_changed.emit(key)

    def set_state(self, state_name: str) -> None:
        """Mirror the companion's AppState in the sidebar pill."""
        name = (state_name or "idle").lower()
        self._state_dot.set_colour(T.STATE_COLOUR.get(name, T.TEXT_FAINT))
        self._state_label.setText(name.capitalize())

    # ── behaviour ────────────────────────────────────────────────────────────

    def open_at(self, key: str = "") -> None:
        """Bring the window up, optionally on a particular page."""
        if key:
            self.show_page(key)
        self.show()
        self.raise_()
        self.activateWindow()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)
