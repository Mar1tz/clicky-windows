"""Google Calendar, read only (CI-17) - the second connector.

The same lifecycle as Gmail send (connections.py): connected for the
"read" feature alone (calendar.readonly), a token from the vault, an
expired grant asking for one reconnect. It reads the events in a date
range from the primary calendar - what is on, when, where - and never
creates, moves or deletes anything.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional

import httpx

EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
MAX_EVENTS = 50


@dataclass
class Event:
    title: str
    start: str                  # ISO date or date-time, as Google gives it
    end: str
    location: str = ""
    all_day: bool = False

    def line(self) -> str:
        when = self.start[:10] + " (all day)" if self.all_day else f"{self.start[:16].replace('T', ' ')}-{self.end[11:16]}"
        return f"{when}: {self.title}" + (f" @ {self.location}" if self.location else "")


class ReadFailed(Exception):
    def __init__(self, words: str, expired: bool = False) -> None:
        super().__init__(words)
        self.expired = expired


def window(start: dt.date, days: int) -> tuple[str, str]:
    """RFC 3339 bounds for `days` local days from `start`."""
    tz = dt.datetime.now().astimezone().tzinfo
    begin = dt.datetime.combine(start, dt.time.min, tzinfo=tz)
    return begin.isoformat(), (begin + dt.timedelta(days=max(1, days))).isoformat()


def events(token: str, start: dt.date, days: int = 1, *,
           transport: Optional[httpx.BaseTransport] = None) -> list[Event]:
    lo, hi = window(start, days)
    try:
        with httpx.Client(transport=transport, timeout=20) as http:
            r = http.get(EVENTS_URL, headers={"Authorization": f"Bearer {token}"},
                         params={"timeMin": lo, "timeMax": hi, "singleEvents": "true", "orderBy": "startTime",
                                 "maxResults": str(MAX_EVENTS)})
    except httpx.HTTPError as exc:
        raise ReadFailed(f"Google Calendar could not be reached ({type(exc).__name__}).")
    if r.status_code == 401:
        raise ReadFailed("Google Calendar no longer accepts HeyTora's connection - it needs connecting again.",
                         expired=True)
    if r.status_code != 200:
        raise ReadFailed(f"Google Calendar did not answer ({r.status_code}).")
    out = []
    for item in r.json().get("items") or []:
        if item.get("status") == "cancelled":
            continue
        s, e = item.get("start") or {}, item.get("end") or {}
        out.append(Event(title=str(item.get("summary") or "(no title)"),
                         start=str(s.get("dateTime") or s.get("date") or ""),
                         end=str(e.get("dateTime") or e.get("date") or ""),
                         location=str(item.get("location") or ""), all_day="date" in s))
    return out
