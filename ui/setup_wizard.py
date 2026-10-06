"""
The first launch: sign in, get to know the user, and prove it all works.

Shown once on the first launch (and from the tray's "Run setup again"), and
whenever Tora has no way to reach Gemini. Its shape is docs/ui/DESIGN.md §7,
after Wispr Flow's onboarding: five phases across the top, question screens
beside a picture that answers back, and test screens the user passes by
doing the real thing - talking, holding the shortcut, asking Tora about a
screen and watching her point.

    Sign up       connect · welcome · work
    Permissions   privacy · mic
    Set up        ready · shortcut
    Learn         try · payoff
    Personalize   voice · personal · done

The steps prove what Tora is - she hears you (mic), the key works
(shortcut), she sees, points and explains (try: "what's wrong with this
equation?" on a sum with one wrong line) - rather than copying another
app's list. The Learn asks are real
asks: they count against the plan like any other question.

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
from ui import kit, tokens
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

# The picture side of "work": the starter skills (skills/*.md) that suit
# what you do, each with what you'd say to use it. Finishing the step turns
# these on and the other starter skills off - fewer for Tora to choose from,
# and Tora > Skills turns any of them back on (docs/agents/SKILLS.md).
SKILLS_FOR = {
    "Accounting": ("send-an-invoice", "file-receipts", "chase-an-unpaid-invoice",
                   "month-end-checklist", "make-a-quote"),
    "Business owner": ("send-an-invoice", "make-a-quote", "book-a-meeting",
                       "draft-an-email-reply", "chase-an-unpaid-invoice", "inbox-summary"),
    "Student": ("meeting-notes", "file-documents", "voice-note-to-email", "fill-a-form"),
    "Teacher": ("draft-an-email-reply", "meeting-notes", "file-documents", "fill-a-form"),
    "Developer": ("tidy-downloads", "file-documents", "inbox-summary"),
    "Sales": ("draft-an-email-reply", "book-a-meeting", "make-a-quote", "inbox-summary",
              "chase-an-unpaid-invoice"),
    "Design & video": ("social-post", "send-an-invoice", "make-a-quote", "tidy-downloads"),
    "Admin & office": ("inbox-summary", "book-a-meeting", "file-documents", "fill-a-form",
                       "month-end-checklist"),
    "Writing": ("voice-note-to-email", "draft-an-email-reply", "social-post"),
}
# Everyone gets these; "Something else" (or no answer) keeps every one on.
SKILLS_FOR_EVERYONE = ("draft-an-email-reply", "tidy-downloads", "compare-prices")

# Each starter skill's card: the tile of the work it suits, and what you'd say.
SKILL_CARDS = {
    "send-an-invoice": ("ledger", "Invoice Sarah for last week"),
    "chase-an-unpaid-invoice": ("wallet", "Chase Bloom about invoice 12"),
    "file-receipts": ("ledger", "File these receipts for me"),
    "month-end-checklist": ("calendar", "Do my month-end"),
    "make-a-quote": ("briefcase", "Quote Bloom for a new website"),
    "book-a-meeting": ("calendar", "Book a call with Sam on Friday"),
    "draft-an-email-reply": ("briefcase", "Reply to this email for me"),
    "inbox-summary": ("briefcase", "What came in today?"),
    "voice-note-to-email": ("mic", "Turn what I said into an email"),
    "social-post": ("video", "Post this on LinkedIn"),
    "meeting-notes": ("mic", "Write up today's meeting"),
    "file-documents": ("cap", "File these documents"),
    "tidy-downloads": ("code", "Tidy my Downloads folder"),
    "compare-prices": ("chart", "Find the best price for a kettle"),
    "fill-a-form": ("cap", "Fill in this form for me"),
}
SKILL_CARDS_SHOWN = 6


def skills_for(uses) -> list[str]:
    """The starter skills for what someone does, best first, each once.
    Empty for "Something else" or no answer: then every one stays on."""
    if not uses or "Something else" in uses:
        return []
    out: list[str] = []
    for use in uses:
        for sid in SKILLS_FOR.get(use, ()):
            if sid not in out:
                out.append(sid)
    for sid in SKILLS_FOR_EVERYONE:
        if sid not in out:
            out.append(sid)
    return out

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

PAGES = ("connect", "welcome", "work", "privacy", "mic",
         "ready", "shortcut", "try", "payoff", "voice", "personal", "done")
PHASE = {"connect": 0, "welcome": 0, "work": 0, "privacy": 1, "mic": 1,
         "ready": 2, "shortcut": 2, "try": 3, "payoff": 3,
         "voice": 4, "personal": 4, "done": 4}
LIVE_PAGES = ("shortcut", "try", "payoff")   # need the running app
LEARN = ("try",)


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
        # Learn page -> (seconds from question to answer, "pointed" | "drew")
        self._answered: dict[str, tuple[float, str]] = {}
        self._learn_words: dict[str, QWidget] = {}
        self._voice = cfg.voice or ""
        self._colour = cfg.cursor_colour()
        self._finished = False

        self._continue: dict[str, QPushButton] = {}
        self._backs: dict[str, QPushButton] = {}
        self._skips: dict[str, QPushButton] = {}
        self._none = QPushButton()                 # stands in where a page has no such button
        self._none.hide()

        self._result.connect(self._on_result)
        self._hotkey.connect(self._on_hotkey)
        self._build()
        # Signing in comes first, whatever the caller asked for: a fresh
        # install and a sign-out both open on it.
        self._go(start if start in PAGES and start != "welcome" else PAGES[0])

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
                go.setStyleSheet(f"QPushButton#continue {{ background: {O.STAGE.button};"
                                 f" color: {O.STAGE.button_text}; }}"
                                 f"QPushButton#continue:hover {{ background: {O.P.card}; }}")
            lay.addWidget(go, 0, Qt.AlignmentFlag.AlignHCenter)
            self._continue[name] = go
        return body, page

    def _back_button(self, name: str, dark: bool = False) -> QPushButton:
        b = QPushButton("←  Back")
        b.setObjectName("back")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(self._back)
        if dark:
            b.setStyleSheet(f"QPushButton#back {{ color: {O.STAGE.dim}; }}"
                            f" QPushButton#back:hover {{ color: {O.STAGE.text}; }}")
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
        # The website's five "What it does" scenes, moving (ui/tour_scenes.py).
        from ui.tour_scenes import TourCarousel
        self.carousel = TourCarousel()
        panel.body.setContentsMargins(24, 24, 24, 12)
        panel.body.addWidget(self.carousel)
        return page

    def _page_connect(self) -> QWidget:
        from account import settings
        from ui.app_widgets import wordmark
        outer, page = self._test("connect")
        outer.addSpacing(10)
        card = QFrame()
        card.setObjectName("signinCard")
        card.setFixedWidth(460)
        card.setStyleSheet(f"QFrame#signinCard {{ background: {T.SURFACE}; border: 1px solid {T.BORDER};"
                           f" border-radius: {tokens.RADIUS_XL}px; }}")
        kit.drop_shadow(card, "hero")
        body = QVBoxLayout(card)
        body.setContentsMargins(36, 32, 36, 30)
        body.setSpacing(12)
        body.addWidget(wordmark(28), 0, Qt.AlignmentFlag.AlignLeft)
        body.addSpacing(10)
        outer.addLayout(self._centre(card))
        if self._gated:
            body.addWidget(O.heading("Sign in to HeyTora."))
            body.addWidget(O.line("With your email - new here, and this makes your account. "
                                  "We email you a link: click it and you're in."))
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
        body.addSpacing(8)
        go = QPushButton("Continue")
        go.setObjectName("continue")
        go.setCursor(Qt.CursorShape.PointingHandCursor)
        go.clicked.connect(self._next)
        body.addWidget(go)
        self._continue["connect"] = go
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

        self.skill_cards: list[O.SkillCard] = []
        panel.body.addWidget(O.text("She'll start with these skills", 14, 700))
        self.skill_stack = QVBoxLayout()
        self.skill_stack.setSpacing(12)
        panel.body.addLayout(self.skill_stack)
        self._show_skills()
        return page

    def _picked_uses(self) -> list[str]:
        return [c.property("use") for c in self.use_chips if c.isChecked()]

    def _show_skills(self) -> None:
        """The right side answers back: the skills for what was picked (the
        everyday ones until something is)."""
        from agents.skills import SkillLibrary
        ids = skills_for(self._picked_uses()) or list(SKILLS_FOR_EVERYONE) + ["send-an-invoice",
                                                                             "book-a-meeting",
                                                                             "inbox-summary"]
        names = {s.id: s.name for s in SkillLibrary().all()}
        while self.skill_stack.count():
            item = self.skill_stack.takeAt(0)
            row = item.layout()
            if row is not None:
                while row.count():
                    w = row.takeAt(0).widget()
                    if w is not None:
                        w.deleteLater()
        self.skill_cards = []
        for i, sid in enumerate([s for s in ids if s in SKILL_CARDS and s in names][:SKILL_CARDS_SHOWN]):
            kind, phrase = SKILL_CARDS[sid]
            card = O.SkillCard(kind, names[sid], phrase, sid)
            self.skill_cards.append(card)
            row = QHBoxLayout()
            row.addSpacing(40 if i % 2 else 0)       # a little stagger, like the site's drift
            row.addWidget(card)
            row.addSpacing(0 if i % 2 else 40)
            self.skill_stack.addLayout(row)

    def _work_changed(self, *_a) -> None:
        self._show_skills()
        if self.page() == "work":
            self.next_btn.setEnabled(bool(self._picked_uses()))

    def _page_privacy(self) -> QWidget:
        body, panel, page = self._question("privacy", O.STEP[2])
        body.addWidget(O.heading("We look when you ask."))
        body.addWidget(O.line("Not before. Tora sees your screen only when you ask her "
                              "something, and only to answer it."))
        body.addSpacing(6)
        self.guard_card = O.OptionCard(
            "Skip private windows",
            "Tora won't look at password managers, banking or private browsing, "
            "even when you ask.", checked=cfg.switch("privacy_guard"))
        body.addWidget(self.guard_card)
        body.addWidget(O.text("You can change this any time in Settings.", 12, 400, T.TEXT_FAINT))
        # The website's privacy card: the peeker asleep on the claim, waking
        # while the pointer is on it.
        self.privacy_visual = O.PrivacyVisual()
        panel.body.setContentsMargins(12, 12, 12, 12)
        panel.body.addWidget(self.privacy_visual)
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
        change.clicked.connect(lambda: (self.mic_select.show(), change.hide(),
                                        self.mic_select.showPopup()))
        self._change_mic = change
        self.mic_name = O.text("", 12, 500, T.TEXT_DIM)
        card.lay.insertWidget(2, self.mic_name)
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

    def _learn_page(self, name: str, title: str, suggestion: str, content: QWidget) -> QWidget:
        """A Learn screen. The words above the content hide while the
        shortcut is held: Tora reads the whole screen, and given the chance
        she reads the instructions out instead of looking at the content."""
        from PyQt6.QtWidgets import QGraphicsOpacityEffect
        body, page = self._test(name, skip=True, go_words="Continue")
        words = QWidget()
        wl = QVBoxLayout(words)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(12)
        wl.addWidget(O.heading(title, centre=True))
        wl.addWidget(O.line(f"Hold {_pretty_hotkey()} and say: {suggestion}", centre=True))
        fade = QGraphicsOpacityEffect(words)
        fade.setOpacity(1.0)
        words.setGraphicsEffect(fade)
        words.fade = fade
        self._learn_words[name] = words
        body.addWidget(words)
        body.addSpacing(18)
        body.addLayout(self._centre(content))
        body.addSpacing(12)
        status = O.text("", 13, 600, T.TEXT_DIM, centre=True)
        body.addWidget(status)
        setattr(self, f"{name}_status", status)
        return page

    def _page_try(self) -> QWidget:
        self.math_sheet = O.MathSheet()
        # Said in full: "where did I go wrong?" alone left her unsure what
        # "it" was. Naming the equation gives her the subject.
        return self._learn_page("try", "Ask Tora about this.",
                                "“Hey Tora, what's wrong with this equation?”", self.math_sheet)

    def _hide_words(self, name: str, hidden: bool) -> None:
        words = self._learn_words.get(name)
        if words is None:
            return
        from PyQt6.QtCore import QPropertyAnimation
        from ui import tokens
        anim = QPropertyAnimation(words.fade, b"opacity", words)
        anim.setStartValue(words.fade.opacity())
        anim.setEndValue(0.0 if hidden else 1.0)
        anim.setDuration(tokens.DURATION_FAST)
        anim.start()
        words._anim = anim
        if not words.isVisible():
            words.fade.setOpacity(0.0 if hidden else 1.0)

    def _page_payoff(self) -> QWidget:
        body, page = self._test("payoff", dark=True, go_words="Continue")
        body.setAlignment(Qt.AlignmentFlag.AlignLeft)
        body.setContentsMargins(60, 10, 60, 0)
        body.addWidget(O.text("Nice job.", 72, 700, O.STAGE.text, spacing=-1.5, wrap=False))
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
            v.setSpacing(4)
            v.addWidget(O.text(caption.upper(), 11, 600, O.STAGE.dim, spacing=1.5, wrap=False))
            v.addWidget(O.text(value, 44, 700, O.STAGE.text, spacing=-1.0, wrap=False))
            # Pinned to what it asks for: next to the wrapping line below it,
            # the column's height-for-width pass gave it 55 px of its 76 and
            # sliced the number in half.
            box.setFixedHeight(box.sizeHint().height())
            return box

        # Short captions: at this size a two-line caption ran into its number.
        if "try" in self._answered:
            lay.addWidget(stat(f"{self._answered['try'][0]:.1f} seconds", "Tora found the mistake in"))
        lay.addWidget(O.text("That's the whole idea: you never left the screen.", 16, 500, O.STAGE.dim))

    # ── Personalize ───────────────────────────────────────────────────────────

    def _ribbon_card(self, name: str, width: int = 560) -> tuple[QVBoxLayout, QWidget]:
        """A centred white card on the ribbon - the Personalize screens."""
        outer, page = self._test(name, go_words="Continue")
        outer.addSpacing(4)
        card = QFrame()
        card.setObjectName(f"{name}Card")
        card.setFixedWidth(width)
        card.setStyleSheet(f"QFrame#{name}Card {{ background: {T.SURFACE}; border: none;"
                           f" border-radius: {tokens.RADIUS_XL}px; }}")
        kit.drop_shadow(card, "hero")
        body = QVBoxLayout(card)
        body.setContentsMargins(32, 28, 32, 26)
        body.setSpacing(12)
        outer.addLayout(self._centre(card))
        return body, page

    def _page_voice(self) -> QWidget:
        from ui import tokens
        from audio.tts.gemini_tts import VOICES
        body, page = self._ribbon_card("voice", 620)
        body.setContentsMargins(32, 32, 32, 28)
        body.addWidget(O.heading("Pick her voice.", centre=True))
        body.addWidget(O.line("Use the arrows to hear each one.", centre=True))
        self.voice_carousel = O.VoiceCarousel(VOICES, self._voice)
        self.voice_carousel.changed.connect(self._pick_voice)
        body.addLayout(self._centre(self.voice_carousel))
        return page

    def _page_personal(self) -> QWidget:
        from ui import tokens
        body, page = self._ribbon_card("personal", 560)
        body.setSpacing(6)
        body.setContentsMargins(30, 22, 30, 18)
        body.addWidget(O.heading("Make her yours."))
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(O.text("Her colour", 14, 600, wrap=False))
        self.tora_preview = O.ToraPreview()
        self.tora_preview.set_colour(tokens.cursor_hex(self._colour))
        self.tora_preview.setFixedSize(96, 62)       # room for her name bubble
        row.addWidget(self.tora_preview)
        row.addStretch(1)
        self.swatches: list[O.Swatch] = []
        for key, name, hex_colour in tokens.CURSOR_COLOURS:
            sw = O.Swatch(key, name, hex_colour)
            sw.set_on(key == self._colour)
            sw.picked.connect(self._pick_colour)
            row.addWidget(sw)
            self.swatches.append(sw)
        body.addLayout(row)
        self.startup_row = ToggleRow("Start HeyTora when Windows starts",
                                     "For you only. No admin rights needed.", True, first=True)
        self.wake_row = ToggleRow("Always listening",
                                  "Wake on “Tora”, or only while the shortcut is held.",
                                  cfg.ambient_mic())
        self.panel_row = ToggleRow("Show the floating panel",
                                   "The small glass window with the conversation in it.", False)
        for r in (self.startup_row, self.wake_row, self.panel_row):
            body.addWidget(r)
        answers = QHBoxLayout()
        answers.addWidget(O.text("How she answers", 14, 600, wrap=False))
        answers.addStretch(1)
        self.route_select = Select("Live: answers in about a second",
                                   "Classic: transcribe, think, then speak", width=300)
        self.route_select.setCurrentIndex(0 if cfg.conversation_mode != "cascade" else 1)
        answers.addWidget(self.route_select)
        body.addSpacing(4)
        body.addLayout(answers)
        return page

    def _pick_colour(self, key: str) -> None:
        from ui import tokens
        self._colour = key
        for sw in self.swatches:
            sw.set_on(sw.key == key)
        self.tora_preview.set_colour(tokens.cursor_hex(key))

    def _pick_voice(self, name: str) -> None:
        """The carousel moved to `name` (and is playing it)."""
        self._voice = name

    def _page_done(self) -> QWidget:
        page = O.FilledRibbon()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(56, 30, 56, 36)
        top = QHBoxLayout()
        top.addWidget(self._back_button("done"))
        top.addStretch(1)
        outer.addLayout(top)
        outer.addStretch(1)
        card = QFrame()
        card.setObjectName("doneCard")
        card.setFixedWidth(460)
        card.setStyleSheet(f"QFrame#doneCard {{ background: {T.SURFACE}; border: none;"
                           f" border-radius: {tokens.RADIUS_XL}px; }}")
        kit.drop_shadow(card, "hero")
        body = QVBoxLayout(card)
        body.setContentsMargins(36, 34, 36, 30)
        body.setSpacing(12)
        self.done_title = O.heading("You're set.")
        body.addWidget(self.done_title)
        self.done_text = O.line("")
        body.addWidget(self.done_text)
        body.addSpacing(6)
        hold = QHBoxLayout()
        hold.setSpacing(10)
        hold.addWidget(O.text("Hold", 14, 500, T.TEXT_DIM, wrap=False))
        hold.addWidget(O.Keycap(_pretty_hotkey(), px=14))
        hold.addWidget(O.text("and ask.", 14, 500, T.TEXT_DIM, wrap=False))
        hold.addStretch(1)
        body.addLayout(hold)
        body.addSpacing(8)
        go = QPushButton("Open HeyTora")
        go.setObjectName("continue")
        go.setCursor(Qt.CursorShape.PointingHandCursor)
        go.clicked.connect(self._next)
        body.addWidget(go)
        self._continue["done"] = go
        outer.addLayout(self._centre(card))
        outer.addStretch(1)
        return page

    def _fill_done(self) -> None:
        name = cfg.profile()[0]
        self.done_title.setText(f"You're set, {name}." if name else "You're set.")
        if not cfg.has_key():
            self.done_text.setText("You're not connected yet. Run setup again from the tray icon "
                                   "to sign in or add a key.")
            return
        # Not a list of sample questions - they all sounded like the same
        # three. The point is that anything on the screen is fair game.
        self.done_text.setText(
            "Be curious. A form you're stuck on, an error, a recipe, a game, a spreadsheet, "
            "a page in another language - if it's on your screen, Tora can help with it. "
            "Ask the way you'd ask a friend sitting next to you. The odder the question, "
            "the more you'll find out what she can do.")

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
        if name == "connect":
            self._backs["connect"].hide()
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
        elif name in LEARN:
            self._arm_learn(name)
        elif name == "payoff":
            self._fill_payoff()
        elif name == "voice":
            self.voice_carousel.setFocus()
            QTimer.singleShot(350, self.voice_carousel.play)
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
        elif name == "privacy":
            cfg.set_switch("privacy_guard", self.guard_card.isChecked())
            live_switch = getattr(self._live, "set_switch", None)
            if live_switch is not None:
                live_switch("privacy_guard", self.guard_card.isChecked())
        elif name == "voice":
            self._save_voice()
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
        if nxt == "payoff" and not self._answered:
            nxt = "voice"                            # nothing was measured: no fake celebration
        self._go(nxt)

    def _skip(self) -> None:
        nxt = {"try": "payoff"}.get(self.page(), self.page())
        if nxt == "payoff" and not self._answered:
            nxt = "voice"
        self._go(nxt)

    def _back(self) -> None:
        i = PAGES.index(self.page())
        prev = PAGES[max(0, i - 1)]
        if prev == "ready":
            # Back past the seam goes to the mic test, if the app runs;
            # the gate's half cannot be re-entered once the app has started.
            prev = "mic"
        if prev == "payoff" and not self._answered:
            prev = "try"
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
            live.draw       pyqtSignal(dict)                (Tora drew a shape)
            live.set_wake(bool), live.show_panel(bool), live.set_voice(str),
            live.set_route(str), live.set_switch(name, bool)   optional

        Waiting on "ready", the window moves straight on to the shortcut test.
        """
        self._live = live
        try:
            live.state.connect(self._on_state)
            live.point.connect(lambda *_a: self._on_answer("pointed"))
            # She may answer by pointing or by drawing on it (look_at_screen
            # boxes what it found): either is an answer.
            draw = getattr(live, "draw", None)
            if draw is not None:
                draw.connect(lambda *_a: self._on_answer("drew"))
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
        status = getattr(self, f"{name}_status")
        done = name in self._answered
        self.next_btn.setEnabled(done)
        self._hide_words(name, False)
        status.setText("Done." if done else "Waiting for you to ask…")

    def _on_state(self, state) -> None:
        name = getattr(state, "name", str(state)).upper()
        page = self.page()
        if page not in LEARN:
            return
        status = getattr(self, f"{page}_status")
        if name == "LISTENING":
            # The words go while the key is held, so the screenshot Tora takes
            # holds the sum or the settings card, not "say: ...".
            self._hide_words(page, True)
            status.setText("Listening…")
        elif name == "THINKING":
            self._asked_at = time.monotonic()        # the question has ended
            status.setText("Tora is looking at your screen…")
        elif name == "SPEAKING" and status.text().startswith("Tora is looking"):
            status.setText("Tora is answering…")
        elif name == "IDLE" and page not in self._answered:
            self._hide_words(page, False)            # nothing came of it: the words come back
            status.setText("Try again, or skip.")

    def _on_answer(self, how: str) -> None:
        """Tora pointed or drew on a Learn screen: timed from the question."""
        page = self.page()
        if page not in LEARN or page in self._answered:
            return
        secs = max(0.1, time.monotonic() - self._asked_at) if self._asked_at else 0.0
        self._answered[page] = (secs, how)
        status = getattr(self, f"{page}_status")
        verb = {"pointed": "pointed at it", "drew": "marked it"}.get(how, how)
        status.setText(f"Tora {verb} in {secs:.1f} seconds." if secs else f"Tora {verb}.")
        self.next_btn.setEnabled(True)

    # ── saving answers ────────────────────────────────────────────────────────

    def _save_profile(self) -> None:
        uses = self._picked_uses()
        cfg.set_profile(self.name_edit.text(), uses)
        self._save_skills(uses)

    @staticmethod
    def _save_skills(uses) -> None:
        """The starter skills for what they do on, the other starter skills
        off; their own skills are never touched. "Something else": all on."""
        try:
            from agents.skills import SkillLibrary
            lib = SkillLibrary()
            wanted = set(skills_for(uses))
            for skill in lib.all():
                if skill.source != "yours":
                    lib.set_enabled(skill.id, not wanted or skill.id in wanted)
        except Exception:  # noqa: BLE001 - the skills are a nicety here, never a blocker
            import logging
            logging.getLogger("tora.setup").warning("could not set the starter skills", exc_info=True)

    def _save_voice(self) -> None:
        fn = getattr(self._live, "set_voice", None)
        if fn is not None and self._voice:
            try:
                fn(self._voice)
            except Exception:  # noqa: BLE001
                pass

    def _save_personal(self) -> None:
        from ui import tokens
        cfg.set_cursor_colour(self._colour)
        try:
            from ui.overlay import set_cursor_colour
            set_cursor_colour(tokens.cursor_hex(self._colour))     # repaints at once
        except Exception:  # noqa: BLE001 - no overlay (tests): saved for next start
            pass
        try:
            from ui import autostart
            autostart.set_enabled(self.startup_row.switch.isChecked())
        except Exception:  # noqa: BLE001 - a nicety, never a blocker
            pass
        live = self._live
        route = "cascade" if self.route_select.currentIndex() == 1 else "realtime"
        if live is not None:
            for attr, value in (("set_route", route),):
                fn = getattr(live, attr, None)
                if fn is not None and value:
                    try:
                        fn(value)
                    except Exception:  # noqa: BLE001
                        pass
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
        current = cfg.mic_device()
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
        self.mic_select.hide()
        self._change_mic.show()
        self._say_mic()
        self._open_stream(self.mic_ids[pick])
        self._meter_timer = QTimer(self)
        self._meter_timer.timeout.connect(lambda: self.mic_bars.push(min(1.0, self._level * 7)))
        self._meter_timer.start(55)

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
            cfg.set_mic_device(device)          # with its name: numbers move
            self._open_stream(device)
        # Picked: back to the button, saying which mic it is now.
        self.mic_select.hide()
        self._change_mic.show()
        self._say_mic()

    def _say_mic(self) -> None:
        k = self.mic_select.currentIndex()
        name = self.mic_select.itemText(k).replace("  (default)", "") if k >= 0 else ""
        self.mic_name.setText(f"Listening on {name}" if name else "")

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
