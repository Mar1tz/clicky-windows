"""The signed-in HeyTora account: sign-in by email code, the session, and the
`heytora` server function that holds the business's Gemini key.

What goes where, and why:

  * Sign-in is Supabase Auth by email - the same account the user created on
    the website. "create_user" is off: an account and its plan are made on
    the website, the app only signs in to one. The email has a link (caught
    on this PC by account/loopback.py and swapped for a session with PKCE)
    and, once the project has its own email sender, a code to type.
  * Live voice (talk) and dictation connect STRAIGHT to Google with a voice
    pass: a short-lived token the server mints with the business key, good
    for one session (voice_pass). As fast as a key, and nothing to steal: it
    expires in minutes and opens one session.
  * Everything else - seeing the screen, planning a walkthrough, the step
    voice, embeddings, the agents - goes through the server, which adds the
    key (proxy_root). The app never holds it.
  * The plan is counted on the server, where a modified app cannot reach
    it (migration 0004). A talk message is a talk WINDOW the server opens -
    the voice pass and every call made for that question run on its ticket
    and end with it; an agent message is an agent window. The app says when
    a new question starts (new_talk); the server never lets a window grow.
    Every forwarded call also counts against hidden monthly ceilings.

Network calls are blocking (httpx.Client) and thread-safe; the async
wrappers run them on a worker thread, so any event loop - the manager's, or
a wizard's throwaway one - can call them.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlencode

import httpx

from account import settings, store

_log = logging.getLogger("tora.account")

REFRESH_EARLY_S = 90          # refresh a session this long before it expires
SUMMARY_TTL_S = 60            # the plan and usage are re-read at most this often
TICKET_SPARE_S = 30           # a window ticket this close to its end is asked for again


class AccountError(Exception):
    """Something to tell the user, in words they can act on."""


class NotConfigured(AccountError):
    def __init__(self):
        super().__init__("HeyTora accounts are not set up in this build - use your own Gemini key.")


class SignInNeeded(AccountError):
    def __init__(self, message: str = "Sign in to HeyTora to keep talking."):
        super().__init__(message)


class NoAccount(AccountError):
    def __init__(self, website: str = ""):
        where = f" Create one at {website}" if website else " Create one on the HeyTora website"
        super().__init__(f"There's no HeyTora account with that email.{where}, then sign in here.")


class LimitReached(AccountError):
    """The plan's allowance for this month is used up - or a per-minute brake."""

    def __init__(self, info: dict):
        self.info = dict(info or {})
        self.kind = self.info.get("kind", "")
        self.used = self.info.get("used")
        self.limit = self.info.get("limit")
        self.plan = self.info.get("plan", "")
        super().__init__(limit_words(self.info, settings.website_url()))


_KIND_WORDS = {"talk": "talk messages", "dictation": "dictations", "agent": "agent messages",
               "calls": "requests"}


def limit_words(info: dict, website: str = "") -> str:
    kind = _KIND_WORDS.get(info.get("kind", ""), "allowance")
    if info.get("reason") == "rate":
        return "That's a lot at once - give it a minute and try again."
    limit, plan = info.get("limit"), (info.get("plan") or "").capitalize()
    head = (f"You've used all {limit} {kind} on the {plan} plan this month."
            if limit is not None else f"You've used this month's {kind}.")
    where = f" at {website}" if website else " on the HeyTora website"
    return f"{head} Upgrade{where}, or switch to your own Gemini key in Settings."


@dataclass
class VoicePass:
    url: str                  # the constrained Live websocket, without the token
    token: str                # access_token for it
    usage: dict = field(default_factory=dict)
    ticket: str = ""          # talk: the window's ticket, for the calls made for this question

    def address(self) -> str:
        return f"{self.url}?access_token={self.token}"


