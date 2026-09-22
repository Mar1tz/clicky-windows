"""Qt surface for the agent workforce.

The ONLY package under `agents/` that imports PyQt6. Everything here runs
on the GUI thread and reaches the runtime through `AgentEventBridge`
(inbound) and `AgentManager`'s thread-safe methods (outbound).

    from agents.ui import open_agents_window
    window = open_agents_window(companion.agents)
"""
from __future__ import annotations

from typing import Optional

from .accounts import ConnectedAccountsDialog, EndpointCard
from .agents_panel import ActiveAgentsPanel, AgentCard, StatusDot
from .approval import ApprovalController, ApprovalDialog
from .bridge import AgentEventBridge
from .orbs import AgentOrbs
from .plan_view import PlanStepRow, PlanView
from .timeline import ActivityTimeline, TimelineRow

__all__ = ["ActiveAgentsPanel", "ActivityTimeline", "AgentCard", "AgentEventBridge",
           "ApprovalController", "ApprovalDialog", "ConnectedAccountsDialog",
           "AgentOrbs", "EndpointCard", "PlanStepRow", "PlanView", "StatusDot", "TimelineRow",
           "attach_approvals", "open_accounts", "open_agents_window", "show_orbs",
           "open_plan", "open_timeline"]

_window: Optional[ActiveAgentsPanel] = None
_timelines: dict[str, ActivityTimeline] = {}
_plans: dict[str, PlanView] = {}
_approvals: Optional[ApprovalController] = None


def open_agents_window(manager, parent=None) -> ActiveAgentsPanel:
    """Show the Active Agents panel, reusing the existing one if it is
    already open (the tray menu can be clicked repeatedly)."""
    global _window
    if _window is None or not _window.isVisible():
        if _window is not None:
            _window.close()
        _window = ActiveAgentsPanel(manager, parent=parent)
        _window.view_logs.connect(lambda task_id: open_timeline(manager, task_id))
    _window.show()
    _window.raise_()
    _window.activateWindow()
    return _window


def open_timeline(manager, task_id: str, parent=None) -> ActivityTimeline:
    """Show the activity timeline for one task, one window per task."""
    window = _timelines.get(task_id)
    if window is None or not window.isVisible():
        if window is not None:
            window.close()
        window = ActivityTimeline(manager, task_id, parent=parent)
        _timelines[task_id] = window
    window.show()
    window.raise_()
    window.activateWindow()
    return window


def open_plan(manager, task_id: str, parent=None) -> PlanView:
    """Show the plan for one task, one window per task."""
    window = _plans.get(task_id)
    if window is None or not window.isVisible():
        if window is not None:
            window.close()
        window = PlanView(manager, task_id, parent=parent)
        _plans[task_id] = window
    window.show()
    window.raise_()
    window.activateWindow()
    return window


def attach_approvals(manager, parent=None) -> ApprovalController:
    """Start answering approval requests with a dialog.

    Call once per manager, early: a request raised while nothing is
    listening leaves the task parked in WAITING_APPROVAL until something
    resolves it.
    """
    global _approvals
    if _approvals is None:
        _approvals = ApprovalController(manager, parent=parent)
    return _approvals


_accounts_dialog: Optional[ConnectedAccountsDialog] = None


def open_accounts(manager=None, parent=None) -> ConnectedAccountsDialog:
    """Show the connected-accounts page, reusing the open one."""
    global _accounts_dialog
    if _accounts_dialog is None or not _accounts_dialog.isVisible():
        _accounts_dialog = ConnectedAccountsDialog(manager, parent=parent)
    _accounts_dialog.show()
    _accounts_dialog.raise_()
    _accounts_dialog.activateWindow()
    return _accounts_dialog


_orbs: Optional[AgentOrbs] = None


def show_orbs(manager, parent=None) -> AgentOrbs:
    """The floating top-right strip: one orb per working agent. Created
    once; it shows and hides itself as agents come and go."""
    global _orbs
    if _orbs is None:
        _orbs = AgentOrbs(manager, parent=parent)
        _orbs.clicked.connect(lambda _agent_id: open_agents_window(manager))
    return _orbs
