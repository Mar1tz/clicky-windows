"""How Tora's buddy cursor looks: pure painting, no state of its own.

The overlay (ui/overlay.py) decides where the buddy is and what it is doing;
this draws it. The user picked this look on 30 Sep 2026 from four shown
side by side on their own screen (today's flat triangle, this, an orb and a
dart): the triangle they know, done properly -

  * rounded corners and a light-to-deep blue gradient, not a flat fill
  * a rim of light along its top edges
  * a soft shadow under it, and a quiet glow that breathes - instead of
    three stacked blue discs that read as a blurry blob
  * four rounded voice bars, a turning ring with a comet's tail for
    thinking, and a blue pill with a soft shadow for the name of the thing
    it points at
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (QBrush, QColor, QConicalGradient, QFont, QFontMetricsF, QLinearGradient,
                         QPainter, QPainterPath, QPainterPathStroker, QPen, QRadialGradient)

# Tora blue, light to deep; the flat classic blue was #3380FF.
BLUE_LIGHT = QColor(0x86, 0xC0, 0xFF)
BLUE = QColor(0x3D, 0x8B, 0xFF)
BLUE_DEEP = QColor(0x24, 0x5C, 0xE8)
_DEFAULT = (QColor(BLUE_LIGHT), QColor(BLUE), QColor(BLUE_DEEP))


def set_colour(hex_colour: str) -> None:
    """Tora wears another colour (Settings / Make Tora yours). The light
    and deep ends of her gradient are worked out from it; the cursor blue
    keeps the exact values above."""
    global BLUE_LIGHT, BLUE, BLUE_DEEP
    c = QColor(hex_colour)
    if not c.isValid() or c.name().lower() == "#3380ff":
        BLUE_LIGHT, BLUE, BLUE_DEEP = (QColor(x) for x in _DEFAULT)
        return
    BLUE = c
    BLUE_LIGHT = c.lighter(145)
    BLUE_DEEP = c.darker(125)
INK = QColor(0x0B, 0x16, 0x30)                 # shadows: a blue-black, not grey

SIDE = 19.0                                    # the triangle's side
CORNER = 3.2                                   # its rounded corners


def _lighten(color: QColor, white: float) -> QColor:
    """`color` mixed with `white` (0-1) of white."""
    return QColor(round(color.red() + (255 - color.red()) * white),
                  round(color.green() + (255 - color.green()) * white),
                  round(color.blue() + (255 - color.blue()) * white))


def _c(color: QColor, alpha: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def _rounded_triangle(side: float, radius: float) -> QPainterPath:
    """Equilateral, pointing up, centred on its centroid."""
    h = side * math.sqrt(3) / 2
    pts = [QPointF(0, -h * 2 / 3), QPointF(side / 2, h / 3), QPointF(-side / 2, h / 3)]
    path = QPainterPath()
    for i, v in enumerate(pts):
        prev, nxt = pts[i - 1], pts[(i + 1) % 3]
        a = v + (prev - v) * (radius / side)
        b = v + (nxt - v) * (radius / side)
        if i == 0:
            path.moveTo(a)
        else:
            path.lineTo(a)
        path.quadTo(v, b)
    path.closeSubpath()
    return path


_TRIANGLE = _rounded_triangle(SIDE, CORNER)


def _soft_shadow(p: QPainter, path: QPainterPath, alpha: float, dy: float = 2.2) -> None:
    """A soft shadow under a shape: the shape, then wider and fainter
    outlines of it - no image blur needed."""
    p.save()
    p.translate(0.0, dy)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_c(INK, 0.30 * alpha)))
    p.drawPath(path)
    stroker = QPainterPathStroker()
    stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    for width, a in ((2.5, 0.16), (5.0, 0.09), (8.0, 0.045)):
        stroker.setWidth(width)
        p.setBrush(QBrush(_c(INK, a * alpha)))
        p.drawPath(stroker.createStroke(path))
    p.restore()


def _glow(p: QPainter, radius: float, alpha: float) -> None:
    g = QRadialGradient(QPointF(0, 0), radius)
    g.setColorAt(0.0, _c(BLUE, 0.34 * alpha))
    g.setColorAt(0.55, _c(BLUE, 0.12 * alpha))
    g.setColorAt(1.0, _c(BLUE, 0.0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(g))
    p.drawEllipse(QPointF(0, 0), radius, radius)


def draw_pointer(p: QPainter, cx: float, cy: float, *, rotation: float, scale: float = 1.0,
                 breath: float = 0.0, alpha: float = 1.0) -> None:
    """The buddy at rest, speaking or pointing. `rotation` in degrees (-35
    leans it like a cursor; in flight it follows its path), `breath` 0..1
    for the quiet glow."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.translate(cx, cy)
    _glow(p, 17.0 + 2.5 * breath, (0.75 + 0.25 * breath) * alpha)
    p.rotate(rotation)
    p.scale(scale, scale)
    _soft_shadow(p, _TRIANGLE, alpha)
    g = QLinearGradient(QPointF(0, -12), QPointF(0, 6))
    g.setColorAt(0.0, _c(BLUE_LIGHT, alpha))
    g.setColorAt(0.55, _c(BLUE, alpha))
    g.setColorAt(1.0, _c(BLUE_DEEP, alpha))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(g))
    p.drawPath(_TRIANGLE)
    # A rim of light along the top edges, fading down: an edge, not a border.
    rim = QLinearGradient(QPointF(0, -12), QPointF(0, 6))
    rim.setColorAt(0.0, _c(QColor("white"), 0.75 * alpha))
    rim.setColorAt(0.6, _c(QColor("white"), 0.10 * alpha))
    rim.setColorAt(1.0, _c(QColor("white"), 0.0))
    p.setPen(QPen(QBrush(rim), 1.1))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(_TRIANGLE)
    p.restore()


