"""
The one place Tora talks to Google.

Everything else in ai/ is a thin job-specific wrapper over this module: the
realtime conversation, the guide planner, the two vision passes, dictation,
the fallback voice and the embedding memory all share the model table and the
HTTP connection below.

Why a shared client at all: every Gemini call used to open its own
httpx.AsyncClient, which means a fresh TCP + TLS handshake — 200-400 ms of
nothing before a single byte of the question leaves the machine. One pooled
HTTP/2 client keeps the connection to generativelanguage.googleapis.com warm
between turns, so the second question of a session starts streaming roughly a
third of a second sooner than the first. warm_up() pays that cost once at
startup, while the user is still reading the tray notification.

Model roles (override any of them in .env):

  GEMINI_REALTIME_MODEL   the conversation itself — audio in, audio out,
                          tool calls, over a websocket
  GEMINI_PLANNER_MODEL    breaks "show me how to..." into steps
  GEMINI_VISION_FAST      first pass on any screen question
  GEMINI_VISION_DEEP      the careful look — reads charts, boards, diagrams
  GEMINI_STT_MODEL        dictation
  GEMINI_TTS_MODEL        the cascaded fallback voice
  GEMINI_EMBED_MODEL      memory + document recall
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import re
import logging
import os
from typing import Any, AsyncIterator, Callable, Optional

import httpx

_log = logging.getLogger("tora.gemini")

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
LIVE_WS = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)


def _m(env_key: str, default: str) -> str:
    return (os.getenv(env_key, "").strip() or default)


class Models:
    """The model behind each job. Read once at import; override via .env."""

    REALTIME    = _m("GEMINI_REALTIME_MODEL", "gemini-3.8-live")
    PLANNER     = _m("GEMINI_PLANNER_MODEL",  "gemini-3.1-pro-preview")
    VISION_FAST = _m("GEMINI_VISION_FAST",    "gemini-3.8-flash")
    VISION_DEEP = _m("GEMINI_VISION_DEEP",    "gemini-3.1-pro-preview")
    STT         = _m("GEMINI_STT_MODEL",      "gemini-3.5-transcribe-live")
    TTS         = _m("GEMINI_TTS_MODEL",      "gemini-3.1-flash-tts-preview")
    EMBED       = _m("GEMINI_EMBED_MODEL",    "gemini-embedding-001")

    # Models that refuse thinkingBudget=0 — asking anyway is a hard 400
    # ("This model only works in thinking mode"), so the fast path has to
    # know which models it may actually rush.
    THINKING_ONLY = ("-pro", "pro-preview")

    @classmethod
    def can_skip_thinking(cls, model: str) -> bool:
        return not any(marker in model for marker in cls.THINKING_ONLY)

    @classmethod
    def takes_level(cls, model: str) -> bool:
        """Gemini 3 is told how hard to think by a level, not a budget (SP-04)."""
        return model.startswith("gemini-3")

    @classmethod
    def all_roles(cls) -> dict[str, str]:
        return {
            "realtime":    cls.REALTIME,
            "planner":     cls.PLANNER,
            "vision_fast": cls.VISION_FAST,
            "vision_deep": cls.VISION_DEEP,
            "stt":         cls.STT,
            "tts":         cls.TTS,
            "embed":       cls.EMBED,
        }


def api_key() -> str:
    """The user's own key, in "own key" mode. Read live so the API-keys
    dialog takes effect without a restart. In "HeyTora account" mode there
    is no key on this machine at all - see _route."""
    from config import cfg
    return cfg.google_api_key or ""


def own_key() -> bool:
    """Straight to Google with the user's key (True), or through HeyTora's
    server on their plan (False)."""
    from config import cfg
    return cfg.access_mode() == "own_key"


# What a request is for, so HeyTora's server counts it against the right
# part of the plan: "talk" (default), "dictation" or "agent".
_kind: contextvars.ContextVar[str] = contextvars.ContextVar("heytora_kind", default="talk")


@contextlib.contextmanager
def request_kind(kind: str):
    token = _kind.set(kind)
    try:
        yield
    finally:
        _kind.reset(token)


_SIGNIN_BODY = json.dumps({"error": {"code": 401, "status": "HEYTORA_SIGNIN",
                                     "message": "Sign in to HeyTora to keep talking."}})


async def _route(answer: bool = False) -> tuple[str, dict, dict]:
    """(root, query params, headers) for a REST call: Google with the user's
    own key, or HeyTora's server with their sign-in - the business key is
    added there and never reaches this machine.

    `answer`: a call that makes the model answer (generateContent and its
    streamed kind). On HeyTora those run on a window ticket - this
    question's talk message, or the agent's - and the server refuses them
    without one. Embeddings and the model list need none.
    """
    if own_key():
        return API_ROOT, {"key": api_key()}, {}
    from account.service import AccountError, LimitReached, account
    kind = _kind.get()
    try:
        token = await account().atoken()
        headers = {"Authorization": f"Bearer {token}", "x-heytora-kind": kind}
        if answer:
            headers["x-heytora-ticket"] = await account().aticket(
                "agent" if kind == "agent" else "talk")
    except LimitReached as e:
        raise GeminiError(402, json.dumps({"error": {"code": 402, "status": "HEYTORA_LIMIT",
                                                     "message": str(e), "heytora": e.info}})) from e
    except AccountError as e:
        raise GeminiError(401, json.dumps({"error": {"code": 401, "status": "HEYTORA_SIGNIN",
                                                     "message": str(e)}})) from e
    return account().proxy_root(), {}, headers


def _ticket_ended(status: int, body: str) -> bool:
    """HeyTora says the window this call ran on has ended (or was never this
    app's): worth one more try with a fresh ticket."""
    if status != 402 or "heytora_ticket" not in (body or "").lower() or own_key():
        return False
    from account.service import account
    account().drop_ticket("agent" if _kind.get() == "agent" else "talk")
    _log.info("window ticket refused - asking for the window again")
    return True


class GeminiError(RuntimeError):
    """An API error with the message Google actually sent, not just a status.

    httpx's raise_for_status() gives "Client error '400 Bad Request'", which
    tells a user nothing. Google puts the real reason — bad key, quota, model
    not found, unsupported argument — in the response body, so that is what
    gets raised and shown in the error toast.
    """

    def __init__(self, status: int, body: str, model: str = ""):
        self.status = status
        self.body = body
        self.model = model
        detail = body
        try:
            detail = json.loads(body)["error"]["message"]
        except Exception:
            detail = (body or "")[:300]
        where = f" [{model}]" if model else ""
        super().__init__(f"Gemini {status}{where}: {detail}")


# ── Shared connection ────────────────────────────────────────────────────────

_client: Optional[httpx.AsyncClient] = None
_client_loop: Optional[asyncio.AbstractEventLoop] = None


def client() -> httpx.AsyncClient:
    """The process-wide pooled client, created on first use.

    Bound to the loop that first asked for it. The manager runs everything on
    one background loop, but the setup wizard and diagnostics spin up their
    own throwaway loops — a client built on a dead loop raises on its next
    request, so it is rebuilt whenever the loop underneath it changed.
    """
    global _client, _client_loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if _client is not None and _client_loop is not loop:
        # Different loop: abandon the old pool rather than awaiting it here
        # (closing needs the loop it was made on, which may already be gone).
        _client = None

    if _client is None:
        _client = httpx.AsyncClient(
            http2=True,
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0),
            limits=httpx.Limits(
                max_connections=8,
                max_keepalive_connections=8,
                keepalive_expiry=300.0,
            ),
            headers={"user-agent": "Tora/2.0 (+gemini)"},
        )
        _client_loop = loop
    return _client


