"""Connecting Gmail and sending (CI-07, CI-09, CI-11..CI-15).

Google is played by an httpx MockTransport and its consent page by a fake
browser; the vault is a dictionary (conftest). Nothing goes online.
"""
from __future__ import annotations

import base64
import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from integrations import gmail, oauth, providers, vault
from integrations.connections import Connections
from integrations.lifecycle import State

SEND = providers.PROVIDERS["gmail"].scopes["send"]
DRAFTS = providers.PROVIDERS["gmail"].scopes["drafts"]


def _id_token(email):
    body = base64.urlsafe_b64encode(json.dumps({"email": email}).encode()).decode().rstrip("=")
    return f"x.{body}.y"


class Google:
    """The token, revoke and Gmail endpoints, and what was asked of them."""

    def __init__(self, grant_scopes=None, revoke_ok=True, refresh_ok=True):
        self.grant_scopes, self.revoke_ok, self.refresh_ok = grant_scopes, revoke_ok, refresh_ok
        self.seen, self.sent = [], []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        form = parse_qs(request.content.decode()) if request.method == "POST" and \
            request.headers.get("content-type", "").startswith("application/x-www-form") else {}
        if request.url.path == "/token":
            if form.get("grant_type") == ["refresh_token"] and not self.refresh_ok:
                return httpx.Response(400, json={"error": "invalid_grant"})
            scopes = self.grant_scopes or ["openid", "email", SEND]
            return httpx.Response(200, json={"access_token": f"at-{len(self.seen)}", "refresh_token": "rt",
                                             "expires_in": 3600, "scope": " ".join(scopes),
                                             "id_token": _id_token("sam@example.com")})
        if request.url.path == "/revoke":
            return httpx.Response(200 if self.revoke_ok else 503)
        if request.url.path.endswith("/messages/send"):
            self.sent.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "msg-1"})
        return httpx.Response(404)


def _browser(answer=None):
    """Plays Google's consent page: comes back with the code and the state it was given."""
    calls = {}

    def factory(handler):
        class Catcher:
            def start(self):
                return "http://127.0.0.1:5555" + oauth.PATH

            def stop(self):
                pass
        calls["handler"] = handler
        return Catcher()

    def open_browser(url):
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        calls["asked"] = q
        reply = answer(q) if answer else {"state": q["state"], "code": "one-time"}
        calls["handler"](reply)
    return factory, open_browser, calls


@pytest.fixture(autouse=True)
def client_id(monkeypatch):
    monkeypatch.setenv("TORA_GOOGLE_CLIENT_ID", "heytora-desktop")
    monkeypatch.setenv("TORA_GOOGLE_CLIENT_SECRET", "not-secret")


def _connections(tmp_path, google, answer=None):
    factory, open_browser, calls = _browser(answer)
    transport = httpx.MockTransport(google)

    def authorize(scopes, login_hint=""):
        return oauth.authorize(scopes, login_hint=login_hint, open_browser=open_browser, transport=transport,
                               catcher_factory=factory, wait_s=2)
    return Connections(tmp_path / "connections.json", transport=transport, authorize=authorize), calls


def test_connecting_asks_for_send_alone_with_pkce_and_state(tmp_path):
    google = Google()
    conns, calls = _connections(tmp_path, google)
    conn = conns.connect("gmail", ["send"])
    asked = calls["asked"]
    assert asked["code_challenge_method"] == "S256" and len(asked["code_challenge"]) == 43
    assert set(asked["scope"].split()) == {"openid", "email", SEND}, "send alone - no inbox reading"
    assert asked["redirect_uri"].startswith("http://127.0.0.1:") and asked["include_granted_scopes"] == "true"
    token_call = parse_qs(google.seen[0].content.decode())
    assert token_call["code_verifier"][0] and token_call["code"] == ["one-time"]
    assert conn.state is State.CONNECTED and conn.account == "sam@example.com"
    saved = (tmp_path / "connections.json").read_text()
    assert "rt" not in saved and "at-" not in saved, "tokens are in the vault, not on disk"
    assert vault.vault().get(conn.vault_ref)["refresh_token"] == "rt"


