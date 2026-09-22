"""Approval dialog - the human in the loop.

Two things get approved, and they look different:

  * a PLAN, before any of it runs (require_plan_approval) - so the dialog
    shows the steps, not arguments;
  * a single risky TOOL CALL mid-run (delete_path, git_push, anything
    above the agent's ceiling) - so the dialog shows the tool, its
    arguments, its risk and the workspace it would touch.

`ApprovalController` owns the queue. Requests arrive from the runtime
thread via `AgentEventBridge.approval_required`, are shown one at a time
(two modal dialogs at once would be a mess, and a paused agent is not in
a hurry), and resolve through `AgentManager.approve(request_id, bool)`.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from agents.core import ApprovalRequest, RiskLevel
from ui import design

log = logging.getLogger("agents.ui.approval")

PLAN_CALL = "approve_plan"

_RISK_COLOUR = {
    RiskLevel.SAFE: design.SUCCESS,
    RiskLevel.LOW: design.SUCCESS,
    RiskLevel.MEDIUM: design.WARNING,
    RiskLevel.HIGH: design.ERROR,
}

APPROVAL_QSS = design.PANEL_QSS + """
QDialog { background-color: rgb(18, 18, 22); }
QLabel#apTool { font-size: 15px; font-weight: 600; color: rgb(240, 240, 245); }
QLabel#apReason { font-size: 12px; color: rgb(200, 200, 215); }
QLabel#apArg { font-size: 12px; color: rgb(215, 215, 228); }
QLabel#apMeta { font-size: 11px; color: rgb(140, 140, 160); }
QLabel#apRisk { font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 8px; }
QPushButton { font-size: 12px; padding: 6px 16px; border-radius: 8px;
              background-color: rgba(60, 60, 75, 110);
              border: 1px solid rgba(90, 90, 110, 150); color: rgb(230, 230, 240); }
QPushButton:hover { background-color: rgba(0, 120, 255, 70); }
QPushButton#apApprove { background-color: rgba(0, 120, 255, 120);
                        border-color: rgba(0, 120, 255, 200); font-weight: 600; }
QPushButton#apDeny { background-color: rgba(255, 70, 70, 60);
                     border-color: rgba(255, 70, 70, 140); }
QPushButton:disabled { color: rgb(120, 120, 135); background-color: rgba(60, 60, 75, 60); }
QLineEdit#apConfirm { font-size: 13px; padding: 6px 8px; border-radius: 6px; color: rgb(240, 240, 245);
                      background-color: rgba(30, 30, 38, 220); border: 1px solid rgba(255, 70, 70, 160); }