async def aclose() -> None:
    """Close the shared pool. Called on shutdown."""
    global _client, _client_loop
    c, _client, _client_loop = _client, None, None
    if c is not None:
        try:
            await c.aclose()
        except Exception:
            _log.debug("closing shared gemini client failed", exc_info=True)


async def warm_up() -> bool:
    """Open the TLS connection before the user's first question needs it.

    Fired at startup. A cheap GET is enough — what matters is that the socket
    and TLS session are established and pooled by the time someone speaks.
    """
    if not own_key() or not api_key():
        return False                     # account mode: the first call warms it
    try:
        r = await client().get(
            f"{API_ROOT}/models/{Models.VISION_FAST}",
            params={"key": api_key()},
            timeout=8.0,
        )
        return r.status_code == 200
    except Exception:
        _log.debug("warm-up failed (harmless)", exc_info=True)
        return False


# ── REST ─────────────────────────────────────────────────────────────────────

# A pooled HTTP/2 connection the server has already closed fails the next
# request on it with a protocol error - "Invalid input RECV_PING in state
# CLOSED". The pool drops that connection, so one immediate retry gets a
# fresh one. Seen in the field on the planner (21 September), where it
# turned a two-second first step into the pro model's 10-20 s fallback.
# Timeouts are not retried: that would double a wait that was already long.
_STALE_CONNECTION = (httpx.ProtocolError, httpx.NetworkError)

