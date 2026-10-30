"""The shape's settings pages (DESIGN.md §4), split as HeyClicky splits its
own: one thing per page - Account, General, Voice, Microphone, Shortcuts,
Cursor, Style - where the app window had a few tabs holding several each
and a Tora page holding the rest.

Nothing here is new behaviour. Each page is made of the parts the app
window already has - its settings tabs' groups and the Tora page's cards
(her sound, her modes, her instructions, teaching her) - borrowed, not
copied. Voice, Cursor, Microphone and Shortcuts are laid out as
HeyClicky's are (heyclicky2.0.mp4): tiles, a list, keys at the row's end
(ui/shape_widgets.py).

Integrations and Routines (step 5b) show what was only reachable by voice
before: the connections (integrations/) and the skills that run by
themselves (agents/manager/scheduler.py). Not here yet, because there is
nothing behind them: HeyClicky's Dictation (a language and a dictionary),
a speech speed and a captions switch.
"""
from __future__ import annotations

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ui import app_theme as T
from ui.app_pages import ToraPage
from ui.app_widgets import Select, Switch, button, label
from ui.settings_tabs import AccountTab, AgentsTab, GeneralTab, SystemTab, Tab, UsageTab, _ask



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
    """HeyClicky's Account, and nothing more (31 Oct: "ours has a lot of
    fluff"): the plan in one card - which plan, a line on the month, a bar
    for each allowance, Upgrade - then Log out, Delete account and Quit.
    The name comes from the tour's interview and the own key lives in
    Advanced, so neither is here; there is no picture."""

    HEADING = "Account"
    _USAGE = UsageTab._USAGE
    _usage = UsageTab._usage

    def showEvent(self, event) -> None:
        # On screen: the plan is read again - it may have changed on Paddle's page.
        super().showEvent(event)
        refresh = getattr(self.bridge, "refresh_account", None)
        if refresh is not None:
            refresh(force=True)

    def refresh_topics(self, topics: set) -> None:
        """The plan moves with every question: only its card is swapped."""
        if "account" in topics and not self._swap(getattr(self, "_slot_w", None), self._plan):
            self.refresh()

    def build(self) -> None:
        from ui.settings_parts import Group
        br = self.bridge
        st = _ask(br, "account_state", {}) or {}
        self._slot_w = self._slot(self._plan)
        self.body.add(self._slot_w)

        acct = Group("Account")
        if st.get("signed_in"):
            from ui import app_pages
            acct.link("Log out", st.get("email") or "",
                      lambda: app_pages.confirm_sign_out(self) and br.account_sign_out())
            gone = acct.link("Delete account", "Permanently erase your account and all its data.",
                             lambda: self._close_account(st.get("email") or ""))
            _danger(gone)
        bye = acct.link("Quit HeyTora", "", lambda: _ask(br, "quit", None))
        _danger(bye)
        self.body.add(acct)

    def _plan(self) -> QWidget:
        """The plan card - or, signed out, reading, or on their own key,
        what the Usage card says then."""
        from account.service import resets_on
        from ui.app_widgets import Meter
        from ui.settings_parts import Group
        st = _ask(self.bridge, "account_state", {}) or {}
        summary = st.get("summary")
        if not (st.get("signed_in") and summary and st.get("mode", "account") == "account"):
            return self._usage()
        plan = summary.get("plan_name") or (summary.get("plan") or "").capitalize() or "HeyTora"
        g = Group("Plan")
        card = QWidget()
        col = QVBoxLayout(card)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        col.addWidget(label(f"You're on the {plan} plan.", "setTitle", wrap=True))
        from account.service import credits_words
        when = resets_on(summary)
        words = credits_words(summary)
        blocked = (summary.get("blocked") or {}).get("all") == "budget"
        line = ("This month's credits are used up." if blocked else
                (words + "." if words else "Your plan's use this month.")) + f" It starts again on {when}."
        col.addWidget(label(line, "setDesc", wrap=True))
        col.addSpacing(10)
        bars = QHBoxLayout()
        bars.setSpacing(24)
        limits, usage = summary.get("limits") or {}, summary.get("usage") or {}
        today = summary.get("today") or {}
        for kind, name, unit in self._USAGE:
            if kind not in limits:
                continue
            used, cap = int(usage.get(kind) or 0), limits.get(kind)
            said = f"{used} {unit[:-1] if used == 1 else unit} this month"
            if today.get(kind):
                said += f" · {int(today[kind])} today"
            one = QVBoxLayout()
            one.setSpacing(4)
            head = QHBoxLayout()
            head.addWidget(label(name, "setDesc"))
            head.addStretch(1)
            if cap is not None:
                head.addWidget(label(f"{used} / {cap}", "setDesc"))
            one.addLayout(head)
            one.addWidget(label(said, "setTitle", wrap=True))
            frac = 0.0 if not cap else min(1.0, used / cap)
            one.addWidget(Meter(frac, colour=T.WARN if frac >= 0.8 else T.ACCENT, height=4))
            bars.addLayout(one, 1)
        col.addLayout(bars)
        col.addSpacing(12)
        top = (summary.get("plan") or "") == "max"
        go = button("Change plan" if top else "Upgrade", "ghost" if top else "primary")
        go.clicked.connect(lambda: self.navigate.emit("settings/plans"))
        foot = QHBoxLayout()
        foot.addWidget(go)
        foot.addStretch(1)
        col.addLayout(foot)
        g.add(card, (16, 14, 16, 16))
        return g


