"""GW-08: step one arrives faster.

Measured before building (live, two runs each):

    whole plan, then step one ........ 3.6-4.3 s    streamed: step one complete at 1.8-1.9 s
    TTS, whole line before a sound ... 3.3-4.1 s    streamed: first audio at 0.85-0.91 s

So a walkthrough was heard about 8 s after it was asked for: the whole plan,
then the whole of step one's speech. Now the plan streams and the walkthrough
starts on step one while the rest is still being written, and speech plays
from its first piece.
"""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from ai import gemini, gemini_planner
from ai.gemini_planner import Guide, Step
from tutor_features.guide import GuideRunner

CONTROLS = ["Five", "Multiply by", "Equals", "Clear"]


def _step(instruction, target):
    return {"instruction": instruction, "target": target, "kind": "action",
            "done_when": "", "possible_actions": [],
            "expected_state": {"control_appears": ""},
            "completion": {"kind": "none", "value": ""}}


PLAN = {"feasible": True, "goal": "multiply 5 by 5", "preamble": "Let's go.",
        "steps": [_step("Press five.", "five"), _step("Press Multiply by.", "Multiply by"),
                  _step("Press Five.", "Five"), _step("Press Equals.", "Equals")]}


def _pieces(text: str, size: int = 17) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)]


def _fake_stream(pieces, *, fail_after=None, seen=None):
    """gemini.stream as the planner calls it: the plan in small pieces."""
    async def stream(model, contents, **kw):
        if seen is not None:
            seen.update(kw)
        for n, piece in enumerate(pieces):
            if fail_after is not None and n >= fail_after:
                raise ConnectionError("stream broke")
            stream.yielded = n + 1
            await asyncio.sleep(0)
            yield piece
    stream.yielded = 0
    return stream


# ── the planner hands step one over early ────────────────────────────────────

def test_only_steps_that_have_fully_arrived_are_read():
    text = ('{"goal": "a {b}", "steps": [{"instruction": "Type \\"{x}\\".", "target": "T"},'
            ' {"instruction": "Press E')
    assert gemini_planner._complete_steps(text) == [
        {"instruction": 'Type "{x}".', "target": "T"}]
    assert gemini_planner._partial_string(text, "goal") == "a {b}"


def test_step_one_is_handed_over_before_the_plan_is_finished(monkeypatch):
    pieces = _pieces(json.dumps(PLAN))
    seen = {}
    fake = _fake_stream(pieces, seen=seen)
    monkeypatch.setattr(gemini_planner.gemini, "stream", fake)
    firsts = []

    def first(partial):
        firsts.append((partial, fake.yielded))

    guide = asyncio.run(gemini_planner.plan("multiply 5 by 5", controls=CONTROLS,
                                            on_first_step=first))
    (partial, at), = firsts
    assert [s.instruction for s in partial.steps] == ["Press five."]
    assert at < len(pieces) / 2, "long before the rest of the plan had arrived"
    assert partial.steps[0].target == "Five", "snapped to the real control, as the full plan is"
    assert len(guide.steps) == 4 and guide.steps[0].instruction == "Press five."
    assert seen["schema"] is gemini_planner._SCHEMA, "the stream is the same structured request"


def test_a_plan_that_says_it_cannot_be_done_starts_nothing(monkeypatch):
    plan = dict(PLAN, feasible=False)
    monkeypatch.setattr(gemini_planner.gemini, "stream", _fake_stream(_pieces(json.dumps(plan))))
    firsts = []
    guide = asyncio.run(gemini_planner.plan("x", on_first_step=firsts.append))
    assert firsts == [] and not guide


def test_a_stream_that_breaks_keeps_every_step_that_closed(monkeypatch):
    text = json.dumps(PLAN)
    cut = text.index('"Press Five."')          # steps 1 and 2 have closed by then
    pieces = _pieces(text)
    fail_after = next(n for n in range(len(pieces)) if sum(map(len, pieces[:n])) > cut)
    monkeypatch.setattr(gemini_planner.gemini, "stream",
                        _fake_stream(pieces, fail_after=fail_after))
    firsts = []
    guide = asyncio.run(gemini_planner.plan("x", on_first_step=firsts.append))
    assert len(firsts) == 1
    assert [s.instruction for s in guide.steps] == ["Press five.", "Press Multiply by."]


