"""The voice never speaks its own planning (SP-18, docs/SHOW_DONT_TELL_POLISH.md).

On 6 Oct, asked "how do I export this as a PDF?", the live voice said "Sure,
let me walk you through it." and then, out loud, its own checklist: "Does it
use markdown? No. Does it have bullet lists? No. ... 7. **Finalize Output.**"
- and then the walkthrough's first step, which show_me_how forbids. About one
answer in 35 on the voice bench. Speech is plain words; markdown in what the
voice is saying is planning on its way out loud, and it is cut there.
"""
from __future__ import annotations

import asyncio

import pytest

import ai.gemini_realtime as rt
from ai.gemini_live import LiveEvent


class Player:
    """PcmStreamPlayer, recording what it was asked to play."""
    queued_seconds = 0.0
    latency = 0.0
    bytes_per_second = 48_000

    def __init__(self):
        self.fed, self.cleared = [], 0

    def start(self): pass
    def stop(self): pass
    def feed(self, pcm): self.fed.append(pcm)
    def clear(self): self.cleared += 1


class Scripted:
    script: list = []
    is_open = True
    resume_handle = None

    def __init__(self, model, setup, resume_handle=None):
        pass

    async def connect(self):
        pass

    async def events(self):
        for ev in Scripted.script:
            yield ev
        yield LiveEvent("closed")

    async def send_text(self, text, turn_complete=True):
        pass

    async def close(self):
        pass


def _run(script):
    Scripted.script = script
    turns, shown = [], []

    async def go():
        conv = rt.RealtimeConversation(tools={}, on_turn_end=turns.append, on_said=shown.append)
        player = conv._player = Player()
        await conv.connect("PROMPT")
        await asyncio.sleep(0.05)
        await conv.close()
        return player
    return asyncio.run(go()), turns, shown


@pytest.fixture(autouse=True)
def scripted(monkeypatch):
    monkeypatch.setattr(rt, "LiveSession", Scripted)


def say(text, audio=b"pcm"):
    return [LiveEvent("output_transcript", text=text), LiveEvent("audio", audio=audio)]


def test_its_own_checklist_is_cut_before_it_is_heard():
    player, turns, shown = _run(
        say("Sure, let me walk you through it.", b"ok")
        + say("    *   Does it use markdown? No.", b"plan")
        + say("7.  **Finalize Output.**", b"plan")
        + say("To get started, click File.", b"plan")
        + [LiveEvent("turn_complete", produced_output=True)])
    assert player.fed == [b"ok"], "nothing after the planning began is played"
    assert player.cleared == 1, "what was already queued is dropped"
    assert [t.said for t in turns] == ["Sure, let me walk you through it."]
    assert shown == ["Sure, let me walk you through it."], "nor shown in the caption"


def test_the_next_generation_speaks_again():
    """The cut ends with its generation: the answer after a tool is heard."""
    player, turns, _ = _run(
        say("Let me look. **Plan:** call the tool", b"plan")
        + [LiveEvent("turn_complete", produced_output=True)]
        + say("Knight to f7.", b"move")
        + [LiveEvent("turn_complete", produced_output=True)])
    assert player.fed == [b"move"]
    assert [t.said for t in turns] == ["Knight to f7."]


@pytest.mark.parametrize("text", ["Knight to f7, it forks the king and queen.", "It costs $39.99 - 20% off.",
                                  "Click Continue at the bottom right.", "Press Ctrl+B for bold.",
                                  "I can help with C# code.", "It is item #3 on the list."])
def test_plain_speech_is_never_cut(text):
    player, turns, _ = _run(say(text, b"ok") + [LiveEvent("turn_complete", produced_output=True)])
    assert player.fed == [b"ok"] and turns[0].said == text
