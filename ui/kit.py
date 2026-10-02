"""The drawing primitives every hand-painted surface shares.

Onboarding, the tour scenes, the tray popup and the app's own widgets each
grew a copy of the same few things - a tween, a colour mix, a font helper, a
soft shadow - with their own numbers. One copy lives here, and the numbers
come from ui/tokens.py (SHADOW, the radii), so a change is made once.

Values only come from tokens; this module is how they are applied.
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, QVariantAnimation
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import QGraphicsDropShadowEffect, QWidget

from ui import tokens


def font(px: float, weight: int = 400, spacing: float = 0.0, *, italic: bool = False,
         family: str = "") -> QFont:
    """The brand face at a pixel size, unhinted (see tokens.app_font)."""
    f = QFont(family or tokens.font_family())
    f.setPixelSize(max(1, int(round(px))))
    f.setWeight(QFont.Weight(weight))
    f.setItalic(italic)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return f


def mix(a: str, b: str, t: float, alpha: int = 255) -> QColor:
    """The colour `t` of the way from a to b."""
    ca, cb = QColor(a), QColor(b)
    return QColor(int(ca.red() + (cb.red() - ca.red()) * t),
                  int(ca.green() + (cb.green() - ca.green()) * t),
                  int(ca.blue() + (cb.blue() - ca.blue()) * t), alpha)


def rgba(colour: str, alpha: float) -> str:
    """A token colour at an opacity, for a stylesheet: rgba(r, g, b, a)."""
    c = QColor(colour)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})"


def with_alpha(colour: str, alpha: float) -> QColor:
    c = QColor(colour)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def tween(owner: QWidget, attr: str, to: float, ms: int = 0) -> None:
    """Move `owner.<attr>` to `to` on the site's curve, repainting as it goes.
    Every animated piece is a float from 0 to 1 and a paintEvent."""
    anims = owner.__dict__.setdefault("_tweens", {})
    old = anims.get(attr)
    if old is not None:
        old.stop()
    start = float(getattr(owner, attr))
    if abs(start - to) < 1e-3 or not owner.isVisible():
        setattr(owner, attr, float(to))
        owner.update()
        return
    a = QVariantAnimation(owner)
    a.setStartValue(start)
    a.setEndValue(float(to))
    a.setDuration(ms or tokens.DURATION)
    a.setEasingCurve(tokens.ease())

    def step(v, _owner=owner, _attr=attr):
        setattr(_owner, _attr, float(v))
        _owner.update()
    a.valueChanged.connect(step)
    a.start()
    anims[attr] = a


def soft_shadow(p: QPainter, r: QRectF, radius: float, level: str = "card",
                strength: float = 1.0) -> None:
    """A painted shadow under a painted card: many faint layers, so it has
    no edge of its own (two or three strong ones read as a second card).
    `level` is a key of tokens.SHADOW; keep the card inset by about the
    blur so the shadow is not cut off at the widget's edge."""
    blur, dy, alpha = tokens.SHADOW[level]
    layers = 10
    step = blur / (layers * 1.6)
    per = max(1, int(alpha * strength / layers * 1.4))
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    for i in range(layers):
        grow = 1.0 + i * step
        p.setBrush(QColor(0, 0, 0, per))
        p.drawRoundedRect(r.adjusted(-grow * 0.6, grow * 0.3 + dy * 0.5, grow * 0.6, grow + dy * 0.5),
                          radius + grow * 0.6, radius + grow * 0.6)
    p.restore()


def drop_shadow(widget: QWidget, level: str = "float") -> QGraphicsDropShadowEffect:
    """The same elevation for a laid-out widget (a QFrame card)."""
    blur, dy, alpha = tokens.SHADOW[level]
    try:
        from ui import app_theme as T
        if T.is_dark():
            alpha = min(255, int(alpha * 2.6))
    except Exception:  # noqa: BLE001
        pass
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, dy)
    effect.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(effect)
    return effect