def test_a_stream_that_breaks_before_step_one_is_an_ordinary_failure(monkeypatch):
    monkeypatch.setattr(gemini_planner.gemini, "stream",
                        _fake_stream(_pieces(json.dumps(PLAN)), fail_after=1))
    firsts = []
    guide = asyncio.run(gemini_planner.plan("x", on_first_step=firsts.append))
    assert firsts == [] and not guide, "so the caller falls back as it always has"


# ── the runner takes a plan that is still being written ──────────────────────

def _runner(steps, *, writing=True, done=True):
    shown, said, ended = [], [], []
    calls: dict = {}

    async def speak(idx):
        shown.append(idx)

    async def check(step):
        calls[id(step)] = calls.get(id(step), 0) + 1
        return done and calls[id(step)] > 1          # false at first, then done

    async def say(text):
        said.append(text)

    guide = Guide(goal="g", steps=[Step(instruction=t) for t in steps])
    runner = GuideRunner(guide, speak_step=speak, check=check, say=say,
                         is_busy=lambda: False, on_stop=lambda: None,
                         on_event=lambda k, d: ended.append(d["reason"]) if k == "ended" else None,
                         poll_s=0.01, present_delay_s=0.0, writing=writing)
    return runner, guide, shown, said, ended


async def _until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)


def test_running_out_of_steps_while_the_plan_is_written_is_a_wait_not_the_end():
    runner, guide, shown, said, ended = _runner(["Press five."])

    async def scenario():
        await runner.start()
        await _until(lambda: runner._idx == 1)      # step one done, nothing after it yet
        await asyncio.sleep(0.1)
        assert runner.running and said == [], "not 'you're all done'"
        guide.steps.append(Step(instruction="Press Equals."))
        runner.plan_written()
        await _until(lambda: not runner.running)

    asyncio.run(scenario())
    assert shown == [0, 1] and ended == ["finished"]
    assert said == ["That's the last step — you're all done!"]


def test_a_plan_that_really_was_one_step_finishes():
    runner, guide, shown, said, ended = _runner(["Press five."])

    async def scenario():
        await runner.start()
        await _until(lambda: runner._idx == 1)
        runner.plan_written()
        await _until(lambda: not runner.running)

    asyncio.run(scenario())
    assert shown == [0] and ended == ["finished"]


def test_stop_while_waiting_for_the_plan_stops():
    runner, guide, shown, said, ended = _runner(["Press five."])

    async def scenario():
        await runner.start()
        await _until(lambda: runner._idx == 1)
        runner.request("stop")
        await _until(lambda: not runner.running)

    asyncio.run(scenario())
    assert ended == ["stopped by the user"] and said == []


def test_a_next_while_waiting_does_not_skip_the_step_that_arrives():
    runner, guide, shown, said, ended = _runner(["Press five."])
    runner._check = None

    async def scenario():
        await runner.start()
        await _until(lambda: runner._idx == 1)
        runner.request("next")                      # nothing to move to yet
        await asyncio.sleep(0.05)
        guide.steps.append(Step(instruction="Press Equals."))

        async def never_done(step):
            return False

        runner._check = never_done                   # step two is not done
        runner.plan_written()
        await _until(lambda: shown == [0, 1])
        await asyncio.sleep(0.15)
        assert runner.running and runner.index == 1, "still on the step that arrived"
        runner.stop()
        await _until(lambda: not runner.running)

    async def first_done(step):
        first_done.n = getattr(first_done, "n", 0) + 1
        return first_done.n > 1

    runner._check = first_done
    asyncio.run(scenario())


# ── the hub starts on step one and takes the rest as it arrives ──────────────

def _bare(monkeypatch):
    import companion_manager as cm

    monkeypatch.setattr(cm, "active_window_title", lambda: "Calculator")
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    mgr._activity = lambda *a: None
    mgr._guide_runner = None
    mgr._lesson_plan = None
    mgr._guide_stats = None
    mgr._plan_in_progress = None
    mgr.progress = []
    mgr.sig_guide_progress = SimpleNamespace(emit=lambda *a: mgr.progress.append(a))
    # A new step takes the last one's arrow and box down (LG-02).
    mgr.sig_guide_release = mgr.sig_clear_drawings = SimpleNamespace(emit=lambda *a: None)
    mgr.submitted = []

    def submit(coro):
        mgr.submitted.append(coro.__name__)
        coro.close()

    mgr._submit = submit
    return mgr


