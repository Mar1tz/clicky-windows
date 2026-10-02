"""
HeyTora for Windows — Entry Point.
Boots Qt, spawns overlay+panel+tray, starts ambient mic listener, binds hotkey.
"""

import os
import sys
from pathlib import Path


def _ensure_std_streams() -> None:
    """Give the process real stdout/stderr before anything tries to write.

    PyInstaller windowed builds (console=False) set both to None. Most code
    survives that — bare print() tolerates it — but anything calling
    .write() directly does not. tqdm is the one that matters: huggingface_hub
    used it for a download bar while a local model was fetched on the first
    transcription, so the very first activation died with
    "'NoneType' object has no attribute 'write'". The model then never
    cached, so it failed again on every subsequent attempt, and the toast
    showed only the bare message with no traceback.

    Runs at import time, ahead of the heavy imports below, so library code
    that writes while loading is covered too.
    """
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is not None:
            continue
        try:
            stream = open(os.devnull, "w", encoding="utf-8")
        except Exception:
            continue
        setattr(sys, name, stream)
        # Some libraries reach for the __-prefixed originals instead.
        if getattr(sys, f"__{name}__", None) is None:
            setattr(sys, f"__{name}__", stream)


_ensure_std_streams()

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QTimer

from config import cfg
from ui.tray import TrayManager
from ui.panel import CompanionPanel, AppState
from ui.overlay import (
    CursorOverlay, MODE_IDLE, MODE_LISTENING, MODE_THINKING, MODE_SPEAKING
)
from hotkey import GlobalHotkeyMonitor, StopHotkey
from companion_manager import CompanionManager
from ui import sign_in_gate


STATE_TO_CURSOR_MODE = {
    AppState.IDLE:      MODE_IDLE,
    AppState.LISTENING: MODE_LISTENING,
    AppState.THINKING:  MODE_THINKING,
    AppState.SPEAKING:  MODE_SPEAKING,
}


def _setup_logging():
    """Rotating runtime log at %LOCALAPPDATA%\\HeyTora\\tora.log — the #1
    ask from bug reports: with no log file, 'stuck on Listening' class issues
    were undiagnosable."""
    import logging
    from logging.handlers import RotatingFileHandler

    from config import data_dir
    log_dir = data_dir()
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_dir / "tora.log", maxBytes=1_000_000, backupCount=2,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
        ))
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        root.addHandler(handler)
        # Secrets stay out of this file: see logsafe.py. Scrubbing the
        # existing logs first closes the leak that already happened.
        import logsafe
        scrubbed = logsafe.scrub_logs(log_dir)
        logsafe.install(root)
        logging.getLogger("tora").info(
            "=== HeyTora starting (python %s) ===", sys.version.split()[0]
        )
        # What moving the old Tora data folder did, if anything (config).
        import config
        for note in config.MIGRATION_NOTES:
            logging.getLogger("tora").info("data folder: %s", note)
        if scrubbed:
            logging.getLogger("tora").info(
                "redacted %d line(s) of earlier logs that contained credentials", scrubbed)
    except Exception:
        pass  # logging must never block startup


