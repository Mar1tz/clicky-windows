"""
The ghost trail: a soft mist behind the cursor while the shortcut is held.

Asked for on 10 Oct 2026: "when I'm holding down the shortcut it can use my
cursor to move around and it leaves a little ghost trail behind it of like
mist" (docs/TRACE_TO_FOCUS.md, TF-02). Decided with the owner: in Tora's
own colour; only while the shortcut is held; and when it is let go the
trail settles into a faint haze over what was traced, until she answers -
so they can see what she will look at.

A puff is left every few pixels along the cursor's path, filled in between
frames so a fast sweep leaves a ribbon and never dots. Each one swells a
little, drifts upward like mist, and fades out over about a second. They
are drawn from one soft blob rendered once per colour, scaled, so the
overlay's 60 frames a second stay cheap; at most MAX_PUFFS live at once.

PT-07 (the Massive Direction Plan): on release a small "Pointing context
attached" sits under the haze for a moment - what was shown went with the
question - and the haze stays until she starts to answer, which is when
the answer has used it (an answer that takes a look can take 10-20 s; the
haze used to go after 8).

PT-03: a setting (Settings > General, "Trail as a line") draws the path
instead as one smooth 6 DIP rounded line - through the One Euro filter the
trace is read with (ai/trace.py) - for people who find mist too soft to
follow. The haze, the cue and their timing are the same; mist stays the
default.

Painting only and state - the overlay (ui/overlay.py) ticks it, asks where
it is to repaint just that, and paints it under everything else.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient

from ui import buddy

SPACING_PX = 7.0           # a puff every this far along the path
MAX_PER_FRAME = 24         # a fast flick: wider spacing, never a flood
MAX_PUFFS = 160
LIFE_S = 1.25              # a moving puff, from first light to gone
SIZE_PX = 30.0             # its diameter when it appears
GROW = 0.45                # and how much it swells over its life
RISE_PX_S = 9.0            # mist drifts upward
HAZE_EVERY_PX = 16.0       # the haze: one resting puff this far apart
HAZE_ALPHA = 0.40
HAZE_MAX_S = 30.0          # an answer that never comes does not leave it up
CUE = "Pointing context attached"
CUE_S = 2.5                # the release cue, then the haze alone
CUE_GAP_PX = 12.0
LINE_DIP = 6.0             # PT-03: the line's width
LINE_ALPHA = 0.9           # while it is drawn
LINE_KEPT_ALPHA = 0.55     # resting, as the haze
HAZE_FADE_S = 0.6
_BLOB_PX = 96              # the cached blob's size


@dataclass
class _Puff:
    x: float
    y: float
    born: float
    size: float
    vx: float
    vy: float


_blobs: dict[str, QPixmap] = {}


def _blob() -> QPixmap:
    """The soft puff in Tora's colour, rendered once: a whiter core, her
    colour, then nothing - mist, not paint."""
    colour = QColor(buddy.BLUE)
    key = colour.name()
    if key not in _blobs:
        pm = QPixmap(_BLOB_PX, _BLOB_PX)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = _BLOB_PX / 2
        g = QRadialGradient(QPointF(r, r), r)
        core = QColor(buddy.BLUE_LIGHT).lighter(115)
        core.setAlphaF(0.66)
        mid = QColor(colour)
        mid.setAlphaF(0.32)
        edge = QColor(colour)
        edge.setAlphaF(0.0)
        g.setColorAt(0.0, core)
        g.setColorAt(0.35, mid)
        g.setColorAt(1.0, edge)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawEllipse(QRectF(0, 0, _BLOB_PX, _BLOB_PX))
        p.end()
        _blobs[key] = pm
    return _blobs[key]


class Mist:
    """The trail and its haze. Desktop coordinates, as the overlay paints."""

    def __init__(self, seed: Optional[int] = None) -> None:
        self.puffs: list[_Puff] = []
        self.tracing = False
        self._last: Optional[tuple[float, float]] = None
        self._path: list[tuple[float, float]] = []      # where the cursor went, for the haze
        self.haze: list[tuple[float, float, float]] = []  # x, y, phase
        self._haze_from = 0.0
        self._haze_fade_at: Optional[float] = None
        self._rand = random.Random(seed)
        self.style = "mist"                              # or "line" (PT-03)
        self.line: list[tuple[float, float]] = []        # the smoothed path, for "line"
        self._line_drop_at: Optional[float] = None
        self._fx = self._fy = None

    # -- driven by the overlay ---------------------------------------------------------------------------------

    def start(self, now: float, style: str = "mist") -> None:
        """The shortcut went down: the trail begins at the cursor's next move."""
        self.tracing = True
        self._last = None
        self._path = []
        self.haze = []
        self._haze_fade_at = None
        self.style = "line" if style == "line" else "mist"
        self.line = []
        self._line_drop_at = None
        from ai.trace import OneEuro
        self._fx, self._fy = OneEuro(), OneEuro()

    def stop(self, now: float, keep: bool) -> None:
        """The shortcut came up. `keep`: the traced area stays as a faint
        haze until she answers (end_haze); otherwise the trail just fades."""
        self.tracing = False
        self._last = None
        if keep and self._path:
            self.haze = [(x, y, self._rand.uniform(0, math.tau)) for x, y in _every(self._path, HAZE_EVERY_PX)]
            self._haze_from = now
            self._haze_fade_at = None
        elif self.line:
            self._line_drop_at = now                     # a tap's line just fades
        self._path = []

    def end_haze(self, now: float) -> None:
        """She is answering: the haze has done its job."""
        if self.haze and self._haze_fade_at is None:
            self._haze_fade_at = now

    def tick(self, x: float, y: float, now: float) -> None:
        """A frame: the cursor is at (x, y)."""
        if self.tracing:
            self._trail_to(x, y, now)
        self.puffs = [p for p in self.puffs if now - p.born < LIFE_S]
        if self.line and not self.tracing and not self.haze and (
                self._line_drop_at is None or now - self._line_drop_at > HAZE_FADE_S):
            self.line = []
        if self.haze:
            if self._haze_fade_at is None and now - self._haze_from > HAZE_MAX_S:
                self._haze_fade_at = now
            if self._haze_fade_at is not None and now - self._haze_fade_at > HAZE_FADE_S:
                self.haze = []
                self.line = []

    def _trail_to(self, x: float, y: float, now: float) -> None:
        if self._last is None:
            self._last = (x, y)
            return
        lx, ly = self._last
        dist = math.hypot(x - lx, y - ly)
        if dist < SPACING_PX:
            return
        n = min(MAX_PER_FRAME, int(dist / SPACING_PX))
        if self.style == "line":
            self._path.append((x, y))
            if self._fx is not None:
                self.line.append((self._fx(now, x), self._fy(now, y)))
            self._last = (x, y)
            return
        for i in range(1, n + 1):
            f = i / n
            px, py = lx + (x - lx) * f, ly + (y - ly) * f
            # Born a touch apart along the move, so a ribbon fades from its tail.
            self.puffs.append(_Puff(px + self._rand.uniform(-2, 2), py + self._rand.uniform(-2, 2),
                                    now - (1 - f) * 0.016, SIZE_PX * self._rand.uniform(0.8, 1.2),
                                    self._rand.uniform(-6, 6), -RISE_PX_S * self._rand.uniform(0.6, 1.4)))
            self._path.append((px, py))
        if len(self.puffs) > MAX_PUFFS:
            del self.puffs[:len(self.puffs) - MAX_PUFFS]
        self._last = (x, y)

    # -- what the overlay asks -----------------------------------------------------------------------------------

    def active(self) -> bool:
        return bool(self.puffs or self.haze or self.tracing or self.line)

    def bounds(self) -> Optional[QRectF]:
        """Everywhere it is drawn now, with room for a puff's full size."""
        pts = [(p.x + p.vx * LIFE_S, p.y + p.vy * LIFE_S) for p in self.puffs] + \
              [(p.x, p.y) for p in self.puffs] + [(x, y) for x, y, _ in self.haze] + list(self.line)
        if not pts:
            return None
        pad = SIZE_PX * (1 + GROW) * 1.3
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        box = QRectF(min(xs) - pad, min(ys) - pad, max(xs) - min(xs) + 2 * pad, max(ys) - min(ys) + 2 * pad)
        cue = self.cue_rect()
        return box.united(cue.adjusted(-4, -4, 4, 4)) if cue is not None else box

    def cue_showing(self, now: float) -> bool:
        return bool(self.haze) and self._haze_fade_at is None and now - self._haze_from < CUE_S

    def cue_rect(self) -> Optional[QRectF]:
        """Where the release cue goes: centred under the haze."""
        if not self.haze:
            return None
        xs, ys = [x for x, _, _ in self.haze], [y for _, y, _ in self.haze]
        fm = QFontMetricsF(_cue_font())
        w, h = fm.horizontalAdvance(CUE) + 20, fm.height() + 8
        cx, bottom = (min(xs) + max(xs)) / 2, max(ys) + SIZE_PX * 0.8 + CUE_GAP_PX
        return QRectF(cx - w / 2, bottom, w, h)

    def draw(self, p: QPainter, now: float) -> None:
        if not (self.puffs or self.haze or self.line):
            return
        blob = _blob()
        p.save()
        try:
            if self.line:
                self._draw_line(p, now)
            if self.haze and self.style == "line":
                if self.cue_showing(now):
                    self._draw_cue(p, now)
            elif self.haze:
                fade = 1.0
                if self._haze_fade_at is not None:
                    fade = max(0.0, 1.0 - (now - self._haze_fade_at) / HAZE_FADE_S)
                settle = min(1.0, (now - self._haze_from) / 0.35)     # the trail eases into it
                for x, y, phase in self.haze:
                    breath = 0.85 + 0.15 * math.sin(now * 2.2 + phase)
                    a = HAZE_ALPHA * fade * breath * (0.6 + 0.4 * settle)
                    size = SIZE_PX * 1.25 * breath
                    p.setOpacity(a)
                    p.drawPixmap(QRectF(x - size / 2, y - size / 2, size, size), blob,
                                 QRectF(0, 0, _BLOB_PX, _BLOB_PX))
                if self.cue_showing(now):
                    self._draw_cue(p, now)
            for puff in self.puffs:
                age = (now - puff.born) / LIFE_S
                if age < 0 or age >= 1:
                    continue
                alpha = min(1.0, age / 0.08) * (1 - age) ** 1.6
                size = puff.size * (1 + GROW * age)
                x, y = puff.x + puff.vx * age * LIFE_S, puff.y + puff.vy * age * LIFE_S
                p.setOpacity(alpha * 0.9)
                p.drawPixmap(QRectF(x - size / 2, y - size / 2, size, size), blob,
                             QRectF(0, 0, _BLOB_PX, _BLOB_PX))
        finally:
            p.restore()

    def _draw_line(self, p: QPainter, now: float) -> None:
        """PT-03: the path as one rounded line, smoothed."""
        if len(self.line) < 2:
            return
        if self.tracing:
            a = LINE_ALPHA
        elif self.haze:
            fade = 1.0 if self._haze_fade_at is None else max(0.0, 1.0 - (now - self._haze_fade_at) / HAZE_FADE_S)
            settle = min(1.0, (now - self._haze_from) / 0.35)
            a = LINE_ALPHA + (LINE_KEPT_ALPHA - LINE_ALPHA) * settle
            a *= fade
        else:
            a = LINE_ALPHA * max(0.0, 1.0 - (now - (self._line_drop_at or now)) / HAZE_FADE_S)
        if a <= 0:
            return
        path = QPainterPath(QPointF(*self.line[0]))
        for x, y in self.line[1:]:
            path.lineTo(x, y)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setBrush(Qt.BrushStyle.NoBrush)
        glow = QColor(buddy.BLUE)
        glow.setAlphaF(0.25 * a)
        p.setOpacity(1.0)
        p.setPen(QPen(glow, LINE_DIP + 6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        p.drawPath(path)
        core = QColor(buddy.BLUE_LIGHT)
        core.setAlphaF(a)
        p.setPen(QPen(core, LINE_DIP, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        p.drawPath(path)

    def _draw_cue(self, p: QPainter, now: float) -> None:
        """PT-07: "Pointing context attached", easing in and out."""
        r = self.cue_rect()
        if r is None:
            return
        age = now - self._haze_from
        a = min(1.0, age / 0.25) * min(1.0, (CUE_S - age) / 0.4)
        if a <= 0:
            return
        p.setOpacity(0.92 * a)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(12, 12, 14, 220))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setFont(_cue_font())
        p.setPen(QColor(buddy.BLUE_LIGHT))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, CUE)


def _cue_font() -> QFont:
    f = QFont("Segoe UI")
    f.setPointSizeF(9.0)
    f.setWeight(QFont.Weight.DemiBold)
    return f


def _every(path: list[tuple[float, float]], spacing: float) -> list[tuple[float, float]]:
    """Points along `path`, `spacing` px apart."""
    if not path:
        return []
    out = [path[0]]
    carried = 0.0
    for (ax, ay), (bx, by) in zip(path, path[1:]):
        seg = math.hypot(bx - ax, by - ay)
        while carried + seg >= spacing and seg > 0:
            f = (spacing - carried) / seg
            ax, ay = ax + (bx - ax) * f, ay + (by - ay) * f
            out.append((ax, ay))
            seg = math.hypot(bx - ax, by - ay)
            carried = 0.0
        carried += seg
    return out[:400]