# ── What kind of failure it is (APP-02) ──────────────────────────────────────
#
# 22 September: 77 requests failed with "Your prepayment credits are
# depleted" over eleven hours, and Tora simply went quiet - the voice uses
# the same account, so she could not even say why. 24 September: a
# walkthrough failed on two 503s ("high demand") and was reported as "I
# couldn't work out the steps from this screen". Each kind needs its own
# answer, so each is named.

# A per-model limit - "generate_requests_per_model_per_day, limit: 100" on the
# TTS model, 24 Sep. Its message ALSO says "exceeded your current quota,
# please check your plan and billing", so it was read as billing: "your
# credits have run out" when they had not, and an account warning that
# every other model's next success took down again (LG-04). It is checked
# first, and it is not an account problem: that one model is resting.
_QUOTA = ("quota exceeded for metric", "per_model_per_day", "requests_per_day",
          "perday", "per_day")
_BILLING = ("prepayment credits", "credits are depleted", "check your plan and billing",
            "exceeded your current quota", "billing", "payment required")
_KEY = ("api key not valid", "api_key_invalid", "api key expired", "invalid api key",
        "api key was reported as leaked", "permission denied", "permission_denied")
_BUSY = ("high demand", "overloaded", "unavailable", "try again later",
         "resource has been exhausted", "rate limit", "too many requests")
_NETWORK = ("getaddrinfo failed", "name or service not known", "timed out during opening handshake",
            "network is unreachable", "connection refused", "no route to host")


def problem_of(error: Any) -> str:
    """"plan", "signin", "quota", "billing", "key", "service", "busy",
    "network", or "" - what went wrong, in the terms a person can act on.
    Takes an exception or a message. "service": Google refused HeyTora's
    own account (a HeyTora plan) - nothing for the user to fix."""
    text = str(error or "").lower()
    status = getattr(error, "status", 0) or 0
    body = str(getattr(error, "body", "") or "").lower()
    # HeyTora's own server first: the plan's allowance for the month is used
    # up, or the user must sign in again. Neither is Google's billing.
    named = type(error).__name__
    # Its other refusals - a window ended (HEYTORA_TICKET), a model or a
    # request it does not forward - are the plan's rules too, shown in the
    # server's own words; never "your credits ran out" or "bad key".
    if named == "LimitReached" or any(f"heytora_{w}" in body or f"heytora_{w}" in text
                                      for w in ("limit", "ticket", "model", "request")):
        return "plan"
    if named in ("SignInNeeded", "NotConfigured") or "heytora_signin" in body \
            or "heytora_signin" in text:
        return "signin"
    google_side = ""
    if any(w in text or w in body for w in _QUOTA):
        google_side = "quota"
    elif any(w in text for w in _BILLING) or status == 402:
        google_side = "billing"
    elif any(w in text for w in _KEY) or status in (401, 403):
        google_side = "key"
    if google_side:
        # FS-14: on a HeyTora plan the account Google refused is HeyTora's,
        # not the user's. 9 Oct, 13:45-14:20: "prepayment credits are
        # depleted" on HeyTora's business key, and every plan user was told
        # to top up Gemini credits in AI Studio - credits they do not have.
        return google_side if own_key() else "service"
    if status in (429, 503) or any(w in text for w in _BUSY):
        return "busy"
    if isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout, ConnectionError)) \
            or any(w in text for w in _NETWORK):
        return "network"
    return ""


