"""
Show it by tracing: where the mouse went while the shortcut was held.

Asked for on 10 Oct 2026: "when I'm holding down the shortcut it can use my
cursor to move around and it leaves a little ghost trail behind it of like
mist - and then the AI must know to focus on this area." The mist is drawn
by the overlay (ui/mist.py); this is the record behind it, in the same
physical pixels Windows UI Automation and the screenshots use
(look_region.cursor_position), each point with its time since the
shortcut went down - so "this... and this" can later be matched to the
words (docs/TRACE_TO_FOCUS.md, TF-01 and on).

Kept small: a move under two pixels is the hand trembling, not a move; a
long hold is capped; straight runs are thinned when the path is read.

PT-04 (the Massive Direction Plan): the raw points are kept as they came -
they are the evidence. What is read for its shape, and drawn, is the path
through a One Euro filter (Casiez et al., CHI 2012): steady when the hand
is slow, so a tremble does not read as turns, and quick to follow when it
is fast, so a fast loop is not cut short. A slow circle, a fast loop and a
scribble enclose the same region before and after.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

MIN_STEP_PX = 2.0          # under this, the hand trembled
MAX_POINTS = 2000          # ~30 s of moving at 60 a second
MOVED_PX = 24.0            # a path shorter than this showed nothing
# One Euro, in physical px and seconds: 1 Hz when still, rising with speed
# (a 2000 px/s flick follows at ~41 Hz, so the loop keeps its size).
EURO_MIN_CUTOFF = 1.0
EURO_BETA = 0.02
EURO_D_CUTOFF = 1.0


@dataclass(frozen=True)
class Point:
    t: float               # seconds since the shortcut went down
    x: float               # physical px, desktop
    y: float


@dataclass
class Trace:
    """The path of one held shortcut."""
    points: list[Point] = field(default_factory=list)
    length: float = 0.0    # px travelled

    def add(self, t: float, x: float, y: float) -> bool:
        """One sample. False when it was not kept (a tremble, or full)."""
        if self.points:
            last = self.points[-1]
            step = math.hypot(x - last.x, y - last.y)
            if step < MIN_STEP_PX:
                return False
            if len(self.points) >= MAX_POINTS:
                return False
            self.length += step
        self.points.append(Point(t, x, y))
        return True

    @property
    def moved(self) -> bool:
        """Did the mouse show anything, or did it sit still? How far it
        reached, not how far it went: a hand jiggling 3 px back and forth
        travels a long way and shows nothing."""
        b = self.bounds()
        return b is not None and math.hypot(b[2] - b[0], b[3] - b[1]) >= MOVED_PX

    def bounds(self) -> Optional[tuple[float, float, float, float]]:
        """(left, top, right, bottom) of the path, physical px."""
        if not self.points:
            return None
        xs = [p.x for p in self.points]
        ys = [p.y for p in self.points]
        return min(xs), min(ys), max(xs), max(ys)

    def simplified(self, epsilon: float = 2.0) -> list[Point]:
        """The path with its straight runs thinned (Ramer-Douglas-Peucker):
        every bend kept, within `epsilon` px."""
        return _rdp(self.points, epsilon)

    def smoothed(self, epsilon: float = 1.5) -> list[Point]:
        """The path as it is drawn and read for its shape: One Euro, then
        thinned. The raw points stay in `points`."""
        return _rdp(smooth(self.points), epsilon)

    def summary(self) -> str:
        """For the log: how much was traced."""
        b = self.bounds()
        if not b or not self.moved:
            return "no trace"
        span = self.points[-1].t - self.points[0].t
        return (f"{len(self.points)} points, {self.length:.0f} px over a {b[2] - b[0]:.0f}x{b[3] - b[1]:.0f} "
                f"area in {span:.1f}s")


# ── TF-03: from a trail to what they meant ───────────────────────────────────
#
# A circle round a chart, a line under a sentence, a scribble over an area, a
# move to a button and a stop on it: each means a thing, and the answer
# needs the thing, not the path.

PAUSE_S = 0.35             # the mouse rested this long: one stroke ends
JUMP_PX = 260.0            # a leap between two samples: a new stroke
DWELL_PAD = 22.0


@dataclass
class Focus:
    """One thing the user showed, in the order they showed it."""
    kind: str                                  # circle | underline | sweep | point
    rect: tuple[float, float, float, float]    # left, top, right, bottom - physical px
    t0: float                                  # when it was shown, s since the shortcut went down
    t1: float
    samples: list = field(default_factory=list)  # where to read what is under it

    def describe(self) -> str:
        return {"circle": "circled", "underline": "underlined", "sweep": "swept over",
                "point": "pointed at"}.get(self.kind, "traced")


class OneEuro:
    """The One Euro filter for one coordinate: a low-pass whose cutoff
    rises with speed - little lag when fast, little jitter when slow."""

    def __init__(self, min_cutoff: float = EURO_MIN_CUTOFF, beta: float = EURO_BETA,
                 d_cutoff: float = EURO_D_CUTOFF) -> None:
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self._t: Optional[float] = None
        self._x = self._dx = 0.0

    @staticmethod
    def _alpha(dt: float, cutoff: float) -> float:
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, t: float, x: float) -> float:
        if self._t is None:
            self._t, self._x = t, x
            return x
        dt = max(t - self._t, 1e-3)
        a_d = self._alpha(dt, self.d_cutoff)
        self._dx = a_d * (x - self._x) / dt + (1 - a_d) * self._dx
        a = self._alpha(dt, self.min_cutoff + self.beta * abs(self._dx))
        self._t, self._x = t, a * x + (1 - a) * self._x
        return self._x


def smooth(points: list[Point]) -> list[Point]:
    """Each point through One Euro (x and y apart); the last point is kept
    exactly, so the stroke still ends where the hand stopped."""
    if len(points) < 3:
        return list(points)
    fx, fy = OneEuro(), OneEuro()
    out = [Point(p.t, fx(p.t, p.x), fy(p.t, p.y)) for p in points]
    out[-1] = points[-1]
    return out


def _turning(pts: list[Point]) -> float:
    """How far the path turns, all told, in radians - one way positive, the
    other negative - over its bends (thinned, so a tremble is no turn)."""
    thin = _rdp(pts, 3.0)
    total = 0.0
    for a, b, c in zip(thin, thin[1:], thin[2:]):
        d = math.atan2(c.y - b.y, c.x - b.x) - math.atan2(b.y - a.y, b.x - a.x)
        while d > math.pi:
            d -= math.tau
        while d < -math.pi:
            d += math.tau
        total += d
    return total


def _length(pts: list[Point]) -> float:
    return sum(math.hypot(b.x - a.x, b.y - a.y) for a, b in zip(pts, pts[1:]))


def _bounds(pts: list[Point]) -> tuple[float, float, float, float]:
    xs, ys = [p.x for p in pts], [p.y for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def strokes(trace: Trace) -> list[list[Point]]:
    """The path cut where the mouse rested or leapt."""
    out: list[list[Point]] = []
    for p in trace.points:
        if out:
            last = out[-1][-1]
            if p.t - last.t >= PAUSE_S or math.hypot(p.x - last.x, p.y - last.y) >= JUMP_PX:
                out.append([p])
                continue
            out[-1].append(p)
        else:
            out.append([p])
    return out


def kind_of(pts: list[Point]) -> str:
    """What a stroke's shape says - read from the smoothed path, so a
    tremble is no turn; where it is stays the raw path's (focuses)."""
    pts = smooth(pts)
    l, t, r, b = _bounds(pts)
    w, h = r - l, b - t
    reach = math.hypot(w, h)
    if reach < MOVED_PX:
        return "point"
    length = _length(pts)
    # Round something: the path keeps turning one way, most of the way round.
    # People seldom close a circle neatly; a zigzag's turns cancel out.
    if abs(_turning(pts)) >= 1.2 * math.pi and min(w, h) >= 0.25 * max(w, h):
        return "circle"
    turns = sum(1 for a, b2, c in zip(pts, pts[1:], pts[2:]) if (b2.x - a.x) * (c.x - b2.x) < 0
                and abs(c.x - b2.x) + abs(b2.x - a.x) > 6)
    if turns >= 3 or length >= 3.0 * reach:
        return "sweep"
    if h <= 0.25 * w and w >= 40:
        return "underline"
    return "line"                               # travel: what matters is where it stopped


