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
    """Title, one line of explanation, optional buttons, then a scrolling body."""

    HEADING = "Page"
    LEDE = ""

    def __init__(self):
        super().__init__()
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
        self.build()
        self.body.finish()

    def build(self) -> None:          # pragma: no cover - overridden
        pass

    def action(self, text: str, kind: str = "ghost"):
        b = button(text, kind)
        self.actions.addWidget(b)
        return b


# ── Home ──────────────────────────────────────────────────────────────────────

class HomePage(Page):
    HEADING = "Tora"
    LEDE = "Press Ctrl+Win and talk. She can see your screen, point at things, and put agents to work."

    def build(self) -> None:
        self.action("Open panel")
        self.action("Ask something", "primary")

        hero = Card()
        row = QHBoxLayout()
        row.setSpacing(16)
        row.addWidget(Dot(T.OK, 12, glow=True), 0, Qt.AlignmentFlag.AlignVCenter)
        text = QVBoxLayout()
        text.setSpacing(3)
        text.addWidget(label("Listening for “Tora”", "cardTitle"))
        text.addWidget(label("Ambient mode · Realme Buds · wake word on",
                             "faint"))
        row.addLayout(text, 1)
        row.addWidget(Pill("Connected", T.OK, dot=True))
        hero.body.addLayout(row)
        self.body.add(hero)

        self.body.add_layout(columns(
            Metric("2", "agents working", T.ACCENT),
            Metric("18", "open exceptions", T.WARN),
            Metric("$0.79", "spent today"),
            Metric("41", "questions answered"),
        ))

        work = Card("Happening now", "Everything running in the background.",
                    action=button("See all"))
        work.add(Row("Application Builder", "Step 4 of 10 · Creating Navbar.tsx",
                     dot=T.ACCENT, trailing=Pill("Running", T.ACCENT), first=True))
        work.add(Row("Month-end · ABC Manufacturing",
                     "Bank reconciliation · 62 of 140 lines matched",
                     dot=T.ACCENT, trailing=Pill("Running", T.ACCENT)))
        work.add(Row("Invoice inbox watch", "Waiting for new files in Inbox\\Bank",
                     dot=T.TEXT_FAINT, trailing=Pill("Idle", T.TEXT_FAINT)))
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
    LEDE = "How Tora teaches: how much she explains, how fast she moves, and what she is allowed to look at."

    def build(self) -> None:
        how = Card("Teaching style")
        for i, (name, sub, on) in enumerate((
            ("Slow mode", "Shorter steps, more checking in. For someone new to the machine.", False),
            ("Advance by itself", "Move to the next step when she sees you do it, without waiting for “next”.", True),
            ("Quiz mode", "Ask a question back at the end, so it sticks.", False),
            ("Code mode", "Notice when you are in an editor and explain like a developer.", True),
            ("Answer in my language", "Reply in whatever language you speak to her in.", True),
        )):
            how.add(ToggleRow(name, sub, on, first=(i == 0)))
        self.body.add(how)

        eyes = Card("Eyes on screen")
        eyes.add(ToggleRow("Privacy guard",
                           "Never send a screenshot when a password manager, "
                           "banking page or private window is in front.",
                           True, first=True))
        eyes.add(ToggleRow("Read text on screen (OCR)",
                           "Find things by the words on them when the app does "
                           "not expose its controls. Runs on this machine.",
                           True))
        eyes.add(ToggleRow("Web search",
                           "Look things up for news, prices and releases.", True))
        self.body.add(eyes)

        scope = Card("What she may see",
                     "Applies to every screenshot, every look and every agent.")
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(Select("The window in front", "This monitor",
                             "Every monitor", width=230))
        row.addWidget(label("Everything else is never captured.", "faint"), 1)
        scope.body.addLayout(row)
        self.body.add(scope)


# ── Voice & models ────────────────────────────────────────────────────────────

class VoicePage(Page):
    HEADING = "Voice & models"
    LEDE = "Which model answers, which voice speaks, and where the requests go."

    def build(self) -> None:
        self.action("Refresh model list")

        voice = Card("Voice")
        for name, value in (("Spoken voice", "Zephyr"),
                            ("Conversation", "gemini-3.8-live"),
                            ("Wake word", "gemini-3.5-transcribe-live")):
            voice.add(Row(name, trailing=Select(value, width=270),
                          first=name == "Spoken voice"))
        self.body.add(voice)

        models = Card("Models", "One job each — the fast one looks, the careful "
                                "one thinks, the planner writes the steps.")
        for job, model, note in (
            ("Quick look at the screen", "gemini-3.8-flash", "~1 s"),
            ("Careful look", "gemini-3.1-pro", "~3 s, much better"),
            ("Walkthrough planner", "gemini-3.8-flash", "3.8 s"),
            ("Agents", "claude-sonnet-4.6", "via OmniRoute"),
        ):
            models.add(Row(job, note, trailing=Select(model, width=230),
                           first=job.startswith("Quick")))
        self.body.add(models)

        ends = Card("Endpoints", "Where agent requests go.",
                    action=button("Add endpoint", "primary"))
        for name, url, on in (
            ("OmniRoute", "https://api.omniroute.ai/v1", True),
            ("OpenAI", "https://api.openai.com/v1", False),
            ("Local (Ollama)", "http://127.0.0.1:11434/v1", False),
        ):
            trail = QWidget()
            tl = QHBoxLayout(trail)
            tl.setContentsMargins(0, 0, 0, 0)
            tl.setSpacing(8)
            tl.addWidget(button("Test"))
            tl.addWidget(Switch(on))
            ends.add(Row(name, url, trailing=trail, first=name == "OmniRoute"))
        self.body.add(ends)