def test_the_walkthrough_starts_on_step_one_and_takes_the_rest_as_it_arrives(monkeypatch):
    mgr = _bare(monkeypatch)

    async def shot():
        return ""

    mgr._planning_shot = shot
    mgr._foreground_controls = lambda title: CONTROLS
    full = gemini_planner.guide_from_dict(PLAN)
    started = []

    def start_guide(guide, *, start_index=0, asked_at=None):
        started.append((guide, list(guide.steps), guide is mgr._plan_in_progress, asked_at))
        mgr._lesson_plan = guide
        mgr._guide_runner = SimpleNamespace(index=0, plan_written=lambda: started.append("written"))
        mgr._guide_stats = {"steps": len(guide.steps)}

    mgr._start_guide = start_guide

    async def plan_guide(goal, shot, title, controls, on_first_step=None):
        on_first_step(Guide(goal=goal, steps=[full.steps[0]]))
        return full

    mgr._plan_guide = plan_guide
    asyncio.run(mgr._plan_and_start_guide("multiply 5 by 5", asked_at=123.0))

    (partial, steps_then, writing, asked_at), written = started
    assert len(steps_then) == 1 and writing, "it started on step one, plan still being written"
    assert asked_at == 123.0
    assert written == "written", "and the runner was told when the rest arrived"
    assert [s.instruction for s in partial.steps] == [s.instruction for s in full.steps]
    assert partial.steps[0] is steps_then[0], "the step on screen is the same object"
    assert mgr._plan_in_progress is None and mgr._guide_stats["steps"] == 4
    assert mgr.progress[-1][:2] == (1, 4), "the real count goes to the screen"
    assert mgr.submitted[:2] == ["_prefetch_step", "_point_first_step"], \
        "at step one: its speech starts downloading and its arrow goes up"
    assert mgr.submitted[-1] == "_prefetch_step", "and when the rest arrives, step two's speech"


def test_step_ones_arrow_is_timed_from_the_request(monkeypatch, caplog):
    import logging

    mgr = _bare(monkeypatch)
    guide = Guide(goal="g", steps=[Step(instruction="Press five.", target="Five")])
    mgr._lesson_plan = guide
    mgr._guide_stats = {"arrow_s": None}
    mgr._step_point = {}

    async def point(idx, step, *, note=True):
        assert note is False, "the step's own presentation counts the target once"
        mgr._step_point[idx] = SimpleNamespace(x=1, y=2)

    mgr._point_step_target = point
    with caplog.at_level(logging.INFO, logger="tora.manager"):
        asyncio.run(mgr._point_first_step(guide, time.monotonic() - 2.0))
    assert 2.0 <= mgr._guide_stats["arrow_s"] < 2.5
    assert "guide step one pointed at 2.0s after it was asked for" in caplog.text


def test_a_step_plays_its_prepared_speech_instead_of_asking_again(monkeypatch):
    import companion_manager as cm

    mgr = _bare(monkeypatch)
    guide = Guide(goal="g", steps=[Step(instruction="Press five.")])
    mgr._lesson_plan = guide
    mgr._plan_in_progress = None
    mgr._make_step_watcher = lambda step, next_target="": SimpleNamespace(begin=lambda: _nothing())
    mgr._emit_state = lambda state: None
    mgr.sig_response_chunk = mgr.sig_response_done = SimpleNamespace(emit=lambda *a: None)
    spoken = []
    mgr._get_tts = lambda: SimpleNamespace(speak=lambda *a, **k: spoken.append(a))
    played = []

    class Prepared:
        async def play(self, *, on_start=None):
            played.append(True)

    mgr._step_audio = {0: Prepared()}
    asyncio.run(mgr._present_step(0))
    assert played == [True] and spoken == []
    assert cm.AppState  # imported for the state calls above


async def _nothing():
    return None


