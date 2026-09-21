"""Active Agents - the window that makes the workforce visible.

    🟢 Application Builder
       Creating React application
       Step 4/10 · Creating Navbar.tsx
       ███████░░░  70%
       [Pause]  [Logs]  [Stop]

One card per agent, driven entirely by `AgentEventBridge` signals, so the
panel never reaches into the runtime except through `AgentManager`'s
thread-safe control methods. Colours and fonts come from `ui.design`;
this file defines no palette of its own.
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from agents.core import AgentState, AgentStatus, TaskStatus
from ui import design

log = logging.getLogger("agents.ui.panel")

# Status dot colours, by agent status.
_DOT = {
    AgentStatus.RUNNING: design.STATE_LISTENING,   # green - it is working
    AgentStatus.PAUSED: design.WARNING,
    AgentStatus.IDLE: design.TEXT_MUTED,
    AgentStatus.STOPPED: design.TEXT_MUTED,
    AgentStatus.ERROR: design.ERROR,
}

_VERB = {
    AgentStatus.RUNNING: "Running",
    AgentStatus.PAUSED: "Paused",
    AgentStatus.IDLE: "Done",
    AgentStatus.STOPPED: "Stopped",
    AgentStatus.ERROR: "Failed",
}

PANEL_QSS = design.PANEL_QSS + """
QFrame#agentCard {
    background-color: rgba(28, 28, 34, 220);
    border: 1px solid rgba(60, 60, 75, 160);
    border-radius: 12px;
}
QLabel#agentName { font-size: 14px; font-weight: 600; color: rgb(240, 240, 245); }
QLabel#agentTask { font-size: 12px; color: rgb(190, 190, 205); }
QLabel#agentStep { font-size: 11px; color: rgb(140, 140, 160); }
QLabel#agentAction { font-size: 12px; color: rgb(220, 220, 230); }
QLabel#emptyState { font-size: 13px; color: rgb(140, 140, 160); }
QProgressBar {
    background-color: rgba(60, 60, 75, 120);
    border: none;
    border-radius: 4px;
    height: 8px;
    text-align: center;
}
QProgressBar::chunk { background-color: rgb(0, 120, 255); border-radius: 4px; }
QPushButton#cardBtn {
    background-color: rgba(60, 60, 75, 90);
    border: 1px solid rgba(90, 90, 110, 140);
    border-radius: 8px;
    color: rgb(225, 225, 235);
    font-size: 12px;
    padding: 5px 14px;
}
QPushButton#cardBtn:hover { background-color: rgba(0, 120, 255, 70); }
QPushButton#cardBtn:disabled { color: rgb(90, 90, 110); border-color: rgba(70, 70, 85, 120); }
/* The scroll viewport and the widget inside it both default to the window
   colour, which shows through as a white band between cards. */
QScrollArea > QWidget > QWidget { background: transparent; }
QWidget#cardHolder { background: transparent; }
"""


def _tokens(n: int) -> str:
    """1234 -> "1.2k tokens", 1500000 -> "1.5M tokens"."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M tokens"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k tokens"
    return f"{n} tokens"


