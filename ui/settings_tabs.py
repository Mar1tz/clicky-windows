"""The tabs of the Settings dialog (ui/settings_dialog.py, DESIGN.md §3D).

Settings was a long page - approvals, the account, updates, agent rails,
allowed actions, the microphone, appearance - and Setup, Voice & models and
half of Tutor were three more pages of switches. Wispr Flow's Settings is a
dialog with a menu down the side, so a thing is found by where it belongs.
These are those tabs. Each is a Page (title, body, live refresh), so a tab
redraws the way the app's pages do; the cards keep their words, only their
home changed.

    SETTINGS  General · System · Agents · Advanced
    ACCOUNT   Account · Usage · Plans & billing · Data & privacy
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from ui import app_pages
from ui import app_theme as T
from ui.app_pages import Page, _ask, _checked_ago, _tidy
from ui.app_widgets import Meter, Select, button, label


class Tab(Page):
    """A Settings tab: a Page whose cards sit on the dialog's white."""

    LIVE = True

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
    """Wispr's General: how you talk to her (the shortcut, the microphone,
    always listening) - each a title, what it is now, and Change."""

    HEADING = "General"

    def build(self) -> None:
        from ui.settings_parts import Group, keycaps
        br = self.bridge
        talk = Group("Talking to Tora")
        change_keys = button("Change")
        change_keys.clicked.connect(self._open_shortcut)
        keys_row = talk.row("Shortcut", "", change_keys)
        hold = QHBoxLayout()
        hold.setSpacing(6)
        hold.addWidget(label("Hold", "setDesc"))
        hold.addWidget(keycaps(_ask(br, "hotkey_keys", []) or [br.hotkey() or "Ctrl + Win"]))
        hold.addWidget(label("and speak.", "setDesc"))
        hold.addStretch(1)
        keys_row.layout().itemAt(0).layout().addLayout(hold)
        change_mic = button("Change")
        change_mic.clicked.connect(self._open_mic)
        talk.row("Microphone", self._mic_words(), change_mic)
        talk.toggle("Always listening", "Say “Tora” to start - no shortcut needed.",
                    br.switch("wake_word"), lambda on: br.set_switch("wake_word", on))
        routes = ("realtime", "cascade")
        route = Select("Live - answers in about a second",
                       "Classic - transcribe, think, then speak", width=300)
        route.setCurrentIndex(routes.index(br.route()) if br.route() in routes else 0)
        route.currentIndexChanged.connect(
            lambda k: br.set_route(routes[k]) if 0 <= k < 2 else None)
        talk.row("How she answers", "", route)
        self.body.add(talk)

        look = Group("Appearance")
        modes = (("system", "Match Windows"), ("light", "Light"), ("dark", "Dark"))
        theme = Select(*(words for _m, words in modes), width=180)
        keys = [m for m, _w in modes]
        theme.setCurrentIndex(keys.index(br.theme()) if br.theme() in keys else 0)
        theme.currentIndexChanged.connect(
            lambda k: br.set_theme(keys[k]) if 0 <= k < len(keys) else None)
        look.row("Theme", "", theme)
        self.body.add(look)

    def _mic_words(self) -> str:
        mics = self.bridge.mics()
        now = self.bridge.mic_index()
        name = next((n for i, n, _d in mics if i == now), None)
        if name is None:
            name = next((n for _i, n, d in mics if d), "")
            return f"{name} (system default)" if name else "No microphone found"
        return name

    def _over(self) -> QWidget:
        """What a small dialog sits over: the Settings card when there is one."""
        w = self.window()
        dlg = getattr(w, "_settings", None)
        return dlg if dlg is not None and dlg.isVisible() else w

    def _open_shortcut(self) -> None:
        from ui.settings_parts import ShortcutDialog
        ShortcutDialog(self._over(), self.bridge, lambda: self._later(self.refresh)).open()

    def _open_mic(self) -> None:
        from ui.settings_parts import MicDialog
        MicDialog(self._over(), self.bridge, lambda: self._later(self.refresh)).open()

    def _later(self, fn) -> None:
        from PyQt6.QtCore import QTimer
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(fn)
        timer.timeout.connect(timer.deleteLater)
        timer.start(0)