def focuses(trace: Trace) -> list[Focus]:
    """What they showed, in order: each stroke as the thing it means. A move
    that just travels to something means the place it stopped."""
    out: list[Focus] = []
    for pts in strokes(trace):
        kind = kind_of(pts)
        l, t, r, b = _bounds(pts)
        if kind in ("line", "point"):
            if kind == "point" and len(pts) < 4:
                continue                        # where the mouse sat before they began: not a gesture
            x, y = (pts[-1].x, pts[-1].y) if kind == "line" else ((l + r) / 2, (t + b) / 2)
            if out and out[-1].kind == "point" and \
                    math.hypot(x - out[-1].samples[0][0], y - out[-1].samples[0][1]) < DWELL_PAD:
                continue                        # the same spot again
            out.append(Focus("point", (x - DWELL_PAD, y - DWELL_PAD, x + DWELL_PAD, y + DWELL_PAD),
                             pts[0].t, pts[-1].t, [(x, y)]))
        elif kind == "underline":
            # The words sit just above the line drawn under them.
            xs = [l + (r - l) * i / 7 for i in range(8)]
            ys = [p.y for p in pts]
            line_y = sorted(ys)[len(ys) // 2]
            out.append(Focus("underline", (l, line_y - 30, r, line_y + 6), pts[0].t, pts[-1].t,
                             [(x, line_y - 10) for x in xs]))
        else:
            grid = [(l + (r - l) * fx, t + (b - t) * fy) for fy in (0.25, 0.5, 0.75) for fx in (0.25, 0.5, 0.75)]
            out.append(Focus(kind, (l, t, r, b), pts[0].t, pts[-1].t, grid))
    return out


def _rdp(points: list[Point], epsilon: float) -> list[Point]:
    if len(points) < 3:
        return list(points)
    a, b = points[0], points[-1]
    dx, dy = b.x - a.x, b.y - a.y
    norm = math.hypot(dx, dy)
    worst, at = -1.0, 0
    for i in range(1, len(points) - 1):
        p = points[i]
        if norm == 0:
            d = math.hypot(p.x - a.x, p.y - a.y)
        else:
            d = abs(dy * p.x - dx * p.y + b.x * a.y - b.y * a.x) / norm
        if d > worst:
            worst, at = d, i
    if worst <= epsilon:
        return [a, b]
    return _rdp(points[:at + 1], epsilon)[:-1] + _rdp(points[at:], epsilon)