class Account:
    def __init__(self, transport: Optional[httpx.BaseTransport] = None):
        self._transport = transport                 # tests give a fake one
        self._lock = threading.RLock()
        self._session: Optional[dict] = store.load()
        self._summary: Optional[dict] = None
        self._summary_at = 0.0
        self._tickets: dict[str, tuple[str, float]] = {}   # kind -> (window ticket, ends)
        self._new_talk = False                              # the next talk window is a new question
        self._window_locks = {"talk": threading.Lock(), "agent": threading.Lock()}
        self._verifier: Optional[str] = None          # PKCE: the secret half of a link sign-in

    # -- state -------------------------------------------------------------------------------------------

    def signed_in(self) -> bool:
        with self._lock:
            return bool(self._session and self._session.get("refresh_token"))

    @property
    def email(self) -> str:
        with self._lock:
            return (self._session or {}).get("email", "")

    def proxy_root(self) -> str:
        """Where REST calls go instead of Google: same paths, the server adds the key."""
        return settings.function_url("v1beta")

    # -- signing in --------------------------------------------------------------------------------------

    def send_code(self, email: str, redirect_to: str = "") -> None:
        """Email a sign-in to an EXISTING account: a link, and a code if the email shows one.

        With `redirect_to` (account/loopback.py's address) the link brings the
        browser back to this PC with a one-time code for verify_link().
        """
        self._need_config()
        email = (email or "").strip().lower()
        if "@" not in email or "." not in email.split("@")[-1]:
            raise AccountError("That doesn't look like an email address.")
        body: dict = {"email": email, "create_user": False}
        path = "otp"
        if redirect_to:
            verifier = secrets.token_urlsafe(48)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            body |= {"code_challenge": challenge.decode().rstrip("="), "code_challenge_method": "s256"}
            path = "otp?" + urlencode({"redirect_to": redirect_to})
            with self._lock:
                self._verifier = verifier
        r = self._auth_post(path, body)
        if r.status_code >= 400:
            text = r.text.lower()
            if "signup" in text or "not found" in text or "not allowed" in text:
                raise NoAccount(settings.website_url())
            raise AccountError(_auth_message(r, "Could not send the code - try again in a minute."))

    def verify(self, email: str, code: str) -> dict:
        """Check the code and stay signed in. Returns the plan summary."""
        self._need_config()
        code = "".join(ch for ch in (code or "") if ch.isdigit())
        r = self._auth_post("verify", {"type": "email", "email": (email or "").strip().lower(),
                                       "token": code})
        if r.status_code >= 400:
            raise AccountError("That code didn't work - it may have expired. Send a new one.")
        self._keep(r.json())
        return self.summary(force=True)

    def verify_link(self, auth_code: str) -> dict:
        """The email's link came back to this PC: swap its one-time code for a session."""
        self._need_config()
        with self._lock:
            verifier = self._verifier
        if not verifier:
            raise AccountError("That link is from an earlier request - send a new one from HeyTora.")
        r = self._auth_post("token?grant_type=pkce", {"auth_code": auth_code, "code_verifier": verifier})
        if r.status_code >= 400:
            raise AccountError("That link didn't work - it may have expired or been used. "
                               "Send a new one from HeyTora.")
        with self._lock:
            self._verifier = None
        self._keep(r.json())
        return self.summary(force=True)

    def sign_out(self) -> None:
        with self._lock:
            session, self._session = self._session, None
            self._summary, self._tickets, self._new_talk = None, {}, False
            store.clear()
        if session and settings.configured():
            try:
                with self._http() as http:
                    http.post(f"{settings.supabase_url()}/auth/v1/logout",
                              headers={**self._apikey(),
                                       "Authorization": f"Bearer {session.get('access_token', '')}"})
            except Exception:  # noqa: BLE001 - signed out here either way
                pass

    # -- the session -------------------------------------------------------------------------------------

    def token(self) -> str:
        """A current access token, refreshed when it is about to expire."""
        self._need_config()
        with self._lock:
            s = self._session
            if not s or not s.get("refresh_token"):
                raise SignInNeeded()
            if time.time() < float(s.get("expires_at") or 0) - REFRESH_EARLY_S:
                return s["access_token"]
            r = self._auth_post("token?grant_type=refresh_token", {"refresh_token": s["refresh_token"]})
            if r.status_code >= 400:
                if r.status_code in (400, 401, 403):
                    self._session = None
                    store.clear()
                    raise SignInNeeded("Your sign-in has expired - sign in to HeyTora again.")
                raise AccountError(_auth_message(r, "Could not reach HeyTora - check the internet."))
            self._keep(r.json())
            return self._session["access_token"]

    async def atoken(self) -> str:
        return await asyncio.to_thread(self.token)

    def _keep(self, data: dict) -> None:
        user = data.get("user") or {}
        expires_at = data.get("expires_at") or (time.time() + float(data.get("expires_in") or 3600))
        with self._lock:
            old = self._session or {}
            self._session = {
                "access_token": data.get("access_token", ""),
                "refresh_token": data.get("refresh_token") or old.get("refresh_token", ""),
                "expires_at": float(expires_at),
                "email": user.get("email") or old.get("email", ""),
                "user_id": user.get("id") or old.get("user_id", ""),
            }
            store.save(self._session)

    # -- the server ----------------------------------------------------------------------------------------

    def summary(self, force: bool = False) -> dict:
        """The plan, its limits and this month's usage: {plan, limits, usage, ...}."""
        with self._lock:
            if not force and self._summary is not None and time.time() - self._summary_at < SUMMARY_TTL_S:
                return dict(self._summary)
        data = self._call("GET", "me")
        with self._lock:
            self._summary, self._summary_at = data, time.time()
        return dict(data)

    async def asummary(self, force: bool = False) -> dict:
        return await asyncio.to_thread(self.summary, force)

    def talk_metered(self) -> bool:
        """Is talk counted on this plan (Free) - so each question needs its
        own voice pass? From the last summary read, never the network (it is
        asked on the hotkey). Not known yet counts as yes: the careful way."""
        with self._lock:
            summary = self._summary
        if summary is None:
            return True
        return (summary.get("limits") or {}).get("talk", 0) is not None

    def voice_pass(self, kind: str, model: str) -> VoicePass:
        """A one-session pass to talk (or dictate) straight to Google.

        Talk: the pass lives in a talk window - a new one for a new question
        (new_talk, or none open), otherwise this question's (a reconnect) -
        and Google ends the session when the window ends.
        """
        if kind != "talk":
            data = self._call("POST", "voice-pass", {"kind": kind, "model": model})
            usage = data.get("usage") or {}
            self._note_usage(usage)
            return VoicePass(url=data["url"], token=data["token"], usage=usage)
        with self._window_locks["talk"]:
            with self._lock:
                new = self._new_talk or not self._held("talk")
            body = {"kind": "talk", "model": model, "new": new}
            try:
                data = self._call("POST", "voice-pass", body)
            except LimitReached as e:
                # This question's window has just ended, or has had its passes:
                # the next question it is.
                if new or e.info.get("reason") not in ("passes", "window"):
                    raise
                data = self._call("POST", "voice-pass", {**body, "new": True})
            usage = data.get("usage") or {}
            self._note_usage(usage)
            with self._lock:
                self._new_talk = False
                if data.get("ticket"):
                    self._tickets["talk"] = (data["ticket"], float(data.get("ticket_expires_at") or 0))
            return VoicePass(url=data["url"], token=data["token"], usage=usage,
                             ticket=data.get("ticket", ""))

    async def avoice_pass(self, kind: str, model: str) -> VoicePass:
        return await asyncio.to_thread(self.voice_pass, kind, model)

    def new_talk(self) -> None:
        """A new question on a plan that counts talk messages: the next talk
        window - a voice pass, or the first call made for it - is a new one."""
        with self._lock:
            self._new_talk = True

    def ticket(self, kind: str = "talk") -> str:
        """The ticket of the window this work belongs to: "talk" (a talk
        message) or "agent" (an agent message, half an hour of agent work).
        The open window's ticket costs nothing; a new window is counted."""
        with self._window_locks[kind]:
            with self._lock:
                new = kind == "talk" and self._new_talk
                if not new and self._held(kind):
                    return self._tickets[kind][0]
            data = self._call("POST", "ticket", {"kind": kind, "new": new})
            self._note_usage(data.get("usage") or {})
            with self._lock:
                if kind == "talk":
                    self._new_talk = False
                self._tickets[kind] = (data["ticket"], float(data.get("expires_at") or 0))
                return data["ticket"]

    async def aticket(self, kind: str = "talk") -> str:
        return await asyncio.to_thread(self.ticket, kind)

    def _held(self, kind: str) -> bool:
        """A ticket for `kind` with some life left in it? (Call with _lock held.)"""
        held = self._tickets.get(kind)
        return bool(held and time.time() < held[1] - TICKET_SPARE_S)

    def agent_ticket(self) -> str:
        """An agent ticket - one agent message: up to half an hour of agent work."""
        return self.ticket("agent")

    async def agent_headers(self) -> dict:
        """Headers for an agent's call through the server."""
        token = await self.atoken()
        ticket = await asyncio.to_thread(self.agent_ticket)
        return {"Authorization": f"Bearer {token}", "x-heytora-kind": "agent",
                "x-heytora-ticket": ticket}

    def _note_usage(self, usage: dict) -> None:
        """Keep the cached summary's count in step with what the server said."""
        kind = usage.get("kind")
        with self._lock:
            if self._summary is not None and kind and usage.get("used") is not None:
                self._summary.setdefault("usage", {})[kind] = usage["used"]

    def _call(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        token = self.token()
        with self._http() as http:
            r = http.request(method, settings.function_url(path), json=body,
                             headers={**self._apikey(), "Authorization": f"Bearer {token}"})
        if r.status_code == 401:
            # The server no longer accepts this sign-in (account deleted, signed
            # out everywhere): forget it here too, so the app asks again.
            with self._lock:
                self._session, self._summary, self._tickets = None, None, {}
                store.clear()
            raise SignInNeeded("Your sign-in has ended - sign in to HeyTora again.")
        if r.status_code in (402, 429):
            raise LimitReached(_heytora_info(r))
        if r.status_code >= 400:
            raise AccountError(_auth_message(r, "HeyTora's server had a problem - try again."))
        return r.json()

    # -- plumbing ------------------------------------------------------------------------------------------

    def _need_config(self) -> None:
        if not settings.configured():
            raise NotConfigured()

    def _apikey(self) -> dict:
        return {"apikey": settings.anon_key()}

    def _http(self) -> httpx.Client:
        return httpx.Client(timeout=httpx.Timeout(15.0, connect=8.0), transport=self._transport)

    def _auth_post(self, path: str, body: dict) -> httpx.Response:
        try:
            with self._http() as http:
                return http.post(f"{settings.supabase_url()}/auth/v1/{path}", json=body,
                                 headers=self._apikey())
        except httpx.HTTPError as e:
            raise AccountError("Could not reach HeyTora - check the internet connection.") from e


def _heytora_info(r: httpx.Response) -> dict:
    try:
        err = r.json().get("error") or {}
        return err.get("heytora") or {"reason": "limit", "message": err.get("message", "")}
    except Exception:  # noqa: BLE001
        return {"reason": "limit"}


def _auth_message(r: httpx.Response, fallback: str) -> str:
    try:
        data = r.json()
        msg = data.get("msg") or data.get("message") or data.get("error_description") \
            or (data.get("error") or {}).get("message")
        return str(msg) if msg else fallback
    except Exception:  # noqa: BLE001
        return fallback


_account: Optional[Account] = None
_account_lock = threading.Lock()


def account() -> Account:
    """The one Account for the app."""
    global _account
    with _account_lock:
        if _account is None:
            _account = Account()
        return _account


def reset(new: Optional[Account] = None) -> None:
    """Replace the app's Account (tests, and after the data folder moves)."""
    global _account
    with _account_lock:
        _account = new
