"""Agent settings - the safety rails, made visible and editable.

Four things the user needs control over before they will trust a
workforce with their filesystem:

    Workspace          where agents may build (never Tora's own folder)
    Auto-approve       the highest risk level that runs without a click
    Plan approval      show the plan and wait, before anything runs
    Max concurrent     how many agents may work at once

Settings persist through `config.py` (the same `.env` upsert the rest of
the app uses) and are applied to the live `AgentManager` immediately, so
nothing needs a restart to take effect.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from agents.core import RiskLevel
from ui import design

log = logging.getLogger("agents.ui.settings")

RISK_CHOICES = [
    ("safe", "Safe — read-only tools run; every write asks"),
    ("low", "Low — writes inside the workspace run"),
    ("medium", "Medium — also runs commands and installs (recommended)"),
    ("high", "High — everything, including deletes and pushes"),
]

SETTINGS_QSS = design.PANEL_QSS + """
QLabel#setLabel { font-size: 12px; font-weight: 600; color: rgb(235, 235, 245); }
QLabel#setHint  { font-size: 11px; color: rgb(140, 140, 160); }
QLineEdit, QComboBox, QSpinBox {
    background-color: rgba(40, 40, 50, 200);
    border: 1px solid rgba(90, 90, 110, 150);
    border-radius: 8px; color: rgb(235, 235, 245);
    font-size: 12px; padding: 6px 8px;
}
QComboBox QAbstractItemView {
    background-color: rgb(28, 28, 34); color: rgb(235, 235, 245);
    selection-background-color: rgba(0, 120, 255, 120);
}
QCheckBox { font-size: 12px; color: rgb(228, 228, 238); }
QPushButton { font-size: 12px; padding: 6px 16px; border-radius: 8px;
              background-color: rgba(60, 60, 75, 110);
              border: 1px solid rgba(90, 90, 110, 150); color: rgb(230, 230, 240); }
QPushButton:hover { background-color: rgba(0, 120, 255, 70); }
QPushButton#setSave { background-color: rgba(0, 120, 255, 120);
                      border-color: rgba(0, 120, 255, 200); font-weight: 600; }