def test_once_step_one_has_started_there_is_no_falling_back_to_another_plan(monkeypatch):
    import companion_manager as cm

    mgr = _bare(monkeypatch)
    calls = []

    async def plan(goal, *, fast=True, on_first_step=None, **kw):
        calls.append("fast" if fast else "pro")
        if on_first_step is not None:
            on_first_step(Guide(goal=goal, steps=[Step(instruction="Press five.")]))
        return Guide(goal=goal, feasible=False)

    monkeypatch.setattr(cm.gemini_planner, "plan", plan)
    asyncio.run(mgr._plan_guide("g", "", "Calculator", [], on_first_step=lambda g: None))
    assert calls == ["fast"]

    calls.clear()

    async def plan_without_a_first_step(goal, *, fast=True, on_first_step=None, **kw):
        calls.append("fast" if fast else "pro")
        return Guide(goal=goal, feasible=False)

    monkeypatch.setattr(cm.gemini_planner, "plan", plan_without_a_first_step)
    asyncio.run(mgr._plan_guide("g", "", "Calculator", [], on_first_step=lambda g: None))
    assert calls == ["fast", "pro"], "nothing started, so the careful model still gets its turn"


def test_step_one_is_not_synthesised_twice(monkeypatch):
    mgr = _bare(monkeypatch)
    mgr._resume = None
    mgr._guide_last = None
    mgr._step_audio = {}
    mgr._step_point = {}
    guide = Guide(goal="g", steps=[Step(instruction="Press five.")])
    mgr._plan_in_progress = guide
    mgr._start_guide(guide, asked_at=1.0)
    assert mgr.submitted == ["start"], "the runner starts; step one streams when it is said"
    assert mgr._guide_runner.writing and mgr._guide_runner._present_delay_s == 0.3


def test_while_the_plan_is_written_nobody_is_told_step_one_is_the_last(monkeypatch):
    from ai import screen_summary

    mgr = _bare(monkeypatch)
    guide = Guide(goal="multiply 5 by 5", steps=[Step(instruction="Press five.", target="Five")])
    mgr._lesson_plan = guide
    mgr._plan_in_progress = guide
    assert mgr._step_line(0) == "Step 1: Press five."

    monkeypatch.setattr(screen_summary, "describe",
                        lambda **kw: SimpleNamespace(names=[], pointables=[], line="x"))
    mgr._guide_runner = SimpleNamespace(running=True, index=0)
    mgr._lesson_step_idx = 0
    mgr._lesson_steps = ["Press five."]
    mgr._resume = None
    mgr._privacy_guard = None
    mgr._context_head = lambda line: "[context]"
    mgr._ref_fresh = lambda: False
    mgr._last_ref = None
    mgr._pending_confirmation_line = lambda: ""
    mgr._attached_docs = []
    mgr.agent_context_line = lambda: ""
    text = asyncio.run(mgr._screen_context())
    assert "step 1 (the rest of the plan is still being written)" in text
    assert "of 1" not in text and "0 left" not in text

    mgr._plan_in_progress = None
    assert mgr._step_line(0) == "Step 1 of 1: Press five."


def test_next_on_step_one_while_the_plan_is_written_is_not_the_end(monkeypatch):
    mgr = _bare(monkeypatch)
    runner, guide, *_ = _runner(["Press five."])
    runner._idx = 0
    runner._task = SimpleNamespace(done=lambda: False)
    mgr._guide_runner = runner
    mgr._lesson_plan = guide
    mgr._request_serial, mgr._guide_moved_for = 1, -1
    mgr._heard_buf = ["next"]
    out = asyncio.run(mgr._tool_guide_step({"action": "next"}))
    assert "still being planned" in out["where"] and "finishing" not in out["where"]


# ── speech plays from its first piece ────────────────────────────────────────

class _Player:
    """PcmStreamPlayer, recording what is done to it."""
    events: list = []
    made = 0

    def __init__(self, sample_rate=24000):
        self.rate = sample_rate
        _Player.made += 1

    def open(self):
        _Player.events.append("open")

    def start(self):
        _Player.events.append("start")

    def feed(self, pcm):
        _Player.events.append(("feed", len(pcm)))

    def clear(self):
        _Player.events.append("clear")

    async def drain(self):
        _Player.events.append("drain")

    def stop(self):
        _Player.events.append("stop")