_RETRY_IN = re.compile(r"retry in\s+(?:(\d+)h)?(?:(\d+)m(?!s))?(?:([\d.]+)s)?", re.I)
_RETRY_DELAY = re.compile(r'"retryDelay"\s*:\s*"([\d.]+)s"')


def retry_after(error: Any) -> Optional[float]:
    """Seconds until Google says to try again ("Please retry in 2h56m56.7s",
    or a RetryInfo "retryDelay"), or None when it does not say."""
    text = f"{error} {getattr(error, 'body', '')}"
    m = _RETRY_DELAY.search(text)
    if m:
        return float(m.group(1))
    m = _RETRY_IN.search(text)
    if m and any(m.groups()):
        h, mins, secs = (float(g) if g else 0.0 for g in m.groups())
        return h * 3600 + mins * 60 + secs
    return None


# Told about account problems - out of credit, key refused - wherever they
# happen: a request, a stream, the live session. The app shows them; see
# CompanionManager._account_problem. One listener; set by the app.
_problem_listener: Optional[Callable[[str, str], None]] = None


def on_problem(listener: Optional[Callable[[str, str], None]]) -> None:
    global _problem_listener
    _problem_listener = listener


_problem_active = False


def report_problem(error: Any) -> str:
    """Classify `error` and, if it is about the account, tell the app.
    Returns the kind. Never raises - a report must not become the failure."""
    global _problem_active
    kind = problem_of(error)
    if kind in ("billing", "key", "plan", "signin", "service") and _problem_listener is not None:
        _problem_active = True
        try:
            _problem_listener(kind, str(error)[:300])
        except Exception:  # noqa: BLE001
            _log.debug("problem listener failed", exc_info=True)
    return kind


def report_ok() -> None:
    """A request got through: an account problem, if there was one, is over
    (the user topped up, or fixed the key). The app takes its warning down."""
    global _problem_active
    if _problem_active and _problem_listener is not None:
        _problem_active = False
        try:
            _problem_listener("", "")
        except Exception:  # noqa: BLE001
            _log.debug("problem listener failed", exc_info=True)


# A busy server is asked again, twice, before anything gives up: 1 s, then 2 s.
BUSY_RETRY_DELAYS = (1.0, 2.0)


async def _post(path: str, body: dict, timeout: float) -> httpx.Response:
    async def once() -> httpx.Response:
        root, params, headers = await _route(answer=path.endswith(":generateContent"))
        extra = {"headers": headers} if headers else {}      # own key: exactly as before
        try:
            return await client().post(f"{root}/{path}", params=params, json=body,
                                       timeout=timeout, **extra)
        except _STALE_CONNECTION as e:
            _log.info("retrying once on a fresh connection after %s: %s",
                      type(e).__name__, e)
            return await client().post(f"{root}/{path}", params=params, json=body,
                                       timeout=timeout, **extra)

    try:
        r = await once()
        if _ticket_ended(r.status_code, r.text):
            r = await once()
    except GeminiError as e:              # HeyTora said no before Google was asked
        report_problem(e)
        raise
    for delay in BUSY_RETRY_DELAYS:
        if r.status_code not in (429, 503) or problem_of(r.text) != "busy":
            break
        _log.info("Google is busy (%d) - asking again in %.0fs", r.status_code, delay)
        await asyncio.sleep(delay)
        r = await once()
    if r.status_code != 200:
        report_problem(GeminiError(r.status_code, r.text))
    else:
        report_ok()
    return r


def _url(model: str, method: str) -> str:
    """The path of a model call; _route says which server it goes to."""
    return f"models/{model}:{method}"


# What a level means to a model that still takes a budget.
_LEVEL_BUDGET = {"minimal": 0, "low": 1024, "medium": 4096, "high": 8192}