"""


class AgentSettingsDialog(QWidget):
    """Edit the workforce's rails. Save writes to config and to the
    running manager; nothing here needs an app restart."""

    saved = pyqtSignal()

    def __init__(self, manager=None, config=None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.setStyleSheet(SETTINGS_QSS)
        self.setWindowTitle("Agent settings")
        self.resize(560, 430)

        self._manager = manager
        if config is None:
            from config import cfg as config
        self._cfg = config

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(6)

        title = QLabel("Agent settings")
        title.setObjectName("title")
        root.addWidget(title)

        # workspace
        root.addWidget(self._label("Workspace"))
        row = QHBoxLayout()
        row.setSpacing(6)
        self.workspace = QLineEdit(self._current_workspace())
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.workspace, 1)
        row.addWidget(browse)
        root.addLayout(row)
        root.addWidget(self._hint("Where agents create projects. Never Tora's own folder."))

        # auto-approve
        root.addWidget(self._label("Run without asking, up to"))
        self.risk = QComboBox()
        for value, text in RISK_CHOICES:
            self.risk.addItem(text, value)
        self.risk.setCurrentIndex(self._risk_index(getattr(self._cfg, "agent_auto_risk", "medium")))
        root.addWidget(self.risk)
        root.addWidget(self._hint(
            "Anything above this level pauses the agent and asks you first."))

        # plan approval
        self.plan_approval = QCheckBox("Show me the plan before anything runs")
        self.plan_approval.setChecked(bool(getattr(self._cfg, "agent_plan_approval", False)))
        root.addWidget(self.plan_approval)

        # concurrency
        root.addWidget(self._label("Maximum agents at once"))
        self.max_concurrent = QSpinBox()
        self.max_concurrent.setRange(0, 20)
        self.max_concurrent.setValue(int(getattr(self._cfg, "agent_max_concurrent", 3)))
        self.max_concurrent.setSpecialValueText("Unlimited")
        root.addWidget(self.max_concurrent)
        root.addWidget(self._hint("Extra tasks queue and start as slots free up. 0 = unlimited."))

        root.addStretch(1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        accounts = QPushButton("Connected accounts…")
        accounts.clicked.connect(self._open_accounts)
        buttons.addWidget(accounts)
        clear = QPushButton("Clear agent history")
        clear.clicked.connect(self._clear_history)
        buttons.addWidget(clear)
        buttons.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        save = QPushButton("Save")
        save.setObjectName("setSave")
        save.clicked.connect(self.save)
        buttons.addWidget(close)
        buttons.addWidget(save)
        root.addLayout(buttons)

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _label(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("setLabel")
        return lab

    @staticmethod
    def _hint(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("setHint")
        lab.setWordWrap(True)
        return lab

    @staticmethod
    def _risk_index(value: str) -> int:
        names = [v for v, _ in RISK_CHOICES]
        try:
            return names.index(str(value).lower())
        except ValueError:
            return names.index("medium")

    def _current_workspace(self) -> str:
        configured = (getattr(self._cfg, "agent_workspace", "") or "").strip()
        if configured:
            return configured
        if self._manager is not None:
            return self._manager.workspace_root
        from agents.bootstrap import default_workspace_root
        return default_workspace_root()

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Choose the agent workspace",
                                                  self.workspace.text())
        if chosen:
            self.workspace.setText(os.path.normpath(chosen))

    # -- actions -------------------------------------------------------------

    def save(self) -> None:
        workspace = self.workspace.text().strip()
        risk = self.risk.currentData()
        plan = self.plan_approval.isChecked()
        limit = self.max_concurrent.value()

        self._cfg.set_agent_workspace(workspace)
        self._cfg.set_agent_auto_risk(risk)
        self._cfg.set_agent_plan_approval(plan)
        self._cfg.set_agent_max_concurrent(limit)

        self.apply_to(self._manager, workspace=workspace, risk=risk, plan=plan, limit=limit)
        self.saved.emit()

    @staticmethod
    def apply_to(manager, *, workspace: str, risk: str, plan: bool, limit: int) -> None:
        """Push the saved values onto a live manager.

        Only the *default* policy is changed: agents already running keep
        the rails they were created with, which is the honest behaviour -
        tightening the ceiling should not retroactively unapprove work the
        user already allowed.
        """
        if manager is None:
            return
        try:
            from agents.bootstrap import RISK_BY_NAME
            if workspace:
                path = os.path.abspath(os.path.expanduser(workspace))
                os.makedirs(path, exist_ok=True)
                manager.workspace_root = path
            manager.default_policy.max_auto_risk = RISK_BY_NAME.get(risk, RiskLevel.MEDIUM)
            manager.orchestrator.require_plan_approval = bool(plan)
            manager.max_concurrent = max(0, int(limit))
        except Exception:
            log.exception("applying agent settings to the live manager failed")

    def _open_accounts(self) -> None:
        from . import open_accounts
        self._accounts_window = open_accounts(self._manager, parent=None)

    def _clear_history(self) -> None:
        if self._manager is None or getattr(self._manager, "store", None) is None:
            return
        answer = QMessageBox.question(
            self, "Clear agent history",
            "Delete the record of every finished task?\n\n"
            "Files the agents created are not touched — only the history.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer is not QMessageBox.StandardButton.Yes:
            return
        self.clear_history(self._manager)

    @staticmethod
    def clear_history(manager) -> int:
        """Delete finished tasks from the store. Running ones are kept -
        deleting a task mid-flight would orphan its events."""
        store = getattr(manager, "store", None)
        if store is None:
            return 0
        removed = 0
        for task in store.list_tasks(limit=10_000):
            if task.status.is_terminal:
                store.delete_task(task.id)
                manager._tasks.pop(task.id, None)
                removed += 1
        manager.events.clear_history()
        return removed
