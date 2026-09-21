"""
Tora for Windows — Entry Point.
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
    showed only the bare message with no traceback (GitHub issue #15).

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
from PyQt6.QtCore import Qt

from config import cfg
from ui.tray import TrayManager
from ui.panel import CompanionPanel, AppState
from ui.overlay import (
    CursorOverlay, MODE_IDLE, MODE_LISTENING, MODE_THINKING, MODE_SPEAKING
)
from hotkey import GlobalHotkeyMonitor, StopHotkey
from companion_manager import CompanionManager


STATE_TO_CURSOR_MODE = {
    AppState.IDLE:      MODE_IDLE,
    AppState.LISTENING: MODE_LISTENING,
    AppState.THINKING:  MODE_THINKING,
    AppState.SPEAKING:  MODE_SPEAKING,
}


def _setup_logging():
    """Rotating runtime log at %LOCALAPPDATA%\\Tora\\tora.log — the #1
    ask from bug reports: with no log file, 'stuck on Listening' class issues
    were undiagnosable (GitHub issue #6)."""
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
        logging.getLogger("tora").info(
            "=== Tora starting (python %s) ===", sys.version.split()[0]
        )
    except Exception:
        pass  # logging must never block startup


def main():
    _setup_logging()
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Tora")
    app.setApplicationDisplayName("Tora - AI Companion")

    # ── Core components ───────────────────────────────────────────────────────
    manager = CompanionManager()
    panel   = CompanionPanel()
    overlay = CursorOverlay()
    tray    = TrayManager()

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

    # Audio level → cursor waveform (+ panel meter)
    manager.sig_audio_level.connect(panel.set_audio_level)
    manager.sig_audio_level.connect(overlay.set_audio_level)

    # Pointing directives
    manager.sig_point_at.connect(overlay.point_at)
    manager.sig_point_hold.connect(overlay.set_point_hold)
    manager.sig_point_release.connect(overlay.release_point)

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
            "Tora error", f"{e}\n\nFull details: {_log_hint}"
        )
    )

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

    # Tray → UI / Manager
    tray.on_show_panel.connect(panel.show)
    tray.on_hide_panel.connect(panel.hide)
    tray.on_toggle_search.connect(manager.set_web_search)
    tray.on_toggle_wake_word.connect(manager.set_wake_word)
    tray.on_toggle_slow_mode.connect(manager.set_slow_mode)
    tray.on_toggle_slow_mode.connect(overlay.set_slow_mode)
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
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        path = os.path.join(base, "Tora")
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

    # Setup wizard (re-run) + diagnostics
    def _run_setup_again():
        from ui.setup_wizard import SetupWizard
        wiz = SetupWizard()
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
        report.append(f"Tora diagnostics — {datetime.datetime.now().isoformat()}")
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

    tray.on_quit.connect(lambda: (tray.hide_icon(), manager.shutdown(), app.quit()))

    # ── Global hotkey ─────────────────────────────────────────────────────────
    hotkey = GlobalHotkeyMonitor(
        on_press=manager.on_hotkey_press,
        on_release=manager.on_hotkey_release,
    )
    hotkey.start()

    # Esc = cancel the answer in progress (stops speech instantly)
    stop_key = StopHotkey(on_stop=manager.stop, key="esc")
    stop_key.start()

    # ── Show UI + start listener ──────────────────────────────────────────────
    overlay.show()        # persistent overlay (cursor follow)
    # Panel is hidden by default — user can open it from the tray menu if needed
    manager.start()        # begin ambient mic + wake-word scanning

    providers = cfg.describe()
    _hk = "+".join(p.strip().capitalize() for p in cfg.hotkey.split("+"))
    tray.show_notification(
        "Tora is running",
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
            _setup_keepalive[0] = wiz
        else:
            wiz = maybe_show_setup_wizard()
            if wiz is not None:
                _setup_keepalive[0] = wiz   # keep a reference so it isn't GC'd
    except Exception as e:
        print(f"[setup-wizard] skipped: {e}")

    sys.exit(app.exec())


# Module-level slots keeping references to the setup wizard [0] and the API
# keys dialog [1] alive while Qt is running (PyQt will GC them otherwise and
# the dialog will vanish).
_setup_keepalive: list = [None, None]


if __name__ == "__main__":
    main()
