"""Plan viewer - the steps, and how far down them the agent has got.

    ✓ 1. Create project folder
    ✓ 2. Create MyApp/package.json
    ▸ 3. Create MyApp/index.html      ← running
    ○ 4. Create MyApp/vite.config.js
    – 5. Install dependencies          ← skipped

Spec §9: "The user should be able to view this." The plan is read from
the live Task (the orchestrator replaces `task.steps` wholesale when a
plan is adopted or revised) and refreshed from step events, so a replan
mid-run reshuffles the list exactly as the agent sees it.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from agents.core import EventKind, Step, StepStatus
from ui import design

_GLYPH = {
    StepStatus.PENDING: ("○", design.TEXT_MUTED),
    StepStatus.RUNNING: ("▸", design.BLUE_GLOW),
    StepStatus.DONE: ("✓", design.SUCCESS),
    StepStatus.FAILED: ("✕", design.ERROR),
    StepStatus.SKIPPED: ("–", design.TEXT_MUTED),
}

PLAN_QSS = design.PANEL_QSS + """
QLabel#planStep { font-size: 12px; color: rgb(225, 225, 235); }
QLabel#planStepMuted { font-size: 12px; color: rgb(130, 130, 150); }
QLabel#planGlyph { font-size: 12px; }
QLabel#planNum { font-size: 11px; color: rgb(120, 120, 140); }
QLabel#planEmpty { font-size: 12px; color: rgb(140, 140, 160); }
QLabel#planNote { font-size: 11px; color: rgb(150, 150, 170); }
QScrollArea > QWidget > QWidget { background: transparent; }
QWidget#planHolder { background: transparent; }
"""


class PlanStepRow(QWidget):
    """One numbered step with a status glyph."""

    def __init__(self, index: int, step: Step, parent: Optional[QWidget] = None):
        super().__init__(parent)
        glyph, colour = _GLYPH.get(step.status, _GLYPH[StepStatus.PENDING])
        done = step.status in (StepStatus.DONE, StepStatus.SKIPPED)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 1, 0, 1)
        row.setSpacing(8)

        mark = QLabel(glyph)
        mark.setObjectName("planGlyph")
        mark.setFixedWidth(12)
        mark.setStyleSheet(f"color: rgb({colour.red()}, {colour.green()}, {colour.blue()});")
        num = QLabel(f"{index + 1}.")
        num.setObjectName("planNum")
        num.setFixedWidth(20)
        text = QLabel(step.title)
        text.setObjectName("planStepMuted" if done else "planStep")
        text.setWordWrap(True)

        row.addWidget(mark)
        row.addWidget(num)
        row.addWidget(text, 1)

        self.title = step.title
        self.status = step.status

    @property
    def line(self) -> str:
        glyph, _ = _GLYPH.get(self.status, _GLYPH[StepStatus.PENDING])
        return f"{glyph} {self.title}"


class PlanView(QWidget):
    """Live list of a task's steps. Usable standalone or embedded in the
    approval dialog, where it is what the user is being asked to approve."""

    EMPTY_TEXT = "No plan yet — the agent is still planning."

    def __init__(self, manager, task_id: str, bridge=None, *, embedded: bool = False,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        # The stylesheet applies either way - it carries the label colours
        # and the transparent scroll viewport, and without it an embedded
        # plan renders white-on-white inside the approval dialog. Only the
        # window chrome (the rounded #panel frame, the size) is skipped.
        self.setStyleSheet(PLAN_QSS)
        if not embedded:
            self.setObjectName("panel")
            self.resize(design.PANEL_WIDTH + 80, design.PANEL_HEIGHT)
        self._manager = manager
        self._task_id = task_id
        self._rows: list[PlanStepRow] = []

        task = manager.get_task(task_id)
        self.setWindowTitle(f"Plan — {task.title}" if task else "Plan")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0) if embedded else outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(8)

        if not embedded:
            title = QLabel(task.title if task else "Plan")
            title.setObjectName("title")
            title.setWordWrap(True)
            outer.addWidget(title)

        self._empty = QLabel(self.EMPTY_TEXT)
        self._empty.setObjectName("planEmpty")
        self._empty.setWordWrap(True)
        outer.addWidget(self._empty)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._holder = QWidget()
        self._holder.setObjectName("planHolder")
        self._scroll.viewport().setAutoFillBackground(False)
        self._list = QVBoxLayout(self._holder)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(1)
        self._list.addStretch(1)
        self._scroll.setWidget(self._holder)
        outer.addWidget(self._scroll, 1)

        self._note = QLabel("")
        self._note.setObjectName("planNote")
        self._note.setWordWrap(True)
        self._note.setVisible(False)
        outer.addWidget(self._note)

        self.refresh()

        if bridge is not None:
            self._bridge = bridge
            self._owns_bridge = False
        else:
            from .bridge import AgentEventBridge
            self._bridge = AgentEventBridge(manager, self)
            self._owns_bridge = True
        self._bridge.event.connect(self._on_event)

    # -- updates -------------------------------------------------------------

    _WATCHED = {EventKind.PLAN_CREATED, EventKind.STEP_STARTED, EventKind.STEP_DONE,
                EventKind.STEP_FAILED, EventKind.TASK_STATUS}

    def _on_event(self, ev) -> None:
        if ev.task_id == self._task_id and ev.kind in self._WATCHED:
            if ev.kind is EventKind.PLAN_CREATED and ev.data.get("revised"):
                self._note.setText("The agent revised this plan after a failure.")
                self._note.setVisible(True)
            self.refresh()

    def refresh(self) -> None:
        """Rebuild from the task's current steps.

        Cheap and total rather than clever: plans are short, and a replan
        can reorder or insert steps, so diffing would buy nothing but bugs.
        """
        task = self._manager.get_task(self._task_id)
        steps = list(task.steps) if task else []

        while self._list.count() > 1:                  # keep the trailing stretch
            item = self._list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._rows.clear()

        for i, step in enumerate(steps):
            row = PlanStepRow(i, step)
            self._rows.append(row)
            self._list.insertWidget(self._list.count() - 1, row)

        self._empty.setVisible(not steps)
        self._scroll.setVisible(bool(steps))

    # -- for tests -----------------------------------------------------------

    @property
    def lines(self) -> list[str]:
        return [r.line for r in self._rows]

    def closeEvent(self, e) -> None:
        if self._owns_bridge:
            self._bridge.close()
        super().closeEvent(e)
