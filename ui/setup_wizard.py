"""
The first launch: sign in, get to know the user, and prove it all works.

Shown once on the first launch (and from the tray's "Run setup again"), and
whenever Tora has no way to reach Gemini. Its shape is docs/ui/DESIGN.md §7,
after Wispr Flow's onboarding: five phases across the top, question screens
beside a picture that answers back, and test screens the user passes by
doing the real thing - talking, holding the shortcut, asking Tora about a
screen and watching her point.

    Sign up       welcome · connect · work · help
    Permissions   control · mic
    Set up        ready · shortcut
    Learn         ask · show · payoff
    Personalize   personal · done

It runs in two halves. Up to the mic test it needs nothing but the window:
the sign-in gate (ui/sign_in_gate.py) shows it before the overlay, the
hotkey or the voice exist. "ready" is the seam: the gate lets the app start,
main.py hands the running app in through attach(), and the same window
carries on with the real shortcut and the real cursor. Opened from a
running app, attach() has already happened and "ready" is skipped.

"Connect" is the one that matters: sign in to the HeyTora account made on
the website (an emailed link, no password). It cannot be skipped - no
account, no HeyTora - and Gemini then runs through HeyTora's server on that
account's plan. Once signed in, the user may think with their own Gemini key
instead. A build without HeyTora's service filled in (account/settings.py)
has no accounts and offers only the key.

Network work (sending the code, signing in, checking a key) runs on worker
threads and reports back through signals: the window never freezes.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog, QFrame, QGridLayout, QHBoxLayout, QLineEdit, QPushButton, QStackedWidget,
    QVBoxLayout, QWidget,
)

from config import cfg
from ui import app_theme as T
from ui import onboarding_parts as O
from ui.app_widgets import Meter, Select, ToggleRow, label


# Marker file: the wizard skips itself if this exists.
def _flag_path() -> Path:
    from config import data_dir
    return data_dir() / "setup_complete.flag"


def setup_already_ran() -> bool:
    return _flag_path().exists()


def mark_setup_complete() -> None:
    try:
        _flag_path().write_text("ok")
    except Exception:
        pass


def _pretty_hotkey() -> str:
    return " + ".join(p.strip().capitalize() for p in cfg.hotkey.split("+"))


# What the user does - chips. Each brings forward a pack on the picture side,
# and the answer goes into Tora's instructions (cfg.profile).
WORK = (
    ("Accounting", "ledger"),
    ("Business owner", "briefcase"),
    ("Student", "cap"),
    ("Teacher", "cap"),
    ("Developer", "code"),
    ("Sales", "chart"),
    ("Design & video", "video"),
    ("Admin & office", "calendar"),
    ("Writing", "mic"),
    ("Something else", "wallet"),
)
USES = tuple(w for w, _k in WORK)

# The picture side of "work": the website's pack chips, with what you'd say.
PACKS = (
    ("ledger", "Accounting", "Reconcile March for me"),
    ("briefcase", "Business", "Draft the quote for Contoso"),
    ("cap", "Students", "Explain question three"),
    ("code", "Developers", "Why is this test failing?"),
    ("chart", "Sales", "Update the pipeline"),
    ("video", "Creators", "Where's the export setting?"),
)

# What the user wants help with - option cards, and the capability cards
# that come into colour beside them.
HELP = (
    ("ask", "Ask", "Ask about anything on screen", "Out loud, about whatever you're looking at.",
     "What's this error?"),
    ("show", "Show me", "Point me to it", "Tora points at the button, the menu or the cell.",
     "Where's the export?"),
    ("teach", "Teach me", "Walk me through it", "Step by step, waiting for you at each one.",
     "Walk me through charts"),
    ("do", "Do it", "Do it for me", "Agents take on whole jobs, and ask before anything big.",
     "Send these invoices"),
)

# Three things to try, by what the user does. Generic ones fill the rest.
TRY = {
    "Accounting": "Where do I reconcile this account?",
    "Business owner": "Draft a reply to this email for me.",
    "Student": "Explain this paragraph more simply.",
    "Teacher": "Turn this page into five quiz questions.",
    "Developer": "Why is this line underlined red?",
    "Sales": "Summarise this thread before my call.",
    "Design & video": "Where's the export setting in here?",
    "Admin & office": "Show me how to share this with the team.",
    "Writing": "Make this paragraph shorter.",
}
TRY_ALWAYS = ("Where do I change the font size?", "What does this button do?",
              "Show me how to add a chart here.")

PAGES = ("welcome", "connect", "work", "help", "control", "mic",
         "ready", "shortcut", "ask", "show", "payoff", "personal", "done")
PHASE = {"welcome": 0, "connect": 0, "work": 0, "help": 0, "control": 1, "mic": 1,
         "ready": 2, "shortcut": 2, "ask": 3, "show": 3, "payoff": 3,
         "personal": 4, "done": 4}
LIVE_PAGES = ("shortcut", "ask", "show", "payoff")   # need the running app


def _check_own_key(key: str) -> tuple[bool, str]:
    """Does this Gemini key work? Asked straight of Google, with the key."""
    import httpx
    try:
        r = httpx.get("https://generativelanguage.googleapis.com/v1beta/models",
                      params={"key": key}, timeout=12.0)
    except httpx.HTTPError:
        return False, "Couldn't reach Google - check the internet connection."
    if r.status_code == 200:
        return True, "Key works."
    return False, ("Google didn't accept that key. Copy it again in full from "
                   "aistudio.google.com/app/apikey.")


class SetupWizard(QDialog):
    """The first launch, one screen at a time."""

    completed = pyqtSignal()                       # finished: access may have changed
    ready = pyqtSignal()                           # the first half is done: the app may start
    _result = pyqtSignal(str, bool, str)           # (job, ok, message) from a worker thread
    _hotkey = pyqtSignal(bool)                     # the shortcut, from the hook's thread

    def __init__(self, parent=None, *, account=None, start: str = "welcome",
                 check_key: Callable[[str], tuple[bool, str]] = _check_own_key):
        super().__init__(parent)
        self.setWindowTitle("HeyTora")
        self.setModal(False)
        self.resize(1080, 700)
        self.setMinimumSize(980, 640)
        self.setStyleSheet(T.qss() + O.stylesheet())
        if account is None:
            from account.service import account as _app_account
            account = _app_account()
        self._account = account
        from account import settings
        self._gated = settings.configured()        # HeyTora accounts: signing in is a must
        self._signin_only = start == "connect" and setup_already_ran()
        self._check_key = check_key
        self._email = ""
        self._connected = cfg.has_key()
        self._level = 0.0
        self._stream = None
        self._catcher = None                       # account.loopback.LinkCatcher, while signing in
        self._live = None                          # the running app, once attach()ed
        self._asked_at = 0.0                       # when the question ended (Learn screens)
        self._pointed_after: Optional[float] = None
        self._steps_walked = 0
        self._show_done = False
        self._finished = False

        self._continue: dict[str, QPushButton] = {}
        self._backs: dict[str, QPushButton] = {}
        self._skips: dict[str, QPushButton] = {}
        self._none = QPushButton()                 # stands in where a page has no such button
        self._none.hide()

        self._result.connect(self._on_result)
        self._hotkey.connect(self._on_hotkey)
        self._build()
        self._go(start if start in PAGES else "welcome")

    # ── the buttons tests and callers reach for ──────────────────────────────

    @property
    def next_btn(self) -> QPushButton:
        return self._continue.get(self.page(), self._none)

    @property
    def back_btn(self) -> QPushButton:
        return self._backs.get(self.page(), self._none)

    @property
    def skip_btn(self) -> QPushButton:
        return self._skips.get(self.page(), self._none)

    # ── layout ────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.phases = O.PhaseBar()
        outer.addWidget(self.phases)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.pages: dict[str, QWidget] = {}
        for name in PAGES:
            page = getattr(self, f"_page_{name}")()
            self.pages[name] = page
            self.stack.addWidget(page)

    def _question(self, name: str, family) -> tuple[QVBoxLayout, O.GradientPanel, QWidget]:
        """A question screen: the white column on the left, the picture on the right."""
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        column = QWidget()
        column.setObjectName("column")
        column.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        column.setFixedWidth(460)
        col = QVBoxLayout(column)
        col.setContentsMargins(56, 36, 56, 40)
        col.setSpacing(0)
        back = self._back_button(name)
        col.addWidget(back, 0, Qt.AlignmentFlag.AlignLeft)
        col.addSpacing(24)
        body = QVBoxLayout()
        body.setSpacing(14)
        col.addLayout(body)
        col.addStretch(1)
        go = QPushButton("Continue")
        go.setObjectName("continue")
        go.setCursor(Qt.CursorShape.PointingHandCursor)
        go.clicked.connect(self._next)
        col.addWidget(go)
        self._continue[name] = go
        row.addWidget(column)
        panel = O.GradientPanel(family)
        row.addWidget(panel, 1)
        return body, panel, page

    def _test(self, name: str, *, skip: bool = False, dark: bool = False,
              go_words: str = "") -> tuple[QVBoxLayout, QWidget]:
        """A test screen: centred words over the swoosh, then the card."""
        page = O.Swoosh(dark)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(56, 30, 56, 36)
        lay.setSpacing(0)
        top = QHBoxLayout()
        top.addWidget(self._back_button(name, dark=dark))
        top.addStretch(1)
        if skip:
            s = QPushButton("Skip")
            s.setObjectName("skip")
            s.setCursor(Qt.CursorShape.PointingHandCursor)
            s.clicked.connect(self._skip)
            top.addWidget(s)
            self._skips[name] = s
        lay.addLayout(top)
        lay.addSpacing(24)
        body = QVBoxLayout()
        body.setSpacing(12)
        body.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addLayout(body)
        lay.addStretch(1)
        if go_words:
            go = QPushButton(go_words)
            go.setObjectName("continue")
            go.setCursor(Qt.CursorShape.PointingHandCursor)
            go.setFixedWidth(360)
            go.clicked.connect(self._next)
            if dark:
                go.setStyleSheet("QPushButton#continue { background: #F4F3F0; color: #141414; }"
                                 "QPushButton#continue:hover { background: #FFFFFF; }")
            lay.addWidget(go, 0, Qt.AlignmentFlag.AlignHCenter)
            self._continue[name] = go
        return body, page

    def _back_button(self, name: str, dark: bool = False) -> QPushButton:
        b = QPushButton("←  Back")
        b.setObjectName("back")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(self._back)
        if dark:
            b.setStyleSheet("QPushButton#back { color: #A1A1A1; } QPushButton#back:hover { color: #FAFAFA; }")
        self._backs[name] = b
        return b

    @staticmethod
    def _centre(widget: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(widget)
        row.addStretch(1)
        return row

    # ── Sign up ───────────────────────────────────────────────────────────────

    def _page_welcome(self) -> QWidget:
        body, panel, page = self._question("welcome", O.CAPABILITY["ask"])
        body.addWidget(O.heading("Hi, I'm Tora."))
        body.addWidget(O.line("I sit next to your cursor. Ask about anything on your screen, "
                              "out loud, and I'll answer and point at it."))
        body.addWidget(O.line("A few quick questions, a couple of tries, and we're set."))
        self._continue["welcome"].setText("Get started")
        cards = QGridLayout()
        cards.setSpacing(18)
        for i, (key, title, *_rest, example) in enumerate(HELP[:2]):
            card = O.CapabilityCard(key, title, example)
            card.set_on(True)
            cards.addWidget(card, 0, i)
        panel.body.addLayout(cards)
        return page

    def _page_connect(self) -> QWidget:
        from account import settings
        body, panel, page = self._question("connect", O.STEP[0])
        if self._gated:
            body.addWidget(O.heading("Sign in to HeyTora."))
            body.addWidget(O.line("With the account you made on heytora.com. We email you a "
                                  "link: click it and you're in."))
        else:
            body.addWidget(O.heading("Connect to Gemini."))
            body.addWidget(O.line("Tora thinks with Google's Gemini. Add your own Gemini key."))
        body.addSpacing(8)
        self.connect_status = label("", "faint", wrap=True)

        if settings.configured():
            self.email_edit = QLineEdit(self._account.email)
            self.email_edit.setPlaceholderText("you@example.com")
            self.send_btn = QPushButton("Send link")
            self.send_btn.setObjectName("ghost")
            self.send_btn.clicked.connect(self._send_code)
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(self.email_edit, 1)
            row.addWidget(self.send_btn)
            body.addLayout(row)
            self.code_row = QWidget()
            code_lay = QHBoxLayout(self.code_row)
            code_lay.setContentsMargins(0, 0, 0, 0)
            code_lay.setSpacing(8)
            self.code_edit = QLineEdit()
            self.code_edit.setPlaceholderText("Or type the code from the email")
            self.code_edit.setMaxLength(12)          # 6-10 digits, maybe pasted with spaces
            self.signin_btn = QPushButton("Sign in")
            self.signin_btn.setObjectName("ghost")
            self.signin_btn.clicked.connect(self._sign_in)
            code_lay.addWidget(self.code_edit, 1)
            code_lay.addWidget(self.signin_btn)
            self.code_row.hide()
            body.addWidget(self.code_row)
            if settings.website_url():
                make = QPushButton("No account yet? Create one")
                make.setObjectName("link")
                make.setCursor(Qt.CursorShape.PointingHandCursor)
                make.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(settings.website_url())))
                body.addWidget(make, 0, Qt.AlignmentFlag.AlignLeft)
        else:
            self.email_edit = self.code_edit = None

        # The own key: after the account, never instead of it.
        self.key_card = QFrame()
        kl = QVBoxLayout(self.key_card)
        kl.setContentsMargins(0, 14, 0, 0)
        kl.setSpacing(8)
        kl.addWidget(O.text("Or think with your own Gemini key" if self._gated else
                            "Your Gemini key", 13, 600))
        kl.addWidget(O.text("Free from aistudio.google.com/app/apikey. Straight to Google, "
                            "no plan limits." if self._gated else
                            "Free from aistudio.google.com/app/apikey.", 12, 400, T.TEXT_DIM))
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("Paste your Gemini API key")
        self.key_btn = QPushButton("Use key")
        self.key_btn.setObjectName("ghost")
        self.key_btn.clicked.connect(self._use_key)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.key_edit, 1)
        row.addWidget(self.key_btn)
        kl.addLayout(row)
        body.addWidget(self.key_card)
        body.addWidget(self.connect_status)

        card = O.CapabilityCard("ask", "Ask", "Where do I change the margins?")
        card.set_on(True)
        panel.body.addWidget(card, 0, Qt.AlignmentFlag.AlignCenter)
        return page

    def _page_work(self) -> QWidget:
        body, panel, page = self._question("work", O.STEP[1])
        body.addWidget(O.heading("Tell us about yourself."))
        body.addSpacing(4)
        body.addWidget(O.text("What should Tora call you?", 14, 600))
        self.name_edit = QLineEdit(cfg.profile()[0])
        self.name_edit.setPlaceholderText("Your first name")
        self.name_edit.setMaxLength(40)
        body.addWidget(self.name_edit)
        body.addSpacing(6)
        body.addWidget(O.text("What do you do? Pick any.", 14, 600))
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        chosen = set(cfg.profile()[1])
        self.use_chips: list[QPushButton] = []
        for i, (work, _kind) in enumerate(WORK):
            c = O.chip(work)
            c.setProperty("use", work)
            c.setChecked(work in chosen)
            c.toggled.connect(self._work_changed)
            grid.addWidget(c, i // 2, i % 2)
            self.use_chips.append(c)
        body.addLayout(grid)

        self.pack_cards: list[O.PackCard] = []
        stack = QVBoxLayout()
        stack.setSpacing(12)
        for i, (kind, name, phrase) in enumerate(PACKS):
            card = O.PackCard(kind, name, phrase)
            self.pack_cards.append(card)
            row = QHBoxLayout()
            row.addSpacing(40 if i % 2 else 0)       # a little stagger, like the site's drift
            row.addWidget(card)
            row.addSpacing(0 if i % 2 else 40)
            stack.addLayout(row)
        panel.body.addLayout(stack)
        return page

    def _work_changed(self, *_a) -> None:
        kinds = {k for w, k in WORK for c in self.use_chips if c.isChecked() and c.property("use") == w}
        for card in self.pack_cards:
            card.set_on(not kinds or card.kind in kinds)
        if self.page() == "work":
            self.next_btn.setEnabled(bool(kinds))

    def _page_help(self) -> QWidget:
        body, panel, page = self._question("help", O.STEP[2])
        body.addWidget(O.heading("What should Tora help with?"))
        body.addWidget(O.line("Pick any. Everything stays available later."))
        body.addSpacing(6)
        self.help_cards: list[O.OptionCard] = []
        for key, _title, head, sub, _ex in HELP:
            card = O.OptionCard(head, sub, value=key, checked=key in ("ask", "show"))
            card.toggled.connect(self._help_changed)
            body.addWidget(card)
            self.help_cards.append(card)
        grid = QGridLayout()
        grid.setSpacing(18)
        self.capability_cards: list[O.CapabilityCard] = []
        for i, (key, title, *_r, example) in enumerate(HELP):
            cc = O.CapabilityCard(key, title, example)
            grid.addWidget(cc, i // 2, i % 2)
            self.capability_cards.append(cc)
        panel.body.addLayout(grid)
        self._help_changed()
        return page

    def _help_changed(self, *_a) -> None:
        on = {c.value for c in self.help_cards if c.isChecked()}
        for cc in self.capability_cards:
            cc.set_on(cc.key in on)
        if self.page() == "help":
            self.next_btn.setEnabled(bool(on))

    # ── Permissions ───────────────────────────────────────────────────────────

    def _page_control(self) -> QWidget:
        body, panel, page = self._question("control", O.CAPABILITY["do"])
        body.addWidget(O.heading("You stay in charge."))
        body.addWidget(O.line("Tora looks at your screen only when you ask. For anything she "
                              "does, you choose how much she checks with you first."))
        body.addSpacing(6)
        auto = cfg.switch("auto_approve")
        self.ask_first = O.OptionCard("Ask me first", "Tora shows what she's about to do and "
                                      "waits for your OK.", checked=not auto)
        self.go_ahead = O.OptionCard("Go ahead without asking", "Still asks, every time, before "
                                     "paying, sending, posting or deleting.", checked=auto)
        # One or the other, like radio buttons.
        self.ask_first.toggled.connect(lambda on: self.go_ahead.setChecked(not on))
        self.go_ahead.toggled.connect(lambda on: self.ask_first.setChecked(not on))
        body.addWidget(self.ask_first)
        body.addWidget(self.go_ahead)
        body.addWidget(O.text("You can change this any time in Settings.", 12, 400, T.TEXT_FAINT))

        # The picture: the approval box, as Tora shows it.
        box = QFrame()
        box.setObjectName("approval")
        box.setFixedWidth(340)
        box.setStyleSheet("QFrame#approval { background: #FFFFFF; border: 1px solid rgba(0,0,0,18);"
                          " border-radius: 16px; }")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(20, 18, 20, 18)
        bl.setSpacing(8)
        bl.addWidget(O.text("TORA WANTS TO", 10, 600, "#8A8884", spacing=1.5))
        bl.addWidget(O.text("Export March's invoices to Excel", 15, 700, O.INK_ON_PALE))
        bl.addWidget(O.text("Saves invoices-march.xlsx to your Documents.", 12, 400, "#5C5C5C"))
        btns = QHBoxLayout()
        btns.addStretch(1)
        no = QPushButton("Not now")
        no.setStyleSheet("QPushButton { background: #EBE9E5; border: none; border-radius: 14px;"
                         " color: #292929; padding: 6px 14px; font-weight: 600; }")
        yes = QPushButton("Allow")
        yes.setStyleSheet(f"QPushButton {{ background: {T.ACCENT}; border: none; border-radius: 14px;"
                          " color: #FFFFFF; padding: 6px 16px; font-weight: 700; }")
        btns.addWidget(no)
        btns.addWidget(yes)
        bl.addLayout(btns)
        panel.body.addWidget(box, 0, Qt.AlignmentFlag.AlignCenter)
        return page

    def _page_mic(self) -> QWidget:
        body, page = self._test("mic")
        body.addWidget(O.heading("Test your microphone.", centre=True))
        body.addWidget(O.line("Built-in or wired microphones work best. Bluetooth headsets "
                              "are less reliable.", centre=True))
        body.addSpacing(26)
        card = O.TestCard("Do the bars move when you talk?")
        self.mic_bars = O.MicBars()
        card.well_lay.addWidget(self.mic_bars)
        self.level_meter = Meter(0.0, height=0)       # kept for callers; the bars are the meter
        self.level_meter.hide()
        self.mic_select = Select(width=300)
        self.mic_select.hide()
        self.mic_ids: list[int] = []
        self.mic_note = label("", "faint", wrap=True)
        card.lay.insertWidget(2, self.mic_note)
        change = QPushButton("Change microphone")
        change.setObjectName("answer")
        change.setCursor(Qt.CursorShape.PointingHandCursor)
        change.clicked.connect(lambda: (self.mic_select.show(), change.hide()))
        yes = QPushButton("Yes")
        yes.setObjectName("answerYes")
        yes.setCursor(Qt.CursorShape.PointingHandCursor)
        yes.clicked.connect(self._next)
        card.foot.addWidget(self.mic_select)
        card.foot.addStretch(1)
        card.foot.addWidget(change)
        card.foot.addWidget(yes)
        self._continue["mic"] = yes
        body.addLayout(self._centre(card))
        return page

    # ── Set up ────────────────────────────────────────────────────────────────

    def _page_ready(self) -> QWidget:
        body, page = self._test("ready")
        self._backs["ready"].hide()
        body.addSpacing(120)
        body.addWidget(O.heading("Getting Tora ready.", centre=True))
        body.addWidget(O.line("This takes a few seconds.", centre=True))
        body.addSpacing(20)
        self.ready_meter = Meter(0.08, height=4)
        self.ready_meter.setFixedWidth(240)
        body.addLayout(self._centre(self.ready_meter))
        self._ready_timer = QTimer(self)
        self._ready_timer.setInterval(60)
        self._ready_tick = 0.08
        self._ready_timer.timeout.connect(self._ready_step)
        return page

    def _ready_step(self) -> None:
        # Creeps towards the end without reaching it; attach() finishes it.
        self._ready_tick += (0.92 - self._ready_tick) * 0.03
        self.ready_meter.set_fraction(self._ready_tick)

    def _page_shortcut(self) -> QWidget:
        body, page = self._test("shortcut")
        body.addWidget(O.heading("Test your shortcut.", centre=True))
        self.shortcut_line = O.line(f"Hold {_pretty_hotkey()} while you talk, and let go when "
                                    "you're done.", centre=True)
        body.addWidget(self.shortcut_line)
        body.addSpacing(26)
        card = O.TestCard("Does the key light up while you hold it?")
        self.keycap = O.Keycap(_pretty_hotkey())
        card.well_lay.addWidget(self.keycap, 0, Qt.AlignmentFlag.AlignCenter)
        self.shortcut_help = O.text("Another app may be using this shortcut. Close it and try "
                                    "again, or skip ahead and change the shortcut in Settings.",
                                    12, 400, T.TEXT_DIM)
        self.shortcut_help.hide()
        card.lay.insertWidget(2, self.shortcut_help)
        no = QPushButton("No")
        no.setObjectName("answer")
        no.setCursor(Qt.CursorShape.PointingHandCursor)
        no.clicked.connect(lambda: (self.shortcut_help.show(), self.next_btn.setEnabled(True)))
        yes = QPushButton("Yes")
        yes.setObjectName("answerYes")
        yes.setCursor(Qt.CursorShape.PointingHandCursor)
        yes.clicked.connect(self._next)
        card.foot.addStretch(1)
        card.foot.addWidget(no)
        card.foot.addWidget(yes)
        self._continue["shortcut"] = yes
        body.addLayout(self._centre(card))
        return page

    # ── Learn ─────────────────────────────────────────────────────────────────

    def _page_ask(self) -> QWidget:
        body, page = self._test("ask", skip=True, go_words="Continue")
        body.addWidget(O.heading("Ask Tora about your screen.", centre=True))
        body.addWidget(O.line(f"Hold {_pretty_hotkey()} and ask: “Where do I export this?”",
                              centre=True))
        body.addSpacing(22)
        self.ask_app = O.PretendApp()
        body.addLayout(self._centre(self.ask_app))
        body.addSpacing(14)
        self.ask_status = O.text("", 13, 600, T.TEXT_DIM, centre=True)
        body.addWidget(self.ask_status)
        return page

    def _page_show(self) -> QWidget:
        body, page = self._test("show", skip=True, go_words="Continue")
        body.addWidget(O.heading("Let Tora show you.", centre=True))
        body.addWidget(O.line(f"Hold {_pretty_hotkey()} and say: “Show me how to send the "
                              "Contoso invoice.”", centre=True))
        body.addSpacing(22)
        self.show_app = O.PretendApp()
        body.addLayout(self._centre(self.show_app))
        body.addSpacing(14)
        self.show_status = O.text("", 13, 600, T.TEXT_DIM, centre=True)
        body.addWidget(self.show_status)
        return page

    def _page_payoff(self) -> QWidget:
        body, page = self._test("payoff", dark=True, go_words="Continue")
        body.setAlignment(Qt.AlignmentFlag.AlignLeft)
        body.setContentsMargins(60, 10, 60, 0)
        body.addWidget(O.text("Nice job.", 72, 700, "#FAFAFA", spacing=-1.5, wrap=False))
        body.addSpacing(28)
        self.payoff_lines = QVBoxLayout()
        self.payoff_lines.setSpacing(22)
        body.addLayout(self.payoff_lines)
        return page

    def _fill_payoff(self) -> None:
        """Only what was measured in this session. Nothing projected, nothing
        averaged from elsewhere (heytora-web BRAND.md: no invented numbers)."""
        lay = self.payoff_lines
        while lay.count():
            w = lay.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        def stat(value: str, caption: str) -> QWidget:
            box = QWidget()
            v = QVBoxLayout(box)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(2)
            v.addWidget(O.text(caption.upper(), 11, 600, "#A1A1A1", spacing=1.5))
            v.addWidget(O.text(value, 44, 700, "#FAFAFA", spacing=-1.0, wrap=False))
            return box

        if self._pointed_after is not None:
            lay.addWidget(stat(f"{self._pointed_after:.1f} seconds",
                               "From your question to Tora pointing"))
        if self._steps_walked:
            lay.addWidget(stat(f"{self._steps_walked} steps", "Tora walked you through"))
        lay.addWidget(O.text("That's the whole idea: you never left the screen.", 16, 500, "#A1A1A1"))

    # ── Personalize ───────────────────────────────────────────────────────────

    def _page_personal(self) -> QWidget:
        body, panel, page = self._question("personal", O.STEP[3])
        body.addWidget(O.heading("Make Tora yours."))
        body.addWidget(O.line("All of these are in Settings too."))
        body.addSpacing(6)
        self.startup_row = ToggleRow("Start HeyTora when Windows starts",
                                     "For you only. No admin rights needed.", True, first=True)
        self.wake_row = ToggleRow("Always listening",
                                  "Wake on “Tora”. Off means she only hears you while the "
                                  "shortcut is held.", cfg.ambient_mic())
        self.panel_row = ToggleRow("Show the floating panel",
                                   "The small glass window with the conversation in it.", False)
        for row in (self.startup_row, self.wake_row, self.panel_row):
            body.addWidget(row)
        card = O.CapabilityCard("teach", "Teach me", "Walk me through pivot tables")
        card.set_on(True)
        panel.body.addWidget(card, 0, Qt.AlignmentFlag.AlignCenter)
        return page

    def _page_done(self) -> QWidget:
        body, panel, page = self._question("done", O.CAPABILITY["ask"])
        self.done_title = O.heading("You're set.")
        body.addWidget(self.done_title)
        self.done_text = O.line("")
        body.addWidget(self.done_text)
        body.addSpacing(8)
        self.try_list = QVBoxLayout()
        self.try_list.setSpacing(8)
        body.addLayout(self.try_list)
        body.addSpacing(14)
        hold = QHBoxLayout()
        hold.setSpacing(10)
        hold.addWidget(O.text("Hold", 14, 500, T.TEXT_DIM, wrap=False))
        hold.addWidget(O.Keycap(_pretty_hotkey(), px=14))
        hold.addWidget(O.text("and ask.", 14, 500, T.TEXT_DIM, wrap=False))
        hold.addStretch(1)
        body.addLayout(hold)
        self._continue["done"].setText("Open HeyTora")
        grid = QGridLayout()
        grid.setSpacing(18)
        for i, (key, title, *_r, example) in enumerate(HELP):
            cc = O.CapabilityCard(key, title, example)
            cc.set_on(True)
            grid.addWidget(cc, i // 2, i % 2)
        panel.body.addLayout(grid)
        return page

    def _fill_done(self) -> None:
        name = cfg.profile()[0]
        self.done_title.setText(f"You're set, {name}." if name else "You're set.")
        if not cfg.has_key():
            self.done_text.setText("You're not connected yet. Run setup again from the tray icon "
                                   "to sign in or add a key.")
        else:
            self.done_text.setText("Three things to try first:")
        while self.try_list.count():
            w = self.try_list.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        picks = [TRY[u] for u in cfg.profile()[1] if u in TRY][:1]
        for ask in (picks + [t for t in TRY_ALWAYS if t not in picks])[:3]:
            bubble = O.text(f"“{ask}”", 14, 500)
            bubble.setStyleSheet(f"color: {T.TEXT}; background: {T.BG}; border-radius: 10px;"
                                 " padding: 10px 14px;")
            self.try_list.addWidget(bubble)

    # ── navigation ──────────────────────────────────────────────────────────────

    def page(self) -> str:
        return PAGES[self.stack.currentIndex()]

    def _go(self, name: str) -> None:
        was = self.page()
        if was == "mic" and name != "mic":
            self._stop_mic()
        if was == "shortcut" and name != "shortcut":
            self._release_hotkey()
        if was == "ready":
            self._ready_timer.stop()
        self.stack.setCurrentWidget(self.pages[name])
        i = PAGES.index(name)
        self.phases.set_progress(PHASE[name], i / (len(PAGES) - 1))
        if name == "welcome":
            self._backs["welcome"].hide()
        if self._signin_only and name == "connect":
            self._backs["connect"].hide()
            self._continue["connect"].setText("Finish")
        go = self._continue.get(name)
        if go is not None:
            go.setEnabled(True)
        if name == "connect":
            self._refresh_connect()
        elif name == "work":
            self._work_changed()
        elif name == "help":
            self._help_changed()
        elif name == "mic":
            self._start_mic()
        elif name == "ready":
            self._ready_tick = 0.08
            self._ready_timer.start()
        elif name == "shortcut":
            self.keycap.set_lit(False)
            self.shortcut_help.hide()
            self.next_btn.setEnabled(False)          # proven by holding it
            self._claim_hotkey()
        elif name in ("ask", "show"):
            self._arm_learn(name)
        elif name == "payoff":
            self._fill_payoff()
        elif name == "done":
            self._fill_done()

    def _next(self) -> None:
        name = self.page()
        if name == "connect" and self._gated and not self._account.signed_in():
            self.connect_status.setText("Sign in first. HeyTora needs your account.")
            return
        if name == "connect" and self._signin_only:
            self._finish()
            return
        if name == "work":
            self._save_profile()
        elif name == "help":
            self._save_help()
        elif name == "control":
            cfg.set_switch("auto_approve", self.go_ahead.isChecked())
        elif name == "personal":
            self._save_personal()
        elif name == "done":
            self._finish()
            return
        nxt = PAGES[PAGES.index(name) + 1]
        if nxt == "ready" and self._live is not None:
            nxt = "shortcut"                         # the app is already running
        elif nxt == "ready":
            self._go("ready")
            self.ready.emit()                        # the gate lets the app start; attach() follows
            return
        if nxt == "payoff" and self._pointed_after is None and not self._steps_walked:
            nxt = "personal"                         # nothing was measured: no fake celebration
        self._go(nxt)

    def _skip(self) -> None:
        self._go({"ask": "show", "show": "payoff"}.get(self.page(), self.page()))
        if self.page() == "payoff" and self._pointed_after is None and not self._steps_walked:
            self._go("personal")

    def _back(self) -> None:
        i = PAGES.index(self.page())
        prev = PAGES[max(0, i - 1)]
        if prev == "ready":
            # Back past the seam goes to the mic test, if the app runs;
            # the gate's half cannot be re-entered once the app has started.
            prev = "mic"
        if prev == "payoff" and self._pointed_after is None and not self._steps_walked:
            prev = "show"
        self._go(prev)

    def _finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        mark_setup_complete()
        self._shutdown()
        self.completed.emit()
        self.close()

    def _shutdown(self) -> None:
        self._stop_mic()
        self._stop_catcher()
        self._release_hotkey()
        self._ready_timer.stop()

    def closeEvent(self, event) -> None:
        # Closed after the app started: the user is signed in and has seen
        # enough; don't bring the whole thing back next launch.
        if self._live is not None and not self._finished:
            mark_setup_complete()
        self._shutdown()
        super().closeEvent(event)

    def reject(self) -> None:                       # the window's X, or the gate giving up
        self._shutdown()
        super().reject()

    def keyPressEvent(self, event) -> None:
        # Esc would close the first launch - and, before a sign-in, the app.
        if event.key() == Qt.Key.Key_Escape:
            return
        super().keyPressEvent(event)

    # ── the running app (second half) ──────────────────────────────────────────

    def attach(self, live) -> None:
        """The app is running: `live` carries the shortcut and Tora's signals.

            live.claim_hotkey(down, up) / live.release_hotkey()
            live.state      pyqtSignal(AppState)
            live.point      pyqtSignal(float, float, str)
            live.guide      pyqtSignal(int, int, str)       (step, total, words)
            live.set_wake(bool), live.show_panel(bool)      optional

        Waiting on "ready", the window moves straight on to the shortcut test.
        """
        self._live = live
        try:
            live.state.connect(self._on_state)
            live.point.connect(self._on_point)
            live.guide.connect(self._on_guide)
        except Exception:  # noqa: BLE001 - a live with fewer signals still runs
            pass
        if self.page() == "ready":
            self.ready_meter.set_fraction(1.0)
            QTimer.singleShot(250, lambda: self._go("shortcut"))

    def _claim_hotkey(self) -> None:
        """While the shortcut test is up, holding the shortcut lights the key
        here instead of starting a conversation."""
        if self._live is not None and hasattr(self._live, "claim_hotkey"):
            self._live.claim_hotkey(lambda: self._hotkey.emit(True),
                                    lambda: self._hotkey.emit(False))

    def _release_hotkey(self) -> None:
        if self._live is not None and hasattr(self._live, "release_hotkey"):
            self._live.release_hotkey()

    def _on_hotkey(self, down: bool) -> None:
        if self.page() != "shortcut":
            return
        self.keycap.set_lit(down)
        if down:
            self.next_btn.setEnabled(True)

    def _arm_learn(self, name: str) -> None:
        status = self.ask_status if name == "ask" else self.show_status
        done = (self._pointed_after is not None) if name == "ask" else self._show_done
        self.next_btn.setEnabled(done)
        status.setText("Done. Tora found it." if done else "Waiting for you to ask…")

    def _on_state(self, state) -> None:
        name = getattr(state, "name", str(state)).upper()
        page = self.page()
        if page not in ("ask", "show"):
            return
        status = self.ask_status if page == "ask" else self.show_status
        if name == "LISTENING":
            status.setText("Listening…")
        elif name == "THINKING":
            self._asked_at = time.monotonic()        # the question has ended
            status.setText("Tora is looking at your screen…")
        elif name == "SPEAKING" and status.text().startswith("Tora is looking"):
            status.setText("Tora is answering…")
        elif name == "IDLE" and page == "show" and self._steps_walked and not self._show_done:
            self._show_done = True
            status.setText(f"Done. Tora walked you through {self._steps_walked} steps.")
            self.next_btn.setEnabled(True)

    def _on_point(self, *_args) -> None:
        if self.page() != "ask" or self._pointed_after is not None:
            return
        if self._asked_at:
            self._pointed_after = max(0.1, time.monotonic() - self._asked_at)
            self.ask_status.setText(f"Tora pointed in {self._pointed_after:.1f} seconds.")
        else:
            self._pointed_after = 0.0
            self.ask_status.setText("Tora pointed at it.")
        self.next_btn.setEnabled(True)

    def _on_guide(self, step: int = 0, total: int = 0, *_rest) -> None:
        if self.page() != "show":
            return
        self._steps_walked = max(self._steps_walked, int(total or step or 0))
        self.show_status.setText(f"Step {step} of {total}. Follow Tora's cursor.")
        if total and step >= total:
            self._show_done = True
            self.next_btn.setEnabled(True)

    # ── saving answers ────────────────────────────────────────────────────────

    def _save_profile(self) -> None:
        uses = [c.property("use") for c in self.use_chips if c.isChecked()]
        cfg.set_profile(self.name_edit.text(), uses)

    def _save_help(self) -> None:
        chosen = [c.value for c in self.help_cards if c.isChecked()]
        try:
            cfg._write_env("TORA_INTERESTS", ",".join(chosen))
        except Exception:  # noqa: BLE001 - a nicety
            pass

    def _save_personal(self) -> None:
        try:
            from ui import autostart
            autostart.set_enabled(self.startup_row.switch.isChecked())
        except Exception:  # noqa: BLE001 - a nicety, never a blocker
            pass
        live = self._live
        if live is not None:
            for attr, row in (("set_wake", self.wake_row), ("show_panel", self.panel_row)):
                fn = getattr(live, attr, None)
                if fn is not None:
                    try:
                        fn(row.switch.isChecked())
                    except Exception:  # noqa: BLE001
                        pass

    # kept for callers of the old first page
    def _save_startup(self) -> None:
        self._save_personal()

    # ── connect ─────────────────────────────────────────────────────────────────

    def _refresh_connect(self) -> None:
        signed_in = self._account.signed_in()
        if signed_in and cfg.access_mode() == "account":
            self.connect_status.setText(f"✓  Signed in as {self._account.email}.")
        elif cfg.access_mode() == "own_key" and cfg.google_api_key and (signed_in or not self._gated):
            who = f"Signed in as {self._account.email}, using" if signed_in else "Using"
            self.connect_status.setText(f"✓  {who} your own Gemini key.")
        else:
            self.connect_status.setText("")
        self._connect_locks()

    def _connect_locks(self) -> None:
        """Gated: no Continue, and no own key, until signed in."""
        if not self._gated:
            return
        signed_in = self._account.signed_in()
        if self.page() == "connect":
            self._continue["connect"].setEnabled(signed_in)
        self.key_card.setEnabled(signed_in)

    def _run(self, job: str, work: Callable[[], str]) -> None:
        """Do `work` off the UI thread; its return (or error) comes back to _on_result."""
        def go():
            try:
                self._result.emit(job, True, work() or "")
            except Exception as e:  # noqa: BLE001 - shown to the user, in words
                self._result.emit(job, False, str(e))
        threading.Thread(target=go, daemon=True).start()

    def _send_code(self) -> None:
        self._email = self.email_edit.text().strip()
        self.send_btn.setEnabled(False)
        self.connect_status.setText("Sending the email…")
        if self._catcher is None:
            from account.loopback import LinkCatcher
            self._catcher = LinkCatcher(self._link_arrived)
        back_to = self._catcher.start()            # "" if it can't listen: the code still works
        self._run("code", lambda: self._account.send_code(self._email, redirect_to=back_to))

    def _link_arrived(self, code: str) -> tuple[bool, str]:
        """The email's link, clicked: runs on the catcher's thread; the browser shows the words."""
        try:
            summary = self._account.verify_link(code)
        except Exception as e:  # noqa: BLE001 - shown in the browser and here, in words
            self._result.emit("signin", False, str(e))
            return False, str(e)
        cfg.set_access_mode("account")
        self._result.emit("signin", True, plan_line(summary))
        return True, "You can close this tab - HeyTora is ready."

    def _stop_catcher(self) -> None:
        if self._catcher is not None:
            self._catcher.stop()

    def _sign_in(self) -> None:
        code = self.code_edit.text().strip()
        self.signin_btn.setEnabled(False)
        self.connect_status.setText("Signing in…")

        def work() -> str:
            summary = self._account.verify(self._email, code)
            cfg.set_access_mode("account")
            return plan_line(summary)
        self._run("signin", work)

    def _use_key(self) -> None:
        key = self.key_edit.text().strip()
        if not key:
            self.connect_status.setText("Paste your key first.")
            return
        self.key_btn.setEnabled(False)
        self.connect_status.setText("Checking your key…")

        def work() -> str:
            ok, words = self._check_key(key)
            if not ok:
                raise RuntimeError(words)
            cfg.set_api_key("GOOGLE_API_KEY", key)
            cfg.set_access_mode("own_key")
            return words
        self._run("key", work)

    def _on_result(self, job: str, ok: bool, message: str) -> None:
        if job == "code":
            self.send_btn.setEnabled(True)
            if ok:
                self.code_row.show()
                self.code_edit.setFocus()
                self.connect_status.setText(f"Email sent to {self._email}. Open it and click the sign-in "
                                            "link: this window signs you in. It can take a minute.")
            else:
                self._stop_catcher()
                self.connect_status.setText(message)
        elif job == "signin":
            self.signin_btn.setEnabled(True)
            if ok:
                self._connected = True
                self._stop_catcher()
                self.code_row.hide()
                self.connect_status.setText(f"✓  Signed in. {message}")
                self._connect_locks()
                self.raise_()                       # the link was clicked in a browser
                self.activateWindow()
            else:
                self.connect_status.setText(message)
        elif job == "key":
            self.key_btn.setEnabled(True)
            if ok:
                self._connected = True
                self.key_edit.clear()
                self.connect_status.setText("✓  Your key works. You're connected.")
            else:
                self.connect_status.setText(message)

    # ── microphone ──────────────────────────────────────────────────────────────

    def _start_mic(self) -> None:
        try:
            import sounddevice as sd
            devices = [(i, d.get("name", ""), i == sd.default.device[0])
                       for i, d in enumerate(sd.query_devices()) if d.get("max_input_channels", 0) > 0]
        except Exception:  # noqa: BLE001 - no audio stack
            devices = []
        self.mic_select.blockSignals(True)
        self.mic_select.clear()
        self.mic_ids = [i for i, _n, _d in devices]
        for _i, name, default in devices:
            self.mic_select.addItem(name + ("  (default)" if default else ""))
        current = cfg.mic_device_index
        pick = (self.mic_ids.index(current) if current in self.mic_ids
                else next((k for k, (_i, _n, d) in enumerate(devices) if d), 0))
        if devices:
            self.mic_select.setCurrentIndex(pick)
        self.mic_select.blockSignals(False)
        try:
            self.mic_select.currentIndexChanged.disconnect()
        except TypeError:
            pass
        self.mic_select.currentIndexChanged.connect(self._pick_mic)
        if not devices:
            self.mic_note.setText("No microphone found. Plug one in. You can choose it later "
                                  "in Settings.")
            return
        self.mic_note.setText("")
        self._open_stream(self.mic_ids[pick])
        self._meter_timer = QTimer(self)
        self._meter_timer.timeout.connect(lambda: self.mic_bars.push(min(1.0, self._level * 6)))
        self._meter_timer.start(70)

    def _open_stream(self, device: int) -> None:
        self._stop_stream()
        try:
            import numpy as np
            import sounddevice as sd

            def hear(data, _frames, _time, _status):
                self._level = float(np.sqrt(np.mean(np.square(data.astype("float32")))))

            self._stream = sd.InputStream(device=device, channels=1, samplerate=16000,
                                          dtype="float32", callback=hear)
            self._stream.start()
        except Exception:  # noqa: BLE001
            self._stream = None
            self.mic_note.setText("That microphone couldn't be opened. Try another one.")

    def _pick_mic(self, k: int) -> None:
        if 0 <= k < len(self.mic_ids):
            device = self.mic_ids[k]
            cfg.mic_device_index = device
            cfg._write_env("MIC_DEVICE_INDEX", str(device))
            self._open_stream(device)

    def _stop_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001
                pass

    def _stop_mic(self) -> None:
        self._stop_stream()
        timer = getattr(self, "_meter_timer", None)
        if timer is not None:
            timer.stop()
        self._level = 0.0


def plan_line(summary: dict) -> str:
    """"Free plan - 25 talk messages and 25 agent messages a month." from /me."""
    plan = (summary.get("plan") or "free").capitalize()
    limits = summary.get("limits") or {}
    parts = []
    if "talk" in limits:
        parts.append("unlimited talk" if limits["talk"] is None else f"{limits['talk']} talk messages")
    if "agent" in limits:
        parts.append("unlimited agent messages" if limits["agent"] is None
                     else f"{limits['agent']} agent messages")
    return f"{plan} plan" + (f" - {' and '.join(parts)} a month." if parts else ".")


def maybe_show_setup_wizard(parent=None) -> Optional[SetupWizard]:
    """Open the wizard on first run - and whenever Tora has no way to reach
    Gemini, however many times it has been launched: a silent do-nothing app
    is a worse answer than a window explaining why."""
    if setup_already_ran() and cfg.has_key():
        return None
    w = SetupWizard(parent)
    w.show()
    return w
