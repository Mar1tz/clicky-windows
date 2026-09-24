"""GW-11: the cheapest signal that can decide a step, decides it.

The watcher has a ladder: a click inside the target (free, instant), the
step's own UIA signal (free, milliseconds), and the vision model (a
screenshot upload and two to four seconds). The race this pins: a settled
screen change triggered the vision model after 0.6 s, but the step's own
signal was only re-read every 0.9 s - so vision was often asked about a
change that a free UIA read would have confirmed a moment later.

And the summary line now says which rung finished each step, so "vision
decides under 20 % of steps" is something the log can show.
"""
from __future__ import annotations

import asyncio

import pytest

from tutor_features import step_watch


# time.monotonic() is large in real life. The watcher's "last asked the
# vision model at" starts at 0.0, so a fake clock starting near zero would
# make the minimum gap between vision calls block the first one - and hide
# the very race this file is about. The first draft of this test did exactly
# that and "passed" against the old code for the wrong reason.
ORIGIN = 50_000.0


class Clock:
    def __init__(self):
        self.now = ORIGIN

    def __call__(self):
        return self.now


class Signal:
    """A step's own local check that goes true when the screen changes."""

    def __init__(self, state):
        self.state = state
        self.reads = 0

    def __call__(self):
        from ai.verification import Verdict
        self.reads += 1
        done = self.state["changed"]
        return Verdict(confidence=0.9 if done else 0.0,
                       method="control_appears" if done else "none",
                       detail="Downloads" if done else "")


def _run(monkeypatch, local_offset: float):
    """The screen changes at t=1.0 and the step's own signal goes true with
    it. The local check last ran `local_offset` s before the change."""
    clock = Clock()
    monkeypatch.setattr(step_watch.time, "monotonic", clock)
    state = {"changed": False}
    judged: list[float] = []

    async def judge(instruction, before, after):
        judged.append(clock.now)
        return {"done": True, "confidence": 0.9, "why": "looks done"}

    signal = Signal(state)
    w = step_watch.StepWatcher("Open Downloads", judge=judge, local_check=signal)

    async def fingerprint(now):
        return step_watch.Fingerprint(title="Brave - Downloads" if state["changed"]
                                      else "Brave", pid=1)

    monkeypatch.setattr(w, "_fingerprint", fingerprint)
    monkeypatch.setattr(step_watch, "grab_screen", lambda *a, **k: ("shot", None))

    async def scenario():
        w._began = True
        w._local_armed = True
        w._base = step_watch.Fingerprint(title="Brave", pid=1)
        w._last = w._base
        w._local_at = ORIGIN + 1.0 - local_offset
        t = 1.0
        while t < 4.0:
            clock.now = ORIGIN + t
            if t >= 1.0:
                state["changed"] = True
            if await w.poll():
                return t
            if w._judge_task is not None:
                await w._judge_task
            t = round(t + 0.05, 2)
        return None

    finished_at = asyncio.run(scenario())
    return finished_at, judged, w


def test_a_change_the_step_can_confirm_itself_never_reaches_the_vision_model(monkeypatch):
    """The local check ran just before the change, so its next scheduled
    read (0.9 s later) is after the vision model's settle window (0.6 s).
    Cheapest-first reads it on the settled change instead."""
    finished_at, judged, w = _run(monkeypatch, local_offset=0.05)
    assert finished_at is not None
    assert judged == [], f"vision was asked at {judged} about a change UIA confirms for free"
    assert "control_appears" in w.done_by


def test_vision_is_still_asked_when_nothing_cheaper_can_tell(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(step_watch.time, "monotonic", clock)
    judged: list[float] = []

    async def judge(instruction, before, after):
        judged.append(clock.now)
        return {"done": True, "confidence": 0.9, "why": "the chart is there"}

    w = step_watch.StepWatcher("Make the chart", judge=judge)     # no local signal
    frame = {"title": "Excel"}

    async def fingerprint(now):
        return step_watch.Fingerprint(title=frame["title"], pid=1)

    monkeypatch.setattr(w, "_fingerprint", fingerprint)
    monkeypatch.setattr(step_watch, "grab_screen", lambda *a, **k: ("shot", None))

    async def scenario():
        w._began = True
        w._base = step_watch.Fingerprint(title="Excel", pid=1)
        w._last = w._base
        t = 0.0
        while t < 4.0:
            clock.now = ORIGIN + t
            if t >= 1.0:
                frame["title"] = "Excel - Chart"
            if await w.poll():
                return True
            if w._judge_task is not None:
                await w._judge_task
            t = round(t + 0.05, 2)
        return False

    assert asyncio.run(scenario()) is True
    assert len(judged) == 1 and "vision" in w.done_by


# ── the summary says which rung finished each step ───────────────────────────

@pytest.mark.parametrize("done_by, kind", [
    ("click on target", "click"),
    ("vision: the dialog opened", "vision"),
    ("expectation: typed_text (11 characters typed)", "typed_text"),
    ("expectation: target_gone ('Help' is gone)", "target_gone"),
    ("expectation: control_appears (control_appears: Downloads)", "control_appears"),
    ("", "unknown"),
])
def test_each_finish_is_named_by_the_signal_that_made_it(done_by, kind):
    from companion_manager import _finish_kind
    assert _finish_kind(done_by) == kind


def test_the_summary_says_how_much_vision_decided(caplog):
    import logging
    import time as _time
    from types import SimpleNamespace

    from companion_manager import CompanionManager

    mgr = CompanionManager.__new__(CompanionManager)
    mgr._lesson_plan = SimpleNamespace(goal="g", steps=[])
    mgr._guide_stats = {
        "goal": "g", "steps": 5, "plan_s": 2.0, "started": _time.monotonic(),
        "auto": 4, "manual": 1, "by": {"click": 2, "typed_text": 1, "vision": 1},
        "judged": 1, "targets": {"uia": 5}, "missed": 0, "stuck": 0, "nudged": 0,
    }
    with caplog.at_level(logging.INFO, logger="tora.manager"):
        mgr._guide_event("ended", {"reason": "finished"})
    line = [r.getMessage() for r in caplog.records if r.getMessage().startswith("guide ended")][0]
    assert "click 2, typed_text 1, vision 1" in line
    assert "vision decided 20%" in line
