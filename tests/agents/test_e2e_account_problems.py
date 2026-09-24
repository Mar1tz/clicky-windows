"""APP-02: a busy or out-of-credit Google is said, not suffered in silence.

22 September: 77 requests failed with "Your prepayment credits are
depleted" over eleven hours; Tora went quiet, and could not even say why -
her voice runs on the same account. 24 September: a walkthrough failed on
two 503s ("high demand") and was reported as "I couldn't work out the steps
for that from this screen".
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from ai import gemini
from ai.gemini import GeminiError

# The messages exactly as the field log has them.
CREDITS = ("received 1011 (internal error) Your prepayment credits are depleted. "
           "Please go to AI Studio at https://ai.studio/projects to manage your "
           "project and billing")
BUSY_BODY = ('{"error": {"code": 503, "message": "This model is currently experiencing '
             'high demand. Spikes in demand are usually temporary. Please try again later.", '
             '"status": "UNAVAILABLE"}}')
QUOTA_BODY = ('{"error": {"code": 429, "message": "You exceeded your current quota, please '
              'check your plan and billing details."}}')
KEY_BODY = '{"error": {"code": 400, "message": "API key not valid. Please pass a valid API key."}}'


@pytest.fixture(autouse=True)
def _no_listener_leaks(monkeypatch):
    monkeypatch.setattr(gemini, "_problem_listener", None)
    monkeypatch.setattr(gemini, "_problem_active", False)
    monkeypatch.setattr(gemini, "BUSY_RETRY_DELAYS", (0.0, 0.0))
    monkeypatch.setattr(gemini, "api_key", lambda: "fake-key")


# ── naming what went wrong ───────────────────────────────────────────────────

@pytest.mark.parametrize("error, kind", [
    (RuntimeError(CREDITS), "billing"),
    (GeminiError(503, BUSY_BODY), "busy"),
    (GeminiError(429, QUOTA_BODY), "billing"),          # a quota is money, not load
    (GeminiError(429, '{"error": {"message": "Resource has been exhausted"}}'), "busy"),
    (GeminiError(400, KEY_BODY), "key"),
    (GeminiError(403, '{"error": {"message": "Forbidden"}}'), "key"),
    (OSError("[Errno 11001] getaddrinfo failed"), "network"),
    (TimeoutError("timed out during opening handshake"), "network"),
    (GeminiError(500, '{"error": {"message": "Internal error"}}'), ""),
    (ValueError("Expecting value: line 1 column 1"), ""),
])
def test_what_went_wrong_is_named(error, kind):
    assert gemini.problem_of(error) == kind


# ── a busy server is asked again ─────────────────────────────────────────────

def _client(responses):
    calls = []

    class Client:
        async def post(self, url, *, params, json, timeout):
            calls.append(url)
            status, body = responses[min(len(calls), len(responses)) - 1]
            return SimpleNamespace(status_code=status, text=body,
                                   json=lambda: {"ok": 1})

    return Client(), calls


def test_a_busy_server_is_asked_again_before_anything_gives_up(monkeypatch):
    client, calls = _client([(503, BUSY_BODY), (503, BUSY_BODY), (200, "")])
    monkeypatch.setattr(gemini, "client", lambda: client)
    assert asyncio.run(gemini.generate("m", [])) == {"ok": 1}
    assert len(calls) == 3


def test_a_busy_server_that_stays_busy_is_an_error_after_two_retries(monkeypatch):
    client, calls = _client([(503, BUSY_BODY)])
    monkeypatch.setattr(gemini, "client", lambda: client)
    with pytest.raises(GeminiError, match="high demand"):
        asyncio.run(gemini.generate("m", []))
    assert len(calls) == 3


def test_running_out_of_credit_is_not_retried(monkeypatch):
    client, calls = _client([(429, QUOTA_BODY)])
    monkeypatch.setattr(gemini, "client", lambda: client)
    with pytest.raises(GeminiError):
        asyncio.run(gemini.generate("m", []))
    assert len(calls) == 1, "asking again cannot top up an account"


def _stream_client(status_then_lines):
    class Client:
        opened = 0

        def stream(self, method, url, *, params, json, timeout):
            Client.opened += 1
            status, lines = status_then_lines[min(Client.opened, len(status_then_lines)) - 1]

            class Resp:
                status_code = status

                async def aiter_lines(self):
                    for line in lines:
                        yield line

                async def aread(self):
                    return BUSY_BODY.encode()

            class Ctx:
                async def __aenter__(self):
                    return Resp()

                async def __aexit__(self, *a):
                    return False

            return Ctx()

    return Client


def test_a_busy_stream_is_asked_again_before_anything_arrived(monkeypatch):
    line = 'data: {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}'
    Client = _stream_client([(503, []), (200, [line])])
    monkeypatch.setattr(gemini, "client", lambda: Client())

    async def collect():
        return [t async for t in gemini.stream("m", [])]

    assert asyncio.run(collect()) == ["hi"] and Client.opened == 2


# ── the account problem reaches the app, once, and clears itself ─────────────

def test_an_account_problem_is_reported_and_its_end_too(monkeypatch):
    heard = []
    gemini.on_problem(lambda kind, detail: heard.append(kind))
    gemini.report_problem(RuntimeError(CREDITS))
    gemini.report_problem(GeminiError(503, BUSY_BODY))       # load is not the account
    gemini.report_ok()
    gemini.report_ok()                                        # only once
    assert heard == ["billing", ""]


def test_a_refused_live_session_reports_it(monkeypatch):
    from ai.gemini_realtime import RealtimeConversation

    heard = []
    gemini.on_problem(lambda kind, detail: heard.append(kind))
    conv = RealtimeConversation.__new__(RealtimeConversation)
    conv._connecting = None
    conv._stale = lambda: True
    conv._system = ""

    async def refused(system_prompt):
        raise RuntimeError(CREDITS)

    conv._connect = refused
    assert asyncio.run(conv.connect("prompt")) is False
    assert heard == ["billing"]


def _manager():
    from companion_manager import CompanionManager

    mgr = CompanionManager.__new__(CompanionManager)
    mgr.emitted, mgr.shown = [], []
    mgr.sig_account_problem = SimpleNamespace(emit=lambda *a: mgr.emitted.append(a))
    mgr.sig_response_chunk = SimpleNamespace(emit=lambda t: None)
    mgr.sig_response_done = SimpleNamespace(emit=lambda t: mgr.shown.append(t))
    return mgr


def test_the_app_shows_it_once_not_seventy_seven_times():
    mgr = _manager()
    for _ in range(77):
        mgr._account_problem("billing", CREDITS)
    assert len(mgr.emitted) == 1
    kind, words = mgr.emitted[0]
    assert kind == "billing" and "credits have run out" in words and "ai.studio" in words


def test_it_is_shown_again_after_ten_minutes(monkeypatch):
    mgr = _manager()
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    mgr._account_problem("billing", CREDITS)
    now[0] += 601
    mgr._account_problem("billing", CREDITS)
    assert len(mgr.emitted) == 2


def test_when_google_answers_again_the_warning_comes_down():
    mgr = _manager()
    mgr._account_problem("billing", CREDITS)
    mgr._account_problem("", "")
    assert mgr.emitted[-1] == ("", "")
    mgr._account_problem("", "")                    # nothing to take down twice
    assert len(mgr.emitted) == 2


@pytest.mark.parametrize("error, words", [
    (RuntimeError(CREDITS), "credits have run out"),
    (GeminiError(503, BUSY_BODY), "overloaded"),
    (OSError("[Errno 11001] getaddrinfo failed"), "internet"),
])
def test_a_failed_request_shows_why_in_the_caption(error, words):
    mgr = _manager()
    assert mgr._show_failure(error) is True
    assert words in mgr.shown[-1]


def test_an_unknown_failure_is_left_to_the_error_toast():
    mgr = _manager()
    assert mgr._show_failure(ValueError("something else")) is False and mgr.shown == []


# ── walkthroughs stop blaming the screen ─────────────────────────────────────

def test_a_busy_planner_says_so(monkeypatch):
    from ai import gemini_planner

    async def busy(*a, **kw):
        raise GeminiError(503, BUSY_BODY)

    monkeypatch.setattr(gemini_planner.gemini, "generate_json", busy)
    guide = asyncio.run(gemini_planner.plan("install an extension"))
    assert not guide and guide.failure == "busy"
    assert "overloaded" in guide.preamble and "screen" not in guide.preamble


@pytest.mark.parametrize("failure, pro_tried", [("billing", False), ("key", False),
                                                ("network", False), ("busy", True),
                                                ("", True)])
def test_the_careful_model_is_only_tried_when_it_could_help(monkeypatch, failure, pro_tried):
    import companion_manager as cm
    from ai.gemini_planner import Guide

    calls = []

    async def plan(goal, *, fast=True, **kw):
        calls.append("fast" if fast else "pro")
        return Guide(goal=goal, feasible=False, failure=failure)

    monkeypatch.setattr(cm.gemini_planner, "plan", plan)
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    asyncio.run(mgr._plan_guide("g", "", "VS Code", []))
    assert calls == (["fast", "pro"] if pro_tried else ["fast"])


# ── the tray says it, without anyone having to read a log ───────────────────

def test_the_tray_warns_while_it_lasts():
    from ui.tray import TrayManager

    rebuilt, messages = [], []
    tray = SimpleNamespace(
        _account_problem="",
        _build_menu=lambda: rebuilt.append(True),
        _tray=SimpleNamespace(showMessage=lambda *a: messages.append(a[:2])),
    )
    TrayManager.set_account_problem(tray, "billing", "credits have run out")
    assert tray._account_problem == "billing" and rebuilt and messages
    TrayManager.set_account_problem(tray, "", "")
    assert tray._account_problem == "" and len(rebuilt) == 2 and len(messages) == 1
