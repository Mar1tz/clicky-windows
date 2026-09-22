"""Extensions dialog and the dashboard window - generic, pack-agnostic.

ExtensionsDialog lists every discovered pack with what it adds, and
turns it on or off (install, enable, disable are the registry's; this is
just the switch). ExtensionDashboard is a tab per dashboard module that
the ENABLED packs registered - the core draws the frame, each pack draws
its own tab, and a pack that is switched off simply has no tab.
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget,
)

from ui import design

log = logging.getLogger("agents.ui.extensions")

EXT_QSS = design.PANEL_QSS + """
QDialog, QWidget#extRoot { background-color: rgb(18, 18, 22); }
QFrame#extCard { background-color: rgba(28, 28, 34, 230); border: 1px solid rgba(60, 60, 75, 180);
                 border-radius: 12px; }
QLabel#extName { font-size: 15px; font-weight: 600; color: rgb(240, 240, 245); }
QLabel#extDesc { font-size: 12px; color: rgb(200, 200, 215); }
QLabel#extMeta { font-size: 11px; color: rgb(140, 140, 160); }
QLabel#extOn { font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 8px;
               color: rgb(50, 200, 100); background-color: rgba(50, 200, 100, 35); }
QLabel#extOff { font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 8px;
                color: rgb(140, 140, 160); background-color: rgba(140, 140, 160, 30); }
QPushButton { font-size: 12px; padding: 6px 14px; border-radius: 8px; color: rgb(230, 230, 240);
              background-color: rgba(60, 60, 75, 110); border: 1px solid rgba(90, 90, 110, 150); }
QPushButton:hover { background-color: rgba(0, 120, 255, 70); }
QPushButton#extPrimary { background-color: rgba(0, 120, 255, 120); border-color: rgba(0, 120, 255, 200);
                         font-weight: 600; }
QTabWidget::pane { border: 1px solid rgba(60, 60, 75, 180); border-radius: 8px; top: -1px; }
QTabBar::tab { background: rgba(28, 28, 34, 230); color: rgb(200, 200, 215); padding: 7px 16px;
               border: 1px solid rgba(60, 60, 75, 180); border-bottom: none; border-top-left-radius: 8px;
               border-top-right-radius: 8px; margin-right: 2px; }
QTabBar::tab:selected { background: rgba(0, 120, 255, 70); color: rgb(240, 240, 245); }
"""


class ExtensionCard(QFrame):
    toggled = pyqtSignal(str, bool)
    open_requested = pyqtSignal(str)

    def __init__(self, info: dict, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("extCard")
        self.ext_id = info["id"]
        self.enabled = bool(info.get("enabled"))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)
        top = QHBoxLayout()
        name = QLabel(f"{info['name']}  ·  v{info.get('version', '1.0')}")
        name.setObjectName("extName")
        top.addWidget(name)
        top.addStretch(1)
        self.badge = QLabel("ON" if self.enabled else "OFF")
        self.badge.setObjectName("extOn" if self.enabled else "extOff")
        top.addWidget(self.badge)
        lay.addLayout(top)
        if info.get("description"):
            desc = QLabel(info["description"])
            desc.setObjectName("extDesc")
            desc.setWordWrap(True)
            lay.addWidget(desc)
        if info.get("offer"):
            adds = QLabel("Adds: " + " · ".join(info["offer"]))
            adds.setObjectName("extMeta")
            adds.setWordWrap(True)
            lay.addWidget(adds)
        if info.get("optional_integrations"):
            integ = QLabel("Integrations: " + ", ".join(info["optional_integrations"]))
            integ.setObjectName("extMeta")
            integ.setWordWrap(True)
            lay.addWidget(integ)
        row = QHBoxLayout()
        row.addStretch(1)
        self.open_btn = QPushButton("Open")
        self.open_btn.setVisible(self.enabled)
        self.open_btn.clicked.connect(lambda: self.open_requested.emit(self.ext_id))
        row.addWidget(self.open_btn)
        self.toggle_btn = QPushButton("Disable" if self.enabled else "Enable")
        self.toggle_btn.setObjectName("" if self.enabled else "extPrimary")
        self.toggle_btn.clicked.connect(lambda: self.toggled.emit(self.ext_id, not self.enabled))
        row.addWidget(self.toggle_btn)
        lay.addLayout(row)


class ExtensionsDialog(QDialog):
    """Turn industry packs on and off."""
    open_dashboard = pyqtSignal(str)

    def __init__(self, manager, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._manager = manager
        self.setWindowTitle("Extensions")
        self.setStyleSheet(EXT_QSS)
        self.resize(560, 520)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        intro = QLabel("Industry extensions give the agents a trade: its tools, workflows, knowledge and "
                       "permissions. Turning one off removes all of it; your data stays.")
        intro.setObjectName("extMeta")
        intro.setWordWrap(True)
        root.addWidget(intro)
        self._list = QVBoxLayout()
        self._list.setSpacing(10)
        box = QWidget()
        box.setObjectName("extRoot")
        box.setLayout(self._list)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(box)
        root.addWidget(scroll, 1)
        self.cards: dict[str, ExtensionCard] = {}
        self.refresh()

    @property
    def registry(self):
        return getattr(self._manager, "extensions", None)

    def refresh(self) -> None:
        while self._list.count():
            item = self._list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.cards.clear()
        reg = self.registry
        infos = reg.available() if reg is not None else []
        if not infos:
            none = QLabel("No extensions installed.")
            none.setObjectName("extDesc")
            self._list.addWidget(none)
        for info in infos:
            card = ExtensionCard(info)
            card.toggled.connect(self._toggle)
            card.open_requested.connect(self.open_dashboard.emit)
            self.cards[info["id"]] = card
            self._list.addWidget(card)
        self._list.addStretch(1)

    def _toggle(self, ext_id: str, on: bool) -> None:
        reg = self.registry
        if reg is None:
            return
        try:
            reg.enable(ext_id) if on else reg.disable(ext_id)
        except Exception:
            log.exception("could not %s %s", "enable" if on else "disable", ext_id)
        self.refresh()


class ExtensionDashboard(QWidget):
    """One tab per dashboard module of every enabled pack."""

    def __init__(self, manager, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._manager = manager
        self.setObjectName("extRoot")
        self.setWindowTitle("Dashboard")
        self.setStyleSheet(EXT_QSS)
        self.resize(980, 700)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)
        self.refresh()

    def refresh(self) -> None:
        self.tabs.clear()
        reg = getattr(self._manager, "extensions", None)
        mods = reg.dashboard_modules() if reg is not None else []
        for m in mods:
            try:
                widget = m.factory(self._manager, reg.get(m.extension_id), self)
            except Exception as exc:  # noqa: BLE001 - one broken module must not blank the dashboard
                log.exception("dashboard module %s failed", m.id)
                widget = QLabel(f"{m.title} could not load: {exc}")
            widget.setProperty("module_id", m.id)
            self.tabs.addTab(widget, m.title)
        if not mods:
            empty = QLabel("Enable an extension (tray → Extensions…) to see its dashboard here.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.tabs.addTab(empty, "Dashboard")

    def show_module(self, module_id: str) -> bool:
        for i in range(self.tabs.count()):
            if self.tabs.widget(i).property("module_id") == module_id:
                self.tabs.setCurrentIndex(i)
                return True
        return False
