"""Email without a connection: a filled-in compose window (the fallback).

Gmail's and Outlook's own compose pages take the recipients, subject and
body in their address. Opened in the person's browser - where they are
already signed in - the message is ready and they press Send themselves.
No Google project, no review, no token: it works for everyone, today, and
nothing is sent by HeyTora.
"""
from __future__ import annotations

from urllib.parse import quote, urlencode

GMAIL = "https://mail.google.com/mail/"
OUTLOOK = "https://outlook.live.com/mail/0/deeplink/compose"


def url(to: list[str], subject: str, body: str, cc: list[str] | None = None, via: str = "gmail") -> str:
    cc = cc or []
    if via == "outlook":
        q = {"to": ";".join(to), "subject": subject, "body": body}
        if cc:
            q["cc"] = ";".join(cc)
        return f"{OUTLOOK}?{urlencode(q, quote_via=quote)}"
    q = {"view": "cm", "fs": "1", "to": ",".join(to), "su": subject, "body": body}
    if cc:
        q["cc"] = ",".join(cc)
    return f"{GMAIL}?{urlencode(q, quote_via=quote)}"