# ── System ────────────────────────────────────────────────────────────────────

class SystemTab(Tab):
    HEADING = "System"
    TOPICS = frozenset({"updates"})

    def refresh_topics(self, topics: set) -> None:
        """An update moved: only its group is swapped."""
        if "updates" in topics and not self._swap(getattr(self, "_updates_slot", None),
                                                    self._updates_card):
            self.refresh()

    def build(self) -> None:
        from ui.settings_parts import Group
        br = self.bridge
        app = Group("App")
        app.toggle("Start HeyTora when Windows starts", "", br.autostart(), br.set_autostart)
        app.toggle("Show the floating panel", "The small window with your conversation.",
                   br.panel_visible(), br.set_panel_visible)
        self.body.add(app)
        self._updates_slot = self._slot(self._updates_card)
        self.body.add(self._updates_slot)

    def _updates_card(self) -> QWidget:
        """HeyTora keeps itself up to date (updates/service.py): which version
        this is, what is happening, and the one switch."""
        from ui.settings_parts import Group
        br = self.bridge
        try:
            st = br.update_state()
        except Exception:  # noqa: BLE001 - a bridge without updates
            st = {"state": "dev", "current": "", "latest": "", "notes": "", "progress": 0.0,
                  "error": "", "auto": True}
        g = Group("Updates")
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
            g.row(f"HeyTora {now}", words, check_button())
            return g
        if state == "ready":
            go = button("Restart to update", "primary")
            go.clicked.connect(br.install_update)
            g.row(f"HeyTora {latest} is ready", f"You have {now}. " + (
                "It installs by itself the next time HeyTora starts - or now."
                if st["auto"] else "Restart to update when it suits you."), go)
        elif state == "downloading":
            g.row(f"Downloading HeyTora {latest}…", f"You have {now} · {round(st['progress'] * 100)}%")
            g.add(Meter(st["progress"]), (20, 0, 20, 16))
        elif state == "installing":
            g.row(f"Installing HeyTora {latest}…", "HeyTora opens again in a moment.")
        elif state == "checking" or (st.get("busy") and state not in ("downloading", "ready")):
            g.row(f"HeyTora {now}", "Checking for updates…")
        elif state == "failed":
            page = button("Download page")
            page.clicked.connect(br.open_download_page)
            g.row(f"HeyTora {now}", st["error"] or "Couldn't update.", page)
            g.add(check_button("link"), (14, 0, 20, 12))
        else:
            if state == "current":
                words = "Up to date" + (f" · checked {ago}." if ago else ".")
            else:
                words = st.get("error") or "Checks for updates every few hours."
            g.row(f"HeyTora {now}", words, check_button())
        if st["notes"] and state in ("ready", "downloading"):
            g.note(f"What's new: {st['notes']}")
        g.toggle("Install updates automatically", "Your settings are kept.", st["auto"],
                 br.set_auto_update)
        return g


# ── Data & privacy ────────────────────────────────────────────────────────────

