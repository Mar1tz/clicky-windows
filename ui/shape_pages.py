"""The shape's own pages (DESIGN.md §3), sized for it: 520 wide, not the app
window's 1040 column. Home, Chat and Insights are its tabs.

Home: a greeting and one line of what is happening, then ONE card - the
newest thing Tora finished (an agent job or the last answer) - and, with
nothing yet, the shortcut and something to try. Live: rebuilt when it is
shown and when what it shows changes (TOPICS), as the app window's pages.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLineEdit, QScrollArea, QVBoxLayout, QWidget

from ui.app_widgets import Meter, button, label
from ui.insights_data import minutes
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
    TOPICS = frozenset({"state", "agents", "journal", "guide"})

    def build(self) -> None:
        hello = label(greeting(self._call("user_name", ""), datetime.now().hour), "upTitle")
        hello.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.lay.addWidget(hello)
        status = label(status_line(self._call("state", "idle"), len(self._call("agents", []))), "upSub")
        status.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.lay.addWidget(status)
        self.lay.addSpacing(18)
        result = newest_result(self._call("tasks", [], 8), self._call("recent", [], 1))
        self.lay.addWidget(self._result_card(result) if result else self._empty_card())
        self.lay.addStretch(1)

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
        QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))

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


# ── Insights ──────────────────────────────────────────────────────────────────

def _count(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def tiles(data: dict) -> list[tuple[str, str, str]]:
    """The four numbers, as (label, value, line under it) - only what was
    measured (insights_data), never an estimate."""
    time_ = data.get("time") or {}
    done = data.get("handled") or {}
    knows = data.get("knows") or {}
    streak, longest = int(data.get("streak") or 0), int(data.get("longest") or 0)
    return [
        ("TIME TORA WORKED", minutes(time_.get("total_min", 0)),
         f"{minutes(time_.get('this_week_min', 0))} this week"),
        ("THINGS HANDLED", str(done.get("total", 0)),
         f"{done.get('ask', 0)} asked · {done.get('show', 0)} shown · {done.get('do', 0)} done"),
        ("WHAT SHE KNOWS", str(knows.get("facts", 0)),
         "facts · " + _count(int(knows.get("documents", 0)), "document")),
        ("STREAK", _count(streak, "day"), f"longest {longest}"),
    ]


class InsightsTab(ShapePage):
    """The Insights page's numbers, two by two, and this month on the plan.
    The full page (charts, apps, the calendar) is a link away."""

    TOPICS = frozenset({"journal", "agents", "account"})
    MARGINS = (0, 0, 0, 0)

    def build(self) -> None:
        data = self._call("insights", {})
        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(20, 4, 20, 12)
        col.setSpacing(10)
        grid = QGridLayout()
        grid.setSpacing(10)
        for i, (name, value, under) in enumerate(tiles(data)):
            tile = QFrame()
            tile.setObjectName("card")
            lay = QVBoxLayout(tile)
            lay.setContentsMargins(14, 12, 14, 12)
            lay.setSpacing(2)
            lay.addWidget(label(name, "sectionTitle"))
            lay.addWidget(label(value, "upTitle"))
            lay.addWidget(label(under, "cardSub", wrap=True))
            grid.addWidget(tile, i // 2, i % 2)
        col.addLayout(grid)
        plan = data.get("plan") or []
        if plan:
            col.addSpacing(4)
            col.addWidget(label("THIS MONTH", "sectionTitle"))
            for what, used, limit in plan:
                row = QHBoxLayout()
                row.addWidget(label(what, "cardSub"))
                row.addStretch(1)
                row.addWidget(label(f"{used} / {limit}" if limit else f"{used} · no limit", "cardSub"))
                col.addLayout(row)
                col.addWidget(Meter(used / limit if limit else 0.0, height=4))
        more = button("All of Insights", "ghost")
        more.clicked.connect(lambda: self.navigate.emit("app/insights"))
        row = QHBoxLayout()
        row.addWidget(more)
        row.addStretch(1)
        col.addLayout(row)
        col.addStretch(1)
        self.lay.addWidget(_scroller(inner), 1)
