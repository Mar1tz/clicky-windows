"""GW-03: a walkthrough's whole life reaches the log.

Fourteen walkthroughs in tora.log recorded their start and nothing else —
not one ending, nudge or step outcome. The audit that found GW-01 had to
guess at durations, and every walkthrough complaint was guesswork. These
tests drive a real GuideRunner through the manager's own event handler and
assert the lines that come out, because every later walkthrough milestone
is measured from them.
"""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from tutor_features.guide import GuideRunner


def _manager_with_plan(steps):
    """The real _guide_event and _note_target, on a bare manager."""
    from companion_manager import CompanionManager
    import time

    mgr = CompanionManager.__new__(CompanionManager)
    mgr._lesson_plan = SimpleNamespace(goal="update the thing", steps=steps)
    mgr._guide_stats = {
        "goal": "update the thing", "steps": len(steps), "plan_s": 3.9,
        "started": time.monotonic(), "auto": 0, "manual": 0, "by": {},
        "judged": 0, "targets": {}, "missed": 0, "stuck": 0, "nudged": 0,
    }
    return mgr


def _steps(n):
    out = []
    for i in range(n):
        watcher = SimpleNamespace(done_by="", judged=0)
        step = SimpleNamespace(instruction=f"Do thing {i + 1}", target=f"Button {i + 1}")
        step._watcher = watcher
        out.append(step)
    return out


def test_a_walkthrough_writes_its_whole_life_down(caplog):
    steps = _steps(3)
    mgr = _manager_with_plan(steps)
    # Step 1 finishes by a click; step 2 is a manual "next"; step 3 by vision.
    steps[0]._watcher.done_by = "click on target"
    steps[2]._watcher.done_by = "vision: the dialog opened"
    steps[2]._watcher.judged = 2

    polls = {0: [False, True, True], 2: [False, True, True]}

    async def check(step):
        i = steps.index(step)
        queue = polls.get(i)
        return queue.pop(0) if queue else None

    async def speak(idx):
        if idx == 1:
            runner.request("next")          # the user says next on step 2

    async def say(_text):
        pass

    runner = GuideRunner(SimpleNamespace(steps=steps), speak_step=speak,
                         check=check, say=say, is_busy=lambda: False,
                         on_stop=lambda: None, on_event=mgr._guide_event,
                         poll_s=0.01, present_delay_s=0.0)

    async def scenario():
        await runner.start()
        for _ in range(300):
            if not runner.running:
                break
            await asyncio.sleep(0.01)

    with caplog.at_level(logging.INFO, logger="tora.manager"):
        asyncio.run(scenario())
    lines = [r.getMessage() for r in caplog.records]
    text = "\n".join(lines)

    assert "guide step 1/3 shown: Do thing 1" in text
    assert "guide step 1/3 finished by itself" in text and "(click on target)" in text
    assert "guide step 2/3 ended: next" in text
    assert "guide step 3/3 finished by itself" in text and "2 vision check(s)" in text

    summary = [l for l in lines if l.startswith("guide ended")]
    assert len(summary) == 1, "exactly one summary per walkthrough"
    s = summary[0]
    assert "guide ended (finished)" in s
    assert "3 steps, 2 finished by themselves" in s
    assert "1 manual moves" in s and "2 vision checks" in s
    assert "planned in 3.9s" in s


def test_stopping_part_way_is_recorded_with_its_reason(caplog):
    steps = _steps(3)
    mgr = _manager_with_plan(steps)

    async def check(step):
        return None

    async def speak(idx):
        runner.request("stop")

    async def say(_text):
        pass

    runner = GuideRunner(SimpleNamespace(steps=steps), speak_step=speak,
                         check=check, say=say, is_busy=lambda: False,
                         on_stop=lambda: None, on_event=mgr._guide_event,
                         poll_s=0.01, present_delay_s=0.0)

    async def scenario():
        await runner.start()
        for _ in range(200):
            if not runner.running:
                break
            await asyncio.sleep(0.01)

    with caplog.at_level(logging.INFO, logger="tora.manager"):
        asyncio.run(scenario())
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "guide ended (stopped by the user)" in text
    assert "0 finished by themselves" in text


def test_the_tier_that_found_each_target_is_recorded(caplog):
    """52 targets went to the vision model across 14 walkthroughs and there
    was no way to see which. GW-18 is measured from this line."""
    steps = _steps(3)
    mgr = _manager_with_plan(steps)
    with caplog.at_level(logging.INFO, logger="tora.manager"):
        mgr._note_target(0, "Help", SimpleNamespace(via="uia"), 0.04)
        mgr._note_target(1, "Check for Updates", SimpleNamespace(via="vision"), 2.8)
        mgr._note_target(2, "Restart", None, 3.1)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "target 'Help' found via uia" in text
    assert "found via vision in 2.80s" in text
    assert "target 'Restart' NOT FOUND" in text
    assert mgr._guide_stats["targets"] == {"uia": 1, "vision": 1}
    assert mgr._guide_stats["missed"] == 1


def test_a_reporting_hook_that_breaks_never_stops_the_walkthrough():
    steps = _steps(2)

    def explode(kind, detail):
        raise RuntimeError("the logger fell over")

    spoken = []

    async def speak(idx):
        spoken.append(idx)
        if idx == 0:
            runner.request("next")

    async def check(step):
        return None

    async def say(_text):
        pass

    runner = GuideRunner(SimpleNamespace(steps=steps), speak_step=speak,
                         check=check, say=say, is_busy=lambda: False,
                         on_stop=lambda: None, on_event=explode,
                         poll_s=0.01, present_delay_s=0.0)

    async def scenario():
        await runner.start()
        for _ in range(100):
            if len(spoken) >= 2:
                break
            await asyncio.sleep(0.01)
        runner.stop()
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert spoken == [0, 1], "reporting is never allowed to break the thing it reports on"