def _wire_agents(manager, tray):
    """Connect the agent workforce to the tray, once it is running.

    Everything here is optional by design: if `agents/` failed to import
    or start, `manager.agents` is None and Tora simply has no workforce —
    the tray items say so rather than throwing.

    The approval controller matters most. Without something listening for
    APPROVAL_REQUIRED, a task that needs a human parks in WAITING_APPROVAL
    forever, looking like a hang.
    """
    workforce = getattr(manager, "agents", None)
    if workforce is None:
        def _unavailable():
            tray.show_notification(
                "Agents unavailable",
                "The agent workforce did not start. See tora.log for why.")
        tray.on_open_agents.connect(_unavailable)
        tray.on_agent_settings.connect(_unavailable)
        tray.on_open_extensions.connect(_unavailable)
        tray.on_open_dashboard.connect(_unavailable)
        return

    from agents.ui import attach_approvals, open_agents_window, show_orbs
    from agents.ui.settings import AgentSettingsDialog

    attach_approvals(workforce)
    # The top-right orbs: one per working agent, with its progress ring.
    # Hidden until the first agent starts; a click opens the panel.
    windows_orbs = show_orbs(workforce)

    windows: dict[str, object] = {}

    def _open_panel():
        windows["panel"] = open_agents_window(workforce)

    def _open_settings():
        dialog = windows.get("settings")
        if dialog is None or not dialog.isVisible():
            dialog = AgentSettingsDialog(workforce)
            windows["settings"] = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    tray.on_open_agents.connect(_open_panel)
    tray.on_agent_settings.connect(_open_settings)

    # Industry extensions: the tray opens the switchboard and the dashboard;
    # the voice side asks for them through sig_open_extension ("turn on the
    # accounting tools", "create an accounts payable agent").
    from agents.ui import open_dashboard, open_extensions

    def _open_extension(ext_id: str, what: str):
        try:
            if what.startswith("wizard"):
                ext = workforce.extensions.get(ext_id) if workforce.extensions else None
                if ext is not None and ext_id == "accounting":
                    from extensions.accounting.ui.wizard import AgentSetupWizard
                    dlg = AgentSetupWizard(workforce, ext, template=what.partition(":")[2])
                    windows["wizard"] = dlg
                    dlg.show()
                    dlg.raise_()
                    return
            if what in ("", "dashboard"):
                windows["dashboard"] = open_dashboard(workforce, f"{ext_id}.dashboard")
        except Exception:
            import logging as _logging
            _logging.getLogger("tora.main").exception("could not open extension %s", ext_id)

    tray.on_open_extensions.connect(lambda: windows.__setitem__("extensions", open_extensions(workforce)))
    tray.on_open_dashboard.connect(lambda: windows.__setitem__("dashboard", open_dashboard(workforce)))
    manager.sig_open_extension.connect(_open_extension)

    def _stop_all():
        n = workforce.cancel_all()
        tray.show_notification("Agents stopped",
                               f"Cancelled {n} running task(s)." if n else "No agents were running.")

    tray.on_stop_agents.connect(_stop_all)

    def _refresh_count(_event=None):
        try:
            tray.set_agents_running(len(workforce.list_agents(active_only=True)))
        except Exception:
            pass

    manager.sig_agent_event.connect(_refresh_count)
    manager.sig_agent_notice.connect(tray.show_notification)
    _refresh_count()

    # Esc is the kill switch for computer control. The same "Stop" that
    # silences Tora must also stop an agent that has the user's mouse:
    # trip the switch (every further move/click/type fails) and cancel
    # the tasks behind it. The switch resets the next time a task starts.
    try:
        from agents.core import EventKind, TaskKind
        from agents.tools.computer_tools import kill_switch

        def _stop_agents():
            kill_switch.trip()
            workforce.cancel_all(kinds=[TaskKind.COMPUTER])

        tray.on_stop.connect(_stop_agents)
        workforce.events.subscribe(lambda ev: kill_switch.reset(),
                                   kinds=[EventKind.TASK_CREATED])
    except Exception:
        import logging
        logging.getLogger("tora").debug("kill switch wiring failed", exc_info=True)


