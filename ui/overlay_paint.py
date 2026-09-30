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

from PyQt6.QtCore import QPointF, QRectF, QSizeF, Qt
from PyQt6.QtGui import QBrush, QFont, QFontMetricsF, QPainter, QPainterPath, QPen

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


def draw_target_bounds(p: QPainter, bounds: QRectF, progress: float) -> None:
    """Round a target whose bounds Windows reported: a rounded rectangle RING_PAD
    outside it, drawing in the same way as the round ring, then still."""
    u = max(0.0, min(1.0, progress))
    ease = 1.0 - (1.0 - u) ** 3
    grow = 4.0 * (1.0 - ease)
    r = bounds.adjusted(-t.RING_PAD - grow, -t.RING_PAD - grow, t.RING_PAD + grow, t.RING_PAD + grow)
    p.save()
    p.setPen(QPen(t.color(t.ACCENT, ease), t.RING_W))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(r, 6.0, 6.0)
    p.restore()


def ease_out(u: float) -> float:
    """The new look's one motion curve: quick away, gentle arrival, no overshoot."""
    u = max(0.0, min(1.0, u))
    return 1.0 - (1.0 - u) ** 3


def explain_move_s(distance: float) -> float:
    """How long a move takes while Tora explains: 0.9 s near, 1.4 s across a screen."""
    return max(t.EXPLAIN_MOVE_MIN_S, min(t.EXPLAIN_MOVE_MAX_S, t.EXPLAIN_MOVE_MIN_S + distance / 2400.0))


# -- the step chip (OD-04) ------------------------------------------------------------------------------

_KEY_PAD = 5.0
_PART_GAP = 5.0
_fonts: dict[str, QFont] = {}


def _font(kind: str) -> QFont:
    if kind not in _fonts:
        _fonts.update(text=t.font(t.CHIP_TEXT_PX, QFont.Weight.Medium),
                      strong=t.font(t.CHIP_TEXT_PX, QFont.Weight.DemiBold),
                      key=t.font(t.BADGE_TEXT_PX, QFont.Weight.DemiBold),
                      badge=t.font(t.BADGE_TEXT_PX, QFont.Weight.DemiBold))
    return _fonts[kind]


def _part_w(kind: str, words: str) -> float:
    w = QFontMetricsF(_font(kind)).horizontalAdvance(words)
    return w + 2 * _KEY_PAD if kind == "key" else w


def badge_width(text: str, widest: str = "") -> float:
    """A step badge's width; `widest` holds it steady while the count changes."""
    fm = QFontMetricsF(_font("badge"))
    return max(fm.horizontalAdvance(text), fm.horizontalAdvance(widest) if widest else 0.0) + 12.0


def draw_badge(p: QPainter, x: float, cy: float, text: str, alpha: float = 1.0, width: float = 0.0) -> float:
    """The step count, "2/5", in a soft accent pill; the chip and the island
    draw the same one. Returns its width."""
    bw = width or badge_width(text)
    badge = QRectF(x, cy - t.BADGE_H / 2, bw, t.BADGE_H)
    p.setBrush(QBrush(t.color(t.ACCENT, t.ACCENT_SOFT_A * alpha)))
    p.setPen(QPen(t.color(t.ACCENT, 0.55 * alpha), 1.0))
    p.drawRoundedRect(badge.adjusted(0.5, 0.5, -0.5, -0.5), t.BADGE_R, t.BADGE_R)
    p.setFont(_font("badge"))
    p.setPen(t.color(t.TEXT, alpha))
    p.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)
    return bw


def draw_island_glyph(p: QPainter, centre: QPointF, kind: str, level: float, phase: float,
                      alpha: float = 1.0) -> None:
    """The island's state at a glance (OD-07): "listen" bars that follow your
    voice, a "think" ring that turns, "speak" bars that move with her words
    (level = how lively), and a still "rest" dot while a walkthrough waits."""
    if alpha <= 0.01:
        return
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    colour = t.color(t.ACCENT, alpha)
    if kind == "think":
        pen = QPen(colour, 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        r = 5.5
        start = int((-math.degrees(phase * 2.0)) * 16) % (360 * 16)
        p.drawArc(QRectF(centre.x() - r, centre.y() - r, 2 * r, 2 * r), start, 250 * 16)
    elif kind in ("listen", "speak"):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(colour))
        lively = max(0.0, min(1.0, level))
        for i, shape in enumerate((0.6, 1.0, 0.75)):
            wobble = 1.2 * (math.sin(phase * 1.8 + i * 0.9) + 1.0) if lively > 0.02 else 0.0
            h = 4.0 + 9.0 * lively * shape + wobble * lively
            p.drawRoundedRect(QRectF(centre.x() - 6.25 + i * 4.5, centre.y() - h / 2.0, 2.5, h), 1.25, 1.25)
    else:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(colour))
        p.drawEllipse(centre, 3.5, 3.5)
    p.restore()


def chip_size(chip) -> QSizeF:
    """The chip's size for its words: padding, the badge, each part."""
    w = 2 * t.CHIP_PAD_X
    if chip.badge:
        w += badge_width(chip.badge) + 8.0
    parts = list(chip.parts)
    for i, (kind, words) in enumerate(parts):
        w += _part_w(kind, words)
        if i:
            w += _PART_GAP
    return QSizeF(round(w), t.CHIP_H)


def draw_chip(p: QPainter, rect: QRectF, chip, alpha: float = 1.0) -> None:
    """One solid chip: the step badge, then the action and the thing, keys as caps."""
    if alpha <= 0.01 or not chip:
        return
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    # one soft shadow, then the body and its 1 px border
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(t.color(t.SHADOW, t.SHADOW_A * 0.55 * alpha)))
    p.drawRoundedRect(rect.translated(0, 2.0), t.CHIP_R, t.CHIP_R)
    p.setBrush(QBrush(t.color(t.SURFACE, t.SURFACE_A * alpha)))
    p.setPen(QPen(t.color(t.BORDER, alpha), 1.0))
    p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), t.CHIP_R, t.CHIP_R)
    x, cy = rect.left() + t.CHIP_PAD_X, rect.center().y()
    if chip.badge:
        x += draw_badge(p, x, cy, chip.badge, alpha) + 8.0
    for i, (kind, words) in enumerate(chip.parts):
        if i:
            x += _PART_GAP
        w = _part_w(kind, words)
        if kind == "key":
            cap = QRectF(x, cy - 9.0, w, 18.0)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(t.color(t.TEXT_DIM, 0.8 * alpha), 1.0))
            p.drawRoundedRect(cap.adjusted(0.5, 0.5, -0.5, -0.5), t.KEYCAP_R, t.KEYCAP_R)
            p.setFont(_font("key"))
            p.setPen(t.color(t.TEXT, alpha))
            p.drawText(cap, Qt.AlignmentFlag.AlignCenter, words)
        else:
            p.setFont(_font(kind))
            p.setPen(t.color(t.TEXT if kind == "strong" else t.TEXT_DIM, alpha))
            p.drawText(QRectF(x, rect.top(), w + 2.0, rect.height()),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, words)
        x += w
    p.restore()
