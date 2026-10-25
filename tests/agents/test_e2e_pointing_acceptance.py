"""The pointing acceptance suite (PT-22, the Massive Direction Plan).

The handbook's cases, by family - each held by its own test file:

    gesture   test_e2e_trace_smoothing   circles, loops, scribbles at 60/120 Hz
              test_e2e_trace_mist        the trail, the haze, the release cue
    context   test_e2e_trace_context     what a trace sends: frame, stroke, region
    DPI       test_e2e_coordinates       one conversion module, 125/100/125
              test_e2e_calibration       100/125/150%, mixed, left and above
              test_e2e_monitor_changes   unplug, move, rescale
    cancel    test_e2e_stop              Esc clears everything, older answers retired
              test_e2e_phrase_marks      a waiting mark never outlives its question
    safety    test_e2e_point_evidence    an arrow only when it is earned
              test_e2e_guide_vs_act      a walkthrough shows; acts only when asked
              test_e2e_in_control        the mouse is theirs

and here, what none of those can: the real trail sampler, driven at 60
and 120 Hz while other processes keep every core busy, must still sample
on time and read the gesture the hand made.
"""
from __future__ import annotations

import asyncio
import math
import multiprocessing
import os
import statistics
import time
from pathlib import Path

import pytest

import companion_manager as cm
from ai import trace as tracing

HERE = Path(__file__).resolve().parent
FAMILIES = ["test_e2e_trace_smoothing", "test_e2e_trace_mist", "test_e2e_trace_context",
            "test_e2e_coordinates", "test_e2e_calibration", "test_e2e_monitor_changes",
            "test_e2e_stop", "test_e2e_phrase_marks", "test_e2e_point_evidence",
            "test_e2e_guide_vs_act", "test_e2e_in_control"]


def test_every_family_has_its_cases():
    for name in FAMILIES:
        src = (HERE / f"{name}.py").read_text(encoding="utf-8")
        assert src.count("\ndef test_") + src.count("\nasync def test_") >= 2, name


def _burn(stop_at: float) -> None:
    x = 0
    while time.time() < stop_at:
        x = (x * 1103515245 + 12345) & 0xFFFFFFFF


@pytest.fixture(scope="module")
def cpu_load():
    """Every core but one busy, for as long as the module runs (a few s)."""
    n = max(1, min(6, (os.cpu_count() or 2) - 1))
    stop_at = time.time() + 20
    procs = [multiprocessing.Process(target=_burn, args=(stop_at,), daemon=True) for _ in range(n)]
    for p in procs:
        p.start()
    yield n
    for p in procs:
        p.terminate()


def _record(hz: int, seconds: float = 1.0):
    """Run the manager's own sampler over a hand drawing a circle."""
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    trace = tracing.Trace()
    mgr._trace, mgr._trace_live = trace, True
    mgr._read_ahead = lambda t: None
    mgr.TRACE_EVERY_S = 1.0 / hz
    t0 = time.monotonic()
    stamps = []

    def hand():
        now = time.monotonic() - t0
        stamps.append(now)
        a = now / seconds * math.tau * 1.1
        return 800 + 120 * math.cos(a), 450 + 120 * math.sin(a)

    async def run():
        task = asyncio.ensure_future(mgr._record_trace(trace, t0))
        await asyncio.sleep(seconds)
        mgr._trace_live = False
        await task

    original = cm.look_region.cursor_position
    cm.look_region.cursor_position = hand
    try:
        asyncio.run(run())
    finally:
        cm.look_region.cursor_position = original
    return trace, stamps


@pytest.mark.parametrize("hz", [60, 120])
def test_the_trail_keeps_time_and_shape_under_load(cpu_load, hz):
    trace, stamps = _record(hz)
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    # Windows' timer is ~15.6 ms unless raised, so 120 Hz is asked for and
    # what arrives is what the clock gives; the bar is that a busy machine
    # does not starve the sampler.
    assert statistics.median(gaps) <= max(2.5 / hz, 0.034), f"median gap {statistics.median(gaps) * 1000:.0f} ms"
    assert max(gaps) <= 0.15, f"a {max(gaps) * 1000:.0f} ms stall"
    l, t, r, b = trace.bounds()
    assert abs((r - l) - 240) < 30 and abs((b - t) - 240) < 30, "the circle's size"
    pts = trace.points
    assert tracing.kind_of(pts) == "circle"
