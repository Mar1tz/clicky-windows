from PyQt6.QtWidgets import QSystemTrayIcon
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QBrush
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QSize, pyqtSignal, QObject

from config import cfg


def taskbar_is_light() -> bool:
    """Windows' "system mode" - the taskbar's colour, which can differ from
    the apps' (a dark taskbar over light apps is the Windows 11 default)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "SystemUsesLightTheme")[0] == 1
    except Exception:  # noqa: BLE001
        return False


def _make_tray_icon(active: bool) -> QIcon:
    """HeyTora's icon (assets/brand/heytora-icon.svg), with a small dot
    while Tora is listening, thinking or speaking (heytora-web BRAND.md: the
    dot is the product's "on" signal). The icon is its own blue, which reads
    on a light or a dark taskbar; the dot gets a ring in the taskbar's
    colour so it stays separate from it. A plain circle if the SVG is
    missing. Must be called AFTER QApplication exists (Qt requirement)."""
    from ui import tokens
    ground = "#F3F3F3" if taskbar_is_light() else "#202020"
    ink = "#009EFD"
    icon = QIcon()
    for side in (16, 20, 24, 32, 40, 48, 64):
        px = QPixmap(QSize(side, side))
        px.fill(Qt.GlobalColor.transparent)
        painter = QPainter(px)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            from PyQt6.QtCore import QByteArray, QRectF
            from PyQt6.QtSvg import QSvgRenderer
            svg = (tokens.assets_dir() / "brand" / "heytora-icon.svg").read_text(encoding="utf-8")
            QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(painter, QRectF(0, 0, side, side))
        except Exception:  # noqa: BLE001
            painter.setBrush(QBrush(QColor(ink)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(side // 8, side // 8, side * 3 // 4, side * 3 // 4)
        if active:
            d = max(5, side * 3 // 8)
            ring = max(1, side // 16)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(ground)))
            painter.drawEllipse(side - d - ring, side - d - ring, d + ring * 2, d + ring * 2)
            painter.setBrush(QBrush(QColor(tokens.BLUE)))
            painter.drawEllipse(side - d, side - d, d, d)
        painter.end()
        icon.addPixmap(px)
    return icon


class TrayManager(QObject):
    """Windows system tray icon and context menu."""

    on_show_panel         = pyqtSignal()
    on_hide_panel         = pyqtSignal()
    on_quit               = pyqtSignal()
    on_toggle_search      = pyqtSignal(bool)
    on_toggle_wake_word   = pyqtSignal(bool)
    on_toggle_slow_mode   = pyqtSignal(bool)
    on_toggle_auto_advance = pyqtSignal(bool)
    on_toggle_quiz_mode   = pyqtSignal(bool)
    on_toggle_privacy     = pyqtSignal(bool)
    on_set_conversation_mode = pyqtSignal(str)  # "realtime" | "cascade"
    on_set_voice          = pyqtSignal(str)     # prebuilt Gemini voice name
    on_refresh_models     = pyqtSignal()
    on_stop               = pyqtSignal()
    on_toggle_code_mode   = pyqtSignal(bool)
    on_toggle_multilang   = pyqtSignal(bool)
    on_toggle_journal     = pyqtSignal(bool)
    on_toggle_ocr         = pyqtSignal(bool)
    on_record_start       = pyqtSignal()
    on_record_stop        = pyqtSignal()
    on_collab_start       = pyqtSignal()
    on_collab_join        = pyqtSignal()
    on_workflow_start     = pyqtSignal()
    on_workflow_stop      = pyqtSignal()
    on_journal_open       = pyqtSignal()
    on_attach_doc         = pyqtSignal()
    on_run_setup          = pyqtSignal()
    on_api_keys           = pyqtSignal()
    on_diagnostics        = pyqtSignal()
    on_set_mic_device     = pyqtSignal(int)     # sounddevice input device index
    on_set_response_language = pyqtSignal(str)  # "" = auto-detect, else ISO code
    on_set_stt_language      = pyqtSignal(str)  # "" = auto-detect, else ISO code
    on_set_custom_instructions = pyqtSignal(str)
    on_open_agents        = pyqtSignal()        # Active Agents panel
    on_agent_settings     = pyqtSignal()        # agent rails
    on_stop_agents        = pyqtSignal()        # cancel every running agent
    on_open_extensions    = pyqtSignal()        # industry packs on/off
    on_open_dashboard     = pyqtSignal()        # the enabled packs' dashboards
    on_open_app           = pyqtSignal()        # the one-window app (ui/app_window)
    on_open_app_page      = pyqtSignal(str)     # ...on a page: "settings", "agents"
    # The HeyTora plan or its use changed - emitted from any thread by the
    # account (main.py wires account().on_change to it); handled on Qt's.
    plan_changed          = pyqtSignal()
    # The updater's state changed (any thread; main.py wires it) and the
    # user's "Restart to update".
    updates_changed       = pyqtSignal()
    on_install_update     = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        # Icons MUST be created after QApplication exists
        quiet, lit = _make_tray_icon(False), _make_tray_icon(True)
        self._icons = {"idle": quiet, "listening": lit, "thinking": lit, "speaking": lit}
        self._state = "idle"
        # main.py sets this to the floating panel's isVisible, so the popup's
        # switch shows the truth. None: the row is left out.
        self.panel_visible: Optional[Callable[[], bool]] = None
        from ui.tray_popup import TrayPopup
        self._popup = TrayPopup()

        self._tray = QSystemTrayIcon()
        self._tray.setIcon(self._icons["idle"])
        self._tray.setToolTip(
            f"HeyTora - AI Companion\nPress {cfg.hotkey} and just talk"
        )
        self._search_enabled = cfg.switch("web_search")
        # How many agents are working, shown next to the Agents… item.
        self._agents_running = 0
        # Mirrors the persisted mic mode — off by default, so Tora only
        # listens while you're actually asking it something.
        self._wake_enabled = cfg.ambient_mic()
        self._response_language = ""
        try:
            from config import cfg as _cfg
            self._custom_instructions = _cfg.custom_instructions
        except Exception:
            self._custom_instructions = ""
        # As the user last left them - the menu must show what the manager
        # actually does, and both now start from the saved values (APP-03).
        self._slow_enabled = cfg.switch("slow_mode")
        self._auto_advance = cfg.switch("auto_advance")
        self._quiz_enabled = False
        self._privacy_enabled = cfg.switch("privacy_guard")
        self._code_enabled = cfg.switch("code_mode")
        self._journal_enabled = cfg.switch("journal")
        self._ocr_enabled = cfg.switch("ocr")
        self._is_recording = False
        # "billing" / "key" while Google is refusing the account (APP-02).
        self._account_problem = ""
        # "Free plan - 18 talk messages left": the first line of the menu.
        self._plan_line = ""
        self.plan_changed.connect(self._refresh_plan)
        # "ready:1.6.5" / "required:1.6.5" while an update waits to be installed.
        self._update_item = ""
        self._update_told: set[str] = set()
        self._update_asked = False            # the user pressed "Check for updates": say what it found
        self.updates_changed.connect(self._refresh_updates)
        # The input device Tora listens on; None = the system default.
        self._active_mic_index = cfg.mic_device()

        self._build_menu()
        self._tray.activated.connect(self._on_activated)
        self._tray.show()

    def _build_menu(self):
        """Describe the popup's rows (ui/tray_popup.py draws them).

        Only what the tray is for: the state, the plan, a warning if there
        is one, open/stop/quit and the two switches people reach for. The
        voice, models, tutor, journal, recording, workflow and setup items
        that used to fill three submenus live in the app window now -
        every one of them is on a page there (ui/app_pages.py)."""
        from ui.tray_popup import Entry
        rows: list[Entry] = []
        providers = cfg.describe()
        account_mode = cfg.access_mode() == "account"

        sub = (self._plan_line if self._plan_line and account_mode
               else f"Gemini · {providers['conversation']} · {providers['voice']}")
        rows.append(Entry("header", "HeyTora", sub=sub, state=self._state))
        rows.append(Entry("sep"))

        notice = None
        if self._update_item:
            kind, version = self._update_item.split(":", 1)
            notice = Entry("notice", (f"Update required - restart for HeyTora {version}"
                                      if kind == "required"
                                      else f"Restart to update to HeyTora {version}"),
                           self.on_install_update.emit)
        elif not cfg.has_key() and account_mode:
            notice = Entry("notice", "Sign in to HeyTora", self.on_run_setup.emit)
        elif not cfg.has_key():
            notice = Entry("notice", "Add your Google API key", self.on_api_keys.emit)
        elif self._account_problem == "plan":
            notice = Entry("notice", "This month's allowance is used up", self._open_website)
        elif self._account_problem == "signin":
            notice = Entry("notice", "Sign in to HeyTora again", self.on_run_setup.emit)
        elif self._account_problem == "billing":
            notice = Entry("notice", "Gemini credits have run out",
                           lambda: __import__("webbrowser").open("https://ai.studio/projects"))
        elif self._account_problem == "key":
            notice = Entry("notice", "Google refused the API key", self.on_api_keys.emit)
        if notice is not None:
            rows.append(notice)

        rows.append(Entry("item", "Open HeyTora", self.on_open_app.emit))
        if self.panel_visible is not None:
            try:
                shown = bool(self.panel_visible())
            except Exception:  # noqa: BLE001
                shown = False
            rows.append(Entry("switch", "Floating panel",
                              lambda on: (self.on_show_panel if on else self.on_hide_panel).emit(),
                              on=shown))
        rows.append(Entry("item", "Stop talking", self.on_stop.emit, trailing="Esc"))
        rows.append(Entry("sep"))

        # The count is the point: "2 running" is how you notice work is
        # happening while the app is closed.
        rows.append(Entry("item", "Agents", lambda: self.on_open_app_page.emit("agents"),
                          trailing=f"{self._agents_running} running" if self._agents_running else ""))
        if self._agents_running:
            rows.append(Entry("item", "Stop all agents", self.on_stop_agents.emit))
        rows.append(Entry("switch", "Web search", self._toggle_search, on=self._search_enabled))
        rows.append(Entry("switch", "Always listening", self._toggle_wake, on=self._wake_enabled))
        rows.append(Entry("sep"))

        rows.append(Entry("item", "Settings", lambda: self.on_open_app_page.emit("settings")))
        rows.append(Entry("item", "Check for updates", self.check_for_updates))
        rows.append(Entry("item", "Quit HeyTora", self.on_quit.emit))

        self._popup.set_entries(rows)

    def menu_texts(self) -> list[str]:
        """The popup's rows, as words."""
        return self._popup.texts()

    def show_popup(self) -> None:
        self._build_menu()                  # the panel switch and state, as of now
        self._popup.popup_at()

    def retheme(self) -> None:
        """Light/dark changed: the popup follows, and the icon follows the taskbar."""
        quiet, lit = _make_tray_icon(False), _make_tray_icon(True)
        self._icons = {"idle": quiet, "listening": lit, "thinking": lit, "speaking": lit}
        self._tray.setIcon(self._icons.get(self._state, quiet))
        self._popup.retheme()

    def hide_icon(self):
        """Remove the tray icon from the notification area. MUST run before
        app.quit(): killing the process without Shell_NotifyIcon(NIM_DELETE)
        leaves a ghost icon in the tray until hovered."""
        try:
            self._tray.hide()
        except Exception:
            pass

    def pick_voice(self, name: str) -> None:
        """Speak in this voice. The menu is rebuilt after the change, so its
        "Voice: …" title and dot show the new one - they used to go on
        showing the old voice until something else rebuilt the menu."""
        self.on_set_voice.emit(name)
        self._build_menu()

    def pick_route(self, mode: str) -> None:
        """Talk over this route ("realtime" / "cascade"), then show it."""
        self.on_set_conversation_mode.emit(mode)
        self._build_menu()

    def pick_mic(self, index: int) -> None:
        """Listen on this input device. The menu ticks it from now on - it
        used to go on ticking the system default whatever was picked."""
        self._active_mic_index = int(index)
        self._build_menu()
        self.on_set_mic_device.emit(int(index))

    def _on_activated(self, reason):
        """Left-click opens HeyTora (it used to do nothing - only a
        double-click did anything, and that opened the floating panel);
        right-click opens the popup."""
        R = QSystemTrayIcon.ActivationReason
        if reason == R.Trigger:
            self.on_open_app.emit()
        elif reason == R.Context:
            self.show_popup()

    def _toggle_search(self, checked: bool):
        self._search_enabled = checked
        self.on_toggle_search.emit(checked)

    def _toggle_wake(self, checked: bool):
        self._wake_enabled = checked
        self.on_toggle_wake_word.emit(checked)

    def _toggle_auto_advance(self, checked: bool):
        self._auto_advance = checked
        self.on_toggle_auto_advance.emit(checked)

    def _toggle_slow(self, checked: bool):
        self._slow_enabled = checked
        self.on_toggle_slow_mode.emit(checked)

    def _toggle_quiz(self, checked: bool):
        self._quiz_enabled = checked
        self.on_toggle_quiz_mode.emit(checked)

    def _toggle_privacy(self, checked: bool):
        self._privacy_enabled = checked
        self.on_toggle_privacy.emit(checked)

    def _toggle_code(self, checked: bool):
        self._code_enabled = checked
        self.on_toggle_code_mode.emit(checked)

    def _toggle_ocr(self, checked: bool):
        self._ocr_enabled = checked
        self.on_toggle_ocr.emit(checked)

    def _toggle_journal(self, checked: bool):
        self._journal_enabled = checked
        self.on_toggle_journal.emit(checked)

    def set_recording_state(self, on: bool):
        self._is_recording = on
        self.rebuild_menu()

    def set_state_icon(self, state: str):
        self._state = state if state in self._icons else "idle"
        self._tray.setIcon(self._icons[self._state])

    def set_agents_running(self, count: int) -> None:
        """Update the running-agent count shown in the menu.

        Rebuilds only when the number actually changes: agent events fire
        several times a second, and rebuilding the whole menu under an
        open cursor would make it flicker.
        """
        count = max(0, int(count))
        if count == self._agents_running:
            return
        self._agents_running = count
        self._build_menu()

    def rebuild_menu(self):
        """Rebuild so the Model submenu reflects the newly-active provider."""
        self._build_menu()

    # switch name -> (the handler a click calls, the menu action it ticks)
    _SWITCH_HANDLERS = {
        "web_search":    ("_toggle_search", "_search_action"),
        "wake_word":     ("_toggle_wake", "_wake_action"),
        "auto_advance":  ("_toggle_auto_advance", "_adv_action"),
        "slow_mode":     ("_toggle_slow", "_slow_action"),
        "quiz_mode":     ("_toggle_quiz", "_quiz_action"),
        "privacy_guard": ("_toggle_privacy", "_privacy_action"),
        "code_mode":     ("_toggle_code", "_code_action"),
        "ocr":           ("_toggle_ocr", "_ocr_action"),
        "journal":       ("_toggle_journal", "_journal_action"),
    }

    def apply_switch(self, name: str, on: bool) -> None:
        """Change a switch exactly as clicking it in this menu would.

        The app window's switches come through here (ui/app_bridge.py), so
        the menu's tick, its label and the manager behind it all follow -
        the tray and the app cannot disagree about what is on."""
        handler, _action = self._SWITCH_HANDLERS[name]
        getattr(self, handler)(bool(on))
        if self._popup.isVisible():
            self._build_menu()

    def set_account_problem(self, kind: str, words: str) -> None:
        """Google is refusing the account ("billing" / "key"), or ("", "")
        has stopped. A warning at the top of the menu while it lasts, and
        one notification when it starts - Tora cannot SAY it, her voice is
        on the same account (APP-02)."""
        kind = kind if kind in ("billing", "key", "plan", "signin") else ""
        if kind and words:
            heading = {"plan": "HeyTora plan", "signin": "Sign in to HeyTora"}.get(
                kind, "Tora can't reach Google")
            self._tray.showMessage(heading, words, QSystemTrayIcon.MessageIcon.Warning, 8000)
        if kind != self._account_problem:
            self._account_problem = kind
            self._build_menu()

    def check_for_updates(self) -> None:
        """The menu's "Check for updates": check now, and say what was found."""
        try:
            from updates import updater
            started = updater().check_soon(force=True)
        except Exception:  # noqa: BLE001
            return
        self._update_asked = True
        if not started:
            self._tray.showMessage("HeyTora", "Already checking for updates…",
                                   QSystemTrayIcon.MessageIcon.Information, 3000)

    def _update_found_words(self, st: dict) -> str:
        if st["state"] == "ready":
            return f"HeyTora {st['latest']} is ready - tray → Restart to update."
        if st["state"] == "current":
            return f"You're up to date - HeyTora {st['current']}."
        if st["state"] == "dev":
            if st["error"]:
                return st["error"]
            return (f"Running from source ({st['current']}). Latest published: {st['latest']}."
                    if st["latest"] else f"Running from source ({st['current']}).")
        return st["error"] or "Couldn't check for updates - try again in a moment."

    def _refresh_updates(self) -> None:
        """An update is ready (or no longer is): the menu's first item, and
        one notification per version."""
        try:
            from updates import updater
            st = updater().state()
        except Exception:  # noqa: BLE001 - the tray must never break on updates
            return
        item = ""
        if st["state"] == "ready":
            item = f"{'required' if st['required'] else 'ready'}:{st['latest']}"
        if item != self._update_item:
            self._update_item = item
            self._build_menu()
        if self._update_asked and not st.get("busy") and st["state"] not in ("checking", "downloading"):
            # What the user's own check found - every answer, not only "ready".
            self._update_asked = False
            if st["state"] == "ready":
                self._update_told.add(st["latest"])
            self._tray.showMessage("HeyTora updates", self._update_found_words(st),
                                   QSystemTrayIcon.MessageIcon.Information, 6000)
            return
        if item and st["latest"] not in self._update_told:
            self._update_told.add(st["latest"])
            self._tray.showMessage(
                "HeyTora update ready",
                f"HeyTora {st['latest']} is ready. " + (
                    "It installs the next time HeyTora starts - or now: tray → Restart to update."
                    if cfg.switch("auto_update") else "Tray → Restart to update when it suits you."),
                QSystemTrayIcon.MessageIcon.Information, 8000)

    def _refresh_plan(self) -> None:
        """The plan changed: the menu's first line follows it, and anything
        worth saying - 80% used, all used, today's fair use - is said once."""
        try:
            from account import settings
            from account.service import account, short_status
            if not settings.configured():
                return
            acc = account()
            line = short_status(acc.plan_state()["summary"] or {}) if acc.signed_in() else ""
            alerts = acc.take_alerts() if acc.signed_in() else []
        except Exception:  # noqa: BLE001 - the tray must never break on the plan
            return
        if line != self._plan_line:
            self._plan_line = line
            self._build_menu()
        for words in alerts:
            self._tray.showMessage("HeyTora plan", words, QSystemTrayIcon.MessageIcon.Information, 8000)

    def _open_website(self) -> None:
        """Where the plan is changed (Settings > Account says the same)."""
        from account import settings
        if settings.website_url():
            __import__("webbrowser").open(settings.website_url())

    def show_notification(self, title: str, message: str):
        self._tray.showMessage(
            title, message, QSystemTrayIcon.MessageIcon.Information, 3000
        )

    @property
    def search_enabled(self) -> bool:
        return self._search_enabled
