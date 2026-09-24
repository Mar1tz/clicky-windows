"""The nine pages of the app window.

Each one is the single-window home of something that is currently its own
floating window or a branch of the tray menu:

    Home         the panel's status, plus what to do next
    Agents       agents/ui/agents_panel · plan_view · timeline
    Extensions   agents/ui/extensions (the pack switches)
    Accounting   extensions/accounting/ui/dashboard
    Journal      the Journal / Lesson Recording / Workflow Capture menus
    Tutor        the Tutor Mode menu
    Voice        the Conversation / Voice / Models menus
    Settings     agents/ui/settings · the mic, hotkey and privacy items
    Setup        ui/api_keys_dialog · the setup wizard · diagnostics

The existing windows are untouched and still open from the tray. This is
the layout those surfaces will move into, with the content drawn from real
examples so the spacing is judged against real text rather than lorem
ipsum. Nothing here reads or writes live state yet — every number is a
placeholder and every control is inert on purpose.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget,
)

from ui import app_theme as T
from ui.app_widgets import (
    Card, Dot, Meter, Metric, PageBody, Pill, Row, Select, Switch, ToggleRow,
    button, columns, hrule, label, lede, section, spacer, title,
)


class Page(QWidget):
    """Title, one line of explanation, optional buttons, then a scrolling body.

    A LIVE page (APP-10) reads everything it shows from the bridge and is
    rebuilt each time it is shown or something it shows changes: its
    header buttons go in build_actions (once), its content in build (every
    refresh). A page that is not live yet builds once, as the M29 drafts do.
    """

    HEADING = "Page"
    LEDE = ""
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
        self.setStyleSheet(f"QWidget#page {{ background: {T.BG}; }}")
        self.setObjectName("page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QWidget()
        head_lay = QHBoxLayout(head)
        head_lay.setContentsMargins(T.GUTTER, 20, T.GUTTER, 18)
        head_lay.setSpacing(16)
        text = QVBoxLayout()
        text.setSpacing(4)
        text.addWidget(title(self.HEADING))
        if self.LEDE:
            text.addWidget(lede(self.LEDE))
        head_lay.addLayout(text, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        head_lay.addLayout(self.actions)
        outer.addWidget(head)

        self.body = PageBody()
        outer.addWidget(self.body, 1)
        self.build_actions()
        self.build()
        self.body.finish()

    def build_actions(self) -> None:
        """The header buttons - built once. Live pages override this."""

    def build(self) -> None:          # pragma: no cover - overridden
        pass

    def refresh(self) -> None:
        """Rebuild the body from what is true now (live pages only)."""
        if not self.LIVE:
            return
        self.body.clear()
        self.build()
        self.body.finish()

    def action(self, text: str, kind: str = "ghost"):
        b = button(text, kind)
        self.actions.addWidget(b)
        return b


# ── Home ──────────────────────────────────────────────────────────────────────

class HomePage(Page):
    HEADING = "Tora"
    LEDE = "Talk to her. She can see your screen, point at things, and put agents to work."
    LIVE = True
    TOPICS = frozenset({"state", "account", "agents", "guide", "switches"})

    _STATE_WORDS = {"listening": "Listening…", "thinking": "Thinking…",
                    "speaking": "Speaking…"}

    def build_actions(self) -> None:
        self.action("Open panel").clicked.connect(self.bridge.show_panel)

    def build(self) -> None:
        br = self.bridge
        state, problem = br.state(), br.problem()
        ambient = br.listening() == "ambient"

        hero = Card()
        row = QHBoxLayout()
        row.setSpacing(16)
        busy = state in self._STATE_WORDS
        row.addWidget(Dot(T.STATE_COLOUR.get(state, T.OK) if busy else
                          (T.OK if ambient else T.TEXT_DIM), 12, glow=busy or ambient),
                      0, Qt.AlignmentFlag.AlignVCenter)
        text = QVBoxLayout()
        text.setSpacing(3)
        headline = self._STATE_WORDS.get(state) or (
            "Listening for “Tora”" if ambient else f"Hold {br.hotkey() or 'the hotkey'} and talk")
        text.addWidget(label(headline, "cardTitle"))
        facts = [br.mic_name() or "no microphone found",
                 "wake word on" if ambient else "wake word off"]
        text.addWidget(label(" · ".join(facts), "faint"))
        row.addLayout(text, 1)
        if problem == "billing":
            row.addWidget(Pill("Out of Gemini credit", T.DANGER, dot=True))
        elif problem == "key":
            row.addWidget(Pill("API key refused", T.DANGER, dot=True))
        elif br.live():
            row.addWidget(Pill("Connected", T.OK, dot=True))
        else:
            row.addWidget(Pill("Not connected", T.WARN, dot=True))
        hero.body.addLayout(row)
        self.body.add(hero)

        agents, today, walk = br.agents(), br.today(), br.walkthrough()
        self.body.add_layout(columns(
            Metric(str(len(agents)), "agents working", T.ACCENT if agents else T.TEXT),
            Metric(str(today["walkthroughs"]), "walkthroughs today"),
            Metric(str(today["questions"]), "questions today"),
            Metric(str(today["due"]), "due for review", T.WARN if today["due"] else T.TEXT),
        ))

        work = Card("Happening now", "Walkthroughs and agents, while they work.")
        first = True
        if walk is not None:
            where = (f"Step {walk['step']} of {walk['total']}" if walk["total"]
                     else f"Step {walk['step']}")
            if walk["instruction"]:
                where += f" · {walk['instruction']}"
            work.add(Row(f"Walkthrough · {walk['goal'] or 'guided steps'}", where,
                         dot=T.ACCENT, trailing=Pill("Running", T.ACCENT), first=first))
            first = False
        for a in agents:
            doing = a["doing"] or (f"Step {a['step']} of {a['total']}" if a["total"] else "Working")
            work.add(Row(a["title"], doing, dot=T.ACCENT,
                         trailing=Pill(f"{round(a['progress'] * 100)}%", T.ACCENT), first=first))
            first = False
        if first:
            work.add(Row("Nothing running right now",
                         "Ask “show me how to…” for a walkthrough, or give her a job to do.",
                         first=True))
        self.body.add(work)

        start = Card("Try asking", "Out loud — no clicking required.")
        for line in ("“Where do I change my display scaling?”",
                     "“Open Spotify and play my focus playlist.”",
                     "“Show me how to share my screen in Google Meet.”",
                     "“Build me a landing page for my coffee shop.”",
                     "“Reconcile the September bank statement.”"):
            start.add(Row(line, first=(line.startswith("“Where"))))
        self.body.add(start)


# ── Agents ────────────────────────────────────────────────────────────────────

class AgentsPage(Page):
    HEADING = "Agents"
    LEDE = "Work Tora is doing for you in the background — the plan, the progress, and every step it took."

    def build(self) -> None:
        self.action("Stop all", "danger")
        self.action("New task", "primary")

        active = Card("Application Builder",
                      "Create a React landing page in ~/Tora Projects/CoffeeCo",
                      action=button("Pause"))
        bar = QVBoxLayout()
        bar.setSpacing(8)
        meta = QHBoxLayout()
        meta.addWidget(label("Step 4 of 10 · Creating Navbar.tsx", "faint"), 1)
        meta.addWidget(label("70%", "faint"))
        bar.addLayout(meta)
        bar.addWidget(Meter(0.7))
        active.body.addLayout(bar)
        self.body.add(active)

        split = QHBoxLayout()
        split.setSpacing(T.GAP)

        plan = Card("Plan", "What it decided to do, before it did any of it.")
        for mark, step, colour in (
            ("✓", "Create project folder", T.OK),
            ("✓", "Write package.json", T.OK),
            ("▸", "Create src/components/Navbar.tsx", T.ACCENT),
            ("○", "Create src/pages/Home.tsx", T.TEXT_FAINT),
            ("○", "Install dependencies", T.TEXT_FAINT),
            ("○", "Run the dev server", T.TEXT_FAINT),
        ):
            row = Row(f"{mark}   {step}", first=(mark == "✓" and "folder" in step))
            row.setStyleSheet(row.styleSheet() + f"QLabel {{ color: {colour}; }}"
                              if mark != "○" else row.styleSheet())
            plan.add(row)
        split.addWidget(plan, 1)

        timeline = Card("Activity", "Minute by minute.")
        for when, what in (
            ("21:40", "Created project folder"),
            ("21:42", "npm create vite@latest — exit 0"),
            ("21:44", "Wrote 6 files"),
            ("21:45", "Asked to install 3 packages"),
            ("21:45", "Approved by you"),
            ("21:47", "Installing dependencies…"),
        ):
            timeline.add(Row(what, when, first=(when == "21:40")))
        split.addWidget(timeline, 1)
        self.body.add_layout(split)

        waiting = Card("Waiting for you", "Nothing risky runs without a yes.")
        waiting.add(Row("Delete build/ and rebuild",
                        "file worker · delete_path · medium risk",
                        dot=T.WARN,
                        trailing=button("Review", "primary"), first=True))
        self.body.add(waiting)


# ── Extensions ────────────────────────────────────────────────────────────────

class ExtensionsPage(Page):
    HEADING = "Extensions"
    LEDE = "Industry packs. Each one adds its own agents, tools and dashboard — and nothing at all while it is off."

    def build(self) -> None:
        self.action("Browse packs", "primary")

        for name, blurb, on, extra in (
            ("Accounting", "Bookkeeping, bank reconciliation, invoice capture, "
                           "month-end and VAT. 61 tools · 8 agent templates.",
             True, "Enabled · ABC Manufacturing (Demo)"),
            ("Legal", "Contract review, clause extraction, matter tracking.",
             False, "Not installed"),
            ("Medical practice", "Scheduling, claims, patient correspondence.",
             False, "Not installed"),
            ("Property", "Listings, viewings, tenant paperwork.",
             False, "Not installed"),
        ):
            card = Card(name, blurb)
            foot = QHBoxLayout()
            foot.setSpacing(10)
            foot.addWidget(Pill(extra, T.OK if on else T.TEXT_FAINT, dot=on))
            foot.addStretch(1)
            if on:
                foot.addWidget(button("Open dashboard"))
            foot.addWidget(Switch(on), 0, Qt.AlignmentFlag.AlignVCenter)
            card.body.addLayout(foot)
            self.body.add(card)


# ── Accounting ────────────────────────────────────────────────────────────────

class AccountingPage(Page):
    HEADING = "Accounting"
    LEDE = "One client at a glance. Exceptions first — everything clean is already done."

    def build(self) -> None:
        self.action("Run month-end", "primary")

        head = Card()
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(Select("ABC Manufacturing (Demo)", "Karoo Coffee Roasters",
                             "+ New client…", width=250))
        row.addWidget(Select("September 2026", "August 2026", "July 2026",
                             width=170))
        row.addWidget(Pill("ZAR · South Africa", T.TEXT_DIM))
        row.addWidget(Pill("Mock provider", T.WARN))
        row.addStretch(1)
        head.body.addLayout(row)
        self.body.add(head)

        progress = Card("Month-end", "Five of nine workflows have run.")
        pl = QVBoxLayout()
        pl.setSpacing(8)
        meta = QHBoxLayout()
        meta.addWidget(label("Bank reconciliation · 62 of 140 lines matched",
                             "faint"), 1)
        meta.addWidget(label("56%", "faint"))
        pl.addLayout(meta)
        pl.addWidget(Meter(0.56))
        progress.body.addLayout(pl)
        self.body.add(progress)

        self.body.add_layout(columns(
            Metric("18", "need attention", T.WARN),
            Metric("122", "posted clean", T.OK),
            Metric("4", "awaiting approval", T.ACCENT),
            Metric("R 48,210", "unreconciled"),
        ))

        attn = Card("Needs attention", "Most severe first.",
                    action=button("Export"))
        for what, why, colour in (
            ("Duplicate payment · Nkosi Supplies R12,400",
             "Two payments, same reference, four days apart", T.DANGER),
            ("VAT on a zero-rated supply · Invoice 20261 ",
             "R1,830 claimed where the supplier is not registered", T.DANGER),
            ("Unmatched deposit · R6,500 on 14 Sep",
             "No invoice within R50 of this amount", T.WARN),
            ("Supplier invoice missing · Cape Freight",
             "Payment posted with no document attached", T.WARN),
            ("Coding confidence 42% · “ADT services”",
             "Could be security or IT — ask before posting", T.ACCENT),
        ):
            attn.add(Row(what, why, dot=colour,
                         trailing=button("Resolve"),
                         first=what.startswith("Duplicate")))
        self.body.add(attn)

        flows = Card("Workflows", "Run one now, or leave it on a schedule.")
        for name, when in (
            ("Bank reconciliation", "Nightly at 02:00"),
            ("Invoice capture from Inbox\\Bank", "On new file"),
            ("Supplier statement matching", "Weekly, Mondays"),
            ("VAT return preparation", "Bi-monthly"),
            ("Management pack", "Month-end"),
        ):
            flows.add(Row(name, when, trailing=button("Run now"),
                          first=name.startswith("Bank")))
        self.body.add(flows)


# ── Journal ───────────────────────────────────────────────────────────────────

class JournalPage(Page):
    HEADING = "Journal"
    LEDE = "What you have learned, what you asked, and the walkthroughs worth keeping."

    def build(self) -> None:
        self.action("Open folder")
        self.action("Attach document", "primary")

        due = Card("Due for review", "Spaced repetition — three questions today.")
        for q, when in (
            ("How do you share a single window in Google Meet?", "learned 3 days ago"),
            ("Where is display scaling in Windows Settings?", "learned 6 days ago"),
            ("What does a VAT zero-rated supply mean?", "learned 12 days ago"),
        ):
            due.add(Row(q, when, trailing=button("Quiz me"),
                        first=q.startswith("How do you")))
        self.body.add(due)

        recent = Card("Recent entries")
        for what, when in (
            ("Google Meet — adding people to a call", "Yesterday, 18:55"),
            ("VS Code — installing an extension", "Yesterday, 20:21"),
            ("Takealot — filtering by price", "21 Sep, 14:25"),
        ):
            recent.add(Row(what, when, first=what.startswith("Google")))
        self.body.add(recent)

        split = QHBoxLayout()
        split.setSpacing(T.GAP)
        rec = Card("Lesson recording",
                   "Record a walkthrough once; replay it for someone else.")
        rec.body.addWidget(button("Start recording", "primary"))
        split.addWidget(rec, 1)
        cap = Card("Workflow capture",
                   "Do the task yourself and Tora writes down the steps.")
        cap.body.addWidget(button("Capture my clicks", "primary"))
        split.addWidget(cap, 1)
        self.body.add_layout(split)


# ── Tutor ─────────────────────────────────────────────────────────────────────

class TutorPage(Page):
    HEADING = "Tutor"
    LEDE = "How Tora teaches: how she paces a walkthrough, what she looks at, and what she always keeps in mind."
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
    LEDE = "Which voice speaks, how she talks, which model does each job, and where agent requests go."
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
            from PyQt6.QtWidgets import QLabel, QPushButton
            from agents.ui.accounts import ConnectedAccountsDialog
            embedded = ConnectedAccountsDialog(wf)
            for b in embedded.findChildren(QPushButton):
                if b.text().strip().lower() == "close":
                    b.hide()
            for lbl in embedded.findChildren(QLabel):
                if lbl.objectName() == "title":
                    lbl.hide()
            embedded.setMinimumHeight(420)
            ends.body.addWidget(embedded)
        else:
            ends.add(Row("The agents did not start",
                         "Endpoints can be set once they do - see tora.log.",
                         dot=T.WARN, first=True))
        self.body.add(ends)


# ── Settings ──────────────────────────────────────────────────────────────────

class SettingsPage(Page):
    HEADING = "Settings"
    LEDE = "The rails. What Tora may touch, what runs without asking, and how she hears you."
    LIVE = True

    def build(self) -> None:
        br = self.bridge

        # The workforce's own settings widget - the one "Agent settings…" in
        # the tray opens: workspace, what runs without asking, plan first,
        # how many at once. It saves to config AND applies to the running
        # agents, so it is embedded rather than re-implemented (APP-11).
        rails = Card("Agent rails", "Agents may only ever write inside the workspace.")
        wf = br.workforce()
        if wf is not None:
            from PyQt6.QtWidgets import QLabel, QPushButton
            from agents.ui.settings import AgentSettingsDialog
            embedded = AgentSettingsDialog(wf)
            # Inside a page its own title repeats the card's, and its Close
            # would hide it and leave an empty card.
            for b in embedded.findChildren(QPushButton):
                if b.text().strip().lower() == "close":
                    b.hide()
            for lbl in embedded.findChildren(QLabel):
                if lbl.text().strip() == "Agent settings":
                    lbl.hide()
            rails.body.addWidget(embedded)
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

        look = Card("Tora on this PC")
        panel = ToggleRow("Show the floating panel",
                          "The small window with the conversation in it.",
                          br.panel_visible(), first=True)
        panel.switch.toggled.connect(br.set_panel_visible)
        look.add(panel)
        start = ToggleRow("Start Tora when Windows starts",
                          "For you only - no admin rights needed.", br.autostart())
        start.switch.toggled.connect(br.set_autostart)
        look.add(start)
        self.body.add(look)


# ── Setup ─────────────────────────────────────────────────────────────────────

class SetupPage(Page):
    HEADING = "Setup"
    LEDE = "Keys, first-run checks and a report you can send when something is wrong."

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
        quit_ = button("Quit Tora", "danger")
        quit_.clicked.connect(br.quit)
        foot.addWidget(quit_)
        wrap = QWidget()
        wrap.setLayout(foot)
        self.body.add(wrap)


# The nav order, and the single place a new page gets added.
PAGES = (
    ("home",       "Home",            "◆", HomePage,       "MAIN"),
    ("agents",     "Agents",          "◈", AgentsPage,     "MAIN"),
    ("extensions", "Extensions",      "▣", ExtensionsPage, "MAIN"),
    ("accounting", "Accounting",      "▤", AccountingPage, "WORK"),
    ("journal",    "Journal",         "▥", JournalPage,    "WORK"),
    ("tutor",      "Tutor",           "◉", TutorPage,      "TUNING"),
    ("voice",      "Voice & models",  "◍", VoicePage,      "TUNING"),
    ("settings",   "Settings",        "⚙", SettingsPage,   "SYSTEM"),
    ("setup",      "Setup",           "⛭", SetupPage,      "SYSTEM"),
)
