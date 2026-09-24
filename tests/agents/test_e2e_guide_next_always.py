"""LG-03: "next" always means next - and a refusal cannot become a loop.

24 Sep, 23:03. The user said "next"; the transcript said "net"; the check on
guide_step refused it as "a question". The live model - which heard "next"
in the audio - called guide_step 41 times in 20 seconds. The loop brake
answered 35 of them with "too many tool calls; just answer" and the model
ignored every one, until the 22 s watchdog ended the turn. "nächste" and
"dann nicht" were refused too: the check only knew English, and its word
pattern could not even see "ä".
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from tutor_features.guide_intent import guide_intent, looks_like_command


# ── what the person said ─────────────────────────────────────────────────────

@pytest.mark.parametrize("heard, intent", [
    ("net", "next"), ("Net.", "next"), ("nächste", "next"), ("Nächste!", "next"),
    ("weiter", "next"), ("fertig", "next"), ("volgende", "next"), ("klaar", "next"),
    ("siguiente", "next"), ("suivant", "next"),
    ("zurück", "back"), ("stopp", "stop"), ("nochmal", "repeat"), ("ja", "ack"),
])
def test_next_and_its_neighbours_in_the_words_people_use(heard, intent):
    assert guide_intent(heard) == intent


@pytest.mark.parametrize("heard", [
    "dann nicht",                      # "then not" - a no, not a next
    "what text do I type?",            # "text" is next only on its own
    "what net do I use",
    "not next",
    "is this the right one",
])
def test_what_is_not_next_still_is_not(heard):
    assert guide_intent(heard) != "next"


@pytest.mark.parametrize("heard, command", [
    ("vámonos", True), ("net please", True),
    ("is this right", False), ("what now?", False), ("dann nicht", False),
    ("could you show me where the export button is", False),
])
def test_what_counts_as_command_shaped(heard, command):
    assert looks_like_command(heard) is command


# ── the model's second ask ───────────────────────────────────────────────────

def _manager(heard, serial=5):
    from companion_manager import CompanionManager
    mgr = CompanionManager.__new__(CompanionManager)
    mgr._heard_buf = [heard]
    mgr._request_serial = serial
    return mgr


RUNNER = SimpleNamespace(index=0, total=3)


def test_the_models_second_ask_for_short_words_is_taken():
    """Words the check does not know, said as a command: the model heard the
    audio, so its second ask for the same move on the same words wins."""
    mgr = _manager("vámonos")
    first = asyncio.run(mgr._check_guide_move("next", RUNNER))
    assert first is not None and first["moved"] is False
    assert asyncio.run(mgr._check_guide_move("next", RUNNER)) is None, "moves on the second ask"


@pytest.mark.parametrize("heard", ["is this the right one", "okay", "dann nicht"])
def test_a_question_an_okay_or_a_no_is_never_taken_however_often(heard):
    mgr = _manager(heard)
    for _ in range(4):
        out = asyncio.run(mgr._check_guide_move("next", RUNNER))
        assert out is not None and out["moved"] is False


def test_a_second_ask_counts_only_for_the_same_words():
    mgr = _manager("vámonos", serial=5)
    asyncio.run(mgr._check_guide_move("next", RUNNER))
    mgr._request_serial = 6                       # they said something new
    assert asyncio.run(mgr._check_guide_move("next", RUNNER)) is not None


def test_net_moves_the_first_time():
    assert asyncio.run(_manager("net")._check_guide_move("next", RUNNER)) is None


# ── a loop is cut off, not answered 35 times ─────────────────────────────────

def test_a_tool_loop_is_cut_off_and_the_conversation_resumes(monkeypatch):
    import ai.gemini_realtime as rt
    from tests.agents.test_realtime_session import FakeSession, ScriptedSession, _call, rt_event

    script = [rt_event("input_transcript", text="net")]
    for _ in range(41):                            # the 23:03 loop, as the wire had it
        script += [rt_event("tool_call", calls=[_call("guide_step")]),
                   rt_event("turn_complete", produced_output=False)]
    ScriptedSession.script = script
    monkeypatch.setattr(FakeSession, "opened", [])     # where every session is recorded
    monkeypatch.setattr(rt, "LiveSession", ScriptedSession)
    monkeypatch.setattr(rt.PcmStreamPlayer, "stop", lambda self: None, raising=False)
    ran, lost = [], []

    async def guide_step(args):
        ran.append(args)
        return {"ok": True, "moved": False}

    conv = rt.RealtimeConversation(tools={"guide_step": guide_step}, on_lost=lost.append)

    async def scenario():
        await conv.connect("PROMPT")
        for _ in range(300):
            if conv._session is None:
                break
            await asyncio.sleep(0.01)

    asyncio.run(scenario())
    session = FakeSession.opened[-1]
    answered = len(getattr(session, "tool_responses", []))
    assert len(ran) == rt.MAX_TOOL_ROUNDS, "the brake still runs at most six"
    assert answered == rt.HARD_TOOL_ROUNDS, f"answered {answered} calls, not 41"
    assert session.closed and lost == ["tool loop"], "closed, and resumed by the manager"
