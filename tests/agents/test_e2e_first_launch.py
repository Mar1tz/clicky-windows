"""Signing in (ui/setup_wizard.py), and Settings' account card: the plan
and this month's use. No real network or profile.

The rest of the old first launch - what you do, privacy, the mic and
shortcut tests, asking about a sum, her voice and colour - is the tour now
(tests/agents/test_e2e_onboarding_*.py); its pages went in revamp step 6b.
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
    return w


def _sign_in(service):
    """HeyTora needs an account: the steps after "connect" are behind a sign-in."""
    service.account.send_code("amy@example.com")
    service.account.verify("amy@example.com", "123456")


def _until(qapp, done, timeout=5.0):
    end = time.monotonic() + timeout
    while not done() and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)
    assert done(), "timed out"


def _opened(monkeypatch):
    from PyQt6.QtGui import QDesktopServices
    urls = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: urls.append(url.toString()) or True)
    return urls


def test_signing_in_via_the_browser_from_the_first_launch(qapp, service, monkeypatch):
    """One button opens the website's sign-in page; the browser comes back
    to this PC and the window signs itself in."""
    from urllib.parse import parse_qs, urlsplit
    from config import cfg
    urls = _opened(monkeypatch)
    w = _wizard(service.account)
    w.browser_btn.click()
    assert len(urls) == 1 and urls[0].startswith("https://heytora.example/login?")
    assert "Finish signing in in your browser" in w.connect_status.text()
    assert w.reopen_btn.isVisibleTo(w)
    query = {k: v[0] for k, v in parse_qs(urlsplit(urls[0]).query).items()}
    service.fake.challenge = query["challenge"]            # the website started the sign-in
    back = httpx.get(query["redirect_to"], params={"code": "link-code-1"}, trust_env=False, timeout=10)
    assert back.status_code == 302 and back.headers["location"] == "https://heytora.example/signed-in"
    _until(qapp, lambda: w.signed_in_box.isVisibleTo(w))
    assert cfg.access_mode() == "account" and service.account.signed_in()
    # Signed in, the way in falls away: who you are, and the plan (seen 6 Oct:
    # the button and the own key stayed, with "Signed in" in small print).
    assert w.signed_in_as.text() == "You're signed in as amy@example.com"
    assert "Free plan" in w.signed_in_plan.text()
    assert not w.browser_btn.isVisibleTo(w) and not w.reopen_btn.isVisibleTo(w)
    assert not w.key_card.isVisibleTo(w)
    assert w.next_btn.isEnabled()


def test_back_on_the_sign_in_screen_it_says_who_and_can_switch(qapp, service, monkeypatch):
    _opened(monkeypatch)
    from PyQt6.QtWidgets import QPushButton
    _sign_in(service)
    w = _wizard(service.account)
    assert w.signed_in_box.isVisibleTo(w) and not w.browser_btn.isVisibleTo(w)
    next(b for b in w.findChildren(QPushButton) if b.text() == "Use a different account").click()
    assert not service.account.signed_in()
    assert w.browser_btn.isVisibleTo(w) and not w.signed_in_box.isVisibleTo(w)
    assert not w.next_btn.isEnabled(), "signed out: sign in again first"


def test_there_is_no_create_an_account_link(qapp, service):
    """Seen 6 Oct: "No account yet? Create one" opened the website, which has
    no sign-up. Typing a new email is how an account is made."""
    from PyQt6.QtWidgets import QPushButton
    w = _wizard(service.account)
    assert not any("create" in b.text().lower() for b in w.findChildren(QPushButton))
    from PyQt6.QtWidgets import QLineEdit
    assert not any(e.placeholderText() == "you@example.com" for e in w.findChildren(QLineEdit)), \
        "no email box: the browser signs in"


def test_or_their_own_gemini_key(qapp, service):
    from config import cfg
    checked = []

    def check(key):
        checked.append(key)
        return True, "Key works."
    _sign_in(service)                                    # a key is on top of an account, not instead
    w = _wizard(service.account, check_key=check)
    w.key_edit.setText("their-own-key")
    w.key_btn.click()
    _until(qapp, lambda: "connected" in w.connect_status.text())
    assert checked == ["their-own-key"] and cfg.access_mode() == "own_key"
    assert cfg.google_api_key == "their-own-key" and not w.key_edit.text(), "not left on screen"


def test_a_bad_key_is_not_kept(qapp, service):
    from config import cfg
    _sign_in(service)
    w = _wizard(service.account, check_key=lambda _k: (False, "Google didn't accept that key."))
    w.key_edit.setText("wrong")
    w.key_btn.click()
    _until(qapp, lambda: "didn't accept" in w.connect_status.text())
    assert cfg.google_api_key != "wrong"


def test_a_build_without_heytora_offers_only_the_key(qapp, monkeypatch):
    from account import service as svc
    monkeypatch.delenv("TORA_SUPABASE_URL", raising=False)
    svc.reset(svc.Account(transport=httpx.MockTransport(FakeHeyTora())))
    w = _wizard(svc.account())
    assert w.browser_btn is None and w.key_edit is not None
    svc.reset(None)


def test_finish_waits_for_a_sign_in_and_leaves_the_tour_to_come(qapp, service):
    """The window signs in and nothing more: finishing it does not mark the
    first launch done - the tour does that, when it ends (main.py)."""
    from ui import setup_wizard
    finished = []
    w = _wizard(service.account)
    w.completed.connect(lambda: finished.append(1))
    assert w.page() == "connect" and w.next_btn.text() == "Continue"
    assert not w.next_btn.isVisibleTo(w), "not until signed in: the sign-in button is the one"
    w.next_btn.setEnabled(True)
    w.next_btn.click()
    assert finished == [] and "Sign in first" in w.connect_status.text()
    _sign_in(service)
    w._refresh_connect()
    w.next_btn.click()
    assert finished == [1] and not setup_wizard.setup_already_ran()


def test_the_window_is_the_tours_card(qapp, service):
    """6c: drawn as the tour's panel - no Windows frame, one card as tall as
    what it shows, the tour's title - not the old 1080 px wizard. No glyph
    the font lacks (the tick and the cross drew as three bars)."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QLabel
    w = _wizard(service.account)
    assert w.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert w.width() < 520 and w.height() < 520
    assert any(l.objectName() == "obTitle" and l.text() == "Sign in to HeyTora." for l in w.findChildren(QLabel))
    short = w.height()
    _sign_in(service)
    w._show_signed_in(True, "Free plan.")
    assert w.height() > short, "it grows with what it shows"
    assert not w.key_card.isVisibleTo(w) and w.key_link.isVisibleTo(w), "the own key waits behind a link"
    w.key_link.click()
    assert w.key_card.isVisibleTo(w)
    assert not any(ch in l.text() for l in w.findChildren(QLabel) for ch in "✓✕")
    w.close_btn.click()
    assert not w.isVisible()


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

    def refresh_account(self, force=False):
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
            "fresh": True, "busy": False, "error": "",
            "summary": {"plan": "free", "plan_name": "Free",
                        "limits": {"talk": 25, "agent": 25, "dictation": 300},
                        "usage": {"talk": 7, "agent": 25}, "period_end": "2026-11-01T00:00:00+00:00",
                        "blocked": {"agent": "limit"}, "fair_use": 0.12,
                        "credits": {"month": 200, "month_used": 24, "month_left": 176, "bought_left": 0,
                                    "can_buy": False}}}
    base.update(kw)
    return base


