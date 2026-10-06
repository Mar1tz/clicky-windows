"""Referrals: give 14 days of Pro, get a free month (docs/REFERRALS.md).

A friend who signs up through a referrer's code gets 14 days of Pro; when
they've asked Tora 15 questions the referrer earns a free month, used on
their next Paddle bill. These tests hold:

  * the rules the database keeps (migration 0010) - read from its SQL, since
    there is no Postgres here: new accounts only, once; not your own, not a
    loop; one trial per PC and never the referrer's PC; no throwaway email;
    15 questions within the 14 days; 12 months a year, flagged above 3 a
    month; the trial is Pro's limits at Pro's rate, and nobody else's plan;
  * the pure rules (Node, supabase/tests/referrals.test.ts);
  * the app: its PC's one-way ID, claiming the invite the website noted for
    a sign-in (proved with the sign-in's verifier), the Refer window;
  * the webhook putting a waiting month on a paying referrer's next bill.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from tests.agents.test_e2e_heytora_account import SUPABASE, FakeHeyTora  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
SQL = (ROOT / "supabase/migrations/0010_heytora_referrals.sql").read_text(encoding="utf-8")


def _fn(name: str) -> str:
    start = SQL.index(f"function public.{name}(")
    return SQL[start:SQL.index("$fn$;", start)]


# ── the database's rules ─────────────────────────────────────────────────────

def test_the_friends_trial_is_pro_at_pros_rate_and_nobody_elses_plan():
    assert "'pro_trial', 'Pro trial', p.limits || '{\"budget_usd\": 6}'::jsonb" in SQL, \
        "Pro's allowances, and Pro's $12-a-month fair use for 14 days"
    assert "from public.plans p where p.key = 'pro'" in SQL and "is_public = false" in SQL
    plan = _fn("_heytora_plan")
    assert "if v_plan = 'free' and v_trial is not null and v_trial > now() then" in plan, \
        "a paying user's plan is never replaced by a trial"
    period = _fn("_heytora_period")
    assert period.index("v_prof.plan_period_start <= now()") < period.index("trial_started_at"), \
        "a paid plan's period first; the trial is its own 14 days"


def test_a_code_is_refused_in_words_for_each_rule():
    apply = _fn("heytora_referral_apply")
    for reason, words in (("own", "your own code"), ("already", "already used a code"),
                          ("too_old", "first 7 days"), ("loop", "someone you invited"),
                          ("email", "throwaway email"), ("paying", "already on a paid plan"),
                          ("same_pc", "same PC"), ("trial_used", "already had a free trial"),
                          ("unknown", "isn''t valid")):
        assert f"'reason', '{reason}'" in apply, reason
        assert words in apply, words
    assert "interval '7 days'" in apply and "interval '14 days'" in apply
    assert "insert into public.trial_devices" in apply, "one trial per PC"


def test_fifteen_questions_within_the_fourteen_days_earn_a_month_with_caps():
    progress = _fn("_heytora_referral_progress")
    assert "e.kind = 'talk'" in progress and "interval '14 days'" in progress
    assert "if v_asked < 15 then" in progress
    assert "interval '365 days') >= 12" in progress, "12 months a year"
    assert "> 3 then" in progress and "set flagged = true" in progress, "flagged, not refused"
    trigger = _fn("_heytora_referral_on_usage")
    assert "exception when others" in trigger, "a referral never stops someone asking"


def test_who_may_call_what():
    for public_fn in ("heytora_referral_lookup(text)", "heytora_referral_intent(text, text)",
                      "heytora_reward_next(uuid)", "heytora_reward_used(bigint, text)"):
        assert f"grant execute on function public.{public_fn}" in SQL and \
            re.search(rf"grant execute on function public\.{re.escape(public_fn)}\s+to service_role;", SQL), public_fn
    for mine in ("heytora_referral_apply(text, text, text)", "heytora_referral_status(text, text)",
                 "heytora_referral_refresh()"):
        assert re.search(rf"grant execute on function public\.{re.escape(mine)}\s+to authenticated;", SQL), mine
    for table in ("referrals", "reward_months", "trial_devices", "referral_intents"):
        assert f"alter table public.{table}" in SQL and "enable row level security" in SQL
    lookup = _fn("heytora_referral_lookup")
    assert "email" not in lookup, "the website sees a first name, never an email"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node runs the rules' own tests")
def test_the_pure_rules_pass_under_node():
    out = subprocess.run(["node", "--test", "supabase/tests/referrals.test.ts"], cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    assert out.returncode == 0, out.stdout[-3000:] + out.stderr[-2000:]


def test_the_server_proves_a_claim_with_the_verifier():
    fn = (ROOT / "supabase/functions/heytora/index.ts").read_text(encoding="utf-8")
    claim = fn[fn.index('path === "referrals/claim"'):]
    assert "p_challenge: await challengeOf(verifier)" in claim
    assert fn.index('if (path === "referrals/lookup" || path === "referrals/intent")') \
        < fn.index("await db.auth.getUser(jwt)"), "the website's two calls come before the sign-in check"


def test_a_waiting_month_goes_on_a_renewing_bill_only():
    hook = (ROOT / "supabase/functions/paddle/index.ts").read_text(encoding="utf-8")
    reward = hook[hook.index("async function applyRewardMonth"):]
    assert 'sub.status !== "active" || sub.discount || sub.scheduled_change' in reward, \
        "not when a discount is lined up, or the plan won't renew"
    assert reward.index('effective_from: "next_billing_period"') < reward.index("heytora_reward_used")
    assert 'recur: false' in hook and 'amount: "100"' in hook


# ── the app ──────────────────────────────────────────────────────────────────

def test_this_pc_is_a_one_way_id():
    from account import device
    device._cached = None
    first = device.device_id()
    assert re.fullmatch(r"[0-9a-f]{64}", first) and device.device_id() == first
    raw = device._machine_guid()
    if raw:
        assert raw.lower() not in first, "never the machine ID itself"


@pytest.fixture
def heytora(monkeypatch):
    from types import SimpleNamespace
    from account import service
    monkeypatch.setenv("TORA_SUPABASE_URL", SUPABASE)
    monkeypatch.setenv("TORA_SUPABASE_ANON_KEY", "anon-public-key")
    monkeypatch.setenv("TORA_WEBSITE_URL", "https://heytora.example")
    monkeypatch.setenv("TORA_ACCESS", "account")
    fake = FakeHeyTora()
    acc = service.Account(transport=httpx.MockTransport(fake))
    service.reset(acc)
    yield SimpleNamespace(fake=fake, account=acc)
    service.reset(None)


def _browser_sign_in(h, *, noted: str = ""):
    """The app opens the website; the website notes the invite (if any) for
    this sign-in's challenge; the browser comes back with the code."""
    from urllib.parse import parse_qs, urlsplit
    url = h.account.browser_sign_in("http://127.0.0.1:5123/heytora/signed-in")
    challenge = parse_qs(urlsplit(url).query)["challenge"][0]
    h.fake.challenge = challenge
    if noted:
        h.fake.noted[challenge] = noted
    h.account.verify_link("link-code-1")


