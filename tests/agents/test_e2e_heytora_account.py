"""HeyTora accounts (1 Oct 2026): the business Gemini key stays on the server.

Users sign in to the account they made on the website (an emailed code);
Gemini then runs through HeyTora's server on their plan - Free, Pro or Max -
or, if they prefer, on their own key straight to Google. These tests run
against a fake Supabase (auth + the `heytora` function); nothing touches the
network or the user's profile.
"""
from __future__ import annotations

import asyncio
import base64
import fnmatch
import hashlib
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
SUPABASE = "https://proj.supabase.co"
PROXY = f"{SUPABASE}/functions/v1/heytora/v1beta"


class FakeHeyTora:
    """Supabase Auth and the heytora function, as far as the app sees them."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict, dict]] = []
        self.accounts = {"amy@example.com"}
        self.code = "123456"
        self.issued = 0
        self.refresh_ok = True
        self.expires_in = 3600
        self.plan = "free"
        self.limits = {"talk": 25, "dictation": 300, "agent": 25, "calls": 1500}
        self.usage = {"talk": 3}
        self.talk_refused = False
        self.link_code = "link-code-1"          # what the email's link brings back
        self.challenge = None                   # PKCE, from the last sign-in email
        self.redirect_to = None
        self.ended = False                      # the server no longer accepts the sign-in
        # Windows (migration 0004): a talk / agent message is a window the
        # server opens; an open one is handed back at no charge.
        self.window_minutes = {"talk": 10, "agent": 30}
        self.windows: dict[str, tuple[int, float]] = {}
        self.opened = {"talk": 0, "agent": 0}
        self.window_seq = 0
        self.pass_refusal = ""                  # "passes" / "window": refuse a reused window once
        self.clock_offset = 0.0                 # the server's clock minus this PC's
        self.standby = 0                        # standby passes (0007): the voice waiting, uncounted
        self.paid = False                       # a paid plan still running: no deleting
        self.signups = True                     # Supabase's "allow new users to sign up"
        self.paying = ""                        # the plan a Paddle subscription is on, if any
        self.billing_off = False                # no PADDLE_API_KEY on the server yet
        # Referrals (migration 0010): the website's notes, and what was asked
        self.noted: dict[str, str] = {}         # PKCE challenge -> code
        self.applied: list[dict] = []           # {code, device} the server was asked to use
        self.referral = {"code": "AMY1234", "friends": [], "months_waiting": 0, "months_used": 0,
                         "new_account": True, "referred_by": None, "trial_until": None}
        self.deleted = False

    def window(self, kind: str, new: bool) -> tuple[int, float, bool]:
        held = self.windows.get(kind)
        if held and not new and held[1] > time.time() + self.clock_offset:
            return held[0], held[1], True
        self.window_seq += 1
        self.opened[kind] += 1
        held = (self.window_seq, time.time() + self.clock_offset + 60 * self.window_minutes[kind])
        self.windows[kind] = held
        return held[0], held[1], False

    @staticmethod
    def ticket(kind: str, window: int, ends: float) -> str:
        return f"{kind}.{window}.{int(ends)}.sig"

    def valid(self, ticket: str) -> bool:
        parts = (ticket or "").split(".")
        return len(parts) == 4 and parts[0] in self.windows and int(parts[1]) == self.windows[parts[0]][0]             and int(parts[2]) > time.time() + self.clock_offset

    def session(self) -> dict:
        self.issued += 1
        return {"access_token": f"at{self.issued}", "refresh_token": "rt-secret",
                "expires_in": self.expires_in, "expires_at": time.time() + self.expires_in,
                "user": {"id": "u1", "email": "amy@example.com"}}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        self.calls.append((request.method, path, dict(request.headers), body))
        if path == "/auth/v1/otp":
            if body.get("email") not in self.accounts:
                if not body.get("create_user") or not self.signups:
                    return httpx.Response(422, json={"msg": "Signups not allowed for otp"})
                self.accounts.add(body["email"])
            self.challenge = body.get("code_challenge")
            self.redirect_to = request.url.params.get("redirect_to")
            return httpx.Response(200, json={})
        if path == "/auth/v1/verify":
            if body.get("token") != self.code:
                return httpx.Response(403, json={"msg": "Token has expired or is invalid"})
            return httpx.Response(200, json=self.session())
        if path == "/auth/v1/token" and request.url.params.get("grant_type") == "pkce":
            digest = hashlib.sha256(body.get("code_verifier", "").encode()).digest()
            challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
            if body.get("auth_code") != self.link_code or challenge != self.challenge:
                return httpx.Response(400, json={"msg": "invalid flow state, no valid flow state found"})
            return httpx.Response(200, json=self.session())
        if path == "/auth/v1/token":
            if not self.refresh_ok:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json=self.session())
        if path == "/auth/v1/logout":
            return httpx.Response(204)
        if self.ended and "/functions/v1/heytora" in path:
            return httpx.Response(401, json={"error": {"code": 401, "status": "HEYTORA_SIGNIN",
                                                       "message": "Sign in to HeyTora to keep talking."}})
        if path.endswith("/heytora/referrals") and request.method == "GET":
            self.referral_query = dict(request.url.params)
            return httpx.Response(200, json=self.referral)
        if path.endswith("/heytora/referrals/refresh"):
            self.referral["code"] = "AMY9876"
            return httpx.Response(200, json={"ok": True, "code": "AMY9876"})
        if path.endswith("/heytora/referrals/apply") or path.endswith("/heytora/referrals/claim"):
            code = body.get("code", "")
            if path.endswith("/claim"):
                digest = hashlib.sha256(body.get("verifier", "").encode()).digest()
                code = self.noted.pop(base64.urlsafe_b64encode(digest).decode().rstrip("="), "")
                if not code:
                    return httpx.Response(200, json={"ok": False, "reason": "none"})
            self.applied.append({"code": code, "device": body.get("device")})
            if code != "DYLANH4821":
                return httpx.Response(200, json={"ok": False, "reason": "unknown",
                                                 "message": "That code isn't valid - check it and try again."})
            return httpx.Response(200, json={"ok": True, "referrer_name": "Dylan",
                                             "trial_until": "2026-10-21T10:00:00Z"})
        if path.endswith("/heytora/checkout"):
            if self.billing_off:
                return httpx.Response(503, json={"error": {"code": 503, "status": "HEYTORA_BILLING",
                                                           "message": "Paid plans aren't open yet."}})
            plan = body.get("plan")
            if plan not in ("pro", "max"):
                return httpx.Response(400, json={"error": {"code": 400, "status": "HEYTORA_PLAN",
                                                           "message": f"Not a plan you can buy: {plan}"}})
            if self.paying:
                same, self.paying = self.paying == plan, plan
                return httpx.Response(200, json={"ok": True, "same": True} if same
                                      else {"ok": True, "changed": True, "plan": plan})
            return httpx.Response(200, json={"ok": True, "url": "https://heytora.example/pay?_ptxn=txn_1"})
        if path.endswith("/heytora/billing"):
            if not self.paying:
                return httpx.Response(404, json={"error": {"code": 404, "status": "HEYTORA_BILLING",
                    "message": "There's no billing for this account yet - choose a plan first."}})
            return httpx.Response(200, json={"ok": True, "url": "https://customer-portal.paddle.com/cpl_1"})
        if path.endswith("/heytora/account") and request.method == "DELETE":
            if body.get("confirm", "").lower() != "amy@example.com":
                return httpx.Response(400, json={"error": {"code": 400, "status": "HEYTORA_CONFIRM",
                                                           "message": "Type the email you sign in with to confirm."}})
            if self.paid:
                return httpx.Response(409, json={"error": {
                    "code": 409, "status": "HEYTORA_PAID", "heytora": {"reason": "paid", "plan": "pro"},
                    "message": "Cancel your plan on the HeyTora website first, then delete the account."}})
            self.deleted, self.ended = True, True
            self.accounts.discard("amy@example.com")
            return httpx.Response(200, json={"deleted": True})
        if path.endswith("/heytora/me"):
            return httpx.Response(200, json={
                "plan": self.plan, "plan_name": self.plan.capitalize(), "limits": self.limits,
                "usage": self.usage, "today": {}, "blocked": {}, "fair_use": 0.1,
                "period_start": "2026-10-01T00:00:00+00:00", "period_end": "2026-11-01T00:00:00+00:00",
                "now": time.time() + self.clock_offset})
        if path.endswith("/heytora/voice-pass"):
            if self.talk_refused:
                info = {"reason": "limit", "kind": "talk", "used": 25, "limit": 25, "plan": "free"}
                return httpx.Response(402, json={"error": {
                    "code": 402, "status": "HEYTORA_LIMIT", "heytora": info,
                    "message": "You've used all 25 talk messages on the Free plan this month."}})
            reply = {"token": "auth_tokens/one-session", "expires_at": time.time() + 600,
                     "url": "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage."
                            "v1beta.GenerativeService.BidiGenerateContentConstrained",
                     "usage": {"ok": True, "kind": body.get("kind"), "used": 4, "limit": 25}}
            if body.get("kind") == "talk" and body.get("standby"):
                if self.limits.get("talk") is not None:          # Free: no standby
                    return httpx.Response(402, json={"error": {
                        "code": 402, "status": "HEYTORA_LIMIT", "message": "not in plan",
                        "heytora": {"ok": False, "reason": "not_in_plan", "kind": "voice"}}})
                self.standby += 1
                reply |= {"standby": True, "now": time.time() + self.clock_offset,
                          "usage": {"ok": True, "kind": "voice", "used": self.standby, "limit": None}}
                return httpx.Response(200, json=reply)
            if body.get("kind") == "talk":
                if self.pass_refusal and body.get("new") is False:
                    info = {"ok": False, "reason": self.pass_refusal, "kind": "talk"}
                    self.pass_refusal = ""
                    return httpx.Response(402, json={"error": {
                        "code": 402, "status": "HEYTORA_LIMIT", "heytora": info,
                        "message": "That conversation has ended - ask again."}})
                window, ends, reused = self.window("talk", body.get("new", True) is not False)
                reply |= {"ticket": self.ticket("talk", window, ends), "ticket_expires_at": ends,
                          "now": time.time() + self.clock_offset,
                          "usage": {"ok": True, "kind": "talk", "window": window, "reused": reused,
                                    **({} if reused else {"used": self.opened["talk"], "limit": 25})}}
            return httpx.Response(200, json=reply)
        if path.endswith("/heytora/ticket") or path.endswith("/heytora/agent-ticket"):
            kind = "agent" if path.endswith("agent-ticket") else body.get("kind", "talk")
            window, ends, reused = self.window(kind, bool(body.get("new")))
            return httpx.Response(200, json={
                "ticket": self.ticket(kind, window, ends), "expires_at": ends, "kind": kind,
                "now": time.time() + self.clock_offset,
                "usage": {"ok": True, "kind": kind, "window": window, "reused": reused,
                          **({} if reused else {"used": self.opened[kind], "limit": 25})}})
        if "/heytora/v1beta/models" in path:
            if path.endswith(":generateContent") or path.endswith(":streamGenerateContent"):
                if not self.valid(request.headers.get("x-heytora-ticket", "")):
                    return httpx.Response(402, json={"error": {
                        "code": 402, "status": "HEYTORA_TICKET", "heytora": {"reason": "ticket"},
                        "message": "That conversation has ended - ask again."}})
                return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
            return httpx.Response(200, json={"models": []})
        return httpx.Response(404, json={"error": {"message": f"no {path}"}})

    def paths(self) -> list[str]:
        return [p for _m, p, _h, _b in self.calls]


@pytest.fixture
def heytora(monkeypatch):
    """The app in HeyTora-account mode, against the fake service."""
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


def _signed_in(h):
    h.account.send_code("amy@example.com")
    return h.account.verify("amy@example.com", "123 456")


# -- choosing: an account or an own key -----------------------------------------------------------------------

def test_an_own_key_is_used_when_there_is_one_and_nothing_is_chosen(monkeypatch):
    from config import cfg
    monkeypatch.delenv("TORA_ACCESS", raising=False)
    monkeypatch.setattr(cfg, "google_api_key", "k")
    assert cfg.access_mode() == "own_key", "a developer's .env keeps working"
    monkeypatch.setattr(cfg, "google_api_key", None)
    assert cfg.access_mode() == "account", "a fresh install signs in"
    monkeypatch.setenv("TORA_ACCESS", "own_key")
    assert cfg.access_mode() == "own_key", "a choice wins"


def test_no_business_key_ships_in_the_app():
    """The only secrets live in the server function; the app holds public values."""
    for f in ("account/settings.py", "account/service.py", "account/store.py", "ai/gemini.py"):
        src = (ROOT / f).read_text(encoding="utf-8")
        assert not re.search(r"AIza[0-9A-Za-z_-]{20,}", src), f"{f} holds a Gemini key"
    fn = (ROOT / "supabase/functions/heytora/index.ts").read_text(encoding="utf-8")
    assert 'Deno.env.get("GEMINI_API_KEY")' in fn and not re.search(r"AIza[0-9A-Za-z_-]{20,}", fn)


# -- signing in ------------------------------------------------------------------------------------------------

def test_signing_in_with_an_emailed_code(heytora):
    summary = _signed_in(heytora)
    otp = next(b for _m, p, _h, b in heytora.fake.calls if p == "/auth/v1/otp")
    assert otp == {"email": "amy@example.com", "create_user": True}, "a new email signs up here"
    assert heytora.account.signed_in() and heytora.account.email == "amy@example.com"
    assert summary["plan"] == "free" and summary["limits"]["talk"] == 25
    headers = heytora.fake.calls[0][2]
    assert headers.get("apikey") == "anon-public-key"


def test_a_new_email_signs_up_in_the_app(heytora):
    """The website has no sign-up (6 Oct 2026): a new email gets its
    account by signing in for the first time."""
    heytora.account.send_code("new@example.com")
    assert "new@example.com" in heytora.fake.accounts


def test_with_sign_ups_off_on_the_server_it_says_so(heytora):
    """Never "create one on the website": the website has no sign-up."""
    from account.service import NoAccount
    heytora.fake.signups = False
    with pytest.raises(NoAccount, match="can't be made right now") as e:
        heytora.account.send_code("nobody@example.com")
    assert "website" not in str(e.value) and "heytora.example" not in str(e.value)


# -- paid plans (Paddle) ------------------------------------------------------------------------------------------

def test_choosing_a_plan_opens_paddles_checkout_for_this_account(heytora):
    _signed_in(heytora)
    out = heytora.account.checkout("pro")
    assert out["url"].startswith("https://heytora.example/pay?_ptxn=")
    method, _path, headers, body = next(c for c in heytora.fake.calls if c[1].endswith("/heytora/checkout"))
    assert method == "POST" and body == {"plan": "pro"}, "only the plan: who pays is the sign-in's"
    assert headers["authorization"].startswith("Bearer ")


def test_already_paying_the_plan_changes_in_place(heytora):
    _signed_in(heytora)
    heytora.fake.paying = "pro"
    assert heytora.account.checkout("max") == {"ok": True, "changed": True, "plan": "max"}
    assert heytora.account.checkout("max") == {"ok": True, "same": True}


def test_billing_closed_or_a_bad_plan_says_so_in_words(heytora):
    from account.service import AccountError
    from ui.app_bridge import AppBridge
    _signed_in(heytora)
    with pytest.raises(AccountError, match="Not a plan you can buy"):
        heytora.account.checkout("gold")
    heytora.fake.billing_off = True
    assert AppBridge().choose_plan("pro") == {"error": "Paid plans aren't open yet."}


def test_manage_billing_is_paddles_portal_once_there_is_billing(heytora):
    from ui.app_bridge import AppBridge
    _signed_in(heytora)
    assert "choose a plan first" in AppBridge().billing_page()["error"]
    heytora.fake.paying = "pro"
    assert AppBridge().billing_page() == {"url": "https://customer-portal.paddle.com/cpl_1"}


def test_a_wrong_code_says_so(heytora):
    from account.service import AccountError
    heytora.account.send_code("amy@example.com")
    with pytest.raises(AccountError, match="didn't work"):
        heytora.account.verify("amy@example.com", "000000")
    assert not heytora.account.signed_in()


def test_the_emails_link_signs_in_on_this_pc(heytora):
    """Supabase's own email sender sends a link, not a code. The link brings
    the browser back to 127.0.0.1 with a one-time code that only this app can
    use - it holds the PKCE verifier behind the challenge it sent."""
    from account.loopback import REDIRECT_PATTERN, LinkCatcher
    seen = []

    def arrived(code):
        seen.append(code)
        heytora.account.verify_link(code)
        return True, ""

    catcher = LinkCatcher(arrived)
    back_to = catcher.start()
    try:
        assert re.fullmatch(r"http://127\.0\.0\.1:\d+/heytora/signed-in", back_to)
        assert fnmatch.fnmatch(back_to, REDIRECT_PATTERN), "on the project's Redirect URLs list"
        heytora.account.send_code("amy@example.com", redirect_to=back_to)
        otp = next(b for _m, p, _h, b in heytora.fake.calls if p == "/auth/v1/otp")
        assert otp["code_challenge_method"] == "s256" and len(otp["code_challenge"]) == 43
        assert heytora.fake.redirect_to == back_to and not heytora.account.signed_in()

        page = httpx.get(back_to, params={"code": "link-code-1"}, trust_env=False, timeout=10)
        assert page.status_code == 302 and seen == ["link-code-1"]
        assert page.headers["location"] == "https://heytora.example/signed-in", "the website says it"
        assert heytora.account.signed_in() and heytora.account.email == "amy@example.com"
        end = time.monotonic() + 5
        while catcher.url and time.monotonic() < end:
            time.sleep(0.02)
        assert catcher.url == "", "one link, one sign-in: the catcher stops"
    finally:
        catcher.stop()


def test_signing_in_via_the_browser(heytora):
    """Wispr's way (6 Oct 2026): the app opens the website's sign-in page with
    a PKCE challenge and its own return address. The website starts Google,
    Microsoft or email with them; the browser comes back here with a code
    only this app can redeem - the verifier never left it."""
    import base64
    import hashlib
    from urllib.parse import parse_qs, urlsplit
    url = heytora.account.browser_sign_in("http://127.0.0.1:5123/heytora/signed-in")
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://heytora.example/login"
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert query["redirect_to"] == "http://127.0.0.1:5123/heytora/signed-in"
    verifier = heytora.account._verifier
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert query["challenge"] == expected and len(expected) == 43
    assert verifier not in url, "the secret half stays in the app"
    heytora.fake.challenge = query["challenge"]          # what the website handed to Supabase
    heytora.account.verify_link("link-code-1")
    assert heytora.account.signed_in() and heytora.account.email == "amy@example.com"


def test_the_browser_sign_in_needs_a_way_back(heytora):
    from account.service import AccountError
    with pytest.raises(AccountError, match="couldn't wait for your browser"):
        heytora.account.browser_sign_in("")


def test_a_link_only_works_for_the_app_that_asked(heytora):
    from account.service import AccountError
    with pytest.raises(AccountError, match="earlier request"):
        heytora.account.verify_link("link-code-1")              # this app asked for nothing
    heytora.account.send_code("amy@example.com", redirect_to="http://127.0.0.1:5/heytora/signed-in")
    heytora.fake.link_code = "someone-elses-link"
    with pytest.raises(AccountError, match="didn't work"):
        heytora.account.verify_link("link-code-1")
    assert not heytora.account.signed_in()


def test_a_used_or_expired_link_says_so_in_the_browser(monkeypatch):
    from account.loopback import LinkCatcher
    monkeypatch.setenv("TORA_WEBSITE_URL", "https://heytora.example")
    calls = []
    catcher = LinkCatcher(lambda code: (calls.append(code), (True, ""))[1])
    url = catcher.start()
    try:
        r = httpx.get(url, params={"error": "access_denied",
                                   "error_description": "Email link is invalid or has expired"},
                      trust_env=False, timeout=10)
        assert r.status_code == 302 and r.headers["location"].endswith("/signed-in?error=1")
        monkeypatch.delenv("TORA_WEBSITE_URL")                         # a build with no website
        r = httpx.get(url, params={"error": "access_denied",
                                   "error_description": "Email link is invalid or has expired"},
                      trust_env=False, timeout=10)
        assert "didn't sign you in" in r.text and "has expired" in r.text and "Send a new one" in r.text
        other = httpx.get(url.replace("/heytora/signed-in", "/favicon.ico"), trust_env=False, timeout=10)
        assert other.status_code == 404
        assert calls == [] and catcher.url == url, "still waiting for the right link"
    finally:
        catcher.stop()


def test_the_sign_in_is_kept_encrypted_and_survives_a_restart(heytora):
    from account import service, store
    _signed_in(heytora)
    raw = store.path().read_bytes()
    assert b"rt-secret" not in raw and b"amy@example.com" not in raw, "encrypted for this Windows user"
    again = service.Account(transport=httpx.MockTransport(heytora.fake))
    assert again.signed_in() and again.email == "amy@example.com"


def test_an_expiring_session_is_refreshed_and_a_dead_one_signs_out(heytora):
    from account.service import SignInNeeded
    heytora.fake.expires_in = 30                         # inside the refresh margin
    _signed_in(heytora)
    before = heytora.fake.issued
    assert heytora.account.token() == f"at{before + 1}", "refreshed, not the old one"
    assert heytora.fake.paths()[-1] == "/auth/v1/token"
    heytora.fake.refresh_ok = False
    with pytest.raises(SignInNeeded):
        heytora.account.token()
    assert not heytora.account.signed_in()


def test_signing_out_forgets_everything(heytora):
    from account import store
    _signed_in(heytora)
    heytora.account.sign_out()
    assert not heytora.account.signed_in() and not store.path().exists()
    assert "/auth/v1/logout" in heytora.fake.paths()


def test_deleting_the_account_closes_it_on_the_server_and_forgets_it_here(heytora):
    from account import store
    _signed_in(heytora)
    heytora.account.delete_account(" Amy@example.com ")
    assert heytora.fake.deleted, "the server was asked, with the sign-in"
    method, _path, headers, body = next(c for c in heytora.fake.calls if c[1].endswith("/heytora/account"))
    assert method == "DELETE" and headers["authorization"].startswith("Bearer ")
    assert body == {"confirm": "Amy@example.com"}
    assert not heytora.account.signed_in() and not store.path().exists()


def test_a_refused_delete_keeps_the_account_and_says_why(heytora):
    from account.service import AccountError
    _signed_in(heytora)
    heytora.fake.paid = True
    with pytest.raises(AccountError, match="Cancel your plan"):
        heytora.account.delete_account("amy@example.com")
    assert heytora.account.signed_in(), "nothing forgotten unless it worked"
    heytora.fake.paid = False
    with pytest.raises(AccountError, match="Type the email"):
        heytora.account.delete_account("someone@else.com")
    assert heytora.account.signed_in() and not heytora.fake.deleted


def test_the_bridge_gives_the_reason_in_words(heytora):
    from ui.app_bridge import AppBridge
    _signed_in(heytora)
    heytora.fake.paid = True
    assert "Cancel your plan" in AppBridge().delete_account("amy@example.com")
    heytora.fake.paid = False
    assert AppBridge().delete_account("amy@example.com") == ""


def test_the_server_deletes_only_on_a_typed_email_and_never_a_paid_plan():
    fn = (ROOT / "supabase/functions/heytora/index.ts").read_text(encoding="utf-8")
    route = fn[fn.index('path === "account" && req.method === "DELETE"'):]
    route = route[:route.index("return json(200, { deleted: true });")]
    assert "HEYTORA_CONFIRM" in route and "user.email" in route
    assert route.index("HEYTORA_PAID") < route.index("deleteUser(user.id)"), "paid plans refused first"
    for sql in ("0001_auth_usage.sql", "0002_agent_tasks.sql", "0004_heytora_metering.sql"):
        text = (ROOT / "supabase/migrations" / sql).read_text(encoding="utf-8")
        refs = re.findall(r"references auth\.users\(id\)[^,;]*", text)
        assert refs and all("on delete cascade" in r for r in refs), sql


# -- the plan ----------------------------------------------------------------------------------------------------

def test_free_counts_talk_and_paid_plans_do_not(heytora):
    assert heytora.account.talk_metered(), "not known yet: the careful way"
    _signed_in(heytora)
    assert heytora.account.talk_metered()
    heytora.fake.limits = {"talk": None, "dictation": None, "agent": 150, "calls": 30000}
    heytora.account.summary(force=True)
    assert not heytora.account.talk_metered(), "Pro: unlimited talk"


def test_a_voice_pass_opens_one_session_straight_to_google(heytora):
    from ai.gemini import Models
    from ai.gemini_live import LiveSession
    _signed_in(heytora)
    talk = asyncio.run(LiveSession(Models.REALTIME, {})._address())
    dictate = asyncio.run(LiveSession(Models.STT, {})._address())
    assert talk.startswith("wss://generativelanguage.googleapis.com/") and "Constrained" in talk
    assert talk.endswith("?access_token=auth_tokens/one-session") and "key=" not in talk
    passes = [b for _m, p, _h, b in heytora.fake.calls if p.endswith("/voice-pass")]
    assert passes == [{"kind": "talk", "model": Models.REALTIME, "standby": True},   # no question yet
                      {"kind": "talk", "model": Models.REALTIME, "new": True},       # Free: no standby
                      {"kind": "dictation", "model": Models.STT}]
    assert dictate


def test_a_used_up_plan_says_which_and_what_to_do(heytora):
    from account.service import LimitReached
    from ai import gemini
    from ai.gemini import Models
    from ai.gemini_live import LiveSession
    _signed_in(heytora)
    heytora.fake.talk_refused = True
    with pytest.raises(LimitReached) as caught:
        asyncio.run(LiveSession(Models.REALTIME, {})._address())
    words = str(caught.value)
    assert "25 talk messages" in words and "Free plan" in words
    assert "heytora.example" in words and "own Gemini key" in words
    assert gemini.problem_of(caught.value) == "plan"


def test_an_agent_ticket_is_one_agent_message_for_half_an_hour(heytora):
    _signed_in(heytora)
    first = asyncio.run(heytora.account.agent_headers())
    second = asyncio.run(heytora.account.agent_headers())
    assert first["x-heytora-ticket"] == second["x-heytora-ticket"]
    assert first["x-heytora-ticket"].startswith("agent.1.")
    assert heytora.fake.paths().count("/functions/v1/heytora/ticket") == 1
    assert heytora.fake.opened["agent"] == 1
    assert first["x-heytora-kind"] == "agent" and first["Authorization"].startswith("Bearer ")


# -- every Gemini call goes through the server -------------------------------------------------------------------

def test_rest_calls_go_to_the_server_with_the_sign_in_not_a_key(heytora):
    from ai import gemini
    _signed_in(heytora)
    root, params, headers = asyncio.run(gemini._route())
    assert root == PROXY and params == {}, "no key on the URL"
    assert headers["Authorization"] == "Bearer at1" and headers["x-heytora-kind"] == "talk"

    async def dictating():
        with gemini.request_kind("dictation"):
            return await gemini._route()
    assert asyncio.run(dictating())[2]["x-heytora-kind"] == "dictation"


def test_signed_out_is_a_sign_in_problem_not_a_bad_key(heytora):
    from ai import gemini
    with pytest.raises(gemini.GeminiError) as caught:
        asyncio.run(gemini._route())
    assert caught.value.status == 401 and gemini.problem_of(caught.value) == "signin"


def test_the_servers_limit_reply_reads_as_the_plan(heytora):
    from ai import gemini
    err = gemini.GeminiError(402, json.dumps({"error": {
        "code": 402, "status": "HEYTORA_LIMIT", "message": "You've used all 25 agent messages."}}))
    assert gemini.problem_of(err) == "plan", "not 'your Google credits have run out'"


def test_own_key_mode_is_exactly_as_before(monkeypatch):
    from ai import gemini
    from config import cfg
    monkeypatch.setattr(cfg, "google_api_key", "my-own-key")
    root, params, headers = asyncio.run(gemini._route())
    assert root == gemini.API_ROOT and params == {"key": "my-own-key"} and headers == {}


def test_the_agents_think_through_the_server_with_a_ticket(heytora):
    from agents.core import ProviderMessage
    from agents.providers import GeminiAgentProvider
    _signed_in(heytora)
    seen = []

    def google(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "done"}]}}]})

    provider = GeminiAgentProvider(base_url=heytora.account.proxy_root(), auth=heytora.account.agent_headers,
                                   client=httpx.AsyncClient(transport=httpx.MockTransport(google)))
    reply = asyncio.run(provider.complete([ProviderMessage(role="user", content="plan a site")]))
    assert reply.text == "done"
    req = seen[0]
    assert str(req.url).startswith(PROXY + "/models/")
    assert "x-goog-api-key" not in req.headers and req.headers["x-heytora-ticket"].startswith("agent.")


def test_signed_in_the_agents_get_omniroute_through_heytora(heytora, monkeypatch):
    """The agents' only model is OmniRoute (Claude) - through HeyTora's server
    for anyone signed in (5 Oct 2026). Never Gemini."""
    from agents.core import TaskKind
    from agents.providers import GeminiAgentProvider, OpenAICompatibleProvider, build_router
    monkeypatch.delenv("TORA_OMNIROUTE_BASE_URL", raising=False)
    _signed_in(heytora)
    router = build_router()
    for kind in (TaskKind.GENERAL, TaskKind.CODING, TaskKind.FAST, TaskKind.VISION):
        picked = router.pick(kind)
        assert isinstance(picked, OpenAICompatibleProvider) and not isinstance(picked, GeminiAgentProvider)
        assert picked.base_url == f"{SUPABASE}/functions/v1/heytora/omniroute/v1" and picked._auth is not None
        assert "claude" in picked.model, (kind, picked.model)


def test_omniroute_set_up_on_this_pc_is_used_straight(heytora, monkeypatch):
    from agents.core import TaskKind
    from agents.providers import build_router
    monkeypatch.setenv("TORA_OMNIROUTE_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setenv("TORA_OMNIROUTE_API_KEY", "sk-test")
    _signed_in(heytora)
    picked = build_router().pick(TaskKind.CODING)
    assert picked.base_url == "https://gateway.example/v1" and picked._auth is None


def test_the_agents_calls_carry_the_agent_ticket(heytora):
    from agents.core import ProviderMessage
    from agents.providers import provider_via_heytora
    _signed_in(heytora)
    seen = []

    def gateway(request):
        seen.append(request)
        body = "".join(f"data: {line}\n\n" for line in (
            '{"choices":[{"delta":{"content":"done"}}]}',
            '{"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":1}}',
            "[DONE]"))
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
    provider = provider_via_heytora()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(gateway))
    reply = asyncio.run(provider.complete([ProviderMessage(role="user", content="plan a site")]))
    req = seen[0]
    assert reply.text == "done" and str(req.url).endswith("/heytora/omniroute/v1/chat/completions")
    assert req.headers["x-heytora-ticket"].startswith("agent.") and req.headers["x-heytora-kind"] == "agent"
    assert req.headers["Authorization"].startswith("Bearer at"), "the sign-in, never a gateway key"
    assert asyncio.run(provider.health_check()) is True, "no paid call to check health"


# -- the manager: a metered plan opens the voice on the question ---------------------------------------------------

def _bare_manager():
    from companion_manager import CompanionManager
    m = CompanionManager.__new__(CompanionManager)
    m._submit = lambda coro: coro.close()
    m._realtime = None
    return m


def test_free_does_not_open_the_voice_until_asked(heytora, monkeypatch):
    _signed_in(heytora)
    m = _bare_manager()
    opened = []

    async def connect(_prompt):
        opened.append(1)
        return True
    m._get_realtime = lambda: SimpleNamespace(connect=connect)
    m._realtime_system_prompt = lambda: "prompt"
    assert asyncio.run(m._realtime_connect()) is False and opened == [], "start-up: no talk message spent"
    assert asyncio.run(m._realtime_connect(on_demand=True)) is True and opened == [1]


def test_out_of_talk_says_so_instead_of_the_slow_route(heytora):
    from companion_manager import AppState
    m = _bare_manager()
    said, states = [], []
    m._rt_streaming = False
    m._listener = SimpleNamespace(stop_recording=lambda: bytes(16000))
    m._problem_kind = "plan"
    m._problem_detail = "You've used all 25 talk messages on the Free plan this month."
    m.sig_response_chunk = SimpleNamespace(emit=said.append)
    m.sig_response_done = SimpleNamespace(emit=lambda _t: None)
    m._emit_state = states.append

    async def no_cascade(_pcm):
        raise AssertionError("the cascade would spend dictation on the same question")
    m._cascade_turn = no_cascade
    asyncio.run(m._end_capture_and_process())
    assert said == ["You've used all 25 talk messages on the Free plan this month."]
    assert states == [AppState.IDLE]


def test_the_first_launch_answers_go_into_toras_instructions(monkeypatch):
    from companion_manager import _build_system_prompt
    from config import cfg
    monkeypatch.setattr(cfg, "_write_env", lambda *_a: None)
    cfg.set_profile("Amy", ["Accounting & money", "Coding"])
    prompt = _build_system_prompt(realtime=True)
    assert "The user's name is Amy" in prompt and "Accounting & money, Coding" in prompt


# -- the server's own rules ----------------------------------------------------------------------------------------

def test_the_server_forwards_only_model_calls():
    fn = (ROOT / "supabase/functions/heytora/index.ts").read_text(encoding="utf-8")
    pattern = re.search(r"const REST_PATH =\s*/(.+)/;", fn).group(1)
    allowed = re.compile(pattern)
    for ok in ("v1beta/models", "v1beta/models/gemini-3.8-flash:generateContent",
               "v1beta/models/gemini-3.1-flash-tts-preview:streamGenerateContent",
               "v1beta/models/gemini-embedding-001:batchEmbedContents"):
        assert allowed.match(ok), ok
    for bad in ("v1beta/files", "v1beta/tunedModels", "v1beta/cachedContents",
                "v1beta/models/x:delete", "v1beta/models/../files", "upload/v1beta/files"):
        assert not allowed.match(bad), bad
    assert 'query.delete("key")' in fn, "a key from the client is never passed on"


def test_the_plans_and_their_allowances():
    sql = (ROOT / "supabase/migrations/0003_heytora_plans.sql").read_text(encoding="utf-8")
    assert "('free', 'Free', '{\"talk\": 25," in sql
    assert "('pro',  'Pro',  '{\"talk\": null, \"dictation\": null, \"agent\": 150," in sql
    assert "('max',  'Max',  '{\"talk\": null, \"dictation\": null, \"agent\": 1000," in sql
    assert "security definer" in sql and "set search_path = public, pg_temp" in sql
    assert "grant execute on function public.heytora_consume(text, int, jsonb) to authenticated" in sql
    assert "from public, anon" in sql, "never callable without signing in"