def _tts(monkeypatch, pieces, *, fail=False, stop_after=None, gap=0.01):
    from audio.tts import gemini_tts

    async def stream_audio(model, contents, *, voice, **kw):
        text = contents[0]["parts"][0]["text"]
        for n, piece in enumerate(pieces):
            if n:
                await asyncio.sleep(gap)             # pieces arrive over time
            _Player.events.append(("piece", n) if "#2" not in text else ("piece2", n))
            yield piece, 24000
        if fail:
            raise ConnectionError("broke")

    _Player.events, _Player.made = [], 0
    monkeypatch.setattr(gemini_tts.gemini, "stream_audio", stream_audio)
    monkeypatch.setattr(gemini_tts, "PcmStreamPlayer", _Player)
    fed = lambda: sum(1 for e in _Player.events if isinstance(e, tuple) and e[0] == "feed")  # noqa: E731
    monkeypatch.setattr(gemini_tts, "playback_stopped",
                        lambda: stop_after is not None and fed() >= stop_after)
    return gemini_tts.GeminiTTS()


def test_speech_starts_with_the_first_piece(monkeypatch):
    tts = _tts(monkeypatch, [b"\0" * 100, b"\0" * 200, b"\0" * 300])
    started = []
    asyncio.run(tts.speak("Step 1: Press five.", on_start=lambda: started.append(True)))
    ev = _Player.events
    assert ev[:3] == [("piece", 0), "start", ("feed", 100)], "played before the rest arrived"
    assert ev[-1] == "drain" and "stop" not in ev and started == [True]


def test_stopping_speech_stops_the_download_and_leaves_nothing_queued(monkeypatch):
    tts = _tts(monkeypatch, [b"\0" * 100, b"\0" * 200, b"\0" * 300], stop_after=1)
    asyncio.run(tts.speak("hello"))
    assert ("piece", 2) not in _Player.events and "drain" not in _Player.events
    assert _Player.events[-1] == "clear", "or it would play before the next line"


def test_speech_prepared_early_plays_without_being_asked_for_twice(monkeypatch):
    """Step one's speech starts downloading while the model is still saying
    "sure, let me walk you through it"; when the step is said it plays from
    that one download - late or early."""
    tts = _tts(monkeypatch, [b"\0" * 100, b"\0" * 200])

    async def scenario():
        prepared = tts.prepare("Step 1: Press five.")
        await asyncio.sleep(0.05)                   # the model is still talking
        assert _Player.events == [("piece", 0), ("piece", 1)], "downloaded, not played"
        await prepared.play()

    asyncio.run(scenario())
    assert [e for e in _Player.events if isinstance(e, tuple) and e[0] == "piece"] ==         [("piece", 0), ("piece", 1)], "one download"
    assert _Player.events[2:] == ["start", ("feed", 100), ("feed", 200), "drain"]


# ── one output stream, kept open between lines ───────────────────────────────
#
# On this machine's MME output a new stream asks for audio 0.24 s after it
# is opened (0.97 s for the first after launch), and the old one-stream-per-
# line playback paid that before every line.

def test_lines_share_one_stream_that_stays_open(monkeypatch):
    tts = _tts(monkeypatch, [b"\0" * 10])

    async def two_lines():
        await tts.speak("one")
        await tts.speak("two")

    asyncio.run(two_lines())
    assert _Player.made == 1 and "stop" not in _Player.events
    assert _Player.events.count("start") == 2


def test_the_stream_closes_after_a_quiet_spell_and_reopens(monkeypatch):
    from audio.tts import gemini_tts

    tts = _tts(monkeypatch, [b"\0" * 10])
    monkeypatch.setattr(gemini_tts, "IDLE_CLOSE_S", 0.05)

    async def scenario():
        await tts.speak("one")
        await asyncio.sleep(0.15)                   # a quiet spell
        assert _Player.events[-1] == "stop", "closed - a device that went away is not held"
        await tts.speak("two")

    asyncio.run(scenario())
    assert _Player.made == 2, "and opened afresh for the next line"


def test_two_lines_never_play_over_each_other(monkeypatch):
    tts = _tts(monkeypatch, [b"\0" * 1, b"\0" * 2, b"\0" * 3], gap=0.02)

    async def both():
        await asyncio.gather(tts.speak("line #1"), tts.speak("line #2"))

    asyncio.run(both())
    feeds = [n for e in _Player.events if isinstance(e, tuple) and e[0] == "feed"
             for n in [e[1]]]
    assert feeds == [1, 2, 3, 1, 2, 3], "one line, then the other - not interleaved"


