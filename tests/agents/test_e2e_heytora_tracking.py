"""Following the plan (2 Oct 2026): what the user sees of their HeyTora use.

Reported: close the app window, open it again, and Settings sat on
"Reading your plan…" for good. Two causes: a page built the first time it
was opened never asked for the plan, and an answer that arrived while the
window was closed was dropped. Under that, the card kept its own copy of the
plan, so the counts never moved while the app was used.

Now the account owns the plan: remembered from the last run (shown at
once), read again in the background whenever Settings comes on screen,
moved by every window the server opens, and announced to every screen. The
card says what is happening - reading, couldn't read (try again), the last
known use - never a silent wait. The tray carries a plan line and says 80%
and all-used once a month. Window ends are measured on the server's clock.
"""
from __future__ import annotations

import asyncio
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import httpx  # noqa: E402
import pytest  # noqa: E402

pytest.importorskip("PyQt6")

from tests.agents.test_e2e_first_launch import _texts, _until, qapp  # noqa: E402,F401 - fixture
from tests.agents.test_e2e_heytora_account import _signed_in, heytora  # noqa: E402,F401 - fixture


def _fresh_run(heytora):
    """The app started again: a new Account from what was kept on disk."""
    from account import service
    acc = service.Account(transport=httpx.MockTransport(heytora.fake))
    service.reset(acc)
    heytora.account = acc
    return acc


# -- the reported bug -------------------------------------------------------------------------------------------

def test_settings_does_not_stick_on_reading_your_plan(qapp, heytora):
    from ui.app_bridge import AppBridge
    from ui.app_window import ToraApp
    _signed_in(heytora)
    acc = heytora.account
    with acc._lock:                                   # nothing known: the first run after sign-in
        acc._summary, acc._summary_at = None, 0.0
    w = ToraApp(AppBridge())
    try:
        w.show()
        w.show_page("settings/account")                       # built for the first time
        _until(qapp, lambda: any(t.startswith("Free plan") for t in _texts(w)))
        assert "Talk:  3 of 25 messages this month · 22 left" in _texts(w)

        # Closed; the plan moves while nobody looks; opened again.
        w.hide()
        heytora.fake.usage = {"talk": 9}
        acc.summary(force=True)
        qapp.processEvents()
        w.show()
        _until(qapp, lambda: "Talk:  9 of 25 messages this month · 16 left" in _texts(w))
        assert "Reading your plan…" not in _texts(w)
    finally:
        w.close()


def test_the_card_moves_with_every_question(qapp, heytora):
    from ui.app_bridge import AppBridge
    from ui.app_window import ToraApp
    _signed_in(heytora)
    w = ToraApp(AppBridge())
    try:
        w.show()
        w.show_page("settings/account")
        _until(qapp, lambda: "Talk:  3 of 25 messages this month · 22 left" in _texts(w))
        heytora.account.new_talk()
        heytora.account.voice_pass("talk", "gemini-3.8-live")   # the server says: talk 1 this month
        _until(qapp, lambda: "Talk:  1 of 25 messages this month · 24 left · 1 today" in _texts(w))
    finally:
        w.close()


# -- the account owns the plan -----------------------------------------------------------------------------------

def test_the_plan_is_remembered_across_a_restart(heytora):
    _signed_in(heytora)
    acc = _fresh_run(heytora)
    st = acc.plan_state()
    assert st["summary"]["plan"] == "free" and not st["fresh"], "known at once, read again soon"
    assert acc.talk_metered(), "Free from the first question - not 'unknown'"
    heytora.fake.limits = {"talk": None, "dictation": None, "agent": 150}
    acc.summary(force=True)
    assert not _fresh_run(heytora).talk_metered(), "Pro remembered: the voice opens at start-up"


def test_a_failed_read_keeps_the_last_known_plan_and_says_why(heytora):
    _signed_in(heytora)
    acc = heytora.account

    def offline(_r):
        raise httpx.ConnectError("no internet")
    acc._transport = httpx.MockTransport(offline)
    heard = []
    acc.on_change(lambda: heard.append(acc.plan_state()))
    assert acc.refresh_soon(max_age=0)
    end = time.monotonic() + 5
    while (not heard or heard[-1]["busy"]) and time.monotonic() < end:
        time.sleep(0.02)
    last = heard[-1]
    assert last["summary"]["plan"] == "free" and "internet" in last["error"] and not last["busy"]
    assert heard[0]["busy"], "'reading' is announced too"