def draw_listening(p: QPainter, cx: float, cy: float, level: float, phase: float,
                   alpha: float = 1.0) -> None:
    """Four rounded bars that follow your voice, over a soft glow."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.translate(cx, cy)
    lively = max(0.0, min(1.0, level))
    _glow(p, 18.0 + 6.0 * lively, alpha)
    bar_w, gap = 3.0, 2.6
    total = 4 * bar_w + 3 * gap
    p.setPen(Qt.PenStyle.NoPen)
    for i, shape in enumerate((0.55, 1.0, 0.8, 0.6)):
        wobble = 0.5 + 0.5 * math.sin(phase * 1.9 + i * 1.1)
        h = 4.0 + 12.0 * lively * shape + 2.2 * wobble
        x = -total / 2 + i * (bar_w + gap)
        g = QLinearGradient(QPointF(0, -h / 2), QPointF(0, h / 2))
        g.setColorAt(0.0, _c(BLUE_LIGHT, alpha))
        g.setColorAt(1.0, _c(BLUE_DEEP, alpha))
        p.setBrush(QBrush(g))
        p.drawRoundedRect(QRectF(x, -h / 2, bar_w, h), bar_w / 2, bar_w / 2)
    p.restore()


def draw_thinking(p: QPainter, cx: float, cy: float, phase: float, alpha: float = 1.0) -> None:
    """A ring with a comet's tail, turning, round a small dot."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.translate(cx, cy)
    _glow(p, 17.0, 0.8 * alpha)
    g = QConicalGradient(QPointF(0, 0), -math.degrees(phase * 2.2) % 360)
    g.setColorAt(0.0, _c(BLUE_LIGHT, alpha))
    g.setColorAt(0.08, _c(BLUE, 0.95 * alpha))
    g.setColorAt(0.75, _c(BLUE, 0.0))
    g.setColorAt(1.0, _c(BLUE_LIGHT, alpha))
    pen = QPen(QBrush(g), 2.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QPointF(0, 0), 8.5, 8.5)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_c(BLUE, alpha)))
    p.drawEllipse(QPointF(0, 0), 2.4, 2.4)
    p.restore()


_BUBBLE_FONT = None


def bubble_rect(cx: float, cy: float, label: str) -> QRectF:
    """Where the name bubble sits beside the buddy at (cx, cy)."""
    global _BUBBLE_FONT
    if _BUBBLE_FONT is None:
        _BUBBLE_FONT = QFont("Segoe UI", 9, QFont.Weight.DemiBold)
    fm = QFontMetricsF(_BUBBLE_FONT)
    w = fm.horizontalAdvance(label) + 20.0
    h = fm.height() + 10.0
    return QRectF(cx + 14.0, cy + 14.0, w, h)


def draw_bubble(p: QPainter, cx: float, cy: float, label: str, *, alpha: float = 1.0,
                scale: float = 1.0) -> None:
    """The name of what Tora points at: a pill in her colour beside the buddy, with a
    soft shadow and white semibold text. `scale` pops it in from its left."""
    if alpha <= 0.01 or not label:
        return
    r = bubble_rect(cx, cy, label)
    x, y, h = r.x(), r.y(), r.height()
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    p.translate(x, y + h / 2)
    p.scale(max(0.01, scale), max(0.01, scale))
    p.translate(-x, -(y + h / 2))
    pill = QPainterPath()
    pill.addRoundedRect(r, h / 2, h / 2)
    _soft_shadow(p, pill, alpha, dy=2.0)
    g = QLinearGradient(QPointF(0, y), QPointF(0, y + h))
    top = QColor("#5A9EFF") if BLUE.name().lower() == _DEFAULT[1].name().lower() else _lighten(BLUE, 0.15)
    g.setColorAt(0.0, _c(top, alpha))                # the blue's own top; any other colour, a touch paler
    g.setColorAt(1.0, _c(BLUE_DEEP, alpha))
    p.setPen(QPen(_c(QColor("white"), 0.28 * alpha), 0.9))
    p.setBrush(QBrush(g))
    p.drawPath(pill)
    p.setFont(_BUBBLE_FONT)
    p.setPen(_c(QColor("white"), alpha))
    p.drawText(r, Qt.AlignmentFlag.AlignCenter, label)
    p.restore()
