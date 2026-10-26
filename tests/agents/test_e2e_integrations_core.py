"""Connector foundations (CI-01, CI-06, CI-08, the Massive Direction Plan).

A service is known only by its exact host; a connection moves through its
states by one rule; its tokens live in the vault and nowhere else.
"""
from __future__ import annotations

import pytest

from integrations import providers, vault
from integrations.lifecycle import Connection, State, can_become


@pytest.mark.parametrize("url,service", [
    ("https://mail.google.com/mail/u/0/#inbox", "gmail"),
    ("https://MAIL.GOOGLE.COM./mail", "gmail"),
    ("https://calendar.google.com/calendar/r", "calendar"),
    ("https://mail.google.com.example.test/mail", None),
    ("https://mail-google.com/", None),
    ("https://mail.google.com@evil.example/", None),
    ("https://evil.example/?next=https://mail.google.com", None),
    ("javascript:alert(1)//mail.google.com", None),
    ("", None),
])
def test_a_service_is_known_only_by_its_exact_host(url, service):
    assert providers.service_for(url) == service


def test_a_feature_asks_for_its_own_scope_only():
    assert providers.scopes_for("gmail", ["send"]) == ["https://www.googleapis.com/auth/gmail.send"]


def test_every_state_and_its_moves():
    S = State
    c = Connection("gmail", "sam@example.com")
    assert c.move(S.AUTHORIZING) and c.move(S.CONNECTED) and c.state.usable
    assert not c.move(S.DISCONNECTED), "a connection is revoked before it is gone"
    assert c.move(S.REAUTH_REQUIRED) and not c.state.usable
    assert c.move(S.AUTHORIZING) and c.move(S.LIMITED, "drafts not granted") and c.state.usable
    assert c.move(S.REVOKING) and c.move(S.DISCONNECTED)
    for s in S:                              # every state has a way on (or is where things end up)
        assert any(can_become(s, t) for t in S if t is not s)
    assert not can_become(S.NOT_CONNECTED, S.CONNECTED), "nothing is connected without authorizing"
    assert Connection.from_dict(c.to_dict()).state is S.DISCONNECTED


def test_tokens_live_in_the_vault_only():
    v = vault.vault()
    assert isinstance(v, vault.MemoryVault), "a test never touches Windows Credential Manager"
    target = vault.ref("gmail", "Sam@Example.com")
    v.put(target, {"refresh_token": "r-1"})
    c = Connection("gmail", "Sam@Example.com", vault_ref=target)
    assert "r-1" not in str(c.to_dict()), "the connection holds the reference, never the token"
    assert v.get(target) == {"refresh_token": "r-1"} and v.delete(target) and v.get(target) is None
