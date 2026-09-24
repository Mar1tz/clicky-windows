"""LG-09: every walkthrough measured.

"It only showed me at the end" had to be reconstructed from scattered log
lines. Now each step writes one timeline line - its arrow, and by which
tier, and its voice, measured from the step going up - and every guide ends
with a scorecard: what LIVE_GUIDE_PLAN's numbers are checked against.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from companion_manager import CompanionManager


def _manager(clock):
    mgr = CompanionManager.__new__(CompanionManager)
    mgr._guide_stats = {"clock": clock}
    return mgr


def test_a_step_says_when_its_arrow_and_voice_came(caplog):
    mgr = _manager({0: {"shown": 10.0, "arrow": 10.4, "via": "ahead uia", "voice": 11.2}})
    with caplog.at_level(logging.INFO, logger="tora.manager"):
        mgr._log_step_timeline(0, SimpleNamespace(target="Captions"))
    assert "guide step 1 timeline: arrow +0.4s (ahead uia), voice +1.2s" in caplog.text
    assert mgr._guide_stats["arrows"] == pytest.approx([0.4])
    assert mgr._guide_stats["ahead"] == 1


def test_a_step_with_a_target_and_no_arrow_says_so(caplog):
    mgr = _manager({1: {"shown": 20.0}})
    with caplog.at_level(logging.INFO, logger="tora.manager"):
        mgr._log_step_timeline(1, SimpleNamespace(target="address bar"))
    assert "guide step 2 timeline: no arrow, not said" in caplog.text
    assert mgr._guide_stats["no_arrow"] == 1


def test_a_keyboard_step_is_not_counted_as_a_missing_arrow(caplog):
    mgr = _manager({0: {"shown": 5.0, "voice": 5.5}})
    with caplog.at_level(logging.INFO, logger="tora.manager"):
        mgr._log_step_timeline(0, SimpleNamespace(target=""))
    assert "no arrow" not in caplog.text and "voice +0.5s" in caplog.text


def test_the_scorecard_holds_the_numbers_live_is_judged_by():
    card = CompanionManager._scorecard({
        "arrows": [0.3, 0.4, 0.2, 2.1], "ahead": 2, "no_arrow": 1, "unsaid": 1,
        "windows_voice": True, "next_gaps": [0.4, 0.6, 0.5]})
    assert "arrow p50 0.4s p90 2.1s (4, 2 found ahead)" in card
    assert "no arrow 1" in card and "not said 1" in card
    assert "Windows voice" in card and "next step up p50 0.5s" in card


def test_a_step_shown_again_is_timed_again():
    mgr = _manager({})
    mgr._clock_step(0, "shown")
    mgr._clock_step(0, "arrow", via="uia")
    first_arrow = mgr._guide_stats["clock"][0]["arrow"]
    mgr._clock_step(0, "arrow", via="vision")          # a second arrow: the first counts
    assert mgr._guide_stats["clock"][0]["arrow"] == first_arrow
    mgr._clock_step(0, "shown")                        # a repeat starts a new timeline
    assert "arrow" not in mgr._guide_stats["clock"][0]
