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
from typing import Callable, Optional
from urllib.parse import urlencode

import httpx

from account import settings, store

_log = logging.getLogger("tora.account")

REFRESH_EARLY_S = 90          # refresh a session this long before it expires
SUMMARY_TTL_S = 60            # the plan and usage are re-read at most this often
ALERT_AT = (0.8, 1.0)         # tell the user at 80% and at 100% of an allowance, once a month each
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


_UNITS = {"talk": ("talk message", "talk messages"), "agent": ("agent message", "agent messages"),
          "dictation": ("dictation", "dictations")}


def _count(n: int, kind: str) -> str:
    one, many = _UNITS.get(kind, (kind, kind))
    return f"{n} {one if n == 1 else many}"


def resets_on(summary: dict) -> str:
    """"1 Nov" - when the month's allowances start again (the user's own calendar)."""
    end = str((summary or {}).get("period_end") or "")
    try:
        import datetime as _dt
        when = _dt.datetime.fromisoformat(end.replace("Z", "+00:00")).astimezone()
        return f"{when.day} {when:%b}"
    except ValueError:
        return "the 1st"


def short_status(summary: dict) -> str:
    """One line for the tray and the log: "Free plan - 18 talk messages left"."""
    if not summary:
        return ""
    plan = summary.get("plan_name") or (summary.get("plan") or "").capitalize() or "HeyTora"
    limits, usage = summary.get("limits") or {}, summary.get("usage") or {}
    blocked = summary.get("blocked") or {}
    if blocked.get("all") == "budget":
        return f"{plan} plan - this month's use is up (resets {resets_on(summary)})"
    parts = []
    for kind in ("talk", "agent"):
        if kind not in limits:
            continue
        if limits[kind] is None:
            parts.append(f"unlimited {_UNITS[kind][1]}")
        else:
            left = max(0, int(limits[kind]) - int(usage.get(kind) or 0))
            parts.append(f"{_count(left, kind)} left")
    return f"{plan} plan" + (" - " + ", ".join(parts) if parts else "")