class PrivacyTab(Tab):
    """What Tora looks at, what she keeps, and the controls over both -
    Wispr's Data and Privacy, for what HeyTora actually stores: the journal,
    on this PC only."""

    HEADING = "Data & privacy"
    TOPICS = frozenset({"journal", "skills"})

    def build(self) -> None:
        from ui.settings_parts import Group
        br = self.bridge

        def switch(g, name, title, desc):
            g.toggle(title, desc, br.switch(name), lambda on, n=name: br.set_switch(n, on))

        eyes = Group("What she looks at")
        switch(eyes, "privacy_guard", "Privacy guard",
               "No screenshots of password managers, banking or private windows.")
        switch(eyes, "ocr", "Read text on screen (OCR)", "For fine print. Runs on this PC.")
        switch(eyes, "web_search", "Web search", "For news, prices and anything recent.")
        self.body.add(eyes)

        kept = Group("What she keeps")
        switch(kept, "journal", "Keep a journal", "Your questions and her answers, for Home and reviews.")
        folder = button("Open folder")
        folder.clicked.connect(br.open_journal_folder)
        kept.row("Stored on this PC only", "Never uploaded or synced.", folder)
        export = button("Export a copy")
        export.clicked.connect(self._export)
        kept.row("Download your data", "Everything she kept, as one file.", export)
        forget = button("Delete everything", "danger")
        forget.clicked.connect(self._delete)
        kept.row("Delete your history", "Your settings stay.", forget)
        self._note = kept.note("")
        self._note.hide()
        self.body.add(kept)

        allowed = Group("Allowed without asking")
        grants = br.grants()
        for key in grants:
            forget_one = button("Forget")
            row = allowed.row(key, "Runs without asking", forget_one)
            forget_one.clicked.connect(lambda _c=False, k=key, r=row: (br.forget_grant(k), r.hide()))
        if not grants:
            allowed.row("Nothing is allowed without asking",
                        "Say “always allow” when she asks, to add one.")
        self.body.add(allowed)

        # The agents' own browser (SK-06): the sites the user signed in to,
        # once, so skills can run in the background. On this PC only.
        sites = _ask(br, "browser_sites", []) or []
        signed = Group("Signed in for the agents")
        for site in sites:
            signed.row(site["name"], site["site"])
        if sites:
            out = button("Sign out everywhere", "danger")
            out.clicked.connect(self._sign_out_sites)
            signed.row("Sign the agents' browser out", "Of every site above. Your own browser "
                       "isn't touched.", out)
        else:
            signed.row("Not signed in anywhere", "When a job needs a site, she asks you to sign "
                       "in once, in a window you can see.")
        self.body.add(signed)

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

    def _sign_out_sites(self) -> None:
        from PyQt6.QtWidgets import QMessageBox
        sure = QMessageBox.question(self, "Sign out everywhere",
                                    "Sign the agents' browser out of every site? Jobs will ask "
                                    "you to sign in again when they need to.",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                    QMessageBox.StandardButton.Cancel)
        if sure != QMessageBox.StandardButton.Yes:
            return
        why = self.bridge.forget_browser_sites()
        if why:
            self._say(why)
        else:
            self.refresh()

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

# (value, words) for the rails' choices - picked, never typed or scrolled.
RISKS = (("safe", "Nothing - every change asks"), ("low", "Changes inside the workspace"),
         ("medium", "Also commands and installs"), ("high", "Everything, deletes too"))
AT_ONCE = ((1, "1"), (2, "2"), (3, "3"), (5, "5"), (10, "10"), (0, "No limit"))
MINUTES = ((10, "10 minutes"), (30, "30 minutes"), (60, "1 hour"), (120, "2 hours"),
           (240, "4 hours"), (0, "No limit"))
TOKENS = ((100_000, "100k"), (300_000, "300k"), (1_000_000, "1 million"),
          (3_000_000, "3 million"), (0, "No limit"))


def _choice(options, now, changed, width: int = 220) -> Select:
    """A dropdown over fixed choices; a value saved that is not one of them
    (set by hand in .env) is shown as itself, first."""
    values = [v for v, _w in options]
    words = [w for _v, w in options]
    if now not in values:
        values.insert(0, now)
        words.insert(0, str(now))
    pick = Select(*words, width=width)
    pick.setCurrentIndex(values.index(now))
    pick.currentIndexChanged.connect(lambda k: changed(values[k]) if 0 <= k < len(values) else None)
    return pick


