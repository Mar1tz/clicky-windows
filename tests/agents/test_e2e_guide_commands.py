"""GW-14: every walkthrough command has one defined, tested meaning.

Before this, the same words meant different things in different places.
The runner's nudge said "'back' to hear it again" - back is the step
BEFORE. The tool description called next "skip ahead" and back "repeat the
previous step". "Skip" and "pause" were not commands at all, "where is it"
only worked when the model happened to misread it as "next", and "I'm
stuck" either got nothing or would have got two voices at once.

The table is above CompanionManager._GUIDE_COMMANDS. Each row is pinned
here.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from types import SimpleNamespace

import pytest

from ai.gemini_planner import Guide, Step
from tutor_features.guide import GuideRunner
from tutor_features.guide_intent import guide_intent


def _plan(n=3, target=True):
    return Guide(goal="export the video", steps=[
        Step(instruction=f"Click Button {i + 1}.",
             target=f"Button {i + 1}" if target else "",
             possible_actions=[f"Press Ctrl+{i + 1}"])
        for i in range(n)])


def _guide(heard="", *, at=0, steps=3, target=True):
    """A bare manager with a running walkthrough on step `at`."""
    from companion_manager import CompanionManager

    async def nothing(*_):
        return None

    plan = _plan(steps, target)
    runner = GuideRunner(plan, speak_step=nothing, check=nothing, say=nothing,
                         is_busy=lambda: False, on_stop=lambda: None)
    runner._idx = at
    runner._task = SimpleNamespace(done=lambda: False)
    mgr = CompanionManager.__new__(CompanionManager)
    mgr._guide_runner = runner
    mgr._lesson_plan = plan
    mgr._resume = None
    mgr._guide_stats = {"said": {}}
    mgr._request_serial, mgr._guide_moved_for = 1, -1
    mgr._heard_buf = [heard] if heard else []
    mgr.pointed = []

    async def point(idx, step):
        mgr.pointed.append(step.target)

    mgr._point_step_target = point
    mgr.pending = []
    mgr._submit = mgr.pending.append
    return mgr, runner


def _say(mgr, action):
    """Call guide_step as the live model would, then let the pointing run."""
    async def run():
        out = await mgr._tool_guide_step({"action": action})
        for coro in mgr.pending:
            await coro
        mgr.pending.clear()
        return out
    return asyncio.run(run())


# ── the words ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("said, intent", [
    ("skip", "next"), ("skip this one", "next"),
    ("pause", "pause"), ("pause the guide", "pause"), ("let's take a break", "pause"),
    ("I'm stuck", "help"), ("help", "help"), ("it's not working", "help"),
    ("it's not working again", "help"),       # a cry for help, not "say it again"
    ("do it for me", "do_it"), ("can you just do it", "do_it"),
    ("help me find the save button", "where"), ("I don't see it", "where"),
    # a pause or a "do it" nobody asked for must not happen...
    ("don't pause", None), ("don't do it for me, I want to learn", None),
    # ...but a negated command does not end the reading
    ("I don't know how, can you do it for me", "help"),
])
def test_what_the_new_commands_sound_like(said, intent):
    assert guide_intent(said) == intent


def test_the_model_is_offered_exactly_the_commands_the_hub_knows():
    from ai.gemini_realtime import tool_declarations
    from companion_manager import CompanionManager

    decls = {d["name"]: d for d in tool_declarations()[0]["functionDeclarations"]}
    enum = decls["guide_step"]["parameters"]["properties"]["action"]["enum"]
    assert sorted(enum) == sorted(CompanionManager._GUIDE_COMMANDS)


def _context(mgr, monkeypatch) -> str:
    from ai import screen_summary

    monkeypatch.setattr(screen_summary, "describe",
                        lambda **kw: SimpleNamespace(names=[], pointables=[], line="x"))
    mgr._privacy_guard = None
    mgr._context_head = lambda line: "[context]"
    mgr._ref_fresh = lambda: False
    mgr._last_ref = None
    mgr._lesson_step_idx = 0
    plan = mgr._lesson_plan
    mgr._lesson_steps = [s.instruction for s in plan.steps] if plan else []
    mgr._pending_confirmation_line = lambda: ""
    mgr._attached_docs = []
    mgr.agent_context_line = lambda: ""
    return asyncio.run(mgr._screen_context())


def test_the_model_is_told_every_command_mid_walkthrough(monkeypatch):
    from companion_manager import CompanionManager

    mgr, _ = _guide()
    text = _context(mgr, monkeypatch)
    commands = text.split("Call guide_step ONLY for a command:", 1)[1].split("Everything else", 1)[0]
    for command in CompanionManager._GUIDE_COMMANDS:
        if command != "resume":                   # nothing to resume while one runs
            assert re.search(rf"\b{command}\b", commands), command
    assert 'arrow is already on "Button 1"' in text


def test_a_stopped_walkthrough_is_named_so_resume_means_that_one(monkeypatch):
    mgr, _ = _guide()
    mgr._guide_runner = None
    mgr._resume = (mgr._lesson_plan, 1)
    mgr._lesson_plan = None
    text = _context(mgr, monkeypatch)
    assert 'toward "export the video" was stopped at step 2 of 3' in text
    assert "action 'resume'" in text


# ── next · skip · back · repeat ──────────────────────────────────────────────

def test_next_moves_on_one_step():
    mgr, runner = _guide("next")
    out = _say(mgr, "next")
    assert runner._pending == "next" and out["where"] == "now on step 2 of 3"
    assert mgr._guide_stats["said"] == {"next": 1}


def test_skip_is_the_same_move_counted_as_a_skip():
    mgr, runner = _guide("skip this one")
    out = _say(mgr, "skip")
    assert runner._pending == "next" and out["where"] == "now on step 2 of 3"
    assert mgr._guide_stats["said"] == {"skip": 1}, "a skipped step is not a step done"


def test_back_goes_to_the_step_before():
    mgr, runner = _guide("go back", at=1)
    out = _say(mgr, "back")
    assert runner._pending == "back" and out["where"] == "now on step 1 of 3"


def test_back_on_the_first_step_says_the_first_step_again():
    mgr, runner = _guide("go back", at=0)
    out = _say(mgr, "back")
    assert runner._pending == "back"
    assert "step 1 of 3" in out["where"] and "first step" in out["where"]


def test_repeat_says_this_step_again_without_moving_on():
    mgr, runner = _guide("say that again", at=1)
    out = _say(mgr, "repeat")
    assert runner._pending == "repeat" and out["where"] == "now on step 2 of 3"


# ── stop · pause · resume ────────────────────────────────────────────────────

def test_stop_ends_it():
    mgr, runner = _guide("stop")
    out = _say(mgr, "stop")
    assert runner._pending == "stop" and "stopped" in out["note"]


def test_pause_ends_it_and_says_how_to_come_back():
    mgr, runner = _guide("pause")
    out = _say(mgr, "pause")
    assert runner._pending == "pause" and "'resume'" in out["note"]
    assert mgr._guide_stats["said"] == {"pause": 1}


@pytest.mark.parametrize("command, reason", [("stop", "stopped by the user"),
                                              ("pause", "paused by the user")])
def test_stop_and_pause_both_keep_the_step_for_resume(command, reason):
    """The real loop: the command lands on step two, the walkthrough ends
    with its own reason, and on_stop still sees the step it was on - which
    is what the hub keeps for "resume"."""
    ended, kept = [], []

    async def speak(idx):
        runner.request(command)

    async def check(step):
        return None

    async def say(_):
        pass

    def on_event(kind, detail):
        if kind == "ended":
            ended.append(detail["reason"])

    runner = GuideRunner(_plan(3), speak_step=speak, check=check, say=say,
                         is_busy=lambda: False,
                         on_stop=lambda: kept.append(runner.index),
                         on_event=on_event, poll_s=0.01, present_delay_s=0.0,
                         start_index=1)

    async def scenario():
        await runner.start()
        for _ in range(200):
            if not runner.running:
                break
            await asyncio.sleep(0.01)

    asyncio.run(scenario())
    assert ended == [reason]
    assert kept == [1] and not runner.completed


def test_resume_picks_a_stopped_walkthrough_up_at_its_step():
    mgr, _ = _guide()
    mgr._guide_runner = None
    plan = _plan(3)
    mgr._resume = (plan, 1)
    started = []
    mgr._start_guide = lambda guide, start_index=0: started.append((guide, start_index))
    out = _say(mgr, "resume")
    assert started == [(plan, 1)]
    assert out["where"] == "back on step 2 of 3"
    assert "do not read the step" in out["note"], "Tora says the step herself"


def test_resume_with_nothing_stopped_says_so():
    mgr, _ = _guide()
    mgr._guide_runner = None
    assert "error" in _say(mgr, "resume")


def test_resume_while_one_is_running_leaves_it_alone():
    mgr, runner = _guide("resume", at=1)
    started = []
    mgr._start_guide = lambda guide, start_index=0: started.append(start_index)
    out = _say(mgr, "resume")
    assert started == [] and runner._pending is None
    assert out["moved"] is False and "already running" in out["note"]
    assert out["where"] == "still on step 2 of 3"


# ── where · help · do_it: none of them moves the guide ───────────────────────

def test_where_points_at_this_steps_target_again():
    mgr, runner = _guide("where is it", at=1)
    out = _say(mgr, "where")
    assert mgr.pointed == ["Button 2"] and runner._pending is None
    assert out["moved"] is False and out["where"] == "still on step 2 of 3"


def test_where_on_a_step_with_nothing_to_point_at_says_so():
    mgr, _ = _guide("where is it", target=False)
    out = _say(mgr, "where")
    assert mgr.pointed == [] and "no one thing to point at" in out["note"]


def test_help_points_again_and_hands_the_model_another_way():
    mgr, runner = _guide("I'm stuck", at=1)
    out = _say(mgr, "help")
    assert mgr.pointed == ["Button 2"] and runner._pending is None
    assert "Click Button 2." in out["note"], "the model is told what they are stuck on"
    assert "Press Ctrl+2" in out["note"], "and the planner's other route"
    assert "do not just repeat" in out["note"]


def test_help_is_one_voice_not_two():
    """Tora only points; the model explains. The runner's own stuck-help
    speaks locally - doing that here as well would talk over the model."""
    mgr, _ = _guide("I'm stuck")
    spoken = []

    async def reply_local(text):
        spoken.append(text)

    mgr._reply_local = reply_local
    _say(mgr, "help")
    assert spoken == []


@pytest.mark.parametrize("asked, offered", [(True, []), (False, [0])])
def test_after_help_the_runner_does_not_offer_the_same_help_again(asked, offered):
    helped = []
    idle = {"s": 0.0}

    async def nothing(*_):
        return None

    async def stuck(idx):
        helped.append(idx)

    observer = SimpleNamespace(poll=lambda: None, idle_seconds=lambda: idle["s"],
                               reset=lambda: None)
    runner = GuideRunner(_plan(2), speak_step=nothing, check=nothing, say=nothing,
                         is_busy=lambda: False, on_stop=lambda: None,
                         on_stuck=stuck, observer=observer, poll_s=0.01,
                         present_delay_s=0.0, stuck_after_s=0.05)

    async def scenario():
        await runner.start()
        while runner._shown < 1:                 # step one has been spoken
            await asyncio.sleep(0.005)
        if asked:
            runner.mark_helped()                 # "I'm stuck", answered
        idle["s"] = 999.0                        # then the screen sits still
        await asyncio.sleep(0.2)
        runner.stop()
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert helped == offered


def test_do_it_says_it_cannot_yet_and_points():
    mgr, runner = _guide("do it for me")
    out = _say(mgr, "do_it")
    assert mgr.pointed == ["Button 1"] and runner._pending is None
    assert "cannot do steps for them yet" in out["note"]


@pytest.mark.parametrize("heard, label", [("I'm stuck", "for HELP"),
                                          ("can you do it for me", "Tora to DO it")])
def test_help_misread_as_next_gets_the_help_instead(heard, label):
    mgr, runner = _guide(heard)
    out = _say(mgr, "next")
    assert runner._pending is None and out["moved"] is False
    assert label in out["note"] and mgr.pointed == ["Button 1"]


# ── the edges ────────────────────────────────────────────────────────────────

def test_an_action_said_with_a_space_is_understood():
    mgr, _ = _guide("do it for me")
    out = _say(mgr, "do it")
    assert out["moved"] is False and "cannot do steps" in out["note"]


def test_an_unknown_command_lists_the_real_ones():
    mgr, _ = _guide()
    out = _say(mgr, "jump")
    assert "error" in out and "do_it" in out["error"]


def test_the_nudge_teaches_the_right_word_for_hearing_it_again():
    said = []

    async def nothing(*_):
        return None

    async def say(text):
        said.append(text)

    runner = GuideRunner(_plan(1), speak_step=nothing, check=nothing, say=say,
                         is_busy=lambda: False, on_stop=lambda: None,
                         poll_s=0.01, present_delay_s=0.0, step_timeout_s=0.05)

    async def scenario():
        await runner.start()
        for _ in range(100):
            if said:
                break
            await asyncio.sleep(0.01)
        runner.stop()
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert said and "'repeat' to hear it again" in said[0]
    assert "'back' to" not in said[0], "back is the step before, not this one again"


def test_the_summary_says_which_commands_were_used(caplog):
    mgr, _ = _guide("where is it")
    mgr._guide_stats = {
        "goal": "g", "steps": 3, "plan_s": None, "started": time.monotonic(),
        "auto": 0, "manual": 0, "by": {}, "judged": 0, "targets": {}, "missed": 0,
        "stuck": 0, "nudged": 0, "asked": 0, "said": {},
    }
    _say(mgr, "where")
    mgr._heard_buf = ["I'm stuck"]
    _say(mgr, "help")
    with caplog.at_level(logging.INFO, logger="tora.manager"):
        mgr._guide_event("ended", {"reason": "stopped by the user"})
    line = [r.getMessage() for r in caplog.records
            if r.getMessage().startswith("guide ended")][0]
    assert "commands: help 1, where 1" in line