def test_the_invite_noted_for_a_sign_in_is_claimed_by_the_app_that_started_it(heytora):
    from account.device import device_id
    _browser_sign_in(heytora, noted="DYLANH4821")
    out = heytora.account.referral_claim()
    assert out["ok"] and out["referrer_name"] == "Dylan"
    assert heytora.fake.applied == [{"code": "DYLANH4821", "device": device_id()}]
    assert heytora.account.referral_claim() == {"ok": False, "reason": "none"}, "once"


def test_no_invite_no_claim(heytora):
    _browser_sign_in(heytora)
    assert heytora.account.referral_claim()["ok"] is False
    assert heytora.fake.applied == []


def test_the_refer_window_tells_the_server_this_pc_and_your_first_name(heytora, monkeypatch):
    from account.device import device_id
    from config import cfg
    from ui.app_bridge import AppBridge
    _browser_sign_in(heytora)
    monkeypatch.setattr(cfg, "profile", lambda: ("Amy", []))
    out = AppBridge().referral_status()
    assert out["code"] == "AMY1234" and out["link"] == "https://heytora.example/r/AMY1234"
    assert heytora.fake.referral_query == {"device": device_id(), "name": "Amy"}
    assert AppBridge().refresh_referral_code() == {"code": "AMY9876"}
    assert AppBridge().apply_referral("NOPE0000")["message"].startswith("That code isn't valid")


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def test_signing_in_through_an_invite_says_who_it_came_from(qapp, heytora, monkeypatch):
    from urllib.parse import parse_qs, urlsplit
    from PyQt6.QtGui import QDesktopServices
    from ui.setup_wizard import SetupWizard
    urls = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: urls.append(url.toString()) or True)
    w = SetupWizard(account=heytora.account)
    w._start_mic = lambda: None
    w._go("connect")
    w.browser_btn.click()
    query = {k: v[0] for k, v in parse_qs(urlsplit(urls[0]).query).items()}
    heytora.fake.challenge = query["challenge"]
    heytora.fake.noted[query["challenge"]] = "DYLANH4821"
    httpx.get(query["redirect_to"], params={"code": "link-code-1"}, trust_env=False, timeout=10)
    end = time.time() + 5
    while not w.signed_in_box.isVisibleTo(w) and time.time() < end:
        qapp.processEvents()
    assert "Dylan gave you 14 days of Pro" in w.signed_in_plan.text()
    assert "Dylan gets a free month too" in w.signed_in_plan.text()


