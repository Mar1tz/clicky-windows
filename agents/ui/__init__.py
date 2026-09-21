"""Qt surface for the agent workforce.

The ONLY package under `agents/` that imports PyQt6. Everything here runs
on the GUI thread and reaches the runtime through `AgentEventBridge`
(inbound) and `AgentManager`'s thread-safe methods (outbound).

    from agents.ui import open_agents_window
    window = open_agents_window(companion.agents)
"""
from __future__ import annotations

from typing import Optional

from .agents_panel import ActiveAgentsPanel, AgentCard, StatusDot
from .bridge import AgentEventBridge
from .timeline import ActivityTimeline, TimelineRow

__all__ = ["ActiveAgentsPanel", "ActivityTimeline", "AgentCard", "AgentEventBridge",
           "StatusDot", "TimelineRow", "open_agents_window", "open_timeline"]

_window: Optional[ActiveAgentsPanel] = None
_timelines: dict[str, ActivityTimeline] = {}


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