def test_warming_opens_the_stream_without_arming_playback(monkeypatch):
    tts = _tts(monkeypatch, [b"\0" * 10])

    async def scenario():
        tts.warm()
        assert _Player.events == ["open"], "opened; the stop flag is not touched"
        await tts.speak("Step 1: Press five.")

    asyncio.run(scenario())
    assert _Player.made == 1, "the line plays through the stream that was warmed"


def test_a_line_can_still_be_synthesised_whole(monkeypatch):
    tts = _tts(monkeypatch, [b"ab", b"cd"])
    assert asyncio.run(tts.synthesize("hello")) == (b"abcd", 24000)


def test_a_failed_stream_is_not_a_crash_and_not_silence(monkeypatch):
    tts = _tts(monkeypatch, [b"ab"], fail=True)
    asyncio.run(tts.speak("hello"))
    assert _Player.events[-1] == "drain", "what arrived is played; nothing raises"
    # LG-04: a line that could not be fetched is said in the Windows voice
    # (scripted as 0.1 s of silence by the test sandbox), not dropped.
    assert asyncio.run(tts.synthesize("hello")) == (b"\x00\x00" * 2400, 24000)


# ── the requests themselves ──────────────────────────────────────────────────

class _Client:
    def __init__(self, lines):
        self.lines, self.body = lines, None

    def stream(self, method, url, *, params, json, timeout):
        client = self
        client.body, client.url = json, url

        class _Resp:
            status_code = 200

            async def aiter_lines(self):
                for line in client.lines:
                    yield line

            async def aread(self):
                return b""

        class _Ctx:
            async def __aenter__(self):
                return _Resp()

            async def __aexit__(self, *a):
                return False

        return _Ctx()


def test_a_streamed_plan_asks_for_the_schema(monkeypatch):
    fake = _Client(['data: {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}'])
    monkeypatch.setattr(gemini, "client", lambda: fake)
    monkeypatch.setattr(gemini, "api_key", lambda: "fake-key")

    async def collect():
        return [t async for t in gemini.stream("m", [], schema={"type": "object"})]

    assert asyncio.run(collect()) == ["{}"]
    assert fake.body["generationConfig"]["responseSchema"] == {"type": "object"}
    assert fake.body["generationConfig"]["responseMimeType"] == "application/json"


def test_streamed_speech_comes_back_as_pcm_pieces_with_their_rate(monkeypatch):
    import base64
    blob = base64.b64encode(b"\1\2\3\4").decode()
    line = ('data: {"candidates": [{"content": {"parts": [{"inlineData": '
            '{"mimeType": "audio/L16;codec=pcm;rate=16000", "data": "%s"}}]}}]}' % blob)
    fake = _Client([line, "", line])
    monkeypatch.setattr(gemini, "client", lambda: fake)
    monkeypatch.setattr(gemini, "api_key", lambda: "fake-key")

    async def collect():
        return [p async for p in gemini.stream_audio("tts", [], voice="Zephyr")]

    assert asyncio.run(collect()) == [(b"\1\2\3\4", 16000)] * 2
    assert "streamGenerateContent" in fake.url
    assert fake.body["generationConfig"]["responseModalities"] == ["AUDIO"]


# ── a dead pooled connection costs a retry, not the slow planner ─────────────
#
# 21 September, 19:43: "planner failed: LocalProtocolError Invalid input
# ConnectionInputs.RECV_PING in state ConnectionState.CLOSED" - an HTTP/2
# connection the server had closed, reused. The walkthrough then fell back to
# the pro model (10-20 s). The same error came up once in GW-08's live runs.

def _dead_connection():
    import httpx
    return httpx.LocalProtocolError(
        "Invalid input ConnectionInputs.RECV_PING in state ConnectionState.CLOSED")


def test_a_request_on_a_dead_connection_is_retried_once(monkeypatch):
    calls = []

    class Client:
        async def post(self, url, *, params, json, timeout):
            calls.append(url)
            if len(calls) == 1:
                raise _dead_connection()
            return SimpleNamespace(status_code=200, json=lambda: {"ok": 1}, text="")

    monkeypatch.setattr(gemini, "client", lambda: Client())
    monkeypatch.setattr(gemini, "api_key", lambda: "fake-key")
    assert asyncio.run(gemini.generate("m", [])) == {"ok": 1}
    assert len(calls) == 2