class StatusDot(QWidget):
    """The 🟢 - a filled circle whose colour tracks the agent's status."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedSize(12, 12)
        self._color: QColor = design.TEXT_MUTED

    def set_status(self, status: AgentStatus) -> None:
        self._color = _DOT.get(status, design.TEXT_MUTED)
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._color)
        p.drawEllipse(1, 1, 10, 10)
        p.end()


class AgentCard(QFrame):
    """One agent. Rebuilt in place from each AgentState it is handed."""

    pause_clicked = pyqtSignal(str)   # task_id
    resume_clicked = pyqtSignal(str)
    stop_clicked = pyqtSignal(str)
    logs_clicked = pyqtSignal(str)

    def __init__(self, state: AgentState, parent: Optional[QWidget] = None,
                 *, child_of: Optional[str] = None):
        super().__init__(parent)
        self.setObjectName("agentCard")
        self.agent_id = state.id
        self._task_id: Optional[str] = state.current_task_id
        self.child_of = child_of              # parent task id for a project member

        root = QVBoxLayout(self)
        # A project's agents sit under their Project Lead, indented, so the
        # panel reads as one job with three hands rather than four jobs.
        root.setContentsMargins(14 + (18 if child_of else 0), 12, 14, 12)
        root.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(8)
        self._dot = StatusDot()
        self._name = QLabel(state.spec.name)
        self._name.setObjectName("agentName")
        self._verb = QLabel("")
        self._verb.setObjectName("agentStep")
        header.addWidget(self._dot)
        header.addWidget(self._name)
        header.addStretch(1)
        header.addWidget(self._verb)
        root.addLayout(header)

        self._task = QLabel("")
        self._task.setObjectName("agentTask")
        self._task.setWordWrap(True)
        root.addWidget(self._task)

        self._step = QLabel("")
        self._step.setObjectName("agentStep")
        root.addWidget(self._step)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setTextVisible(False)
        root.addWidget(self._bar)

        self._action = QLabel("")
        self._action.setObjectName("agentAction")
        self._action.setWordWrap(True)
        root.addWidget(self._action)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self._pause = QPushButton("Pause")
        self._logs = QPushButton("Logs")
        self._stop = QPushButton("Stop")
        for b in (self._pause, self._logs, self._stop):
            b.setObjectName("cardBtn")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            buttons.addWidget(b)
        buttons.addStretch(1)
        root.addLayout(buttons)

        self._pause.clicked.connect(self._on_pause)
        self._stop.clicked.connect(lambda: self._task_id and self.stop_clicked.emit(self._task_id))
        self._logs.clicked.connect(lambda: self._task_id and self.logs_clicked.emit(self._task_id))

        self.update_state(state)

    # -- rendering -----------------------------------------------------------

    def set_child_of(self, parent_task: Optional[str]) -> None:
        self.child_of = parent_task
        self.layout().setContentsMargins(14 + (18 if parent_task else 0), 12, 14, 12)

    def update_state(self, state: AgentState) -> None:
        self._task_id = state.current_task_id
        self._dot.set_status(state.status)
        self._name.setText(state.spec.name)
        self._verb.setText(_VERB.get(state.status, ""))
        self._task.setText(state.current_task_title or state.spec.goal or "")

        bits = []
        if state.step_total:
            bits.append(f"Step {min(state.step_index, state.step_total)}/{state.step_total}")
        if state.tokens_used:
            bits.append(_tokens(state.tokens_used))
        self._step.setText(" · ".join(bits))
        self._step.setVisible(bool(bits))

        self._bar.setValue(int(round(max(0.0, min(1.0, state.progress)) * 100)))
        self._action.setText(state.last_error if state.status is AgentStatus.ERROR
                             else state.current_action)

        live = state.status in (AgentStatus.RUNNING, AgentStatus.PAUSED) and bool(self._task_id)
        self._pause.setText("Resume" if state.status is AgentStatus.PAUSED else "Pause")
        self._pause.setEnabled(live)
        self._stop.setEnabled(live)
        self._logs.setEnabled(bool(self._task_id))
        self._paused = state.status is AgentStatus.PAUSED

    def set_progress(self, fraction: float, action: str) -> None:
        """Cheap path for TASK_PROGRESS, which arrives far more often than
        a full AgentState."""
        self._bar.setValue(int(round(max(0.0, min(1.0, fraction)) * 100)))
        if action:
            self._action.setText(action)

    def _on_pause(self) -> None:
        if not self._task_id:
            return
        (self.resume_clicked if self._paused else self.pause_clicked).emit(self._task_id)

    # -- for tests -----------------------------------------------------------

    @property
    def summary(self) -> str:
        return " · ".join(t for t in (self._name.text(), self._verb.text(),
                                      self._step.text(), self._action.text()) if t)

    @property
    def progress_percent(self) -> int:
        return self._bar.value()


class ActiveAgentsPanel(QWidget):
    """The scrollable list of agent cards, plus the empty state."""

    view_logs = pyqtSignal(str)       # task_id - M9 connects the timeline here

    EMPTY_TEXT = "No agents running.\nAsk Tora to build something."

    def __init__(self, manager, bridge=None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.setWindowTitle("Active Agents")
        self.setStyleSheet(PANEL_QSS)
        self.resize(design.PANEL_WIDTH + 80, design.PANEL_HEIGHT)

        self._manager = manager
        self._cards: dict[str, AgentCard] = {}

        from .bridge import AgentEventBridge
        self._bridge = bridge or AgentEventBridge(manager, self)
        self._owns_bridge = bridge is None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        title = QLabel("Active Agents")
        title.setObjectName("title")
        outer.addWidget(title)

        self._empty = QLabel(self.EMPTY_TEXT)
        self._empty.setObjectName("emptyState")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        outer.addWidget(self._empty, 1)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        holder.setObjectName("cardHolder")
        self._scroll.viewport().setAutoFillBackground(False)
        self._list = QVBoxLayout(holder)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(10)
        self._list.addStretch(1)
        self._scroll.setWidget(holder)
        self._scroll.setVisible(False)
        outer.addWidget(self._scroll, 1)

        self._bridge.agent_changed.connect(self.on_agent_changed)
        self._bridge.task_progress.connect(self.on_task_progress)

        for state in manager.list_agents():
            self.on_agent_changed(state)

    # -- signal handlers (GUI thread) ----------------------------------------

    def on_agent_changed(self, state: AgentState) -> None:
        card = self._cards.get(state.id)
        if card is None:
            parent_task = self._parent_task_of(state)
            card = AgentCard(state, child_of=parent_task)
            card.pause_clicked.connect(self._manager.pause)
            card.resume_clicked.connect(self._manager.resume)
            card.stop_clicked.connect(self._manager.cancel)
            card.logs_clicked.connect(self.view_logs)
            self._cards[state.id] = card
            self._list.insertWidget(self._slot_for(parent_task), card)
        else:
            card.update_state(state)
            # The first AGENT_STATUS for an agent fires from create_agent(),
            # before its task is linked - so a project member's card is born
            # without a parent. Re-home it the moment the link appears.
            if card.child_of is None:
                parent_task = self._parent_task_of(state)
                if parent_task:
                    self._rehome(card, parent_task)
        self._refresh_empty_state()

    def _rehome(self, card: "AgentCard", parent_task: str) -> None:
        card.set_child_of(parent_task)
        self._list.removeWidget(card)
        self._list.insertWidget(self._slot_for(parent_task), card)

    def _parent_task_of(self, state: AgentState) -> Optional[str]:
        """The project this agent belongs to, if any."""
        try:
            task = self._manager.get_task(state.current_task_id or "")
            return (task.metadata or {}).get("parent_id") if task else None
        except Exception:  # noqa: BLE001
            return None

    def _slot_for(self, parent_task: Optional[str]) -> int:
        """Where a new card goes: after the last card of its project, or
        at the end."""
        end = self._list.count() - 1                      # before the stretch
        if not parent_task:
            return end
        slot = end
        for i in range(self._list.count() - 1):
            w = self._list.itemAt(i).widget()
            if isinstance(w, AgentCard) and (w._task_id == parent_task or w.child_of == parent_task):
                slot = i + 1
        return slot

    def on_task_progress(self, task_id: str, fraction: float, action: str) -> None:
        for card in self._cards.values():
            if card._task_id == task_id:
                card.set_progress(fraction, action)
                return

    # -- helpers -------------------------------------------------------------

    def _refresh_empty_state(self) -> None:
        has_cards = bool(self._cards)
        self._empty.setVisible(not has_cards)
        self._scroll.setVisible(has_cards)

    @property
    def cards(self) -> dict[str, AgentCard]:
        return dict(self._cards)

    def card_for_agent(self, agent_id: str) -> Optional[AgentCard]:
        return self._cards.get(agent_id)

    def closeEvent(self, e) -> None:
        if self._owns_bridge:
            self._bridge.close()
        super().closeEvent(e)