class _Bridge:
    def __init__(self, state):
        self.state, self.done = state, []

    def referral_status(self):
        self.done.append("status")
        return self.state

    def refresh_referral_code(self):
        self.done.append("refresh")
        return {"code": "NEW12345"}

    def apply_referral(self, code):
        self.done.append(("apply", code))
        return {"ok": False, "message": "That's your own code - share it with a friend instead."}


def _state(**kw):
    base = {"code": "DYLANH4821", "link": "https://heytora.example/r/DYLANH4821", "new_account": True,
            "referred_by": None, "trial_until": None, "months_waiting": 0, "months_used": 0, "friends": []}
    base.update(kw)
    return base


def _open(qapp, bridge):
    from PyQt6.QtWidgets import QWidget
    from ui.referral_window import ReferralWindow
    host = QWidget()
    host.resize(1000, 900)
    win = ReferralWindow(host, bridge)
    end = time.time() + 5
    while win._state is None and time.time() < end:
        qapp.processEvents()
    return host, win


def _texts(w):
    from PyQt6.QtWidgets import QLabel
    return [lbl.text() for lbl in w.findChildren(QLabel)]


def test_the_refer_tab_has_your_link_and_copies_it(qapp):
    from PyQt6.QtWidgets import QApplication
    host, win = _open(qapp, _Bridge(_state(months_waiting=2)))
    assert win.link_edit.text() == "https://heytora.example/r/DYLANH4821"
    win.copy_btn.click()
    assert QApplication.clipboard().text() == "https://heytora.example/r/DYLANH4821"
    assert "You have 2 free months waiting." in _texts(win)
    assert "Subscribe in the app" in win.foot_note.text()


def test_past_referrals_say_where_each_friend_is(qapp):
    friends = [{"name": "Sam", "status": "joined", "asked": 9, "earned": False},
               {"name": "Lee", "status": "qualified", "asked": 15, "earned": True},
               {"name": "Kim", "status": "qualified", "asked": 15, "earned": False}]
    host, win = _open(qapp, _Bridge(_state(friends=friends)))
    win._show("past")
    texts = _texts(win)
    assert "Joined · 9 of 15 questions" in texts and "Earned you a free month" in texts
    assert "Counted - you've reached this year's 12 free months" in texts
    host2, empty = _open(qapp, _Bridge(_state()))
    empty._show("past")
    assert "You haven't referred anyone yet" in _texts(empty)


def test_apply_says_why_a_code_cant_be_used_and_old_accounts_cant(qapp):
    bridge = _Bridge(_state())
    host, win = _open(qapp, bridge)
    win._show("apply")
    win.code_edit.setText("dylanh4821")
    win.apply_btn.click()
    end = time.time() + 5
    while "own code" not in win.apply_note.text() and time.time() < end:
        qapp.processEvents()
    assert ("apply", "dylanh4821") in bridge.done and "own code" in win.apply_note.text()
    host2, old = _open(qapp, _Bridge(_state(new_account=False)))
    old._show("apply")
    assert not old.code_edit.isEnabled() and "first 7 days" in old.apply_note.text()


def test_a_running_trial_shows_in_plans_and_billing(qapp):
    from ui.settings_tabs import PlansTab

    class B:
        def account_state(self):
            return {"signed_in": True, "mode": "account",
                    "summary": {"plan": "pro_trial", "plan_name": "Pro trial", "plan_status": "active",
                                "trial_until": "2026-10-21T10:00:00Z"}}
    tab = PlansTab(B())
    from PyQt6.QtWidgets import QLabel
    assert "Pro trial until 21 Oct" in [l.text() for l in tab.findChildren(QLabel)]
