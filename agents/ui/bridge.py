"""The one place PyQt6 and the agent runtime are allowed to touch.

Agent events are published from the runtime thread (and from tool threads
under it). Qt widgets may only be touched from the GUI thread. A
`pyqtSignal` emitted from a foreign thread is delivered as a queued
connection, which is exactly the hand-off we need - so the bridge
subscribes to the `EventBus` and re-emits, and every widget downstream can
then be written as ordinary single-threaded Qt code.

This mirrors what `CompanionManager` already does with its `sig_*` signals.
Nothing else under `agents/` imports PyQt6.
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

from agents.core import Event, EventKind

log = logging.getLogger("agents.ui.bridge")


class AgentEventBridge(QObject):
    """Re-emits `EventBus` traffic on the Qt thread.

    `event` carries everything; the rest are conveniences so a widget can
    connect to just the thing it renders.
    """

    event = pyqtSignal(object)               # agents.core.Event (all of them)
    agent_changed = pyqtSignal(object)       # agents.core.AgentState
    task_status = pyqtSignal(str, str)       # (task_id, TaskStatus value)
    task_progress = pyqtSignal(str, float, str)   # (task_id, 0..1, current action)
    approval_required = pyqtSignal(object)   # agents.core.ApprovalRequest
    log_line = pyqtSignal(object)            # Event, for the timeline (M9)

    def __init__(self, manager, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._manager = manager
        self._unsub = manager.events.subscribe(self._on_event)

    @property
    def manager(self):
        return self._manager

    def close(self) -> None:
        """Stop listening. Safe to call twice."""
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    # -- runtime thread ------------------------------------------------------

    def _on_event(self, ev: Event) -> None:
        """Called on the agent runtime's thread. Emit and return fast:
        anything slow here would stall the agent that published it."""
        try:
            self.event.emit(ev)
            if ev.kind is EventKind.AGENT_STATUS:
                state = self._state_from(ev)
                if state is not None:
                    self.agent_changed.emit(state)
            elif ev.kind is EventKind.TASK_STATUS:
                self.task_status.emit(ev.task_id or "", str(ev.data.get("status", "")))
            elif ev.kind is EventKind.TASK_PROGRESS:
                self.task_progress.emit(ev.task_id or "",
                                        float(ev.data.get("progress", 0.0)),
                                        str(ev.data.get("current_action", ev.message)))
            elif ev.kind is EventKind.APPROVAL_REQUIRED:
                req = self._approval_from(ev)
                if req is not None:
                    self.approval_required.emit(req)
            self.log_line.emit(ev)
        except RuntimeError:
            # The C++ object went away while an agent was still running -
            # the window was closed. Not an error; stop listening.
            self.close()
        except Exception:
            log.exception("bridging %s failed", ev.kind.value)

    @staticmethod
    def _state_from(ev: Event):
        from agents.core import AgentState
        data = ev.data.get("state")
        return AgentState.from_dict(data) if isinstance(data, dict) else None

    @staticmethod
    def _approval_from(ev: Event):
        from agents.core import ApprovalRequest
        data = ev.data.get("request")
        return ApprovalRequest.from_dict(data) if isinstance(data, dict) else None