def test_two_dead_connections_in_a_row_are_an_error(monkeypatch):
    import httpx

    class Client:
        async def post(self, url, **kw):
            raise _dead_connection()

    monkeypatch.setattr(gemini, "client", lambda: Client())
    monkeypatch.setattr(gemini, "api_key", lambda: "fake-key")
    with pytest.raises(httpx.LocalProtocolError):
        asyncio.run(gemini.generate("m", []))


def test_a_timeout_is_not_retried(monkeypatch):
    import httpx
    calls = []

    class Client:
        async def post(self, url, **kw):
            calls.append(url)
            raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(gemini, "client", lambda: Client())
    monkeypatch.setattr(gemini, "api_key", lambda: "fake-key")
    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(gemini.generate("m", []))
    assert len(calls) == 1, "a retry would double a wait that was already long"


def _flaky_stream_client(fail_on_open: int, fail_after_line: bool = False):
    line = 'data: {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}'

    class Client:
        opened = 0

        def stream(self, method, url, *, params, json, timeout):
            Client.opened += 1
            n = Client.opened

            class Resp:
                status_code = 200

                async def aiter_lines(self):
                    yield line
                    if fail_after_line:
                        raise _dead_connection()

                async def aread(self):
                    return b""

            class Ctx:
                async def __aenter__(self):
                    if n <= fail_on_open:
                        raise _dead_connection()
                    return Resp()

                async def __aexit__(self, *a):
                    return False

            return Ctx()

    return Client


def test_a_stream_that_cannot_start_is_retried_once(monkeypatch):
    Client = _flaky_stream_client(fail_on_open=1)
    monkeypatch.setattr(gemini, "client", lambda: Client())
    monkeypatch.setattr(gemini, "api_key", lambda: "fake-key")

    async def collect():
        return [t async for t in gemini.stream("m", [])]

    assert asyncio.run(collect()) == ["hi"] and Client.opened == 2


def test_a_stream_that_breaks_after_it_started_is_not_replayed(monkeypatch):
    import httpx

    Client = _flaky_stream_client(fail_on_open=0, fail_after_line=True)
    monkeypatch.setattr(gemini, "client", lambda: Client())
    monkeypatch.setattr(gemini, "api_key", lambda: "fake-key")
    got = []

    async def collect():
        async for t in gemini.stream("m", []):
            got.append(t)

    with pytest.raises(httpx.LocalProtocolError):
        asyncio.run(collect())
    assert got == ["hi"] and Client.opened == 1, "replaying would say 'hi' twice"


# ── the screen says "step 1", not "step 1 of 1" ──────────────────────────────

def test_the_island_shows_step_n_alone_while_the_plan_is_written():
    from ui.overlay import CursorOverlay

    island = SimpleNamespace(_guide_total=0, _guide_cur=0, _guide_label="",
                             _annotations=[], _activity_text="", _activity_at=0.0)
    CursorOverlay.set_guide_progress(island, 1, -1, "Press five.")
    assert CursorOverlay._island_lines(island)[1] == "Step 1 · Press five."
    CursorOverlay.set_guide_progress(island, 1, 4, "Press five.")
    assert CursorOverlay._island_lines(island)[1] == "Step 1 of 4 · Press five."
    CursorOverlay.set_guide_progress(island, 0, 0, "")
    assert CursorOverlay._island_lines(island) is None


def test_the_panel_shows_step_n_alone_while_the_plan_is_written():
    from ui.panel import CompanionPanel

    label = SimpleNamespace(text="", visible=None, tip="")
    label.setText = lambda t: setattr(label, "text", t)
    label.setVisible = lambda v: setattr(label, "visible", v)
    label.setToolTip = lambda t: setattr(label, "tip", t)
    panel = SimpleNamespace(_guide_label=label)
    CompanionPanel.set_guide_progress(panel, 1, -1, "Press five.")
    assert label.text == "Guide · 1" and label.visible
    CompanionPanel.set_guide_progress(panel, 2, 4)
    assert label.text == "Guide · 2/4"
    CompanionPanel.set_guide_progress(panel, 0, 0)
    assert label.visible is False
