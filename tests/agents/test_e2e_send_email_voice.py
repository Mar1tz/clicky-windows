"""Sending an email by voice (CI-09..CI-14, the Massive Direction Plan).

Not connected: she offers to connect, once. Connected: they see from, to,
subject and body and approve exactly that; it is "sent" only when Gmail
gives back the message id. An expired grant asks to reconnect.
"""
from __future__ import annotations

import asyncio
import time
from concurrent.futures import Future
from types import SimpleNamespace

import pytest

import companion_manager as cm
from integrations import connections as conn_mod
from integrations import gmail, vault
from integrations.connections import Connections
from integrations.lifecycle import Connection, State


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv("TORA_GOOGLE_CLIENT_ID", "heytora-desktop")
    conns = Connections(tmp_path / "connections.json", authorize=lambda *a, **k: None)
    monkeypatch.setattr(conn_mod, "_connections", conns)
    return conns


def _connected(conns, account="sam@example.com"):
    c = Connection("gmail", account, State.CONNECTED,
                   scopes=["https://www.googleapis.com/auth/gmail.send"], vault_ref=vault.ref("gmail", account))
    vault.vault().put(c.vault_ref, {"access_token": "at", "refresh_token": "rt", "expires_at": time.time() + 600})
    conns._items[c.key] = c
    return c


def _manager(answer=True):
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    asked, said, later = [], [], []

    def confirm(reason, **kw):
        asked.append((reason, kw))
        f = Future()
        f.set_result(answer)
        return f
    mgr._agents = SimpleNamespace(request_confirmation=confirm)
    mgr._say_when_idle = said.append
    mgr._submit = later.append
    return mgr, asked, said, later


ARGS = {"to": ["lee@example.com"], "subject": "Running late", "body": "Be there at 3:15."}


def test_not_connected_the_message_opens_filled_in_for_them_to_send(setup, monkeypatch):
    opened = []
    monkeypatch.setattr(cm.os, "startfile", opened.append)
    mgr, asked, *_ = _manager()
    out = asyncio.run(mgr._tool_send_email(dict(ARGS)))
    assert out["opened_compose"] == "gmail" and "press Send themselves" in out["note"] and not asked
    assert "connect_account" in out["note"], "and connecting is offered, once"
    from urllib.parse import parse_qs, urlsplit
    q = {k: v[0] for k, v in parse_qs(urlsplit(opened[0]).query).items()}
    assert opened[0].startswith("https://mail.google.com/mail/?")
    assert (q["to"], q["su"], q["body"], q["view"]) == ("lee@example.com", "Running late", "Be there at 3:15.", "cm")


def test_outlook_and_no_google_client_still_work(setup, monkeypatch):
    opened = []
    monkeypatch.setattr(cm.os, "startfile", opened.append)
    monkeypatch.delenv("TORA_GOOGLE_CLIENT_ID")
    mgr, *_ = _manager()
    out = asyncio.run(mgr._tool_send_email({**ARGS, "via": "outlook"}))
    assert out["opened_compose"] == "outlook" and opened[0].startswith("https://outlook.live.com/mail/0/deeplink/compose?")
    assert "connect_account" not in out["note"], "nothing to connect to on this copy"
    bad = asyncio.run(mgr._tool_send_email({**ARGS, "to": ["not an address"]}))
    assert not bad["ok"] and "not an email address" in bad["error"]


def test_they_approve_exactly_what_goes_and_it_is_sent_when_gmail_confirms(setup, monkeypatch):
    _connected(setup)
    sent = []
    monkeypatch.setattr(gmail, "send", lambda draft, token, **kw: sent.append((draft, token)) or "msg-9")
    mgr, asked, said, later = _manager(answer=True)
    out = asyncio.run(mgr._tool_send_email(dict(ARGS)))
    assert out["awaiting_approval"] and not sent, "nothing goes before the yes"
    reason, kw = asked[0]
    assert "From: sam@example.com" in reason and "To: lee@example.com" in reason and "Be there at 3:15." in reason
    assert kw["may_auto_approve"] is False and kw["grant_key"] == "", "a send is never waved through"
    asyncio.run(later[0])
    draft, token = sent[0]
    assert token == "at" and draft.digest() == kw["arguments"]["digest"], "exactly what was approved"
    assert said == ["Sent to lee@example.com."]


