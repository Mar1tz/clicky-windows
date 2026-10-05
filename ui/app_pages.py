"""The nine pages of the app window.

Each one is the single-window home of something that was its own floating
window or a branch of the tray menu:

    Home         what is happening now, and what to try
    Agents       agents/ui/agents_panel · plan_view · timeline, embedded
    Extensions   the pack switches (agents/extensions)
    Accounting   extensions/accounting/ui/dashboard, embedded
    Journal      the Journal / Lesson Recording / Workflow Capture menus
    Tutor        the Tutor Mode menu
    Voice        the Conversation / Voice / Models menus
    Settings     agents/ui/settings · the mic, hotkey and privacy items
    Setup        ui/api_keys_dialog · the setup wizard · diagnostics

Every page reads the running app through ui/app_bridge.py and changes it
down the tray's own path, so the menu and the window cannot disagree
(APP-10 to APP-19). Where a worker already built the real thing - the
agents panel, the endpoint editor, a pack's dashboard - it is embedded, not
drawn again. The floating windows still open from the tray.
"""

from __future__ import annotations

from typing import Optional

import logging
from datetime import date, datetime

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QStackedWidget,
    QTabWidget, QVBoxLayout, QWidget,
)

from ui import app_theme as T
from ui.app_widgets import (
    Card, Dot, Meter, PageBody, Pill, Row, Select, Switch, ToggleRow,
    button, columns, hrule, label, refill, section, spacer, title,
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
        # See ToraApp: a QWidget subclass paints no stylesheet background
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
            QTimer.singleShot(0, lambda: bar.setValue(min(was, bar.maximum())))

    def action(self, text: str, kind: str = "ghost"):
        b = button(text, kind)
        self.actions.addWidget(b)
        return b


# ── Home ──────────────────────────────────────────────────────────────────────

class HomePage(Page):
    """Wispr Flow's Dictation page, for Tora: a greeting with the shortcut,
    a hero, the numbers that are true, then everything you asked - by day,
    newest first, each with her answer (DESIGN.md §3C)."""

    HEADING = "Home"
    LIVE = True
    TOPICS = frozenset({"state", "account", "agents", "guide", "switches", "journal"})
    PAGE = 100            # history rows loaded at a time; more as the end comes up

    def __init__(self, bridge=None):
        self._open: set[int] = set()        # history rows showing their whole answer
        self._revealed: Optional[int] = None  # the review card showing its answer
        self._limit = self.PAGE
        self._more = False                  # the last read filled the page: there may be more
        self._query = ""
        self._parts = None                  # the places build() fills, made once
        super().__init__(bridge)

    def build_title(self) -> QWidget:
        """"Hey Dylan, just hold [Ctrl + Win] and ask" - the one thing to
        know, with the shortcut on a keycap (Wispr Flow's greeting)."""
        name = _ask(self.bridge, "user_name", "")
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(title(f"Hey {name or 'there'}, just hold"))
        lay.addWidget(label(self.bridge.hotkey() or "Ctrl + Win", "keycap"))
        lay.addWidget(title("and ask"))
        lay.addStretch(1)
        return row

    def showEvent(self, event) -> None:
        """On screen: the plan is read again (at most once a minute); every
        question asked meanwhile moves it without a read."""
        super().showEvent(event)
        refresh = getattr(self.bridge, "refresh_account", None)
        if refresh is not None:
            refresh()

    # ── layout ───────────────────────────────────────────────────────────────
    #
    # Two columns, as Wispr's Home: on the left Up next and everything asked;
    # on the right the numbers, sticky while the history scrolls under them.
    # The page is built once and its parts refilled, so the search box keeps
    # its text and focus while what it finds redraws.

    def build(self) -> None:
        if self._parts is None:
            from ui.app_parts import SearchBox
            parts = {}
            for name in ("banner", "up", "history"):
                host = QWidget()
                lay = QVBoxLayout(host)
                lay.setContentsMargins(0, 0, 0, 0)
                lay.setSpacing(T.GAP)
                self.body.add(host)
                parts[name] = lay
                if name == "up":
                    head = QWidget()
                    hl = QHBoxLayout(head)
                    hl.setContentsMargins(2, 12, 0, 0)
                    self._first_day = label("TODAY", "sectionTitle")
                    hl.addWidget(self._first_day, 0, Qt.AlignmentFlag.AlignBottom)
                    hl.addStretch(1)
                    self._search = SearchBox("Search everything you asked")
                    self._search.changed.connect(self._find)
                    hl.addWidget(self._search, 0, Qt.AlignmentFlag.AlignBottom)
                    self.body.add(head)
            self._parts = parts
            self.body.verticalScrollBar().valueChanged.connect(self._near_end)
        self._fill()

    def refresh(self) -> None:
        bar = self.body.verticalScrollBar()
        was = bar.value()
        self._fill()
        if was:
            QTimer.singleShot(0, lambda: bar.setValue(min(was, bar.maximum())))

    def _fill(self) -> None:
        from ui.app_parts import SideCard
        br = self.bridge
        banner = self._banner()
        refill(self._parts["banner"], [banner] if banner is not None else [])
        # Empty, it must take no room: the first card lines up with the aside.
        self._parts["banner"].parentWidget().setVisible(banner is not None)
        refill(self._parts["up"], [self._up_next()])
        stats = _ask(br, "stats", {}) or {}
        self.body.set_aside(SideCard([
            (str(stats.get("questions", 0)), "questions asked"),
            (str(stats.get("walkthroughs", 0)), "walkthroughs"),
            (str(stats.get("streak", 0)), "day streak"),
        ], self._side_foot()), SideCard.W)
        self._fill_history()

    # ── pieces ───────────────────────────────────────────────────────────────

    def _banner(self) -> Optional[QWidget]:
        """Only when she cannot answer: everything fine says nothing."""
        br = self.bridge
        problem = br.problem()
        if problem == "billing":
            words = "Out of Gemini credit. Tora can't answer until it's topped up."
        elif problem == "key":
            words = "API key refused. Add a working key in Help > Keys and diagnostics."
        elif getattr(br, "manager", None) is not None and not br.live():
            words = "Not connected. Tora can't hear you right now - she keeps trying."
        else:
            return None
        box = QFrame()
        box.setObjectName("banner")
        lay = QHBoxLayout(box)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(10)
        lay.addWidget(Dot(T.WARN, 8), 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(label(words, "bannerText", wrap=True), 1)
        return box

    def _side_foot(self) -> QWidget:
        """Under the numbers: this month's plan, or what is due for review."""
        foot = QWidget()
        lay = QVBoxLayout(foot)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        plan = self._plan()
        if plan is not None:
            name, used, resets = plan
            lay.addWidget(label(f"Your {name} plan", "footTitle"))
            lay.addWidget(label(f"{round(used * 100)}% of this month used", "faint"))
            lay.addSpacing(2)
            lay.addWidget(Meter(used, T.WARN if used >= 0.8 else T.ACCENT))
            lay.addSpacing(2)
            lay.addWidget(label(f"Starts again on {resets}", "faint"))
            return foot
        due = _ask(self.bridge, "due_count", 0) or 0
        lay.addWidget(label("Due for review", "footTitle"))
        lay.addWidget(label(f"{due} thing{'s' if due != 1 else ''} she taught you"
                            if due else "Nothing due. What you ask comes back tomorrow.",
                            "faint", wrap=True))
        if due:
            go = button("Review now", "link")
            go.clicked.connect(lambda: self.navigate.emit("journal"))
            lay.addWidget(go, 0, Qt.AlignmentFlag.AlignLeft)
        return foot

    def _plan(self):
        """(plan name, share of the month used, when it resets) - or None
        (own key, no accounts, signed out, not read yet)."""
        try:
            st = self.bridge.account_state()
        except Exception:  # noqa: BLE001 - a bridge without accounts: no plan
            return None
        summary = st.get("summary")
        if not (st.get("configured") and st.get("signed_in") and summary and st.get("mode") == "account"):
            return None
        from account.service import resets_on
        fair = summary.get("fair_use")
        used = float(fair) if isinstance(fair, (int, float)) else 0.0
        if (summary.get("blocked") or {}).get("all") == "budget":
            used = 1.0
        name = summary.get("plan_name") or (summary.get("plan") or "").capitalize() or "HeyTora"
        return name, max(0.0, min(1.0, used)), resets_on(summary)

    # ── Up next: the one useful thing, in the space a hero would take ────────

    def _up_next(self) -> QWidget:
        """What is worth doing now, in order: watch what is running; answer
        what is due for review; or try one of three things she is good at
        for the work they said they do (DESIGN.md §3C)."""
        br = self.bridge
        walk, agents = br.walkthrough(), br.agents()
        if walk is not None or agents:
            return self._running(walk, agents)
        due = _ask(br, "due", [], 1) or []
        if due:
            return self._review(due[0], _ask(br, "due_count", 1) or 1)
        return self._try_asking()

    def _card(self, eyebrow: str, dot: str = "") -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("upNext")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(30, 26, 30, 26)
        lay.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        if dot:
            head.addWidget(Dot(dot, 7), 0, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(label(eyebrow.upper(), "sectionTitle"))
        head.addStretch(1)
        lay.addLayout(head)
        lay.addSpacing(6)
        return card, lay

    def _running(self, walk, agents) -> QWidget:
        card, lay = self._card("Happening now", T.ACCENT)
        rows = []
        if walk is not None:
            total = walk["total"] or 0
            where = f"Step {walk['step']} of {total}" if total else f"Step {walk['step']}"
            rows.append((f"Walkthrough · {walk['goal'] or 'guided steps'}",
                         f"{where} · {walk['instruction']}" if walk["instruction"] else where,
                         (walk["step"] / total) if total else 0.0))
        for a in agents:
            doing = a["doing"] or (f"Step {a['step']} of {a['total']}" if a["total"] else "Working")
            rows.append((a["title"], doing, float(a["progress"] or 0.0)))
        head, sub, frac = rows[0]
        lay.addWidget(label(head, "upTitle", wrap=True))
        lay.addWidget(label(sub, "upSub", wrap=True))
        lay.addSpacing(10)
        bar = QHBoxLayout()
        bar.setSpacing(12)
        bar.addWidget(Meter(frac, T.ACCENT), 1)
        bar.addWidget(label(f"{round(frac * 100)}%", "upPct"))
        lay.addLayout(bar)
        for title_, doing, frac_ in rows[1:3]:
            lay.addWidget(label(f"{title_} · {doing} · {round(frac_ * 100)}%", "faint", wrap=True))
        lay.addStretch(1)
        if agents:
            foot = QHBoxLayout()
            go = button("Open Agents")
            go.clicked.connect(lambda: self.navigate.emit("agents"))
            foot.addWidget(go)
            foot.addStretch(1)
            lay.addLayout(foot)
        return card

    def _review(self, entry: dict, count: int) -> QWidget:
        """One thing she taught them, back for a check: the question; the
        answer when asked for; did you know it."""
        entry_id = int(entry["id"])
        card, lay = self._card(f"Up next · review{'' if count < 2 else f' 1 of {count}'}", T.WARN)
        lay.addWidget(label(_clip(entry.get("question"), 160), "upTitle", wrap=True))
        shown = self._revealed == entry_id
        if shown:
            lay.addWidget(label(_clip(entry.get("answer"), 320), "upSub", wrap=True))
        else:
            lay.addWidget(label(f"You asked this {_ago(entry['created_at'])}. "
                                "Answer it in your head, then check.", "upSub", wrap=True))
        lay.addStretch(1)
        foot = QHBoxLayout()
        foot.setSpacing(8)
        if shown:
            knew = button("I knew it", "primary")
            knew.clicked.connect(lambda: self._grade(entry_id, True))
            again = button("Not yet")
            again.clicked.connect(lambda: self._grade(entry_id, False))
            foot.addWidget(knew)
            foot.addWidget(again)
        else:
            reveal = button("Show answer", "primary")
            reveal.clicked.connect(lambda: self._reveal(entry_id))
            foot.addWidget(reveal)
        foot.addStretch(1)
        lay.addLayout(foot)
        return card

    def _reveal(self, entry_id: int) -> None:
        self._revealed = entry_id
        self._later(self.refresh)

    def _grade(self, entry_id: int, knew: bool) -> None:
        self._revealed = None
        self.bridge.review(entry_id, knew)
        self._later(self.refresh)

    def _try_asking(self) -> QWidget:
        """Three things to say, for the work they said they do."""
        card, lay = self._card("Try asking")
        for line in try_asking(_ask(self.bridge, "uses", []) or []):
            row = QHBoxLayout()
            row.setSpacing(12)
            glyph = QLabel()
            from ui.app_widgets import icon
            glyph.setPixmap(icon("mic", T.ACCENT, 16))
            row.addWidget(glyph, 0, Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(label(f"“{line}”", "upPrompt", wrap=True), 1)
            lay.addLayout(row)
            lay.addSpacing(4)
        lay.addStretch(1)
        lay.addWidget(label(f"Hold {self.bridge.hotkey() or 'Ctrl + Win'} and say it, "
                            "with the thing it's about on your screen.", "faint", wrap=True))
        return card

    def _fill_history(self) -> None:
        """Everything asked, by day, newest first - all of it, a page at a
        time - or, while searching, everything that mentions the words."""
        from ui.app_parts import ListBox, day_name, empty, history_row, section_head
        br = self.bridge
        if self._query:
            entries = _ask(br, "search", [], self._query, 200) or []
            self._more = False
        else:
            entries = _ask(br, "recent", [], self._limit) or []
            self._more = len(entries) >= self._limit
        widgets: list[QWidget] = []
        if not entries:
            self._first_day.setText("NO MATCHES" if self._query else "TODAY")
            widgets.append(empty(
                f"Nothing asked mentions “{self._query}”" if self._query else "Nothing asked yet",
                "Try fewer words, or a word from her answer." if self._query else
                f"Hold {br.hotkey() or 'Ctrl + Win'} and ask about anything on your screen. "
                "Every question and her answer land here.", "search" if self._query else "journal"))
        box, day = None, ""
        for e in entries:
            name = day_name(e["created_at"])
            if name != day:
                day, box = name, ListBox()
                if widgets:
                    widgets.append(section_head(name))
                else:                        # the first day's name is on the search row
                    self._first_day.setText(name.upper())
                widgets.append(box)
            entry_id = int(e["id"])
            box.add(history_row(e, entry_id in self._open,
                                lambda i=entry_id: self._flip(i)))
        refill(self._parts["history"], widgets)
        self.body.place_aside()

    def _find(self, text: str) -> None:
        self._query = text
        self._fill_history()

    def _near_end(self, value: int) -> None:
        """Scrolled near the bottom with more to show: the next page."""
        bar = self.body.verticalScrollBar()
        if self._more and value >= bar.maximum() - 600:
            self._more = False
            self._limit += self.PAGE
            self._fill_history()

    def _flip(self, entry_id: int) -> None:
        # After the click returns: the row being clicked is one the redraw replaces.
        self._open ^= {entry_id}
        self._later(self._fill_history)

    def _later(self, fn) -> None:
        """Run `fn` once the current event is done - on a timer this page
        owns, so nothing fires into a page that has been closed."""
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(fn)
        timer.timeout.connect(timer.deleteLater)
        timer.start(0)


# Things she is good at, by what people said they do at first launch
# (setup_wizard.WORK). Each is something Tora really does on the screen.
TRY_ASKING = {
    "Accounting": ("Why won't this total add up?", "What does this line on the statement mean?",
                   "Show me how to make a pivot table here."),
    "Business owner": ("Draft a polite reply to this email.", "What's the catch in this clause?",
                       "Show me how to send this as a PDF."),
    "Student": ("What's wrong with this equation?", "Explain this paragraph more simply.",
                "Quiz me on this page."),
    "Teacher": ("Turn this page into five quiz questions.", "Explain this so a twelve-year-old gets it.",
                "Show me how to share this with my class."),
    "Developer": ("Why is this error happening?", "What does this function do?",
                  "Show me how to make a new branch here."),
    "Sales": ("Draft a follow-up to this email.", "Summarise this company's page for me.",
              "Show me how to log this call in the CRM."),
    "Design & video": ("Show me how to export this as MP4.", "Where's the colour picker in this app?",
                       "Why does this text look blurry?"),
    "Admin & office": ("Show me how to share this file.", "Make this table tidy.",
                       "Where do I change the page margins?"),
    "Writing": ("Make this paragraph shorter.", "Is there a better word here?",
                "Does this email sound too blunt?"),
}
TRY_ANYWAY = ("What's wrong with this equation?", "Where do I change my display scaling?",
              "Show me how to share my screen in Meet.")


def try_asking(uses: list[str]) -> list[str]:
    """Three lines, taking in turn from each thing they do."""
    pools = [list(TRY_ASKING[u]) for u in uses if u in TRY_ASKING] or [list(TRY_ANYWAY)]
    out: list[str] = []
    while len(out) < 3 and any(pools):
        for pool in pools:
            if pool and len(out) < 3:
                out.append(pool.pop(0))
    return out


def _ask(bridge, name: str, default, *args):
    """A bridge reading that a plain or older bridge may not have."""
    fn = getattr(bridge, name, None)
    if fn is None:
        return default
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001 - a page shows what it can
        return default


def _padded(row: QWidget) -> QWidget:
    """A Row inside a ListBox: the row has no side padding of its own."""
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(18, 2, 18, 2)
    lay.addWidget(row)
    return box


# ── Insights ──────────────────────────────────────────────────────────────────

class InsightsPage(Page):
    """What Tora has done for you, counted - never estimated (DESIGN.md §3C,
    ui/insights_data.py). Wispr's Insights layout: three tiles, then where
    you use it and the streak calendar, then the month's plan and agents."""

    HEADING = "Insights"
    LIVE = True
    TOPICS = frozenset({"journal", "account", "agents"})

    def showEvent(self, event) -> None:
        """Opened: the charts grow in, one a beat after another (Wispr's
        page does this). A live redraw while it is open does not replay."""
        super().showEvent(event)
        from ui.app_parts import Grows
        for i, chart in enumerate(self.findChildren(Grows)):
            chart.play(80 + 45 * i)

    def build(self) -> None:
        from ui import insights_data as data
        from ui.app_parts import DayBars, Gauge, GrowMeter, InfoCard, StreakGrid, legend, stat_tile
        d = _ask(self.bridge, "insights", {}) or {}
        if not d:
            d = data.summarise([])
            d.update(agents=data.agents_month([]), plan=[], plan_summary=None, due=0)

        # ── three tiles
        change = d["change"]
        week = (f"{'+' if change > 0 else ''}{change}%" if change is not None else "–")
        tiles = QHBoxLayout()
        tiles.setSpacing(T.GAP)
        tiles.addWidget(stat_tile(str(d["questions"]), "questions asked",
                                  [("This week", str(d["this_week"])), ("On last week", week)],
                                  chart=DayBars(data.daily(d["days"]))), 1)
        tiles.addWidget(stat_tile(str(d["walkthroughs"]), "walkthroughs finished",
                                  [("Time she guided you", f"{d['guided_minutes']} min")]), 1)
        share = d["remembered"] / d["learnable"] if d["learnable"] else 0.0
        tiles.addWidget(stat_tile(f"{d['remembered']}", "things remembered",
                                  [("Of all she taught you", str(d["learnable"])),
                                   ("Due for review", str(d["due"]))],
                                  chart=Gauge(share, "remembered")), 1)
        self.body.add_layout(tiles)

        # ── where you ask · the streak
        mid = QHBoxLayout()
        mid.setSpacing(T.GAP)
        apps = InfoCard("Where you ask", f"apps · {d['apps_used']}", big=True)
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
        streak = InfoCard(f"{d['streak']} day streak", f"longest · {d['longest']} "
                          f"day{'s' if d['longest'] != 1 else ''}", big=True)
        streak.body.addWidget(StreakGrid(data.calendar(d["days"])), 0, Qt.AlignmentFlag.AlignLeft)
        streak.body.addWidget(legend())
        mid.addWidget(streak, 1)
        self.body.add_layout(mid)

        # ── the plan · agents
        low = QHBoxLayout()
        low.setSpacing(T.GAP)
        low.addWidget(self._plan_card(d), 1)
        low.addWidget(self._agents_card(d["agents"]), 1)
        self.body.add_layout(low)

    def _plan_card(self, d: dict) -> QWidget:
        from ui.app_parts import GrowMeter, InfoCard
        summary = d.get("plan_summary")
        if not d["plan"]:
            card = InfoCard("This month")
            card.body.addWidget(label("Your plan's use shows here when you are signed in to "
                                      "a HeyTora plan.", "infoFaint", wrap=True))
            return card
        from account.service import resets_on
        name = summary.get("plan_name") or (summary.get("plan") or "").capitalize() or "HeyTora"
        card = InfoCard("This month", f"{name} plan · resets {resets_on(summary)}")
        for words, used, limit in d["plan"]:
            row = QHBoxLayout()
            row.addWidget(label(words, "tileLine"))
            row.addStretch(1)
            row.addWidget(label(f"{used} of {limit}" if limit is not None else f"{used} · unlimited",
                                "tileLineValue"))
            card.body.addLayout(row)
            frac = (used / limit) if limit else 0.0
            card.body.addWidget(GrowMeter(frac, T.WARN if frac >= 0.8 else T.ACCENT))
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
    and leave the card empty, and its own title repeats the card's."""
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


# How a pack's integration ids read to a person.
_BRANDS = {"quickbooks": "QuickBooks", "onedrive": "OneDrive", "sharepoint": "SharePoint",
           "google_drive": "Google Drive", "gmail": "Gmail"}


def _brand(ident: str) -> str:
    return _BRANDS.get(ident, ident.replace("_", " ").title())


def _no_agents(why: str) -> Card:
    card = Card()
    card.add(Row("The agents did not start", why, dot=T.WARN, first=True))
    return card


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
_TASK_WORDS = {
    "completed": ("Done", T.OK),
    "failed": ("Failed", T.DANGER),
    "cancelled": ("Stopped", T.TEXT_FAINT),
    "running": ("Working", T.ACCENT),
    "planning": ("Planning", T.ACCENT),
    "verifying": ("Checking its work", T.ACCENT),
    "pending": ("Queued", T.TEXT_DIM),
    "waiting_approval": ("Waiting for you", T.WARN),
    "paused": ("Paused", T.WARN),
}


class AgentsPage(Page):
    HEADING = "Agents"
    LIVE = True
    TOPICS = frozenset({"agents"})

    def __init__(self, bridge=None):
        # Built once and kept for the life of the window: the live panel,
        # and a plan-and-activity view for each task looked at. They all
        # listen through ONE event bridge, so neither opening this page nor
        # redrawing it ever adds a listener to the workforce.
        self._events = None
        self._panel = None
        self._details: dict[str, QWidget] = {}
        self._drawn: tuple = ()
        super().__init__(bridge)

    def build_actions(self) -> None:
        self.action("Agent settings").clicked.connect(lambda: self.navigate.emit("settings"))
        self.action("Stop all", "danger").clicked.connect(self.bridge.stop_agents)

    def build(self) -> None:
        wf = self.bridge.workforce()
        if wf is None:
            self.body.add(_no_agents("Nothing can be handed to them until they do - "
                                     "see tora.log for why."))
            return
        # The workforce's own panel - the one the tray's "Active Agents"
        # opens: a card per agent with its progress, Pause and Stop, and a
        # box to give them a job. It redraws itself from agent events.
        from agents.ui import ActiveAgentsPanel, AgentEventBridge
        self._events = AgentEventBridge(wf, self)
        self._panel = _tidy(ActiveAgentsPanel(wf, bridge=self._events))
        self._panel.setMinimumHeight(320)
        self._panel.view_logs.connect(self.show_task)
        work = Card("Working now", "Give them a job here, or just ask Tora out loud.")
        work.add(self._panel)
        self.body.add(work)

        self._waiting = Card("Waiting for you",
                             "Nothing risky runs without a yes - answer in the approval window.")
        self.body.add(self._waiting)
        self._recent = Card("Recent tasks", "Open one for its plan and every step it took.")
        self.body.add(self._recent)

        self._detail = Card("Plan and activity")
        self._detail_title = label("", "cardSub")
        self._detail.add(self._detail_title)
        self._detail.add(spacer(10))
        self._stack = QStackedWidget()
        self._stack.setMinimumHeight(360)
        self._detail.add(self._stack)
        self._detail.hide()
        self.body.add(self._detail)
        self._drawn = ()
        self._fill()

    def refresh(self) -> None:
        """Only the lists are redrawn: the panel and the task views follow
        the agents by themselves, and keep what is typed and scrolled."""
        if self._panel is None:
            if self.bridge.workforce() is not None:
                super().refresh()        # the agents started after this page was drawn
            return
        self._fill()

    def _fill(self) -> None:
        tasks, waiting = self.bridge.tasks(), self.bridge.approvals()
        drawn = (tuple((t["id"], t["status"]) for t in tasks), tuple(a["id"] for a in waiting))
        if drawn == self._drawn:
            return                       # a progress tick: nothing listed here changed
        self._drawn = drawn

        rows = []
        for i, a in enumerate(waiting):
            sub = a["tool"] + (" · needs a typed confirm word" if a["sensitive"] else "")
            if a["task"]:
                sub = f"{a['task']} · {sub}"
            rows.append(Row(_clip(a["reason"], 200), sub, dot=T.WARN, first=(i == 0), wrap=True))
        refill(self._waiting.body, rows)
        self._waiting.setVisible(bool(waiting))

        rows = []
        for i, t in enumerate(tasks):
            words, colour = _TASK_WORDS.get(t["status"], (t["status"].capitalize(), T.TEXT_DIM))
            sub = f"{words} · {_when(t['when'])}" if t["when"] else words
            if t["status"] == "failed" and t["error"]:
                sub += f" · {t['error']}"
            details = button("Details")
            details.clicked.connect(lambda _c=False, tid=t["id"]: self.show_task(tid))
            rows.append(Row(_clip(t["title"], 120), sub, dot=colour, trailing=details,
                            first=(i == 0), wrap=True))
        if not rows:
            rows.append(Row("Nothing yet",
                            "Jobs given to the agents are listed here, finished ones too.",
                            first=True))
        refill(self._recent.body, rows)

    def show_task(self, task_id: str) -> None:
        """One task's plan and activity log, under the lists."""
        wf = self.bridge.workforce()
        if wf is None or self._events is None:
            return
        view = self._details.get(task_id)
        if view is None:
            from agents.ui import ActivityTimeline, PlanView
            view = QWidget()
            lay = QHBoxLayout(view)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(T.GAP)
            lay.addWidget(PlanView(wf, task_id, self._events, embedded=True), 1)
            timeline = _tidy(ActivityTimeline(wf, task_id, bridge=self._events))
            timeline.layout().setContentsMargins(0, 0, 0, 0)    # level with the plan
            lay.addWidget(timeline, 1)
            self._details[task_id] = view
            self._stack.addWidget(view)
        task = wf.get_task(task_id)
        self._detail_title.setText(task.title if task else "")
        self._stack.setCurrentWidget(view)
        self._detail.show()
        QTimer.singleShot(0, lambda: self.body.ensureWidgetVisible(self._detail))


# ── Extensions ────────────────────────────────────────────────────────────────

# Packs whose dashboard is a page of this window. Any other pack's opens in
# the floating dashboard, as it does from the tray.
PACK_PAGES = {"accounting": "accounting"}


class ExtensionsPage(Page):
    HEADING = "Packs"
    LIVE = True
    TOPICS = frozenset({"extensions"})

    def __init__(self, bridge=None):
        self._errors: dict[str, str] = {}      # a pack that would not switch says why
        super().__init__(bridge)

    def build(self) -> None:
        br = self.bridge
        if br.workforce() is None:
            self.body.add(_no_agents("Packs plug into the agents, so none can run "
                                     "until they do - see tora.log for why."))
            return
        packs = br.extensions()
        if not packs:
            card = Card()
            card.add(Row("No packs found",
                         "Packs live in the extensions folder next to HeyTora.", first=True))
            self.body.add(card)
            return
        for info in packs:
            ext_id, on = info["id"], bool(info.get("enabled"))
            card = Card(info.get("name") or ext_id, info.get("description") or "")
            if info.get("offer"):
                card.add(label("Adds: " + " · ".join(info["offer"]), "faint", wrap=True))
            if info.get("optional_integrations"):
                card.add(label("Works with: " + ", ".join(_brand(i) for i in info["optional_integrations"]),
                               "faint", wrap=True))
            card.add(spacer(10))
            foot = QHBoxLayout()
            foot.setSpacing(10)
            foot.addWidget(Pill("On" if on else "Off", T.OK if on else T.TEXT_FAINT, dot=on))
            if info.get("version"):
                foot.addWidget(label(f"v{info['version']}", "faint"))
            foot.addStretch(1)
            if on:
                opener = button("Open dashboard")
                opener.clicked.connect(lambda _c=False, e=ext_id: self._open(e))
                foot.addWidget(opener)
            switch = Switch(on)
            switch.toggled.connect(lambda v, e=ext_id: self._turn(e, v))
            foot.addWidget(switch, 0, Qt.AlignmentFlag.AlignVCenter)
            card.body.addLayout(foot)
            if self._errors.get(ext_id):
                why = label(f"That did not work: {self._errors[ext_id]}", wrap=True)
                why.setStyleSheet(f"color: {T.DANGER};")
                card.add(spacer(8))
                card.add(why)
            self.body.add(card)

    def _turn(self, ext_id: str, on: bool) -> None:
        why = self.bridge.set_extension(ext_id, on)
        if why:
            self._errors[ext_id] = why
        else:
            self._errors.pop(ext_id, None)
        # Redrawn after the click has finished: the switch being clicked is
        # one of the widgets a redraw replaces.
        QTimer.singleShot(0, self.refresh)

    def _open(self, ext_id: str) -> None:
        page = PACK_PAGES.get(ext_id)
        if page:
            self.navigate.emit(page)
        else:
            self.bridge.open_dashboard(ext_id)


# ── Accounting ────────────────────────────────────────────────────────────────

class AccountingPage(Page):
    HEADING = "Accounting"
    LIVE = True
    TOPICS = frozenset({"extensions"})
    PACK = "accounting"

    def __init__(self, bridge=None):
        # The pack's own dashboard (extensions/accounting/ui/dashboard.py) -
        # client picker, month-end, exceptions, drafts, reports. Built when
        # the pack is on and kept while it stays on: it holds the chosen
        # client and redraws itself.
        self._dash = None
        self._drawn_for = ""
        self._error = ""
        super().__init__(bridge)

    def _mode(self) -> str:
        if self.bridge.workforce() is None:
            return "no agents"
        return "on" if self.bridge.extension_on(self.PACK) else "off"

    def build(self) -> None:
        br = self.bridge
        mode = self._drawn_for = self._mode()
        if mode == "no agents":
            self.body.add(_no_agents("The Accounting pack runs on them - see tora.log for why."))
            return
        if mode == "on":
            why = ""
            try:
                dash = br.dashboard(self.PACK)
            except Exception as exc:  # noqa: BLE001 - the pack's code; the page says why
                dash, why = None, str(exc) or type(exc).__name__
            if dash is not None:
                self._dash = _tidy(dash)
                dash.setMinimumHeight(640)
                holder = Card()
                holder.add(dash)
                self.body.add(holder)
                return
            card = Card()
            card.add(Row("The dashboard could not load", why or "See tora.log for why.",
                         dot=T.DANGER, first=True, wrap=True))
            self.body.add(card)
            return

        info = next((p for p in br.extensions() if p.get("id") == self.PACK), None)
        if info is None:
            card = Card()
            card.add(Row("The Accounting pack is not installed",
                         "It lives in the extensions folder next to HeyTora.", first=True))
            self.body.add(card)
            return
        card = Card("Accounting is off", info.get("description") or "")
        if info.get("offer"):
            card.add(label("Adds: " + " · ".join(info["offer"]), "faint", wrap=True))
        card.add(spacer(10))
        foot = QHBoxLayout()
        foot.setSpacing(10)
        turn_on = button("Turn on Accounting", "primary")
        turn_on.clicked.connect(self._turn_on)
        every = button("All extensions")
        every.clicked.connect(lambda: self.navigate.emit("extensions"))
        foot.addWidget(turn_on)
        foot.addWidget(every)
        foot.addStretch(1)
        card.body.addLayout(foot)
        if self._error:
            why = label(f"That did not work: {self._error}", wrap=True)
            why.setStyleSheet(f"color: {T.DANGER};")
            card.add(spacer(8))
            card.add(why)
        self.body.add(card)

    def refresh(self) -> None:
        if self._mode() == self._drawn_for:
            if self._dash is not None:
                try:
                    self._dash.refresh()
                except Exception:  # noqa: BLE001 - the pack's code
                    _log.debug("the accounting dashboard could not refresh", exc_info=True)
            return
        self._clock(False)                # the old dashboard's clock stops before it goes
        self._dash = None
        super().refresh()

    def _turn_on(self) -> None:
        self._error = self.bridge.set_extension(self.PACK, True)
        self._drawn_for = ""              # redraw even if it stayed off: to say why
        QTimer.singleShot(0, self.refresh)

    # The dashboard redraws itself every few seconds. Only while it can be
    # seen: kept for the life of the window, it would otherwise read the
    # books all day behind a page nobody has open.
    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._clock(True)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._clock(False)

    def _clock(self, on: bool) -> None:
        if self._dash is None:
            return
        for timer in self._dash.findChildren(QTimer, options=Qt.FindChildOption.FindDirectChildrenOnly):
            timer.start() if on else timer.stop()


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

        split = QHBoxLayout()
        split.setSpacing(T.GAP)
        split.addWidget(self._recording(), 1)
        split.addWidget(self._capture(), 1)
        self.body.add_layout(split)

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

    def _recording(self) -> Card:
        on = self.bridge.recording()
        card = Card("Lesson recording",
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
        card = Card("Workflow capture",
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


# ── Tutor ─────────────────────────────────────────────────────────────────────

class TutorPage(Page):
    HEADING = "Tutor"
    LIVE = True

    def _toggle(self, card: Card, name: str, heading: str, sub: str, *, first=False) -> None:
        row = ToggleRow(heading, sub, self.bridge.switch(name), first=first)
        row.switch.toggled.connect(lambda on, n=name: self.bridge.set_switch(n, on))
        card.add(row)

    def build(self) -> None:
        how = Card("Teaching style")
        self._toggle(how, "slow_mode", "Slow mode",
                     "A calmer pace on screen. For someone new to the machine.", first=True)
        self._toggle(how, "auto_advance", "Advance by itself",
                     "Move on when she sees the step done, without waiting for “next”.")
        self._toggle(how, "quiz_mode", "Quiz mode",
                     "Ask a question back, so it sticks. Lasts until Tora restarts.")
        self._toggle(how, "code_mode", "Code mode",
                     "Notice when you are in an editor and explain like a developer.")
        self.body.add(how)

        eyes = Card("Eyes on screen")
        self._toggle(eyes, "privacy_guard", "Privacy guard",
                     "Never send a screenshot when a password manager, banking "
                     "page or private window is in front.", first=True)
        self._toggle(eyes, "ocr", "Read text on screen (OCR)",
                     "Read fine print when you ask her to. Runs on this machine.")
        self._toggle(eyes, "web_search", "Web search",
                     "Look things up for news, prices and releases.")
        self.body.add(eyes)

        # The tray's "Instructions for Tora…" - standing orders she keeps in
        # mind on every answer. Saved through the tray's own signal.
        rules = Card("Instructions for Tora",
                     "Kept in mind on every answer - “only help with Excel”, "
                     "“keep answers very short”.")
        from PyQt6.QtWidgets import QPlainTextEdit
        box = QPlainTextEdit(self.bridge.instructions())
        box.setPlaceholderText("Nothing yet - she helps with anything.")
        box.setFixedHeight(84)
        rules.body.addWidget(box)
        foot = QHBoxLayout()
        foot.setSpacing(10)
        saved = label("", "faint")
        save = button("Save instructions", "primary")

        def _save() -> None:
            self.bridge.set_instructions(box.toPlainText())
            saved.setText("Saved - used from the next answer.")

        save.clicked.connect(_save)
        foot.addWidget(save)
        foot.addWidget(saved, 1)
        rules.body.addLayout(foot)
        self.body.add(rules)


# ── Voice & models ────────────────────────────────────────────────────────────

class VoicePage(Page):
    HEADING = "Voice & models"
    LIVE = True

    # What each model role is FOR, in words (gemini.Models.all_roles).
    _ROLES = (("realtime", "Conversation", "Hears you and answers, live"),
              ("vision_fast", "Quick look · walkthrough planner", "Fast; plans step one first"),
              ("vision_deep", "Careful look", "Slower, much better at detail"),
              ("planner", "Careful planner", "Only when the quick plan fails"),
              ("stt", "Transcription", "Wake word, and the classic route"),
              ("tts", "Tora's own voice", "Walkthrough steps and local replies"),
              ("embed", "Memory", "Finding what she remembers"))

    def build_actions(self) -> None:
        self.action("Refresh model list").clicked.connect(self.bridge.refresh_models)

    def build(self) -> None:
        br = self.bridge

        voice = Card("Voice")
        voices = br.voices()
        pick = Select(*voices, width=240)
        if br.voice() in voices:
            pick.setCurrentIndex(voices.index(br.voice()))
        pick.currentIndexChanged.connect(
            lambda k: br.set_voice(voices[k]) if 0 <= k < len(voices) else None)
        voice.add(Row("Spoken voice", "Both routes use it; the live session restarts to switch.",
                      trailing=pick, first=True))
        routes = ("realtime", "cascade")
        route = Select("Live - answers in about a second",
                       "Classic - transcribe, think, then speak", width=320)
        route.setCurrentIndex(routes.index(br.route()) if br.route() in routes else 0)
        route.currentIndexChanged.connect(
            lambda k: br.set_route(routes[k]) if 0 <= k < 2 else None)
        voice.add(Row("Conversation", trailing=route))
        self.body.add(voice)

        models = Card("Models", "One job each. Set in .env; the list refreshes itself weekly.")
        roles = br.models()
        for i, (role, job, why) in enumerate(self._ROLES):
            if role in roles:
                models.add(Row(job, why, trailing=label(roles[role], "dim"), first=(i == 0)))
        self.body.add(models)

        # The workforce's own endpoint editor - the same one "Connected
        # accounts…" opens: add, test and remove OpenAI-compatible endpoints
        # and route agents to them. It applies to the live agents on Save.
        ends = Card("Agent endpoints", "Where the agents' requests go.")
        wf = br.workforce()
        if wf is not None:
            from agents.ui.accounts import ConnectedAccountsDialog
            embedded = _tidy(ConnectedAccountsDialog(wf))
            embedded.setMinimumHeight(420)
            ends.body.addWidget(embedded)
        else:
            ends.add(Row("The agents did not start",
                         "Endpoints can be set once they do - see tora.log.",
                         dot=T.WARN, first=True))
        self.body.add(ends)


# ── Tora ──────────────────────────────────────────────────────────────────────

class ToraPage(TutorPage, VoicePage):
    """Make her yours: how she teaches and how she sounds, on one page.
    (Step 1 of the shell puts the two pages' cards together; the page gets
    its own design in step A4 - DESIGN.md §9.)"""
    HEADING = "Tora"

    def build(self) -> None:
        VoicePage.build(self)
        self.body.add(self._sounds())
        TutorPage.build(self)

    def _sounds(self) -> QWidget:
        """The pop as she starts listening (ui/sounds.py). Choosing one plays it."""
        from ui import sounds
        card = Card("Sounds")
        keys = [k for k, _n in sounds.SOUNDS] + [sounds.OFF]
        pick = Select(*[n for _k, n in sounds.SOUNDS], "Off", width=240)
        now = _ask(self.bridge, "listen_sound", keys[0])
        pick.setCurrentIndex(keys.index(now) if now in keys else 0)
        pick.currentIndexChanged.connect(
            lambda i: self.bridge.set_listen_sound(keys[i]) if 0 <= i < len(keys) else None)
        card.add(Row("When she starts listening",
                     "A short pop the moment you can talk. Pick one to hear it.",
                     trailing=pick, first=True))
        return card


# ── Settings ──────────────────────────────────────────────────────────────────

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
    from PyQt6.QtWidgets import QMessageBox
    answer = QMessageBox.question(
        parent, "Sign out of HeyTora?",
        "HeyTora needs your account to work. Signing out closes it and opens the "
        "sign-in window.",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Cancel)
    return answer == QMessageBox.StandardButton.Yes


class SettingsPage(Page):
    HEADING = "Settings"
    LIVE = True
    # The plan and usage arrive after the page is drawn, and move with every
    # question; an update downloads in the background. Both are redrawn in
    # place (refresh_topics) - never the whole page.
    TOPICS = frozenset({"account", "updates"})

    # The allowances shown, in the user's words. "calls" is a hidden backstop.
    _USAGE = (("talk", "Talk", "messages"), ("agent", "Agent", "messages"),
              ("dictation", "Dictation", "dictations"))

    def showEvent(self, event) -> None:
        """Every time the page comes on screen - opened, switched to, or the
        window back from being closed - the plan is read again (at most once a
        minute). What is known shows at once; the answer redraws the card."""
        super().showEvent(event)
        self.bridge.refresh_account()

    def refresh_topics(self, topics: set) -> None:
        """The plan moved (every question moves it), or an update did: only
        that card is swapped. The rest of Settings - a switch being reached
        for, something half-typed, where the page is scrolled to - stays
        exactly as it is."""
        for topic, slot_name, make in (("account", "_account_slot", self._account_card),
                                       ("updates", "_updates_slot", self._updates_card)):
            if topic not in topics:
                continue
            slot = getattr(self, slot_name, None)
            if slot is None or not slot.layout():
                self.refresh()
                return
            lay = slot.layout()
            while lay.count():
                old = lay.takeAt(0).widget()
                if old is not None:
                    old.setParent(None)
                    old.deleteLater()
            lay.addWidget(make())

    def _updates_card(self) -> Card:
        """HeyTora keeps itself up to date (updates/service.py): which version
        this is, what is happening, and the one switch."""
        br = self.bridge
        try:
            st = br.update_state()
        except Exception:  # noqa: BLE001 - a bridge without updates
            st = {"state": "dev", "current": "", "latest": "", "notes": "", "progress": 0.0,
                  "error": "", "auto": True}
        card = Card("Updates", "HeyTora keeps itself up to date - nothing to download, nothing to set up.")
        state, now, latest = st["state"], st["current"], st["latest"]

        def check_button(kind: str = "ghost"):
            b = button("Check for updates", kind)
            b.clicked.connect(br.check_updates)
            return b
        checked = st.get("checked_at") or 0
        ago = _checked_ago(checked) if checked else ""
        if state == "dev":
            words = "Running from source: updates come from git, not from here."
            if latest:
                words += f" Latest published: {latest}" + (f" (checked {ago})." if ago else ".")
            if st.get("error"):
                words += f" {st['error']}"
            card.add(Row(f"HeyTora {now}", words, dot=T.TEXT_DIM, trailing=check_button(), first=True))
            return card
        if state == "ready":
            go = button("Restart to update", "primary")
            go.clicked.connect(br.install_update)
            card.add(Row(f"HeyTora {latest} is ready", f"You have {now}. " + (
                "It installs by itself the next time HeyTora starts - or now."
                if st["auto"] else "Restart to update when it suits you."),
                dot=T.ACCENT, trailing=go, first=True))
        elif state == "downloading":
            card.add(Row(f"Downloading HeyTora {latest}…", f"You have {now} · {round(st['progress'] * 100)}%",
                         dot=T.ACCENT, first=True))
            card.body.addWidget(Meter(st["progress"]))
        elif state == "installing":
            card.add(Row(f"Installing HeyTora {latest}…", "HeyTora opens again in a moment.",
                         dot=T.ACCENT, first=True))
        elif state == "checking" or (st.get("busy") and state not in ("downloading", "ready")):
            card.add(Row(f"HeyTora {now}", "Checking for updates…", dot=T.OK, first=True))
        elif state == "failed":
            page = button("Download page")
            page.clicked.connect(br.open_download_page)
            card.add(Row(f"HeyTora {now}", st["error"] or "Couldn't update.", dot=T.WARN,
                         trailing=page, first=True))
            card.body.addWidget(check_button("link"), 0, Qt.AlignmentFlag.AlignLeft)
        else:
            if state == "current":
                words = "Up to date" + (f" · checked {ago}." if ago else ".")
            else:
                words = st.get("error") or "Checks for updates every few hours."
            card.add(Row(f"HeyTora {now}", words, dot=T.WARN if st.get("error") else T.OK,
                         trailing=check_button(), first=True))
        if st["notes"] and state in ("ready", "downloading"):
            card.add(label(f"What's new: {st['notes']}", "faint", wrap=True))
        auto = ToggleRow("Install updates automatically",
                         "When an update is ready it installs by itself - the next time HeyTora "
                         "starts, or as you quit it. HeyTora keeps everything you set.", st["auto"])
        auto.switch.toggled.connect(br.set_auto_update)
        card.add(auto)
        return card

    def _sign_out_button(self):
        out = button("Sign out")
        out.clicked.connect(lambda: confirm_sign_out(self) and self.bridge.account_sign_out())
        return out

    def _account_card(self) -> Card:
        """The HeyTora account (1 Oct 2026) - who is signed in, the plan, and
        this month's use - or the user's own key."""
        br = self.bridge
        st = br.account_state()
        if st["mode"] == "own_key":
            card = Card("Your HeyTora account", "Thinking with your own Gemini key, straight to "
                                                "Google - no plan limits.")
            first = True
            if st["configured"] and st["signed_in"]:
                card.add(Row(st["email"] or "Signed in", "Signed in", dot=T.OK,
                             trailing=self._sign_out_button(), first=True))
                first = False
            card.add(Row("Using your own Gemini key", "Change it in Setup → API keys.",
                         dot=T.OK if st["own_key"] else T.WARN, first=first))
            if st["configured"] and st["signed_in"]:
                switch = button("Use my plan instead")
                switch.clicked.connect(lambda: br.set_access_mode("account"))
                known = st["summary"] or {}
                plan = known.get("plan_name") or (known.get("plan") or "").capitalize()
                card.add(Row(f"Your HeyTora plan{f' ({plan})' if plan else ''}",
                             "Think on your plan instead of your key.", trailing=switch))
            elif st["configured"]:
                switch = button("Use a HeyTora account instead")
                switch.clicked.connect(lambda: (br.set_access_mode("account"), br.run_setup()))
                card.add(Row("Have a HeyTora plan?", "Sign in and use it here.", trailing=switch))
            return card

        manage = button("Manage plan", "primary") if st["website"] else None
        if manage is not None:
            manage.clicked.connect(br.open_website)
        card = Card("Your HeyTora account", "Your plan and this month's use.", action=manage)
        if not st["configured"]:
            card.add(Row("HeyTora accounts aren't set up in this build",
                         "Use your own Gemini key instead.", dot=T.WARN, first=True))
            return card
        if not st["signed_in"]:
            sign_in = button("Sign in…", "primary")
            sign_in.clicked.connect(br.run_setup)
            card.add(Row("Not signed in", "Sign in with the email you used on the website.",
                         dot=T.WARN, trailing=sign_in, first=True))
            return card

        from account.service import resets_on
        who = st["email"] or "Signed in"
        summary = st["summary"]
        if summary is None:
            # Nothing known yet: say what is happening - never a silent wait.
            if st["error"] and not st["busy"]:
                again = button("Try again")
                again.clicked.connect(lambda: br.refresh_account(force=True))
                card.add(Row(who, "Couldn't read your plan", dot=T.WARN, trailing=again, first=True))
                card.add(label(st["error"], "faint", wrap=True))
            else:
                card.add(Row(who, "Reading your plan…", dot=T.OK,
                             trailing=self._sign_out_button(), first=True))
        else:
            plan = summary.get("plan_name") or (summary.get("plan") or "").capitalize() or "HeyTora"
            when = resets_on(summary)
            card.add(Row(who, f"{plan} plan · this month's use starts again on {when}",
                         dot=T.OK, trailing=self._sign_out_button(), first=True))
            if st["error"] and not st["busy"]:
                again = button("Try again")
                again.clicked.connect(lambda: br.refresh_account(force=True))
                card.add(Row("Couldn't update just now", "This is your last known use.",
                             dot=T.WARN, trailing=again))
            blocked = summary.get("blocked") or {}
            if blocked.get("all") == "budget":
                card.add(Row("This month's use is up",
                             f"Everything starts again on {when} - or upgrade on the website, or "
                             "use your own Gemini key.", dot=T.WARN))
            limits, usage = summary.get("limits") or {}, summary.get("usage") or {}
            for kind, name, unit in self._USAGE:
                if kind not in limits:
                    continue
                used, limit = int(usage.get(kind) or 0), limits[kind]
                if limit is None:
                    words = f"{used} this month - unlimited"
                else:
                    left = max(0, int(limit) - used)
                    words = f"{used} of {limit} {unit} this month · " + (f"{left} left" if left else "all used")
                done_today = int((summary.get("today") or {}).get(kind) or 0)
                if done_today:
                    words += f" · {done_today} today"
                if blocked.get(kind) == "day":
                    words += " · today's fair use reached"
                full = limit is not None and used >= int(limit)
                meter = Meter(0.0 if limit is None else min(1.0, used / max(1, int(limit))),
                              colour=T.WARN if full or blocked.get(kind) else T.ACCENT)
                box = QVBoxLayout()
                box.setSpacing(6)
                box.addWidget(label(f"{name}:  {words}"))
                box.addWidget(meter)
                card.body.addLayout(box)
            fair = summary.get("fair_use")
            if isinstance(fair, (int, float)):
                box = QVBoxLayout()
                box.setSpacing(6)
                box.addWidget(label(f"Overall use this month:  {round(fair * 100)}%"))
                box.addWidget(Meter(min(1.0, float(fair)), colour=T.WARN if fair >= 0.8 else T.ACCENT))
                box.addWidget(label("Everything Tora does for you - looking at your screen, planning, "
                                    "agents thinking - counts a little towards this.", "faint", wrap=True))
                card.body.addLayout(box)
        own = button("Use my own Gemini key instead", "link")
        own.clicked.connect(lambda: (br.set_access_mode("own_key"), br.run_setup()))
        card.body.addWidget(own, 0, Qt.AlignmentFlag.AlignLeft)
        return card

    def build(self) -> None:
        br = self.bridge

        # APP-14. One switch for "stop asking me": the field log had 71
        # approval boxes in three days and not one "no". What it still asks
        # about is said on the page, because a switch like this is only
        # safe when its edges are plain.
        approvals = Card("Approvals", "When Tora and her agents stop to ask you first.")
        auto = ToggleRow("Approve automatically",
                         "Agents and the desktop actions you ask for go ahead without the "
                         "approval box - files, commands, the browser, your mouse and "
                         "keyboard - whatever “Run without asking” below says.",
                         br.auto_approve(), first=True)
        auto.switch.toggled.connect(br.set_auto_approve)
        approvals.add(auto)
        approvals.add(label("Still asks, every time: anything that pays, files a return, "
                            "sends, posts or deletes for others to see. A plan still waits "
                            "for you while “Show me the plan before anything runs” is on. "
                            "Esc takes your mouse and keyboard back. What an agent runs "
                            "this way is marked in its task's activity.", "faint", wrap=True))
        self.body.add(approvals)
        # The HeyTora account (or own key): second, after the approvals that
        # APP-14 put first. In a slot of its own, so the plan can move with
        # every question without the page around it being rebuilt.
        self._account_slot = QWidget()
        slot = QVBoxLayout(self._account_slot)
        slot.setContentsMargins(0, 0, 0, 0)
        slot.addWidget(self._account_card())
        self.body.add(self._account_slot)
        self._updates_slot = QWidget()
        slot = QVBoxLayout(self._updates_slot)
        slot.setContentsMargins(0, 0, 0, 0)
        slot.addWidget(self._updates_card())
        self.body.add(self._updates_slot)

        # The workforce's own settings widget - the one "Agent settings…" in
        # the tray opens: workspace, what runs without asking, plan first,
        # how many at once. It saves to config AND applies to the running
        # agents, so it is embedded rather than re-implemented (APP-11).
        rails = Card("Agent rails", "Agents may only ever write inside the workspace.")
        wf = br.workforce()
        if wf is not None:
            from agents.ui.settings import AgentSettingsDialog
            rails.body.addWidget(_tidy(AgentSettingsDialog(wf), "Agent settings"))
        else:
            rails.add(Row("The agents did not start",
                          "Nothing here can be changed until they do - see "
                          "tora.log for why.", dot=T.WARN, first=True))
        self.body.add(rails)

        allowed = Card("Desktop actions you allowed",
                       "Everything else on your desktop asks first, every time.")
        grants = br.grants()
        for i, key in enumerate(grants):
            forget = button("Forget")
            row = Row(key, "Runs without asking", dot=T.WARN, trailing=forget,
                      first=(i == 0))
            forget.clicked.connect(lambda _c=False, k=key, r=row: (br.forget_grant(k), r.hide()))
            allowed.add(row)
        if not grants:
            allowed.add(Row("Nothing is allowed without asking",
                            "Say “always allow” in a confirmation to add one.",
                            first=True))
        self.body.add(allowed)

        mic = Card("Microphone")
        devices = br.mics()
        dev = Select(*(name + ("  (system default)" if default else "")
                       for _i, name, default in devices) or ("No microphone found",),
                     width=420)
        current = br.mic_index()
        indexes = [i for i, _n, _d in devices]
        chosen = (indexes.index(current) if current in indexes else
                  next((k for k, (_i, _n, d) in enumerate(devices) if d), 0))
        dev.setCurrentIndex(chosen)
        dev.currentIndexChanged.connect(
            lambda k: br.set_mic(indexes[k]) if 0 <= k < len(indexes) else None)
        mic.add(Row("Input device", trailing=dev, first=True))
        listen = ToggleRow("Always listening",
                           "Wake on “Tora”. Off means she only hears you while "
                           "the hotkey is held.", br.switch("wake_word"))
        listen.switch.toggled.connect(lambda on: br.set_switch("wake_word", on))
        mic.add(listen)
        mic.add(Row("Hold to talk", br.hotkey() or "not set",
                    trailing=label("set in .env as TORA_HOTKEY", "faint")))
        self.body.add(mic)

        look = Card("HeyTora on this PC")
        panel = ToggleRow("Show the floating panel",
                          "The small window with the conversation in it.",
                          br.panel_visible(), first=True)
        panel.switch.toggled.connect(br.set_panel_visible)
        look.add(panel)
        start = ToggleRow("Start HeyTora when Windows starts",
                          "For you only - no admin rights needed.", br.autostart())
        start.switch.toggled.connect(br.set_autostart)
        look.add(start)
        modes = (("system", "Match Windows"), ("light", "Light"), ("dark", "Dark"))
        theme = Select(*(words for _m, words in modes), width=180)
        keys = [m for m, _w in modes]
        theme.setCurrentIndex(keys.index(br.theme()) if br.theme() in keys else 0)
        theme.currentIndexChanged.connect(
            lambda k: br.set_theme(keys[k]) if 0 <= k < len(keys) else None)
        look.add(Row("Appearance", "Light is heytora.com's soft grey; dark is its black.",
                     trailing=theme))
        self.body.add(look)


# ── Setup ─────────────────────────────────────────────────────────────────────

class SetupPage(Page):
    HEADING = "Setup"

    LIVE = True
    TOPICS = frozenset({"state", "account", "agents"})

    _LEVEL = {"ok": T.OK, "warn": T.WARN, "bad": T.DANGER}

    def build(self) -> None:
        br = self.bridge
        change = button("Change keys…", "primary")
        change.clicked.connect(br.open_keys)
        keys = Card("API keys", "Stored in .env on this machine - never shown here.",
                    action=change)
        problem = br.problem()
        for i, k in enumerate(br.keys()):
            if not k["set"]:
                state, colour = ("Not set - Tora cannot answer without it"
                                 if k["env"] == "GOOGLE_API_KEY" else
                                 "Not set - web search uses the free engine"), (
                    T.DANGER if k["env"] == "GOOGLE_API_KEY" else T.TEXT_FAINT)
            elif k["env"] == "GOOGLE_API_KEY" and problem == "key":
                state, colour = f"Set (…{k['tail']}) but Google refused it", T.DANGER
            elif k["env"] == "GOOGLE_API_KEY" and problem == "billing":
                state, colour = f"Set (…{k['tail']}) - but the credits have run out", T.DANGER
            else:
                state, colour = (f"Set · ends in {k['tail']}" if k["tail"] else "Set"), T.OK
            keys.add(Row(k["label"], state, dot=colour, first=(i == 0)))
        self.body.add(keys)

        checks = Card("Diagnostics", "What is working right now. Nothing here goes online.")
        for i, (name, detail, level) in enumerate(br.diagnostics()):
            checks.add(Row(name, detail, dot=self._LEVEL.get(level, T.TEXT_FAINT),
                           first=(i == 0)))
        self.body.add(checks)

        foot = QHBoxLayout()
        foot.setSpacing(10)
        wizard = button("Run the setup wizard again")
        wizard.clicked.connect(br.run_setup)
        foot.addWidget(wizard)
        report = button("Save diagnostics report", "primary")
        report.clicked.connect(br.save_report)
        foot.addWidget(report)
        foot.addStretch(1)
        quit_ = button("Quit HeyTora", "danger")
        quit_.clicked.connect(br.quit)
        foot.addWidget(quit_)
        wrap = QWidget()
        wrap.setLayout(foot)
        self.body.add(wrap)


# The sidebar, top to bottom, and the single place a new page gets added.
# place: "main" (the list), "pack" (a pack's page: a row while the pack is
# on - PACK_ROWS), "bottom" (under the list, with the free month and help),
# "hidden" (reached from elsewhere: Help -> Keys and diagnostics).
PAGES = (
    ("home",       "Home",       "main",   HomePage),
    ("insights",   "Insights",   "main",   InsightsPage),
    ("agents",     "Agents",     "main",   AgentsPage),
    ("journal",    "Journal",    "main",   JournalPage),
    ("extensions", "Packs",      "main",   ExtensionsPage),
    ("accounting", "Accounting", "pack",   AccountingPage),
    ("tora",       "Tora",       "main",   ToraPage),
    ("settings",   "Settings",   "bottom", SettingsPage),
    ("setup",      "Setup",      "hidden", SetupPage),
)

# A pack page's row -> the pack that must be on for it to show.
PACK_ROWS = {"accounting": "accounting"}