def test_refusals_show_on_the_card_at_once(heytora):
    from account.service import LimitReached
    _signed_in(heytora)
    heytora.fake.talk_refused = True
    with pytest.raises(LimitReached):
        heytora.account.voice_pass("talk", "gemini-3.8-live")
    assert heytora.account.plan_state()["summary"]["blocked"] == {"talk": "limit"}


def test_signing_out_forgets_the_plan_too(heytora):
    _signed_in(heytora)
    heytora.account.sign_out()
    assert heytora.account.plan_state()["summary"] is None
    assert _fresh_run(heytora).plan_state()["summary"] is None


# -- alerts, said once ------------------------------------------------------------------------------------------

def test_80_percent_and_all_used_are_said_once_a_month(heytora):
    _signed_in(heytora)
    acc = heytora.account
    assert acc.take_alerts() == [], "3 of 25: nothing to say"
    heytora.fake.usage = {"talk": 20}
    acc.summary(force=True)
    said = acc.take_alerts()
    assert said == ["5 talk messages left this month on the Free plan (20 of 25 used). More on 1 Nov."]
    assert acc.take_alerts() == [] and _fresh_run(heytora).take_alerts() == [], "once, even after a restart"
    acc = heytora.account
    heytora.fake.usage = {"talk": 25}
    acc.summary(force=True)
    assert acc.take_alerts() == ["You've used all 25 talk messages on the Free plan this month. "
                                 "They come back on 1 Nov - or upgrade on the HeyTora website."]


def test_the_month_spent_and_todays_fair_use_are_said(heytora):
    from account.service import alerts
    pro = {"plan": "pro", "plan_name": "Pro", "period_start": "2026-10-01T00:00:00+00:00",
           "period_end": "2026-11-01T00:00:00+00:00", "limits": {"talk": None, "agent": 150},
           "usage": {"talk": 300, "agent": 10}, "blocked": {"talk": "day", "all": "budget"},
           "fair_use": 1.0, "day_resets_at": 1790985600}
    words = [w for _k, w in alerts(pro)]
    assert "That's today's fair use of talk messages on the Pro plan - more tomorrow." in words
    assert any(w.startswith("This month's HeyTora use on the Pro plan is used up.") for w in words)


def test_the_tray_line(heytora):
    from account.service import short_status
    _signed_in(heytora)
    assert short_status(heytora.account.plan_state()["summary"]) == \
        "Free plan - 22 talk messages left, 25 agent messages left"
    pro = {"plan_name": "Pro", "limits": {"talk": None, "agent": 150}, "usage": {"agent": 149}}
    assert short_status(pro) == "Pro plan - 0 talk messages this month, 1 agent message left"
    mx = {"plan_name": "Max", "limits": {"talk": None, "agent": 1000},
          "usage": {"talk": 19}, "today": {"talk": 4}}
    assert short_status(mx) == "Max plan - 19 talk messages this month (4 today), 1000 agent messages left"


def test_the_tray_follows_the_plan(qapp, heytora):
    from ui.tray import TrayManager
    _signed_in(heytora)
    tray = TrayManager()
    try:
        heytora.account.on_change(tray.plan_changed.emit)
        heytora.fake.usage = {"talk": 21}
        heytora.account.summary(force=True)
        _until(qapp, lambda: tray._plan_line == "Free plan - 4 talk messages left, 25 agent messages left")
    finally:
        tray.hide_icon()


# -- the server's clock -----------------------------------------------------------------------------------------

def test_a_pc_with_a_wrong_clock_still_keeps_its_window(heytora):
    """This PC runs 20 minutes ahead of the server: a 10-minute window would
    look over before it began, and every call would ask for a new one."""
    _signed_in(heytora)
    heytora.fake.clock_offset = -1200
    acc = heytora.account
    first = acc.ticket("talk")
    assert acc.ticket("talk") == first and heytora.fake.opened["talk"] == 1