def test_the_account_card_shows_the_plan_and_this_months_use(qapp, monkeypatch):
    from PyQt6.QtWidgets import QPushButton
    from ui import app_pages
    from ui.settings_tabs import UsageTab as SettingsPage
    monkeypatch.setattr(app_pages, "confirm_sign_out", lambda _parent: True)
    bridge = _Bridge(_state())
    page = SettingsPage(bridge)
    page.show()                                    # on screen: the plan is asked for
    qapp.processEvents()
    try:
        texts = _texts(page)
        assert "Free plan" in texts and "7 questions this month" in texts
        assert "25 jobs this month · this month's limit reached" in texts
        assert any(t.startswith("This month's credits come back on 1 Nov") for t in texts)
        assert "176 of 200 credits left this month" in texts
        assert not any("calls" in t.lower() or "requests" in t.lower() or "$" in t for t in texts), \
            "the server's own numbers stay hidden"
        buttons = {b.text(): b for b in page.findChildren(QPushButton)}
        seen = []
        page.navigate.connect(seen.append)
        buttons["Change plan"].click()
        assert seen == ["settings/plans"] and "refresh" in bridge.done
    finally:
        page.close()
    from ui.settings_tabs import AccountTab
    account = AccountTab(bridge)
    assert "amy@example.com" in _texts(account), "who is signed in is on Account"
    next(b for b in account.findChildren(QPushButton) if b.text() == "Sign out").click()
    assert "sign out" in bridge.done


def test_sign_out_asks_first_because_it_closes_heytora(qapp, monkeypatch):
    from PyQt6.QtWidgets import QPushButton
    from ui import app_pages
    asked = []
    monkeypatch.setattr(app_pages, "confirm_sign_out", lambda _parent: asked.append(1) or False)
    bridge = _Bridge(_state())
    from ui.settings_tabs import AccountTab
    page = AccountTab(bridge)
    page.refresh()
    next(b for b in page.findChildren(QPushButton) if b.text() == "Sign out").click()
    assert asked == [1] and "sign out" not in bridge.done, "Cancel keeps them signed in"


