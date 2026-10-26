"""Gmail send - the first connector (CI-09, CI-11).

Sending uses the gmail.send scope alone: HeyTora never asks to read the
inbox to send a message. A message is a Draft - sender, recipients,
subject, body - and its approval is bound to a hash of exactly those; any
change voids it (the person is asked again). It counts as sent only when
Gmail answers with the message's id.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from email.message import EmailMessage
from typing import Optional

import httpx

SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
_ADDRESS = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")


@dataclass
class Draft:
    sender: str
    to: list = field(default_factory=list)
    subject: str = ""
    body: str = ""
    cc: list = field(default_factory=list)

    def problems(self) -> list[str]:
        out = []
        if not self.to:
            out.append("no recipient")
        bad = [a for a in [*self.to, *self.cc] if not _ADDRESS.match(a.strip())]
        if bad:
            out.append("not an email address: " + ", ".join(bad))
        if not (self.subject.strip() or self.body.strip()):
            out.append("nothing to send")
        return out

    def digest(self) -> str:
        """CI-11: what an approval is bound to - every field, exactly."""
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode("utf-8")).hexdigest()

    def review(self) -> str:
        """What the approval shows, in full."""
        lines = [f"From: {self.sender}", f"To: {', '.join(self.to)}"]
        if self.cc:
            lines.append(f"Cc: {', '.join(self.cc)}")
        lines += [f"Subject: {self.subject}", "", self.body]
        return "\n".join(lines)

    def mime(self) -> str:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, ", ".join(self.to), self.subject
        if self.cc:
            msg["Cc"] = ", ".join(self.cc)
        msg.set_content(self.body)
        return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


class SendFailed(Exception):
    def __init__(self, words: str, expired: bool = False) -> None:
        super().__init__(words)
        self.expired = expired


def send(draft: Draft, token: str, *, transport: Optional[httpx.BaseTransport] = None) -> str:
    """Send it; the Gmail message id - only once Gmail confirms."""
    if draft.problems():
        raise SendFailed("; ".join(draft.problems()))
    try:
        with httpx.Client(transport=transport, timeout=30) as http:
            r = http.post(SEND_URL, headers={"Authorization": f"Bearer {token}"}, json={"raw": draft.mime()})
    except httpx.HTTPError as exc:
        raise SendFailed(f"Gmail could not be reached ({type(exc).__name__}) - nothing was sent.")
    if r.status_code == 401:
        raise SendFailed("Gmail no longer accepts HeyTora's connection - it needs connecting again.", expired=True)
    if r.status_code != 200 or not (r.json() or {}).get("id"):
        raise SendFailed(f"Gmail did not send it ({r.status_code}).")
    return r.json()["id"]
