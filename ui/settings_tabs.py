"""The tabs of the Settings dialog (ui/settings_dialog.py, DESIGN.md §3D).

Settings was a long page - approvals, the account, updates, agent rails,
allowed actions, the microphone, appearance - and Setup, Voice & models and
half of Tutor were three more pages of switches. Wispr Flow's Settings is a
dialog with a menu down the side, so a thing is found by where it belongs.
These are those tabs. Each is a Page (title, body, live refresh), so a tab
redraws the way the app's pages do; the cards keep their words, only their
home changed.

    SETTINGS  General · System · Agents · Models · Advanced
    ACCOUNT   Account · Plans & billing · Data & privacy
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from ui import app_pages
from ui import app_theme as T
from ui.app_pages import Page, _ask, _checked_ago, _tidy
from ui.app_widgets import Card, Meter, Row, Select, ToggleRow, button, label


class Tab(Page):
    """A Settings tab: a Page whose cards sit on the dialog's white."""

    LIVE = True

    def _toggle(self, card: Card, name: str, heading: str, sub: str, *, first=False) -> None:
        row = ToggleRow(heading, sub, self.bridge.switch(name), first=first)
        row.switch.toggled.connect(lambda on, n=name: self.bridge.set_switch(n, on))
        card.add(row)

    def _slot(self, make) -> QWidget:
        """A card in a slot of its own, so it can be swapped in place."""
        slot = QWidget()
        lay = QVBoxLayout(slot)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(make())
        return slot

    @staticmethod
    def _swap(slot: Optional[QWidget], make) -> bool:
        if slot is None or not slot.layout():
            return False
        lay = slot.layout()
        while lay.count():
            old = lay.takeAt(0).widget()
            if old is not None:
                old.setParent(None)
                old.deleteLater()
        lay.addWidget(make())
        return True


# ── General ───────────────────────────────────────────────────────────────────

class GeneralTab(Tab):
    HEADING = "General"

    def build(self) -> None:
        br = self.bridge
        mic = Card("Microphone")
        devices = br.mics()
        dev = Select(*(name + ("  (system default)" if default else "")
                       for _i, name, default in devices) or ("No microphone found",),
                     width=340)
        current = br.mic_index()
        indexes = [i for i, _n, _d in devices]
        chosen = (indexes.index(current) if current in indexes else
                  next((k for k, (_i, _n, d) in enumerate(devices) if d), 0))
        dev.setCurrentIndex(chosen)
        dev.currentIndexChanged.connect(
            lambda k: br.set_mic(indexes[k]) if 0 <= k < len(indexes) else None)
        mic.add(Row("Input device", trailing=dev, first=True))
        mic.add(Row("Hold to talk", br.hotkey() or "not set",
                    trailing=label("set in .env as TORA_HOTKEY", "faint")))
        listen = ToggleRow("Always listening",
                           "Wake on “Tora”. Off means she only hears you while "
                           "the hotkey is held.", br.switch("wake_word"))
        listen.switch.toggled.connect(lambda on: br.set_switch("wake_word", on))
        mic.add(listen)
        self.body.add(mic)

        look = Card("Appearance")
        modes = (("system", "Match Windows"), ("light", "Light"), ("dark", "Dark"))
        theme = Select(*(words for _m, words in modes), width=180)
        keys = [m for m, _w in modes]
        theme.setCurrentIndex(keys.index(br.theme()) if br.theme() in keys else 0)
        theme.currentIndexChanged.connect(
            lambda k: br.set_theme(keys[k]) if 0 <= k < len(keys) else None)
        look.add(Row("Theme", "Light is heytora.com's soft grey; dark is its black.",
                     trailing=theme, first=True))
        self.body.add(look)


# ── System ────────────────────────────────────────────────────────────────────

class SystemTab(Tab):
    HEADING = "System"
    TOPICS = frozenset({"updates"})

    def refresh_topics(self, topics: set) -> None:
        """An update moved: only its card is swapped."""
        if "updates" in topics and not self._swap(getattr(self, "_updates_slot", None),
                                                    self._updates_card):
            self.refresh()

    def build(self) -> None:
        br = self.bridge
        pc = Card("HeyTora on this PC")
        start = ToggleRow("Start HeyTora when Windows starts",
                          "For you only - no admin rights needed.", br.autostart(), first=True)
        start.switch.toggled.connect(br.set_autostart)
        pc.add(start)
        panel = ToggleRow("Show the floating panel",
                          "The small window with the conversation in it.", br.panel_visible())
        panel.switch.toggled.connect(br.set_panel_visible)
        pc.add(panel)
        self.body.add(pc)
        self.body.add(self._sounds())
        self._updates_slot = self._slot(self._updates_card)
        self.body.add(self._updates_slot)

    def _sounds(self) -> Card:
        """The pop as she starts listening (ui/sounds.py). Choosing one plays it."""
        from ui import sounds
        card = Card("Sounds")
        keys = [k for k, _n in sounds.SOUNDS] + [sounds.OFF]
        pick = Select(*[n for _k, n in sounds.SOUNDS], "Off", width=200)
        now = _ask(self.bridge, "listen_sound", keys[0])
        pick.setCurrentIndex(keys.index(now) if now in keys else 0)
        pick.currentIndexChanged.connect(
            lambda i: self.bridge.set_listen_sound(keys[i]) if 0 <= i < len(keys) else None)
        card.add(Row("When she starts listening",
                     "A short pop the moment you can talk. Pick one to hear it.",
                     trailing=pick, first=True))
        return card

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