def test_a_wrong_state_or_a_no_is_refused(tmp_path):
    conns, _ = _connections(tmp_path, Google(), answer=lambda q: {"state": "forged", "code": "x"})
    assert conns.connect("gmail", ["send"]).state is State.ERROR
    conns, _ = _connections(tmp_path, Google(), answer=lambda q: {"state": q["state"], "error": "access_denied"})
    assert conns.connect("gmail", ["send"]).state is State.DENIED


def test_a_partial_grant_is_limited_and_disables_just_that_part(tmp_path):
    conns, _ = _connections(tmp_path, Google(grant_scopes=["openid", "email", SEND]))
    conn = conns.connect("gmail", ["send", "drafts"])
    assert conn.state is State.LIMITED
    assert conns.usable("gmail", "send") and not conns.usable("gmail", "drafts")


def test_sending_needs_gmail_to_confirm(tmp_path):
    google = Google()
    conns, _ = _connections(tmp_path, google)
    conn = conns.connect("gmail", ["send"])
    draft = gmail.Draft(sender=conn.account, to=["lee@example.com"], subject="Hi", body="See you at 3.")
    msg_id = gmail.send(draft, conns.token(conn), transport=httpx.MockTransport(google))
    assert msg_id == "msg-1" and google.sent and "raw" in google.sent[0]
    assert google.seen[-1].headers["authorization"].startswith("Bearer at-")


def test_an_edit_voids_the_approval():
    a = gmail.Draft("sam@example.com", ["lee@example.com"], "Hi", "See you at 3.")
    b = gmail.Draft("sam@example.com", ["lee@example.com"], "Hi", "See you at 4.")
    assert a.digest() != b.digest() and a.digest() == gmail.Draft(**a.__dict__).digest()
    assert "From: sam@example.com" in a.review() and "To: lee@example.com" in a.review()
    assert gmail.Draft("sam@example.com", [], "Hi", "x").problems() == ["no recipient"]


def test_an_expired_grant_asks_to_reconnect_once(tmp_path):
    google = Google(refresh_ok=False)
    conns, _ = _connections(tmp_path, google)
    conn = conns.connect("gmail", ["send"])
    secret = vault.vault().get(conn.vault_ref)
    vault.vault().put(conn.vault_ref, {**secret, "expires_at": time.time() - 1})
    assert conns.token(conn) is None and conn.state is State.REAUTH_REQUIRED
    assert not conns.usable("gmail", "send")


def test_disconnect_revokes_and_forgets_or_says_where_to_remove_it(tmp_path):
    google = Google()
    conns, _ = _connections(tmp_path, google)
    conn = conns.connect("gmail", ["send"])
    assert conns.disconnect(conn) and vault.vault().get(conn.vault_ref) is None and not conns.all()
    conns2, _ = _connections(tmp_path, Google(revoke_ok=False))
    conn2 = conns2.connect("gmail", ["send"])
    assert not conns2.disconnect(conn2)
    assert conn2.state is State.ERROR and "myaccount.google.com/permissions" in conn2.detail
    assert not conns2.usable("gmail", "send"), "blocked until it is sorted"


def test_two_accounts_are_kept_apart(tmp_path):
    conns, _ = _connections(tmp_path, Google())
    conns.connect("gmail", ["send"])
    other = Google()
    other_conns, _ = _connections(tmp_path, other)
    assert [c.account for c in other_conns.all("gmail")] == ["sam@example.com"], "kept on disk by account"


def test_without_heytoras_client_id_nothing_connects(tmp_path, monkeypatch):
    monkeypatch.delenv("TORA_GOOGLE_CLIENT_ID")
    with pytest.raises(oauth.NotConfigured):
        oauth.client()
