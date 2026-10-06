"""The first launch: signing in, getting to know the user, proving it works.

Five phases (docs/ui/DESIGN.md §7): sign in → hello → what you do →
privacy → mic test → [the app starts] → shortcut test → ask Tora about a sum
with a mistake → what was measured → her voice → her colour and the
switches → done. And Settings' account card: the plan and this month's
use. No real microphone, hotkey, network or profile.
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


def test_the_pages_in_order(qapp, service):
    from ui.setup_wizard import PAGES
    _sign_in(service)
    w = _wizard(service.account)
    seen = [w.page()]
    while w.page() != "done":
        w._next()
        seen.append(w.page())
    # Nothing was measured on the Learn screens (no app attached): there is
    # no celebration of nothing, so "payoff" is passed over.
    assert seen == [p for p in PAGES if p != "payoff"] == [
        "connect", "welcome", "work", "privacy", "mic", "ready",
        "shortcut", "try", "voice", "personal", "done"]


def test_the_name_and_what_the_pc_is_for_are_kept(qapp, service):
    from config import cfg
    w = _wizard(service.account)
    w._go("work")
    assert not w.next_btn.isEnabled(), "Continue waits for an answer"
    w.name_edit.setText("  Amy  ")
    for chip in w.use_chips:
        chip.setChecked(chip.property("use") in ("Accounting", "Design & video"))
    assert any(c.text() == "Design && video" for c in w.use_chips), "& shown, not a shortcut"
    assert w.next_btn.isEnabled()
    shown = [c.skill_id for c in w.skill_cards]
    assert shown[:2] == ["send-an-invoice", "file-receipts"], "the picture answers back"
    assert "social-post" in shown or len(shown) == 6
    w.next_btn.click()
    assert cfg.profile() == ("Amy", ["Accounting", "Design & video"])
    assert w.page() == "privacy"
    # Finishing the step turns on the skills for what they do, and the
    # other starter skills off (Tora > Skills turns them back on).
    from agents.skills import SkillLibrary
    on = {s.id for s in SkillLibrary().enabled()}
    assert {"send-an-invoice", "file-receipts", "social-post", "tidy-downloads"} <= on
    assert "meeting-notes" not in on and "fill-a-form" not in on


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


def test_a_new_email_signs_up_right_here(qapp, service):
    """The website has no sign-up: a new email gets its account by asking
    for a code, like anyone signing in (6 Oct 2026)."""
    w = _wizard(service.account)
    w._go("connect")
    w.email_edit.setText("new@example.com")
    w.send_btn.click()
    _until(qapp, lambda: w.code_row.isVisibleTo(w))
    assert "new@example.com" in service.fake.accounts


def test_with_sign_ups_off_it_sends_them_to_the_website(qapp, service):
    service.fake.signups = False
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
    _sign_in(service)                                    # a key is on top of an account, not instead
    w = _wizard(service.account, check_key=check)
    w._go("connect")
    w.key_edit.setText("their-own-key")
    w.key_btn.click()
    _until(qapp, lambda: "connected" in w.connect_status.text())
    assert checked == ["their-own-key"] and cfg.access_mode() == "own_key"
    assert cfg.google_api_key == "their-own-key" and not w.key_edit.text(), "not left on screen"


def test_a_bad_key_is_not_kept(qapp, service):
    from config import cfg
    _sign_in(service)
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
    w._go("personal")
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
                        "blocked": {"agent": "limit"}, "fair_use": 0.12}}
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
        assert "Free plan" in texts and "7 of 25 messages this month · 18 left" in texts
        assert "25 of 25 messages this month · all used" in texts
        assert any(t.startswith("Starts again on 1 Nov") for t in texts)
        assert "12% of this month" in texts
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
    assert "Pro plan" in texts and "210 this month - unlimited" in texts
    assert "Overall use" not in texts, "not known, not shown"


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
    assert "Couldn't update just now" in texts and "7 of 25 messages this month · 18 left" in texts


def test_a_spent_month_and_todays_fair_use_are_said_plainly(qapp):
    summary = _state()["summary"] | {"blocked": {"all": "budget", "talk": "day"}, "fair_use": 1.0}
    _page, texts = _card_texts(qapp, summary=summary)
    assert "This month's use is up" in texts
    assert "7 of 25 messages this month · 18 left · today's fair use reached" in texts
    assert "100% of this month" in texts


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


# -- the second half: the running app ----------------------------------------------------------------

class _Live:
    """What main.py hands the wizard once the app runs - recorded."""

    def __init__(self):
        from PyQt6.QtCore import QObject, pyqtSignal

        class Signals(QObject):
            state = pyqtSignal(object)
            point = pyqtSignal(float, float, str)
            draw = pyqtSignal(dict)
        self._s = Signals()
        self.state, self.point, self.draw = self._s.state, self._s.point, self._s.draw
        self.claimed = None
        self.wake = []
        self.voice = []
        self.route = []
        self.switched = []

    def claim_hotkey(self, down, up):
        self.claimed = (down, up)

    def release_hotkey(self):
        self.claimed = None

    def set_wake(self, on):
        self.wake.append(on)

    def set_voice(self, name):
        self.voice.append(name)

    def set_route(self, mode):
        self.route.append(mode)

    def set_switch(self, name, on):
        self.switched.append((name, on))


def _app_state(name):
    from ui.panel import AppState
    return AppState[name]


def test_the_app_starts_after_the_mic_test_and_the_window_carries_on(qapp, service):
    _sign_in(service)
    w = _wizard(service.account)
    started = []
    w.ready.connect(lambda: started.append(1))
    w._go("mic")
    w._next()
    assert w.page() == "ready" and started == [1], "the gate lets the app start here"
    w.attach(_Live())
    _until(qapp, lambda: w.page() == "shortcut")


def test_opened_from_a_running_app_there_is_no_waiting(qapp, service):
    _sign_in(service)
    w = _wizard(service.account)
    w.attach(_Live())
    w._go("mic")
    w._next()
    assert w.page() == "shortcut"


def test_holding_the_shortcut_lights_the_key_and_starts_nothing(qapp, service):
    _sign_in(service)
    w = _wizard(service.account)
    live = _Live()
    w.attach(live)
    w._go("shortcut")
    assert live.claimed, "the hotkey is borrowed: no conversation starts"
    assert not w.next_btn.isEnabled(), "proven by holding it"
    down, up = live.claimed
    down()
    _until(qapp, lambda: w.keycap.lit())
    assert w.next_btn.isEnabled()
    up()
    _until(qapp, lambda: not w.keycap.lit())
    w._next()
    assert w.page() == "try" and live.claimed is None, "given back"


def test_asking_tora_is_measured_and_celebrated(qapp, service):
    _sign_in(service)
    w = _wizard(service.account)
    live = _Live()
    w.attach(live)
    w._go("try")
    assert not w.next_btn.isEnabled()
    live.state.emit(_app_state("LISTENING"))
    live.state.emit(_app_state("THINKING"))
    live.point.emit(100.0, 200.0, "line 2")
    assert w._answered["try"][1] == "pointed" and "seconds" in w.try_status.text()
    assert w.next_btn.isEnabled()
    w._next()
    assert w.page() == "payoff"
    words = [lab.text() for lab in w.pages["payoff"].findChildren(type(w.try_status))]
    assert any("found the mistake" in t.lower() for t in words)


def test_a_drawing_on_the_sum_is_an_answer_too(qapp, service):
    """look_at_screen boxes the wrong line rather than pointing at it; the
    page said "try again" while the box was on screen."""
    _sign_in(service)
    w = _wizard(service.account)
    live = _Live()
    w.attach(live)
    w._go("try")
    live.state.emit(_app_state("THINKING"))
    live.draw.emit({"kind": "rect"})
    live.state.emit(_app_state("IDLE"))
    assert w._answered["try"][1] == "drew" and w.next_btn.isEnabled()
    assert "again" not in w.try_status.text()


def test_the_instructions_hide_while_the_shortcut_is_held(qapp, service):
    """Tora reads the whole screen: with "ask Tora: ..." on it she read the
    instruction out instead of looking at the sum."""
    _sign_in(service)
    w = _wizard(service.account)
    live = _Live()
    w.attach(live)
    w._go("try")
    words = w._learn_words["try"]
    live.state.emit(_app_state("LISTENING"))
    _until(qapp, lambda: words.fade.opacity() < 0.05)
    live.state.emit(_app_state("IDLE"))                 # nothing came of it
    _until(qapp, lambda: words.fade.opacity() > 0.95)
    assert "again" in w.try_status.text()


def test_learn_screens_can_be_skipped(qapp, service):
    _sign_in(service)
    w = _wizard(service.account)
    w.attach(_Live())
    w._go("try")
    assert w.skip_btn.isVisibleTo(w)
    w.skip_btn.click()
    assert w.page() == "voice", "nothing measured, nothing celebrated"


def test_closing_after_the_app_started_counts_as_done(qapp, service):
    from ui import setup_wizard
    _sign_in(service)
    w = _wizard(service.account)
    w.attach(_Live())
    w._go("try")
    w.close()
    assert setup_wizard.setup_already_ran()


# -- what the screens show back ------------------------------------------------------------------------

def test_the_welcome_carousel_plays_the_websites_five_scenes(qapp, service):
    """The website's "What it does" tour, ported (ui/tour_scenes.py): five
    scenes, each stepping through its beats and then handing on."""
    from ui import tour_scenes
    w = _wizard(service.account)
    w._go("welcome")
    c = w.carousel
    assert [n for n, _cls in tour_scenes.SCENES] == ["Ask", "Show me", "Teach me", "Do it", "Build it"]
    scene = c.scene()
    first = scene.clock.beat
    scene.clock.t0 -= 60                                 # as if the beat's time were up
    scene.advance()
    assert scene.clock.beat != first, "the beats move on"
    while not scene.done:
        scene.clock.t0 -= 60
        scene.advance()
    c._tick()
    assert c.index() == 1, "a finished scene hands on to the next"
    c.go(4)
    assert c.index() == 4 and c.scene().clock.beat == "listen", "each starts from its first frame"

def test_privacy_saves_the_real_switch(qapp, service):
    from config import cfg
    _sign_in(service)
    w = _wizard(service.account)
    live = _Live()
    w.attach(live)
    w._go("privacy")
    w.guard_card.setChecked(False)
    w._next()
    assert cfg.switch("privacy_guard") is False and live.switched == [("privacy_guard", False)]
    assert w.page() == "mic"


def test_her_voice_and_colour(qapp, service, monkeypatch):
    from config import cfg
    from ui import onboarding_parts as O
    monkeypatch.setenv("TORA_CURSOR_COLOUR", "blue")       # restored after the test
    played = []
    monkeypatch.setattr(O, "play_voice", lambda name: played.append(name) or True)
    _sign_in(service)
    w = _wizard(service.account)
    live = _Live()
    w.attach(live)
    w._go("voice")
    start = w.voice_carousel.voice()
    w.voice_carousel.go(1)
    assert w.voice_carousel.voice() != start and played[-1] == w.voice_carousel.voice(), \
        "moving on plays the next voice"
    chosen = w.voice_carousel.voice()
    w._next()
    assert live.voice == [chosen] and w.page() == "personal"


def test_the_last_switches_and_how_she_answers(qapp, service, monkeypatch):
    from ui import autostart
    monkeypatch.setattr(autostart, "set_enabled", lambda on: True)
    _sign_in(service)
    w = _wizard(service.account)
    live = _Live()
    w.attach(live)
    monkeypatch.setenv("TORA_CURSOR_COLOUR", "blue")       # restored after the test
    w._go("personal")
    w._pick_colour("violet")
    w.route_select.setCurrentIndex(1)
    w.wake_row.switch.setChecked(True)
    w._next()
    from config import cfg
    assert cfg.cursor_colour() == "violet"
    assert live.route == ["cascade"] and live.wake == [True]
    assert w.page() == "done"


def test_every_voice_has_a_bundled_hello(qapp):
    from audio.tts.gemini_tts import VOICES
    from ui import tokens
    missing = [v for v in VOICES if not (tokens.assets_dir() / "voices" / f"{v.lower()}.wav").exists()]
    assert not missing, f"record them: python assets/voices/make_voices.py ({missing})"


def test_picking_a_microphone_puts_the_button_back(qapp, service):
    w = _wizard(service.account)
    w._go("mic")
    w.mic_ids = [3, 7]
    w.mic_select.addItems(["Headset", "Desk mic"])
    w._open_stream = lambda device: None
    picked = []
    from config import cfg
    real = cfg.set_mic_device
    cfg.set_mic_device = picked.append
    try:
        w._change_mic.click()
        w._pick_mic(1)
    finally:
        cfg.set_mic_device = real
    assert picked == [7]
    assert not w.mic_select.isVisibleTo(w) and w._change_mic.isVisibleTo(w)
