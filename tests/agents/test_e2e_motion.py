"""Entrances and exits (PT-19, the Massive Direction Plan).

A pointing mark is revealed in Windows' normal 250 ms, the next one 83 ms
later; every drawing leaves in a fast 167 ms fade; and one thing moves at
a time - a mark waits for a flying arrow to land. A lesson's strokes keep
the hand's pace.
"""
from __future__ import annotations

import time

import pytest
from PyQt6.QtWidgets import QApplication

from ui import overlay as ov_mod
from ui.overlay import CursorOverlay


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def ov(qapp):
    o = CursorOverlay()
    yield o
    o.close()


def test_a_mark_comes_in_on_the_motion_tokens(ov):
    ov.add_shape({"kind": "rect", "mark": True, "x1": 10, "y1": 10, "x2": 90, "y2": 40})
    ov.add_shape({"kind": "circle", "mark": True, "x": 50, "y": 50, "r": 30})
    a, b = ov._annotations
    assert a["dur"] == ov_mod.MOTION_NORMAL_S
    assert b["start"] == pytest.approx(a["start"] + ov_mod.MOTION_NORMAL_S + ov_mod.MOTION_QUICK_S)


def test_a_lesson_keeps_the_hands_pace(ov):
    ov.add_shape({"kind": "line", "pts": [(0, 0), (840, 0)]})
    assert ov._annotations[0]["dur"] == pytest.approx(2.0)


def test_one_thing_moves_at_a_time(ov):
    ov.point_at(900.0, 500.0, "Save")                  # the arrow is flying
    lands = ov._fly_t0 + ov._fly_duration
    ov.add_shape({"kind": "rect", "mark": True, "x1": 880, "y1": 490, "x2": 920, "y2": 510})
    assert ov._annotations[-1]["start"] >= lands - 1e-6, "the ring waits for the arrow"


def test_drawings_leave_in_a_fast_fade(ov):
    ttl = ov_mod.ANNOTATION_TTL_S
    ov._annotations = [{"kind": "rect", "x1": 0, "y1": 0, "x2": 10, "y2": 10, "dur": 0.25,
                        "start": time.monotonic() - (ttl - 1.0), "ttl": ttl, "color": "tora"}]
    assert not ov._annotations_moving(), "a second before the end it is still at rest"
    ov._annotations[0]["start"] = time.monotonic() - (ttl - 0.1)
    assert ov._annotations_moving(), "and in its last 167 ms it is fading"
