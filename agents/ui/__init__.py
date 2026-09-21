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

__all__ = ["ActiveAgentsPanel", "AgentCard", "AgentEventBridge", "StatusDot",
           "open_agents_window"]

_window: Optional[ActiveAgentsPanel] = None


def open_agents_window(manager, parent=None) -> ActiveAgentsPanel:
    """Show the Active Agents panel, reusing the existing one if it is
    already open (the tray menu can be clicked repeatedly)."""
    global _window
    if _window is None or not _window.isVisible():
        if _window is not None:
            _window.close()
        _window = ActiveAgentsPanel(manager, parent=parent)
    _window.show()
    _window.raise_()
    _window.activateWindow()
    return _window
