"""GW-09: "wait for it" is its own kind of step.

CapCut, 22 September: step three was "wait for the captions to generate".
The watcher treated it like a click — every time the progress bar moved
and settled, it asked the vision model whether the user had done the step:

    23:32:17  not done yet (caption processing is still in progress at 45%)
    23:32:31  step done — vision: processing has finished

A progress bar changes the screen every second. It only goes still when
the work is done. So a waiting step is watched with a long settle window:
the vision model is asked once, at the end, instead of all the way through.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ai import gemini_planner
from ai.gemini_planner import Guide, Step
from tutor_features import step_watch


# ── the planner can say a step is waiting ────────────────────────────────────

def test_a_waiting_step_comes_back_marked_and_without_a_target(monkeypatch):
    async def fake(model, contents, schema, **kw):
        return {"goal": "g", "feasible": True, "preamble": "", "steps": [
            {"instruction": "Click Generate.", "target": "Generate", "kind": "action",
             "done_when": "", "possible_actions": [], "expected_state": {},
             "completion": {"kind": "none", "value": ""}},
            {"instruction": "Wait for the captions to generate.", "target": "Progress",
             "kind": "wait", "done_when": "caption blocks appear", "possible_actions": [],
             "expected_state": {"control_appears": "Captions"},
             "completion": {"kind": "none", "value": ""}},
        ]}

    monkeypatch.setattr(gemini_planner.gemini, "generate_json", fake)
    guide = asyncio.run(gemini_planner.plan("auto captions"))
    assert [s.kind for s in guide.steps] == ["action", "wait"]
    assert guide.steps[1].waiting and guide.steps[1].target == "", \
        "nothing to point at while the app works"


def test_the_planner_is_told_what_a_waiting_step_is():
    assert 'kind "wait"' in gemini_planner._SYSTEM
    assert "generating" in gemini_planner._SYSTEM


def test_an_older_plan_without_kinds_is_all_actions():
    assert Step(instruction="x").kind == "action"


# ── Tora says she will speak up ──────────────────────────────────────────────

def _manager(steps):
    from companion_manager import CompanionManager
    mgr = CompanionManager.__new__(CompanionManager)
    mgr._lesson_plan = Guide(goal="g", steps=steps)
    return mgr


def test_a_waiting_step_says_nothing_needs_doing_and_tora_will_speak_up():
    mgr = _manager([Step(instruction="Click Generate."),
                    Step(instruction="Wait for the captions.", kind="wait"),
                    Step(instruction="Click Text.")])
    assert "I'll tell you when it's done" in mgr._step_line(1)
    assert mgr._step_line(2).startswith("That's finished."), \
        "and says so when it has, before the next instruction"
    assert "finished" not in mgr._step_line(0)


# ── the watcher asks once, at the end ────────────────────────────────────────

class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _drive(waiting: bool, monkeypatch) -> list[float]:
    """20 s of a progress bar moving every second, then 8 s of stillness.
    Returns the times the vision model was asked."""
    clock = Clock()
    monkeypatch.setattr(step_watch.time, "monotonic", clock)
    asked: list[float] = []

    async def judge(instruction, before, after):
        asked.append(clock.now)
        return {"done": False, "confidence": 0.9, "why": "still going"}

    w = step_watch.StepWatcher("Wait for the captions to generate", judge=judge,
                               waiting=waiting)
    frame = {"n": 0}

    async def fingerprint(now):
        return step_watch.Fingerprint(title="CapCut", pid=1, focused="",
                                      thumb=bytes([frame["n"] % 256]) * 16)

    async def fake_grab(*_a, **_k):
        return "shot", bytes(16)

    monkeypatch.setattr(w, "_fingerprint", fingerprint)
    monkeypatch.setattr(step_watch, "THUMB_DIFF_MIN", 1.0)

    async def scenario():
        w._began = True
        w._base = step_watch.Fingerprint(title="CapCut", pid=1, focused="",
                                         thumb=bytes(16))
        w._last = w._base
        w._stable_since = 0.0
        # The after-shot must be the SAME frame the poll saw - otherwise the
        # rebase after "not done yet" differs from every later poll and the
        # watcher looks changed forever, which is the harness lying.
        monkeypatch.setattr(step_watch, "grab_screen",
                            lambda *a, **k: ("shot", bytes([frame["n"] % 256]) * 16))
        t = 0.0
        while t < 28.0:
            clock.now = t
            if t < 20.0 and abs(t - round(t)) < 1e-9:
                frame["n"] += 7                       # the bar moves each second
            await w.poll()
            if w._judge_task is not None:
                await w._judge_task
            t = round(t + 0.25, 2)

    asyncio.run(scenario())
    return asked


def test_a_moving_progress_bar_is_not_asked_about_again_and_again(monkeypatch):
    ordinary = _drive(waiting=False, monkeypatch=monkeypatch)
    assert len(ordinary) >= 5, (
        "the old behaviour, reproduced: an action step asks about every "
        f"settled change while the bar moves (asked {len(ordinary)} times)")


def test_a_waiting_step_asks_once_after_the_work_stops(monkeypatch):
    asked = _drive(waiting=True, monkeypatch=monkeypatch)
    assert len(asked) == 1, f"once, at the end - got {len(asked)} at {asked}"
    last_move = 19.0                     # the bar moves on each whole second < 20
    assert asked[0] >= last_move + step_watch.WAIT_SETTLE_S, \
        f"only once the screen had been still for the settle window (asked at {asked[0]})"


# ── waiting is not being stuck ───────────────────────────────────────────────

def test_the_runner_does_not_nudge_or_offer_help_during_a_wait():
    from tutor_features.guide import GuideRunner

    said: list[str] = []
    helped: list[int] = []

    async def speak(idx):
        pass

    async def check(step):
        return None

    async def say(text):
        said.append(text)

    async def stuck(idx):
        helped.append(idx)

    observer = SimpleNamespace(poll=lambda: None, idle_seconds=lambda: 999.0,
                               reset=lambda: None)
    guide = Guide(goal="g", steps=[Step(instruction="Wait for the export.", kind="wait")])
    runner = GuideRunner(guide, speak_step=speak, check=check, say=say,
                         is_busy=lambda: False, on_stop=lambda: None,
                         on_stuck=stuck, observer=observer, poll_s=0.01,
                         present_delay_s=0.0, step_timeout_s=0.2,
                         stuck_after_s=0.05)

    async def scenario():
        await runner.start()
        await asyncio.sleep(0.5)            # past the ordinary timeout, inside the wait one
        runner.stop()
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert helped == [], "a render that is quiet is not a person who is stuck"
    assert not any("Take your time" in s for s in said), \
        "and 'say next when you've done it' is the wrong thing to say about an export"
