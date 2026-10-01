"""The first launch (1 Oct 2026): getting to know the user, and connecting.

name → what the PC is for → sign in to HeyTora (or an own Gemini key) →
microphone → start with Windows → done. And Settings' account card: the
plan and this month's use. No real microphone, network or profile.
"""
from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402

pytest.importorskip("PyQt6")

from tests.agents.test_e2e_heytora_account import SUPABASE, FakeHeyTora  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def service(monkeypatch):
    from account import service as svc
    monkeypatch.setenv("TORA_SUPABASE_URL", SUPABASE)
    monkeypatch.setenv("TORA_SUPABASE_ANON_KEY", "anon-public-key")
    monkeypatch.setenv("TORA_WEBSITE_URL", "https://heytora.example")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)     # restored afterwards: set_api_key writes it
    fake = FakeHeyTora()
    acc = svc.Account(transport=httpx.MockTransport(fake))
    svc.reset(acc)
    yield SimpleNamespace(fake=fake, account=acc)
    svc.reset(None)


def _wizard(account, **kw):
    from ui.setup_wizard import SetupWizard
    w = SetupWizard(account=account, **kw)
    w._start_mic = lambda: None                         # never the real microphone
    return w


def _until(qapp, done, timeout=5.0):
    end = time.monotonic() + timeout
    while not done() and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)
    assert done(), "timed out"


def test_the_pages_in_order(qapp, service):
    from ui.setup_wizard import PAGES
    w = _wizard(service.account)
    seen = [w.page()]
    for _ in PAGES[1:]:
        w._next()
        seen.append(w.page())
    assert seen == list(PAGES) == ["welcome", "name", "uses", "connect", "mic", "startup", "done"]


def test_the_name_and_what_the_pc_is_for_are_kept(qapp, service):
    from config import cfg
    w = _wizard(service.account)
    w._next()                                            # name
    w.name_edit.setText("  Amy  ")
    w._next()                                            # uses
    for chip in w.use_chips:
        chip.setChecked(chip.property("use") in ("Accounting & money", "Coding"))
    assert any(c.text() == "Accounting && money" for c in w.use_chips), "& shown, not a shortcut"
    w.next_btn.click()
    assert cfg.profile() == ("Amy", ["Coding", "Accounting & money"])


def test_signing_in_to_heytora_from_the_first_launch(qapp, service):
    from config import cfg
    w = _wizard(service.account)
    w._go("connect")
    w.email_edit.setText("amy@example.com")
    w.send_btn.click()
    _until(qapp, lambda: w.code_row.isVisibleTo(w))
    w.code_edit.setText("123456")
    w.signin_btn.click()
    _until(qapp, lambda: "Signed in" in w.connect_status.text())
    assert "Free plan - 25 talk messages and 25 agent messages a month." in w.connect_status.text()
    assert cfg.access_mode() == "account" and service.account.signed_in()


def test_clicking_the_link_in_the_email_signs_the_wizard_in(qapp, service):
    from config import cfg
    w = _wizard(service.account)
    w._go("connect")
    w.email_edit.setText("amy@example.com")
    w.send_btn.click()
    _until(qapp, lambda: w.code_row.isVisibleTo(w))
    assert "click the sign-in link" in w.connect_status.text()
    back_to = service.fake.redirect_to
    assert back_to and back_to.startswith("http://127.0.0.1:")
    page = httpx.get(back_to, params={"code": "link-code-1"}, trust_env=False, timeout=10)
    assert "signed in to HeyTora" in page.text
    _until(qapp, lambda: "Signed in" in w.connect_status.text())
    assert cfg.access_mode() == "account" and service.account.signed_in()
    assert not w.code_row.isVisibleTo(w)


def test_no_account_sends_them_to_the_website(qapp, service):
    w = _wizard(service.account)
    w._go("connect")
    w.email_edit.setText("nobody@example.com")
    w.send_btn.click()
    _until(qapp, lambda: "heytora.example" in w.connect_status.text())
    assert not w.code_row.isVisibleTo(w)


def test_or_their_own_gemini_key(qapp, service):
    from config import cfg
    checked = []

    def check(key):
        checked.append(key)
        return True, "Key works."
    w = _wizard(service.account, check_key=check)
    w._go("connect")
    w.key_edit.setText("their-own-key")
    w.key_btn.click()
    _until(qapp, lambda: "connected" in w.connect_status.text())
    assert checked == ["their-own-key"] and cfg.access_mode() == "own_key"
    assert cfg.google_api_key == "their-own-key" and not w.key_edit.text(), "not left on screen"


def test_a_bad_key_is_not_kept(qapp, service):
    from config import cfg
    w = _wizard(service.account, check_key=lambda _k: (False, "Google didn't accept that key."))
    w._go("connect")
    w.key_edit.setText("wrong")
    w.key_btn.click()
    _until(qapp, lambda: "didn't accept" in w.connect_status.text())
    assert cfg.google_api_key != "wrong"