def main():
    _setup_logging()
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("HeyTora")
    app.setApplicationDisplayName("HeyTora - AI Companion")
    # A "start with Windows" set before the rename lives under the old name.
    from ui import autostart
    autostart.migrate_legacy()

    # ── Updates (updates/service.py) ──────────────────────────────────────────
    # An update downloaded last time installs before anything is shown: the
    # installer runs over this app, silently, and opens HeyTora again on the
    # new version. Run from source, there is nothing to update.
    from updates import updater as _updater
    upd = _updater()
    update_outcome = upd.at_startup(auto=cfg.switch("auto_update"))
    if update_outcome == "installing":
        return

    # ── No HeyTora account, no HeyTora ────────────────────────────────────────
    # Before the overlay, the hotkey, the microphone or the agents exist: no
    # sign-in, only the sign-in window - and closing it closes the app.
    door = sign_in_gate.gate()
    if door == "closed":
        return
    sign_in_gate.relay()                     # on this thread, before anything can lose the sign-in

    # ── Core components ───────────────────────────────────────────────────────
    manager = CompanionManager()
    panel   = CompanionPanel()
    overlay = CursorOverlay()
    tray    = TrayManager()

    def _shutdown():
        """Close HeyTora: the tray, the companion, the app."""
        tray.hide_icon()
        manager.shutdown()
        app.quit()

    def _quit():
        """Quit from the tray. An update that is ready installs on the way
        out (no reopening) when updates install by themselves."""
        if cfg.switch("auto_update") and upd.ready():
            upd.install_now(relaunch=False)
        _shutdown()

    # ── Wire signals ──────────────────────────────────────────────────────────

    # State changes → Panel + Tray + Cursor
    def _on_state(state: AppState):
        panel.set_state(state)
        tray.set_state_icon(state.name.lower())
        overlay.set_mode(STATE_TO_CURSOR_MODE.get(state, MODE_IDLE))
        # Already on the main thread here; hand the caption the NAME so
        # overlay.py never has to import AppState.
        overlay.caption_state(state.name)

    manager.sig_state_changed.connect(_on_state)

    # Response streaming -> panel scrollback AND the top caption island.
    # Qt delivers to both slots in connection order; the panel keeps the
    # full text, the caption is the glanceable live line.
    manager.sig_response_chunk.connect(panel.append_response_chunk)
    manager.sig_response_chunk.connect(overlay.caption_chunk)
    manager.sig_response_done.connect(overlay.caption_done)
    # The words arrive seconds ahead of the voice; the caption paces them by
    # how far the voice has actually got.
    overlay.set_voice_clock(manager.voice_clock)

    # Audio level → cursor waveform (+ panel meter)
    manager.sig_audio_level.connect(panel.set_audio_level)
    manager.sig_audio_level.connect(overlay.set_audio_level)

    # Pointing directives
    manager.sig_point_at.connect(overlay.point_at)
    manager.sig_point_hold.connect(overlay.set_point_hold)
    manager.sig_point_release.connect(overlay.release_point)
    manager.sig_guide_point.connect(overlay.guide_point)
    manager.sig_guide_release.connect(overlay.guide_release)

    # Whiteboard annotations (legacy per-shape signals)
    manager.sig_arrow.connect(overlay.add_arrow)
    manager.sig_circle.connect(overlay.add_circle)
    manager.sig_underline.connect(overlay.add_underline)
    manager.sig_label.connect(overlay.add_text)
    # Teaching drawings — generic shape channel with progressive animation
    manager.sig_draw.connect(overlay.add_shape)
    manager.sig_clear_drawings.connect(overlay.clear_annotations)

    # Errors. The toast names the log file because it only has room for the
    # message itself — issue #15 was reported with no traceback simply because
    # nobody knew a log existed.
    from config import data_dir as _data_dir
    _log_hint = _data_dir() / "tora.log"
    manager.sig_error.connect(
        lambda e: tray.show_notification(
            "HeyTora error", f"{e}\n\nFull details: {_log_hint}"
        )
    )
    # Out of credit / a refused key (APP-02): a warning in the tray menu and
    # one notification - never 77 silent failures.
    manager.sig_account_problem.connect(tray.set_account_problem)

    # The plan, in the tray: its first menu line follows every question,
    # and 80% / all used is said once a month (account.service.alerts).
    try:
        from account import settings as _acc_settings
        from account.service import account as _account
        if _acc_settings.configured():
            _account().on_change(tray.plan_changed.emit)
            tray.plan_changed.emit()          # what was remembered from the last run
    except Exception:  # noqa: BLE001 - the plan line is a convenience
        pass

    # A sign-in that ends while HeyTora runs - Settings → Sign out, or the
    # server no longer accepting it - closes this copy and opens a fresh one
    # at the sign-in window, instead of carrying on unlocked.
    def _account_problem(kind: str):
        if kind != "signin":
            return
        if sign_in_gate.required():
            sign_in_gate.lost()
        else:
            sign_in_gate.check_in_background()      # the server said no: does the sign-in stand?
    manager.sig_account_problem.connect(_account_problem)

    def _sign_in_lost():
        if not sign_in_gate.required():
            return
        import logging as _lg
        _lg.getLogger("tora").info("signed out - restarting at the sign-in window")
        sign_in_gate.restart()
        _shutdown()                           # not _quit: no update installs under the new copy
    sign_in_gate.relay().lost.connect(_sign_in_lost)

    # Panel → Manager
    panel.on_model_changed.connect(manager.set_model)

    def _on_doc_dropped(path: str):
        ok = manager.attach_document(path)
        tray.show_notification(
            "Document Attached" if ok else "Attach failed",
            f"{path}\nAsk Tora about it now." if ok else
            "Couldn't read that file."
        )
    panel.on_document_dropped.connect(_on_doc_dropped)

    # The one-window app (ui/app_window). Built the first time it is asked
    # for — nine pages of widgets is a visible pause, and most sessions never
    # open it — and kept, so what it embeds (the agents panel, a pack's
    # dashboard) is built once. Every control in it goes down the same path
    # as the tray item it stands for, so the windows below still work too.
    app_window = {}

    def _open_app():
        try:
            if "w" not in app_window:
                from ui.app_bridge import AppBridge
                from ui.app_window import ToraApp
                # Everything the window reads comes from these, and every
                # change it makes goes down the tray's own path (APP-10).
                w = ToraApp(AppBridge(manager, tray, panel))
                app_window["w"] = w
            app_window["w"].open_at()
        except Exception as exc:          # noqa: BLE001 - never kill the tray
            import logging as _lg
            _lg.getLogger("tora").exception("could not open the app window")
            tray.show_notification("HeyTora", f"Couldn't open the app: {exc}")

    tray.on_open_app.connect(_open_app)

    # Tray → UI / Manager
    tray.on_show_panel.connect(panel.show)
    tray.on_hide_panel.connect(panel.hide)
    tray.on_toggle_search.connect(manager.set_web_search)
    tray.on_toggle_wake_word.connect(manager.set_wake_word)
    tray.on_toggle_slow_mode.connect(manager.set_slow_mode)
    tray.on_toggle_slow_mode.connect(overlay.set_slow_mode)
    overlay.set_slow_mode(cfg.switch("slow_mode"))    # as it was left (APP-03)
    tray.on_toggle_auto_advance.connect(manager.set_guide_auto_advance)
    tray.on_toggle_quiz_mode.connect(manager.set_quiz_mode)
    tray.on_toggle_privacy.connect(manager.set_privacy_guard)
    tray.on_toggle_code_mode.connect(manager.set_code_mode_auto)
    tray.on_toggle_multilang.connect(manager.set_multilang)
    tray.on_toggle_journal.connect(manager.set_journal)
    tray.on_toggle_ocr.connect(manager.set_ocr_enabled)

    # Lesson recording
    def _record_start():
        out = manager.start_recording()
        tray.show_notification(
            "Lesson Recording",
            f"Recording to:\n{out}" if out else
            "Failed — install imageio[ffmpeg]: pip install imageio imageio-ffmpeg"
        )
    def _record_stop():
        out = manager.stop_recording()
        if out:
            tray.show_notification("Lesson saved", out)
    tray.on_record_start.connect(_record_start)
    tray.on_record_stop.connect(_record_stop)
    manager.sig_recording_state.connect(
        lambda on, _path: tray.set_recording_state(on)
    )
    # Autonomous-guide progress → panel pill AND the cursor status island
    manager.sig_guide_progress.connect(panel.set_guide_progress)
    manager.sig_guide_progress.connect(overlay.set_guide_progress)
    # What the assistant is doing, shown next to the cursor
    manager.sig_activity.connect(overlay.set_activity)

    # Workflow capture
    def _wf_start():
        ok = manager.workflow_start()
        tray.show_notification(
            "Workflow Capture",
            "Recording your clicks + keys. Stop from tray when done."
            if ok else "Install pynput: pip install pynput"
        )
    def _wf_stop():
        summary = manager.workflow_stop()
        if summary:
            tray.show_notification(
                "Workflow Captured",
                "Sent to Tora as context. Ask: 'what did I just do?'"
            )
            # Stash as an attached doc so the next question sees it
            manager.attach_text("recorded_workflow.txt", summary)
    tray.on_workflow_start.connect(_wf_start)
    tray.on_workflow_stop.connect(_wf_stop)

    # Live collab
    tray.on_collab_start.connect(manager.collab_start_host)
    def _collab_join():
        from PyQt6.QtWidgets import QInputDialog
        code, ok = QInputDialog.getText(None, "Join Live Session",
                                        "Enter 6-character session code:")
        if ok and code:
            manager.collab_join(code.strip())
    tray.on_collab_join.connect(_collab_join)

    # Journal folder
    def _open_journal():
        import os, subprocess
        from config import data_dir
        path = str(data_dir())
        try:
            os.startfile(path)
        except Exception:
            subprocess.Popen(["explorer", path])
    tray.on_journal_open.connect(_open_journal)

    # Attach document (drag-drop alternative — file picker)
    def _attach_doc():
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            None, "Attach a document for Tora",
            "", "Documents (*.pdf *.docx *.txt *.md *.csv)"
        )
        if path:
            ok = manager.attach_document(path)
            tray.show_notification(
                "Document Attached",
                f"{path}\nAsk Tora about it now." if ok else
                "Couldn't read that file."
            )
    tray.on_attach_doc.connect(_attach_doc)

    tray.on_stop.connect(manager.stop)

    # Conversation route, voice, model list
    tray.on_set_conversation_mode.connect(manager.set_conversation_mode)
    tray.on_set_voice.connect(manager.set_voice)
    tray.on_refresh_models.connect(manager.refresh_models)

    def _on_models_refreshed(provider: str, count: int):
        panel.refresh_for_provider(provider)
        tray.rebuild_menu()
    manager.sig_models_refreshed.connect(_on_models_refreshed)

    tray.on_set_mic_device.connect(manager.set_mic_device)
    tray.on_set_response_language.connect(manager.set_response_language)
    tray.on_set_stt_language.connect(manager.set_stt_language)
    tray.on_set_custom_instructions.connect(manager.set_custom_instructions)

    # Setup wizard (re-run) + diagnostics. Finishing it may have signed in to
    # a HeyTora account or set a key: the running app takes that up at once.
    def _access_changed():
        manager.on_access_changed()
        tray.rebuild_menu()

    def _run_setup_again():
        from ui.setup_wizard import SetupWizard
        wiz = SetupWizard()
        wiz.completed.connect(_access_changed)
        wiz.show()
        _setup_keepalive[0] = wiz
    tray.on_run_setup.connect(_run_setup_again)

    def _open_api_keys():
        from ui.api_keys_dialog import ApiKeysDialog
        dlg = ApiKeysDialog()

        def _applied():
            # A new key means the model list is worth re-fetching and every
            # surface that renders key state is now stale.
            manager.refresh_models()
            panel.refresh_for_provider("gemini")
            tray.rebuild_menu()
            tray.show_notification(
                "Tora",
                "API key saved." if cfg.has_key() else
                "Key cleared — Tora can't answer without one."
            )

        dlg.keys_saved.connect(_applied)
        dlg.show()
        _setup_keepalive[1] = dlg   # keep a reference so Qt doesn't GC it
    tray.on_api_keys.connect(_open_api_keys)

    def _save_diagnostics():
        import datetime, platform, traceback
        from config import data_dir
        out = data_dir() / f"diagnostics-{datetime.datetime.now():%Y%m%d-%H%M%S}.txt"
        try:
            providers_d = cfg.describe()
        except Exception:
            providers_d = {}
        report = []
        report.append(f"HeyTora diagnostics — {datetime.datetime.now().isoformat()}")
        report.append(f"Python: {sys.version.split()[0]}")
        report.append(f"Platform: {platform.platform()}")
        report.append(f"Active LLM: {providers_d.get('llm', '?')}")
        report.append(f"STT: {providers_d.get('stt', '?')}  TTS: {providers_d.get('tts', '?')}")
        report.append("")
        report.append("─── Models ───")
        try:
            from ai.gemini import Models
            for role, model in Models.all_roles().items():
                report.append(f"  {role:<12} {model}")
            report.append(f"Conversation route: {providers_d.get('conversation', '?')}")
            report.append(f"Voice: {providers_d.get('voice', '?')}")
            report.append(f"Google API key: {providers_d.get('key', '?')}")
        except Exception:
            report.append(traceback.format_exc())
        report.append("")
        report.append("─── Memory ───")
        try:
            from ai import gemini_memory
            report.append(str(gemini_memory.stats()))
        except Exception:
            report.append(traceback.format_exc())
        report.append("")
        try:
            out.write_text("\n".join(report), encoding="utf-8")
            tray.show_notification("Diagnostics saved", str(out))
            try:
                os.startfile(str(out))
            except Exception:
                pass
        except Exception as e:
            tray.show_notification("Diagnostics failed", str(e))
    tray.on_diagnostics.connect(_save_diagnostics)

    tray.on_quit.connect(_quit)

    # ── Updates, while running ────────────────────────────────────────────────
    # Checked a little after start-up and every few hours (CHECK_EVERY_S); a
    # newer version downloads in the background. Ready: the tray and
    # Settings offer "Restart to update" - or it installs by itself next
    # time. One the server requires is asked for at once.
    upd.on_change(tray.updates_changed.emit)
    tray.updates_changed.emit()

    def _install_update():
        if upd.install_now(relaunch=True):
            _shutdown()
    tray.on_install_update.connect(_install_update)
    if update_outcome == "updated":
        tray.show_notification("HeyTora updated", f"You're on HeyTora {upd.current} now.")

    _required_asked = set()

    def _required_update():
        st = upd.state()
        if not (st["required"] and st["state"] == "ready") or st["latest"] in _required_asked:
            return
        _required_asked.add(st["latest"])
        from PyQt6.QtWidgets import QMessageBox
        box = QMessageBox(QMessageBox.Icon.Information, "HeyTora needs an update",
                          f"HeyTora {st['latest']} is ready, and this version can't keep working "
                          "without it. It installs in a moment and HeyTora opens again.")
        now = box.addButton("Update now", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        box.exec()
        if box.clickedButton() is now:
            _install_update()
    tray.updates_changed.connect(_required_update)

    QTimer.singleShot(15_000, upd.check_soon)
    _update_timer = QTimer(app)
    _update_timer.setInterval(60 * 60 * 1000)           # check_soon itself waits CHECK_EVERY_S
    _update_timer.timeout.connect(upd.check_soon)
    _update_timer.start()

    # ── Global hotkey ─────────────────────────────────────────────────────────
    hotkey = GlobalHotkeyMonitor(
        on_press=manager.on_hotkey_press,
        on_release=manager.on_hotkey_release,
    )
    hotkey.start()

    # Esc = cancel the answer in progress (stops speech instantly), and the
    # kill switch for any agent that has the mouse - see _wire_agents.
    def _esc():
        manager.stop()
        tray.on_stop.emit()
    stop_key = StopHotkey(on_stop=_esc, key="esc")
    stop_key.start()

    # ── Show UI + start listener ──────────────────────────────────────────────
    overlay.show()        # persistent overlay (cursor follow)
    # Panel is hidden by default — user can open it from the tray menu if needed
    # The workforce builds on a worker thread (~650 ms) so the tray icon
    # is not held up; wire its UI when it says it is ready.
    manager.sig_agents_ready.connect(lambda: _wire_agents(manager, tray))
    manager.start()        # begin ambient mic + wake-word scanning; brings up agents
    if door == "ok":
        sign_in_gate.check_in_background()   # still an account the server accepts?

    providers = cfg.describe()
    _hk = "+".join(p.strip().capitalize() for p in cfg.hotkey.split("+"))
    tray.show_notification(
        "HeyTora is running",
        (f"Say 'Tora' or press {_hk}  |  LLM: {providers['llm']}"
         if cfg.ambient_mic() else
         f"Press {_hk}, ask your question, and Tora answers when you stop "
         f"talking  |  LLM: {providers['llm']}"),
    )

    # ── First-run setup wizard ────────────────────────────────────────────────
    # Show the welcome + API key walkthrough on the first launch.
    # If everything is already wired up, the helper is a no-op.
    try:
        from ui.setup_wizard import maybe_show_setup_wizard, SetupWizard

        # Force-show via env var (handy for testing).
        if os.environ.get("TORA_FORCE_SETUP", "").strip() in ("1", "true", "yes"):
            wiz = SetupWizard()
            wiz.show()
        elif door == "signed_in":
            wiz = None                        # the gate just showed it
        else:
            wiz = maybe_show_setup_wizard()
        if wiz is not None:
            wiz.completed.connect(_access_changed)
            _setup_keepalive[0] = wiz       # keep a reference so it isn't GC'd
    except Exception as e:
        print(f"[setup-wizard] skipped: {e}")

    sys.exit(app.exec())


# Module-level slots keeping references to the setup wizard [0] and the API
# keys dialog [1] alive while Qt is running (PyQt will GC them otherwise and
# the dialog will vanish).
_setup_keepalive: list = [None, None]


if __name__ == "__main__":
    main()