def thinking(budget: "Optional[int | str]", model: str) -> dict:
    """generationConfig fragment that caps deliberation.

    budget=0 on a flash model is the single biggest latency win available:
    it removes a whole silent reasoning pass (~240 tokens, roughly half a
    second) from an answer that never needed one. Pro models reject it, so
    for them this is a no-op and they think as much as they want to.

    Gemini 3 takes a thinking LEVEL - low, medium or high, medium if unsaid -
    and no budget. Sent thinkingBudget 0, gemini-3.8-flash still thought on
    14 of 23 test screens, up to 338 tokens, and 613 on a real one; the pro
    model spent 2,050 on a 1,536 budget, $0.037 for one look (SP-01). So
    budget 0 - as little as possible - is "low" on Gemini 3, the least
    either model takes. A caller may name a level instead of a budget; a
    model that still takes budgets gets the budget it means.
    """
    if budget is None:
        return {}
    if isinstance(budget, str):
        if Models.takes_level(model):
            return {"thinkingConfig": {"thinkingLevel": budget}}
        budget = _LEVEL_BUDGET.get(budget, 1024)
    if budget == 0 and Models.takes_level(model):
        return {"thinkingConfig": {"thinkingLevel": "low"}}
    if budget == 0 and not Models.can_skip_thinking(model):
        return {}
    return {"thinkingConfig": {"thinkingBudget": budget}}


async def generate(
    model: str,
    contents: list,
    *,
    system: str = "",
    max_tokens: int = 1024,
    temperature: float = 0.7,
    think: "Optional[int | str]" = None,
    schema: Optional[dict] = None,
    tools: Optional[list] = None,
    audio_voice: Optional[str] = None,
    media_resolution: Optional[str] = None,
    timeout: float = 120.0,
) -> dict:
    """One non-streaming generateContent call. Returns the raw response.

    audio_voice switches the response from text to speech — that is how the
    TTS model is addressed, since it shares this same endpoint.
    media_resolution - low, medium or high - is how many tokens an image
    becomes (SP-06); unsaid, the model's default.
    """
    gen: dict[str, Any] = {
        "maxOutputTokens": max_tokens,
        "temperature": temperature,
    }
    gen.update(thinking(think, model))
    if media_resolution:
        gen["mediaResolution"] = "MEDIA_RESOLUTION_" + media_resolution.upper()
    if schema is not None:
        gen["responseMimeType"] = "application/json"
        gen["responseSchema"] = schema
    if audio_voice:
        gen["responseModalities"] = ["AUDIO"]
        gen["speechConfig"] = {
            "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": audio_voice}}
        }

    body: dict[str, Any] = {"contents": contents, "generationConfig": gen}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if tools:
        body["tools"] = tools

    r = await _post(_url(model, "generateContent"), body, timeout)
    if r.status_code != 200:
        raise GeminiError(r.status_code, r.text, model)
    out = r.json()
    timing = server_timing((getattr(r, "headers", None) or {}).get("server-timing", ""))
    if timing and isinstance(out, dict):
        out["_server_timing"] = timing
    return out


def server_timing(header: str) -> dict:
    """FS-06: HeyTora's server says where a forwarded answer's time went -
    "auth;dur=120, consume;dur=80, google;dur=2100, total;dur=2400" - as
    {"auth": 120, ...} in ms. Empty for Google itself, or a bad header."""
    out = {}
    for part in (header or "").split(","):
        name, _, rest = part.strip().partition(";")
        if rest.startswith("dur="):
            try:
                out[name.strip()] = float(rest[4:])
            except ValueError:
                pass
    return out


