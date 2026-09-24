"""LG-06: the next step's arrow is ready before the step is.

A step's target is usually made visible by the step before it: clicking
"Captions" is what puts "Auto captions" on screen. So while step N is being
done, step N+1's target is looked up the moment the screen settles after a
change - on the free tiers only - and when step N+1 goes up its arrow can go
up with it, with no lookup at all. A look that is stale, or from another
window, is not trusted.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import companion_manager as cm
from ai import hybrid_pointer
from ai.gemini_planner import Guide, Step


def _manager(monkeypatch, *, title="CapCut"):
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    plan = Guide(goal="g", steps=[Step(instruction="Click Captions.", target="Captions"),
                                  Step(instruction="Click Auto captions on the left.",
                                       target="Auto captions")])
    mgr._lesson_plan, mgr._lesson_step_idx = plan, 0
    mgr._privacy_guard = False
    mgr._dpi_scale = lambda: 1.0
    mgr._activity = lambda *_a: None
    windows = {"title": title}
    monkeypatch.setattr(cm, "active_window_title", lambda: windows["title"])
    return mgr, plan, windows


def test_the_next_target_is_looked_up_once_the_screen_settles(monkeypatch):
    mgr, plan, _w = _manager(monkeypatch)
    looked = []

    async def resolve(query, **kw):
        looked.append((query, kw.get("vision")))
        return SimpleNamespace(x=40.0, y=300.0, label=query, via="ocr", bbox=None)

    mgr._resolve_point = resolve

    async def run():
        watcher = SimpleNamespace(changed_at=time.monotonic())
        mgr._look_ahead(watcher)                  # changed, not settled yet
        await asyncio.sleep(0)
        assert looked == []
        watcher.changed_at = time.monotonic() - 1.0
        mgr._look_ahead(watcher)                  # settled: look
        mgr._look_ahead(watcher)                  # same change: not again
        await asyncio.sleep(0.01)

    asyncio.run(run())
    assert looked == [("Auto captions", False)], "once, and never with the paid model"
    found, _at, window = mgr._step_found[(id(plan), 1)]
    assert found.via == "ahead ocr" and window == "CapCut"


def test_a_look_ahead_is_free(monkeypatch):
    """vision=False: a miss is a miss - no screenshot goes to the model."""
    mgr, _plan, _w = _manager(monkeypatch)
    asked = []

    async def vision_point(*a, **kw):
        asked.append(a)

    monkeypatch.setattr(hybrid_pointer, "find_target", lambda query, **kw: None)
    mgr._vision_point = vision_point
    assert asyncio.run(mgr._resolve_point("Auto captions", vision=False)) is None
    assert asked == []


def _cached(mgr, plan, *, age=0.5, window="CapCut"):
    found = SimpleNamespace(x=40.0, y=300.0, label="Auto captions", via="ahead ocr", bbox=None)
    mgr._step_found = {(id(plan), 1): (found, time.monotonic() - age, window)}
    return found


def test_a_fresh_look_ahead_is_the_arrow_at_once(monkeypatch):
    mgr, plan, _w = _manager(monkeypatch)
    found = _cached(mgr, plan)
    looked = []

    async def resolve(query, **kw):
        looked.append(query)

    mgr._resolve_point = resolve
    got = asyncio.run(mgr._step_target(plan, 1, plan.steps[1]))
    assert got is found and looked == [], "no lookup: it was already found"


def test_a_stale_look_ahead_is_not_trusted(monkeypatch):
    mgr, plan, _w = _manager(monkeypatch)
    _cached(mgr, plan, age=mgr.AHEAD_TRUST_S + 1)
    looked = []

    async def resolve(query, **kw):
        looked.append(query)
        return SimpleNamespace(x=1.0, y=2.0, label=query, via="uia", bbox=None)

    mgr._resolve_point = resolve
    asyncio.run(mgr._step_target(plan, 1, plan.steps[1]))
    assert looked == ["Auto captions"], "looked again, against the screen as it is"


def test_a_look_ahead_from_another_window_is_not_trusted(monkeypatch):
    mgr, plan, windows = _manager(monkeypatch)
    _cached(mgr, plan, window="CapCut")
    windows["title"] = "Settings"                 # they switched away
    looked = []

    async def resolve(query, **kw):
        looked.append(query)

    mgr._resolve_point = resolve
    asyncio.run(mgr._step_target(plan, 1, plan.steps[1]))
    assert looked == ["Auto captions"]


def test_nothing_is_looked_ahead_for_a_step_with_nothing_to_point_at(monkeypatch):
    mgr, plan, _w = _manager(monkeypatch)
    plan.steps[1].target = ""
    started = []
    mgr._find_ahead = lambda *a: started.append(a) or asyncio.sleep(0)
    mgr._look_ahead(SimpleNamespace(changed_at=time.monotonic() - 1.0))
    assert started == []


def test_the_watcher_says_when_the_screen_changed(monkeypatch):
    """The look-ahead's trigger: a real StepWatcher, scripted frames."""
    from tutor_features import step_watch

    async def never(*a):
        return {"done": False, "confidence": 0.9, "why": "not yet"}

    w = step_watch.StepWatcher("Click Captions.", judge=never)
    frame = {"title": "CapCut"}

    async def fingerprint(now):
        return step_watch.Fingerprint(title=frame["title"], pid=1)

    monkeypatch.setattr(w, "_fingerprint", fingerprint)
    monkeypatch.setattr(step_watch, "grab_screen", lambda *a, **k: ("shot", None))

    async def scenario():
        w._began = True
        w._base = step_watch.Fingerprint(title="CapCut", pid=1)
        await w.poll()
        assert w.changed_at == 0.0, "nothing changed yet"
        frame["title"] = "CapCut - Captions"
        await w.poll()
        return w.changed_at

    assert asyncio.run(scenario()) > 0.0
