"""APP-04: pointing never uses an old screen.

The pointer's first tier is the control snapshot gathered when the user
last spoke - free, and usually right for "where is X?" in the same turn.
But a walkthrough step presented a minute and three screens later was
resolved against it too, so a control sharing a name with one from back
then ("Save" in a dialog and in the main window) got the arrow where the
OLD one was. Now the snapshot answers only while it is fresh.
"""
from __future__ import annotations

import time

import pytest

SAVE_IN_MAIN_WINDOW = [(400, 300, "Save", "ButtonControl"),
                       (900, 300, "Cancel", "ButtonControl")]


@pytest.fixture
def mgr(monkeypatch):
    import companion_manager as cm

    title = {"now": "Invoicer - Main"}
    monkeypatch.setattr(cm, "active_window_title", lambda: title["now"])
    m = cm.CompanionManager.__new__(cm.CompanionManager)
    m._dpi_scale = lambda: 1.0
    m._pointables = SAVE_IN_MAIN_WINDOW
    m._pointables_at = time.monotonic()
    m._pointables_title = "Invoicer - Main"
    m.title = title
    return m


def test_a_fresh_snapshot_answers_at_once(mgr):
    found = mgr._match_snapshot("Save")
    assert found is not None and (found.x, found.y) == (400, 300)


def test_an_old_snapshot_is_not_trusted(mgr, monkeypatch):
    mgr._pointables_at = time.monotonic() - (mgr.SNAPSHOT_TRUST_S + 1)
    assert mgr._match_snapshot("Save") is None, "the live search runs instead"


def test_a_snapshot_of_another_window_is_not_trusted(mgr):
    mgr.title["now"] = "Save As"                     # a dialog came up
    assert mgr._match_snapshot("Save") is None


def test_once_a_walkthrough_step_ends_the_snapshot_is_spent(mgr):
    from types import SimpleNamespace

    mgr._lesson_plan = SimpleNamespace(steps=[SimpleNamespace(instruction="Click Save.",
                                                              _watcher=None)])
    mgr._guide_stats = None
    mgr._plan_in_progress = None
    mgr._guide_event("step_ended", {"index": 0, "outcome": "done", "secs": 2.0})
    assert mgr._match_snapshot("Save") is None, "the screen was just acted on"


def test_a_manager_that_never_took_one_has_none(monkeypatch):
    import companion_manager as cm

    monkeypatch.setattr(cm, "active_window_title", lambda: "X")
    m = cm.CompanionManager.__new__(cm.CompanionManager)
    m._pointables = SAVE_IN_MAIN_WINDOW
    assert m._snapshot_fresh() is False and m._match_snapshot("Save") is None
