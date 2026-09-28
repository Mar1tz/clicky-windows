"""Drawing the new overlay look (the redesign, OD-03 onwards).

Pure functions over a QPainter: no widget state, no timers, and no colour
of their own - every colour, size and timing comes from ui/overlay_theme.py
(a test fails if one turns up here). CursorOverlay decides WHAT is on
screen; this module decides how it looks.

The arrow's tip is its anchor: when Tora points, the point she means is the
pixel under the tip, not the middle of a shape.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QPainter, QPainterPath, QPen

from ui import overlay_theme as t

# The Windows pointer silhouette, tip at (0, 0), 20 units tall.
_ARROW = ((0.0, 0.0), (0.0, 17.0), (4.5, 13.0), (7.5, 20.0), (10.5, 18.8), (7.5, 12.0), (13.0, 12.0))


def arrow_path(height: float = t.ARROW_H) -> QPainterPath:
    s = height / 20.0
    path = QPainterPath()
    path.moveTo(0.0, 0.0)
    for x, y in _ARROW[1:]:
        path.lineTo(x * s, y * s)
    path.closeSubpath()
    return path


def arrow_box(tip: QPointF, height: float = t.ARROW_H) -> QRectF:
    """Everything the arrow paints, outline and shadow included."""
    s = height / 20.0
    pad = t.OUTLINE_W + t.CURSOR_SHADOW_BLUR
    return QRectF(tip.x() - pad, tip.y() - pad, 13.0 * s + 2 * pad, 20.0 * s + 2 * pad + 1.0)


def draw_arrow(p: QPainter, tip: QPointF, alpha: float = 1.0) -> None:
    """The cursor: Tora blue, a white outline that shows on any screen, and
    one small shadow. No halo."""
    if alpha <= 0.01:
        return
    path = arrow_path()
    p.save()
    p.translate(tip)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    # Shadow: the same shape, a pixel down and faint - small enough to read as depth, not glow.
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(t.color(t.SHADOW, t.SHADOW_A * 0.6 * alpha)))
    p.drawPath(path.translated(0.6, t.CURSOR_SHADOW_BLUR * 0.6))
    # Outline first, twice its width, so the fill leaves exactly OUTLINE_W showing outside.
    pen = QPen(t.color(t.OUTLINE, t.OUTLINE_A * alpha), t.OUTLINE_W * 2)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(QBrush(t.color(t.ACCENT, alpha)))
    p.drawPath(path)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawPath(path)
    p.restore()


def draw_listening(p: QPainter, tip: QPointF, level: float, phase: float, alpha: float = 1.0) -> None:
    """Three small bars beside the arrow that follow your voice."""
    x0, cy = tip.x() + 16.0, tip.y() + 14.0
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(t.color(t.ACCENT, alpha)))
    for i, shape in enumerate((0.6, 1.0, 0.75)):
        h = 4.0 + 9.0 * max(0.0, min(1.0, level)) * shape + 1.2 * (math.sin(phase * 1.8 + i * 0.9) + 1.0)
        p.drawRoundedRect(QRectF(x0 + i * 4.5, cy - h / 2.0, 2.5, h), 1.25, 1.25)
    p.restore()


def draw_thinking(p: QPainter, tip: QPointF, phase: float, alpha: float = 1.0) -> None:
    """A thin ring turning round the arrow."""
    centre = QPointF(tip.x() + 5.5, tip.y() + 9.0)
    r = 15.0
    pen = QPen(t.color(t.ACCENT, alpha), 2.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.save()
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    start = int((-math.degrees(phase * 2.0)) * 16) % (360 * 16)
    p.drawArc(QRectF(centre.x() - r, centre.y() - r, 2 * r, 2 * r), start, 250 * 16)
    p.restore()


def draw_target_ring(p: QPainter, centre: QPointF, progress: float) -> None:
    """Round the target: it draws in over RING_IN_S (a slight settle from
    120% to 100%) and then stays still."""
    u = max(0.0, min(1.0, progress))
    ease = 1.0 - (1.0 - u) ** 3
    r = t.RING_R * (1.2 - 0.2 * ease)
    p.save()
    p.setPen(QPen(t.color(t.ACCENT, ease), t.RING_W))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(centre, r, r)
    p.restore()


def ease_out(u: float) -> float:
    """The new look's one motion curve: quick away, gentle arrival, no overshoot."""
    u = max(0.0, min(1.0, u))
    return 1.0 - (1.0 - u) ** 3


def explain_move_s(distance: float) -> float:
    """How long a move takes while Tora explains: 0.9 s near, 1.4 s across a screen."""
    return max(t.EXPLAIN_MOVE_MIN_S, min(t.EXPLAIN_MOVE_MAX_S, t.EXPLAIN_MOVE_MIN_S + distance / 2400.0))
