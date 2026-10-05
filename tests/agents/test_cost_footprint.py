"""What an agent loop actually sends - measured, and held there.

Every iteration of the ReAct loop re-sends the whole conversation, so a
long loop pays for its own history again and again. These tests measure
the payload of a realistic loop and fail if it grows back: they are the
regression guard for the cost work, not a style check.
"""
from __future__ import annotations

import json

import pytest

from agents.core import ProviderMessage
from agents.tools import default_registry
from agents.workers import default_workers
from agents.workers.base import KEEP_FULL_RESULTS, trim_history


def tokens(text: str) -> int:
    return len(text) // 4


def test_old_tool_output_is_trimmed_but_never_lost():
    messages = [ProviderMessage(role="user", content="Goal: build the thing")]
    for i in range(6):
        messages.append(ProviderMessage(role="assistant", content=f"step {i}"))
        messages.append(ProviderMessage(role="tool", tool_call_id=str(i),
                                        content=f"result {i}: " + "x" * 4000))
    before = sum(len(m.content or "") for m in messages)
    trim_history(messages)
    after = sum(len(m.content or "") for m in messages)

    assert after < before * 0.6, "an old loop should not re-send every byte it has read"
    kept = [m for m in messages if m.role == "tool" and len(m.content) > 1000]
    assert len(kept) == KEEP_FULL_RESULTS, "the recent results stay verbatim"
    for i in range(6):
        assert any(f"result {i}:" in (m.content or "") for m in messages), \
            f"result {i} must still be visible in the history"


def test_only_the_newest_screenshot_is_carried():
    """A screenshot is ~500 tokens on Gemini and more on other providers.
    Re-sending three of them on every call was most of a computer task."""
    messages = [
        ProviderMessage(role="user", content="[image from screenshot]", images_b64=["a" * 100]),
        ProviderMessage(role="assistant", content="I see the window"),
        ProviderMessage(role="user", content="[image from screenshot]", images_b64=["b" * 100]),
        ProviderMessage(role="assistant", content="now the dialog"),
        ProviderMessage(role="user", content="[image from screenshot]", images_b64=["c" * 100]),
    ]
    trim_history(messages)
    carried = [m for m in messages if m.images_b64]
    assert len(carried) == 1 and carried[0].images_b64 == ["c" * 100]
    assert "already described" in messages[0].content


def test_worker_tool_schemas_stay_within_budget():
    """Tool schemas are re-sent on every call, so their size is a per-call
    tax. These numbers were measured; a big jump means a worker picked up
    a tool set it does not need."""
    reg = default_registry()
    workers = default_workers()
    # file: 2200 + render_design (5 Oct 2026, ~350 - trimmed from 655), the File Agent's posters.
    budget = {"coding": 3000, "file": 2400, "terminal": 1300, "research": 700,
              "browser": 700, "computer": 2100}
    for worker_type, limit in budget.items():
        names = list(workers.get(worker_type).default_tools)
        size = tokens(json.dumps([s.to_dict() for s in reg.specs(names)], default=str))
        assert size <= limit, f"{worker_type} sends {size} tokens of tool schema per call (budget {limit})"


def test_a_realistic_loop_is_cheaper_than_it_was(monkeypatch):
    """Ten iterations, each reading a file: what the provider receives."""
    def build(trim: bool) -> int:
        messages = [ProviderMessage(role="user", content="Goal: refactor the module")]
        total = 0
        for i in range(10):
            messages.append(ProviderMessage(role="assistant", content="calling read_file"))
            messages.append(ProviderMessage(role="tool", tool_call_id=str(i),
                                            content="file contents " + "y" * 3000))
            sent = trim_history(list(_clone(messages))) if trim else list(_clone(messages))
            total += sum(len(m.content or "") for m in sent)
        return total

    def _clone(messages):
        return [ProviderMessage(role=m.role, content=m.content, tool_call_id=m.tool_call_id,
                                images_b64=list(m.images_b64 or [])) for m in messages]

    before, after = build(False), build(True)
    saving = 1 - after / before
    # Measured at 46% on this shape of loop (ten reads of a 3 KB file). The
    # guard is set below it so a regression shows up, not so the number is
    # pinned: a longer loop saves more, a two-step loop saves nothing.
    assert saving > 0.4, f"expected the loop to get materially cheaper, saved {saving:.0%}"


# ── the per-turn cost: what rides along with every spoken question ──────────


