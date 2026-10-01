"""No HeyTora account, no HeyTora (2 Oct 2026).

A reinstall on a PC with a Gemini key in its environment and an old
"setup done" note opened straight into a working app - no sign-in, no first
launch. Now nothing starts until the user is signed in (ui/sign_in_gate.py):
the sign-in window cannot be skipped, closing it closes the app, an own key
is on top of an account and not instead of one, and a sign-in that ends
while running sends the app back to that window. Fake Supabase, no network.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PyQt6")

from tests.agents.test_e2e_first_launch import (  # noqa: E402,F401 - fixtures
    _sign_in, _until, _wizard, qapp, service,
)

ROOT = Path(__file__).resolve().parents[2]


def test_signing_in_is_required_whatever_keys_are_around(qapp, service, monkeypatch):
    from config import cfg
    from ui import setup_wizard, sign_in_gate
    monkeypatch.setattr(cfg, "google_api_key", "a-key-in-the-environment")
    monkeypatch.setenv("TORA_ACCESS", "own_key")
    setup_wizard.mark_setup_complete()
    assert cfg.has_key(), "the case that slipped through: a key, and setup done before"
    assert sign_in_gate.required(), "still: no account, no HeyTora"
    _sign_in(service)
    assert not sign_in_gate.required()


def test_a_build_without_accounts_has_no_gate(qapp, monkeypatch):
    from ui import sign_in_gate
    monkeypatch.delenv("TORA_SUPABASE_URL", raising=False)
    assert not sign_in_gate.required() and sign_in_gate.gate() == "ok"


def test_the_sign_in_page_cannot_be_skipped(qapp, service):
    w = _wizard(service.account, start="connect")
    assert w.page() == "connect" and w.windowTitle()
    assert not w.skip_btn.isVisibleTo(w) and not w.next_btn.isEnabled()
    assert not w.key_card.isEnabled(), "an own key comes after the account, not instead"
    w._next()
    assert w.page() == "connect" and "Sign in first" in w.connect_status.text()

    _sign_in(service)
    w._refresh_connect()
    assert w.next_btn.isEnabled() and w.key_card.isEnabled()
    w._next()
    assert w.page() == "mic"


def test_esc_does_not_close_it(qapp, service):
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QKeyEvent
    w = _wizard(service.account, start="connect")
    w.show()
    qapp.sendEvent(w, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    qapp.processEvents()
    assert w.isVisible()
    w.close()


def _gate_with(service, act):
    """Run the gate with a real wizard; `act(wizard)` runs once it is up."""
    from PyQt6.QtCore import QTimer
    from ui import sign_in_gate
    made = []

    def factory(start):
        w = _wizard(service.account, start=start)
        made.append(w)
        QTimer.singleShot(30, lambda: act(w))
        return w
    return sign_in_gate.gate(wizard_factory=factory), made


def test_closing_the_window_without_signing_in_closes_heytora(qapp, service):
    result, made = _gate_with(service, lambda w: w.close())
    assert result == "closed" and made and not made[0].isVisible()


def test_a_fresh_install_gets_the_whole_first_launch(qapp, service):
    from ui import setup_wizard
    assert not setup_wizard.setup_already_ran()
    pages = []
    result, _made = _gate_with(service, lambda w: (pages.append(w.page()), w.close()))
    assert pages == ["welcome"] and result == "closed"


def test_after_a_sign_out_it_opens_at_signing_in(qapp, service):
    from ui import setup_wizard
    setup_wizard.mark_setup_complete()
    pages = []
    result, _made = _gate_with(service, lambda w: (pages.append(w.page()), w.close()))
    assert pages == ["connect"] and result == "closed"


def test_signing_in_through_the_window_lets_heytora_start(qapp, service):
    def act(w):
        _sign_in(service)
        w._refresh_connect()
        w._go("done")
        w._next()                                        # Finish
    result, _made = _gate_with(service, act)
    assert result == "signed_in" and service.account.signed_in()


def test_already_signed_in_shows_nothing(qapp, service):
    from ui import sign_in_gate
    _sign_in(service)
    shown = []
    assert sign_in_gate.gate(wizard_factory=lambda start: shown.append(start)) == "ok" and not shown


def test_the_gate_comes_before_anything_starts():
    """main.py: no overlay, hotkey, microphone or agents before the gate."""
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    gate = src.index("door = sign_in_gate.gate()")
    for later in ("manager = CompanionManager()", "overlay = CursorOverlay()", "hotkey.start()",
                  "manager.start()", "tray    = TrayManager()"):
        assert gate < src.index(later), later
    assert 'if door == "closed":\n        return' in src


# -- a sign-in that ends while HeyTora runs --------------------------------------------------------------------

def _spy_lost(qapp):
    from ui import sign_in_gate
    hits = []
    relay = sign_in_gate.relay()
    relay.lost.connect(lambda: hits.append(1))
    return hits, lambda: relay.lost.disconnect()


def test_signing_out_sends_heytora_back_to_the_window(qapp, service):
    from ui.app_bridge import AppBridge
    _sign_in(service)
    hits, done = _spy_lost(qapp)
    try:
        AppBridge().account_sign_out()
        _until(qapp, lambda: hits)
        assert not service.account.signed_in()
    finally:
        done()


def test_the_server_ending_the_sign_in_locks_heytora(qapp, service):
    from account.service import SignInNeeded
    from ui import sign_in_gate
    _sign_in(service)
    service.fake.ended = True                           # deleted, or signed out everywhere
    hits, done = _spy_lost(qapp)
    try:
        sign_in_gate.check_in_background()
        _until(qapp, lambda: hits)
        assert sign_in_gate.required(), "forgotten here too"
        with pytest.raises(SignInNeeded):
            service.account.token()
    finally:
        done()


def test_offline_is_not_a_lost_sign_in(qapp, service, monkeypatch):
    import httpx
    from ui import sign_in_gate
    _sign_in(service)

    def offline(_request):
        raise httpx.ConnectError("no internet")
    service.account._transport = httpx.MockTransport(offline)
    hits, done = _spy_lost(qapp)
    try:
        service.account._summary_at = 0
        sign_in_gate.check_in_background()
        end = time.monotonic() + 0.5
        while time.monotonic() < end:
            qapp.processEvents()
            time.sleep(0.01)
        assert not hits and service.account.signed_in()
    finally:
        done()


def test_restart_starts_the_same_app_again(monkeypatch):
    from ui import sign_in_gate
    started = []
    monkeypatch.setattr(sign_in_gate.QProcess, "startDetached",
                        staticmethod(lambda prog, args, cwd: started.append((prog, args)) or (True, 1)))
    monkeypatch.setattr(sys, "argv", ["main.py"])
    assert sign_in_gate.restart()
    assert started[-1][0] == sys.executable and started[-1][1][0].endswith("main.py")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "argv", [r"C:\HeyTora\HeyTora.exe"])
    sign_in_gate.restart()
    assert started[-1] == (sys.executable, [])


def test_uninstalling_signs_out_and_forgets_the_first_launch():
    from account import store
    from ui import setup_wizard
    iss = (ROOT / "installer.iss").read_text(encoding="utf-8")
    assert store.path().name == "account.bin" and setup_wizard._flag_path().name == "setup_complete.flag"
    assert r'Type: files; Name: "{localappdata}\HeyTora\account.bin"' in iss
    assert r'Type: files; Name: "{localappdata}\HeyTora\setup_complete.flag"' in iss
