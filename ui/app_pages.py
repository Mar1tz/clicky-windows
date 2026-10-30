"""The whole pages the shape opens at the Settings size, and the pieces the
Settings tabs share.

    Insights     what she measured (the shape's "more")
    Journal      everything asked
    Tora         her voice and look, how she works (Settings > Tora)

They began as the old app window's pages; that window went in revamp step
6b, and these are what the shape (ui/shape.py PAGES_WIDE, ui/shape_settings.py)
still shows. Every page reads the running app through ui/app_bridge.py and
changes it down the tray's own path, so the menu and the pages cannot
disagree. Where a worker already built the real thing - the endpoint
editor, say - it is embedded, not drawn again.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from ui import app_theme as T
from ui.app_widgets import (
    Card, Meter, PageBody, Pill, Row, ToggleRow, button, label, later, spacer, title,
)

_log = logging.getLogger("tora.app")


class Page(QWidget):
    """A title, optional buttons beside it, then a scrolling body. No line
    of explanation under the title: the page says what it is by what is on
    it (DESIGN.md §3B).

    A LIVE page (APP-10) reads everything it shows from the bridge and is
    rebuilt each time it is shown or something it shows changes: its
    header buttons go in build_actions (once), its content in build (every
    refresh). A page that embeds something with its own state overrides
    refresh to redraw only around it.
    """

    # Another page asked for by this one ("Open dashboard" -> accounting).
    navigate = pyqtSignal(str)

    HEADING = "Page"
    LIVE = False
    # The bridge topics that redraw this page WHILE it is on screen. Empty:
    # it refreshes only when opened - right for a page with something to
    # type into, which a redraw would wipe mid-sentence.
    TOPICS: frozenset = frozenset()

    def __init__(self, bridge=None):
        super().__init__()
        if bridge is None:
            from ui.app_bridge import AppBridge
            bridge = AppBridge()
        self.bridge = bridge
        # A QWidget subclass paints no stylesheet background
        # without this, and a page sits directly on the translucent window.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Its background is `QWidget#page` in app_theme.qss(), so it follows the theme.
        self.setObjectName("page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # The title is inside the scrolling column: it lines up with the
        # content under it and scrolls away with it, as Wispr's do.
        self.body = PageBody()
        outer.addWidget(self.body, 1)
        head = QWidget()
        head_lay = QHBoxLayout(head)
        head_lay.setContentsMargins(0, 34, 0, 10)
        head_lay.setSpacing(16)
        head_lay.addWidget(self.build_title(), 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        head_lay.addLayout(self.actions)
        self.body.top.addWidget(head)
        self.build_actions()
        self.build()
        self.body.finish()

    def build_title(self) -> QWidget:
        """The page's title - built once. Home greets instead."""
        return title(self.HEADING)

    def build_actions(self) -> None:
        """The header buttons - built once. Live pages override this."""

    def build(self) -> None:          # pragma: no cover - overridden
        pass

    def refresh(self) -> None:
        """Rebuild the body from what is true now (live pages only), staying
        where the page was scrolled to - a page that redraws while someone
        reads it must not jump back to the top."""
        if not self.LIVE:
            return
        bar = self.body.verticalScrollBar()
        was = bar.value()
        self.body.clear()
        self.build()
        self.body.finish()
        if was:
            later(self, lambda: bar.setValue(min(was, bar.maximum())))

    def action(self, text: str, kind: str = "ghost"):
        b = button(text, kind)
        self.actions.addWidget(b)
        return b


def _fill(card: Card) -> None:
    """Let a card's body take its spare height (so what is in it can sit in
    the middle) instead of the stretch Card keeps under it."""
    outer = card.layout()
    last = outer.itemAt(outer.count() - 1)
    if last is not None and last.spacerItem() is not None:
        outer.takeAt(outer.count() - 1)
    outer.setStretchFactor(card.body, 1)


def _ask(bridge, name: str, default, *args):
    """A bridge reading that a plain or older bridge may not have."""
    fn = getattr(bridge, name, None)
    if fn is None:
        return default
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001 - a page shows what it can
        return default


