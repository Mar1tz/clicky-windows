"""Google Calendar, read only (CI-17, the Massive Direction Plan).

The second connector on the same lifecycle: read scope alone, the events
in a range from the primary calendar, an expired grant asking for one
reconnect. Google is a MockTransport.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import time

import httpx

import companion_manager as cm
from integrations import connections as conn_mod
from integrations import gcal, providers, vault
from integrations.connections import Connections
from integrations.lifecycle import Connection, State

READ = providers.PROVIDERS["calendar"].scopes["read"]


def _google(seen, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if status != 200:
            return httpx.Response(status)
        return httpx.Response(200, json={"items": [
            {"summary": "Standup", "start": {"dateTime": "2026-10-27T09:00:00+00:00"},
             "end": {"dateTime": "2026-10-27T09:15:00+00:00"}, "location": "Teams"},
            {"summary": "Mum's birthday", "start": {"date": "2026-10-27"}, "end": {"date": "2026-10-28"}},
            {"summary": "Old", "status": "cancelled", "start": {"date": "2026-10-27"}, "end": {"date": "2026-10-28"}},
        ]})
    return httpx.MockTransport(handler)


def test_events_in_a_range_read_only():
    seen = []
    found = gcal.events("at", dt.date(2026, 10, 27), 1, transport=_google(seen))
    assert [e.line() for e in found] == ["2026-10-27 09:00-09:15: Standup @ Teams", "2026-10-27 (all day): Mum's birthday"]
    req = seen[0]
    assert req.method == "GET" and req.url.path.endswith("/calendars/primary/events")
    assert req.url.params["singleEvents"] == "true" and req.headers["authorization"] == "Bearer at"
    assert READ == "https://www.googleapis.com/auth/calendar.readonly"


def test_an_expired_grant_says_so():
    try:
        gcal.events("at", dt.date(2026, 10, 27), 1, transport=_google([], status=401))
    except gcal.ReadFailed as exc:
        assert exc.expired
    else:
        raise AssertionError("expected ReadFailed")


def test_the_voice_reads_it_or_offers_to_connect(tmp_path, monkeypatch):
    monkeypatch.setenv("TORA_GOOGLE_CLIENT_ID", "heytora-desktop")
    conns = Connections(tmp_path / "connections.json", authorize=lambda *a, **k: None)
    monkeypatch.setattr(conn_mod, "_connections", conns)
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    out = asyncio.run(mgr._tool_read_calendar({}))
    assert out["needs_connection"] == "calendar"
    c = Connection("calendar", "sam@example.com", State.CONNECTED, scopes=[READ],
                   vault_ref=vault.ref("calendar", "sam@example.com"))
    vault.vault().put(c.vault_ref, {"access_token": "at", "refresh_token": "rt", "expires_at": time.time() + 600})
    conns._items[c.key] = c
    monkeypatch.setattr(gcal, "events", lambda token, start, days: [
        gcal.Event("Standup", "2026-10-27T09:00:00", "2026-10-27T09:15:00")])
    out = asyncio.run(mgr._tool_read_calendar({"start": "2026-10-27", "days": 2}))
    assert out["ok"] and out["events"] == ["2026-10-27 09:00-09:15: Standup"] and out["days"] == 2