# ── Data & privacy ────────────────────────────────────────────────────────────

class PrivacyTab(Tab):
    """What Tora looks at, what she keeps, and the controls over both -
    Wispr's Data and Privacy, for what HeyTora actually stores: the journal,
    on this PC only."""

    HEADING = "Data & privacy"
    TOPICS = frozenset({"journal"})

    def build(self) -> None:
        br = self.bridge
        eyes = Card("What she looks at")
        self._toggle(eyes, "privacy_guard", "Privacy guard",
                     "Never send a screenshot when a password manager, banking "
                     "page or private window is in front.", first=True)
        self._toggle(eyes, "ocr", "Read text on screen (OCR)",
                     "Read fine print when you ask her to. Runs on this machine.")
        self._toggle(eyes, "web_search", "Web search",
                     "Look things up for news, prices and releases.")
        self.body.add(eyes)

        kept = Card("What she keeps")
        keep = ToggleRow("Keep a journal",
                         "Save every question and answer, so they come back for "
                         "review and show on Home. Off: nothing new is kept.",
                         br.switch("journal"), first=True)
        keep.switch.toggled.connect(lambda on: br.set_switch("journal", on))
        kept.add(keep)
        folder = button("Open folder")
        folder.clicked.connect(br.open_journal_folder)
        kept.add(Row("Stored on this PC only", "Your journal is a file on this computer - "
                     "it is not uploaded or synced.", trailing=folder))
        export = button("Export a copy")
        export.clicked.connect(self._export)
        kept.add(Row("Download your data", "Everything you asked, her answers and the "
                     "walkthroughs, as one file.", trailing=export))
        forget = button("Delete everything", "danger")
        forget.clicked.connect(self._delete)
        kept.add(Row("Delete your history", "Every question, answer and walkthrough, "
                     "for good. Your settings stay.", trailing=forget))
        self._note = label("", "faint", wrap=True)
        self._note.hide()
        kept.add(self._note)
        self.body.add(kept)

        allowed = Card("Desktop actions you allowed",
                       "Everything else on your desktop asks first, every time.")
        grants = br.grants()
        for i, key in enumerate(grants):
            forget_one = button("Forget")
            row = Row(key, "Runs without asking", dot=T.WARN, trailing=forget_one,
                      first=(i == 0))
            forget_one.clicked.connect(lambda _c=False, k=key, r=row: (br.forget_grant(k), r.hide()))
            allowed.add(row)
        if not grants:
            allowed.add(Row("Nothing is allowed without asking",
                            "Say “always allow” in a confirmation to add one.", first=True))
        self.body.add(allowed)

        links = QHBoxLayout()
        links.setSpacing(8)
        for words, path in (("Privacy policy", "/privacy"), ("Terms", "/terms")):
            b = button(words)
            b.clicked.connect(lambda _c=False, p=path: br.open_web(p))
            links.addWidget(b)
        links.addStretch(1)
        wrap = QWidget()
        wrap.setLayout(links)
        self.body.add(wrap)

    def _say(self, words: str) -> None:
        self._note.setText(words)
        self._note.show()

    def _export(self) -> None:
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(self, "Export your HeyTora history",
                                              "heytora-history.json", "JSON (*.json)")
        if not path:
            return
        try:
            n = self.bridge.export_history(path)
            self._say(f"Saved {n} entries to {path}.")
        except Exception as e:  # noqa: BLE001
            self._say(f"Couldn't save that: {e}")

    def _delete(self) -> None:
        from PyQt6.QtWidgets import QMessageBox
        sure = QMessageBox.question(
            self, "Delete your history?",
            "Every question, answer and walkthrough Tora has kept will be deleted. "
            "This can't be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if sure != QMessageBox.StandardButton.Yes:
            return
        n = self.bridge.delete_history()
        self._say(f"Deleted {n} entries.")


# ── Agents ────────────────────────────────────────────────────────────────────

class AgentsTab(Tab):
    HEADING = "Agents"

    def build(self) -> None:
        br = self.bridge
        # APP-14. One switch for "stop asking me": the field log had 71
        # approval boxes in three days and not one "no". What it still asks
        # about is said beside it, because a switch like this is only safe
        # when its edges are plain.
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

        # The workforce's own settings and endpoint editors - the ones the
        # tray opens. They save to config AND apply to the running agents, so
        # they are embedded rather than re-implemented (APP-11).
        wf = br.workforce()
        rails = Card("Agent rails", "Agents may only ever write inside the workspace.")
        ends = Card("Agent endpoints", "Where the agents' requests go.")
        if wf is not None:
            from agents.ui.accounts import ConnectedAccountsDialog
            from agents.ui.settings import AgentSettingsDialog
            rails.body.addWidget(_tidy(AgentSettingsDialog(wf), "Agent settings"))
            embedded = _tidy(ConnectedAccountsDialog(wf))
            embedded.setMinimumHeight(420)
            ends.body.addWidget(embedded)
        else:
            for card in (rails, ends):
                card.add(Row("The agents did not start",
                             "Nothing here can be changed until they do - see "
                             "tora.log for why.", dot=T.WARN, first=True))
        self.body.add(rails)
        self.body.add(ends)


# ── Models ────────────────────────────────────────────────────────────────────

class ModelsTab(Tab):
    HEADING = "Models"

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
        talk = Card("Conversation")
        routes = ("realtime", "cascade")
        route = Select("Live - answers in about a second",
                       "Classic - transcribe, think, then speak", width=320)
        route.setCurrentIndex(routes.index(br.route()) if br.route() in routes else 0)
        route.currentIndexChanged.connect(
            lambda k: br.set_route(routes[k]) if 0 <= k < 2 else None)
        talk.add(Row("How she talks", trailing=route, first=True))
        self.body.add(talk)

        models = Card("Models", "One job each. Set in .env; the list refreshes itself weekly.")
        roles = br.models()
        for i, (role, job, why) in enumerate(self._ROLES):
            if role in roles:
                models.add(Row(job, why, trailing=label(roles[role], "dim"), first=(i == 0)))
        self.body.add(models)


# ── Advanced ──────────────────────────────────────────────────────────────────

class AdvancedTab(Tab):
    HEADING = "Advanced"
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


# ── Account ───────────────────────────────────────────────────────────────────

class AccountTab(Tab):
    """Who you are to Tora, and the HeyTora account (1 Oct 2026): the plan
    and this month's use - or your own key. The plan card moves with every
    question, so it is swapped in place, never the whole tab."""

    HEADING = "Account"
    TOPICS = frozenset({"account"})

    # The allowances shown, in the user's words. "calls" is a hidden backstop.
    _USAGE = (("talk", "Talk", "messages"), ("agent", "Agent", "messages"),
              ("dictation", "Dictation", "dictations"))

    def showEvent(self, event) -> None:
        """On screen - opened or switched to: the plan is read again (at most
        once a minute). What is known shows at once; the answer redraws it."""
        super().showEvent(event)
        refresh = getattr(self.bridge, "refresh_account", None)
        if refresh is not None:
            refresh()

    def refresh_topics(self, topics: set) -> None:
        if "account" in topics and not self._swap(getattr(self, "_account_slot", None),
                                                    self._account_card):
            self.refresh()

    def build(self) -> None:
        you = Card("You")
        name = QLineEdit(_ask(self.bridge, "user_name", "") or "")
        name.setPlaceholderText("Your first name")
        name.setFixedWidth(240)
        saved = label("", "faint")
        save = button("Save")

        def _save() -> None:
            self.bridge.set_user_name(name.text())
            saved.setText("Saved.")
        save.clicked.connect(_save)
        name.returnPressed.connect(_save)
        box = QHBoxLayout()
        box.setSpacing(8)
        box.addWidget(saved)
        box.addWidget(name)
        box.addWidget(save)
        holder = QWidget()
        holder.setLayout(box)
        you.add(Row("Name", "The name Tora greets you by.", trailing=holder, first=True))
        self.body.add(you)
        self._account_slot = self._slot(self._account_card)
        self.body.add(self._account_slot)

    def _sign_out_button(self):
        out = button("Sign out")
        out.clicked.connect(lambda: app_pages.confirm_sign_out(self) and self.bridge.account_sign_out())
        return out

    def _account_card(self) -> Card:
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
            card.add(Row("Using your own Gemini key", "Change it in Settings → Advanced.",
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


# ── Plans & billing ───────────────────────────────────────────────────────────

# heytora-web src/lib/site.ts `pricing` - the same plans, words and prices.
# Change them there and here together.
PLANS = (
    {"key": "free", "name": "Free", "audience": "Best for trying it out", "price": "$0",
     "detail": "See what it does.",
     "features": ("25 asks a month", "Voice, with live captions", "25 tasks a month, up to 10 a day")},
    {"key": "pro", "name": "Pro", "audience": "Best for everyday use", "price": "$20",
     "detail": "Ask all day long.", "flag": "Popular",
     "features": ("Up to 300 asks a day", "Voice, with live captions", "150 tasks a month, up to 50 a day")},
    {"key": "max", "name": "Max", "audience": "Best for heavy use", "price": "$100",
     "detail": "Hand over the work.",
     "features": ("Up to 600 asks a day", "Voice, with live captions", "1,000 tasks a month, up to 200 a day")},
)
PLANS_NOTE = "Prices in US dollars. Paid plans open soon - nothing is charged until they do."


class PlansTab(Tab):
    """Wispr's Plans and Billing: the plans side by side, yours marked, and
    billing - which lives on heytora.com, where you pay."""

    HEADING = "Plans & billing"
    TOPICS = frozenset({"account"})

    def build(self) -> None:
        from PyQt6.QtWidgets import QFrame
        br = self.bridge
        st = _ask(br, "account_state", {}) or {}
        mine = ((st.get("summary") or {}).get("plan") or "").lower() if st.get("signed_in") else ""
        own_key = st.get("mode") == "own_key"

        box = QFrame()
        box.setObjectName("plansBox")
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        for i, plan in enumerate(PLANS):
            if i:
                line = QFrame()
                line.setObjectName("vr")
                line.setFixedWidth(1)
                row.addWidget(line)
            row.addWidget(self._column(plan, current=(plan["key"] == mine and not own_key)), 1)
        self.body.add(box)
        self.body.add(label(PLANS_NOTE, "faint", wrap=True))

        billing = Card("Billing")
        manage = button("Open billing")
        manage.clicked.connect(lambda: br.open_web("/pricing"))
        if own_key:
            words = "You're using your own Gemini key - Google bills you for it directly."
        else:
            words = "Payment details and invoices live on heytora.com, with your plan."
        billing.add(Row("Payment and invoices", words, trailing=manage, first=True))
        self.body.add(billing)

    def _column(self, plan: dict, current: bool) -> QWidget:
        from PyQt6.QtWidgets import QLabel
        from ui.app_widgets import icon
        from PyQt6.QtWidgets import QSizePolicy
        col = QWidget()
        # Three equal columns, whatever is in them (the flag widened Pro).
        col.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        lay = QVBoxLayout(col)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(4)
        top = QHBoxLayout()
        top.addWidget(label(plan["audience"], "faint"))
        top.addStretch(1)
        if plan.get("flag"):
            top.addWidget(label(plan["flag"], "planFlag"))
        lay.addLayout(top)
        lay.addSpacing(8)
        lay.addWidget(label(plan["name"], "planName"))
        price = QHBoxLayout()
        price.setSpacing(4)
        price.addWidget(label(plan["price"], "planPrice"), 0, Qt.AlignmentFlag.AlignBaseline)
        price.addWidget(label("/month", "dim"), 0, Qt.AlignmentFlag.AlignBaseline)
        price.addStretch(1)
        lay.addLayout(price)
        lay.addWidget(label(plan["detail"], "dim"))
        lay.addSpacing(10)
        for feature in plan["features"]:
            line = QHBoxLayout()
            line.setSpacing(8)
            tick = QLabel()
            tick.setPixmap(icon("check", T.OK, 14))
            line.addWidget(tick, 0, Qt.AlignmentFlag.AlignTop)
            line.addWidget(label(feature, wrap=True), 1)
            lay.addLayout(line)
        lay.addStretch(1)
        lay.addSpacing(14)
        if current:
            go = button("Your plan")
            go.setEnabled(False)
        else:
            go = button(f"Choose {plan['name']}", "primary" if plan.get("flag") else "ghost")
            go.clicked.connect(lambda: self.bridge.open_web("/pricing"))
        lay.addWidget(go)
        return col


# The menu, top to bottom: (section, [(key, words, icon, tab), ...]).
TABS = (
    ("Settings", (
        ("general", "General", "sliders-horizontal", GeneralTab),
        ("system", "System", "monitor", SystemTab),
        ("agents", "Agents", "bot", AgentsTab),
        ("models", "Models", "cpu", ModelsTab),
        ("advanced", "Advanced", "wrench", AdvancedTab),
    )),
    # Wispr's order: who you are, what you pay, what is kept about you.
    ("Account", (
        ("account", "Account", "circle-user-round", AccountTab),
        ("plans", "Plans & billing", "credit-card", PlansTab),
        ("privacy", "Data & privacy", "shield-check", PrivacyTab),
    )),
)