class _CursorRow(ToggleRow):
    """Show her cursor - or only her bar. Follows the shortcut while the
    page is open: Win + Alt + H flips it from anywhere (main.py)."""

    def __init__(self, bridge):
        keys = _ask(bridge, "cursor_hotkey", "Win + Alt + H")
        super().__init__("Show her cursor",
                         f"Off: only her bar shows - she still flies to what she points at. "
                         f"{keys} switches it any time.",
                         bool(_ask(bridge, "cursor_shown", True)), first=True)
        self._bridge = bridge
        self.switch.toggled.connect(lambda on: _ask(bridge, "set_cursor_shown", None, on))
        changed = getattr(bridge, "changed", None)
        if changed is not None:
            changed.connect(self._follow)          # disconnected when the row goes (a QObject)

    def _follow(self, topic: str) -> None:
        if topic == "cursor":
            self.switch.setChecked(bool(_ask(self._bridge, "cursor_shown", True)))


# ── Insights ──────────────────────────────────────────────────────────────────

class InsightsPage(Page):
    """What Tora has done for you, counted - never estimated (DESIGN.md §3C,
    ui/insights_data.py). Wispr's Insights layout: three tiles, then where
    you use it and the streak calendar, then the month's plan and agents."""

    HEADING = "Insights"
    LIVE = True
    TOPICS = frozenset({"journal", "account", "agents"})
    # In the shape (820 px, not the old window's 1040): the time tile on its
    # own, the other two beside each other, and the pairs below stacked.
    NARROW = False

    def _row(self):
        lay = QVBoxLayout() if self.NARROW else QHBoxLayout()
        lay.setSpacing(T.GAP)
        return lay

    def showEvent(self, event) -> None:
        """Opened: the charts grow in, one a beat after another (Wispr's
        page does this). A live redraw while it is open does not replay."""
        super().showEvent(event)
        from ui.app_parts import Grows
        for i, chart in enumerate(self.findChildren(Grows)):
            chart.play(80 + 45 * i)

    def build(self) -> None:
        from ui import insights_data as data
        from ui.app_parts import GrowMeter, InfoCard, StreakGrid, legend
        d = _ask(self.bridge, "insights", {}) or {}
        if not d:
            d = data.summarise([])
            d.update(agents=data.agents_month([]), plan=[], plan_summary=None, due=0)
        d.setdefault("time", data.time_worked([], []))
        d.setdefault("handled", data.handled(d, []))
        d.setdefault("knows", {"facts": 0, "documents": 0, "recent": []})

        # ── three tiles: time worked (the lead), things handled, what she knows
        if self.NARROW:
            self.body.add(self._time_tile(d["time"]))
            tiles = QHBoxLayout()
            tiles.setSpacing(T.GAP)
        else:
            tiles = QHBoxLayout()
            tiles.setSpacing(T.GAP)
            tiles.addWidget(self._time_tile(d["time"]), 3)
        tiles.addWidget(self._handled_tile(d["handled"]), 2)
        tiles.addWidget(self._knows_tile(d["knows"]), 2)
        self.body.add_layout(tiles)

        # ── where you use Tora · the streak
        mid = self._row()
        apps = InfoCard("Where you use Tora", f"apps · {d['apps_used']}", big=True)
        if d["apps"]:
            for rank, (name, n, share) in enumerate(d["apps"]):
                from ui.app_parts import ShareBar
                row = QHBoxLayout()
                row.setSpacing(12)
                who = label("", "tileLine")
                who.setFixedWidth(110)
                who.setText(who.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, 108))
                who.setToolTip(name)
                row.addWidget(who)
                row.addWidget(ShareBar(share, rank), 1)
                count = label(f"{n} question{'s' if n != 1 else ''}".upper(), "infoNote")
                count.setFixedWidth(110)
                row.addWidget(count)
                apps.body.addLayout(row)
        else:
            apps.body.addWidget(label("Ask about something in another app - Excel, a PDF, a "
                                      "website - and it shows up here.", "infoFaint", wrap=True))
        mid.addWidget(apps, 1)
        streak = InfoCard(f"{d['streak']} day streak", f"longest · {d['longest']} · "
                          f"active days · {d['active_days']}", big=True)
        streak.body.addWidget(StreakGrid(data.calendar(d["days"])), 0, Qt.AlignmentFlag.AlignLeft)
        streak.body.addWidget(legend())
        mid.addWidget(streak, 1)
        self.body.add_layout(mid)

        # ── the plan · agents
        low = self._row()
        low.addWidget(self._plan_card(d), 1)
        low.addWidget(self._agents_card(d["agents"]), 1)
        self.body.add_layout(low)

    def _time_tile(self, t: dict) -> QWidget:
        """Time Tora worked for you: measured - the time she guided your
        walkthroughs and the time your agent jobs ran. Never "saved"."""
        from ui import insights_data as data
        from ui.app_parts import DayBars, stat_tile
        change = t["change"]
        week = f"{'+' if change > 0 else ''}{change}%" if change is not None else "–"
        return stat_tile(data.minutes(t["total_min"]), "time Tora worked for you",
                         [("This week", data.minutes(t["this_week_min"])), ("On last week", week),
                          ("Guiding you · agents running",
                           f"{data.minutes(t['guided_min'])} · {data.minutes(t['agents_min'])}")],
                         chart=DayBars(t["daily"]))

    def _handled_tile(self, h: dict) -> QWidget:
        """Everything she did, by kind: Ask (answered), Show (walked you
        through), Do (an agent finished)."""
        from ui.app_parts import InfoCard, SplitBar, swatch
        card = InfoCard()
        card.body.setSpacing(4)
        card.body.addWidget(label(str(h["total"]), "tileValue"))
        card.body.addWidget(label("THINGS TORA HANDLED", "infoNote"))
        card.body.addSpacing(12)
        card.body.addWidget(SplitBar([h["ask"], h["show"], h["do"]]))
        card.body.addSpacing(10)
        for (words, n), shade in zip((("Ask · answered", h["ask"]), ("Show · walked you through", h["show"]),
                                      ("Do · agent jobs finished", h["do"])), SplitBar.SHADES):
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(swatch(shade), 0, Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(label(words, "tileLine"))
            row.addStretch(1)
            row.addWidget(label(str(n), "tileLineValue"))
            card.body.addLayout(row)
        return card

    def _knows_tile(self, k: dict) -> QWidget:
        """What Tora knows about you: the facts her remember tool kept (and
        attached documents), with the two she learned most recently."""
        from ui.app_parts import InfoCard
        card = InfoCard()
        card.body.setSpacing(4)
        card.body.addWidget(label(str(k["facts"]), "tileValue"))
        card.body.addWidget(label("THINGS TORA KNOWS ABOUT YOU", "infoNote"))
        card.body.addSpacing(12)
        if k["recent"]:
            card.body.addWidget(label("LEARNED RECENTLY", "infoNote"))
            for text, when in k["recent"]:
                card.body.addWidget(label(f"“{_clip(text, 90)}” · {_ago(when)}", "tileLine", wrap=True))
        else:
            card.body.addWidget(label("Tell her something to keep - “remember I use metric” - "
                                      "and it shows here.", "infoFaint", wrap=True))
        if k["documents"]:
            card.body.addSpacing(4)
            card.body.addWidget(label(f"{k['documents']} document{'s' if k['documents'] != 1 else ''} "
                                      "attached", "infoFaint"))
        return card

    def _plan_card(self, d: dict) -> QWidget:
        from ui.app_parts import GrowMeter, InfoCard
        summary = d.get("plan_summary")
        if not d["plan"]:
            card = InfoCard("Monthly usage")
            card.body.addWidget(label("Your plan's use shows here when you are signed in to "
                                      "a HeyTora plan.", "infoFaint", wrap=True))
            return card
        from account.service import resets_on
        name = summary.get("plan_name") or (summary.get("plan") or "").capitalize() or "HeyTora"
        card = InfoCard("Monthly usage", f"{name} plan · resets {resets_on(summary)}")
        fair = summary.get("fair_use")
        credits = summary.get("credits") or {}
        if isinstance(fair, (int, float)) and isinstance(credits.get("month"), int):
            row = QHBoxLayout()
            row.addWidget(label("Credits", "tileValueSmall"))
            row.addStretch(1)
            left = f"{int(credits.get('month_left') or 0):,} of {credits['month']:,} left"
            if credits.get("bought_left"):
                left += f" · {int(credits['bought_left']):,} added"
            row.addWidget(label(left, "tileLineValue"))
            card.body.addLayout(row)
            card.body.addWidget(GrowMeter(min(1.0, float(fair)), T.WARN if fair >= 0.8 else T.ACCENT, 10))
            card.body.addSpacing(8)
        # Credits are the limit (0012); the rest is what was done, no allowances.
        for words, used, _limit in d["plan"]:
            row = QHBoxLayout()
            row.addWidget(label(words, "tileLine"))
            row.addStretch(1)
            row.addWidget(label(f"{used} this month", "tileLineValue"))
            card.body.addLayout(row)
            card.body.addSpacing(4)
        return card

    def _agents_card(self, a: dict) -> QWidget:
        from ui.app_parts import InfoCard
        card = InfoCard("Agents", "this month")
        nums = QHBoxLayout()
        nums.setSpacing(28)
        for value, words in ((a["done"], "finished"), (a["failed"], "didn't finish"),
                             (a["running"], "running")):
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(label(str(value), "tileValue"))
            col.addWidget(label(words.upper(), "infoNote"))
            nums.addLayout(col)
        nums.addStretch(1)
        card.body.addLayout(nums)
        if a.get("ran_min"):
            from ui import insights_data as data
            card.body.addWidget(label(f"Ran for {data.minutes(a['ran_min'])} this month", "tileLine"))
        if a["recent"]:
            card.body.addSpacing(6)
            for t in a["recent"]:
                card.body.addWidget(label(f"{t['title']} · {t['status'].replace('_', ' ')}",
                                          "infoFaint", wrap=True))
        else:
            card.body.addWidget(label("Give Tora a job - \u201cbuild me a landing page\u201d - "
                                      "and she works on it in the background.", "infoFaint", wrap=True))
        go = button("Open Agents", "link")
        go.clicked.connect(lambda: self.navigate.emit("agents"))
        card.body.addWidget(go, 0, Qt.AlignmentFlag.AlignLeft)
        return card


# ── shared by the pages below ─────────────────────────────────────────────────

def _tidy(embedded: QWidget, *titles: str) -> QWidget:
    """Fit a floating window's widget into a card: its Close would hide it
    and leave the card empty, and its own title repeats the card's.

    Its own stylesheet goes too. The agents' dialogs (agents/ui) were drawn
    for the old dark floating windows - white-on-charcoal fields that stayed
    dark in the light theme. Without it they take the app's sheet, which
    names their parts (setLabel, acctCard...) in app_theme.qss()."""
    embedded.setStyleSheet("")
    embedded.setObjectName("embedded")          # "panel" is the app window's own frame
    for b in embedded.findChildren(QPushButton):
        if b.text().strip().lower() == "close":
            b.hide()
    for lbl in embedded.findChildren(QLabel):
        if lbl.objectName() in ("title", "accTitle") or lbl.text().strip() in titles:
            lbl.hide()
    # A tab reads "&" as a keyboard mnemonic: "Debtors & creditors" drew as
    # "Debtors  creditors" - the nav rail's old bug, in someone else's tabs.
    for tabs in embedded.findChildren(QTabWidget):
        for i in range(tabs.count()):
            text = tabs.tabText(i)
            if "&" in text and "&&" not in text:
                tabs.setTabText(i, text.replace("&", "&&"))
    return embedded


def _clip(text: str, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _when(ts: float) -> str:
    """"Today, 18:55" · "Yesterday, 20:21" · "21 Sep, 14:25"."""
    d = datetime.fromtimestamp(ts)
    days = (date.today() - d.date()).days
    day = "Today" if days == 0 else "Yesterday" if days == 1 else f"{d.day} {d:%b}"
    return f"{day}, {d:%H:%M}"


def _ago(ts: float) -> str:
    days = (date.today() - datetime.fromtimestamp(ts).date()).days
    return "today" if days <= 0 else "yesterday" if days == 1 else f"{days} days ago"


def _coming(ts: float) -> str:
    """When a review comes back: "tomorrow" · "on Friday" · "on 3 Oct"."""
    d = datetime.fromtimestamp(ts).date()
    days = (d - date.today()).days
    if days <= 0:
        return "later today"
    if days == 1:
        return "tomorrow"
    return f"on {d:%A}" if days < 7 else f"on {d.day} {d:%b}"


# ── Agents ────────────────────────────────────────────────────────────────────

# A task's status, as its row says it, and the colour of its dot.
# ── Journal ───────────────────────────────────────────────────────────────────

class JournalPage(Page):
    HEADING = "Journal"
    LIVE = True
    TOPICS = frozenset({"journal", "recording", "guide", "switches"})
    SHOWN_DUE = 5

    def __init__(self, bridge=None):
        # The entries opened on the page stay open when it redraws - per
        # list: opening a review must not give its answer away under Recent.
        self._open: set[tuple[str, int]] = set()
        super().__init__(bridge)

    def build_actions(self) -> None:
        self.action("Open folder").clicked.connect(self.bridge.open_journal_folder)
        self.action("Attach document", "primary").clicked.connect(self.bridge.attach_document)

    def build(self) -> None:
        br = self.bridge
        due, n_due = br.due(self.SHOWN_DUE), br.due_count()
        if n_due:
            sub = (f"{n_due} due now. Answer it in your head, then check - "
                   "what you knew comes back later, the rest tomorrow.")
        else:
            nxt = br.next_due()
            sub = (f"Nothing due. The next one comes back {_coming(nxt)}." if nxt else
                   "Nothing due. What you ask comes back here the next day, to check it stuck.")
        card = Card("Due for review", sub)
        for i, entry in enumerate(due):
            card.add(self._entry(entry, first=(i == 0), review=True))
        if n_due > len(due):
            card.add(Row(f"…and {n_due - len(due)} more", "They come up here as you go."))
        self.body.add(card)

        recent = br.recent(8)
        card = Card("Recent", "Newest first." if br.switch("journal") else
                    "Saving is off - new questions are not being kept.")
        for i, entry in enumerate(recent):
            card.add(self._entry(entry, first=(i == 0)))
        if not recent:
            card.add(Row("Nothing yet", "Ask Tora anything; it is kept here.", first=True))
        self.body.add(card)

        keep = Card()
        row = ToggleRow("Keep a journal",
                        "Save every question and answer, so they come back for "
                        "review. Kept on this PC.", br.switch("journal"), first=True)
        row.switch.toggled.connect(lambda on: br.set_switch("journal", on))
        keep.add(row)
        self.body.add(keep)

    def _entry(self, e: dict, *, first: bool, review: bool = False) -> QWidget:
        """One entry: the question and when - and, opened, the answer."""
        from tutor_features.journal import WALKTHROUGH_TAG
        entry_id = int(e["id"])
        key = ("due" if review else "recent", entry_id)
        walk = WALKTHROUGH_TAG in (e.get("tags") or "")
        answer = (e.get("answer") or "").strip()
        opened = key in self._open
        if review:
            sub = f"{'walked through' if walk else 'asked'} {_ago(e['created_at'])}"
            streak = int(e.get("streak") or 0)
            if streak:
                sub += " · remembered " + {1: "once", 2: "twice"}.get(streak, f"{streak} times")
        else:
            sub = _when(e["created_at"])
            if walk:
                sub += f" · walkthrough · {len(answer.splitlines())} steps"
        flip = button("Hide" if opened else ("Show answer" if review else "Show"))
        flip.clicked.connect(lambda: self._flip(key))
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(Row(_clip(e.get("question"), 200), sub, trailing=flip,
                          first=first, wrap=True))
        if opened:
            well = QFrame()
            well.setObjectName("well")
            inner = QVBoxLayout(well)
            inner.setContentsMargins(14, 12, 14, 12)
            inner.setSpacing(10)
            text = answer if len(answer) <= 1200 else answer[:1199].rstrip() + "…"
            inner.addWidget(label(text, wrap=True))
            if review:
                grade = QHBoxLayout()
                grade.setSpacing(8)
                grade.addStretch(1)
                again = button("Not yet")
                again.clicked.connect(lambda: self._grade(entry_id, False))
                knew = button("I knew it", "primary")
                knew.clicked.connect(lambda: self._grade(entry_id, True))
                grade.addWidget(again)
                grade.addWidget(knew)
                inner.addLayout(grade)
            lay.addWidget(well)
            lay.addSpacing(10)
        return box

    def _flip(self, key: tuple[str, int]) -> None:
        self._open.symmetric_difference_update({key})
        QTimer.singleShot(0, self.refresh)

    def _grade(self, entry_id: int, knew: bool) -> None:
        self._open.discard(("due", entry_id))
        self.bridge.review(entry_id, knew)
        QTimer.singleShot(0, self.refresh)

# ── Tora ──────────────────────────────────────────────────────────────────────

class ToraPage(Page):
    """Make her yours (DESIGN.md §3E): her voice, her colour, her sound, her
    modes, what she always keeps in mind, and the ways to teach her how you
    work. The technical half - models, what she may look at - is Settings."""

    HEADING = "Tora"
    LIVE = True
    TOPICS = frozenset({"recording", "skills"})
    VOICE_WAIT_MS = 700          # carousel settles before the live session restarts

    # Her modes: switch, icon, what it is, what it does.
    MODES = (("slow_mode", "turtle", "Slow mode",
              "A calmer pace on screen. For someone new to the machine."),
             ("auto_advance", "fast-forward", "Advance by itself",
              "Move on when she sees the step done, without waiting for “next”."),
             ("quiz_mode", "circle-help", "Quiz mode",
              "Ask a question back, so it sticks. Lasts until Tora restarts."),
             ("code_mode", "code", "Code mode",
              "Notice when you are in an editor and explain like a developer."))

    # The page's own tabs (Wispr's Style page): one long page was three.
    TABS = (("look", "Voice & look"), ("works", "How she works"), ("skills", "Skills"),
            ("teach", "Teach her"))

    def __init__(self, bridge=None):
        self._tab = self.TABS[0][0]
        super().__init__(bridge)

    def show_tab(self, key: str) -> None:
        if key in dict(self.TABS):
            self._tab = key
            self.refresh()

    def build(self) -> None:
        from ui.app_parts import TabBar
        bar = TabBar(list(self.TABS), self._tab)
        bar.picked.connect(lambda k: self._later(lambda: self.show_tab(k)))
        self.body.add(bar)
        self.body.column.addSpacing(6)
        if self._tab == "look":
            self.body.add(self._voice())
            looks = QHBoxLayout()
            looks.setSpacing(T.GAP)
            looks.addWidget(self._colour(), 1)
            looks.addWidget(self._sound(), 1)
            self.body.add_layout(looks)
        elif self._tab == "works":
            self.body.add(self._modes())
            self.body.add(self._instructions())
        elif self._tab == "skills":
            from ui.skills_page import skills_card
            self.body.add(skills_card(self.bridge, self.edit_skill))
        else:
            teach = QHBoxLayout()
            teach.setSpacing(T.GAP)
            teach.addWidget(self._recording(), 1)
            teach.addWidget(self._capture(), 1)
            self.body.add_layout(teach)

    def edit_skill(self, skill=None) -> None:
        """The skill editor over the window; a new skill when None, a draft
        to finish when it has no id (from a job - see JobDrawer)."""
        from ui.skills_page import open_editor
        self._editor = open_editor(self.window(), self.bridge, skill,
                                   lambda: self._later(self.refresh))

    def _later(self, fn) -> None:
        """After the click returns: the tab clicked is one the redraw replaces."""
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(fn)
        timer.timeout.connect(timer.deleteLater)
        timer.start(0)

    # ── her voice ────────────────────────────────────────────────────────────

    def _voice(self) -> Card:
        """The first launch's carousel: her name, the audio bars moving with
        the clip, arrows to the next. Settling on one saves it."""
        from audio.tts.gemini_tts import VOICES
        from ui.onboarding_parts import VoiceCarousel
        br = self.bridge
        card = Card("Her voice", "Hear each one, then stop on hers. The live session "
                                 "restarts to switch.")
        voices = list(VOICES)
        current = br.voice()
        if current and current not in voices:
            voices.insert(0, current)
        self.carousel = VoiceCarousel(voices, current)
        wait = QTimer(self)
        wait.setSingleShot(True)
        wait.setInterval(self.VOICE_WAIT_MS)
        wait.timeout.connect(lambda: br.set_voice(self.carousel.voice()))
        self.carousel.changed.connect(lambda _name: wait.start())
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.carousel)
        row.addStretch(1)
        card.body.addLayout(row)
        return card

    # ── her colour, her sound ────────────────────────────────────────────────

    def _colour(self) -> Card:
        from ui import tokens
        from ui.onboarding_parts import Swatch, ToraPreview
        card = Card("Her colour", "What her cursor wears beside yours.")
        now = _ask(self.bridge, "cursor_colour", "blue") or "blue"
        preview = ToraPreview()
        preview.setFixedWidth(130)
        preview.set_colour(tokens.cursor_hex(now))
        swatches = []

        def pick(key: str) -> None:
            for sw in swatches:
                sw.set_on(sw.key == key)
            preview.set_colour(tokens.cursor_hex(key))
            _ask(self.bridge, "set_cursor_colour", None, key)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(preview)
        for key, name, hex_colour in tokens.CURSOR_COLOURS:
            sw = Swatch(key, name, hex_colour)
            sw.set_on(key == now)
            sw.picked.connect(pick)
            swatches.append(sw)
            row.addWidget(sw)
        row.addStretch(1)
        card.body.addLayout(row)
        card.body.addWidget(_CursorRow(self.bridge))
        return card

    def _sound(self) -> Card:
        from ui import sounds
        from ui.app_parts import chips
        card = Card("Her sound", "The pop the moment she is listening. Pick one to hear it.")
        options = list(sounds.SOUNDS) + [(sounds.OFF, "Off")]
        now = _ask(self.bridge, "listen_sound", sounds.SOUNDS[0][0])
        card.body.addStretch(1)
        card.body.addWidget(chips(options, now, lambda k: _ask(self.bridge, "set_listen_sound", None, k)))
        card.body.addStretch(1)
        _fill(card)
        return card

    # ── her modes ────────────────────────────────────────────────────────────

    def _modes(self) -> Card:
        from PyQt6.QtWidgets import QGridLayout
        from ui.app_parts import ModeTile
        card = Card("Her modes", "How she teaches. Each takes effect from her next answer.")
        grid = QGridLayout()
        grid.setSpacing(12)
        for i, (name, icon_name, heading, sub) in enumerate(self.MODES):
            tile = ModeTile(icon_name, heading, sub, self.bridge.switch(name))
            tile.setObjectName("modeTile")
            tile.switch.setAccessibleName(heading)
            tile.toggled.connect(lambda on, n=name: self.bridge.set_switch(n, on))
            grid.addWidget(tile, i // 2, i % 2)
        card.body.addLayout(grid)
        return card

    # ── what she keeps in mind ───────────────────────────────────────────────

    def _instructions(self) -> Card:
        # The tray's "Instructions for Tora…" - standing orders she keeps in
        # mind on every answer. Saved through the tray's own signal.
        br = self.bridge
        rules = Card("Instructions for Tora",
                     "Kept in mind on every answer - “only help with Excel”, "
                     "“keep answers very short”.")
        from ui.app_parts import growing_text
        box = growing_text(br.instructions(), "Nothing yet - she helps with anything.")
        rules.body.addWidget(box)
        rules.body.addSpacing(12)
        foot = QHBoxLayout()
        foot.setSpacing(10)
        saved = label("", "faint")
        save = button("Save instructions", "primary")

        def _save() -> None:
            br.set_instructions(box.toPlainText())
            saved.setText("Saved - used from the next answer.")

        save.clicked.connect(_save)
        foot.addWidget(save)
        foot.addWidget(saved, 1)
        rules.body.addLayout(foot)
        return rules

    # ── teach her how you work ───────────────────────────────────────────────

    def _recording(self) -> Card:
        on = self.bridge.recording()
        from ui.app_parts import InfoButton
        about = InfoButton("Record a lesson", [
            "Records your screen as a video while you work with Tora - her pointer included.",
            "Everything you ask her, and her answers, is written into a transcript beside the video.",
            "Each lesson is saved in its own folder in Documents\\Tora Lessons, on this PC - "
            "watch it again, or send it to someone.",
            "Press Start recording, ask her as you normally would, then Stop recording.",
        ])
        card = Card("Record a lesson", action=about, sub=
                    "Records the screen and every question and answer into "
                    "Documents\\Tora Lessons - to watch again, or to send to someone.")
        foot = QHBoxLayout()
        foot.setSpacing(10)
        go = button("Stop recording", "danger") if on else button("Start recording", "primary")
        go.clicked.connect(lambda: self.bridge.set_recording(not on))
        foot.addWidget(go)
        if on:
            foot.addWidget(Pill("Recording", T.DANGER, dot=True))
        foot.addStretch(1)
        card.body.addLayout(foot)
        return card

    def _capture(self) -> Card:
        on = self.bridge.capturing()
        from ui.app_parts import InfoButton
        about = InfoButton("Show her what you did", [
            "Do a task yourself while Tora notes every click and key you press.",
            "When you stop, she gets the list of what you did - ask her “what did I just do?” "
            "or “how could I do this faster?”.",
            "It is handled like a document you attach: it goes to Tora with your next questions.",
            "It records typing too, so stop it before you type a password.",
        ])
        card = Card("Show her what you did", action=about, sub=
                    "Do a task yourself while Tora notes every click and key, then "
                    "ask “what did I just do?”. It records typing too - stop it "
                    "before a password.")
        foot = QHBoxLayout()
        foot.setSpacing(10)
        go = (button("Stop and send to Tora", "danger") if on else
              button("Capture my clicks", "primary"))
        go.clicked.connect(lambda: self._set_capturing(not on))
        foot.addWidget(go)
        if on:
            foot.addWidget(Pill("Capturing", T.WARN, dot=True))
        foot.addStretch(1)
        card.body.addLayout(foot)
        return card

    def _set_capturing(self, on: bool) -> None:
        self.bridge.set_capturing(on)
        QTimer.singleShot(0, self.refresh)




# ── shared with the Settings tabs ─────────────────────────────────────────────

def _checked_ago(when: float) -> str:
    """"just now", "5 min ago", "3 h ago" - when updates were last checked.
    (Not _ago: that is the Journal's "asked 3 days ago".)"""
    import time
    gone = max(0.0, time.time() - when)
    if gone < 90:
        return "just now"
    if gone < 3600:
        return f"{round(gone / 60)} min ago"
    return f"{round(gone / 3600)} h ago"


def confirm_sign_out(parent) -> bool:
    """HeyTora needs an account: signing out closes it and opens the sign-in
    window (ui/sign_in_gate.py) - so say that before doing it."""
    from ui.settings_parts import confirm
    return confirm(parent, "Are you sure you want to sign out?",
                   "HeyTora closes and opens the sign-in window. Your history and settings "
                   "stay on this PC.", "Sign out", "Back to account")

