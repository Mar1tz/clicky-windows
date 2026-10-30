"""A job's details, and the words the shape's Jobs tab says about a job.

The Agents page that used to be here went with the app window (revamp step
6b): jobs are given and watched in the shape (ui/shape_pages.py JobsTab),
and a job's details slide in over it (JobDrawer) - its goal, the plan as a
checklist, its live activity, how it ended, and its folder.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ui import app_theme as T
from ui import tokens
from ui.app_pages import _clip
from ui.app_parts import Drawer
from ui.app_widgets import Pill, button, icon, label


def status_words(status: str) -> tuple[str, str]:
    """How a job's state reads, and its colour - looked up now, so a theme
    change is followed."""
    return {
        "completed": ("Finished", T.OK),
        "failed": ("Didn't finish", T.DANGER),
        "cancelled": ("Stopped", T.TEXT_FAINT),
        "running": ("Working", T.ACCENT),
        "planning": ("Planning", T.ACCENT),
        "verifying": ("Checking its work", T.ACCENT),
        "pending": ("Queued", T.TEXT_DIM),
        "waiting_approval": ("Waiting for you", T.WARN),
        "paused": ("Paused", T.WARN),
        "needs_attention": ("Needs you", T.WARN),
        "waiting_user": ("Your turn", T.WARN),
        "waiting": ("Waiting", T.TEXT_DIM),
        "retry_wait": ("Trying again soon", T.TEXT_DIM),
        "rejected": ("Plan declined", T.TEXT_FAINT),
    }.get(status, (status.replace("_", " ").capitalize(), T.TEXT_DIM))


def ran_for(start: float, end: float) -> str:
    """"4 min", "1h 12m" - how long a job has run (its own timestamps)."""
    from ui.insights_data import minutes
    if not start or not end or end < start:
        return ""
    secs = end - start
    return "under a minute" if secs < 60 else minutes(round(secs / 60))


# ── the drawer ────────────────────────────────────────────────────────────────


class JobDrawer(Drawer):
    """A job's details (Drawer): what it was asked, its plan as a checklist,
    its live activity, how it ended, its folder."""

    def __init__(self, page: QWidget, bridge, host: QWidget | None = None):
        super().__init__(page, host)
        self.bridge = bridge
        self._task_id = ""
        self._events = None
        self._timeline = None

    def show_job(self, task_id: str) -> None:
        self._task_id = task_id
        self.open()

    def refresh(self) -> None:
        job = self.bridge.job(self._task_id) if self._task_id else None
        self.clear(keep=(self._timeline,))
        if job is None:
            self.body.addWidget(label("That job is no longer here.", "dim"))
            return
        self.header(job["title"])
        words, colour = status_words(job["status"])
        meta = words
        took = ran_for(job["started"], job["updated"] if job["status"] in
                       ("completed", "failed", "cancelled", "rejected") else time.time())
        if took:
            meta += f" · ran for {took}"
        state = QHBoxLayout()
        state.addWidget(Pill(words, colour, dot=True))
        state.addWidget(label(meta.split(" · ", 1)[1] if " · " in meta else "", "faint"))
        state.addStretch(1)
        sw = QWidget()
        sw.setLayout(state)
        self.body.addWidget(sw)
        if job["goal"] and job["goal"] != job["title"]:
            self.body.addWidget(label("WHAT YOU ASKED", "sectionTitle"))
            self.body.addWidget(label(job["goal"], wrap=True))

        if job["steps"]:
            self.body.addSpacing(6)
            self.body.addWidget(label("THE PLAN", "sectionTitle"))
            for i, st in enumerate(job["steps"], 1):
                self.body.addWidget(_step_row(i, st))
        if job["result"] and job["status"] == "completed":
            self.body.addSpacing(6)
            self.body.addWidget(label("WHAT IT DID", "sectionTitle"))
            self.body.addWidget(label(_clip(job["result"], 1200), wrap=True))
        if job["error"] and job["status"] in ("failed", "cancelled", "rejected"):
            self.body.addSpacing(6)
            self.body.addWidget(label("WHY IT STOPPED", "sectionTitle"))
            why = label(_clip(job["error"], 600), wrap=True)
            why.setStyleSheet(f"color: {T.DANGER};")
            self.body.addWidget(why)

        self.body.addSpacing(6)
        self.body.addWidget(label("ACTIVITY", "sectionTitle"))
        self.body.addWidget(self._activity(job["id"]))

        foot = QHBoxLayout()
        foot.setSpacing(8)
        if job["folder"]:
            folder = button("Open folder")
            folder.clicked.connect(lambda: self.bridge.open_folder(job["folder"]))
            foot.addWidget(folder)
        if job["status"] in ("completed", "failed", "cancelled", "rejected") and job["goal"]:
            again = button("Run again", "primary")
            again.clicked.connect(lambda: (self.bridge.give_job(job["goal"]), self.close_drawer()))
            foot.addWidget(again)
        if job["status"] == "completed" and not job.get("skill") and job["steps"]:
            keep = button("Save as a skill")
            keep.setToolTip("Do it this way next time: a draft from this job, for you to check first")
            keep.clicked.connect(lambda: self._save_as_skill(job["id"]))
            foot.addWidget(keep)
        elif job["status"] not in ("completed", "failed", "cancelled", "rejected"):
            stop = button("Stop", "danger")
            stop.clicked.connect(lambda: self.bridge.stop_job(job["id"]))
            foot.addWidget(stop)
        foot.addStretch(1)
        fw = QWidget()
        fw.setLayout(foot)
        self.body.addSpacing(8)
        self.body.addWidget(fw)
        self.body.addStretch(1)

    def _save_as_skill(self, task_id: str) -> None:
        """A draft skill from this job, in the editor - nothing is saved
        until the user has read it and pressed Save (SK-05)."""
        draft = self.bridge.skill_from_job(task_id)
        if draft is None:
            return
        from ui.skills_page import open_editor
        over = self.window()
        self.close_drawer()
        self._editor = open_editor(over, self.bridge, draft, lambda: None)

    def _activity(self, task_id: str) -> QWidget:
        """The workforce's own activity log for the job - every tool it ran,
        live - restyled to the app (app_pages._tidy). One listener for the
        drawer, however many jobs it shows."""
        wf = self.bridge.workforce()
        if wf is None:
            return label("No activity to show.", "faint")
        try:
            from agents.ui import ActivityTimeline, AgentEventBridge
            from ui.app_pages import _tidy
            if self._events is None:
                self._events = AgentEventBridge(wf, self)
            if self._timeline is not None and getattr(self._timeline, "_task", None) != task_id:
                self._timeline.deleteLater()
                self._timeline = None
            if self._timeline is None:
                self._timeline = _tidy(ActivityTimeline(wf, task_id, bridge=self._events))
                self._timeline._task = task_id
                self._timeline.layout().setContentsMargins(0, 0, 0, 0)
                self._timeline.setMinimumHeight(140)
            return self._timeline
        except Exception:  # noqa: BLE001
            return label("No activity to show.", "faint")


def _step_row(i: int, st: dict) -> QWidget:
    """One step of the plan: done (a tick), on now (the accent), waiting,
    or failed - and what it did or why it stopped."""
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 2, 0, 2)
    lay.setSpacing(10)
    status = st["status"]
    mark = QLabel()
    if status == "done":
        mark.setPixmap(icon("check", T.OK, 16))
    elif status == "running":
        mark.setPixmap(_dot_pixmap(T.ACCENT))
    elif status in ("failed", "error"):
        mark.setPixmap(icon("x", T.DANGER, 16) if _has_icon("x") else _dot_pixmap(T.DANGER))
    else:
        mark.setPixmap(_dot_pixmap(T.BORDER_STRONG))
    mark.setFixedWidth(18)
    lay.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
    text = QVBoxLayout()
    text.setSpacing(2)
    text.addWidget(label(f"{i}. {st['title']}", "dim" if status not in ("done", "running") else "", wrap=True))
    note = st["error"] if status in ("failed", "error") else st["summary"]
    if note:
        text.addWidget(label(_clip(note, 240), "faint", wrap=True))
    lay.addLayout(text, 1)
    return row


def _has_icon(name: str) -> bool:
    return (tokens.assets_dir() / "icons" / f"{name}.svg").exists()


def _dot_pixmap(colour: str):
    from PyQt6.QtGui import QPixmap
    px = QPixmap(32, 32)
    px.setDevicePixelRatio(2.0)
    px.fill(QColor(0, 0, 0, 0))
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(colour))
    p.drawEllipse(4, 4, 8, 8)
    p.end()
    return px
