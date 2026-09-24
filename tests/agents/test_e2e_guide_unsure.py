"""GW-12: when the guide cannot tell, it asks - once - instead of stalling.

Google Meet, 21 September, one step:

    screen changed - not done yet (the meeting screen is still loading)
    screen changed - not done yet ('Add others' visible but not clicked)
    screen changed - not done yet (the camera was turned off instead)
    step done - vision: the 'Add people' modal is now open

Something was clearly happening, the vision model kept saying it was not
the step, and the person heard nothing: the only thing that would have
spoken was the nudge a full minute in. After two "not done yet" answers on
a changed screen, the guide now points at the target again and asks - and
never advances on a guess.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from tutor_features import step_watch


def _watcher(answers):
    """A watcher whose vision model gives these answers in turn."""
    replies = iter(answers)

    async def judge(instruction, before, after):
        return next(replies)

    return step_watch.StepWatcher("Click Add others", judge=judge)


def _judge_once(w, monkeypatch):
    monkeypatch.setattr(step_watch, "grab_screen", lambda *a, **k: ("shot", None))
    asyncio.run(w._ask(step_watch.Fingerprint(title="Meet"), "screen"))


NO = {"done": False, "confidence": 0.9, "why": "Add others not clicked"}
YES = {"done": True, "confidence": 0.9, "why": "the dialog is open"}


def test_one_no_is_not_unsure(monkeypatch):
    w = _watcher([NO])
    _judge_once(w, monkeypatch)
    assert w.not_yet == 1 and not w.unsure


def test_two_noes_on_a_changing_screen_is_unsure(monkeypatch):
    w = _watcher([NO, NO])
    _judge_once(w, monkeypatch)
    _judge_once(w, monkeypatch)
    assert w.unsure and "not clicked" in w.last_reason


def test_a_yes_clears_the_doubt(monkeypatch):
    w = _watcher([NO, YES])
    _judge_once(w, monkeypatch)
    _judge_once(w, monkeypatch)
    assert w.not_yet == 0 and not w.unsure


# ── the manager asks once, points again, and never guesses ───────────────────

class UnsureWatcher:
    unsure = True
    not_yet = 2
    last_reason = "the camera was turned off"

    async def poll(self):
        return None


def _manager():
    from companion_manager import CompanionManager
    mgr = CompanionManager.__new__(CompanionManager)
    step = SimpleNamespace(instruction="Click Add others", target="Add others",
                           _watcher=UnsureWatcher())
    mgr._lesson_plan = SimpleNamespace(goal="g", steps=[step])
    mgr._guide_auto_advance = True
    mgr._guide_stats = {"asked": 0}
    said, pointed, submitted = [], [], []

    async def reply(text):
        said.append(text)

    async def point(idx, st):
        pointed.append(st.target)

    mgr._reply_local = reply
    mgr._point_step_target = point
    mgr._submit = lambda coro: submitted.append(coro)
    return mgr, step, said, pointed, submitted


def _drain(submitted):
    async def run_all():
        while submitted:
            await submitted.pop(0)
    asyncio.run(run_all())


def test_an_unsure_step_asks_and_points_again():
    mgr, step, said, pointed, submitted = _manager()
    result = asyncio.run(mgr._step_done(step))
    _drain(submitted)
    assert result is None, "asking is not advancing"
    assert said and "can't quite tell" in said[0] and "say next" in said[0]
    assert pointed == ["Add others"], "the arrow goes back to where the step is"
    assert mgr._guide_stats["asked"] == 1


def test_it_asks_only_once_per_step():
    mgr, step, said, pointed, submitted = _manager()
    for _ in range(5):
        asyncio.run(mgr._step_done(step))
        _drain(submitted)
    assert len(said) == 1, "asking again and again is nagging"