def test_a_refused_ticket_is_asked_for_again_once(heytora, monkeypatch):
    from ai import gemini
    _signed_in(heytora)
    acc = heytora.account
    acc.ticket("talk")
    window, _ends = heytora.fake.windows["talk"]
    heytora.fake.windows["talk"] = (window + 100, 0.0)   # over on the server, not yet here
    seen = []

    def routed(request):
        seen.append(request.headers.get("x-heytora-ticket", ""))
        return heytora.fake(request)
    client = httpx.AsyncClient(transport=httpx.MockTransport(routed))
    monkeypatch.setattr(gemini, "client", lambda: client)
    r = asyncio.run(gemini._post("models/gemini-3.8-flash:generateContent", {"contents": []}, 10))
    assert r.status_code == 200 and len(seen) == 2 and seen[0] != seen[1]
    assert heytora.fake.opened["talk"] == 2


# -- Home: the plan at a glance, live ---------------------------------------------------------------------------

def _home(qapp, state):
    from tests.agents.test_e2e_first_launch import _Bridge, _state
    from ui.app_pages import HomePage

    class HomeBridge(_Bridge):
        """_Bridge keeps the account state in .state - which Home calls."""
        def __init__(self, account):
            super().__init__(account)
            self.account = self.__dict__.pop("state")

        def account_state(self):
            return dict(self.account)
    bridge = HomeBridge(_state(**state))
    page = HomePage(bridge)
    page.refresh()
    return page, _texts(page)


def test_home_shows_the_plan_moving_with_use(qapp):
    max_plan = {"plan": "max", "plan_name": "Max", "limits": {"talk": None, "agent": 1000, "dictation": None},
                "usage": {"talk": 19, "agent": 3, "dictation": 2}, "today": {"talk": 4},
                "period_end": "2026-11-01T00:00:00+00:00", "fair_use": 0.05}
    _page, texts = _home(qapp, {"summary": max_plan})
    assert "Your Max plan" in texts and "Starts again on 1 Nov" in texts
    assert "5% of this month used" in texts


def test_home_has_no_plan_card_on_an_own_key(qapp):
    _page, texts = _home(qapp, {"mode": "own_key"})
    assert not any(t.startswith("Your ") and t.endswith(" plan") for t in texts)


def test_settings_says_todays_use(qapp):
    from tests.agents.test_e2e_first_launch import _Bridge, _state
    from ui.settings_tabs import AccountTab as SettingsPage
    summary = _state()["summary"] | {"today": {"talk": 2}}
    page = SettingsPage(_Bridge(_state(summary=summary)))
    page.refresh()
    assert "Talk:  7 of 25 messages this month · 18 left · 2 today" in _texts(page)


def test_talking_moves_settings_in_place(qapp, heytora):
    """Reported 2 Oct: "I'm on the Settings page, I talk, the values don't
    change." Every time the user talks is a talk message, counted when Tora
    hears it; the plan card is swapped in place - the rest of Settings (a
    switch, something half-typed, the scroll) is left alone."""
    from PyQt6.QtWidgets import QWidget
    from ui.app_bridge import AppBridge
    from ui.app_window import ToraApp
    _signed_in(heytora)
    w = ToraApp(AppBridge())
    try:
        w.show()
        w.show_page("settings/account")
        _until(qapp, lambda: "Talk:  3 of 25 messages this month · 22 left" in _texts(w))
        page = w._settings.tab("account")
        untouched = page.body.widget().findChildren(QWidget)[0]
        for n in (1, 2, 3):                           # three questions, one after another
            heytora.account.new_talk()
            heytora.account.ticket("talk")            # what _count_question does when Tora hears them
            _until(qapp, lambda n=n: f"Talk:  {n} of 25 messages this month · {25 - n} left · {n} today"
                   in _texts(w))
        assert untouched in page.body.widget().findChildren(QWidget), "the page was not rebuilt"
        assert heytora.fake.opened["talk"] == 3
    finally:
        w.close()
