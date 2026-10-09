"""What a whole answer costs, and the bench that measures the voice (SP-02, SP-03).

The scorecard priced the screen look and nothing else, while the live voice
turn - the system prompt and the tool list billed again on every generation,
the audio out - is most of what a message costs. "Never dearer per message"
could not be shown without it. Now the live server's usageMetadata is read,
folded into the turn it belongs to, priced, and put on the answer's line.

tools/voice_eval.py asks the live model typed questions with recorded tool
results; its checks are tested here against a scripted session, so the paid
run is never the first time the code runs.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from pathlib import Path

import pytest

import ai.gemini_live as live
import ai.gemini_realtime as rt
from ai.gemini_live import LiveEvent, ToolCall

ROOT = Path(__file__).resolve().parents[2]

# What the server sent on 6 Oct for a look question's first generation.
USAGE = {"promptTokenCount": 4100, "responseTokenCount": 120, "totalTokenCount": 4220,
         "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 4100}],
         "responseTokensDetails": [{"modality": "AUDIO", "tokenCount": 120}]}


def test_a_live_generation_is_priced_by_modality():
    # 4,100 text in at $0.75/M and 120 audio out at $12/M
    assert live.live_cost_usd(USAGE) == pytest.approx(4100 * 0.75e-6 + 120 * 12e-6)
    audio_in = {"promptTokenCount": 300, "promptTokensDetails": [{"modality": "AUDIO", "tokenCount": 300}]}
    assert live.live_cost_usd(audio_in) == pytest.approx(300 * 3e-6), "the user's voice is dearer than text"
    assert live.live_cost_usd({"thoughtsTokenCount": 100}) == pytest.approx(100 * 4.5e-6), "thinking is text out"
    assert live.live_cost_usd({}) == 0.0


def test_usage_adds_up_count_by_count_and_modality_by_modality():
    twice = live.add_usage(USAGE, USAGE)
    assert twice["promptTokenCount"] == 8200
    assert twice["promptTokensDetails"] == [{"modality": "TEXT", "tokenCount": 8200}]
    assert live.live_cost_usd(twice) == pytest.approx(2 * live.live_cost_usd(USAGE))


class FakeSocket:
    def __init__(self, messages):
        self.messages = [json.dumps(m) for m in messages]

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.messages:
            raise StopAsyncIteration
        return self.messages.pop(0)


def _kinds(messages):
    s = live.LiveSession("m", {})
    s._ws = FakeSocket(messages)

    async def read():
        return [(e.kind, e.raw) async for e in s.events()]
    return asyncio.run(read())


def test_the_server_usage_is_read_wherever_it_rides():
    got = _kinds([
        {"toolCall": {"functionCalls": [{"id": "1", "name": "look_at_screen"}]}, "usageMetadata": USAGE},
        {"serverContent": {"turnComplete": True}, "usageMetadata": {"promptTokenCount": 5}},
        {"usageMetadata": {"promptTokenCount": 7}},
    ])
    kinds = [k for k, _ in got]
    assert kinds == ["usage", "tool_call", "usage", "turn_complete", "usage", "closed"]
    assert got[0][1] == USAGE, "on a tool call too - that branch `continue`s"


class Scripted:
    """LiveSession, answering like the server: a question gets "let me look"
    and a look_at_screen call; the tool's result gets the answer."""
    is_open = True
    resume_handle = None

    def __init__(self, model, setup, resume_handle=None):
        self.q: asyncio.Queue = asyncio.Queue()
        self.sent: list = []

    async def connect(self):
        pass

    async def events(self):
        while True:
            ev = await self.q.get()
            yield ev
            if ev.kind == "closed":
                return

    async def send_text(self, text, turn_complete=True):
        self.sent.append(text)
        if turn_complete:
            for ev in [LiveEvent("output_transcript", text="Let me look."),
                       LiveEvent("tool_call", calls=[ToolCall("1", "look_at_screen", {"question": text})]),
                       LiveEvent("usage", raw=USAGE),
                       LiveEvent("turn_complete", produced_output=True)]:
                self.q.put_nowait(ev)

    async def send_tool_response(self, results):
        for ev in [LiveEvent("output_transcript", text="Knight to f7 - it forks the king and the queen."),
                   LiveEvent("usage", raw=USAGE),
                   LiveEvent("turn_complete", produced_output=True)]:
            self.q.put_nowait(ev)

    async def close(self):
        self.q.put_nowait(LiveEvent("closed"))


@pytest.fixture
def scripted(monkeypatch):
    monkeypatch.setattr(rt, "LiveSession", Scripted)
    monkeypatch.setattr(rt.PcmStreamPlayer, "stop", lambda self: None, raising=False)