class AgentsTab(Tab):
    """What the agents may do on their own, where they work, how much one
    job may use, and where their requests go - Wispr's rows: a title, one
    line, the control or Change. Every choice saves as it is made, and is
    put on the running agents at once (bridge.set_agent_rail)."""

    HEADING = "Agents"

    def build(self) -> None:
        from ui.settings_parts import Group
        br = self.bridge
        r = _ask(br, "agent_rails", None) or {"workspace": "", "risk": "medium", "plan": False,
                                               "at_once": 3, "minutes": 30, "tokens": 300000}
        rail = lambda name: (lambda v: br.set_agent_rail(name, v))   # noqa: E731

        # APP-14. One switch for "stop asking me": the field log had 71
        # approval boxes in three days and not one "no". What it still asks
        # about is in the line under it - a switch like this is only safe
        # when its edges are plain.
        asks = Group("When they ask you")
        asks.toggle("Approve automatically",
                    "Still asks, every time: anything that pays, files a return, sends, "
                    "posts or deletes for others to see.", br.auto_approve(), br.set_auto_approve)
        asks.toggle("Show me the plan first", "Approve a job's steps before it starts.",
                    r["plan"], rail("plan"))
        asks.row("Run without asking, up to", "Anything above this stops and asks.",
                 _choice(RISKS, r["risk"], rail("risk"), 240))
        self.body.add(asks)

        work = Group("Where they work")
        change = button("Change")
        change.clicked.connect(self._pick_workspace)
        where = work.row("Workspace", r["workspace"] or "Not set", change)
        if r["workspace"]:
            opener = button("Open")
            opener.clicked.connect(lambda: br.open_folder(r["workspace"]))
            where.layout().insertWidget(where.layout().count() - 1, opener)
        self.body.add(work)

        limits = Group("Limits for each job")
        limits.row("Jobs at once", "More wait their turn.",
                   _choice(AT_ONCE, r["at_once"], rail("at_once"), 160))
        limits.row("Time limit", "A job past it stops and says what it got done.",
                   _choice(MINUTES, r["minutes"], rail("minutes"), 160))
        limits.row("Token budget", "How much AI work one job may use.",
                   _choice(TOKENS, r["tokens"], rail("tokens"), 160))
        self.body.add(limits)

        manage = button("Manage")
        manage.clicked.connect(self._manage_endpoints)
        ends = Group("Where their requests go", manage)
        endpoints = _ask(br, "endpoints", []) or []
        for e in endpoints:
            ends.row(e["name"], e["url"] + ("" if e["on"] else " · off"))
        if not endpoints:
            ends.row("HeyTora's own models", "Add an OpenAI-compatible endpoint to use your own.")
        self.body.add(ends)

        history = Group("History")
        clear = button("Clear", "danger")
        clear.clicked.connect(self._clear_history)
        history.row("Clear agent history", "Forgets finished jobs. The files they made stay.", clear)
        self.body.add(history)

        if br.workforce() is None:
            self.body.add(label("The agents did not start - see tora.log for why. Changes here "
                                "are kept and used when they do.", "setNote", wrap=True))

    def _pick_workspace(self) -> None:
        import os
        from PyQt6.QtWidgets import QFileDialog
        now = (_ask(self.bridge, "agent_rails", {}) or {}).get("workspace", "")
        chosen = QFileDialog.getExistingDirectory(self, "Where the agents work", now)
        if chosen:
            self.bridge.set_agent_rail("workspace", os.path.normpath(chosen))
            self.refresh()

    def _manage_endpoints(self) -> None:
        """The workforce's own endpoint editor - name, URL, key, Test, and
        the fallback routing - in a dialog over Settings, not on the tab."""
        from ui.settings_parts import SubDialog
        wf = self.bridge.workforce()
        w = self.window()
        over = getattr(w, "_settings", None)
        over = over if over is not None and over.isVisible() else w
        dlg = SubDialog(over, "Where their requests go",
                        "Any OpenAI-compatible endpoint: a gateway, a local server, or api.openai.com.")
        dlg.W = 680
        try:
            from agents.ui.accounts import ConnectedAccountsDialog
            editor = _tidy(ConnectedAccountsDialog(wf))
            for lbl in editor.findChildren(QWidget):
                if getattr(lbl, "text", None) and callable(lbl.text) and lbl.objectName() == "acctHint" \
                        and lbl.text().startswith("Any OpenAI-compatible"):
                    lbl.hide()                       # said in the dialog's own line
            editor.setMinimumHeight(440)
            editor.saved.connect(lambda: (dlg.close_dialog(), self.refresh()))
            dlg.body.addWidget(editor)
        except Exception as e:  # noqa: BLE001
            dlg.body.addWidget(label(f"The endpoint editor could not open: {e}", "setDesc", wrap=True))
        dlg.open()

    def _clear_history(self) -> None:
        from PyQt6.QtWidgets import QMessageBox
        sure = QMessageBox.question(self, "Clear agent history",
                                    "Forget every finished job? The files they made are kept.",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                    QMessageBox.StandardButton.Cancel)
        if sure == QMessageBox.StandardButton.Yes:
            self.bridge.clear_agent_history()