async def generate_json(
    model: str,
    contents: list,
    schema: dict,
    *,
    system: str = "",
    max_tokens: int = 2048,
    think: "Optional[int | str]" = None,
    media_resolution: Optional[str] = None,
    timeout: float = 120.0,
    usage: Optional[dict] = None,
) -> Optional[dict]:
    """generateContent constrained to a JSON schema. None if it came back
    unparseable — every caller has a sane path for 'no structured answer'.
    `usage`, when given, is filled with the call's usageMetadata (tokens in,
    out and thinking), so a caller can log what an answer cost."""
    resp = await generate(
        model, contents,
        system=system, max_tokens=max_tokens, temperature=0.2,
        think=think, schema=schema, media_resolution=media_resolution, timeout=timeout,
    )
    if usage is not None:
        usage.update(resp.get("usageMetadata") or {})
        # As numbers, so a look that took two calls adds them up (_summed).
        for name, ms in (resp.get("_server_timing") or {}).items():
            usage[f"server_{name}_ms"] = ms
    text = first_text(resp)
    if not text:
        return None
    data = parse_json_lenient(text)
    if data is None:
        # Say what came back, once, instead of a bare "Expecting value" —
        # this was how the guide's vision judge failed silently.
        fin = ""
        try:
            fin = resp.get("candidates", [{}])[0].get("finishReason", "")
        except Exception:
            pass
        _log.warning("generate_json: unparseable reply (finish=%s): %.160r",
                     fin, text)
    return data


def parse_json_lenient(text: str) -> Optional[dict]:
    """json.loads that survives what models actually emit: a ```json fence,
    prose before or after the object, a trailing comma, or a reply cut off
    at max_tokens (in which case the outermost object is still recovered
    when it closed)."""
    t = text.strip()
    try:
        out = json.loads(t)
        return out if isinstance(out, dict) else None
    except Exception:
        pass
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    i, j = t.find("{"), t.rfind("}")
    if i == -1 or j <= i:
        return None
    body = t[i:j + 1]
    for cand in (body, body.replace(",}", "}").replace(",]", "]")):
        try:
            out = json.loads(cand)
            return out if isinstance(out, dict) else None
        except Exception:
            continue
    return None


async def stream(
    model: str,
    contents: list,
    *,
    system: str = "",
    max_tokens: int = 1024,
    temperature: float = 0.7,
    think: "Optional[int | str]" = None,
    schema: Optional[dict] = None,
    timeout: float = 120.0,
) -> AsyncIterator[str]:
    """Server-sent-events streaming. Yields text as it arrives.

    With `schema`, the text is JSON constrained to it, arriving in pieces -
    which is how a walkthrough's first step can be used before the rest of
    the plan has been written (GW-08)."""
    gen: dict[str, Any] = {
        "maxOutputTokens": max_tokens,
        "temperature": temperature,
    }
    gen.update(thinking(think, model))
    if schema is not None:
        gen["responseMimeType"] = "application/json"
        gen["responseSchema"] = schema
    body: dict[str, Any] = {"contents": contents, "generationConfig": gen}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}

    async for obj in _sse(model, body, timeout):
        for cand in obj.get("candidates", []):
            for part in cand.get("content", {}).get("parts", []):
                # Gemini 3 streams its reasoning as parts flagged
                # "thought". Those are not the answer and must never be
                # spoken or shown — only the real text is yielded.
                if part.get("thought"):
                    continue
                text = part.get("text", "")
                if text:
                    yield text


async def _sse(model: str, body: dict, timeout: float) -> AsyncIterator[dict]:
    """The JSON objects of a streamGenerateContent reply, as they arrive.

    A dead pooled connection (see _STALE_CONNECTION) is retried once, and a
    busy server twice (BUSY_RETRY_DELAYS) - but only if nothing has arrived
    yet: a stream that has started cannot be replayed without saying its
    first part twice. An account problem is reported (report_problem).
    """
    stale_left = 1
    ticket_left = 1
    busy_delays = list(BUSY_RETRY_DELAYS)
    while True:
        started = False
        try:
            root, params, headers = await _route(answer=True)
            async with client().stream(
                "POST",
                f"{root}/{_url(model, 'streamGenerateContent')}",
                params={**params, "alt": "sse"},
                json=body,
                timeout=timeout,
                **({"headers": headers} if headers else {}),
            ) as resp:
                if resp.status_code != 200:
                    raise GeminiError(resp.status_code,
                                      (await resp.aread()).decode(), model)
                report_ok()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    started = True
                    yield obj
            return
        except _STALE_CONNECTION as e:
            if started or stale_left == 0:
                raise
            stale_left -= 1
            _log.info("retrying the stream once on a fresh connection after "
                      "%s: %s", type(e).__name__, e)
        except GeminiError as e:
            if not started and ticket_left and _ticket_ended(e.status, e.body):
                ticket_left = 0
                continue
            if not started and busy_delays and problem_of(e) == "busy":
                delay = busy_delays.pop(0)
                _log.info("Google is busy (%d) - asking again in %.0fs",
                          e.status, delay)
                await asyncio.sleep(delay)
                continue
            report_problem(e)
            raise