QLabel#apSensitive { font-size: 12px; color: rgb(255, 170, 160); }
"""


def _always_asks(manager, tool_name: str) -> bool:
    try:
        return bool(getattr(manager.tools.get(tool_name), "always_ask", False))
    except Exception:
        return False


def _risk_of(manager, tool_name: str) -> RiskLevel:
    try:
        return manager.tools.get(tool_name).spec.risk
    except Exception:
        return RiskLevel.HIGH          # unknown tool: assume the worst


def format_arguments(args: dict, *, max_value: int = 300) -> str:
    """Arguments as the user needs to judge them: every key, values long
    enough to be meaningful, truncated before they become a wall."""
    if not args:
        return "(no arguments)"
    lines = []
    for k, v in args.items():
        text = str(v)
        if len(text) > max_value:
            text = f"{text[:max_value]}… ({len(str(v))} chars)"
        lines.append(f"{k}: {text}")
    return "\n".join(lines)


class ApprovalDialog(QDialog):
    """One request. Returns via `decision` / `always` after exec()."""

    def __init__(self, manager, request: ApprovalRequest, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setStyleSheet(APPROVAL_QSS)
        self.setModal(True)
        self.request = request
        self.decision: Optional[bool] = None
        self.always = False
        self.confirmation = ""
        self._sensitive = bool(getattr(request, "sensitive", False))

        self._manager = manager
        is_plan = request.call.tool_name == PLAN_CALL
        task = manager.get_task(request.task_id)
        agent = manager.get_agent(request.agent_id)
        self.setWindowTitle("Approve plan" if is_plan else "Approve action")
        self.resize(520, 430 if is_plan else 340)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        name = QLabel("Approve this plan?" if is_plan else request.call.tool_name)
        name.setObjectName("apTool")
        header.addWidget(name)
        header.addStretch(1)
        if not is_plan:
            risk = _risk_of(manager, request.call.tool_name)
            badge = QLabel("SENSITIVE" if self._sensitive else risk.value.upper())
            badge.setObjectName("apRisk")
            c = _RISK_COLOUR.get(risk, design.WARNING)
            badge.setStyleSheet(
                f"color: rgb({c.red()}, {c.green()}, {c.blue()});"
                f"background-color: rgba({c.red()}, {c.green()}, {c.blue()}, 40);")
            header.addWidget(badge)
        root.addLayout(header)

        who = QLabel(f"{agent.spec.name if agent else 'Agent'} · {task.title if task else ''}")
        who.setObjectName("apMeta")
        who.setWordWrap(True)
        root.addWidget(who)

        reason = QLabel(request.reason)
        reason.setObjectName("apReason")
        reason.setWordWrap(True)
        root.addWidget(reason)

        if is_plan:
            from .plan_view import PlanView
            self._plan = PlanView(manager, request.task_id, embedded=True)
            root.addWidget(self._plan, 1)
        else:
            args = QLabel(format_arguments(request.call.arguments))
            args.setObjectName("apArg")
            args.setWordWrap(True)
            args.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            root.addWidget(args, 1)

        if task:
            where = QLabel(f"Workspace: {os.path.normpath(task.workspace)}")
            where.setObjectName("apMeta")
            where.setWordWrap(True)
            root.addWidget(where)

        self._confirm: Optional[QLineEdit] = None
        if self._sensitive:
            # Money leaves or a filing goes out: no grant, no voice "yes",
            # no default button - the person types the word.
            word = request.confirm_word or "CONFIRM"
            warn = QLabel(f"This cannot be undone from here. Type {word} to approve.")
            warn.setObjectName("apSensitive")
            warn.setWordWrap(True)
            root.addWidget(warn)
            self._confirm = QLineEdit()
            self._confirm.setObjectName("apConfirm")
            self._confirm.setPlaceholderText(word)
            root.addWidget(self._confirm)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self._always_btn: Optional[QPushButton] = None
        self._task_scoped = _always_asks(manager, request.call.tool_name)
        if not is_plan and not self._sensitive:
            # An always_ask tool (mouse, keyboard) cannot be allowed forever
            # by any setting - that is the floor - but a person can allow
            # it for the rest of THIS job. Offering "Always allow" there was
            # a button that silently did nothing, which is worse than none.
            label = (f"Allow {request.call.tool_name} for this task" if self._task_scoped
                     else f"Always allow {request.call.tool_name}")
            self._always_btn = QPushButton(label)
            self._always_btn.clicked.connect(self._on_always)
            buttons.addWidget(self._always_btn)
        buttons.addStretch(1)
        self._deny_btn = QPushButton("Deny")
        self._deny_btn.setObjectName("apDeny")
        self._approve_btn = QPushButton("Approve")
        self._approve_btn.setObjectName("apApprove")
        self._approve_btn.setDefault(not self._sensitive)
        if self._confirm is not None:
            word = (request.confirm_word or "CONFIRM").upper()
            self._approve_btn.setEnabled(False)
            self._confirm.textChanged.connect(
                lambda t: self._approve_btn.setEnabled(t.strip().upper() == word))
            self._deny_btn.setDefault(True)
        self._deny_btn.clicked.connect(lambda: self._finish(False))
        self._approve_btn.clicked.connect(lambda: self._finish(True))
        buttons.addWidget(self._deny_btn)
        buttons.addWidget(self._approve_btn)
        root.addLayout(buttons)

    def _on_always(self) -> None:
        """Record the standing approval on the agent's policy, then approve.

        This mutates the live AgentSpec, so the next call to the same tool
        by the same agent never reaches a dialog. It cannot override a
        denied tool - see PermissionPolicy.allows.
        """
        if self._task_scoped:
            self._manager.grant_for_task(self.request.task_id, self.request.call.tool_name)
        else:
            agent = self._manager.get_agent(self.request.agent_id)
            if agent is not None:
                agent.spec.permissions.allow_always(self.request.call.tool_name)
        self.always = True
        self._finish(True)

    def _finish(self, approved: bool) -> None:
        if self._confirm is not None:
            self.confirmation = self._confirm.text().strip()
        self.decision = approved
        self.accept() if approved else self.reject()

    # -- for tests -----------------------------------------------------------

    def click_approve(self) -> None:
        self._approve_btn.click()

    def click_deny(self) -> None:
        self._deny_btn.click()

    def click_always(self) -> None:
        if self._always_btn is None:
            raise RuntimeError("plan approvals and sensitive actions have no 'always allow' button")
        self._always_btn.click()

    def type_confirmation(self, text: str) -> None:
        if self._confirm is None:
            raise RuntimeError("only sensitive actions ask for a typed confirmation")
        self._confirm.setText(text)


class ApprovalController(QObject):
    """Shows one dialog at a time for incoming approval requests."""

    resolved = pyqtSignal(str, bool)      # (request_id, approved)

    def __init__(self, manager, bridge=None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._manager = manager
        self._parent_widget = parent
        self._queue: list[ApprovalRequest] = []
        self._current: Optional[ApprovalDialog] = None

        if bridge is not None:
            self._bridge = bridge
            self._owns_bridge = False
        else:
            from .bridge import AgentEventBridge
            self._bridge = AgentEventBridge(manager, self)
            self._owns_bridge = True
        self._bridge.approval_required.connect(self.enqueue)

    def enqueue(self, request: ApprovalRequest) -> None:
        self._queue.append(request)
        self._show_next()

    def _show_next(self) -> None:
        if self._current is not None or not self._queue:
            return
        request = self._queue.pop(0)
        dialog = ApprovalDialog(self._manager, request, self._parent_widget)
        self._current = dialog
        try:
            dialog.exec()
            approved = bool(dialog.decision)
            self._manager.approve(request.id, approved, confirmation=dialog.confirmation)
            self.resolved.emit(request.id, approved)
        except Exception:
            log.exception("approval dialog failed; denying %s", request.id)
            self._manager.approve(request.id, False)
        finally:
            self._current = None
        self._show_next()

    # -- for tests -----------------------------------------------------------

    @property
    def pending(self) -> int:
        return len(self._queue)

    def close(self) -> None:
        if self._owns_bridge:
            self._bridge.close()
