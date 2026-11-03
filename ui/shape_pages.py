"""The shape's own pages (DESIGN.md §3), sized for it: 520 wide, not the app
window's 1040 column. Home, Chat and Jobs are its tabs. (Insights was one
until 31 Oct: in the small shape it was four numbers - its calendar is on
Settings > Account now, and all of it opens as a whole page.)

Home: a greeting and one line of what is happening, then ONE card - the
newest thing Tora finished (an agent job or the last answer) - and, with
nothing yet, the shortcut and something to try. Live: rebuilt when it is
shown and when what it shows changes (TOPICS), as the app window's pages.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLineEdit, QScrollArea, QVBoxLayout, QWidget

from ui.app_widgets import Meter, button, label, later
from ui.settings_parts import keycaps

# What the newest-result card says for a job, by how it ended.
ENDED = {"completed": "Finished", "failed": "Didn't finish", "cancelled": "Stopped"}
SOMETHING_TO_TRY = "What's on my screen right now?"


def greeting(name: str, hour: int) -> str:
    """"Evening, Dylan." - or just "Good evening." without a name."""
    part = "Morning" if 5 <= hour < 12 else "Afternoon" if 12 <= hour < 18 else "Evening"
    return f"{part}, {name}." if name else f"Good {part.lower()}."


def status_line(state: str, running: int) -> str:
    """One line of what is happening: jobs first, then Tora herself."""
    if running:
        return f"{running} job working" if running == 1 else f"{running} jobs working"
    return {"listening": "Listening", "thinking": "Thinking", "speaking": "Speaking"}.get(
        (state or "").lower(), "Ready")


def newest_result(tasks: list[dict], entries: list[dict]) -> dict:
    """The newest finished job or answer, as {kind, title, text, when, id};
    {} when there is neither."""
    best: dict = {}
    for t in tasks:
        if t.get("status") in ENDED:
            best = {"kind": "job", "title": t.get("title") or "A job",
                    "text": t.get("error") or ENDED[t["status"]] + ".",
                    "ended": ENDED[t["status"]], "when": float(t.get("when") or 0.0), "id": t.get("id")}
            break                                    # tasks() is newest first
    for e in entries:
        if (e.get("question") or "").strip():
            when = float(e.get("created_at") or 0.0)
            if when > best.get("when", -1.0):
                best = {"kind": "answer", "title": e["question"].strip(),
                        "text": (e.get("answer") or "").strip(), "when": when}
            break
    return best


def _clip(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


class ShapePage(QWidget):
    """What every page of the shape shares: live (rebuilt when shown and
    when one of its TOPICS changes), reads the bridge without ever falling
    over, and asks for other pages with `navigate`."""

    LIVE = True
    TOPICS: frozenset = frozenset()
    navigate = pyqtSignal(str)
    MARGINS = (24, 8, 24, 16)

    def __init__(self, bridge):
        super().__init__()
        self.bridge = bridge
        self.setObjectName("page")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(*self.MARGINS)
        self.lay.setSpacing(0)
        self.refresh()

    def _call(self, name: str, default, *args):
        try:
            return getattr(self.bridge, name)(*args)
        except Exception:  # noqa: BLE001 - a page never falls over on what it reads
            return default

    def _clear(self) -> None:
        while self.lay.count():
            item = self.lay.takeAt(0)
            w = item.widget()
            if w is not None:
                # Off the page now, not when the event loop gets to it: a
                # redraw before then showed the old greeting under the new.
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def refresh(self) -> None:
        self._clear()
        self.build()

    def build(self) -> None:          # pragma: no cover - overridden
        pass


class HomeTab(ShapePage):
    TOPICS = frozenset({"state", "agents", "journal", "guide", "ready", "account"})

    def build(self) -> None:
        hello = label(greeting(self._call("user_name", ""), datetime.now().hour), "upTitle")
        hello.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.lay.addWidget(hello)
        status = label(status_line(self._call("state", "idle"), len(self._call("agents", []))), "upSub")
        status.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.lay.addWidget(status)
        self.lay.addSpacing(14)
        strip = self._trouble()
        if strip is not None:
            self.lay.addWidget(strip)
            self.lay.addSpacing(10)
        # The cards scroll; they never shrink. Stacked into the shape's fixed
        # height they were squeezed until the words ran into each other (the
        # owner, 29 Oct 2026).
        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)
        ready = self._call("ready_jobs", [])
        if ready:                                        # what the first launch got ready
            col.addWidget(self._ready_card(ready))
        result = newest_result(self._call("tasks", [], 8), self._call("recent", [], 1))
        if result:
            col.addWidget(self._result_card(result))
        elif not ready:
            col.addWidget(self._empty_card())
        col.addStretch(1)
        self.lay.addWidget(_scroller(inner), 1)

    def _trouble(self) -> Optional[QFrame]:
        """Only when she can't answer - out of credits, a refused key, our
        server, no connection - and why, with the fix one click away.
        Everything fine says nothing (the old Home's banner, 6b)."""
        words, fix = self._call("trouble", ("", ""))
        if not words:
            return None
        from ui import app_theme as T
        from ui.app_widgets import Dot
        box = QFrame()
        box.setObjectName("banner")
        lay = QHBoxLayout(box)
        lay.setContentsMargins(14, 10, 12, 10)
        lay.setSpacing(10)
        lay.addWidget(Dot(T.WARN, 8), 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(label(words, "bannerText", wrap=True), 1)
        if fix:
            go = button({"plans": "See plans", "account": "Sign in"}.get(fix, "Fix it"), "link")
            go.clicked.connect(lambda: self.navigate.emit(f"settings/{fix}"))
            lay.addWidget(go, 0, Qt.AlignmentFlag.AlignVCenter)
        return box

    def _ready_card(self, jobs: list[dict]) -> QFrame:
        """The jobs the first launch made ready, a row each: Start asks her
        in the job's own words, Not now puts it away."""
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 14, 14, 14)
        lay.setSpacing(10)
        lay.addWidget(label("READY TO START", "sectionTitle"))
        for index, job in enumerate(jobs):
            row = QHBoxLayout()
            row.setSpacing(10)
            words = QVBoxLayout()
            words.setSpacing(1)
            # The words wrap; the buttons keep their size. Unwrapped, a long
            # line made the row wider than the shape and pushed Start off its
            # edge (31 Oct, seen while recording).
            words.addWidget(label(_clip(job.get("title", ""), 60), "cardTitle", wrap=True))
            if job.get("why"):
                words.addWidget(label(_clip(job["why"], 90), "cardSub", wrap=True))
            row.addLayout(words, 1)
            start = button("Start", "primary")
            start.clicked.connect(lambda _c=False, i=index: self._call("start_ready_job", None, i))
            later = button("Not now", "link")
            later.clicked.connect(lambda _c=False, i=index: self._call("put_away_ready_job", None, i))
            row.addWidget(later)
            row.addWidget(start)
            lay.addLayout(row)
        return card

    def _result_card(self, r: dict) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(6)
        kind = "AGENT JOB · " + r["ended"].upper() if r["kind"] == "job" else "LAST ANSWER"
        # No date, no "20755 D AGO": the label stands on its own.
        lay.addWidget(label(kind + (" · " + _ago(r["when"]) if r["when"] > 0 else ""), "sectionTitle"))
        lay.addWidget(label(_clip(r["title"], 120), "cardTitle", wrap=True))
        if r["text"]:
            lay.addWidget(label(_clip(r["text"], 260), "cardSub", wrap=True))
        lay.addSpacing(6)
        row = QHBoxLayout()
        go = button("Open job" if r["kind"] == "job" else "See everything asked", "ghost")
        target = f"agents/job/{r['id']}" if r["kind"] == "job" and r.get("id") else (
            "agents" if r["kind"] == "job" else "journal")
        go.clicked.connect(lambda: self.navigate.emit(target))
        row.addWidget(go)
        row.addStretch(1)
        lay.addLayout(row)
        return card

    def _empty_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(8)
        lay.addWidget(label("Hold these and ask about anything on your screen.", "cardTitle", wrap=True))
        lay.addWidget(keycaps(self._call("hotkey_keys", ["Ctrl", "Win"])))
        lay.addWidget(label(f"Try: “{SOMETHING_TO_TRY}”", "cardSub", wrap=True))
        return card


def _ago(when: float, now: float = 0.0) -> str:
    s = max(0, int((now or time.time()) - when))
    if s < 60:
        return "JUST NOW"
    if s < 3600:
        return f"{s // 60} MIN AGO"
    if s < 86400:
        return f"{s // 3600} H AGO"
    return f"{s // 86400} D AGO"


def _scroller(inner: QWidget) -> QScrollArea:
    """A page's scrolling part: no frame, the page's own ground."""
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    inner.setObjectName("page")
    area.setWidget(inner)
    return area


# ── Chat ──────────────────────────────────────────────────────────────────────

CHAT_LIMIT = 30
ASK_MAX = 2000           # a question, not a document: attach those


def conversation(entries: list[dict]) -> list[tuple[str, str]]:
    """recent() rows (newest first) as the chat reads them, oldest at the top:
    [("asked", text), ("answer", text), ...]."""
    out: list[tuple[str, str]] = []
    for e in reversed(entries):
        q = (e.get("question") or "").strip()
        if not q:
            continue
        out.append(("asked", q))
        a = (e.get("answer") or "").strip()
        if a:
            out.append(("answer", a))
    return out


class ChatTab(ShapePage):
    """Everything asked, newest at the bottom; Talk and Ask under it, as
    HeyClicky's. Both tuck the shape away first ("talk", "ask:<text>"):
    she looks at the screen, and the shape would be in the picture. Only
    the history redraws - a redraw would wipe a question half typed."""

    TOPICS = frozenset({"journal"})
    MARGINS = (0, 0, 0, 0)

    def __init__(self, bridge):
        self._area: Optional[QScrollArea] = None
        super().__init__(bridge)                 # the history (refresh)
        self.lay.addLayout(self._foot())         # once: it keeps what is typed

    def refresh(self) -> None:
        area = self._history()
        if self._area is None:
            self.lay.insertWidget(0, area, 1)
        else:
            self.lay.replaceWidget(self._area, area)
            self._area.hide()
            self._area.setParent(None)
            self._area.deleteLater()
        self._area = area
        bar = area.verticalScrollBar()
        # Kept at the bottom while the chat settles - built while the shape is
        # still growing (Ctrl, Ctrl), it has no height yet, and one jump to
        # "the bottom" then left it at the top (31 Oct). Scrolled up by hand,
        # it is left where it was put.
        stick = [True]
        bar.valueChanged.connect(lambda v: stick.__setitem__(0, v >= bar.maximum() - 4))
        bar.rangeChanged.connect(lambda _lo, hi: stick[0] and bar.setValue(hi))
        later(area, lambda: bar.setValue(bar.maximum()))

    def to_bottom(self) -> None:
        """The newest message in view."""
        if self._area is not None:
            bar = self._area.verticalScrollBar()
            bar.setValue(bar.maximum())

    def _history(self) -> QScrollArea:
        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(20, 4, 20, 8)
        col.setSpacing(8)
        said = conversation(self._call("recent", [], CHAT_LIMIT))
        col.addStretch(1)                        # a short chat sits at the bottom, by the box
        if not said:
            for text, name in (("Say hi to Tora", "emptyTitle"),
                               ("Everything you ask her, by voice or by typing, lives here.", "emptySub")):
                line = label(text, name)
                line.setAlignment(Qt.AlignmentFlag.AlignHCenter)
                col.addWidget(line)
            col.addStretch(1)
        for who, text in said:
            col.addWidget(self._bubble(who, text))
        return _scroller(inner)

    def _foot(self) -> QHBoxLayout:
        foot = QHBoxLayout()
        foot.setContentsMargins(20, 6, 20, 2)
        foot.setSpacing(8)
        keys = " + ".join(self._call("hotkey_keys", ["Ctrl", "Win"]))
        talk = button(f"Talk  ·  {keys}", "primary")
        talk.setToolTip(f"Tora listens - or hold {keys} anywhere")
        talk.clicked.connect(lambda: self.navigate.emit("talk"))
        foot.addWidget(talk, 2)
        self.ask_box = QLineEdit()
        self.ask_box.setPlaceholderText("Ask Tora…")
        self.ask_box.setMaxLength(ASK_MAX)
        self.ask_box.returnPressed.connect(self._ask)
        foot.addWidget(self.ask_box, 3)
        return foot

    def _ask(self) -> None:
        text = self.ask_box.text().strip()
        if text:
            self.ask_box.clear()
            self.navigate.emit("ask:" + text)

    @staticmethod
    def _bubble(who: str, text: str) -> QWidget:
        frame = QFrame()
        frame.setObjectName("chatAsked" if who == "asked" else "chatAnswer")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.addWidget(label(_clip(text, 600), "", wrap=True))
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        # Asked on the right, her answer on the left, as a chat reads.
        if who == "asked":
            line.addStretch(1)
            line.addWidget(frame, 4)
        else:
            line.addWidget(frame, 5)
            line.addStretch(1)
        return row


# ── Jobs ──────────────────────────────────────────────────────────────────────

HISTORY_LIMIT = 8
ENDED_STATUSES = ("completed", "failed", "cancelled", "rejected")


def _status_words():
    """agents_page.status_words, imported when first needed."""
    from ui.agents_page import status_words
    return status_words


def history(tasks: list[dict]) -> list[dict]:
    """The jobs that have ended, newest first (tasks() is newest first)."""
    return [t for t in tasks if t.get("status") in ENDED_STATUSES][:HISTORY_LIMIT]


class JobsTab(ShapePage):
    """Give Tora a job, answer what her agents wait on, watch what runs,
    look back at what ended - the app window's Agents page, in the shape.

    Running jobs report progress all the time, and a redraw would wipe a
    job half described or a confirm word half typed: the job box is built
    once, and each part redraws only when what it lists has changed (its
    signature); a running job's progress moves in place."""

    TOPICS = frozenset({"agents"})
    MARGINS = (0, 0, 0, 0)

    def __init__(self, bridge):
        self._built = False
        super().__init__(bridge)

    def build(self) -> None:
        inner = QWidget()
        self.col = QVBoxLayout(inner)
        self.col.setContentsMargins(20, 4, 20, 12)
        self.col.setSpacing(8)
        self.col.addWidget(label("GIVE TORA A JOB", "sectionTitle"))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.job_box = QLineEdit()
        self.job_box.setPlaceholderText("Build a one-page site for my coffee shop…")
        self.job_box.setMaxLength(ASK_MAX)
        self.job_box.returnPressed.connect(self._give)
        row.addWidget(self.job_box, 1)
        start = button("Start", "primary")
        start.clicked.connect(self._give)
        row.addWidget(start)
        self.col.addLayout(row)
        self.note = label("", "cardSub", wrap=True)
        self.note.hide()
        self.col.addWidget(self.note)
        self._parts: dict[str, QVBoxLayout] = {}
        self._signatures: dict[str, tuple] = {}
        self._meters: dict = {}
        for part in ("approvals", "running", "history"):
            holder = QVBoxLayout()
            holder.setSpacing(8)
            self.col.addLayout(holder)
            self._parts[part] = holder
        self.col.addStretch(1)
        self.lay.addWidget(_scroller(inner), 1)
        self._built = True
        self._fill()

    def refresh(self) -> None:
        if not self._built:
            super().refresh()
        else:
            self._fill()

    def focus_job_box(self) -> None:
        self.job_box.setFocus()

    # ── the parts ──
    def _fill(self) -> None:
        approvals = self._call("approvals", [])
        running = self._call("running", [])
        ended = history(self._call("tasks", [], 50))
        self._part("approvals", tuple(a.get("id") for a in approvals),
                   lambda: ([label("NEEDS YOUR OK", "sectionTitle")] if approvals else [])
                   + [self._approval(a) for a in approvals])
        self._part("running", tuple((j.get("id"), j.get("status"), j.get("step"), j.get("doing")) for j in running),
                   lambda: ([label("WORKING NOW", "sectionTitle")] if running else [])
                   + [self._running(j) for j in running])
        for j in running:                              # progress moves in place
            meter = self._meters.get(j.get("id"))
            if meter is not None:
                meter.set_fraction(float(j.get("progress") or 0.0))
        if ended:
            make = lambda: [label("HISTORY", "sectionTitle")] + [self._ended(t) for t in ended]  # noqa: E731
        elif not running and not approvals:
            make = lambda: [self._empty()]  # noqa: E731
        else:
            make = list
        self._part("history", tuple((t.get("id"), t.get("status")) for t in ended) or (bool(running or approvals),),
                   make)

    def _part(self, name: str, signature: tuple, make) -> None:
        if name in self._signatures and self._signatures[name] == signature:
            return
        self._signatures[name] = signature
        holder = self._parts[name]
        while holder.count():
            w = holder.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        if name == "running":
            self._meters = {}
        for w in make():
            holder.addWidget(w)

    def _empty(self) -> QWidget:
        box = QFrame()
        box.setObjectName("card")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.addWidget(label("Tora works while you don't.", "cardTitle"))
        lay.addWidget(label("Give her a job above. She plans it, asks before anything that "
                            "pays, sends or deletes, and tells you when it's done.", "cardSub", wrap=True))
        return box

    def _approval(self, a: dict) -> QWidget:
        box = QFrame()
        box.setObjectName("card")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)
        lay.addWidget(label(_clip(a.get("reason", ""), 200), "rowTitle", wrap=True))
        sub = (a.get("task") or "") + (" · " if a.get("task") else "") + (a.get("tool") or "")
        lay.addWidget(label(sub, "cardSub", wrap=True))
        row = QHBoxLayout()
        word = None
        if a.get("sensitive"):
            word = QLineEdit()
            word.setPlaceholderText(f"Type {a.get('confirm_word') or 'the word'} to approve")
            row.addWidget(word, 1)
        else:
            row.addStretch(1)
        yes, no = button("Approve", "primary"), button("Deny", "ghost")
        yes.clicked.connect(lambda: self._answer(a["id"], True, word.text() if word else ""))
        no.clicked.connect(lambda: self._answer(a["id"], False))
        row.addWidget(yes)
        row.addWidget(no)
        lay.addLayout(row)
        return box

    def _answer(self, request_id: str, yes: bool, word: str = "") -> None:
        self._call("answer_approval", False, request_id, yes, word)
        self._signatures.pop("approvals", None)
        self._fill()

    def _running(self, j: dict) -> QWidget:
        status_words = _status_words()
        box = QFrame()
        box.setObjectName("card")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(5)
        top = QHBoxLayout()
        top.addWidget(label(_clip(j.get("title", "Job"), 70), "rowTitle", wrap=True), 1)
        paused = j.get("status") == "paused"
        flip = button("Resume" if paused else "Pause", "ghost")
        flip.clicked.connect(lambda: self._job_call("resume_job" if paused else "pause_job", j["id"]))
        stop = button("Stop", "ghost")
        stop.clicked.connect(lambda: self._job_call("stop_job", j["id"]))
        top.addWidget(flip)
        top.addWidget(stop)
        lay.addLayout(top)
        step = f"Step {j['step']} of {j['total']}" if j.get("total") else status_words(j.get("status", ""))[0]
        doing = j.get("doing") or ""
        lay.addWidget(label(step + (f" · {_clip(doing, 80)}" if doing else ""), "cardSub", wrap=True))
        meter = Meter(float(j.get("progress") or 0.0), height=4)
        self._meters[j.get("id")] = meter
        lay.addWidget(meter)
        return box

    def _job_call(self, name: str, task_id: str) -> None:
        self._call(name, False, task_id)
        self._signatures.pop("running", None)
        self._fill()

    def _ended(self, t: dict) -> QWidget:
        status_words = _status_words()
        row = _Clickable(lambda i=t.get("id"): self.navigate.emit(f"app/agents/job/{i}"))
        row.setObjectName("card")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.addWidget(label(_clip(t.get("title", "Job"), 60), "rowTitle"), 1)
        words = status_words(t.get("status", ""))[0]
        when = float(t.get("when") or 0.0)
        lay.addWidget(label(words + (f" · {_ago(when).lower()}" if when > 0 else ""), "cardSub"))
        return row

    def _give(self) -> None:
        text = self.job_box.text().strip()
        if not text:
            return
        kind, said = self._call("give_job", ("error", "The agents did not start."), text)
        if kind == "started":
            self.job_box.clear()
            self._say(f"Started: {said}.")
        elif kind == "chat":
            self._say("That reads as a question. Ask it in Chat and Tora answers it.")
        else:
            self._say(f"Couldn't start it: {said}")
        self._fill()

    def _say(self, text: str) -> None:
        self.note.setText(text)
        self.note.show()


class _Clickable(QFrame):
    """A row that opens something when clicked: a job's details, for now
    the app window's (they hold the plan, the log and its folder)."""

    def __init__(self, on_click):
        super().__init__()
        self._on_click = on_click
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._on_click()