# ── Advanced ──────────────────────────────────────────────────────────────────

# What each model role is FOR, in words (gemini.Models.all_roles).
MODEL_ROLES = (("realtime", "Conversation", "Hears you and answers, live"),
               ("vision_fast", "Quick look", "Fast; plans a walkthrough's first step"),
               ("vision_deep", "Careful look", "Slower, much better at detail"),
               ("planner", "Careful planner", "Only when the quick plan fails"),
               ("stt", "Transcription", "Wake word, and the classic route"),
               ("tts", "Tora's own voice", "Walkthrough steps and local replies"),
               ("embed", "Memory", "Finding what she remembers"))


class AdvancedTab(Tab):
    HEADING = "Advanced"
    TOPICS = frozenset({"state", "account", "agents"})

    _LEVEL = {"ok": T.OK, "warn": T.WARN, "bad": T.DANGER}

    def build(self) -> None:
        from ui.app_widgets import Dot
        from ui.settings_parts import Group
        br = self.bridge
        change = button("Change keys…", "primary")
        change.clicked.connect(br.open_keys)
        keys = Group("API keys", change)
        problem = br.problem()
        for k in br.keys():
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
            keys.row(k["label"], state, Dot(colour, 8))
        keys.note("Stored in .env on this PC - never shown here.")
        self.body.add(keys)

        refresh = button("Refresh list")
        refresh.clicked.connect(br.refresh_models)
        models = Group("Models in use", refresh)
        roles = br.models()
        for role, job, why in MODEL_ROLES:
            if role in roles:
                models.row(job, why, label(roles[role], "setDesc"))
        models.note("Set in .env; the list refreshes itself weekly.")
        self.body.add(models)

        checks = Group("Diagnostics")
        for name, detail, level in br.diagnostics():
            checks.row(name, detail, Dot(self._LEVEL.get(level, T.TEXT_FAINT), 8))
        checks.note("What is working right now. Nothing here goes online.")
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
    """Who you are to HeyTora (Wispr's Account): your name, the email you
    sign in with, how Tora thinks (your plan or your own key), sign out,
    and closing the account."""

    HEADING = "Account"
    TOPICS = frozenset({"account"})

    def showEvent(self, event) -> None:
        super().showEvent(event)
        refresh = getattr(self.bridge, "refresh_account", None)
        if refresh is not None:
            refresh()

    def build(self) -> None:
        from ui.app_popover import Avatar
        from ui.settings_parts import Group
        br = self.bridge
        st = _ask(br, "account_state", {}) or {}
        you = Group("Profile")
        name = QLineEdit(_ask(br, "user_name", "") or "")
        name.setPlaceholderText("Your first name")
        name.setFixedWidth(220)
        saved = label("", "setNote")
        save = button("Save")

        def _save() -> None:
            br.set_user_name(name.text())
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
        you.row("Name", "The name Tora greets you by.", holder)
        if st.get("email"):
            you.row("Email", "", label(st["email"], "setDesc"))
        you.row("Picture", "", Avatar(name.text() or st.get("email") or "?", 34))
        self.body.add(you)

        access = Group("How Tora thinks")
        if not st.get("configured"):
            access.row("HeyTora accounts aren't set up in this build", "Use your own Gemini key.")
        elif st.get("mode") == "own_key":
            access.row("Using your own Gemini key", "Change it in Settings → Advanced.")
            if st.get("signed_in"):
                switch = button("Use my plan instead")
                switch.clicked.connect(lambda: br.set_access_mode("account"))
                access.row("Your HeyTora plan", "Think on your plan instead of your key.", switch)
            else:
                switch = button("Use a HeyTora account instead")
                switch.clicked.connect(lambda: (br.set_access_mode("account"), br.run_setup()))
                access.row("Have a HeyTora plan?", "Sign in and use it here.", switch)
        elif not st.get("signed_in"):
            sign_in = button("Sign in…", "primary")
            sign_in.clicked.connect(br.run_setup)
            access.row("Not signed in", "Sign in with the email you used on the website.", sign_in)
        else:
            own = button("Use my own key")
            own.clicked.connect(lambda: (br.set_access_mode("own_key"), br.run_setup()))
            access.row("Your HeyTora plan", "Answers count towards your plan (see Usage).", own)
        self.body.add(access)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        if st.get("signed_in"):
            out = button("Sign out")
            out.clicked.connect(lambda: app_pages.confirm_sign_out(self) and br.account_sign_out())
            foot.addWidget(out)
            close = button("Delete account", "link")
            close.clicked.connect(lambda: self._close_account(st.get("email") or ""))
            foot.addWidget(close)
        foot.addStretch(1)
        wrap = QWidget()
        wrap.setLayout(foot)
        self.body.add(wrap)

    def _close_account(self, email: str) -> None:
        """Closing the account, done by HeyTora's server once the email is
        typed to confirm (settings_parts.DeleteAccountDialog)."""
        from ui.settings_parts import DeleteAccountDialog
        w = self.window()
        over = getattr(w, "_settings", None)
        over = over if over is not None and over.isVisible() else w
        DeleteAccountDialog(over, self.bridge, email, self.bridge.account_deleted).open()


