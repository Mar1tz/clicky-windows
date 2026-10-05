"""One window for the whole of HeyTora.

Everything Tora can do used to live in its own floating window, reached
through the tray menu. This is the one place that shows what the product
is, laid out the way Wispr Flow lays out its app (DESIGN.md §3B):

    ┌─[≡][◯]─────────────────────────────── [●Listening] [🔔] ─ ▢ ✕ ┐
    │ heytora      ╭──────────────────────────────────────────────╮ │
    │ ⌂ Home       │  Hey Dylan, just hold [Ctrl + Win] and ask   │ │
    │ ◇ Agents     │                                              │ │
    │ ▢ Journal    │  …the page…                                  │ │
    │ ▦ Packs      │                                              │ │
    │ ✦ Tora       │                                              │ │
    │              │                                              │ │
    │ 🎁 Free month │                                              │ │
    │ ⚙ Settings   │                                              │ │
    │ ? Help       ╰──────────────────────────────────────────────╯ │
    └────────────────────────────────────────────────────────────────┘

The title bar is the app's own (ui/window_frame.py keeps it a real Windows
window). The sidebar closes to icons; a pack with a dashboard (Accounting)
gets its own row while it is on. Profile, notifications, the free month and
help open as cards (ui/app_popover.py).

Every page is live (APP-10 to APP-19): it reads the running companion
through ui/app_bridge.py, and every control goes down the same path as the
tray item it replaces.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractButton, QButtonGroup, QFrame, QHBoxLayout, QStackedWidget,
    QVBoxLayout, QWidget,
)

from ui import app_popover
from ui import app_theme as T
from ui.app_pages import PACK_ROWS, PAGES
from ui.app_shell import BrandRow, IconButton, NavItem, Sidebar, WindowButton
from ui.app_widgets import Dot, label
from ui.window_frame import NativeFrame


class ToraApp(QWidget):
    """The single window. `show_page("agents")` is the whole API."""

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
        self.setWindowTitle("HeyTora")
        self.setObjectName("appRoot")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        # A QWidget subclass paints no stylesheet background without this.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.resize(T.WINDOW_W, T.WINDOW_H)
        self.setMinimumSize(T.WINDOW_MIN_W, T.WINDOW_MIN_H)
        self.setStyleSheet(T.qss())

        self._pages: dict[str, QWidget] = {}
        self._buttons: dict[str, NavItem] = {}
        self._settings = None                 # the Settings dialog, made when first opened
        self._stack = QStackedWidget()
        self._stack.setObjectName("stack")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_top_bar())
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 8, 8)
        body.setSpacing(0)
        body.addWidget(self._build_sidebar())
        panel = QFrame()
        panel.setObjectName("panel")
        self._panel = panel
        inner = QVBoxLayout(panel)
        inner.setContentsMargins(1, 1, 1, 1)
        inner.addWidget(self._stack)
        body.addWidget(panel, 1)
        root.addLayout(body, 1)

        self._frame = NativeFrame(self, self._is_caption, self._max_button,
                                  self._max_button.set_hot)
        self._frame.install()
        self.show_page(PAGES[0][0])

    # ── title bar ────────────────────────────────────────────────────────────

    def _build_top_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("topBar")
        bar.setFixedHeight(T.TITLEBAR_H)
        lay = QHBoxLayout(bar)
        # The sidebar button sits over the sidebar's icon column.
        lay.setContentsMargins(15, 0, 0, 0)
        lay.setSpacing(4)
        self._sidebar_button = IconButton("sidebar", "Close sidebar")
        self._sidebar_button.clicked.connect(self.toggle_sidebar)
        lay.addWidget(self._sidebar_button)
        self._profile_button = IconButton("profile", "Your account")
        self._profile_button.clicked.connect(self.open_profile)
        lay.addWidget(self._profile_button)
        lay.addStretch(1)

        # Tora's state, only while she is doing something: idle is the
        # normal case and says nothing.
        self._state_chip = QWidget()
        chip = QHBoxLayout(self._state_chip)
        chip.setContentsMargins(0, 0, 10, 0)
        chip.setSpacing(7)
        self._state_dot = Dot(T.STATE_COLOUR["idle"], 7)
        chip.addWidget(self._state_dot)
        self._state_label = label("Idle", "stateChip")
        chip.addWidget(self._state_label)
        self._state_chip.hide()
        lay.addWidget(self._state_chip)

        self._bell = IconButton("notifications", "Notifications")
        self._bell.clicked.connect(self.open_notifications)
        lay.addWidget(self._bell)
        lay.addSpacing(10)
        for kind in ("min", "max", "close"):
            b = WindowButton(kind)
            b.clicked.connect({"min": self.showMinimized, "max": self._toggle_max,
                               "close": self.close}[kind])
            lay.addWidget(b)
            if kind == "max":
                self._max_button = b
        self._top_bar = bar
        return bar

    def _is_caption(self, local: QPoint) -> bool:
        """Empty title bar - drag, double-click, snap - anywhere in it that
        is not a button."""
        bar = self._top_bar
        if not bar.geometry().contains(local):
            return False
        child = bar.childAt(bar.mapFrom(self, local))
        while child is not None and child is not bar:
            if isinstance(child, QAbstractButton):
                return False
            child = child.parentWidget()
        return True

    def _toggle_max(self) -> None:
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def paintEvent(self, event) -> None:
        """A soft shadow under the panel the pages sit on (Wispr's), in
        place of an outline. Painted here, under it, rather than as a
        QGraphicsDropShadowEffect: an effect would re-render the whole page
        into an image on every repaint. In dark the panel keeps a hairline
        instead - a shadow on black does not show."""
        super().paintEvent(event)
        if T.is_dark() or not hasattr(self, "_panel"):
            return
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QPainter
        from ui import kit
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        kit.soft_shadow(p, QRectF(self._panel.geometry()), T.PANEL_RADIUS, "card", 0.55)
        p.end()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if hasattr(self, "_max_button"):
            self._max_button.update()        # maximise <-> restore glyph

    # ── sidebar ──────────────────────────────────────────────────────────────

    def _build_sidebar(self) -> QWidget:
        open_ = not self._call("sidebar_collapsed", False)
        self._want_open = open_
        bar = Sidebar(open_)
        self._sidebar = bar
        openness = lambda: bar.openness          # noqa: E731
        self._brand = BrandRow(openness)
        bar.lay.addWidget(self._brand)
        bar.lay.addSpacing(10)

        group = QButtonGroup(self)
        group.setExclusive(True)
        for key, text, place, _cls in PAGES:
            if place == "hidden":
                continue
            item = NavItem(key, text, openness)
            item.clicked.connect(lambda _c=False, k=key: self.show_page(k))
            group.addButton(item)
            bar.lay.addWidget(item)
            self._buttons[key] = item
        # Under the list: the free month, Settings (a dialog, not a page) and Help.
        bar.lay.addStretch(1)
        bar.lay.addWidget(self._action_item("free_month", "Get a free month",
                                            self.open_free_month, openness))
        bar.lay.addWidget(self._action_item("settings", "Settings",
                                            lambda: self.open_settings(), openness))
        bar.lay.addWidget(self._action_item("help", "Help", self.open_help, openness))
        bar.set_open(open_, animate=False)
        self._sidebar_button.setToolTip("Close sidebar" if open_ else "Open sidebar")
        self._sync_pack_rows()
        return bar

    def _action_item(self, key: str, text: str, action, openness) -> NavItem:
        item = NavItem(key, text, openness, page=False)
        item.clicked.connect(lambda _c=False: action())
        self._buttons[key] = item
        return item

    def toggle_sidebar(self) -> None:
        on = not self._sidebar.is_open()
        self._want_open = on
        self._show_sidebar(on)
        setter = getattr(self.bridge, "set_sidebar_collapsed", None)
        if setter is not None:
            setter(not on)

    def _show_sidebar(self, on: bool) -> None:
        self._sidebar.set_open(on, animate=self.isVisible())
        self._sidebar_button.setToolTip("Close sidebar" if on else "Open sidebar")

    # Narrower than this, the open sidebar would leave the pages less room
    # than their widest row (Home's four tiles), so it shuts by itself and
    # opens again when there is room - the choice is still the user's.
    AUTO_SHUT_W = 1100

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not hasattr(self, "_sidebar"):
            return
        want = self._want_open and self.width() >= self.AUTO_SHUT_W
        if want != self._sidebar.is_open():
            self._show_sidebar(want)

    def _sync_pack_rows(self) -> None:
        """A pack's own page has a row only while the pack is on."""
        for key, ext_id in PACK_ROWS.items():
            item = self._buttons.get(key)
            if item is not None:
                item.setVisible(bool(self._call("extension_on", False, ext_id)))

    # ── cards from the title bar and sidebar ─────────────────────────────────

    def _below_top_right(self) -> QPoint:
        return self.mapToGlobal(QPoint(self.width() - 8, T.TITLEBAR_H - 2))

    def open_profile(self) -> None:
        def sign_out() -> None:
            from ui.app_pages import confirm_sign_out
            if confirm_sign_out(self):
                self.bridge.account_sign_out()
        self._popover = app_popover.profile_card(self.bridge, sign_out)
        self._popover.open_at(self._below_top_right(), "right")

    def open_notifications(self) -> None:
        self._popover = app_popover.notifications_card(self.bridge)
        self._popover.open_at(self._below_top_right(), "right")

    def _beside(self, key: str) -> QPoint:
        item = self._buttons[key]
        return item.mapToGlobal(QPoint(item.width() + 10, -8))

    def open_free_month(self) -> None:
        self._popover = app_popover.free_month_card()
        self._popover.open_at(self._beside("free_month"), "left")

    def open_help(self) -> None:
        self._popover = app_popover.help_card(self.bridge, self.show_page)
        self._popover.open_at(self._beside("help"), "left")

    # ── navigation ───────────────────────────────────────────────────────────

    def open_settings(self, tab: str = "general") -> None:
        """Settings is a dialog over the window (ui/settings_dialog.py)."""
        if self._settings is None:
            from ui.settings_dialog import SettingsDialog
            self._settings = SettingsDialog(self, self.bridge)
        self._settings.open(tab)

    def show_page(self, key: str) -> None:
        """Switch to a page, building it the first time it is asked for.

        Lazily, because a window of pages is a visible pause on a cold start
        and most sessions open one of them. "settings" (or "settings/agents"
        for a tab) opens the Settings dialog; "setup" is its Advanced tab.
        """
        if key == "setup" or key.startswith("settings"):
            self.open_settings("advanced" if key == "setup" else key.partition("/")[2] or "general")
            return
        if key not in self._pages:
            for k, _text, _place, cls in PAGES:
                if k == key:
                    widget = cls(self.bridge)
                    widget.navigate.connect(self.show_page)
                    self._pages[key] = widget
                    self._stack.addWidget(widget)
                    # A page builds its labels before it is parented here, so
                    # their size hints were measured at the default 13px.
                    # Re-applying the sheet re-polishes the new subtree.
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
        elif button is None:
            checked = next((b for b in self._buttons.values() if b.isChecked()), None)
            if checked is not None:          # a page with no row: nothing lit
                group = checked.group()
                group.setExclusive(False)
                checked.setChecked(False)
                group.setExclusive(True)
        self.page_changed.emit(key)

    def retheme(self) -> None:
        """Windows (or Settings) switched light/dark: restyle in place.

        The pages are kept rather than rebuilt - the Agents page embeds a
        live panel - so this re-applies the sheet, repaints what is painted
        by hand and refreshes the page on screen."""
        self.setStyleSheet(T.qss())
        self._brand.retheme()
        self.set_state(self.bridge.state() if hasattr(self.bridge, "state") else "idle")
        page = self._stack.currentWidget()
        if page is not None:
            page.refresh()
        for w in self.findChildren(QWidget):
            w.update()

    def set_state(self, state_name: str) -> None:
        """Tora's AppState in the title bar, while she is not idle."""
        name = (state_name or "idle").lower()
        if name not in T.STATE_COLOUR:
            name = "idle"
        self._state_dot.set_colour(T.STATE_COLOUR[name])
        self._state_label.setText(name.capitalize())
        self._state_chip.setVisible(name != "idle")

    def _call(self, name: str, default, *args):
        fn = getattr(self.bridge, name, None)
        try:
            return fn(*args) if fn is not None else default
        except Exception:  # noqa: BLE001 - a bridge without it: the default
            return default

    def _on_changed(self, topic: str) -> None:
        if topic == "state":
            self.set_state(self.bridge.state())
        if topic == "extensions":
            self._sync_pack_rows()
        self._pending_topics.add(topic)
        self._refresh_timer.start()          # restarts: one redraw per burst

    def _refresh_now(self) -> None:
        """Redraw the page on screen - if it is live, anyone can see it, and
        what changed is something it shows (Page.TOPICS)."""
        if not self.isVisible():
            return          # kept: showEvent redraws when the window is back
        topics, self._pending_topics = self._pending_topics, set()
        shown = [self._stack.currentWidget()]
        if self._settings is not None and self._settings.isVisible():
            shown.append(self._settings.stack.currentWidget())     # the open Settings tab too
        for page in shown:
            if (page is not None and getattr(page, "LIVE", False)
                    and set(getattr(page, "TOPICS", ())) & topics):
                # A page that can redraw just what changed (the plan, an
                # update) does; anything else is rebuilt.
                partial = getattr(page, "refresh_topics", None)
                if partial is not None:
                    partial(topics)
                else:
                    page.refresh()

    def showEvent(self, event) -> None:
        """Back on screen after being closed: the page shows what is true now,
        including anything that changed while nobody could see it."""
        super().showEvent(event)
        self._pending_topics.clear()
        shown = [self._stack.currentWidget()]
        if self._settings is not None and not self._settings.isHidden():
            shown.append(self._settings.stack.currentWidget())     # the open Settings tab too
        for page in shown:
            if page is not None and getattr(page, "LIVE", False):
                page.refresh()

    def nativeEvent(self, kind, message):
        answer = self._frame.handle(message) if kind == b"windows_generic_MSG" else None
        # Not super().nativeEvent(): on PyQt6 6.11 that call is an access
        # violation. (False, 0) is what it would answer - "not handled".
        return answer if answer is not None else (False, 0)

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
            if self._settings is not None and self._settings.isVisible():
                self._settings.close_dialog()     # Escape closes Settings first
                return
            self.hide()
            return
        super().keyPressEvent(event)