def test_a_build_without_heytora_offers_only_the_key(qapp, monkeypatch):
    from account import service as svc
    monkeypatch.delenv("TORA_SUPABASE_URL", raising=False)
    svc.reset(svc.Account(transport=httpx.MockTransport(FakeHeyTora())))
    w = _wizard(svc.account())
    assert w.email_edit is None and w.key_edit is not None
    svc.reset(None)


def test_start_with_windows_and_finishing(qapp, service, monkeypatch):
    from ui import autostart, setup_wizard
    switched, finished = [], []
    monkeypatch.setattr(autostart, "set_enabled", lambda on: switched.append(on) or True)
    w = _wizard(service.account)
    w.completed.connect(lambda: finished.append(1))
    w._go("startup")
    assert w.startup_row.switch.isChecked(), "on unless they turn it off"
    w._next()
    assert switched == [True] and w.page() == "done"
    w._next()
    assert finished == [1] and setup_wizard.setup_already_ran()


def test_the_wizard_comes_back_while_tora_cannot_reach_gemini(qapp, service):
    from ui import setup_wizard
    setup_wizard.mark_setup_complete()
    shown = setup_wizard.maybe_show_setup_wizard()
    assert shown is not None, "signed out with no key: explain, don't sit silent"
    shown.close()


# -- Settings: the account card ------------------------------------------------------------------------------

class _Bridge:
    """The page's bridge, recorded; the rest from the real, empty one."""

    def __init__(self, state):
        from ui.app_bridge import AppBridge
        self._real = AppBridge()
        self.state = state
        self.done = []

    def account_state(self):
        return dict(self.state)

    def refresh_account(self):
        self.done.append("refresh")

    def account_sign_out(self):
        self.done.append("sign out")

    def open_website(self):
        self.done.append("website")

    def run_setup(self):
        self.done.append("setup")

    def set_access_mode(self, mode):
        self.done.append(("mode", mode))

    def __getattr__(self, name):
        return getattr(self._real, name)


def _texts(w):
    from PyQt6.QtWidgets import QLabel
    return [lbl.text() for lbl in w.findChildren(QLabel)]


def _state(**kw):
    base = {"configured": True, "mode": "account", "signed_in": True, "email": "amy@example.com",
            "website": "https://heytora.example", "own_key": False,
            "summary": {"plan": "free", "limits": {"talk": 25, "agent": 25, "dictation": 300, "calls": 1500},
                        "usage": {"talk": 7, "agent": 25, "calls": 40}}}
    base.update(kw)
    return base


def test_the_account_card_shows_the_plan_and_this_months_use(qapp):
    from PyQt6.QtWidgets import QPushButton
    from ui.app_pages import SettingsPage
    bridge = _Bridge(_state())
    page = SettingsPage(bridge)
    page.refresh()
    texts = _texts(page)
    assert "amy@example.com" in texts and "Free plan" in texts
    assert "Talk:  7 of 25 messages this month" in texts
    assert "Agent:  25 of 25 messages this month" in texts
    assert not any("calls" in t.lower() or "requests" in t.lower() for t in texts), "the backstop is hidden"
    buttons = {b.text(): b for b in page.findChildren(QPushButton)}
    buttons["Manage plan"].click()
    buttons["Sign out"].click()
    assert "website" in bridge.done and "sign out" in bridge.done and "refresh" in bridge.done


def test_unlimited_talk_on_pro(qapp):
    from ui.app_pages import SettingsPage
    page = SettingsPage(_Bridge(_state(summary={
        "plan": "pro", "limits": {"talk": None, "agent": 150, "dictation": None},
        "usage": {"talk": 210, "agent": 12}})))
    page.refresh()
    texts = _texts(page)
    assert "Pro plan" in texts and "Talk:  210 this month - unlimited" in texts


def test_signed_out_offers_to_sign_in(qapp):
    from PyQt6.QtWidgets import QPushButton
    from ui.app_pages import SettingsPage
    bridge = _Bridge(_state(signed_in=False, summary=None))
    page = SettingsPage(bridge)
    page.refresh()
    assert "Not signed in" in _texts(page)
    next(b for b in page.findChildren(QPushButton) if b.text() == "Sign in…").click()
    assert "setup" in bridge.done


def test_own_key_mode_says_so_and_offers_an_account(qapp):
    from PyQt6.QtWidgets import QPushButton
    from ui.app_pages import SettingsPage
    bridge = _Bridge(_state(mode="own_key", own_key=True))
    page = SettingsPage(bridge)
    page.refresh()
    assert "Using your own Gemini key" in _texts(page)
    next(b for b in page.findChildren(QPushButton) if b.text() == "Use a HeyTora account instead").click()
    assert ("mode", "account") in bridge.done and "setup" in bridge.done
