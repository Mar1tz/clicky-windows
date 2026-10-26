"""Which services HeyTora knows, by exact host (CI-01).

A page's address says which service it is only when its host is exactly
one of the service's hosts: "mail.google.com" is Gmail; "mail.google.com.
example.test", "mail-google.com" or a "mail.google.com@evil.test" login
trick are not. Each service lists its features, the OAuth scope each one
needs (so a scope is asked for only when its feature is used, CI-15), and
the rules a model is given when it works there.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    hosts: tuple[str, ...]
    scopes: dict = field(default_factory=dict)        # feature -> OAuth scope
    rules: str = ""                                    # for a model working with it
    auth: str = "google"                               # which OAuth server


GOOGLE_SCOPE = "https://www.googleapis.com/auth/"
PROVIDERS: dict[str, Provider] = {
    "gmail": Provider(
        "gmail", "Gmail", ("mail.google.com",),
        scopes={"send": GOOGLE_SCOPE + "gmail.send", "drafts": GOOGLE_SCOPE + "gmail.compose",
                "read": GOOGLE_SCOPE + "gmail.readonly"},
        rules="An email is sent only after the person approved exactly this sender, these recipients, "
              "this subject and this body. Never send to an address the person did not give."),
    "calendar": Provider(
        "calendar", "Google Calendar", ("calendar.google.com",),
        scopes={"read": GOOGLE_SCOPE + "calendar.readonly"},
        rules="Read only: say what is in the calendar; never create, move or delete events."),
}


def service_for(url: str) -> Optional[str]:
    """The service a page belongs to, by its exact host - or None."""
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return None
    if parts.scheme not in ("https", "http") or "@" in parts.netloc:
        return None                                   # user@host tricks are never a service
    host = (parts.hostname or "").lower().rstrip(".")
    for pid, p in PROVIDERS.items():
        if host in p.hosts:
            return pid
    return None


def scopes_for(provider: str, features: list[str]) -> list[str]:
    """The scopes those features need - and no others."""
    p = PROVIDERS[provider]
    return [p.scopes[f] for f in features if f in p.scopes]
