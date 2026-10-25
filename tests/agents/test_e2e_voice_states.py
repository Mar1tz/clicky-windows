"""Voice states, named and timed (WX-01, the Massive Direction Plan).

Idle, Listening, Thinking, Speaking and Stopped each have a name - the
pill's tooltip and, for a screen reader, its accessible name - and
"Stopped" shows for a moment after Esc. When Listening and Thinking reach
the screen is measured on the turn's line against 100 and 120 ms.
"""
from __future__ import annotations

import logging
import time

import pytest
from PyQt6.QtWidgets import QApplication

from ai import speed
from ui import flow_bar
from ui.flow_bar import FlowBar


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def test_every_state_has_a_name(qapp):
    flow = FlowBar()
    try:
        for state, words in flow_bar.STATE_LABELS.items():
            flow.set_state(state)
            assert flow.pill.accessibleName() == f"Tora: {words}"
            assert words in flow.pill.toolTip()
    finally:
        flow.close()


def test_stopped_shows_then_rests(qapp):
    flow = FlowBar()
    try:
        flow.set_state("speaking")
        flow.set_state("stopped")
        flow.set_state("idle")                         # the manager's IDLE right after
        assert flow._state == "stopped", "the stop is seen first"
        flow._stopped_until = time.monotonic() - 0.01
        flow._tick()
        assert flow._state == "idle"
    finally:
        flow.close()


def test_when_a_state_reached_the_screen_is_on_the_turn_line(qapp, caplog, monkeypatch):
    monkeypatch.setattr(speed, "_turn", None)
    caplog.set_level(logging.INFO, logger="tora.speed")
    clock = iter([0.0, 0.04, 2.0, 2.3])            # press, listening shown, release, thinking shown
    monkeypatch.setattr(speed, "_now", lambda: next(clock))
    flow = FlowBar()
    try:
        flow.on_shown = lambda name: speed.mark(f"{name} shown")
        speed.begin()
        flow.set_state("listening")
        speed.mark("release")
        flow.set_state("thinking")
        monkeypatch.setattr(speed, "_now", lambda: 3.0)
        speed.finish("answered")
    finally:
        flow.close()
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("turn timeline"))
    assert "listening shown 40 ms" in line and "thinking shown +300 ms" in line
    assert "over budget: thinking shown" in line, "300 ms is past its 120"
