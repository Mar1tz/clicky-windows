"""
First launch: getting to know the user, and getting them connected.

Shown once on the first launch (and from "Tray → Run setup again…"), and
whenever Tora has no way to reach Gemini. Seven short pages:

  welcome → name → what the PC is for → connect → microphone → start with
  Windows → done

"Connect" is the one that matters: sign in to the HeyTora account made on
the website (an emailed code, no password) - Gemini then runs through
HeyTora's server on that account's plan, and the business key never comes
to this PC - or paste one's own Gemini key, straight to Google. A build
without HeyTora's service filled in (account/settings.py) offers only the
key.

The name and what the PC is for go into Tora's instructions (cfg.profile),
so the first answer already starts from the user's world.

Network work (sending the code, signing in, checking a key) runs on worker
threads and reports back through signals: the window never freezes.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog, QGridLayout, QHBoxLayout, QLineEdit, QPushButton, QStackedWidget, QVBoxLayout,
    QWidget,
)

from config import cfg
from ui import app_theme as T
from ui.app_widgets import Card, Meter, Select, ToggleRow, button, label, lede, title


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


# What a PC is for - shown as chips. Kept short and in the user's words.
USES = ("Work & email", "School & study", "Writing", "Design & creative", "Coding",
        "Accounting & money", "Running a business", "Video & content", "Gaming",
        "Everyday stuff")

PAGES = ("welcome", "name", "uses", "connect", "mic", "startup", "done")


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
    """The first launch, one page at a time."""

    completed = pyqtSignal()                       # finished: access may have changed
    _result = pyqtSignal(str, bool, str)           # (job, ok, message) from a worker thread

    def __init__(self, parent=None, *, account=None,
                 check_key: Callable[[str], tuple[bool, str]] = _check_own_key):
        super().__init__(parent)
        self.setWindowTitle("Welcome to HeyTora")
        self.setModal(False)
        self.setFixedSize(720, 560)
        self.setStyleSheet(T.qss() + f"QDialog {{ background: {T.BG}; }}")
        if account is None:
            from account.service import account as _app_account
            account = _app_account()
        self._account = account
        self._check_key = check_key
        self._email = ""
        self._connected = cfg.has_key()
        self._level = 0.0
        self._stream = None

        self._result.connect(self._on_result)
        self._build()
        self._go("welcome")

    # ── layout ────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(36, 28, 36, 24)
        outer.setSpacing(16)
        self.steps = label("", "faint")
        outer.addWidget(self.steps)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.pages: dict[str, QWidget] = {}
        for name in PAGES:
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(12)
            getattr(self, f"_page_{name}")(lay)
            lay.addStretch(1)
            self.pages[name] = page
            self.stack.addWidget(page)

        row = QHBoxLayout()
        self.back_btn = button("Back")
        self.back_btn.clicked.connect(self._back)
        row.addWidget(self.back_btn)
        row.addStretch(1)
        self.skip_btn = button("Skip")
        self.skip_btn.clicked.connect(self._next)
        row.addWidget(self.skip_btn)
        self.next_btn = button("Continue", "primary")
        self.next_btn.clicked.connect(self._next)
        row.addWidget(self.next_btn)
        outer.addLayout(row)

    def _page_welcome(self, lay) -> None:
        lay.addWidget(title("Hi, I'm Tora"))
        lay.addWidget(lede(
            "I sit next to your cursor, look at your screen when you ask, and talk you "
            "through whatever you're doing - and point at what to click.\n\n"
            "A few quick questions and we're set."))

    def _page_name(self, lay) -> None:
        lay.addWidget(title("What should I call you?"))
        self.name_edit = QLineEdit(cfg.profile()[0])
        self.name_edit.setPlaceholderText("Your first name")
        self.name_edit.setMaxLength(40)
        lay.addWidget(self.name_edit)

    def _page_uses(self, lay) -> None:
        lay.addWidget(title("What do you use this PC for?"))
        lay.addWidget(lede("Pick any - it helps me start from your world."))
        grid = QGridLayout()
        grid.setSpacing(8)
        chosen = set(cfg.profile()[1])
        self.use_chips: list[QPushButton] = []
        for i, use in enumerate(USES):
            chip = button(use.replace("&", "&&"))       # a lone & would become a shortcut marker
            chip.setProperty("use", use)
            chip.setCheckable(True)
            chip.setChecked(use in chosen)
            chip.setStyleSheet(
                "QPushButton:checked { background: rgba(10, 132, 255, 0.22);"
                f" border: 1px solid {T.ACCENT}; color: {T.TEXT}; }}")
            grid.addWidget(chip, i // 2, i % 2)
            self.use_chips.append(chip)
        lay.addLayout(grid)

    def _page_connect(self, lay) -> None:
        from account import settings
        lay.addWidget(title("Connect to Gemini"))
        lay.addWidget(lede("Tora thinks with Google's Gemini. Sign in to your HeyTora account, "
                           "or use your own Gemini key."))
        self.connect_status = label("", "faint", wrap=True)

        if settings.configured():
            card = Card("Sign in to HeyTora", "The account you made on the website - "
                                             "we'll email you a sign-in code.")
            self.email_edit = QLineEdit(self._account.email)
            self.email_edit.setPlaceholderText("you@example.com")
            self.send_btn = button("Send code", "primary")
            self.send_btn.setFixedWidth(120)
            self.send_btn.clicked.connect(self._send_code)
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(self.email_edit, 1)
            row.addWidget(self.send_btn)
            card.body.addLayout(row)
            self.code_row = QWidget()
            code_lay = QHBoxLayout(self.code_row)
            code_lay.setContentsMargins(0, 0, 0, 0)
            code_lay.setSpacing(8)
            self.code_edit = QLineEdit()
            self.code_edit.setPlaceholderText("Code from the email")
            self.code_edit.setMaxLength(12)          # 6-10 digits, maybe pasted with spaces
            self.signin_btn = button("Sign in", "primary")
            self.signin_btn.setFixedWidth(120)
            self.signin_btn.clicked.connect(self._sign_in)
            code_lay.addWidget(self.code_edit, 1)
            code_lay.addWidget(self.signin_btn)
            self.code_row.hide()
            card.body.addWidget(self.code_row)
            if settings.website_url():
                make = button("No account yet? Create one", "link")
                make.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(settings.website_url())))
                card.body.addWidget(make, 0, Qt.AlignmentFlag.AlignLeft)
            lay.addWidget(card)
        else:
            self.email_edit = self.code_edit = None

        key_card = Card("Use my own Gemini key", "Free from aistudio.google.com/app/apikey. "
                                                 "Straight to Google, no HeyTora limits.")
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("Paste your Gemini API key")
        self.key_btn = button("Use this key")
        self.key_btn.setFixedWidth(120)
        self.key_btn.clicked.connect(self._use_key)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.key_edit, 1)
        row.addWidget(self.key_btn)
        key_card.body.addLayout(row)
        lay.addWidget(key_card)
        lay.addWidget(self.connect_status)

    def _page_mic(self, lay) -> None:
        lay.addWidget(title("Let's check your microphone"))
        lay.addWidget(lede("Say something - the bar should move."))
        self.mic_select = Select(width=420)
        self.mic_ids: list[int] = []
        lay.addWidget(self.mic_select)
        self.level_meter = Meter(0.0, height=8)
        lay.addWidget(self.level_meter)
        self.mic_note = label("", "faint", wrap=True)
        lay.addWidget(self.mic_note)
        shortcut = Card("How to talk to me",
                        f"Hold {_pretty_hotkey()}, ask, and let go. I answer out loud, "
                        f"and point at what I mean.")
        lay.addWidget(shortcut)

    def _page_startup(self, lay) -> None:
        lay.addWidget(title("Start with Windows?"))
        lay.addWidget(lede("So I'm there whenever you need me - you can change it any time "
                           "in Settings."))
        self.startup_row = ToggleRow("Start HeyTora when Windows starts",
                                     "For you only - no admin rights needed.", True, first=True)
        lay.addWidget(self.startup_row)

    def _page_done(self, lay) -> None:
        self.done_title = title("You're all set")
        lay.addWidget(self.done_title)
        self.done_text = lede("")
        lay.addWidget(self.done_text)

    # ── navigation ──────────────────────────────────────────────────────────────

    def page(self) -> str:
        return PAGES[self.stack.currentIndex()]

    def _go(self, name: str) -> None:
        if self.page() == "mic" and name != "mic":
            self._stop_mic()
        self.stack.setCurrentWidget(self.pages[name])
        i = PAGES.index(name)
        self.steps.setText("" if name in ("welcome", "done") else f"Step {i} of {len(PAGES) - 2}")
        self.back_btn.setVisible(name not in ("welcome", "done"))
        self.skip_btn.setVisible(name in ("name", "uses", "connect", "mic"))
        self.next_btn.setText({"welcome": "Get started", "done": "Finish"}.get(name, "Continue"))
        if name == "connect":
            self._refresh_connect()
        elif name == "mic":
            self._start_mic()
        elif name == "done":
            self._fill_done()

    def _next(self) -> None:
        name = self.page()
        if name == "name" or (name == "uses" and self.sender() is self.next_btn):
            self._save_profile()
        if name == "startup":
            self._save_startup()
        if name == "done":
            self._finish()
            return
        self._go(PAGES[PAGES.index(name) + 1])

    def _back(self) -> None:
        self._go(PAGES[max(0, PAGES.index(self.page()) - 1)])

    def _finish(self) -> None:
        mark_setup_complete()
        self._stop_mic()
        self.completed.emit()
        self.close()

    def closeEvent(self, event) -> None:
        self._stop_mic()
        super().closeEvent(event)

    # ── name and uses ───────────────────────────────────────────────────────────

    def _save_profile(self) -> None:
        uses = [c.property("use") for c in self.use_chips if c.isChecked()]
        cfg.set_profile(self.name_edit.text(), uses)

    # ── connect ─────────────────────────────────────────────────────────────────

    def _refresh_connect(self) -> None:
        if self._account.signed_in() and cfg.access_mode() == "account":
            self.connect_status.setText(f"✓  Signed in as {self._account.email}.")
        elif cfg.access_mode() == "own_key" and cfg.google_api_key:
            self.connect_status.setText("✓  Using your own Gemini key.")
        else:
            self.connect_status.setText("")

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
        self.connect_status.setText("Sending a code…")
        self._run("code", lambda: self._account.send_code(self._email))

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
                self.connect_status.setText(f"Code sent to {self._email} - it can take a minute.")
            else:
                self.connect_status.setText(message)
        elif job == "signin":
            self.signin_btn.setEnabled(True)
            if ok:
                self._connected = True
                self.connect_status.setText(f"✓  Signed in. {message}")
            else:
                self.connect_status.setText(message)
        elif job == "key":
            self.key_btn.setEnabled(True)
            if ok:
                self._connected = True
                self.key_edit.clear()
                self.connect_status.setText("✓  Your key works - you're connected.")
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
        current = cfg.mic_device_index
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
            self.mic_note.setText("No microphone found. Plug one in - you can choose it later "
                                  "in Settings.")
            return
        self.mic_note.setText("")
        self._open_stream(self.mic_ids[pick])
        self._meter_timer = QTimer(self)
        self._meter_timer.timeout.connect(lambda: self.level_meter.set_fraction(min(1.0, self._level * 6)))
        self._meter_timer.start(50)

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
            self.mic_note.setText("That microphone couldn't be opened - try another one.")

    def _pick_mic(self, k: int) -> None:
        if 0 <= k < len(self.mic_ids):
            device = self.mic_ids[k]
            cfg.mic_device_index = device
            cfg._write_env("MIC_DEVICE_INDEX", str(device))
            self._open_stream(device)

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

    # ── start with Windows, and done ────────────────────────────────────────────

    def _save_startup(self) -> None:
        try:
            from ui import autostart
            autostart.set_enabled(self.startup_row.switch.isChecked())
        except Exception:  # noqa: BLE001 - a nicety, never a blocker
            pass

    def _fill_done(self) -> None:
        name = cfg.profile()[0]
        self.done_title.setText(f"You're all set, {name}" if name else "You're all set")
        if not cfg.has_key():
            how = ("You're not connected yet - open the tray icon's menu and run setup again "
                   "to sign in or add a key.")
        else:
            how = (f"Hold {_pretty_hotkey()} and ask me anything about what's on your screen - "
                   f"try \"where do I change the font size?\"")
        self.done_text.setText(how + "\n\nEverything else is in the tray icon by the clock.")


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
