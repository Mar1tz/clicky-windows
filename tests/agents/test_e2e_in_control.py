"""You're in control (PT-17, the Massive Direction Plan).

During a walkthrough the mouse is the user's. While they move it, Tora's
arrow holds still where it is - a flight pauses and goes on once the mouse
rests - and its bubble says "You're in control". Outside a walkthrough
nothing changes.
"""
from __future__ import annotations

import time

import pytest
from PyQt6.QtWidgets import QApplication

from ui import overlay as ov_mod
from ui.overlay import CursorOverlay, IN_CONTROL_CUE


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def ov(qapp):
    o = CursorOverlay()
    yield o
    o.close()


def _flying(o):
    o.guide_point(800.0, 500.0, "Save")
    assert o._flight_phase == ov_mod._PHASE_FLYING


def test_moving_the_mouse_pauses_the_arrow_and_says_so(ov):
    ov.set_guide_progress(1, 3, "Save it")
    _flying(ov)
    ov._last_tick_at = time.monotonic()
    started = ov._fly_t0
    ov._user_moved_at = time.monotonic()                # they are moving it
    time.sleep(0.05)
    ov._tick()
    assert ov._fly_t0 > started, "the flight's clock waits"
    assert ov._bubble_now() == IN_CONTROL_CUE
    ov._user_moved_at = time.monotonic() - 5           # the mouse has rested
    ov._tick()
    assert ov._bubble_now() == "Save", "and it goes on, with its own words"


def test_outside_a_walkthrough_nothing_changes(ov):
    ov.set_guide_progress(0, 0)
    ov.point_at(800.0, 500.0, "Save")
    ov._last_tick_at = time.monotonic()
    started = ov._fly_t0
    ov._user_moved_at = time.monotonic()
    time.sleep(0.02)
    ov._tick()
    assert ov._fly_t0 == started and ov._bubble_now() == "Save"