def test_each_turn_carries_what_its_generations_cost(scripted):
    turns = []

    async def look(args):
        return {"say": "Knight to f7.", "drawn": ["arrow: Nf7"], "highlights": []}

    async def scenario():
        conv = rt.RealtimeConversation(tools={"look_at_screen": look}, on_turn_end=turns.append)
        assert await conv.connect("PROMPT")
        await conv._session.send_text("best move?")
        for _ in range(50):
            await asyncio.sleep(0.01)
            if len(turns) == 2:
                break
        await conv.close()
    asyncio.run(scenario())
    assert [t.said for t in turns] == ["Let me look.", "Knight to f7 - it forks the king and the queen."]
    assert turns[0].usage["promptTokenCount"] == 4100 and turns[1].usage["promptTokenCount"] == 4100


# -- the voice bench --------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def bench():
    """tools/voice_eval.py, without leaving its own-key switch on for other tests."""
    before = os.environ.get("TORA_ACCESS")
    spec = importlib.util.spec_from_file_location("voice_eval", ROOT / "tools" / "voice_eval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if before is None:
        os.environ.pop("TORA_ACCESS", None)
    else:
        os.environ["TORA_ACCESS"] = before
    return mod


def test_the_bench_asks_fifteen_questions_and_sends_no_screen(bench):
    assert len(bench.CASES) == 15
    assert len({c.name for c in bench.CASES}) == 15
    for c in bench.CASES:
        line = bench.context_line(c)
        assert line.startswith("[context] Today is") and c.title in line
        if "look_at_screen" in c.expect:
            assert c.look.get("say"), f"{c.name}: a look question needs its recorded look"


def test_the_bench_scores_what_was_said(bench):
    chess = next(c for c in bench.CASES if c.name == "chess")
    good = bench.check(chess, "Let me look. Knight to f7 - it forks the king and the queen.",
                       "Knight to f7 - it forks the king and the queen.", ["look_at_screen"])
    assert all(good.values()), good
    narrated = bench.check(chess, "Let me look. White has eight pawns and a bishop. Let me recount. Knight f7.",
                           "White has eight pawns and a bishop. Let me recount. Knight f7.", ["look_at_screen"])
    assert {k for k, v in narrated.items() if not v} == {"short", "no_let_me_after", "no_counting", "no_readings"}
    piled_on = bench.check(chess, "Knight to f7.", "Knight to f7.", ["look_at_screen", "draw"])
    assert not piled_on["nothing_drawn_on_top"], "what the look drew is the answer"

    thanks = next(c for c in bench.CASES if c.name == "thanks")
    ok = bench.check(thanks, "You're welcome! Let me know if you need anything else.", "", [])
    assert all(ok.values()), "'let me know' is not narrating"
    assert not bench.check(thanks, "Sure.", "", ["look_at_screen"])["tool"]


def test_the_bench_runs_a_question_end_to_end_without_a_speaker(bench, scripted):
    chess = next(c for c in bench.CASES if c.name == "chess")
    row = asyncio.run(bench.ask(chess, "PROMPT", timeout=5.0))
    assert row["tools"] == ["look_at_screen"]
    assert row["answer"] == "Knight to f7 - it forks the king and the queen."
    assert row["ok"], row["failed"]
    assert row["first_word_s"] is not None and row["answer_s"] >= row["first_word_s"]
    assert bench.priced(row) == pytest.approx(2 * live.live_cost_usd(USAGE)), "both generations, once each"


# -- on the answer's line in tora.log ---------------------------------------------------------------------------------

def _manager():
    import companion_manager as cm
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    mgr._ans, mgr._ans_stats = None, None
    return cm, mgr


def test_the_timeline_waits_past_let_me_look_for_the_answer_and_prices_it(caplog, monkeypatch):
    import logging
    from types import SimpleNamespace
    cm, mgr = _manager()
    clock = iter([0.4, 0.9, 1.6, 4.9])
    monkeypatch.setattr(cm.time, "monotonic", lambda: next(clock))
    mgr._ans = {"heard": 0.0, "asked": 0.0}
    mgr._answer_stats()["questions"] = 1
    with caplog.at_level(logging.INFO, logger="tora.manager"):
        mgr._mark_answer("audio")                    # 0.4 "Let me look at your options."
        mgr._mark_answer("tool")                     # 0.9 look_at_screen
        filler = SimpleNamespace(heard="", said="Let me look.", tools_used=["look_at_screen"], usage=USAGE)
        mgr._note_voice(filler.usage)
        a = mgr._ans
        assert "tool" in a and "answer" not in a
        mgr._mark_answer("look", "fast")             # 1.6
        mgr._mark_answer("answer")                   # 4.9 "Pick the Arctic Fan."
        mgr._note_voice(USAGE)
        mgr._log_answer("Pick the Arctic Fan.")
    line = next(r.message for r in caplog.records if r.message.startswith("answer timeline"))
    assert "voice +0.4s, answer +4.9s" in line, "the first word, then the answer itself"
    assert "voice 8200 in / 240 out $%.4f" % (2 * live.live_cost_usd(USAGE)) in line
    card = next(r.message for r in caplog.records if r.message.startswith("answer scorecard"))
    assert "voice cost avg $%.4f" % (2 * live.live_cost_usd(USAGE)) in card


def test_a_filler_turn_does_not_close_the_answer(monkeypatch):
    from types import SimpleNamespace
    cm, mgr = _manager()
    logged = []
    monkeypatch.setattr(mgr, "_log_answer", logged.append, raising=False)
    for name in ("_ensure_pointed", "_submit", "_record_turn", "_emit_state", "_metered_talk"):
        monkeypatch.setattr(mgr, name, lambda *a, **k: False, raising=False)
    mgr._pointed_this_turn, mgr._pointing_held, mgr._heard_buf = True, False, []
    mgr.sig_response_done = SimpleNamespace(emit=lambda *_: None)
    mgr._ans = {"heard": 0.0, "audio": 0.4, "tool": 0.9}
    mgr._on_realtime_turn_end(SimpleNamespace(heard="which one?", said="Let me look.",
                                              tools_used=["look_at_screen"], usage=USAGE))
    assert logged == [] and mgr._ans is not None, "the answer is still to come"
    mgr._ans["answer"] = 4.9
    mgr._on_realtime_turn_end(SimpleNamespace(heard="", said="Pick the Arctic Fan.", tools_used=[], usage=USAGE))
    assert logged == ["Pick the Arctic Fan."]


def test_the_scorecard_prices_the_whole_message(caplog):
    import logging
    cm, mgr = _manager()
    st = mgr._answer_stats()
    st["questions"] = 4                              # four asked, one of them looked
    mgr._note_voice({"promptTokenCount": 4000})     # $0.003 in all
    mgr._ans = {"heard": 0.0, "asked": 0.0, "tool": 0.5, "look": 2.0}
    mgr._note_answer(path="fast", cost=0.004)
    with caplog.at_level(logging.INFO, logger="tora.manager"):
        mgr._log_answer("Click Checkout.")
    assert "voice cost avg $0.0008, answer cost avg $0.0018" in caplog.text


def test_the_bench_checks_what_a_tool_was_asked_to_do(bench):
    song = next(c for c in bench.CASES if c.name == "play_song")
    said = "Playing Blinding Lights by The Weeknd."
    assert bench.check(song, said, said, ["desktop_action"], [{"app": "spotify", "action": "play"}])["args"]
    assert not bench.check(song, said, said, ["desktop_action"], [{"app": "browser", "action": "open_url"}])["args"],         "a song is Spotify, never a web page"


def test_the_bench_catches_spoken_planning(bench):
    """6 Oct: the voice once spoke its own checklist before the walkthrough."""
    how = next(c for c in bench.CASES if c.name == "how_to")
    leaked = ("Sure, let me walk you through it.    *   Does it use markdown? No.\n    *   Does it have "
              "bullet lists? No.\n\n7.  **Finalize Output.**To get started, click File.")
    assert not bench.check(how, leaked, leaked, ["show_me_how"])["no_planning"]
    assert bench.check(how, "Sure, let me walk you through it.", "", ["show_me_how"])["no_planning"]


def test_the_bench_counts_a_cut_for_spoken_planning(bench, scripted, monkeypatch):
    """A cut hides the leak from the transcript, so the bench counts it from the log."""
    chess = next(c for c in bench.CASES if c.name == "chess")

    async def leaky(self, results):
        for ev in [LiveEvent("output_transcript", text="Knight to f7.    *   Check: one line? Yes."),
                   LiveEvent("turn_complete", produced_output=True),
                   LiveEvent("output_transcript", text="Knight to f7."),
                   LiveEvent("turn_complete", produced_output=True)]:
            self.q.put_nowait(ev)
    monkeypatch.setattr(Scripted, "send_tool_response", leaky)
    row = asyncio.run(bench.ask(chess, "PROMPT", timeout=5.0))
    assert row["planning_cut"] == 1
    assert "*" not in row["said"]


# -- FS-06: where a look's time goes on HeyTora's server ------------------------------------------------------------

def test_the_servers_timing_header_is_read():
    from ai import gemini
    assert gemini.server_timing("auth;dur=120, consume;dur=80, google;dur=2100, total;dur=2400") == \
        {"auth": 120.0, "consume": 80.0, "google": 2100.0, "total": 2400.0}
    assert gemini.server_timing("") == {} and gemini.server_timing("junk, x;desc=y") == {}


def test_a_forwarded_look_carries_the_split_into_its_usage(monkeypatch):
    from ai import gemini

    class Reply:
        status_code = 200
        headers = {"server-timing": "auth;dur=310, consume;dur=95, google;dur=1900, total;dur=2350"}

        @staticmethod
        def json():
            return {"candidates": [{"content": {"parts": [{"text": '{"answer": "Click Buy.", "actions": [], '
                                                                   '"sure": true}'}]}}],
                    "usageMetadata": {"promptTokenCount": 830}}

    async def post(path, body, timeout):
        return Reply()
    monkeypatch.setattr(gemini, "_post", post)
    usage = {}
    data = asyncio.run(gemini.generate_json("gemini-3.8-flash", [], {}, usage=usage))
    assert data["answer"] == "Click Buy."
    assert usage["server_total_ms"] == 2350 and usage["server_auth_ms"] == 310 and usage["promptTokenCount"] == 830
