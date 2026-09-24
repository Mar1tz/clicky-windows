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

Every page is live (APP-10 to APP-19): it reads the running companion
through ui/app_bridge.py, and every control goes down the same path as the
tray item it replaces. The old floating windows still open from the tray.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QPoint, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QStackedWidget,
    QVBoxLayout, QWidget,
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


class ToraApp(QWidget):
    """The single window. `show_page("agents")` is the whole API for now."""

    page_changed = pyqtSignal(str)

    # A burst of changes (a walkthrough step, an agent event, a state flip)
    # redraws the page once, not once per event.
    REFRESH_MS = 150

    def __init__(self, bridge=None):
        super().__init__()
        from ui.app_bridge import AppBridge
        # The one door into the running app (APP-10). Without one the window
        # still opens - it just has nothing live to show.
        self.bridge = bridge if bridge is not None else AppBridge()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(self.REFRESH_MS)
        self._refresh_timer.timeout.connect(self._refresh_now)
        self._pending_topics: set[str] = set()
        self.bridge.changed.connect(self._on_changed)
        self.setWindowTitle("Tora")
        self.setObjectName("appRoot")
        # A NATIVE window frame, not a hand-rolled one. The first draft was
        # frameless and translucent so the corners could be rounded; on a
        # real desktop the strip the custom title bar sat in stayed
        # transparent and the windows BEHIND Tora showed through it. Windows
        # 11 already rounds every window, draws the shadow, and does snapping
        # and drag properly - all of which a frameless window gives up in
        # exchange for a rectangle we then have to paint ourselves.
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
        self._stack.setObjectName("stack")

        shell.addWidget(self._build_sidebar())
        shell.addWidget(self._stack, 1)

        self.show_page(PAGES[0][0])

    def _dark_titlebar(self) -> None:
        """Ask DWM for a dark title bar, so the native frame is not a bright
        white band above a near-black app. Silently does nothing off Windows
        or on a build that does not know the attribute."""
        try:
            import ctypes
            hwnd = int(self.winId())
            for attribute in (20, 19):       # 20 = Win11/late Win10, 19 = early
                value = ctypes.c_int(1)
                ok = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    ctypes.c_void_p(hwnd), ctypes.c_int(attribute),
                    ctypes.byref(value), ctypes.sizeof(value))
                if ok == 0:
                    return
        except Exception:                    # noqa: BLE001 - cosmetic only
            pass

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
        hotkey = self.bridge.hotkey() or "Ctrl + Win"
        lay.addWidget(label(f"Hold  {hotkey}  to talk", "faint"))
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
                    widget = cls(self.bridge)
                    widget.navigate.connect(self.show_page)
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
        else:
            # Shown again: a live page shows what is true now, not what was
            # true the last time someone looked.
            self._pages[key].refresh()
        self._stack.setCurrentWidget(self._pages[key])
        button = self._buttons.get(key)
        if button is not None and not button.isChecked():
            button.setChecked(True)
        heading = next((t for k, t, *_ in PAGES if k == key), "")
        self.setWindowTitle(f"Tora  ·  {heading}")
        self.page_changed.emit(key)

    def set_state(self, state_name: str) -> None:
        """Mirror the companion's AppState in the sidebar pill."""
        name = (state_name or "idle").lower()
        self._state_dot.set_colour(T.STATE_COLOUR.get(name, T.TEXT_FAINT))
        self._state_label.setText(name.capitalize())

    def _on_changed(self, topic: str) -> None:
        if topic == "state":
            self.set_state(self.bridge.state())
        self._pending_topics.add(topic)
        self._refresh_timer.start()          # restarts: one redraw per burst

    def _refresh_now(self) -> None:
        """Redraw the page on screen - if it is live, anyone can see it, and
        what changed is something it shows (Page.TOPICS)."""
        topics, self._pending_topics = self._pending_topics, set()
        page = self._stack.currentWidget()
        if (page is not None and self.isVisible() and getattr(page, "LIVE", False)
                and set(getattr(page, "TOPICS", ())) & topics):
            page.refresh()

    # ── behaviour ────────────────────────────────────────────────────────────

    def open_at(self, key: str = "") -> None:
        """Bring the window up, optionally on a particular page."""
        if key:
            self.show_page(key)
        self.show()
        self._dark_titlebar()       # needs a real window handle, so: after show
        self.raise_()
        self.activateWindow()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)
