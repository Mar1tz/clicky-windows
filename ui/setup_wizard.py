"""
First-run setup wizard.

Shown once on the first launch, or whenever the user clicks
"Tray → Run setup again…". There is only one thing to set up now:

  0. What Tora is and how to talk to it
  1. A Google API key
  2. Check the key actually works, and say which models it reached

The old wizard spent four screens installing Ollama and pulling several
gigabytes of local weights before Tora could answer anything. With one
cloud provider there is nothing to install — paste a key and it works — so
this is a short walkthrough rather than a download manager.

The verify step is worth its screen: a typo'd or restricted key otherwise
fails silently later, as a Tora that hears you and then says nothing.
"""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar,
)

from config import cfg


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
    return "+".join(p.strip().capitalize() for p in cfg.hotkey.split("+"))


# ─── Wizard ───────────────────────────────────────────────────────────────────

class SetupWizard(QDialog):
    """One window: welcome → key → verify → done."""

    finished_signal = pyqtSignal(bool, str)   # ok, message

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("HeyTora Setup")
        self.setModal(False)
        self.setMinimumSize(600, 420)
        self.setStyleSheet("""
            QDialog { background: #0e1014; color: #e8eaed; }
            QLabel  { color: #e8eaed; }
            QLabel#title { font-size: 22px; font-weight: 700; }
            QLabel#subtitle { color: #a0a3a8; font-size: 13px; }
            QLabel#status { color: #c8cbd0; font-size: 13px; }
            QPushButton {
                background: #1f6feb; color: white; border: none;
                padding: 10px 18px; border-radius: 8px;
                font-weight: 600; font-size: 13px;
            }
            QPushButton:hover  { background: #2f7fff; }
            QPushButton:disabled { background: #333; color: #888; }
            QPushButton#secondary {
                background: transparent; color: #a0a3a8;
                border: 1px solid #2a2d33;
            }
            QPushButton#secondary:hover { color: #e8eaed; border-color: #444; }
            QProgressBar {
                background: #1a1d22; border: 1px solid #2a2d33;
                border-radius: 6px; height: 12px; text-align: center;
                color: #e8eaed; font-size: 11px;
            }
            QProgressBar::chunk { background: #1f6feb; border-radius: 6px; }
        """)

        self._keys_dialog = None
        self._worker: threading.Thread | None = None
        self._step = "welcome"

        self._build_ui()
        self.finished_signal.connect(self._on_finished)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 24)
        layout.setSpacing(14)

        self.title = QLabel("Welcome to HeyTora")
        self.title.setObjectName("title")
        layout.addWidget(self.title)

        self.subtitle = QLabel("")
        self.subtitle.setObjectName("subtitle")
        self.subtitle.setWordWrap(True)
        layout.addWidget(self.subtitle)

        self.status = QLabel("")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        layout.addSpacing(8)
        layout.addWidget(self.status)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)     # indeterminate — it's one request
        self.progress.hide()
        layout.addWidget(self.progress)

        layout.addStretch(1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.skip_btn = QPushButton("Skip")
        self.skip_btn.setObjectName("secondary")
        self.skip_btn.clicked.connect(self._on_skip)
        btn_row.addWidget(self.skip_btn)

        btn_row.addStretch(1)

        self.alt_btn = QPushButton("")
        self.alt_btn.setObjectName("secondary")
        self.alt_btn.clicked.connect(self._on_alt)
        self.alt_btn.hide()
        btn_row.addWidget(self.alt_btn)

        self.action_btn = QPushButton("Get started")
        self.action_btn.clicked.connect(self._on_action)
        btn_row.addWidget(self.action_btn)

        layout.addLayout(btn_row)

        self._set_step("welcome")

    # ── State machine ────────────────────────────────────────────────────────

    def _set_step(self, step: str):
        self._step = step
        self.progress.hide()
        self.alt_btn.hide()
        self.skip_btn.show()
        self.action_btn.setEnabled(True)
        hk = _pretty_hotkey()

        if step == "welcome":
            self.title.setText("Welcome to HeyTora")
            self.subtitle.setText(
                f"Tora sits next to your cursor, watches your screen and "
                f"talks you through whatever you're doing.\n\n"
                f"Hold {hk} and ask a question — Tora answers when you "
                f"stop talking, and points at what it means."
            )
            self.status.setText("")
            self.action_btn.setText("Get started")

        elif step == "key":
            self.title.setText("One key, and you're done")
            self.subtitle.setText(
                "HeyTora runs on Google's Gemini models — the conversation, "
                "reading your screen, hearing you and speaking back are all "
                "Gemini.\n\n"
                "Get a free key from Google AI Studio and paste it in. "
                "Nothing to install and nothing to download."
            )
            self._refresh_key_status()
            self.action_btn.setText(
                "Continue" if cfg.has_key() else "Paste an API key…"
            )
            if cfg.has_key():
                self.alt_btn.setText("Change key…")
                self.alt_btn.show()

        elif step == "verify":
            self.title.setText("Checking your key…")
            self.subtitle.setText(
                "One request to Google to confirm the key works and the "
                "models Tora needs are reachable."
            )
            self.status.setText("Connecting…")
            self.progress.show()
            self.action_btn.setEnabled(False)
            self._start_verify_worker()

        elif step == "done":
            self.title.setText("You're all set")
            self.subtitle.setText(
                f"Hold {hk} and ask Tora something about what's on your "
                f"screen. Try \"where do I change the font size?\" or "
                f"\"what am I looking at?\"\n\n"
                f"Everything else lives in the tray icon: the voice, whether "
                f"Tora listens for its name, web search, and a diagnostics "
                f"report if anything misbehaves."
            )
            self.skip_btn.hide()
            self.action_btn.setText("Finish")

    def _refresh_key_status(self):
        if cfg.has_key():
            self.status.setText("✓  A Google API key is saved.")
        else:
            self.status.setText(
                "No key yet — Tora cannot answer anything without one."
            )

    # ── Buttons ──────────────────────────────────────────────────────────────

    def _on_action(self):
        s = self._step
        if s == "welcome":
            self._set_step("key")
        elif s == "key":
            if cfg.has_key():
                self._set_step("verify")
            else:
                self._open_keys_dialog()
        elif s in ("verify", "key_retry"):
            self._set_step("done")
        elif s == "done":
            mark_setup_complete()
            self.close()

    def _on_alt(self):
        if self._step in ("key", "key_retry"):
            self._open_keys_dialog()

    def _on_skip(self):
        if self._step == "welcome":
            self._set_step("key")
        elif self._step in ("key", "verify", "key_retry"):
            self._set_step("done")
        else:
            mark_setup_complete()
            self.close()

    def _open_keys_dialog(self):
        from ui.api_keys_dialog import ApiKeysDialog
        dlg = ApiKeysDialog(self)
        dlg.keys_saved.connect(self._on_keys_saved)
        dlg.finished.connect(lambda _=0: self._refresh_key_status())
        dlg.show()
        self._keys_dialog = dlg    # keep a reference so Qt doesn't GC it

    def _on_keys_saved(self):
        self._refresh_key_status()
        if cfg.has_key():
            self._set_step("verify")

    # ── Verification ─────────────────────────────────────────────────────────

    def _start_verify_worker(self):
        """Confirm the key works, off the UI thread.

        Runs on its own event loop in a worker thread — the wizard can open
        before the manager exists, so there is no shared loop to borrow.
        """
        def _worker():
            async def _check():
                from ai import gemini
                from ai.gemini import Models
                try:
                    if not await gemini.health_check():
                        return False, (
                            "Google rejected that key. Check it was copied in "
                            "full, and that the Generative Language API is "
                            "enabled for the project it belongs to."
                        )
                    from ai.model_registry import refresh
                    models = await refresh()
                    return True, (
                        f"✓  Key works — {len(models)} models available.\n\n"
                        f"Conversation:  {Models.REALTIME}\n"
                        f"Reading your screen:  {Models.VISION_FAST} "
                        f"/ {Models.VISION_DEEP}\n"
                        f"Hearing you:  {Models.STT}\n"
                        f"Speaking:  {Models.TTS}"
                    )
                except Exception as e:
                    return False, f"Could not reach Google: {e}"
                finally:
                    await gemini.aclose()

            try:
                ok, msg = asyncio.run(_check())
            except Exception as e:
                ok, msg = False, str(e)
            self.finished_signal.emit(ok, msg)

        self._worker = threading.Thread(target=_worker, daemon=True)
        self._worker.start()

    def _on_finished(self, ok: bool, msg: str):
        self.progress.hide()
        self.status.setText(msg)
        self.action_btn.setEnabled(True)
        if ok:
            self.title.setText("Key verified")
            self.subtitle.setText("")
            self.action_btn.setText("Continue")
        else:
            self.title.setText("That key didn't work")
            self.subtitle.setText("")
            self.action_btn.setText("Continue anyway")
            self.alt_btn.setText("Try another key…")
            self.alt_btn.show()
            self._step = "key_retry"


def maybe_show_setup_wizard(parent=None) -> "SetupWizard | None":
    """Open the wizard on first run.

    Also opens when there is no key at all, however many times Tora has
    been launched: without one nothing works, and a silent do-nothing app is
    a worse answer than a window explaining why.
    """
    if setup_already_ran() and cfg.has_key():
        return None

    w = SetupWizard(parent)
    w.show()
    return w
