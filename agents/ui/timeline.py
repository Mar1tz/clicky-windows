"""Activity timeline - what the agent actually did, minute by minute.

    21:40  Created project folder
    21:42  Installed dependencies
    21:45  Created files
             src/
               components/
               pages/
    21:48  Running tests

Two sources, one renderer: `TaskStore.events(task_id)` replays history
(so a task from last week reads the same as one running now) and the
`AgentEventBridge` appends live rows as they arrive. Both hand over
`agents.core.Event`, so `TimelineRow` never needs to know which it got.

Steps are the spine: tool calls and their results fold in underneath the
step that produced them, and everything else lands at the top level.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Iterable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

from agents.core import Event, EventKind
from ui import design

log = logging.getLogger("agents.ui.timeline")

# What each kind looks like: (glyph, colour, indented under its step?)
_STYLE: dict[EventKind, tuple[str, object, bool]] = {
    EventKind.TASK_CREATED:      ("●", design.BLUE_GLOW, False),
    EventKind.PLAN_CREATED:      ("☰", design.BLUE_GLOW, False),
    EventKind.STEP_STARTED:      ("▸", design.TEXT_PRIMARY, False),
    EventKind.STEP_DONE:         ("✓", design.SUCCESS, False),
    EventKind.STEP_FAILED:       ("✕", design.ERROR, False),
    EventKind.TOOL_CALL:         ("·", design.TEXT_SECONDARY, True),
    EventKind.TOOL_RESULT:       ("·", design.TEXT_SECONDARY, True),
    EventKind.VERIFICATION:      ("✓", design.SUCCESS, True),
    EventKind.APPROVAL_REQUIRED: ("!", design.WARNING, False),
    EventKind.APPROVAL_RESOLVED: ("·", design.TEXT_SECONDARY, True),
    EventKind.TASK_STATUS:       ("●", design.TEXT_SECONDARY, False),
    EventKind.ERROR:             ("✕", design.ERROR, False),
    EventKind.LOG:               ("·", design.TEXT_MUTED, True),
}

# Progress fires on every step and would drown the timeline.
_HIDDEN = {EventKind.TASK_PROGRESS, EventKind.AGENT_STATUS}

TIMELINE_QSS = design.PANEL_QSS + """
QLabel#tlTime { font-size: 11px; color: rgb(120, 120, 140); }
QLabel#tlText { font-size: 12px; color: rgb(225, 225, 235); }
QLabel#tlSub  { font-size: 11px; color: rgb(150, 150, 170); }
QLabel#tlGlyph { font-size: 12px; }
QLabel#tlEmpty { font-size: 12px; color: rgb(140, 140, 160); }
QFrame#tlRow { background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QWidget#tlHolder { background: transparent; }
"""


def describe(ev: Event) -> tuple[str, str]:
    """(headline, detail) for one event. Detail may be "".

    Written for a person reading down the column, so it says "Created
    package.json", not "tool.result ok=True". An empty headline means
    "don't show this row at all" - used where an event would only repeat
    what the rows around it already said.
    """
    kind, data = ev.kind, ev.data

    if kind is EventKind.TASK_CREATED:
        return f"Task started: {ev.message}", ""
    if kind is EventKind.PLAN_CREATED:
        steps = (data.get("plan") or {}).get("steps") or []
        titles = [s.get("title", "") for s in steps][:12]
        return ev.message, "\n".join(f"{i + 1}. {t}" for i, t in enumerate(titles) if t)
    if kind is EventKind.STEP_STARTED:
        idx, total = data.get("index"), data.get("total")
        where = f"Step {idx + 1}/{total}: " if isinstance(idx, int) and total else ""
        return f"{where}{ev.message}", ""
    if kind is EventKind.STEP_DONE:
        # A step that ran tools has already told its story through the
        # tool rows and the verification tick; its summary would just be a
        # fourth way of saying "create_file". Steps with no tools (an
        # LLM-only step) have nothing else, so their summary is the row.
        step = data.get("step") or {}
        if step.get("tool_calls"):
            return "", ""
        return ev.message or step.get("title", ""), ""
    if kind is EventKind.STEP_FAILED:
        return f"Failed: {ev.message}", ""
    if kind is EventKind.TOOL_CALL:
        call = data.get("call") or {}
        return f"{call.get('tool_name', 'tool')}({_args(call.get('arguments'))})", ""
    if kind is EventKind.TOOL_RESULT:
        res = data.get("result") or {}
        name = res.get("tool_name", "tool")
        if not res.get("ok", True):
            return f"{name} failed", str(res.get("error", ""))[:200]
        return _result_headline(name, res), ""
    if kind is EventKind.VERIFICATION:
        checks = (data.get("result") or {}).get("checks") or []
        passed = sum(1 for c in checks if c.get("passed"))
        scope = data.get("scope", "step")
        # The verifier's summary usually already counts the checks; only
        # add the tally when it does not.
        tail = "" if ("check" in ev.message.lower() or not checks) \
            else f" ({passed}/{len(checks)} checks)"
        return f"Verified {scope}: {ev.message}{tail}", ""
    if kind is EventKind.APPROVAL_REQUIRED:
        return f"Waiting for approval — {ev.message}", ""
    if kind is EventKind.APPROVAL_RESOLVED:
        return f"Approval {ev.message}", ""
    if kind is EventKind.TASK_STATUS:
        return f"Task {ev.message}", ""
    if kind is EventKind.ERROR:
        return ev.message, ""
    return ev.message, ""


def _result_headline(name: str, res: dict) -> str:
    """Name the thing that changed, not the tool that changed it."""
    path = (res.get("data") or {}).get("path")
    if path:
        leaf = os.path.basename(str(path).rstrip("/\\")) or str(path)
        verbs = {"create_folder": "Created folder", "create_file": "Created",
                 "write_file": "Wrote", "edit_file": "Edited",
                 "move_path": "Moved", "delete_path": "Deleted"}
        if name in verbs:
            return f"{verbs[name]} {leaf}"
    out = (res.get("output") or "").strip().splitlines()
    return out[0][:120] if out else f"{name} ok"


# Arguments that carry a whole file. Their size is informative; their
# contents turn the timeline into a code dump.
_BULK_ARGS = {"content", "text", "body", "new_string", "old_string", "script"}


def _args(args: Optional[dict]) -> str:
    if not args:
        return ""
    bits = []
    for k, v in list(args.items())[:3]:
        if k in _BULK_ARGS:
            bits.append(f"{k}={len(str(v))} chars")
            continue
        text = str(v).replace("\n", " ")
        bits.append(f"{k}={text[:40]}…" if len(text) > 40 else f"{k}={text}")
    return ", ".join(bits)


class TimelineRow(QFrame):
    """One line: time · glyph · text, with an optional indented detail."""

    def __init__(self, ev: Event, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("tlRow")
        self.kind = ev.kind
        glyph, colour, indented = _STYLE.get(ev.kind, ("·", design.TEXT_SECONDARY, False))
        headline, detail = describe(ev)
        self.headline = headline

        root = QVBoxLayout(self)
        root.setContentsMargins(20 if indented else 0, 0, 0, 0)
        root.setSpacing(1)

        line = QHBoxLayout()
        line.setSpacing(8)
        stamp = QLabel(time.strftime("%H:%M", time.localtime(ev.timestamp)))
        stamp.setObjectName("tlTime")
        stamp.setFixedWidth(36)
        mark = QLabel(glyph)
        mark.setObjectName("tlGlyph")
        mark.setFixedWidth(12)
        mark.setStyleSheet(f"color: rgb({colour.red()}, {colour.green()}, {colour.blue()});")
        text = QLabel(headline)
        text.setObjectName("tlText")
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if ev.level == "error":
            text.setStyleSheet("color: rgb(255, 120, 120);")
        line.addWidget(stamp)
        line.addWidget(mark)
        line.addWidget(text, 1)
        root.addLayout(line)

        if detail:
            sub = QLabel(detail)
            sub.setObjectName("tlSub")
            sub.setContentsMargins(56, 0, 0, 4)
            sub.setWordWrap(True)
            root.addWidget(sub)


class ActivityTimeline(QWidget):
    """Scrollable activity log for one task: history then live rows."""

    EMPTY_TEXT = "No activity recorded for this task."

    def __init__(self, manager, task_id: str, bridge=None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.setStyleSheet(TIMELINE_QSS)
        self.resize(design.PANEL_WIDTH + 160, design.PANEL_HEIGHT)

        self._manager = manager
        self._task_id = task_id
        self._rows: list[TimelineRow] = []
        self._seen: set[str] = set()

        task = manager.get_task(task_id)
        self.setWindowTitle(f"Activity — {task.title}" if task else "Activity")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(8)

        title = QLabel(task.title if task else "Activity")
        title.setObjectName("title")
        title.setWordWrap(True)
        outer.addWidget(title)

        self._empty = QLabel(self.EMPTY_TEXT)
        self._empty.setObjectName("tlEmpty")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self._empty, 1)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        holder.setObjectName("tlHolder")
        self._scroll.viewport().setAutoFillBackground(False)
        self._list = QVBoxLayout(holder)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(2)
        self._list.addStretch(1)
        self._scroll.setWidget(holder)
        self._scroll.setVisible(False)
        outer.addWidget(self._scroll, 1)

        self.load_history()

        from .bridge import AgentEventBridge
        self._bridge = bridge or AgentEventBridge(manager, self)
        self._owns_bridge = bridge is None
        self._bridge.log_line.connect(self.on_event)

    # -- sources -------------------------------------------------------------

    def load_history(self) -> int:
        """Replay everything the store already holds for this task.

        This is what makes a finished task readable after a restart: the
        events outlive the process because SQLiteTaskStore subscribed to
        the bus while they were happening.
        """
        store = getattr(self._manager, "store", None)
        events: Iterable[Event] = ()
        if store is not None:
            try:
                events = store.events(self._task_id, limit=2000)
            except Exception:
                log.exception("reading history for %s failed", self._task_id)
        if not events:                      # no store, or nothing persisted yet
            events = self._manager.events.history(task_id=self._task_id, limit=2000)
        added = 0
        for ev in events:
            added += int(self._append(ev))
        return added

    def on_event(self, ev: Event) -> None:
        if ev.task_id == self._task_id:
            self._append(ev)

    # -- rendering -----------------------------------------------------------

    def _append(self, ev: Event) -> bool:
        if ev.kind in _HIDDEN or ev.id in self._seen:
            return False
        headline, _detail = describe(ev)
        if not headline:                    # describe() asked us to skip it
            self._seen.add(ev.id)
            return False
        self._seen.add(ev.id)
        row = TimelineRow(ev)
        self._rows.append(row)
        self._list.insertWidget(self._list.count() - 1, row)
        self._empty.setVisible(False)
        self._scroll.setVisible(True)
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        return True

    # -- for tests -----------------------------------------------------------

    @property
    def rows(self) -> list[TimelineRow]:
        return list(self._rows)

    @property
    def lines(self) -> list[str]:
        return [r.headline for r in self._rows]

    def closeEvent(self, e) -> None:
        if self._owns_bridge:
            self._bridge.close()
        super().closeEvent(e)
