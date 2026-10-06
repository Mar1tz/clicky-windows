"""Paid plans through Paddle (6 Oct 2026).

Paddle is the merchant of record; HeyTora's server asks it for checkouts,
plan changes, the billing portal and cancellations, and Paddle's signed
webhooks are the only thing that changes a plan (migration 0008). These
tests hold: the rules (Node, supabase/tests/billing.test.ts); the server's
promises, read from its code - who pays is the sign-in's, a webhook is
checked before anything, only the server can apply one; and what the
Plans & billing tab does with each answer.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FN = ROOT / "supabase/functions"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node runs the rules' own tests")
def test_the_billing_rules_pass_under_node():
    out = subprocess.run(["node", "--test", "supabase/tests/billing.test.ts"], cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    assert out.returncode == 0, out.stdout[-3000:] + out.stderr[-2000:]


# ── what the server promises ─────────────────────────────────────────────────

def test_who_pays_is_the_sign_ins_never_the_apps():
    fn = (FN / "heytora/index.ts").read_text(encoding="utf-8")
    route = fn[fn.index('path === "checkout"'):fn.index('path === "billing"')]
    assert "custom_data: { user_id: user.id }" in route
    assert "body?.user" not in route and "body.user" not in route


def test_a_webhook_is_checked_before_anything_is_read_or_applied():
    hook = (FN / "paddle/index.ts").read_text(encoding="utf-8")
    assert hook.index("verifySignature(") < hook.index("JSON.parse(raw)") < hook.index('rpc("heytora_billing_event"')
    assert "await req.text()" in hook, "the signature is over the raw bytes"
    assert "--no-verify-jwt" in hook, "Paddle has no Supabase sign-in"


def test_only_the_server_can_apply_a_payment():
    sql = (ROOT / "supabase/migrations/0008_heytora_billing.sql").read_text(encoding="utf-8")
    grants = re.findall(r"grant execute on function public\.heytora_billing_event\(.*?\)\s+to (\w+);", sql, re.S)
    assert grants == ["service_role"]
    assert re.search(r"revoke all on function public\.heytora_billing_event\(.*?\)\s+from public, anon, "
                     r"authenticated;", sql, re.S)
    assert "alter table public.billing_events enable row level security" in sql
    assert "on conflict (id) do nothing" in sql, "Paddle retries: each event once"
    assert "p_occurred_at < v_last" in sql, "an older event changes nothing"


def test_deleting_a_paying_account_cancels_at_paddle_first():
    fn = (FN / "heytora/index.ts").read_text(encoding="utf-8")
    route = fn[fn.index('path === "account" && req.method === "DELETE"'):]
    route = route[:route.index("return json(200, { deleted: true });")]
    assert route.index('/cancel`') < route.index("deleteUser(user.id)")
    assert 'effective_from: "immediately"' in route


def test_the_prices_are_the_ones_made_in_paddle():
    billing = (FN / "_shared/billing.ts").read_text(encoding="utf-8")
    sandbox = billing[billing.index("SANDBOX_PRICES"):billing.index("LIVE_PRICES")]
    live = billing[billing.index("LIVE_PRICES"):]
    assert 'pro: "pri_01m49bc41gge68ge4hf8mgbd6p"' in sandbox and 'max: "pri_01m49bddnrepm7dn5xvrzdrbv0"' in sandbox
    assert 'pro: "pri_01m4971kmb5pw7cw739j3rktr1"' in live and 'max: "pri_01m496kctf4c4jxgzfzncrktny"' in live


# ── the Plans & billing tab ─────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


class _Bridge:
    def __init__(self, summary=None, signed_in=True, answer=None, portal=None):
        self.summary = summary if summary is not None else {"plan": "free", "plan_name": "Free"}
        self.signed_in, self.answer, self.portal = signed_in, answer or {}, portal or {}
        self.done = []

    def account_state(self):
        return {"signed_in": self.signed_in, "mode": "account", "summary": self.summary}

    def choose_plan(self, plan):
        self.done.append(("choose", plan))
        return self.answer

    def billing_page(self):
        self.done.append("portal")
        return self.portal

    def watch_for_plan(self, plan):
        self.done.append(("watch", plan))

    def refresh_account(self, force=False):
        self.done.append("refresh")

    def run_setup(self):
        self.done.append("setup")


def _texts(w):
    from PyQt6.QtWidgets import QLabel
    return [l.text() for l in w.findChildren(QLabel)]


def _button(w, text):
    from PyQt6.QtWidgets import QPushButton
    return next(b for b in w.findChildren(QPushButton) if b.text() == text)


def _wait(qapp, cond):
    import time
    end = time.time() + 5
    while not cond() and time.time() < end:
        qapp.processEvents()
    assert cond()


@pytest.fixture
def opened(monkeypatch):
    from PyQt6.QtGui import QDesktopServices
    urls = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: urls.append(url.toString()) or True)
    return urls


def test_choosing_pro_opens_the_checkout_and_watches_for_the_plan(qapp, opened):
    from ui.settings_tabs import PlansTab
    br = _Bridge(answer={"url": "https://heytora.example/pay?_ptxn=txn_1"})
    tab = PlansTab(br)
    _button(tab, "Choose Pro").click()
    _wait(qapp, lambda: ("watch", "pro") in br.done)
    assert ("choose", "pro") in br.done and opened == ["https://heytora.example/pay?_ptxn=txn_1"]
    assert any("Finish paying in your browser" in t for t in _texts(tab))


def test_already_paying_a_change_is_made_and_said(qapp, opened):
    from ui.settings_tabs import PlansTab
    br = _Bridge(summary={"plan": "pro", "plan_name": "Pro", "plan_status": "active", "billing": True,
                          "plan_renews_at": "2026-11-06T10:00:00Z"},
                 answer={"changed": True, "plan": "max"})
    tab = PlansTab(br)
    assert not _button(tab, "Your plan").isEnabled()
    assert "Pro, renews on 6 Nov" in _texts(tab)
    _button(tab, "Choose Max").click()
    _wait(qapp, lambda: ("watch", "max") in br.done)
    assert opened == [] and any("you're on Max" in t for t in _texts(tab))


def test_an_error_is_said_and_nothing_opens(qapp, opened):
    from ui.settings_tabs import PlansTab
    br = _Bridge(answer={"error": "Paid plans aren't open yet."})
    tab = PlansTab(br)
    _button(tab, "Choose Max").click()
    _wait(qapp, lambda: "Paid plans aren't open yet." in _texts(tab))
    assert opened == [] and _button(tab, "Choose Max").isEnabled()


def test_going_back_to_free_is_cancelling_on_the_billing_page(qapp, opened):
    from ui.settings_tabs import PlansTab
    br = _Bridge(summary={"plan": "max", "plan_name": "Max", "plan_status": "active", "billing": True},
                 portal={"url": "https://customer-portal.paddle.com/cpl_1"})
    tab = PlansTab(br)
    _button(tab, "Choose Free").click()
    _wait(qapp, lambda: opened == ["https://customer-portal.paddle.com/cpl_1"])
    assert ("choose", "free") not in br.done


def test_a_cancelled_plan_says_until_when_and_can_be_chosen_again(qapp):
    from ui.settings_tabs import PlansTab
    tab = PlansTab(_Bridge(summary={"plan": "pro", "plan_name": "Pro", "plan_status": "active",
                                    "billing": True, "plan_cancel_at": "2026-11-06T10:00:00Z"}))
    assert "Pro until 6 Nov" in _texts(tab)
    assert _button(tab, "Choose Pro").isEnabled(), "choosing it again keeps it"


def test_a_failed_payment_says_to_update_the_card(qapp):
    from ui.settings_tabs import PlansTab
    tab = PlansTab(_Bridge(summary={"plan": "pro", "plan_name": "Pro", "plan_status": "past_due",
                                    "billing": True}))
    assert "Pro: your last payment didn't go through" in _texts(tab)
    _button(tab, "Manage billing")


def test_signed_out_a_plan_starts_with_signing_in(qapp, opened):
    from ui.settings_tabs import PlansTab
    br = _Bridge(signed_in=False)
    tab = PlansTab(br)
    _button(tab, "Choose Pro").click()
    assert br.done == ["setup"] and opened == []


def test_the_plan_is_watched_until_paddle_has_told_the_server(qapp, monkeypatch):
    from ui.app_bridge import AppBridge
    br = AppBridge()
    states = iter([{"plan": "free"}, {"plan": "pro", "plan_status": "active"}])
    refreshed = []
    monkeypatch.setattr(br, "account_state", lambda: {"summary": next(states)})
    monkeypatch.setattr(br, "refresh_account", lambda force=False: refreshed.append(force))
    br.watch_for_plan("pro")
    assert br._plan_timer.isActive()
    br._check_plan()
    assert refreshed == [True] and br._plan_timer.isActive(), "not yet: read again"
    br._check_plan()
    assert not br._plan_timer.isActive(), "Pro: stop watching"
