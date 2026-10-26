"""Granting a connector, the desktop way (CI-07, CI-13, CI-15).

The system browser opens Google's consent page; the answer comes back to
127.0.0.1 (account/loopback.py's catcher, on its own path) with a one-time
code that only this app can redeem: it was asked for with a PKCE S256
challenge whose verifier never left the app, and with a random `state`
that must come back unchanged - anything else is refused. Scopes are asked
for only as features need them, with include_granted_scopes so a later
grant adds to the earlier one (CI-15).

The client id is HeyTora's Google "Desktop app" client, from the
environment (TORA_GOOGLE_CLIENT_ID / TORA_GOOGLE_CLIENT_SECRET - a desktop
client's secret is not a secret, Google says so). Without it, nothing
connects and the voice says why. Transport injectable: tests never go
online.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urlencode

import httpx

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
PATH = "/heytora/connected"
IDENTITY = ["openid", "email"]                 # which account it is - shown at every send
WAIT_S = 300.0


class NotConfigured(Exception):
    def __init__(self) -> None:
        super().__init__("Connecting Google accounts is not set up on this copy of HeyTora yet "
                         "(it needs HeyTora's Google client id).")


class Refused(Exception):
    """The person said no, the state did not match, or the code was not accepted."""

    def __init__(self, words: str, denied: bool = False) -> None:
        super().__init__(words)
        self.denied = denied


@dataclass
class Grant:
    access_token: str
    refresh_token: str
    expires_at: float
    scopes: list = field(default_factory=list)
    account: str = ""                          # the email it belongs to

    def secret(self) -> dict:
        return {"access_token": self.access_token, "refresh_token": self.refresh_token,
                "expires_at": self.expires_at}


def client() -> tuple[str, str]:
    cid = os.environ.get("TORA_GOOGLE_CLIENT_ID", "").strip()
    if not cid:
        raise NotConfigured()
    return cid, os.environ.get("TORA_GOOGLE_CLIENT_SECRET", "").strip()


def pkce() -> tuple[str, str]:
    """(verifier, S256 challenge)."""
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def auth_url(client_id: str, redirect: str, scopes: list[str], state: str, challenge: str,
             login_hint: str = "") -> str:
    q = {"client_id": client_id, "redirect_uri": redirect, "response_type": "code",
         "scope": " ".join(scopes), "state": state, "code_challenge": challenge,
         "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent",
         "include_granted_scopes": "true"}
    if login_hint:
        q["login_hint"] = login_hint
    return f"{AUTH_URL}?{urlencode(q)}"


def _email(id_token: str) -> str:
    """The account's email from the ID token Google's token endpoint just
    sent over TLS (its payload; the endpoint is the authority here)."""
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return str(json.loads(base64.urlsafe_b64decode(payload)).get("email") or "")
    except Exception:  # noqa: BLE001
        return ""


def _grant(data: dict, previous_refresh: str = "") -> Grant:
    return Grant(access_token=data["access_token"], refresh_token=data.get("refresh_token") or previous_refresh,
                 expires_at=time.time() + float(data.get("expires_in", 3600)) - 60,
                 scopes=str(data.get("scope", "")).split(), account=_email(data.get("id_token", "")))


def authorize(scopes: list[str], *, login_hint: str = "", open_browser: Callable[[str], object] = webbrowser.open,
              transport: Optional[httpx.BaseTransport] = None, wait_s: float = WAIT_S,
              catcher_factory=None) -> Grant:
    """Ask for `scopes` (plus who the account is) in the system browser and
    return the grant. Raises Refused (denied, wrong state) or NotConfigured."""
    from account.loopback import LinkCatcher
    client_id, client_secret = client()
    verifier, challenge = pkce()
    state = secrets.token_urlsafe(24)
    got: dict = {}
    done = threading.Event()

    def on_params(params: dict) -> tuple[bool, str]:
        if params.get("state") != state:
            got["error"] = "state"
            done.set()
            return False, "This answer was not for HeyTora's request. Try connecting again from HeyTora."
        if params.get("error"):
            got["error"] = params["error"]
            done.set()
            return False, "Nothing was connected."
        got["code"] = params.get("code", "")
        done.set()
        return True, "You can close this tab and go back to HeyTora."

    make = catcher_factory or (lambda handler: LinkCatcher(lambda c: (False, ""), wait_s, path=PATH,
                                                           on_params=handler, done_title="Connected to HeyTora"))
    catcher = make(on_params)
    redirect = catcher.start()
    if not redirect:
        raise Refused("HeyTora could not listen for Google's answer on this PC.")
    try:
        open_browser(auth_url(client_id, redirect, IDENTITY + list(scopes), state, challenge, login_hint))
        if not done.wait(wait_s):
            raise Refused("Nothing came back from Google in time.")
    finally:
        catcher.stop()
    if got.get("error"):
        raise Refused("You chose not to connect it." if got["error"] == "access_denied"
                      else "That answer did not come from HeyTora's request." if got["error"] == "state"
                      else f"Google said: {got['error']}", denied=got["error"] == "access_denied")
    with httpx.Client(transport=transport, timeout=20) as http:
        r = http.post(TOKEN_URL, data={"client_id": client_id, "client_secret": client_secret,
                                       "code": got["code"], "code_verifier": verifier,
                                       "grant_type": "authorization_code", "redirect_uri": redirect})
    if r.status_code != 200:
        raise Refused(f"Google did not accept the sign-in ({r.status_code}).")
    return _grant(r.json())


def refresh(refresh_token: str, *, transport: Optional[httpx.BaseTransport] = None) -> Grant:
    """A new access token. Raises Refused when the grant was revoked or expired."""
    client_id, client_secret = client()
    with httpx.Client(transport=transport, timeout=20) as http:
        r = http.post(TOKEN_URL, data={"client_id": client_id, "client_secret": client_secret,
                                       "refresh_token": refresh_token, "grant_type": "refresh_token"})
    if r.status_code != 200:
        raise Refused("The connection has expired or was removed - it needs connecting again.")
    return _grant(r.json(), previous_refresh=refresh_token)


def revoke(token: str, *, transport: Optional[httpx.BaseTransport] = None) -> bool:
    """Ask Google to forget the grant. True only when Google confirmed it."""
    try:
        with httpx.Client(transport=transport, timeout=20) as http:
            r = http.post(REVOKE_URL, data={"token": token})
        return r.status_code == 200
    except httpx.HTTPError:
        return False
