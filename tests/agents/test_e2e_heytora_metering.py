"""HeyTora plans that hold (2 Oct 2026): the server decides, not the app.

0003 counted what the app said it was doing - and the app is easy to
change. Now a talk message is a talk WINDOW the server opens (Free 10 min),
an agent message an agent window; the voice pass and every answer made for
a question run on that window's ticket and end with it; and every
forwarded call's real cost counts against a hidden monthly ceiling.

The app's side is tested against the fake server; the server's rules
(rules.ts) with Node; the database's (0004) by reading the migration -
and all three were run live against the real project (scratch scripts,
throwaway users) on 2 Oct 2026.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.agents.test_e2e_heytora_account import _signed_in, heytora  # noqa: F401 - fixture

ROOT = Path(__file__).resolve().parents[2]


def _ticket_calls(h):
    return [b for _m, p, _h, b in h.fake.calls if p.endswith("/heytora/ticket")]


# -- a question is one talk message, however it is answered ------------------------------------------------

def test_a_question_answered_without_the_voice_opens_one_window(heytora):
    acc = heytora.account
    _signed_in(heytora)
    acc.new_talk()
    first = acc.ticket("talk")
    again = acc.ticket("talk")                      # the next call for the same question
    assert first == again and first.startswith("talk.")
    assert _ticket_calls(heytora) == [{"kind": "talk", "new": True}]
    assert heytora.fake.opened["talk"] == 1


def test_a_voice_question_is_counted_once_not_twice(heytora):
    """The pass opens the window; the calls made for the question ride on it."""
    acc = heytora.account
    _signed_in(heytora)
    acc.new_talk()
    vp = acc.voice_pass("talk", "gemini-3.8-live")
    assert acc.ticket("talk") == vp.ticket, "screen reading etc. use the pass's window"
    assert heytora.fake.opened["talk"] == 1 and _ticket_calls(heytora) == []


def test_a_call_before_the_pass_still_counts_once(heytora):
    acc = heytora.account
    _signed_in(heytora)
    acc.new_talk()
    acc.ticket("talk")                              # e.g. the screen read first
    acc.voice_pass("talk", "gemini-3.8-live")
    passes = [b for _m, p, _h, b in heytora.fake.calls if p.endswith("/voice-pass")]
    assert passes[-1]["new"] is False, "the pass rides on this question's window"
    assert heytora.fake.opened["talk"] == 1


def test_the_next_question_is_the_next_talk_message(heytora):
    acc = heytora.account
    _signed_in(heytora)
    acc.new_talk()
    one = acc.voice_pass("talk", "gemini-3.8-live").ticket
    acc.new_talk()
    two = acc.voice_pass("talk", "gemini-3.8-live").ticket
    assert one != two and heytora.fake.opened["talk"] == 2


def test_a_reconnect_rides_on_the_open_window(heytora):
    """Pro keeps the voice open: a dropped session reconnects in its window."""
    acc = heytora.account
    _signed_in(heytora)
    acc.voice_pass("talk", "gemini-3.8-live")
    acc.voice_pass("talk", "gemini-3.8-live")       # no new question: a reconnect
    passes = [b["new"] for _m, p, _h, b in heytora.fake.calls if p.endswith("/voice-pass")]
    assert passes == [True, False] and heytora.fake.opened["talk"] == 1


@pytest.mark.parametrize("reason", ["passes", "window"])
def test_a_used_up_window_starts_the_next(heytora, reason):
    acc = heytora.account
    _signed_in(heytora)
    acc.voice_pass("talk", "gemini-3.8-live")
    heytora.fake.pass_refusal = reason
    acc.voice_pass("talk", "gemini-3.8-live")
    passes = [b["new"] for _m, p, _h, b in heytora.fake.calls if p.endswith("/voice-pass")]
    assert passes == [True, False, True] and heytora.fake.opened["talk"] == 2


def test_an_ending_window_is_asked_for_again_and_never_stretched(heytora):
    acc = heytora.account
    _signed_in(heytora)
    acc.ticket("talk")
    held = acc._tickets["talk"]
    acc._tickets["talk"] = (held[0], 0.0)            # nearly over, as the app sees it
    assert acc.ticket("talk") == held[0], "still open on the server: the same window, no charge"
    assert len(_ticket_calls(heytora)) == 2 and heytora.fake.opened["talk"] == 1
    window, _ends = heytora.fake.windows["talk"]
    heytora.fake.windows["talk"] = (window, 0.0)     # over on the server too
    acc._tickets["talk"] = (held[0], 0.0)
    assert acc.ticket("talk") != held[0] and heytora.fake.opened["talk"] == 2


# -- what the app sends --------------------------------------------------------------------------------------

def test_answers_carry_the_window_ticket_and_nothing_else_needs_one(heytora):
    from ai import gemini
    _signed_in(heytora)
    root, params, headers = asyncio.run(gemini._route())
    assert "x-heytora-ticket" not in headers and _ticket_calls(heytora) == [], \
        "the model list and embeddings never spend a talk message"
    _r, _p, answer = asyncio.run(gemini._route(answer=True))
    assert answer["x-heytora-ticket"].startswith("talk.")

    async def agent():
        with gemini.request_kind("agent"):
            return await gemini._route(answer=True)
    assert asyncio.run(agent())[2]["x-heytora-ticket"].startswith("agent.")


def test_the_servers_other_refusals_read_as_the_plan():
    from ai import gemini
    for status, code in ((402, "HEYTORA_TICKET"), (403, "HEYTORA_MODEL"), (413, "HEYTORA_REQUEST")):
        err = gemini.GeminiError(status, json.dumps({"error": {"code": status, "status": code,
                                                               "message": "That conversation has ended."}}))
        assert gemini.problem_of(err) == "plan", f"{code}: not 'credits ran out' or 'bad key'"


def test_a_refused_window_is_a_plan_problem_not_a_crash(heytora):
    from ai import gemini
    _signed_in(heytora)
    heytora.fake.windows.clear()

    def refuse(_request):
        import httpx
        return httpx.Response(402, json={"error": {
            "code": 402, "status": "HEYTORA_LIMIT", "heytora": {"reason": "limit", "kind": "talk",
                                                              "limit": 25, "plan": "free"},
            "message": "You've used all 25 talk messages on the Free plan this month."}})
    import httpx
    heytora.account._transport = httpx.MockTransport(
        lambda r: refuse(r) if r.url.path.endswith("/ticket") else heytora.fake(r))
    with pytest.raises(gemini.GeminiError) as caught:
        asyncio.run(gemini._route(answer=True))
    assert caught.value.status == 402 and gemini.problem_of(caught.value) == "plan"
    assert "25 talk messages" in str(caught.value)


# -- the manager: a new question says so before the voice is asked for --------------------------------------

def _capture_manager(heytora, ready: bool):
    from companion_manager import CompanionManager
    m = CompanionManager.__new__(CompanionManager)
    order = []

    def submit(coro):
        order.append((coro.__name__, heytora.account._new_talk))
        coro.close()
    m._submit = submit
    m._realtime = SimpleNamespace(ready=True, connecting=False, cancel=lambda: None) if ready else None
    m._rt_audio = []
    m._listener = SimpleNamespace(set_chunk_sink=lambda _f: None, start_recording=lambda: None)
    m._emit_state = lambda _s: None
    m._get_realtime = lambda: None
    m._request_serial = 0
    m.sig_error = SimpleNamespace(emit=lambda _e: None)
    return m, order


def test_a_new_free_question_opens_a_new_talk_message_before_the_pass(heytora, monkeypatch):
    from config import cfg
    _signed_in(heytora)
    monkeypatch.setattr(cfg, "realtime", lambda: True)
    m, order = _capture_manager(heytora, ready=False)
    m._begin_capture()
    assert ("_realtime_connect", True) in order, "flagged before the voice pass is asked for"


def test_a_follow_up_on_the_open_session_rides_on_it(heytora, monkeypatch):
    import time
    from config import cfg
    _signed_in(heytora)
    monkeypatch.setattr(cfg, "realtime", lambda: True)
    m, order = _capture_manager(heytora, ready=True)
    m._answered_at = time.monotonic() - 5            # Tora answered five seconds ago
    m._begin_capture()
    assert heytora.account._new_talk is False
    assert not any(name == "_open_talk_window" for name, _f in order)


def test_unlimited_talk_counts_every_question_too(heytora, monkeypatch):
    """Pro/Max keep the voice open, so no pass marks a question: the window
    opens as it is asked - and the plan moves with use (reported 2 Oct)."""
    import time
    from config import cfg
    _signed_in(heytora)
    heytora.fake.limits = {"talk": None, "dictation": None, "agent": 150}
    heytora.account.summary(force=True)
    monkeypatch.setattr(cfg, "realtime", lambda: True)
    m, order = _capture_manager(heytora, ready=True)
    m._answered_at = time.monotonic() - 600          # the last answer was ten minutes ago
    m._begin_capture()
    assert ("_open_talk_window", True) in order, "a new talk message, opened now"
    asyncio.run(m._open_talk_window())
    assert heytora.fake.opened["talk"] == 1 and heytora.account._new_talk is False


def test_a_question_on_a_used_up_plan_is_said_not_swallowed(heytora, monkeypatch):
    from ai import gemini
    _signed_in(heytora)
    said = []
    monkeypatch.setattr(gemini, "report_problem", lambda e: said.append(str(e)) or "plan")
    heytora.account._transport = __import__("httpx").MockTransport(lambda r: __import__("httpx").Response(
        402, json={"error": {"code": 402, "status": "HEYTORA_LIMIT", "heytora": {
            "reason": "limit", "kind": "talk", "limit": 25, "plan": "free"}, "message": "used up"}})
        if r.url.path.endswith("/ticket") else heytora.fake(r))
    m, _order = _capture_manager(heytora, ready=False)
    heytora.account.new_talk()
    asyncio.run(m._open_talk_window())
    assert said and "25 talk messages" in said[0]


# -- the wake word is not a dictation --------------------------------------------------------------------------

def test_a_wake_word_check_is_its_own_kind(heytora):
    from ai import gemini
    from ai.gemini import Models
    from ai.gemini_live import LiveSession
    _signed_in(heytora)

    async def wake_check():
        with gemini.request_kind("wake"):
            return await LiveSession(Models.STT, {})._address()
    asyncio.run(wake_check())
    asyncio.run(LiveSession(Models.STT, {})._address())
    kinds = [b["kind"] for _m, p, _h, b in heytora.fake.calls if p.endswith("/voice-pass")]
    assert kinds == ["wake", "dictation"], "a dictation is still a dictation"


def test_a_refused_wake_check_pauses_the_wake_word(heytora):
    import threading
    import time
    from account.service import LimitReached
    from companion_manager import CompanionManager
    m = CompanionManager.__new__(CompanionManager)
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    asked = []

    class Refusing:
        async def transcribe(self, _pcm):
            asked.append(1)
            raise LimitReached({"reason": "day", "kind": "wake", "plan": "free"})
    m._loop = loop
    m._get_stt = lambda: Refusing()
    try:
        assert m._transcribe_blocking(b"x" * 3200) == ""
        assert m._transcribe_blocking(b"x" * 3200) == ""
        assert asked == [1], "not asked again every few seconds"
        assert m._wake_refused_until > time.monotonic() + 600
    finally:
        loop.call_soon_threadsafe(loop.stop)


# -- the server's rules ---------------------------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("node") is None, reason="Node runs the function's own tests")
def test_the_functions_rules():
    """Models it forwards and their price, what a request may hold, tickets,
    reading Google's token counts (supabase/tests/heytora_rules.test.ts)."""
    out = subprocess.run(["node", "--test", "supabase/tests/heytora_rules.test.ts"], cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert out.returncode == 0, out.stdout[-2000:] + out.stderr[-2000:]
    assert "# fail 0" in out.stdout or "fail 0" in out.stdout


def test_the_function_meters_with_its_rules():
    fn = (ROOT / "supabase/functions/heytora/index.ts").read_text(encoding="utf-8")
    for needle in ('fieldMask: "model"', "heytora_window", "heytora_pass", "readTicket(",
                   "checkGenerate(", "UsageReader", "heytora_record_cost", "MAX_BODY_BYTES",
                   'data.models.filter', 'query.delete("key")'):
        assert needle in fn, needle
    # The kind recorded is the ticket's, never the app's label.
    assert "kind = t.kind" in fn


def test_the_database_keeps_the_rules():
    sql = (ROOT / "supabase/migrations/0004_heytora_metering.sql").read_text(encoding="utf-8")
    for knob in ('"budget_usd"', '"talk_day"', '"talk_minutes"', '"passes_per_talk"'):
        assert knob in sql, knob
    for reason in ("'day'", "'budget'", "'passes'", "'window'", "'rate'", "'limit'"):
        assert reason in sql, reason
    # Only the server function records cost; nothing lets a user add or remove usage.
    assert "grant execute on function public.heytora_record_cost(uuid, text, text, bigint, bigint, bigint) " \
           "to service_role" in sql
    assert "revoke all on function public.record_usage(text, int, jsonb)              " \
           "from public, anon, authenticated" in sql
    assert "revoke all on function public._heytora_spend(uuid, text, int, jsonb)      " \
           "from public, anon, authenticated" in sql
    assert sql.count("security definer") >= 6 and sql.count("set search_path = public, pg_temp") >= 6
