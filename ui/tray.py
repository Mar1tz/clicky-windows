from PyQt6.QtWidgets import (
    QSystemTrayIcon, QMenu, QDialog, QVBoxLayout, QTextEdit,
    QPushButton, QLabel, QHBoxLayout,
)
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QBrush
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QObject

from config import cfg


def _make_tray_icon(color: QColor) -> QIcon:
    """Generate a simple coloured circle as the tray icon.
    Must be called AFTER QApplication exists (Qt requirement)."""
    px = QPixmap(QSize(22, 22))
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QBrush(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(3, 3, 16, 16)
    painter.end()
    return QIcon(px)


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

    def __init__(self, parent=None):
        super().__init__(parent)

        # Icons MUST be created after QApplication exists
        self._icons = {
            "idle":      _make_tray_icon(QColor(80, 80, 120)),
            "listening": _make_tray_icon(QColor(50, 200, 100)),
            "thinking":  _make_tray_icon(QColor(0, 120, 255)),
            "speaking":  _make_tray_icon(QColor(255, 140, 0)),
        }

        self._tray = QSystemTrayIcon()
        self._tray.setIcon(self._icons["idle"])
        self._tray.setToolTip(
            f"Tora - AI Companion\nPress {cfg.hotkey} and just talk"
        )
        self._search_enabled = True
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
        self._slow_enabled = False
        self._auto_advance = True
        self._quiz_enabled = False
        self._privacy_enabled = True
        self._code_enabled = True
        self._multilang_enabled = True
        self._journal_enabled = True
        self._ocr_enabled = True
        self._is_recording = False

        self._build_menu()
        self._tray.activated.connect(self._on_activated)
        self._tray.show()

    def _build_menu(self):
        menu = QMenu()
        menu.setStyleSheet(
            "QMenu { background: rgb(22,22,28); border: 1px solid rgb(55,55,70);"
            "border-radius: 8px; color: rgb(220,220,230); font-size: 13px; }"
            "QMenu::item:selected { background: rgb(0,90,200); border-radius: 4px; }"
            "QMenu::separator { height: 1px; background: rgb(55,55,70); margin: 4px 8px; }"
        )

        providers = cfg.describe()
        if not cfg.has_key():
            warn = menu.addAction("⚠  No Google API key — click API Key…")
            warn.triggered.connect(self.on_api_keys)
            menu.addSeparator()
        info = menu.addAction(
            f"Gemini · {providers['conversation']}  |  voice: {providers['voice']}"
        )
        info.setEnabled(False)
        menu.addSeparator()

        # The single-window app. First in the list because it is meant to
        # become the way in; the individual windows below still work.
        app_action = menu.addAction("Open Tora app")
        app_action.triggered.connect(self.on_open_app)
        menu.addSeparator()

        show_action = menu.addAction("Show Panel")
        show_action.triggered.connect(self.on_show_panel)

        hide_action = menu.addAction("Hide Panel")
        hide_action.triggered.connect(self.on_hide_panel)

        stop_action = menu.addAction("Stop (Esc)")
        stop_action.triggered.connect(self.on_stop)

        menu.addSeparator()

        # ── Autonomous agents ───────────────────────────────────────────
        # The count is the point: "Agents… (2 running)" is how you notice
        # work is happening while the panel is closed.
        agents_label = "Agents…"
        if self._agents_running:
            agents_label = f"Agents… ({self._agents_running} running)"
        agents_action = menu.addAction(agents_label)
        agents_action.triggered.connect(self.on_open_agents)

        agent_settings = menu.addAction("Agent settings…")
        agent_settings.triggered.connect(self.on_agent_settings)

        stop_agents = menu.addAction("Stop all agents")
        stop_agents.setEnabled(self._agents_running > 0)
        stop_agents.triggered.connect(self.on_stop_agents)

        dashboard = menu.addAction("Dashboard…")
        dashboard.triggered.connect(self.on_open_dashboard)
        extensions = menu.addAction("Extensions…")
        extensions.triggered.connect(self.on_open_extensions)

        menu.addSeparator()

        # ── How Tora talks ──────────────────────────────────────────────
        route_menu = menu.addMenu(f"Conversation: {providers['conversation']}")
        for mode, blurb in (
            ("realtime", "Realtime — one websocket, answers in ~1.5 s"),
            ("cascade",  "Cascade — transcribe, think, speak. Slower."),
        ):
            act = route_menu.addAction(
                f"● {blurb}" if providers["conversation"] == mode else f"   {blurb}"
            )
            act.triggered.connect(
                lambda _=False, m=mode: self.on_set_conversation_mode.emit(m)
            )
        route_menu.addSeparator()
        note = route_menu.addAction(
            f"realtime: {providers['realtime_model']}"
        )
        note.setEnabled(False)

        # ── Voice ─────────────────────────────────────────────────────────
        voice_menu = menu.addMenu(f"Voice: {providers['voice']}")
        from audio.tts.gemini_tts import VOICES
        for name in VOICES:
            act = voice_menu.addAction(
                f"● {name}" if name == providers["voice"] else f"   {name}"
            )
            act.triggered.connect(
                lambda _=False, n=name: self.on_set_voice.emit(n)
            )

        # ── Which model reads the screen ──────────────────────────────────
        models_menu = menu.addMenu("Models")
        for role, label in (
            ("vision_fast", "Glance"),
            ("vision_deep", "Careful look"),
            ("planner_model", "Guide planner"),
            ("embed_model", "Memory"),
        ):
            act = models_menu.addAction(f"{label}: {providers.get(role, '?')}")
            act.setEnabled(False)
        models_menu.addSeparator()
        refresh_act = models_menu.addAction("Refresh model list")
        refresh_act.triggered.connect(self.on_refresh_models)

        menu.addSeparator()

        search_action = menu.addAction(
            "Web Search: ON" if self._search_enabled else "Web Search: OFF"
        )
        search_action.setCheckable(True)
        search_action.setChecked(self._search_enabled)
        search_action.triggered.connect(self._toggle_search)
        self._search_action = search_action

        wake_action = menu.addAction(
            "Always listening (wake word): ON" if self._wake_enabled
            else "Always listening (wake word): OFF — press the hotkey to talk"
        )
        wake_action.setCheckable(True)
        wake_action.setChecked(self._wake_enabled)
        wake_action.triggered.connect(self._toggle_wake)
        self._wake_action = wake_action

        scope_label = "Instructions for Tora…"
        scope_action = menu.addAction(scope_label)
        scope_action.triggered.connect(self._prompt_custom_instructions)

        # ── Tutor toggles ──
        menu.addSeparator()
        tutor_menu = menu.addMenu("Tutor Mode")

        slow_action = tutor_menu.addAction(
            "Slow Mode (teacher pace): ON" if self._slow_enabled
            else "Slow Mode (teacher pace): OFF"
        )
        slow_action.setCheckable(True)
        slow_action.setChecked(self._slow_enabled)
        slow_action.triggered.connect(self._toggle_slow)
        self._slow_action = slow_action

        adv_action = tutor_menu.addAction(
            "Guide auto-advance (detect each step): ON" if self._auto_advance
            else "Guide auto-advance (detect each step): OFF — say 'next'"
        )
        adv_action.setCheckable(True)
        adv_action.setChecked(self._auto_advance)
        adv_action.triggered.connect(self._toggle_auto_advance)
        self._adv_action = adv_action

        quiz_action = tutor_menu.addAction(
            "Quiz Mode: ON" if self._quiz_enabled else "Quiz Mode: OFF"
        )
        quiz_action.setCheckable(True)
        quiz_action.setChecked(self._quiz_enabled)
        quiz_action.triggered.connect(self._toggle_quiz)
        self._quiz_action = quiz_action

        privacy_action = tutor_menu.addAction(
            "Privacy Guard: ON" if self._privacy_enabled
            else "Privacy Guard: OFF"
        )
        privacy_action.setCheckable(True)
        privacy_action.setChecked(self._privacy_enabled)
        privacy_action.triggered.connect(self._toggle_privacy)
        self._privacy_action = privacy_action

        code_action = tutor_menu.addAction(
            "Code Mode (auto): ON" if self._code_enabled else "Code Mode (auto): OFF"
        )
        code_action.setCheckable(True)
        code_action.setChecked(self._code_enabled)
        code_action.triggered.connect(self._toggle_code)
        self._code_action = code_action

        ml_action = tutor_menu.addAction(
            "Multilingual: ON" if self._multilang_enabled else "Multilingual: OFF"
        )
        ml_action.setCheckable(True)
        ml_action.setChecked(self._multilang_enabled)
        ml_action.triggered.connect(self._toggle_multilang)
        self._ml_action = ml_action

        ocr_action = tutor_menu.addAction(
            "OCR Fallback: ON" if self._ocr_enabled else "OCR Fallback: OFF"
        )
        ocr_action.setCheckable(True)
        ocr_action.setChecked(self._ocr_enabled)
        ocr_action.triggered.connect(self._toggle_ocr)
        self._ocr_action = ocr_action

        # ── Journal ──
        menu.addSeparator()
        journal_menu = menu.addMenu("Journal")

        journal_action = journal_menu.addAction(
            "Logging: ON" if self._journal_enabled else "Logging: OFF"
        )
        journal_action.setCheckable(True)
        journal_action.setChecked(self._journal_enabled)
        journal_action.triggered.connect(self._toggle_journal)
        self._journal_action = journal_action

        open_journal = journal_menu.addAction("Open journal folder")
        open_journal.triggered.connect(self.on_journal_open)

        attach = journal_menu.addAction("Attach document (PDF / TXT / DOCX)…")
        attach.triggered.connect(self.on_attach_doc)

        # ── Recording ──
        rec_menu = menu.addMenu("Lesson Recording")
        if self._is_recording:
            stop_rec = rec_menu.addAction("● Stop recording")
            stop_rec.triggered.connect(self.on_record_stop)
        else:
            start_rec = rec_menu.addAction("Start recording")
            start_rec.triggered.connect(self.on_record_start)

        # ── Workflow capture ──
        wf_menu = menu.addMenu("Workflow Capture")
        wf_start = wf_menu.addAction("Start capturing my clicks")
        wf_start.triggered.connect(self.on_workflow_start)
        wf_stop  = wf_menu.addAction("Stop + send to Tora")
        wf_stop.triggered.connect(self.on_workflow_stop)

        # ── Live collaboration ──
        # NOTE: WebRTC signalling server isn't shipped yet, so this whole
        # menu is hidden until tutor_features/collab.py gets a real backend.
        # The signals are still defined on this object so any existing
        # bindings in main.py don't crash on connect().
        # collab_menu = menu.addMenu("Live Session")  # disabled in this build
        # host = collab_menu.addAction("Start hosting")
        # host.triggered.connect(self.on_collab_start)
        # join = collab_menu.addAction("Join with code…")
        # join.triggered.connect(self.on_collab_join)

        menu.addSeparator()

        # ── Setup / Diagnostics ──
        setup_menu = menu.addMenu("Setup && Diagnostics")
        self._build_mic_submenu(setup_menu)
        keys_act = setup_menu.addAction("API Keys…")
        keys_act.triggered.connect(self.on_api_keys)
        run_setup = setup_menu.addAction("Run setup wizard again…")
        run_setup.triggered.connect(self.on_run_setup)
        diag = setup_menu.addAction("Save diagnostics report…")
        diag.triggered.connect(self.on_diagnostics)

        menu.addSeparator()

        quit_action = menu.addAction("Quit Tora")
        quit_action.triggered.connect(self.on_quit)

        self._tray.setContextMenu(menu)
        # Keep refs to prevent GC
        self._menu = menu

    def hide_icon(self):
        """Remove the tray icon from the notification area. MUST run before
        app.quit(): killing the process without Shell_NotifyIcon(NIM_DELETE)
        leaves a ghost icon in the tray until hovered (GitHub issue #2)."""
        try:
            self._tray.hide()
        except Exception:
            pass

    def _prompt_custom_instructions(self):
        dlg = QDialog(None)
        dlg.setWindowTitle("Instructions for Tora")
        dlg.setMinimumSize(480, 380)
        dlg.setStyleSheet("""
            QDialog { background-color: #1e1e24; }
            QLabel { color: #e8e8ec; font-size: 13px; }
            QTextEdit {
                background-color: #26262e; color: #f0f0f4;
                border: 1px solid #3a3a44; border-radius: 8px;
                padding: 10px; font-size: 13px;
            }
            QTextEdit:focus { border: 1px solid #7c5cff; }
            QPushButton {
                background-color: #3a3a44; color: #f0f0f4;
                border: none; border-radius: 6px; padding: 8px 18px;
                font-size: 13px;
            }
            QPushButton:hover { background-color: #47475400; }
            QPushButton#primary { background-color: #7c5cff; }
            QPushButton#primary:hover { background-color: #8f72ff; }
        """)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(10)

        title = QLabel("Instructions for Tora")
        title.setStyleSheet("font-size: 15px; font-weight: 600; color: #ffffff;")
        layout.addWidget(title)

        subtitle = QLabel(
            "Replaces Tora's core behavior (name, rules, tone).\n"
            "{{CONTEXT}} and {{TODAY}} are placeholders — best left in."
        )
        subtitle.setStyleSheet("color: #9a9aa4; font-size: 12px;")
        layout.addWidget(subtitle)

        editor = QTextEdit()
        editor.setPlainText(self._custom_instructions)
        editor.setPlaceholderText("e.g. \"Your name is Max, a laid-back coding buddy…\"")
        layout.addWidget(editor, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dlg.reject)
        ok_btn = QPushButton("Save")
        ok_btn.setObjectName("primary")
        ok_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._custom_instructions = editor.toPlainText().strip()
            self.on_set_custom_instructions.emit(self._custom_instructions)



    def _build_mic_submenu(self, parent_menu: QMenu):
        """Lists available input devices so the user can pick a mic without
        digging through Windows sound settings."""
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            default_idx = sd.default.device[0]
        except Exception:
            return  # sounddevice not ready yet — skip silently, not fatal

        mic_menu = parent_menu.addMenu("Microphone")
        for idx, dev in enumerate(devices):
            if dev.get("max_input_channels", 0) <= 0:
                continue
            label = dev["name"]
            if idx == default_idx:
                label += "  (system default)"
            act = mic_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(idx == getattr(self, "_active_mic_index", default_idx))
            act.triggered.connect(lambda checked, i=idx: self.on_set_mic_device.emit(i))

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.on_show_panel.emit()

    def _toggle_search(self, checked: bool):
        self._search_enabled = checked
        self._search_action.setText(
            "Web Search: ON" if checked else "Web Search: OFF"
        )
        self.on_toggle_search.emit(checked)

    def _toggle_wake(self, checked: bool):
        self._wake_enabled = checked
        self._wake_action.setText(
            "Wake word 'Tora': ON" if checked else "Wake word 'Tora': OFF"
        )
        self.on_toggle_wake_word.emit(checked)

    def _toggle_auto_advance(self, checked: bool):
        self._auto_advance = checked
        self._adv_action.setText(
            "Guide auto-advance (detect each step): ON" if checked
            else "Guide auto-advance (detect each step): OFF — say 'next'"
        )
        self.on_toggle_auto_advance.emit(checked)

    def _toggle_slow(self, checked: bool):
        self._slow_enabled = checked
        self._slow_action.setText(
            "Slow Mode (teacher pace): ON" if checked
            else "Slow Mode (teacher pace): OFF"
        )
        self.on_toggle_slow_mode.emit(checked)

    def _toggle_quiz(self, checked: bool):
        self._quiz_enabled = checked
        self._quiz_action.setText(
            "Quiz Mode: ON" if checked else "Quiz Mode: OFF"
        )
        self.on_toggle_quiz_mode.emit(checked)

    def _toggle_privacy(self, checked: bool):
        self._privacy_enabled = checked
        self._privacy_action.setText(
            "Privacy Guard: ON" if checked else "Privacy Guard: OFF"
        )
        self.on_toggle_privacy.emit(checked)

    def _toggle_code(self, checked: bool):
        self._code_enabled = checked
        self._code_action.setText(
            "Code Mode (auto): ON" if checked else "Code Mode (auto): OFF"
        )
        self.on_toggle_code_mode.emit(checked)

    def _toggle_multilang(self, checked: bool):
        self._multilang_enabled = checked
        self._ml_action.setText(
            "Multilingual: ON" if checked else "Multilingual: OFF"
        )
        self.on_toggle_multilang.emit(checked)

    def _toggle_ocr(self, checked: bool):
        self._ocr_enabled = checked
        self._ocr_action.setText(
            "OCR Fallback: ON" if checked else "OCR Fallback: OFF"
        )
        self.on_toggle_ocr.emit(checked)

    def _toggle_journal(self, checked: bool):
        self._journal_enabled = checked
        self._journal_action.setText(
            "Logging: ON" if checked else "Logging: OFF"
        )
        self.on_toggle_journal.emit(checked)

    def set_recording_state(self, on: bool):
        self._is_recording = on
        self.rebuild_menu()

    def set_state_icon(self, state: str):
        self._tray.setIcon(self._icons.get(state, self._icons["idle"]))

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

    def show_notification(self, title: str, message: str):
        self._tray.showMessage(
            title, message, QSystemTrayIcon.MessageIcon.Information, 3000
        )

    @property
    def search_enabled(self) -> bool:
        return self._search_enabled
