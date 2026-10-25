"""Smoothing that keeps the loop (PT-04, the Massive Direction Plan).

The held-key path goes through a One Euro filter before its shape is read
or drawn; the raw points are kept. A slow circle, a fast loop and a
scribble - sampled like a mouse at 60 and 120 Hz, with a hand's tremble -
enclose the same region before and after, and are still read as what they
are.
"""
from __future__ import annotations

import math
import random
import statistics

import pytest

from ai import trace as tracing
from ai.trace import Trace


def _record(path, seconds: float, hz: int, tremble: float = 1.5, seed: int = 7) -> Trace:
    """`path(u)` for u in 0..1, sampled at `hz` with a tremble in px."""
    rng = random.Random(seed)
    tr = Trace()
    n = max(2, int(seconds * hz))
    for i in range(n + 1):
        u = i / n
        x, y = path(u)
        tr.add(u * seconds, x + rng.gauss(0, tremble), y + rng.gauss(0, tremble))
    return tr


def _circle(cx, cy, r, turns=1.1):
    return lambda u: (cx + r * math.cos(u * turns * math.tau), cy + r * math.sin(u * turns * math.tau))


def _scribble(u):
    return 400 + 150 * math.sin(u * 7 * math.pi), 300 + 75 * u


def _underline(u):
    return 200 + 300 * u, 500.0


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    area = lambda r: (r[2] - r[0]) * (r[3] - r[1])
    return inter / (area(a) + area(b) - inter)


CASES = [
    ("slow circle", _circle(600, 400, 120), 2.5, "circle"),
    ("fast loop", _circle(600, 400, 80), 0.35, "circle"),
    ("scribble", _scribble, 1.2, "sweep"),
    ("underline", _underline, 0.5, "underline"),
]


@pytest.mark.parametrize("hz", [60, 120])
@pytest.mark.parametrize("name,path,seconds,kind", CASES)
def test_the_region_and_the_reading_survive_smoothing(name, path, seconds, kind, hz):
    tr = _record(path, seconds, hz)
    raw = list(tr.points)
    smooth = tr.smoothed()
    assert tr.points == raw, "the raw points are kept as they came"
    # Padded as the reader pads what it reads: an underline's height is only
    # its tremble, which smoothing is meant to take away.
    pad = tracing.DWELL_PAD
    region = [(l - pad, t - pad, r + pad, b + pad)
              for l, t, r, b in (tracing._bounds(raw), tracing._bounds(smooth))]
    assert _iou(*region) >= 0.9, f"{name} at {hz} Hz: {region}"
    assert tracing.kind_of(raw) == kind
    assert len(smooth) < len(raw), "and it is thinner to draw"


def test_a_tremble_is_steadied():
    tr = _record(_underline, 0.8, 60, tremble=2.5)
    ys_raw = [p.y for p in tr.points]
    ys = [p.y for p in tracing.smooth(tr.points)]
    assert statistics.pstdev(ys[5:]) < 0.6 * statistics.pstdev(ys_raw[5:])


def test_the_stroke_still_ends_where_the_hand_stopped():
    tr = _record(_circle(300, 300, 60), 0.4, 60)
    assert tracing.smooth(tr.points)[-1] == tr.points[-1]