def test_a_no_sends_nothing(setup, monkeypatch):
    _connected(setup)
    monkeypatch.setattr(gmail, "send", lambda *a, **k: pytest.fail("sent without a yes"))
    mgr, asked, said, later = _manager(answer=False)
    asyncio.run(mgr._tool_send_email(dict(ARGS)))
    asyncio.run(later[0])
    assert said == ["Okay, I didn't send it."]


def test_an_expired_grant_asks_to_reconnect(setup):
    c = _connected(setup)
    c.state = State.REAUTH_REQUIRED
    mgr, *_ = _manager()
    out = asyncio.run(mgr._tool_send_email(dict(ARGS)))
    assert out["needs_reconnect"] == "sam@example.com"


def test_two_accounts_are_never_picked_silently(setup):
    _connected(setup, "sam@example.com")
    _connected(setup, "sam@work.example")
    mgr, asked, *_ = _manager()
    out = asyncio.run(mgr._tool_send_email(dict(ARGS)))
    assert sorted(out["which_account"]) == ["sam@example.com", "sam@work.example"] and not asked
    out = asyncio.run(mgr._tool_send_email({**ARGS, "account": "sam@work.example"}))
    assert out["awaiting_approval"] and "From: sam@work.example" in asked[0][0]


def test_while_guiding_it_is_an_act_that_needs_asking():
    assert "send_email" in cm.CompanionManager.ACT_TOOLS


def test_send_it_sends_the_open_email_after_a_yes(setup, monkeypatch):
    """Field, 26 Oct: "send it" had no way to press Send in the compose
    window. They approve its To and Subject; Ctrl+Enter sends it there."""
    from agents.tools import app_tools, computer_tools
    from ai import form_fill
    from ai.form_fill import Field
    monkeypatch.setattr(cm.os, "startfile", lambda link: None)
    windows = {"Compose Mail"}
    monkeypatch.setattr(app_tools, "focus_window", lambda f: {"hwnd": 2, "title": f} if f in windows else None)
    monkeypatch.setattr(app_tools, "find_window", lambda f: {"hwnd": 2, "title": f} if f in windows else None)
    monkeypatch.setattr(form_fill, "read_fields", lambda: [Field("f1", "To recipients", "lee@example.com"),
                                                           Field("f2", "Subject", "Running late")])
    pressed = []
    monkeypatch.setattr(computer_tools, "get_backend",
                        lambda: SimpleNamespace(press=lambda keys: pressed.append(keys) or windows.clear()))
    mgr, asked, said, later = _manager(answer=True)
    monkeypatch.delenv("TORA_GOOGLE_CLIENT_ID")
    asyncio.run(mgr._tool_send_email(dict(ARGS)))                  # the compose window opens
    out = asyncio.run(mgr._tool_send_open_email({}))
    assert out["awaiting_approval"] and not pressed, "nothing goes before the yes"
    reason, kw = asked[0]
    assert "To: lee@example.com" in reason and "Subject: Running late" in reason and kw["may_auto_approve"] is False
    asyncio.run(later[0])
    assert pressed == [["ctrl", "enter"]] and said == ["Sent."]


def test_connecting_is_offered_once_and_a_no_is_kept(setup, monkeypatch, tmp_path):
    """CI-10: offered when a feature needs it, at most once a session; "not
    now" holds for the session, "don't ask again" until they ask for it."""
    from integrations import offers as offers_mod
    monkeypatch.setattr(cm.os, "startfile", lambda link: None)
    mgr, *_ = _manager()
    first = asyncio.run(mgr._tool_send_email(dict(ARGS)))
    again = asyncio.run(mgr._tool_send_email(dict(ARGS)))
    assert "connect Gmail" in first["note"] and "connect Gmail" not in again["note"], "once a session"
    asyncio.run(mgr._tool_decline_connect({"service": "gmail", "never": True}))
    fresh = offers_mod.Offers(tmp_path)
    fresh.declined("gmail", never=True)
    later = offers_mod.Offers(tmp_path)                     # the next session
    assert not later.may_offer("gmail") and later.may_offer("calendar")
    later.asked_for("gmail")
    assert offers_mod.Offers(tmp_path).may_offer("gmail"), "asking for it lifts the no"