# ── Usage ─────────────────────────────────────────────────────────────────────

class UsageTab(Tab):
    """This month on your plan: each allowance with how much is used and
    left, today's, the overall share, and when it starts again. It moves
    with every question, so it is redrawn in place (refresh_topics)."""

    HEADING = "Usage"
    TOPICS = frozenset({"account"})

    # The allowances shown, in the user's words. "calls" is a hidden backstop.
    _USAGE = (("talk", "Talk", "messages"), ("agent", "Agent", "messages"),
              ("dictation", "Dictation", "dictations"))

    def showEvent(self, event) -> None:
        """On screen: the plan is read again (at most once a minute); what
        is known shows at once and the answer redraws it."""
        super().showEvent(event)
        refresh = getattr(self.bridge, "refresh_account", None)
        if refresh is not None:
            refresh()

    def refresh_topics(self, topics: set) -> None:
        if "account" in topics and not self._swap(getattr(self, "_slot_w", None), self._usage):
            self.refresh()

    def build(self) -> None:
        self._slot_w = self._slot(self._usage)
        self.body.add(self._slot_w)

    def _usage(self) -> QWidget:
        from account.service import resets_on
        from ui.settings_parts import Group
        br = self.bridge
        st = br.account_state()
        host = QWidget()
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(T.GAP)
        if st["mode"] == "own_key":
            g = Group("This month")
            g.row("Using your own Gemini key", "Google bills you directly - there are no plan limits here.")
            col.addWidget(g)
            return host
        if not st["configured"] or not st["signed_in"]:
            g = Group("This month")
            sign_in = button("Sign in…", "primary")
            sign_in.clicked.connect(br.run_setup)
            g.row("Not signed in", "Sign in to see your plan and this month's use.", sign_in)
            col.addWidget(g)
            return host
        summary = st["summary"]
        if summary is None:
            g = Group("This month")
            if st["error"] and not st["busy"]:
                again = button("Try again")
                again.clicked.connect(lambda: br.refresh_account(force=True))
                g.row("Couldn't read your plan", st["error"], again)
            else:
                g.row("Reading your plan…", st["email"] or "")
            col.addWidget(g)
            return host

        plan = summary.get("plan_name") or (summary.get("plan") or "").capitalize() or "HeyTora"
        when = resets_on(summary)
        change = button("Change plan")
        change.clicked.connect(lambda: self.navigate.emit("settings/plans"))
        g = Group(f"{plan} plan", change)
        blocked = summary.get("blocked") or {}
        if st["error"] and not st["busy"]:
            again = button("Try again")
            again.clicked.connect(lambda: br.refresh_account(force=True))
            g.row("Couldn't update just now", "This is your last known use.", again)
        if blocked.get("all") == "budget":
            g.row("This month's use is up", f"Everything starts again on {when} - or change plan, "
                  "or use your own Gemini key.")
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
            share = 0.0 if limit is None else min(1.0, used / max(1, int(limit)))
            r = g.row(name, words)
            # Amber from 80% used, as on Insights: the bar warns before it runs out.
            r.layout().itemAt(0).layout().addWidget(
                Meter(share, colour=T.WARN if share >= 0.8 or blocked.get(kind) else T.ACCENT))
        fair = summary.get("fair_use")
        if isinstance(fair, (int, float)):
            r = g.row("Overall use", f"{round(fair * 100)}% of this month")
            r.layout().itemAt(0).layout().addWidget(
                Meter(min(1.0, float(fair)), colour=T.WARN if fair >= 0.8 else T.ACCENT))
        g.note(f"Starts again on {when}. Everything Tora does for you - looking at your screen, "
               "planning, agents thinking - counts a little towards overall use.")
        col.addWidget(g)
        return host


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
PLANS_NOTE = ("Prices in US dollars, plus tax where it applies. Paddle, our reseller, handles "
              "the payment - your card never reaches us.")