def test_unlimited_talk_on_pro(qapp):
    from ui.settings_tabs import UsageTab as SettingsPage
    page = SettingsPage(_Bridge(_state(summary={
        "plan": "pro", "plan_name": "Pro", "limits": {"talk": None, "agent": 150, "dictation": None},
        "usage": {"talk": 210, "agent": 12}, "period_end": "2026-11-01T00:00:00+00:00"})))
    page.refresh()
    texts = _texts(page)
    assert "Pro plan" in texts and "210 questions this month" in texts
    assert "Credits" not in texts, "not known, not shown"


def _card_texts(qapp, **state):
    from ui.settings_tabs import UsageTab as SettingsPage
    page = SettingsPage(_Bridge(_state(**state)))
    page.refresh()
    return page, _texts(page)


def test_while_the_plan_is_read_it_says_so(qapp):
    _page, texts = _card_texts(qapp, summary=None, busy=True, fresh=False)
    assert "Reading your plan…" in texts


def test_a_plan_that_could_not_be_read_says_why_and_offers_another_try(qapp):
    from PyQt6.QtWidgets import QPushButton
    page, texts = _card_texts(qapp, summary=None, error="Could not reach HeyTora - check the internet.")
    assert "Couldn't read your plan" in texts and "Reading your plan…" not in texts
    assert "Could not reach HeyTora - check the internet." in texts
    next(b for b in page.findChildren(QPushButton) if b.text() == "Try again").click()
    assert "refresh" in page.bridge.done


def test_a_failed_update_keeps_the_last_known_use(qapp):
    _page, texts = _card_texts(qapp, error="Could not reach HeyTora.", fresh=False)
    assert "Couldn't update just now" in texts and "7 questions this month" in texts


def test_a_spent_month_and_todays_fair_use_are_said_plainly(qapp):
    summary = _state()["summary"] | {"blocked": {"all": "budget", "talk": "day"}, "fair_use": 1.0,
                                     "credits": {"month": 200, "month_used": 200, "month_left": 0,
                                                 "bought_left": 0, "can_buy": False}}
    _page, texts = _card_texts(qapp, summary=summary)
    assert "This month's credits are used up" in texts
    assert "They come back on 1 Nov. Upgrade in Settings → Plans & billing." in texts, "Free can't buy credits"
    assert "7 questions this month · today's fair use reached" in texts
    assert "0 of 200 credits left this month" in texts


def test_signed_out_offers_to_sign_in(qapp):
    from PyQt6.QtWidgets import QPushButton
    from ui.settings_tabs import UsageTab as SettingsPage
    bridge = _Bridge(_state(signed_in=False, summary=None))
    page = SettingsPage(bridge)
    page.refresh()
    assert "Not signed in" in _texts(page)
    next(b for b in page.findChildren(QPushButton) if b.text() == "Sign in…").click()
    assert "setup" in bridge.done


def test_own_key_mode_is_still_a_signed_in_account(qapp):
    from PyQt6.QtWidgets import QPushButton
    from ui.settings_tabs import AccountTab as SettingsPage
    bridge = _Bridge(_state(mode="own_key", own_key=True))
    page = SettingsPage(bridge)
    page.refresh()
    texts = _texts(page)
    assert "Using your own Gemini key" in texts and "amy@example.com" in texts
    buttons = {b.text(): b for b in page.findChildren(QPushButton)}
    assert "Sign out" in buttons
    buttons["Use my plan instead"].click()
    assert bridge.done[-1] == ("mode", "account") and "setup" not in bridge.done, "already signed in"


def test_own_key_mode_signed_out_offers_an_account(qapp):
    from PyQt6.QtWidgets import QPushButton
    from ui.settings_tabs import AccountTab as SettingsPage
    bridge = _Bridge(_state(mode="own_key", own_key=True, signed_in=False))
    page = SettingsPage(bridge)
    page.refresh()
    next(b for b in page.findChildren(QPushButton) if b.text() == "Use a HeyTora account instead").click()
    assert ("mode", "account") in bridge.done and "setup" in bridge.done


def test_every_voice_has_a_bundled_hello(qapp):
    from audio.tts.gemini_tts import VOICES
    from ui import tokens
    missing = [v for v in VOICES if not (tokens.assets_dir() / "voices" / f"{v.lower()}.wav").exists()]
    assert not missing, f"record them: python assets/voices/make_voices.py ({missing})"