# ── Settings ──────────────────────────────────────────────────────────────────

class SettingsPage(Page):
    HEADING = "Settings"
    LEDE = "The rails. What Tora may touch, what runs without asking, and how she hears you."

    def build(self) -> None:
        rails = Card("Agent rails",
                     "Agents may only ever write inside the workspace.")
        # Read from the machine, never hard-coded: this ships to other
        # people's PCs, and one developer's home folder baked into the
        # settings page is how a product announces it was never tested
        # anywhere else.
        import os
        ws = QLineEdit(os.path.join(os.path.expanduser("~"), "Documents",
                                    "Tora Projects"))
        ws.setReadOnly(True)
        pick = QWidget()
        pl = QHBoxLayout(pick)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(8)
        pl.addWidget(ws, 1)
        pl.addWidget(button("Change"))
        rails.body.addWidget(label("Workspace", "faint"))
        rails.body.addWidget(pick)
        rails.body.addWidget(spacer(6))

        auto = Select("Safe only", "Safe and low risk", "Up to medium risk",
                      "Ask me every time", width=250)
        auto.setCurrentIndex(1)
        rails.add(Row("Runs without asking",
                      "Anything above this level stops and waits for you.",
                      trailing=auto))
        rails.add(ToggleRow("Show me the plan first",
                            "Nothing runs until you have read the steps.", True))
        rails.add(ToggleRow("Confirm every click on my desktop",
                            "Opening an app, pressing a media key, typing into "
                            "a window — always ask.", True))
        self.body.add(rails)

        mic = Card("Microphone")
        dev = Select("Realme Buds Air 5 Pro (Hands-Free)", "Microphone Array",
                     "System default", width=290)
        mic.add(Row("Input device", trailing=dev, first=True))
        mic.add(ToggleRow("Always listening",
                          "Wake on “Tora”. Off means she only hears you while "
                          "the hotkey is held.", True))
        hk = QLineEdit("Ctrl + Win")
        hk.setFixedWidth(160)
        mic.add(Row("Hold to talk", trailing=hk))
        self.body.add(mic)

        panel = Card("Appearance")
        panel.add(ToggleRow("Show the floating panel", "", True, first=True))
        panel.add(ToggleRow("Draw on screen while explaining",
                            "Arrows, circles and labels over what she is "
                            "talking about.", True))
        panel.add(ToggleRow("Start Tora when Windows starts", "", False))
        self.body.add(panel)


# ── Setup ─────────────────────────────────────────────────────────────────────

class SetupPage(Page):
    HEADING = "Setup"
    LEDE = "Keys, first-run checks and a report you can send when something is wrong."

    def build(self) -> None:
        keys = Card("API keys", "Stored in .env on this machine. Never in the app.")
        for name, state, colour in (
            ("Google · Gemini", "Connected", T.OK),
            ("OmniRoute · agents", "Connected", T.OK),
            ("Tavily · web search", "Not set — using the free search", T.TEXT_FAINT),
        ):
            field = QLineEdit("••••••••••••••••••••")
            field.setEchoMode(QLineEdit.EchoMode.Password)
            field.setFixedWidth(220)
            trail = QWidget()
            tl = QHBoxLayout(trail)
            tl.setContentsMargins(0, 0, 0, 0)
            tl.setSpacing(8)
            tl.addWidget(field)
            tl.addWidget(button("Test"))
            keys.add(Row(name, state, dot=colour, trailing=trail,
                         first=name.startswith("Google")))
        self.body.add(keys)

        checks = Card("Diagnostics", "What is working right now.")
        for name, state, colour in (
            ("Microphone", "Realme Buds Air 5 Pro · 16 kHz", T.OK),
            ("Live session", "Open · gemini-3.8-live", T.OK),
            ("Agent workforce", "Ready · 6 workers · 1 extension", T.OK),
            ("Screen capture", "3 monitors · DPI 1.5, 1.0, 1.0", T.OK),
            ("Local OCR", "RapidOCR ready", T.OK),
        ):
            checks.add(Row(name, state, dot=colour,
                           first=name == "Microphone"))
        self.body.add(checks)

        foot = QHBoxLayout()
        foot.setSpacing(10)
        foot.addWidget(button("Run the setup wizard again"))
        foot.addWidget(button("Save diagnostics report", "primary"))
        foot.addStretch(1)
        foot.addWidget(button("Quit Tora", "danger"))
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
