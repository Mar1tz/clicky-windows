"""The shape's settings pages (DESIGN.md §4), split as HeyClicky splits its
own: one thing per page - Account, General, Voice, Microphone, Shortcuts,
Cursor, Style - where the app window had a few tabs holding several each
and a Tora page holding the rest.

Nothing here is new behaviour. Each page is made of the parts the app
window already has - its settings tabs' groups and the Tora page's cards
(the voice carousel, her colour, her sound, her modes, her instructions,
teaching her) - borrowed, not copied, so the two windows stay the same
until the app window goes (DESIGN.md §13).

Not here yet, because there is nothing behind them: HeyClicky's Dictation
(a language and a dictionary), Integrations and Routines pages, a speech
speed and a captions switch (step 5b).
"""
from __future__ import annotations

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QHBoxLayout, QWidget

from ui.app_pages import ToraPage
from ui.app_widgets import Select, button, label
from ui.settings_tabs import AccountTab, GeneralTab, SystemTab, Tab, UsageTab, _ask


CAROUSEL_W = 480


class _Later:
    """Run something once the click that asked for it has finished."""

    def _later(self, fn) -> None:
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(fn)
        timer.timeout.connect(timer.deleteLater)
        timer.start(0)


# ── the top group ─────────────────────────────────────────────────────────────

class AccountPage(AccountTab):
    """HeyClicky's Account: this month on the plan first (the Usage tab's
    group), then who you are, how Tora thinks, signing out and closing."""

    HEADING = "Account"
    _USAGE = UsageTab._USAGE
    _usage = UsageTab._usage

    def showEvent(self, event) -> None:
        # On screen: the plan is read again - it may have changed on Paddle's page.
        super().showEvent(event)
        refresh = getattr(self.bridge, "refresh_account", None)
        if refresh is not None:
            refresh(force=True)

    def build(self) -> None:
        self._slot_w = self._slot(self._usage)
        self.body.add(self._slot_w)
        super().build()


class GeneralPage(SystemTab):
    """How HeyTora behaves on the PC: starting with Windows, the floating
    panel, how it looks, and keeping itself up to date."""

    HEADING = "General"

    def build(self) -> None:
        from ui.settings_parts import Group
        super().build()
        br = self.bridge
        look = Group("Appearance")
        modes = (("system", "Match Windows"), ("light", "Light"), ("dark", "Dark"))
        theme = Select(*(words for _m, words in modes), width=180)
        keys = [m for m, _w in modes]
        theme.setCurrentIndex(keys.index(br.theme()) if br.theme() in keys else 0)
        theme.currentIndexChanged.connect(
            lambda k: br.set_theme(keys[k]) if 0 <= k < len(keys) else None)
        look.row("Theme", "", theme)
        self.body.add(look)


# ── TORA ──────────────────────────────────────────────────────────────────────

class VoicePage(_Later, Tab):
    """Her voice (the carousel: hear each, stop on hers), the sound the
    moment she listens, and how she answers."""

    HEADING = "Voice"
    VOICE_WAIT_MS = ToraPage.VOICE_WAIT_MS
    _voice = ToraPage._voice
    _sound = ToraPage._sound

    def build(self) -> None:
        from ui.settings_parts import Group
        br = self.bridge
        self.body.add(self._voice())
        # The first launch's 560 px is wider than a settings page here; the
        # carousel draws from its own width, arrows and all.
        self.carousel.setFixedWidth(CAROUSEL_W)
        self.body.add(self._sound())
        answers = Group("How she answers")
        routes = ("realtime", "cascade")
        route = Select("Live - answers in about a second",
                       "Classic - transcribe, think, then speak", width=300)
        route.setCurrentIndex(routes.index(br.route()) if br.route() in routes else 0)
        route.currentIndexChanged.connect(
            lambda k: br.set_route(routes[k]) if 0 <= k < 2 else None)
        answers.row("Answers", "", route)
        self.body.add(answers)