async def stream_audio(
    model: str,
    contents: list,
    *,
    voice: str,
    max_tokens: int = 8192,
    temperature: float = 1.0,
    timeout: float = 60.0,
) -> AsyncIterator[tuple[bytes, int]]:
    """Speech from the TTS model as it is generated: (pcm16, rate) pieces.

    Measured (GW-08): the first piece arrives in about 0.9 s and the whole
    line in about 2.1 s. Asked for in one piece, the same line took 3.3-4.1 s
    before a single sound could be played.
    """
    import base64
    import re as _re

    body: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": temperature,
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}
            },
        },
    }
    async for obj in _sse(model, body, timeout):
        for cand in obj.get("candidates", []):
            for part in cand.get("content", {}).get("parts", []):
                blob = part.get("inlineData") or part.get("inline_data")
                if not blob or not blob.get("data"):
                    continue
                mime = blob.get("mimeType") or blob.get("mime_type") or ""
                m = _re.search(r"rate=(\d+)", mime)
                yield base64.b64decode(blob["data"]), int(m.group(1)) if m else 24000


async def embed(
    texts: list[str],
    *,
    task: str = "RETRIEVAL_DOCUMENT",
    dims: int = 768,
    timeout: float = 30.0,
) -> list[list[float]]:
    """Embed a batch of strings in one round trip.

    768 dimensions rather than the 3072 default: the quality difference is
    invisible for "which of these twenty notes is relevant", and it keeps the
    on-disk memory index a quarter of the size.
    """
    if not texts:
        return []
    body = {
        "requests": [
            {
                "model": f"models/{Models.EMBED}",
                "content": {"parts": [{"text": t}]},
                "taskType": task,
                "outputDimensionality": dims,
            }
            for t in texts
        ]
    }
    r = await _post(_url(Models.EMBED, "batchEmbedContents"), body, timeout)
    if r.status_code != 200:
        raise GeminiError(r.status_code, r.text, Models.EMBED)
    return [e.get("values", []) for e in r.json().get("embeddings", [])]


# ── Response helpers ─────────────────────────────────────────────────────────

def first_text(resp: dict) -> str:
    """Concatenate the answer text of a generateContent response."""
    out = []
    for cand in resp.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            if part.get("thought"):
                continue
            if "text" in part:
                out.append(part["text"])
    return "".join(out).strip()


def first_audio(resp: dict) -> tuple[bytes, int]:
    """(pcm16_bytes, sample_rate) from a TTS response. (b"", 0) if none.

    The mime type comes back as "audio/l16; rate=24000; channels=1" — raw
    signed 16-bit PCM, no container, so the rate has to be read out of the
    header rather than assumed.
    """
    import base64
    import re as _re

    for cand in resp.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            blob = part.get("inlineData") or part.get("inline_data")
            if not blob:
                continue
            mime = blob.get("mimeType") or blob.get("mime_type") or ""
            m = _re.search(r"rate=(\d+)", mime)
            rate = int(m.group(1)) if m else 24000
            return base64.b64decode(blob.get("data", "")), rate
    return b"", 0


def image_part(jpeg_b64: str) -> dict:
    return {"inlineData": {"mimeType": "image/jpeg", "data": jpeg_b64}}


def text_part(text: str) -> dict:
    return {"text": text}


async def health_check() -> bool:
    """Can Gemini be reached: the own key works, or the account is signed in
    and HeyTora's server answers."""
    if not own_key():
        try:
            from account.service import account
            await account().asummary(force=True)
            return True
        except Exception:
            return False
    if not api_key():
        return False
    try:
        r = await client().get(
            f"{API_ROOT}/models", params={"key": api_key()}, timeout=8.0
        )
        return r.status_code == 200
    except Exception:
        return False