def _danger(row) -> None:
    """A row that ends something: its title in the warning red."""
    row.title.setStyleSheet(f"color: {T.DANGER};")


class GeneralPage(SystemTab):
    """How HeyTora behaves on the PC, HeyClicky's way: in the taskbar or
    not, starting with Windows, keeping itself up to date, how it looks,
    the tour again - and, one step in, Privacy and Advanced.

    The floating panel's switch went (31 Oct: "I don't understand it -
    whenever I click it, it disables itself"): the panel hides itself when
    a conversation ends, so the switch flipped back. The shape is where the
    conversation is now."""

    HEADING = "General"

    def build(self) -> None:
        from ui.settings_parts import Group
        br = self.bridge
        app = Group("Behaviour")
        app.toggle("Show in taskbar", "A HeyTora button in the taskbar, for when the pill is hard to find.",
                   br.switch("taskbar"), lambda on: br.set_switch("taskbar", on))
        app.toggle("Launch at login", "Open HeyTora when you sign in to Windows.",
                   br.autostart(), br.set_autostart)
        self.body.add(app)
        self._updates_slot = self._slot(self._updates_card)
        self.body.add(self._updates_slot)
        look = Group("Appearance")
        modes = (("system", "Match Windows"), ("light", "Light"), ("dark", "Dark"))
        theme = Select(*(words for _m, words in modes), width=180)
        keys = [m for m, _w in modes]
        theme.setCurrentIndex(keys.index(br.theme()) if br.theme() in keys else 0)
        theme.currentIndexChanged.connect(
            lambda k: br.set_theme(keys[k]) if 0 <= k < len(keys) else None)
        look.row("Theme", "", theme)
        self.body.add(look)
        tour = Group("Welcome tour")
        again = button("Replay")
        again.clicked.connect(br.run_setup)
        tour.row("Replay the welcome tour", "Her voice, the microphone, the interview, circling and "
                 "driving - from the start.", again)
        self.body.add(tour)
        more = Group("More")
        more.link("Privacy", "What Tora looks at, and what she keeps.",
                  lambda: self.navigate.emit("settings/privacy"))
        more.link("Advanced", "Your own keys, the models in use, diagnostics.",
                  lambda: self.navigate.emit("settings/advanced"))
        self.body.add(more)


# ── WORK ──────────────────────────────────────────────────────────────────────

class AgentsPage(AgentsTab):
    """HeyClicky's Agents, as short as theirs (31 Oct: "theirs is much
    cleaner"): where the agents work, and how much they do without asking.
    The limits, the models and the history are one step in (AgentLimitsPage)."""

    HEADING = "Agents"

    def build(self) -> None:
        from ui.settings_parts import Group
        br = self.bridge
        r = _ask(br, "agent_rails", None) or {"workspace": "", "plan": False}
        work = Group("Workspace")
        change = button("Change")
        change.clicked.connect(self._pick_workspace)
        where = work.row("Agent folder", r["workspace"] or "Where agents work and put the things they "
                         "make. Not set yet.", change)
        if r["workspace"]:
            opener = button("Open")
            opener.clicked.connect(lambda: br.open_folder(r["workspace"]))
            where.layout().insertWidget(where.layout().count() - 1, opener)
        self.body.add(work)
        auto = Group("Autonomy")
        auto.toggle("Approve automatically", "Jobs keep going without asking. Paying, sending, "
                    "posting or deleting for others still asks.", br.auto_approve(), br.set_auto_approve)
        auto.toggle("Show me the plan first", "See a job's steps, and say yes, before it starts.",
                    r["plan"], lambda v: br.set_agent_rail("plan", v))
        self.body.add(auto)
        more = Group("More")
        more.link("Limits & models", "What they may run without asking, jobs at once, time and work "
                  "limits, where their requests go, and their history.",
                  lambda: self.navigate.emit("settings/agent-limits"))
        self.body.add(more)
        if br.workforce() is None:
            self.body.add(label("The agents did not start - see tora.log for why. Changes here "
                                "are kept and used when they do.", "setNote", wrap=True))


