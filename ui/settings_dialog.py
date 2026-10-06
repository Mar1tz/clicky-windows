"""Settings, as Wispr Flow does it: a dialog over the app, a menu down its
side, one tab at a time (DESIGN.md §3D).

It is an overlay inside the app window, not a second window: the app dims
behind it, it moves and resizes with the window, and Escape or a click on
the dimmed part closes it. Tabs (ui/settings_tabs.py) are built the first
time they are opened, like the app's pages.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QEvent, QRect, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QStackedWidget, QVBoxLayout, QWidget,
)

from ui import app_theme as T
from ui import kit
from ui.app_shell import NavItem
from ui.app_widgets import icon, label


class SettingsDialog(QWidget):
    """Lives on the app window; open(tab) shows it."""

    W, H = 1000, 720           # at most; less on a small window
    NAV_W = 232

    def __init__(self, window: QWidget, bridge):
        super().__init__(window)
        from ui.settings_tabs import TABS
        self.bridge = bridge
        self._tabs: dict[str, QWidget] = {}
        self._kinds = {key: cls for _sec, rows in TABS for key, _w, _i, cls in rows}
        self._rows: dict[str, NavItem] = {}
        self.setObjectName("settingsOverlay")
        self.hide()
        window.installEventFilter(self)

        self.card = QFrame(self)
        self.card.setObjectName("settingsCard")
        kit.drop_shadow(self.card, "hero")
        lay = QHBoxLayout(self.card)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        nav = QFrame()
        nav.setObjectName("settingsNav")
        nav.setFixedWidth(self.NAV_W)
        col = QVBoxLayout(nav)
        col.setContentsMargins(12, 20, 12, 16)
        col.setSpacing(2)
        group = QButtonGroup(self)
        group.setExclusive(True)
        for i, (section, rows) in enumerate(TABS):
            if i:
                col.addSpacing(12)
                line = QFrame()
                line.setObjectName("hr")
                line.setFixedHeight(1)
                col.addWidget(line)
                col.addSpacing(12)
            head = label(section.upper(), "sectionTitle")
            head.setContentsMargins(12, 0, 0, 6)
            col.addWidget(head)
            for key, words, icon_name, _cls in rows:
                row = NavItem(icon_name, words, lambda: 1.0)
                row.clicked.connect(lambda _c=False, k=key: self.select(k))
                group.addButton(row)
                col.addWidget(row)
                self._rows[key] = row
        col.addStretch(1)
        foot = QHBoxLayout()
        foot.setContentsMargins(12, 0, 6, 0)
        self._version = label("", "faint")
        foot.addWidget(self._version)
        foot.addStretch(1)
        self._update_icon = QLabel()
        foot.addWidget(self._update_icon)
        col.addLayout(foot)
        lay.addWidget(nav)

        self.stack = QStackedWidget()
        self.stack.setObjectName("settingsStack")
        lay.addWidget(self.stack, 1)

    # ── showing ──────────────────────────────────────────────────────────────

    def open(self, tab: str = "general") -> None:
        self.setGeometry(self.parentWidget().rect())
        self._footer()
        self.show()
        self.raise_()
        self.select(tab if tab in self._kinds else "general")
        self.setFocus()

    def close_dialog(self) -> None:
        self.hide()

    def current(self) -> str:
        return next((k for k, w in self._tabs.items() if w is self.stack.currentWidget()), "")

    def tab(self, key: str) -> QWidget:
        """The tab for `key`, built the first time it is asked for."""
        if key not in self._tabs:
            page = self._kinds[key](self.bridge)
            page.navigate.connect(self._navigate)
            self._tabs[key] = page
            self.stack.addWidget(page)
            # Built before it was parented: re-polish with the real sheet.
            self.window().setStyleSheet(T.qss())
        return self._tabs[key]

    def select(self, key: str) -> None:
        fresh = key not in self._tabs
        page = self.tab(key)
        if not fresh:
            page.refresh()
        self.stack.setCurrentWidget(page)
        self._rows[key].setChecked(True)

    def _navigate(self, key: str) -> None:
        """A tab asking for an app page: close, and go there."""
        self.close_dialog()
        show = getattr(self.window(), "show_page", None)
        if show is not None:
            show(key)

    def _footer(self) -> None:
        """"HeyTora 1.7.0", and a cloud: ticked when up to date, an arrow
        when an update is waiting (Wispr's footer)."""
        version = ""
        try:
            version = self.bridge.version()
        except Exception:  # noqa: BLE001
            pass
        self._version.setText(f"HeyTora {version}" if version else "HeyTora")
        state = ""
        try:
            state = self.bridge.update_state().get("state", "")
        except Exception:  # noqa: BLE001
            pass
        waiting = state in ("ready", "downloading")
        self._update_icon.setPixmap(icon("cloud-download" if waiting else "cloud-check",
                                         T.ACCENT if waiting else T.TEXT_FAINT, 16))
        self._update_icon.setToolTip("An update is ready" if waiting else "Up to date")

    # ── the overlay ──────────────────────────────────────────────────────────

    def eventFilter(self, obj, event) -> bool:
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize and self.isVisible():
            self.setGeometry(obj.rect())
        return False

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        w = min(self.W, self.width() - 96)
        h = min(self.H, self.height() - 80)
        self.card.setGeometry(QRect((self.width() - w) // 2, (self.height() - h) // 2, w, h))

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 120 if T.is_dark() else 70))
        p.end()

    def mousePressEvent(self, event) -> None:
        if not self.card.geometry().contains(event.position().toPoint()):
            self.close_dialog()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close_dialog()
            return
        super().keyPressEvent(event)
