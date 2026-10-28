"""The shape's own pages (DESIGN.md §3), sized for it: 520 wide, not the app
window's 1040 column.

Home: a greeting and one line of what is happening, then ONE card - the
newest thing Tora finished (an agent job or the last answer) - and, with
nothing yet, the shortcut and something to try. Live: rebuilt when it is
shown and when what it shows changes (TOPICS), as the app window's pages.
"""
from __future__ import annotations

import time
from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from ui.app_widgets import button, label
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


class HomeTab(QWidget):
    LIVE = True
    TOPICS = frozenset({"state", "agents", "journal", "guide"})
    navigate = pyqtSignal(str)

    def __init__(self, bridge):
        super().__init__()
        self.bridge = bridge
        self.setObjectName("page")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(24, 8, 24, 16)
        self.lay.setSpacing(0)
        self.refresh()

    def _call(self, name: str, default, *args):
        try:
            return getattr(self.bridge, name)(*args)
        except Exception:  # noqa: BLE001 - a page never falls over on what it reads
            return default

    def refresh(self) -> None:
        while self.lay.count():
            item = self.lay.takeAt(0)
            w = item.widget()
            if w is not None:
                # Off the page now, not when the event loop gets to it: a
                # redraw before then showed the old greeting under the new.
                w.hide()
                w.setParent(None)
                w.deleteLater()
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
        lay.addWidget(label(kind + " · " + _ago(r["when"]), "sectionTitle"))
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
