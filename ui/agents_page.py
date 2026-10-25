"""The Agents page (DESIGN.md §3F): give Tora a job, answer what her agents
are waiting on, watch what is running, and look back at what they did.

It used to embed the workforce's own floating-window panels (agents/ui,
worker 1's) - the old dark look, a job box hidden inside them, approvals
that only said "answer in the approval window", and a job's details opened
at the foot of a long page. Those panels stay for the tray; this page draws
its own, from the same workforce (through ui/app_bridge.py), in the app's
two columns:

    left    Give Tora a job · Needs your OK · Working now · History
    right   this month's jobs, sticky, and Agent settings

A job's details slide in from the right (JobDrawer): its goal, the plan as
a checklist, its live activity, how it ended, and its folder.
"""

from __future__ import annotations

import time
from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from ui import app_theme as T
from ui import tokens
from ui.app_pages import Page, _ask, _clip, refill
from ui.app_widgets import Dot, Meter, Pill, button, icon, label


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


# Jobs to try, in the user's words - each one the agents can really do
# inside their workspace (they only ever write there).
# (what the chip says, the job it fills in)
EXAMPLES = (("Coffee shop website", "Build a one-page website for my coffee shop"),
            ("Compare laptops", "Research the best laptops under R15,000 and compare them in a table"),
            ("Rename my photos", "Write a script that renames my photos by the date they were taken"))