def test_the_screen_context_is_not_repeated_word_for_word():
    """The Live API re-bills the whole conversation each turn, so a control
    list sent twenty times is paid for two hundred times. It is spelled out
    when it changes, and periodically in case compression dropped it."""
    from companion_manager import CompanionManager, CONTEXT_REPEAT_TURNS

    mgr = CompanionManager.__new__(CompanionManager)
    mgr._ctx_memo = (-1, "", 0)
    mgr._realtime = None
    line = 'Screen: "VS Code". Controls you can point at by name: ' + ", ".join(
        f"Button {i}" for i in range(28))

    first = mgr._context_head(line)
    assert "Button 27" in first, "the first turn must be told everything"
    repeats = [mgr._context_head(line) for _ in range(CONTEXT_REPEAT_TURNS - 1)]
    assert all("Button 27" not in r for r in repeats)
    assert all(len(r) < len(first) / 4 for r in repeats)

    assert "Button 27" in mgr._context_head(line), \
        "it must be spelled out again before the model can have forgotten it"
    assert "Notepad" in mgr._context_head('Screen: "Notepad".'), \
        "a different screen is always described in full"


def test_a_blank_session_is_told_the_screen_again():
    """A resumed session still has what it was told; a fresh one does not."""
    from companion_manager import CompanionManager

    mgr = CompanionManager.__new__(CompanionManager)
    mgr._ctx_memo = (-1, "", 0)
    mgr._realtime = type("C", (), {"epoch": 1})()
    line = 'Screen: "VS Code". Controls you can point at by name: Run, Debug'
    mgr._context_head(line)
    assert "Run, Debug" not in mgr._context_head(line)
    mgr._realtime.epoch = 2                     # the server dropped us
    assert "Run, Debug" in mgr._context_head(line)


# ── the idle cost: ambient wake-word checking ────────────────────────────────
#
# In ambient mode every speech segment near the machine is transcribed to see
# whether it was Tora's name. That is the one cost the user pays without
# asking for anything, so it is worth holding down.

ambient = pytest.importorskip("audio.ambient_listener")


def _blocks(n: int, loud: bool):
    import numpy as np
    level = 3000 if loud else 0
    return [np.full(ambient.FRAMES_PER_BLOCK, level, dtype=np.int16) for _ in range(n)]


def _listener(monkeypatch):
    sent: list[bytes] = []
    lis = ambient.AmbientListener(on_level=lambda rms: None, on_wake=lambda: None)
    monkeypatch.setattr(lis, "_dispatch_wake_check", lambda pcm: sent.append(pcm))
    lis._running = True
    return lis, sent


def _feed(lis, blocks):
    for b in blocks:
        lis._callback(b, len(b), None, None)


def test_only_the_opening_of_a_long_segment_is_checked():
    """The wake word is the first thing said. Everything after it is someone
    finishing their sentence, and transcribing it is paid for."""
    clip = ambient.wake_clip(_blocks(ambient.MAX_SEGMENT_BLOCKS, loud=True), 0)
    assert len(clip) == ambient.WAKE_WINDOW_BLOCKS
    seconds = len(clip) * ambient.BLOCK_MS / 1000
    assert 1.2 <= seconds <= 2.0, f"{seconds:.1f}s is not a wake-word-sized clip"


def test_the_silence_that_ended_a_segment_is_not_paid_for():
    clip = ambient.wake_clip(_blocks(50, loud=True), ambient.SILENCE_BLOCKS_END)
    assert len(clip) == 50 - ambient.SILENCE_BLOCKS_END + ambient.WAKE_TAIL_BLOCKS
    assert clip, "something must always be sent when there was speech"


def test_a_cough_or_a_keystroke_is_never_sent(monkeypatch):
    lis, sent = _listener(monkeypatch)
    _feed(lis, _blocks(ambient.MIN_SPEECH_BLOCKS, loud=True))
    _feed(lis, _blocks(ambient.SILENCE_BLOCKS_END, loud=False))
    assert not sent, "a 90ms noise is not a name being called"


def test_a_spoken_word_still_gets_checked(monkeypatch):
    lis, sent = _listener(monkeypatch)
    _feed(lis, _blocks(20, loud=True))        # ~600ms: "hey tora"
    _feed(lis, _blocks(ambient.SILENCE_BLOCKS_END, loud=False))
    assert len(sent) == 1, "the wake word must still wake Tora"


def test_nothing_is_transcribed_while_tora_is_already_answering(monkeypatch):
    """Her own voice comes back through the microphone; a wake word is
    refused in that state anyway, so checking it is pure spend."""
    lis, sent = _listener(monkeypatch)
    lis.set_wake_paused(True)
    _feed(lis, _blocks(40, loud=True))
    _feed(lis, _blocks(ambient.SILENCE_BLOCKS_END, loud=False))
    assert not sent
    lis.set_wake_paused(False)
    _feed(lis, _blocks(20, loud=True))
    _feed(lis, _blocks(ambient.SILENCE_BLOCKS_END, loud=False))
    assert len(sent) == 1, "and it starts again the moment she is idle"