class MicrophonePage(GeneralTab):
    """Which microphone she listens through, and listening for her name."""

    HEADING = "Microphone"

    def build(self) -> None:
        from ui.settings_parts import Group
        br = self.bridge
        mic = Group("Input")
        change = button("Change")
        change.clicked.connect(self._open_mic)
        mic.row("Microphone", self._mic_words(), change)
        mic.toggle("Always listening", "Say “Tora” to start - no shortcut needed.",
                   br.switch("wake_word"), lambda on: br.set_switch("wake_word", on))
        self.body.add(mic)


class ShortcutsPage(GeneralTab):
    """The keys that summon her, as keycaps."""

    HEADING = "Shortcuts"

    def build(self) -> None:
        from ui.settings_parts import Group, keycaps
        br = self.bridge
        talk = Group("Talk to Tora")
        change = button("Change")
        change.clicked.connect(self._open_shortcut)
        row = talk.row("Push to talk", "Hold to talk to her; let go to send. A quick tap: she "
                                       "listens until you stop.", change)
        keys = QHBoxLayout()
        keys.setSpacing(6)
        keys.addWidget(keycaps(_ask(br, "hotkey_keys", []) or [br.hotkey() or "Ctrl + Win"]))
        keys.addStretch(1)
        row.layout().itemAt(0).layout().addLayout(keys)
        cursor = Group("Her cursor")
        hide = cursor.row("Show or hide her cursor", "From anywhere, any time.")
        hide_keys = QHBoxLayout()
        hide_keys.addWidget(keycaps([k.strip().capitalize() for k in
                                     str(_ask(br, "cursor_hotkey", "Win + Alt + H")).split("+")]))
        hide_keys.addStretch(1)
        hide.layout().itemAt(0).layout().addLayout(hide_keys)
        self.body.add(talk)
        self.body.add(cursor)


class CursorPage(_Later, Tab):
    """The cursor that lives on the screen with yours: its colour, whether it
    shows, and how what she points at is drawn."""

    HEADING = "Cursor"
    TOPICS = frozenset({"cursor"})
    _colour = ToraPage._colour

    def build(self) -> None:
        from ui.settings_parts import Group
        br = self.bridge
        self.body.add(self._colour())
        trail = Group("Pointing")
        trail.toggle("Trail as a line", "Show what you point at with a smooth line instead of mist.",
                     br.switch("trail_line"), lambda on: br.set_switch("trail_line", on))
        self.body.add(trail)


class StylePage(_Later, Tab):
    """How she teaches (her modes) and what she always keeps in mind."""

    HEADING = "Style"
    MODES = ToraPage.MODES
    _modes = ToraPage._modes
    _instructions = ToraPage._instructions

    def build(self) -> None:
        self.body.add(self._modes())
        self.body.add(self._instructions())


# ── WORK ──────────────────────────────────────────────────────────────────────

class SkillsPage(_Later, Tab):
    """How you like jobs done (skills), and the two ways to show her how you
    work: record a lesson, or let her watch a task."""

    HEADING = "Skills"
    TOPICS = frozenset({"recording", "skills"})
    edit_skill = ToraPage.edit_skill
    _recording = ToraPage._recording
    _capture = ToraPage._capture
    _set_capturing = ToraPage._set_capturing

    def build(self) -> None:
        from ui.skills_page import skills_card
        self.body.add(skills_card(self.bridge, self.edit_skill))
        self.body.add(label("TEACH HER HOW YOU WORK", "sectionTitle"))
        teach = QWidget()
        row = QHBoxLayout(teach)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        row.addWidget(self._recording(), 1)
        row.addWidget(self._capture(), 1)
        self.body.add(teach)


# key -> the page; the shape's sidebar (shape.SETTINGS) names and orders them.
PAGES = {
    "account": AccountPage,
    "system": GeneralPage,
    "voice": VoicePage,
    "microphone": MicrophonePage,
    "shortcuts": ShortcutsPage,
    "cursor": CursorPage,
    "style": StylePage,
    "skills": SkillsPage,
}