class AgentLimitsPage(AgentsTab):
    """Everything else about the agents, one step in from Agents."""

    HEADING = "Limits & models"
    BASICS = False


# ── TORA ──────────────────────────────────────────────────────────────────────

def _grid(tiles: list, columns: int) -> QWidget:
    from PyQt6.QtWidgets import QGridLayout
    box = QWidget()
    grid = QGridLayout(box)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(8)
    for i, tile in enumerate(tiles):
        grid.addWidget(tile, i // columns, i % columns)
    return box


def _keys(names: list[str]) -> QWidget:
    """Keycaps in a row, no stretch: they sit at a row's end."""
    box = QWidget()
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    for k in names:
        row.addWidget(label(k, "keycapSmall"))
    return box


class VoicePage(_Later, Tab):
    """Her voice as tiles (HeyClicky's way: tap one to hear it - it is hers
    from the next answer), the sound the moment she listens, and how she
    answers."""

    HEADING = "Voice"
    _sound = ToraPage._sound

    def build(self) -> None:
        from audio.tts.gemini_tts import VOICES
        from ui import tokens
        from ui.settings_parts import Group
        from ui.shape_widgets import VoiceTile
        br = self.bridge
        now = _ask(br, "voice", "") or ""
        voices = list(VOICES) + ([now] if now and now not in VOICES else [])
        self.tiles = []
        for i, name in enumerate(voices):
            tile = VoiceTile(name, tokens.VOICE_COLOURS[i % len(tokens.VOICE_COLOURS)])
            tile.setChecked(name == now)
            tile.clicked.connect(lambda _c=False, n=name: self._pick(n))
            self.tiles.append(tile)
        self.body.add(label("VOICE", "setGroup"))
        self.body.add(_grid(self.tiles, 4))
        self.body.add(label("Tap a voice to hear it. It is hers from the next answer.", "setNote"))
        self.body.add(self._sound())
        ui = Group("Interface sounds")
        ui.toggle("Sounds as HeyTora opens", "A soft sound as it grows out of the pill, and into "
                  "Settings.", br.switch("ui_sounds"), lambda on: br.set_switch("ui_sounds", on))
        self.body.add(ui)
        answers = Group("How she answers")
        routes = ("realtime", "cascade")
        route = Select("Live - answers in about a second",
                       "Classic - transcribe, think, then speak", width=280)
        route.setCurrentIndex(routes.index(br.route()) if br.route() in routes else 0)
        route.currentIndexChanged.connect(
            lambda k: br.set_route(routes[k]) if 0 <= k < 2 else None)
        answers.row("Answers", "", route)
        self.body.add(answers)

    def _pick(self, name: str) -> None:
        from ui.onboarding_parts import _clip_envelope, play_voice
        for tile in self.tiles:
            tile.setChecked(tile.name == name)
            if tile.name != name:
                tile.stop()
        if play_voice(name):                     # the bundled hello: no talk message spent
            tile = next(t for t in self.tiles if t.name == name)
            tile.play(_clip_envelope(name))      # its waveform moves with the clip
        _ask(self.bridge, "set_voice", None, name)


class MicrophonePage(GeneralTab):
    """Which microphone she listens through - every one listed, the chosen
    one ticked - a test with live bars, and listening for her name."""

    HEADING = "Microphone"
    TEST_S = 8.0

    def build(self) -> None:
        from ui.settings_parts import Group
        from ui.shape_widgets import LevelBars, Radio
        br = self.bridge
        self._stop_test()
        mics = _ask(br, "mics", []) or []
        now = _ask(br, "mic_index", None)
        chosen = now if any(i == now for i, _n, _d in mics) else next((i for i, _n, d in mics if d), None)
        self._device = chosen
        pick = Group("Input device")
        if not mics:
            pick.row("No microphone found", "Plug one in, then open this page again.")
        for index, name, default in mics:
            pick.choice(name, "System default" if default else "", lambda i=index: self._pick(i),
                        Radio(index == chosen))
        self.body.add(pick)
        test = Group("Test")
        self.bars = LevelBars()
        go = button("Test microphone")
        go.clicked.connect(self._test)
        row = test.row("Test microphone", "Speak normally - the bars move as Tora hears you.",
                       _row_controls(self.bars, go))
        row.setObjectName("setRowFirst")
        self.body.add(test)
        listen = Group("Listening")
        listen.toggle("Always listening", "Say “Tora” to start - no shortcut needed.",
                      br.switch("wake_word"), lambda on: br.set_switch("wake_word", on))
        self.body.add(listen)

    def _pick(self, index: int) -> None:
        _ask(self.bridge, "set_mic", None, index)
        self._later(self.refresh)

    def _test(self) -> None:
        """Listen on the chosen microphone for TEST_S, the bars moving."""
        import time
        self._stop_test()
        if self._device is None:
            return
        try:
            import numpy as np
            import sounddevice as sd
            level = [0.0]

            def hear(data, _frames, _time, _status):
                level[0] = float(np.sqrt(np.mean(np.square(data.astype("float32")))))
            self._stream = sd.InputStream(device=self._device, channels=1, samplerate=16000,
                                          dtype="float32", callback=hear)
            self._stream.start()
        except Exception:  # noqa: BLE001 - still bars: the microphone could not be opened
            self._stream = None
            return
        until = time.monotonic() + self.TEST_S
        self._timer = QTimer(self)
        self._timer.setInterval(60)

        def tick() -> None:
            if time.monotonic() > until or not self.isVisible():
                self._stop_test()
                return
            self.bars.level += (min(1.0, level[0] * 9) - self.bars.level) * 0.5
            self.bars.update()
        self._timer.timeout.connect(tick)
        self._timer.start()

    def _stop_test(self) -> None:
        timer, self._timer = getattr(self, "_timer", None), None
        if timer is not None:
            timer.stop()
        stream, self._stream = getattr(self, "_stream", None), None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001
                pass
        bars = getattr(self, "bars", None)
        if bars is not None:
            try:
                bars.level = 0.0
                bars.update()
            except RuntimeError:                 # the page was rebuilt meanwhile
                pass

    def hideEvent(self, e) -> None:
        self._stop_test()
        super().hideEvent(e)


class ShortcutsPage(GeneralTab):
    """The keys that summon her: each row its keys as keycaps and Change at
    the end, HeyClicky's way; Reset to defaults under them."""

    HEADING = "Shortcuts"
    DEFAULT = "ctrl+win"

    def build(self) -> None:
        from ui.settings_parts import Group
        br = self.bridge
        talk = Group("Talk to Tora")
        change = button("Change")
        change.clicked.connect(self._open_shortcut)
        held = _ask(br, "hotkey_keys", []) or [br.hotkey() or "Ctrl + Win"]
        talk.row("Push to talk", "Hold to talk; let go to send. A quick tap: she listens until you stop.",
                 _row_controls(_keys(held), change))
        # Not a switch: it is how you type to her (31 Oct 2026).
        talk.row("Type to Tora", "Tap Ctrl twice: HeyTora opens on Chat, ready to type.",
                 _keys(["Ctrl", "Ctrl"]))
        cursor = Group("Her cursor")
        cursor.row("Show or hide her cursor", "From anywhere, any time.",
                   _keys([k.strip().capitalize() for k in
                          str(_ask(br, "cursor_hotkey", "Win + Alt + H")).split("+")]))
        self.body.add(talk)
        self.body.add(cursor)
        reset = Group()
        undo = button("Reset", "ghost")
        undo.clicked.connect(self._reset)
        reset.row("Reset to defaults", "Push to talk goes back to Ctrl + Win.", undo)
        self.body.add(reset)

    def _reset(self) -> None:
        _ask(self.bridge, "set_hotkey", None, self.DEFAULT)
        self._later(self.refresh)


class CursorPage(_Later, Tab):
    """Her cursor's colour as tiles - her own cursor, glowing in each - and
    whether it shows, and how what she points at is drawn."""

    HEADING = "Cursor"
    TOPICS = frozenset({"cursor"})

    def build(self) -> None:
        from ui import tokens
        from ui.settings_parts import Group
        from ui.shape_widgets import CursorTile
        br = self.bridge
        now = _ask(br, "cursor_colour", "blue") or "blue"
        self.tiles = []
        for key, name, hex_colour in tokens.CURSOR_COLOURS:
            tile = CursorTile(key, name, hex_colour)
            tile.setChecked(key == now)
            tile.clicked.connect(lambda _c=False, k=key: self._pick(k))
            self.tiles.append(tile)
        self.body.add(label("COLOUR", "setGroup"))
        self.body.add(_grid(self.tiles, len(self.tiles)))
        seen = Group("Visibility")
        keys = _ask(br, "cursor_hotkey", "Win + Alt + H")
        seen.toggle("Show her cursor", f"Off: only her bar shows. {keys} switches it any time.",
                    bool(_ask(br, "cursor_shown", True)), lambda on: _ask(br, "set_cursor_shown", None, on))
        seen.toggle("Trail as a line", "Show what you point at with a smooth line instead of mist.",
                    br.switch("trail_line"), lambda on: br.set_switch("trail_line", on))
        self.body.add(seen)

    def _pick(self, key: str) -> None:
        for tile in self.tiles:
            tile.setChecked(tile.key == key)
        _ask(self.bridge, "set_cursor_colour", None, key)


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


def _row_controls(*widgets) -> QWidget:
    """Several controls at a row's end (a switch and Edit)."""
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    for w in widgets:
        lay.addWidget(w)
    return box


# What stands in for a logo where a service has none of its own.
NO_LOGO = {"files": "folder-open", "custom": "link", "composio": "blocks"}
LOGO_PX = 22


def _mark(row, service: str):
    """The service's logo before its name (assets/logos), or a plain icon."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QLabel
    from ui import app_theme as T
    from ui.app_widgets import icon, logo
    mark = QLabel()
    mark.setFixedSize(LOGO_PX + 6, LOGO_PX)
    pix = logo(service, LOGO_PX, mark.devicePixelRatioF())
    mark.setPixmap(pix if pix is not None else icon(NO_LOGO.get(service, "link"), T.TEXT_DIM, 18))
    row.layout().insertWidget(0, mark, 0, Qt.AlignmentFlag.AlignVCenter)
    return row


class IntegrationsPage(Tab):
    """Apps her jobs can reach: what is connected (Disconnect), and what can
    be (Connect) - the same tools as "Tora, connect Notion", so the page and
    her voice never disagree. A sign-in opens in the browser and she says
    when it is done; a service with a key opens its key page, and the key
    is read off the clipboard once, kept in Credential Manager and wiped
    from the clipboard - never typed into HeyTora, never shown."""

    HEADING = "Integrations"
    TOPICS = frozenset({"integrations"})

    def __init__(self, bridge):
        self._waiting: dict = {}             # the service whose key page is open
        self._said = ""
        self._busy = None
        super().__init__(bridge)

    def build(self) -> None:
        from ui.settings_parts import Group
        data = _ask(self.bridge, "integrations", {"connected": [], "available": [], "running": False}) or {}
        if self._waiting:
            w = self._waiting
            wait = Group("Waiting for your key")
            done = button("I've copied it", "primary")
            done.clicked.connect(lambda: self._connect(w, pasted=True))
            what = "a key" if w.get("how") == "key" else "your personal link"
            wait.row(w["name"], f"Its page is open in your browser. Make {what} there and copy it, then "
                                "press I've copied it.", done)
            self.body.add(wait)
        have = Group("Connected")
        for c in data.get("connected") or []:
            off = button("Disconnect", "ghost")
            off.clicked.connect(lambda _c=False, c=c: self._disconnect(c))
            _mark(have.row(c["name"], c["detail"], off), c["id"])
        if not data.get("connected"):
            have.row("Nothing yet", "Connect one below - or ask Tora: “connect Notion”.")
        self.body.add(have)
        add = Group("Add a connection")
        for s in data.get("available") or []:
            go = button("Connect")
            go.clicked.connect(lambda _c=False, s=s: self._connect(s))
            _mark(add.row(s["name"], (s.get("gives") or "").capitalize(), go), s["id"])
        custom = button("Add")
        custom.clicked.connect(lambda: self._connect({"id": "custom", "name": "Your server"}, pasted=True))
        _mark(add.row("Custom connector", "Any service with an MCP server: copy its https:// address, "
                                          "then press Add.", custom), "custom")
        editors = Group("Code editors")
        editors.link("VS Code and its forks", "Read as data - your file, errors, terminal - instead of "
                                              "screenshots.", lambda: self.navigate.emit("settings/devlink"))
        if not data.get("running"):
            add.note("Tora's jobs aren't running, so only Google can be connected right now.")
        self.body.add(add)
        self.body.add(editors)
        self.note = label(self._said, "setNote", wrap=True)
        self.note.setVisible(bool(self._said))
        self.body.add(self.note)

    def _say(self, words: str) -> None:
        self._said = words
        self.note.setText(words)
        self.note.setVisible(bool(words))

    def _connect(self, svc: dict, pasted: bool = False) -> None:
        from ui.settings_tabs import _Background
        if self._busy is not None:
            return
        self._say("Connecting…" if pasted else f"Opening {svc['name']}…")

        def done(answer: dict) -> None:
            self._busy = None
            if pasted:
                self._waiting = {}
            if answer.get("error"):
                self._said = answer["error"]
            elif answer.get("waiting_for_copy"):
                self._waiting, self._said = svc, ""
            elif answer.get("through"):
                self._said = (f"{svc['name']} works through your {answer['through']} link: switch it on "
                              f"there, and jobs that mention it can use it.")
            elif pasted:
                self._said = "Got it - connecting. Tora tells you when it's done."
            else:
                self._said = f"{svc['name']}: sign in in your browser. Tora tells you when it's connected."
            self.refresh()
        self._busy = _Background(self, lambda: self.bridge.connect_integration(svc["id"], pasted), done)

    def _disconnect(self, c: dict) -> None:
        from ui.settings_tabs import _Background
        if self._busy is not None:
            return

        def done(answer: dict) -> None:
            self._busy = None
            self._said = answer.get("error") or ("" if answer.get("ok") else answer.get("note", ""))
            self.refresh()
        self._busy = _Background(self, lambda: self.bridge.disconnect_integration(c["id"]), done)


def _when(iso: str) -> str:
    """"Thu 30 Oct, 09:00" from the scheduler's next run; words kept as they are."""
    from datetime import datetime
    try:
        d = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso
    return f"{d:%a} {d.day} {d:%b}, {d:%H:%M}"


class RoutinesPage(_Later, Tab):
    """Things Tora does on repeat, on this PC: the skills that run by
    themselves (their "Runs by itself" line), when each runs next and last
    ran, a switch each, and Edit."""

    HEADING = "Routines"
    TOPICS = frozenset({"skills", "agents"})
    edit_skill = ToraPage.edit_skill

    def build(self) -> None:
        from ui.settings_parts import Group
        from ui.shape_pages import _ago
        routines = _ask(self.bridge, "routines", []) or []
        new = button("New routine", "primary")
        new.clicked.connect(lambda: self.edit_skill(None))
        if not routines:
            empty = Group("Nothing on repeat yet")
            empty.row("Make one", "A skill with “Runs by itself”: every: weekdays 9am, every: Friday "
                                  "16:00, every: new file in Downloads\\Receipts.", new)
            self.body.add(empty)
            return
        group = Group("On repeat")
        for r in routines:
            words = [f"Every {r['every'].removeprefix('every').strip(': ')}"]
            if r["enabled"] and r.get("next"):
                words.append(f"next {_when(r['next'])}")
            if r.get("last_fired"):
                words.append(f"last ran {_ago(r['last_fired']).lower()}")
            switch = Switch(r["enabled"])
            switch.toggled.connect(lambda on, i=r["id"]: _ask(self.bridge, "set_skill_enabled", None, i, on))
            edit = button("Edit", "ghost")
            edit.clicked.connect(lambda _c=False, i=r["id"]: self.edit_skill(_ask(self.bridge, "skill", None, i)))
            group.row(r["name"], " · ".join(words), _row_controls(switch, edit))
        self.body.add(group)
        foot = QHBoxLayout()
        foot.addWidget(new)
        foot.addStretch(1)
        holder = QWidget()
        holder.setLayout(foot)
        self.body.add(holder)


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
    "integrations": IntegrationsPage,
    "agents": AgentsPage,
    "agent-limits": AgentLimitsPage,
    "routines": RoutinesPage,
}