def alerts(summary: dict) -> list[tuple[str, str]]:
    """(key, words) for each allowance worth telling the user about now.
    The key holds the month, so each is said once a month."""
    if not summary:
        return []
    month = str(summary.get("period_start") or "")[:7]
    plan = summary.get("plan_name") or (summary.get("plan") or "").capitalize()
    limits, usage = summary.get("limits") or {}, summary.get("usage") or {}
    blocked = summary.get("blocked") or {}
    when = resets_on(summary)
    out: list[tuple[str, str]] = []
    for kind in ("talk", "agent", "dictation"):
        limit = limits.get(kind)
        if limit is None or kind not in limits:
            if blocked.get(kind) == "day":
                out.append((f"{month}:{kind}:day:{summary.get('day_resets_at')}",
                            f"That's today's fair use of {_UNITS[kind][1]} on the {plan} plan - "
                            "more tomorrow."))
            continue
        used = int(usage.get(kind) or 0)
        limit = int(limit)
        if limit and used >= limit:
            out.append((f"{month}:{kind}:100",
                        f"You've used all {_count(limit, kind)} on the {plan} plan this month. "
                        f"They come back on {when} - or upgrade on the HeyTora website."))
        elif limit and used >= ALERT_AT[0] * limit:
            out.append((f"{month}:{kind}:80",
                        f"{_count(limit - used, kind)} left this month on the {plan} plan "
                        f"({used} of {limit} used). More on {when}."))
    fair = summary.get("fair_use")
    if blocked.get("all") == "budget":
        out.append((f"{month}:all:100", f"This month's HeyTora use on the {plan} plan is used up. "
                                        f"It starts again on {when} - or upgrade on the HeyTora website."))
    elif isinstance(fair, (int, float)) and fair >= ALERT_AT[0]:
        out.append((f"{month}:all:80", f"You've used most of this month's HeyTora use on the {plan} plan. "
                                       f"It starts again on {when}."))
    return out


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
        # The last plan read, kept with the sign-in: shown at once after a
        # restart (and talk_metered() is right from the first question), then
        # read again in the background. _summary_at 0 = not read this run.
        self._summary: Optional[dict] = (self._session or {}).get("summary")
        self._summary_at = 0.0
        self._summary_error = ""                            # why the last read failed, in words
        self._summary_busy = False
        self._listeners: list[Callable[[], None]] = []
        self._skew = 0.0                                    # this PC's clock minus the server's
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
            self._summary_error = ""
            store.clear()
        _log.info("signed out")
        self._changed()
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
                # The remembered plan and alerts belong to this user only.
                "summary": old.get("summary") if old.get("user_id", "") in ("", user.get("id", ""))
                or not user.get("id") else None,
                "warned": old.get("warned", []) if old.get("user_id", "") in ("", user.get("id", ""))
                or not user.get("id") else [],
            }
            store.save(self._session)

    # -- the server ----------------------------------------------------------------------------------------

    def summary(self, force: bool = False) -> dict:
        """The plan, its limits and this month's usage: {plan, limits, usage,
        today, blocked, fair_use, period_end, ...} (migration 0005)."""
        with self._lock:
            if not force and self._summary is not None and time.time() - self._summary_at < SUMMARY_TTL_S:
                return dict(self._summary)
        try:
            data = self._call("GET", "me")
        except AccountError as e:
            with self._lock:
                self._summary_error = str(e)
            self._changed()
            raise
        self._clock(data)
        with self._lock:
            self._summary, self._summary_at, self._summary_error = data, time.time(), ""
            self._remember()
        _log.info("plan: %s", short_status(data))
        self._changed()
        return dict(data)

    def refresh_soon(self, max_age: float = SUMMARY_TTL_S) -> bool:
        """Read the plan again in the background if it is older than
        `max_age` (and not being read already). Listeners hear when it lands
        - or fails. True if a read was started."""
        with self._lock:
            if (self._summary_busy or not self.signed_in() or not settings.configured()
                    or (self._summary_at > 0 and time.time() - self._summary_at < max_age)):
                return False
            self._summary_busy = True
        self._changed()                                     # "reading your plan"

        def read() -> None:
            try:
                self.summary(force=True)
            except Exception as e:  # noqa: BLE001 - the error is kept for the screen
                _log.info("could not read the plan: %s", e)
            finally:
                with self._lock:
                    self._summary_busy = False
                self._changed()
        threading.Thread(target=read, name="heytora-plan", daemon=True).start()
        return True

    def plan_state(self) -> dict:
        """What the screens show - never the network: the last plan read (or
        remembered from the last run), whether it is being read, and why the
        last read failed."""
        with self._lock:
            return {"summary": dict(self._summary) if self._summary else None,
                    "fresh": self._summary_at > 0, "busy": self._summary_busy,
                    "error": self._summary_error}

    def on_change(self, listener: Callable[[], None]) -> None:
        """Call `listener` (on any thread) whenever the plan or its use changes."""
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def _changed(self) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener()
            except Exception:  # noqa: BLE001 - a screen must never break the account
                _log.debug("plan listener failed", exc_info=True)

    def _remember(self) -> None:
        """Keep the plan with the sign-in, for the next run. (Call with _lock held.)"""
        if self._session is not None:
            self._session["summary"] = self._summary
            try:
                store.save(self._session)
            except Exception:  # noqa: BLE001 - only a convenience
                _log.debug("could not keep the plan on disk", exc_info=True)

    def _clock(self, data: dict) -> None:
        """Note how far this PC's clock is from the server's."""
        now = data.get("now")
        if isinstance(now, (int, float)) and now > 0:
            with self._lock:
                self._skew = time.time() - float(now)

    def _local(self, server_time: float) -> float:
        """A server time, on this PC's clock."""
        with self._lock:
            return float(server_time) + self._skew

    def take_alerts(self) -> list[str]:
        """What to tell the user about their allowances that they have not been
        told this month: 80% used, all used, today's fair use, the month's
        overall use. Each once; remembered across restarts."""
        with self._lock:
            summary = self._summary
            if not summary or self._session is None:
                return []
            warned = set(self._session.get("warned") or [])
            fresh = [(key, words) for key, words in alerts(summary) if key not in warned]
            if fresh:
                month = str(summary.get("period_start") or "")[:7]
                # Last month's keys go; this month's stay.
                self._session["warned"] = sorted({k for k in warned if k.startswith(month)}
                                                 | {k for k, _w in fresh})
                self._remember()
        for _key, words in fresh:
            _log.info("plan alert: %s", words)
        return [words for _key, words in fresh]

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
            self._clock(data)
            usage = data.get("usage") or {}
            self._note_usage(usage)
            with self._lock:
                self._new_talk = False
                if data.get("ticket"):
                    self._tickets["talk"] = (data["ticket"],
                                             self._local(float(data.get("ticket_expires_at") or 0)))
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
            self._clock(data)
            self._note_usage(data.get("usage") or {})
            with self._lock:
                if kind == "talk":
                    self._new_talk = False
                self._tickets[kind] = (data["ticket"], self._local(float(data.get("expires_at") or 0)))
                return data["ticket"]

    def drop_ticket(self, kind: str) -> None:
        """The server says this window has ended: the next call asks for its ticket again."""
        with self._lock:
            self._tickets.pop(kind, None)

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
        """Keep the plan's counts in step with what the server just said - a
        new talk window, an agent window, a dictation - so the screens move
        with every question, not only when the plan is read again."""
        kind = usage.get("kind")
        if not kind or usage.get("used") is None or usage.get("reused"):
            return
        _log.info("%s: %s of %s this month%s", _KIND_WORDS.get(kind, kind), usage["used"],
                  "unlimited" if usage.get("limit") is None else usage["limit"],
                  f" (window {usage['window']})" if usage.get("window") else "")
        with self._lock:
            if self._summary is not None:
                self._summary.setdefault("usage", {})[kind] = usage["used"]
                today = self._summary.setdefault("today", {})
                today[kind] = int(today.get(kind) or 0) + 1
                self._remember()
        self._changed()

    def _note_refusal(self, info: dict) -> None:
        """The server refused: the screens say so now, not at the next read."""
        reason, kind = info.get("reason"), info.get("kind")
        _log.info("refused by the plan: %s %s", reason, kind)
        with self._lock:
            if self._summary is None:
                return
            blocked = self._summary.setdefault("blocked", {})
            if reason == "budget":
                blocked["all"] = "budget"
            elif reason in ("limit", "day") and kind in ("talk", "dictation", "agent"):
                blocked[kind] = reason
            else:
                return
        self._changed()

    def _call(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        token = self.token()
        try:
            with self._http() as http:
                r = http.request(method, settings.function_url(path), json=body,
                                 headers={**self._apikey(), "Authorization": f"Bearer {token}"})
        except httpx.HTTPError as e:
            # Offline, or HeyTora's server not answering: words, not a traceback.
            raise AccountError("Could not reach HeyTora - check the internet connection.") from e
        if r.status_code == 401:
            # The server no longer accepts this sign-in (account deleted, signed
            # out everywhere): forget it here too, so the app asks again.
            with self._lock:
                self._session, self._summary, self._tickets = None, None, {}
                store.clear()
            raise SignInNeeded("Your sign-in has ended - sign in to HeyTora again.")
        if r.status_code in (402, 429):
            info = _heytora_info(r)
            self._note_refusal(info)
            raise LimitReached(info)
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