def _day(iso) -> str:
    """"6 Nov" from the server's ISO time; "" when there is none."""
    from datetime import datetime
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return ""
    return f"{d.day} {d:%b}"


class _Background:
    """Run one network call off the UI thread and hand its answer back on
    it. Kept by the tab while it runs; a tab closed meanwhile is not told."""

    def __init__(self, owner: QWidget, work, done):
        import threading
        from PyQt6.QtCore import QObject, pyqtSignal

        class _Relay(QObject):
            heard = pyqtSignal(object)

        self.relay = _Relay(owner)
        self.relay.heard.connect(done)

        def run() -> None:
            answer = work()
            try:
                self.relay.heard.emit(answer)
            except RuntimeError:
                pass                                   # the tab is gone
        threading.Thread(target=run, name="heytora-billing", daemon=True).start()


class PlansTab(Tab):
    """Wispr's Plans and Billing: the plans side by side, yours marked, and
    billing. Choosing a paid plan opens Paddle's checkout in the browser (or,
    already paying, changes the plan at once); the plan here changes when
    Paddle tells HeyTora's server, and this tab watches for it."""

    HEADING = "Plans & billing"
    TOPICS = frozenset({"account"})

    def showEvent(self, event) -> None:
        """On screen: the plan is read again - it may have been changed on
        Paddle's page since (a cancel, a new card)."""
        super().showEvent(event)
        refresh = getattr(self.bridge, "refresh_account", None)
        if refresh is not None:
            refresh(force=True)

    def build(self) -> None:
        from PyQt6.QtWidgets import QFrame
        br = self.bridge
        st = _ask(br, "account_state", {}) or {}
        summary = (st.get("summary") or {}) if st.get("signed_in") else {}
        mine = (summary.get("plan") or "").lower()
        own_key = st.get("mode") == "own_key"
        self._st, self._summary, self._mine = st, summary, mine
        self._busy: Optional[_Background] = None

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
            # A plan cancelled but not yet ended is offered again: Resume keeps it.
            yours = plan["key"] == mine and not own_key
            ending = yours and bool(summary.get("plan_cancel_at"))
            row.addWidget(self._column(plan, current=yours and not ending, resume=ending), 1)
        self.body.add(box)
        self.body.add(label(PLANS_NOTE, "faint", wrap=True))

        from ui.settings_parts import Group
        billing = Group("Billing")
        if own_key:
            billing.row("Payment and invoices",
                        "You're using your own Gemini key - Google bills you for it directly.")
        elif not st.get("signed_in"):
            sign_in = button("Sign in…", "primary")
            sign_in.clicked.connect(br.run_setup)
            billing.row("Not signed in", "Sign in to choose a plan.", sign_in)
        else:
            status = summary.get("plan_status") or ""
            name = summary.get("plan_name") or mine.capitalize()
            if mine != "free" and status == "past_due":
                billing.row(f"{name}: your last payment didn't go through",
                            "Update your card in Manage billing to keep the plan.")
            elif mine != "free" and summary.get("plan_cancel_at"):
                billing.row(f"{name} until {_day(summary['plan_cancel_at'])}",
                            f"It won't renew. Resume {name} above to keep it.")
            elif mine != "free" and summary.get("plan_renews_at"):
                billing.row(f"{name}, renews on {_day(summary['plan_renews_at'])}",
                            "Change or cancel any time; a change is prorated.")
            if summary.get("billing"):
                manage = button("Manage billing")
                manage.clicked.connect(self._manage)
                billing.row("Payment and invoices",
                            "Your card, invoices and cancelling, on Paddle's secure page.", manage)
            else:
                billing.row("No billing yet", "You're on Free. Choose a plan above - you pay on "
                            "Paddle's secure checkout, in your browser.")
        self._note = billing.note("")
        self._note.hide()
        self.body.add(billing)

    def _say(self, words: str) -> None:
        self._note.setText(words)
        self._note.setVisible(bool(words))

    def _choose(self, plan: dict, go) -> None:
        """A paid plan: Paddle's checkout, or the change made at once. Free,
        while paying: cancelling is on the billing page."""
        br = self.bridge
        if not self._st.get("signed_in"):
            br.run_setup()
            return
        if plan["key"] == "free":
            self._say("To go back to Free, cancel in Manage billing - your plan stays until "
                      "the end of what you've paid for.")
            self._manage()
            return
        if self._busy is not None:
            return
        resuming = plan["key"] == self._mine and bool(self._summary.get("plan_cancel_at"))
        label = go.text()
        go.setEnabled(False)
        go.setText("Resuming…" if resuming else "Opening checkout…" if self._mine == "free" else "Changing…")

        def done(answer: dict) -> None:
            self._busy = None
            go.setEnabled(True)
            go.setText(label)
            if answer.get("error"):
                self._say(answer["error"])
            elif answer.get("url"):
                from PyQt6.QtCore import QUrl
                from PyQt6.QtGui import QDesktopServices
                QDesktopServices.openUrl(QUrl(answer["url"]))
                self._say(f"Finish paying in your browser - you'll be on {plan['name']} here as "
                          "soon as Paddle confirms it.")
                br.watch_for_plan(plan["key"])
            elif answer.get("changed") and resuming:
                self._say(f"Done - {plan['name']} carries on and renews as usual.")
                br.watch_for_plan(plan["key"])
            elif answer.get("changed"):
                self._say(f"Done - you're on {plan['name']}. Paddle prorates the difference.")
                br.watch_for_plan(plan["key"])
            elif answer.get("same"):
                self._say(f"You're already on {plan['name']}.")
        self._busy = _Background(self, lambda: br.choose_plan(plan["key"]), done)

    def _manage(self) -> None:
        """Paddle's customer portal: card, invoices, cancel."""
        if self._busy is not None:
            return
        br = self.bridge

        def done(answer: dict) -> None:
            self._busy = None
            if answer.get("url"):
                from PyQt6.QtCore import QUrl
                from PyQt6.QtGui import QDesktopServices
                QDesktopServices.openUrl(QUrl(answer["url"]))
                br.watch_billing()                    # a cancel there shows here
            else:
                self._say(answer.get("error") or "The billing page didn't open - try again.")
        self._busy = _Background(self, br.billing_page, done)

    def _column(self, plan: dict, current: bool, resume: bool = False) -> QWidget:
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
            go = (button(f"Resume {plan['name']}", "primary") if resume else
                  button(f"Choose {plan['name']}", "primary" if plan.get("flag") else "ghost"))
            go.clicked.connect(lambda _c=False, b=go: self._choose(plan, b))
        lay.addWidget(go)
        return col


# The menu, top to bottom: (section, [(key, words, icon, tab), ...]).
TABS = (
    ("Settings", (
        ("general", "General", "sliders-horizontal", GeneralTab),
        ("system", "System", "monitor", SystemTab),
        ("agents", "Agents", "bot", AgentsTab),
        ("advanced", "Advanced", "wrench", AdvancedTab),
    )),
    # Wispr's order: who you are, what you pay, what is kept about you.
    ("Account", (
        ("account", "Account", "circle-user-round", AccountTab),
        ("usage", "Usage", "chart-column", UsageTab),
        ("plans", "Plans & billing", "credit-card", PlansTab),
        ("privacy", "Data & privacy", "shield-check", PrivacyTab),
    )),
)