class AgentsPage(Page):
    HEADING = "Agents"
    LIVE = True
    TOPICS = frozenset({"agents"})

    def __init__(self, bridge=None):
        self._parts = None
        self._drawer: Optional[JobDrawer] = None
        self._shown_running: tuple = ()
        super().__init__(bridge)

    def build_actions(self) -> None:
        self.action("Agent settings").clicked.connect(lambda: self.navigate.emit("settings/agents"))
        self.action("Stop all", "danger").clicked.connect(self.bridge.stop_agents)

    # ── layout: built once, refilled ─────────────────────────────────────────

    def build(self) -> None:
        if self.bridge.workforce() is None:
            self._parts = None
            self.body.add(_no_agents())
            return
        if self._parts is None:
            parts = {}
            self.body.add(self._composer())
            for name in ("waiting", "running", "history"):
                host = QWidget()
                lay = QVBoxLayout(host)
                lay.setContentsMargins(0, 0, 0, 0)
                lay.setSpacing(T.GAP)
                self.body.add(host)
                parts[name] = lay
            self._parts = parts
            # A running job's clock moves even when nothing else does.
            self._tick = QTimer(self)
            self._tick.setInterval(15000)
            self._tick.timeout.connect(self._fill_running)
            self._tick.start()
        self._fill()

    def refresh(self) -> None:
        if self._parts is None:
            if self.bridge.workforce() is not None:
                self.body.clear()
                super().refresh()          # the agents started after this page was drawn
            return
        self._fill()
        if self._drawer is not None and self._drawer.isVisible():
            self._drawer.refresh()

    def _fill(self) -> None:
        self._fill_waiting()
        self._fill_running()
        self._fill_history()
        self._aside()

    # ── give Tora a job ──────────────────────────────────────────────────────

    def _composer(self) -> QWidget:
        from ui.app_parts import growing_text
        card = QFrame()
        card.setObjectName("upNext")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(26, 22, 26, 22)
        lay.setSpacing(10)
        lay.addWidget(label("GIVE TORA A JOB", "sectionTitle"))
        self._job = growing_text("", "What should she work on? Say what you want made, found or fixed.",
                                 least=64, most=200)
        lay.addWidget(self._job)
        row = QHBoxLayout()
        row.setSpacing(8)
        for short, words in self._examples():
            chip = button(short)
            chip.setObjectName("chip")
            chip.setToolTip(words)
            chip.clicked.connect(lambda _c=False, w=words: self._job.setPlainText(w))
            row.addWidget(chip)
        row.addStretch(1)
        go = button("Start", "primary")
        go.clicked.connect(self._start)
        row.addWidget(go)
        lay.addLayout(row)
        self._said = label("Or just ask Tora out loud - “build me…”, “research…”.", "faint", wrap=True)
        lay.addWidget(self._said)
        return card

    def _examples(self) -> list[tuple[str, str]]:
        # The same three for everyone; the user's own jobs are their skills.
        return list(EXAMPLES)[:3]

    def _start(self) -> None:
        text = self._job.toPlainText().strip()
        if not text:
            self._said.setText("Say what you want done first.")
            return
        kind, words = self.bridge.give_job(text)
        if kind == "started":
            self._job.clear()
            self._said.setText(f"Started: {words}. It shows under Working now.")
        elif kind == "chat":
            self._said.setText("That reads like a question, not a job - ask Tora out loud "
                               "and she will answer it.")
        else:
            self._said.setText(f"That didn't start: {words}")

    # ── needs your OK ────────────────────────────────────────────────────────

    def _fill_waiting(self) -> None:
        from ui.app_parts import ListBox, section_head
        waiting = _ask(self.bridge, "approvals", []) or []
        widgets: list[QWidget] = []
        if waiting:
            widgets.append(section_head("Needs your OK"))
            box = ListBox()
            for a in waiting:
                box.add(self._approval(a))
            widgets.append(box)
        refill(self._parts["waiting"], widgets)
        self._parts["waiting"].parentWidget().setVisible(bool(waiting))

    def _approval(self, a: dict) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(18, 14, 14, 14)
        lay.setSpacing(14)
        lay.addWidget(Dot(T.WARN, 8), 0, Qt.AlignmentFlag.AlignVCenter)
        text = QVBoxLayout()
        text.setSpacing(3)
        text.addWidget(label(_clip(a["reason"], 200), "rowTitle", wrap=True))
        sub = a["task"] + (" · " if a["task"] else "") + a["tool"]
        text.addWidget(label(sub, "rowSub", wrap=True))
        lay.addLayout(text, 1)
        word = None
        if a.get("sensitive"):
            word = QLineEdit()
            word.setPlaceholderText(f"Type {a.get('confirm_word') or 'the word'} to approve")
            word.setFixedWidth(200)
            lay.addWidget(word, 0, Qt.AlignmentFlag.AlignVCenter)
        yes = button("Approve", "primary")
        no = button("Deny")
        yes.clicked.connect(lambda: self._answer(a["id"], True, word.text() if word else ""))
        no.clicked.connect(lambda: self._answer(a["id"], False))
        lay.addWidget(yes, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(no, 0, Qt.AlignmentFlag.AlignVCenter)
        return row

    def _answer(self, request_id: str, approved: bool, confirmation: str = "") -> None:
        self.bridge.answer_approval(request_id, approved, confirmation)
        self._later(self._fill)

    # ── working now ──────────────────────────────────────────────────────────

    def _fill_running(self) -> None:
        from ui.app_parts import section_head
        jobs = _ask(self.bridge, "running", []) or []
        widgets: list[QWidget] = []
        if jobs:
            widgets.append(section_head("Working now"))
            widgets += [self._job_card(j) for j in jobs]
        refill(self._parts["running"], widgets)
        self._parts["running"].parentWidget().setVisible(bool(jobs))

    def _job_card(self, j: dict) -> QWidget:
        card = _Clickable(lambda: self.open_job(j["id"]))
        card.setObjectName("upNext")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(10)
        words, colour = status_words(j["status"])
        top.addWidget(Dot(colour, 8, glow=j["status"] == "running"), 0, Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(label(_clip(j["title"], 90), "cardTitle"), 1)
        took = ran_for(j["started"], time.time())
        if took:
            top.addWidget(label(took, "faint"))
        paused = j["status"] in ("paused", "needs_attention", "waiting_user")
        hold = button("Resume" if paused else "Pause")
        hold.clicked.connect(lambda: (self.bridge.resume_job(j["id"]) if paused
                                      else self.bridge.pause_job(j["id"]), self._later(self._fill)))
        top.addWidget(hold)
        stop = button("Stop", "danger")
        stop.clicked.connect(lambda: (self.bridge.stop_job(j["id"]), self._later(self._fill)))
        top.addWidget(stop)
        lay.addLayout(top)
        where = f"Step {j['step']} of {j['total']}" if j["total"] else words
        doing = j["doing"] or words
        lay.addWidget(label(f"{where} · {doing}" if j["total"] else doing, "dim", wrap=True))
        bar = QHBoxLayout()
        bar.setSpacing(12)
        bar.addWidget(Meter(j["progress"], T.WARN if paused else T.ACCENT), 1)
        bar.addWidget(label(f"{round(j['progress'] * 100)}%", "upPct"))
        lay.addLayout(bar)
        return card

    # ── history ──────────────────────────────────────────────────────────────

    def _fill_history(self) -> None:
        from ui.app_parts import HoverRow, ListBox, RowAction, day_name, empty, section_head
        tasks = [t for t in (_ask(self.bridge, "tasks", [], 60) or [])
                 if t["status"] in ("completed", "failed", "cancelled", "rejected")]
        widgets: list[QWidget] = []
        if not tasks:
            widgets.append(section_head("History"))
            widgets.append(empty("No jobs yet", "Give Tora a job above - finished ones are "
                                 "listed here, with how they went.", "agents"))
        box, day = None, ""
        for t in tasks:
            name = day_name(t["when"]) if t["when"] else "Earlier"
            if name != day:
                day, box = name, ListBox()
                widgets.append(section_head(name))
                widgets.append(box)
            row = HoverRow()
            lay = QHBoxLayout(row)
            lay.setContentsMargins(18, 14, 14, 14)
            lay.setSpacing(14)
            words, colour = status_words(t["status"])
            lay.addWidget(Dot(colour, 8), 0, Qt.AlignmentFlag.AlignVCenter)
            text = QVBoxLayout()
            text.setSpacing(3)
            text.addWidget(label(_clip(t["title"], 110), "rowTitle", wrap=True))
            sub = words
            took = ran_for(t.get("created", 0), t["when"])
            if took:
                sub += f" · ran for {took}"
            if t["status"] == "failed" and t.get("error"):
                sub += f" · {t['error']}"
            text.addWidget(label(sub, "rowSub", wrap=True))
            lay.addLayout(text, 1)
            row.actions_lay.addWidget(RowAction("folder-open", "Open its folder",
                                                lambda tid=t["id"]: self._open_folder(tid)))
            row.actions.setFixedWidth(32)
            row._show_actions(False)
            lay.addWidget(row.actions, 0, Qt.AlignmentFlag.AlignVCenter)
            row.clicked.connect(lambda tid=t["id"]: self.open_job(tid))
            box.add(row)
        refill(self._parts["history"], widgets)
        self.body.place_aside()

    def _open_folder(self, task_id: str) -> None:
        job = self.bridge.job(task_id)
        if job:
            self.bridge.open_folder(job["folder"])

    # ── the aside ────────────────────────────────────────────────────────────

    def _aside(self) -> None:
        from ui import insights_data as data
        from ui.app_parts import SideCard
        month = data.agents_month(_ask(self.bridge, "tasks", [], 500) or [])
        foot = QWidget()
        lay = QVBoxLayout(foot)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        if month["ran_min"]:
            lay.addWidget(label(f"Ran for {data.minutes(month['ran_min'])} this month", "faint"))
        auto = _ask(self.bridge, "auto_approve", False)
        lay.addWidget(label("Approve automatically: " + ("on" if auto else "off"), "faint"))
        go = button("Agent settings", "link")
        go.clicked.connect(lambda: self.navigate.emit("settings/agents"))
        lay.addWidget(go, 0, Qt.AlignmentFlag.AlignLeft)
        self.body.set_aside(SideCard([
            (str(month["done"]), "finished this month"),
            (str(month["failed"]), "didn't finish"),
            (str(month["running"]), "running"),
        ], foot), SideCard.W)

    # ── a job's details ──────────────────────────────────────────────────────

    def open_job(self, task_id: str) -> None:
        if self._drawer is None:
            self._drawer = JobDrawer(self, self.bridge)
        self._drawer.show_job(task_id)

    def _later(self, fn) -> None:
        """After the click returns: what was clicked is one the redraw replaces."""
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(fn)
        timer.timeout.connect(timer.deleteLater)
        timer.start(0)


class _Clickable(QFrame):
    def __init__(self, on_click):
        super().__init__()
        self._on_click = on_click
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self._on_click()
        super().mouseReleaseEvent(e)


def _no_agents() -> QWidget:
    from ui.app_parts import empty
    return empty("The agents did not start",
                 "Nothing can be handed to them until they do - see tora.log for why.", "agents")


# ── the drawer ────────────────────────────────────────────────────────────────

from ui.app_parts import Drawer  # noqa: E402


class JobDrawer(Drawer):
    """A job's details (Drawer): what it was asked, its plan as a checklist,
    its live activity, how it ended, its folder."""

    def __init__(self, page: QWidget, bridge):
        super().__init__(page)
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
