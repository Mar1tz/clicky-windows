"""The pieces the first launch is built from (docs/ui/DESIGN.md §7).

The onboarding has exactly two layouts, after Wispr Flow's:

* a QUESTION screen: a white left column (title, one line, choices, a
  Continue pinned to the bottom) beside a gradient picture panel that
  reacts to the answer;
* a TEST screen: centred title and one line over the logo's swoosh, and a
  single floating card holding the live check (mic bars, the keycap, the
  spreadsheet Tora points into). The ground is HeyTora's ribbon.

Everything a screen needs is here; ui/setup_wizard.py only arranges them
and decides what each answer means. Colours and gradients come from
app_theme / tokens - the gradient families are the website's own.
"""

from __future__ import annotations

from typing import Optional, Sequence

from PyQt6.QtCore import (
    QByteArray, QPointF, QRectF, QSize, Qt, QVariantAnimation, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient,
)
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

from ui import app_theme as T
from ui import tokens

# The website's gradient families, the paper greys and the stage live in
# ui/tokens.py; they are re-exported here because the wizard reads O.STEP etc.
from ui.tokens import CAPABILITY, PAPER as P, STAGE, STEP, VOICE_COLOURS, WORK_TILES  # noqa: E402,F401
from ui.kit import drop_shadow, mix, soft_shadow, tween  # noqa: E402,F401
from ui import kit  # noqa: E402

INK_ON_PALE = P.ink


def _font(px: float, weight: int = 400, spacing: float = 0.0) -> QFont:
    return kit.font(px, weight, spacing)


def text(words: str, px: int = 14, weight: int = 400, colour: str = "",
         *, wrap: bool = True, centre: bool = False, spacing: float = 0.3) -> QLabel:
    lab = QLabel(words)
    lab.setFont(_font(px, weight, spacing))
    lab.setWordWrap(wrap)
    # In the sheet, not only the QFont: app_theme's `QWidget { font-size: 13px }`
    # beats setFont(), and every heading came out at body size.
    lab.setStyleSheet(f"color: {colour or T.TEXT}; background: transparent;"
                      f" font-size: {px}px; font-weight: {weight}; letter-spacing: {spacing}px;")
    if centre:
        lab.setAlignment(Qt.AlignmentFlag.AlignHCenter)
    if px >= 24 and not wrap:
        # Fixed, not minimum: a layout sizes an aligned label from its size
        # hint, worked out before the stylesheet's font-size applies - the
        # payoff's 44 px number was given 13 px text's height and sliced.
        lab.setFixedHeight(int(px * 1.32))
    return lab


def heading(words: str, *, centre: bool = False) -> QLabel:
    return text(words, 30, 700, centre=centre, spacing=-0.5)


def line(words: str, *, centre: bool = False) -> QLabel:
    return text(words, 15, 400, T.TEXT_DIM, centre=centre)


# ── the phase bar ─────────────────────────────────────────────────────────────

class PhaseBar(QWidget):
    """SIGN UP › PERMISSIONS › SET UP › LEARN › PERSONALIZE, with a line that
    grows under it. Wispr Flow's best piece of furniture: you always know
    where you are and how much is left, without "step 7 of 13"."""

    PHASES = ("Sign up", "Permissions", "Set up", "Learn", "Personalize")

    def __init__(self):
        super().__init__()
        self.setFixedHeight(54)
        self._phase = 0
        self._fraction = 0.0
        self._shown = 0.0
        self._anim: Optional[QVariantAnimation] = None

    def set_progress(self, phase: int, fraction: float) -> None:
        """`fraction` is how far through the whole flow, 0..1."""
        self._phase = phase
        self._fraction = max(0.0, min(1.0, fraction))
        if self._anim is not None:
            self._anim.stop()
        anim = QVariantAnimation(self)
        anim.setStartValue(float(self._shown))
        anim.setEndValue(float(self._fraction))
        anim.setDuration(tokens.DURATION)
        anim.setEasingCurve(tokens.ease())
        anim.valueChanged.connect(self._step)
        anim.start()
        self._anim = anim

    def _step(self, v) -> None:
        self._shown = float(v)
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(T.SURFACE))
        p.fillRect(QRectF(0, h - 1, w, 1), QColor(T.BORDER))
        p.fillRect(QRectF(0, h - 2, w * self._shown, 2), QColor(T.ACCENT))
        font = _font(11, 600, 1.6)
        p.setFont(font)
        fm = p.fontMetrics()
        words = [ph.upper() for ph in self.PHASES]
        gap = 46
        widths = [fm.horizontalAdvance(x) for x in words]
        total = sum(widths) + gap * (len(words) - 1)
        x = (w - total) / 2
        y = (h - 2) / 2 + fm.ascent() / 2 - 1
        for i, (word, ww) in enumerate(zip(words, widths)):
            p.setPen(QColor(T.TEXT if i <= self._phase else T.TEXT_FAINT))
            p.drawText(QPointF(x, y), word)
            x += ww
            if i < len(words) - 1:
                # the chevron between two phases, ink once both sides are reached
                cx = x + gap / 2
                pen = QPen(QColor(T.TEXT if i < self._phase else T.TEXT_FAINT), 1.4)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                cy = (h - 2) / 2
                p.drawPolyline([QPointF(cx - 2, cy - 4), QPointF(cx + 2, cy), QPointF(cx - 2, cy + 4)])
                x += gap
        p.end()


# ── backgrounds ───────────────────────────────────────────────────────────────

class GradientPanel(QWidget):
    """The picture side of a question screen: a wash with soft blobs, the
    website's tour treatment. Cards sit on it via `body`."""

    def __init__(self, family=STEP[0]):
        super().__init__()
        self._wash, self._blobs = family
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(48, 48, 48, 48)
        self.body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_family(self, family) -> None:
        self._wash, self._blobs = family
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = float(self.width()), float(self.height())
        p.fillRect(self.rect(), QColor(self._wash))
        spots = ((0.10, 0.12, 0.75), (0.95, 0.20, 0.65), (0.80, 1.00, 0.80),
                 (0.15, 0.95, 0.70), (0.55, 0.50, 0.55))
        for (fx, fy, fr), colour in zip(spots, self._blobs):
            r = max(w, h) * fr
            g = QRadialGradient(QPointF(w * fx, h * fy), r)
            c = QColor(colour)
            c.setAlpha(210)
            g.setColorAt(0.0, c)
            c.setAlpha(0)
            g.setColorAt(1.0, c)
            p.fillRect(self.rect(), QBrush(g))
        p.fillRect(self.rect(), QColor(255, 255, 255, 40))
        p.end()


class Swoosh(QWidget):
    """The test screens' ground: HeyTora's own ribbon - one broad stroke in
    the website's cyan, blue and violet running across the page, the way the
    stroke runs through the logo and through the site's "How it works".
    Wispr Flow does this with its own mark; this is ours."""

    def __init__(self, dark: bool = False):
        super().__init__()
        self._dark = dark

    def set_dark(self, dark: bool) -> None:
        self._dark = dark
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = float(self.width()), float(self.height())
        ground = STAGE.ground if self._dark else T.BG
        p.fillRect(self.rect(), QColor(ground))
        a = 70 if self._dark else 46
        # Two soft pools of colour in the corners, then the ribbon over them.
        for (fx, fy, colour) in ((0.0, 0.0, tokens.RIBBON_POOLS[0]), (1.0, 1.0, tokens.RIBBON_POOLS[1])):
            g = QRadialGradient(QPointF(w * fx, h * fy), max(w, h) * 0.55)
            c = QColor(colour)
            c.setAlpha(a)
            g.setColorAt(0, c)
            c.setAlpha(0)
            g.setColorAt(1, c)
            p.fillRect(self.rect(), QBrush(g))
        path = QPainterPath(QPointF(-0.1 * w, 0.78 * h))
        path.cubicTo(QPointF(0.22 * w, 0.30 * h), QPointF(0.45 * w, 1.05 * h), QPointF(0.66 * w, 0.58 * h))
        path.cubicTo(QPointF(0.80 * w, 0.26 * h), QPointF(0.95 * w, 0.20 * h), QPointF(1.12 * w, 0.30 * h))
        grad = QLinearGradient(QPointF(0, 0), QPointF(w, 0))
        for stop, colour in zip((0.0, 0.45, 1.0), tokens.RIBBON):
            c = QColor(colour)
            c.setAlpha(a + 10)
            grad.setColorAt(stop, c)
        pen = QPen(QBrush(grad), max(90.0, h * 0.17))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.end()


# ── choices ───────────────────────────────────────────────────────────────────

class Chip(QPushButton):
    """A checkable pill that fills with the accent's pale tint as it is
    chosen, and gives a small press under the pointer."""

    def __init__(self, words: str):
        super().__init__(words.replace("&", "&&"))
        self._words = words
        self.setProperty("value", words)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("chip")
        self._on = 0.0
        self._hover = 0.0
        self._press = 0.0
        self.setFixedHeight(36)
        self.toggled.connect(lambda on: tween(self, "_on", 1.0 if on else 0.0, tokens.DURATION_FAST))

    def enterEvent(self, e) -> None:
        tween(self, "_hover", 1.0, tokens.DURATION_FAST)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        tween(self, "_hover", 0.0, tokens.DURATION_FAST)
        super().leaveEvent(e)

    def mousePressEvent(self, e) -> None:
        tween(self, "_press", 1.0, 90)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        tween(self, "_press", 0.0, tokens.DURATION_FAST)
        super().mouseReleaseEvent(e)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        inset = 1.0 + 1.5 * self._press
        r = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
        base = mix(T.SURFACE, T.SURFACE_HOVER, self._hover * (1 - self._on))
        fill = mix(base.name(), tokens.BLUE_TINT if not T.is_dark() else tokens.BLUE_TINT_DARK, self._on)
        p.setBrush(fill)
        p.setPen(QPen(mix(T.BORDER_STRONG, T.ACCENT, self._on), 1.0 + 0.4 * self._on))
        p.drawRoundedRect(r, 10, 10)
        p.setFont(_font(13, 600 if self._on > 0.5 else 500))
        p.setPen(mix(T.TEXT, T.ACCENT, self._on * 0.85) if not T.is_dark() else QColor(T.TEXT))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self._words)
        p.end()


def chip(words: str) -> Chip:
    return Chip(words)


class OptionCard(QFrame):
    """A checkable card: a title, a line, a tick box. The tint fills in and
    the tick draws itself as it is chosen."""

    toggled = pyqtSignal(bool)

    def __init__(self, title: str, sub: str, *, checked: bool = False, value: str = ""):
        super().__init__()
        self.value = value or title
        self.setObjectName("option")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._checked = checked
        self._on = 1.0 if checked else 0.0
        self._hover = 0.0
        lay = QHBoxLayout(self)
        lay.setContentsMargins(52, 14, 18, 14)
        lay.setSpacing(0)
        words = QVBoxLayout()
        words.setSpacing(3)
        words.addWidget(text(title, 14, 600))
        words.addWidget(text(sub, 13, 400, T.TEXT_DIM))
        lay.addLayout(words, 1)
        self.setStyleSheet("QFrame#option { background: transparent; border: none; }")

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, on: bool) -> None:
        if on != self._checked:
            self._checked = on
            tween(self, "_on", 1.0 if on else 0.0)
            self.toggled.emit(on)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)

    def enterEvent(self, e) -> None:
        tween(self, "_hover", 1.0, tokens.DURATION_FAST)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        tween(self, "_hover", 0.0, tokens.DURATION_FAST)
        super().leaveEvent(e)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self._on
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        base = mix(T.SURFACE, T.SURFACE_HOVER, self._hover * (1 - t))
        p.setBrush(mix(base.name(), tokens.BLUE_TINT if not T.is_dark() else tokens.BLUE_TINT_DARK, t))
        p.setPen(QPen(mix(T.BORDER_STRONG, T.ACCENT, t), 1.0 + 0.5 * t))
        p.drawRoundedRect(r, 12, 12)
        # the tick box, filling and drawing its tick as t goes 0 -> 1
        box = QRectF(18, 16, 20, 20)
        p.setBrush(mix(T.SURFACE, T.ACCENT, t))
        p.setPen(QPen(mix(T.BORDER_STRONG, T.ACCENT, t), 1.4))
        p.drawRoundedRect(box, 5, 5)
        if t > 0.05:
            pen = QPen(QColor(tokens.ON_COLOUR), 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            pts = [QPointF(box.left() + 4.5, box.top() + 10), QPointF(box.left() + 7.5, box.top() + 13),
                   QPointF(box.left() + 13.5, box.top() + 7)]
            path = QPainterPath(pts[0])
            first = min(1.0, t * 2)
            path.lineTo(pts[0] + (pts[1] - pts[0]) * first)
            if t > 0.5:
                path.lineTo(pts[1] + (pts[2] - pts[1]) * ((t - 0.5) * 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        p.end()


# ── the test card and what goes in it ─────────────────────────────────────────

class TestCard(QFrame):
    """The one floating card on a test screen."""

    def __init__(self, question: str, width: int = 560):
        super().__init__()
        self.setObjectName("testCard")
        self.setFixedWidth(width)
        drop_shadow(self, "float")
        self.setStyleSheet(f"QFrame#testCard {{ background: {T.SURFACE};"
                           f" border: 1px solid {T.BORDER}; border-radius: 16px; }}")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(24, 22, 24, 22)
        self.lay.setSpacing(16)
        if question:
            self.question = text(question, 16, 600)
            self.lay.addWidget(self.question)
        self.well = QFrame()
        self.well.setObjectName("well")
        self.well_lay = QVBoxLayout(self.well)
        self.well_lay.setContentsMargins(16, 16, 16, 16)
        self.well_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.well.setStyleSheet(f"QFrame#well {{ background: {T.BG}; border: none;"
                                f" border-radius: 12px; }}")
        self.lay.addWidget(self.well)
        self.foot = QHBoxLayout()
        self.foot.setSpacing(10)
        self.lay.addLayout(self.foot)


class Keycap(QWidget):
    """The site's keycap: a plate with a 2px side. Lit while held - the
    "does the key light up?" of the shortcut test. One per screen (§6)."""

    def __init__(self, words: str, px: int = 18):
        super().__init__()
        self._words = words
        self._lit = False
        self._px = px
        fm_font = _font(px, 600)
        from PyQt6.QtGui import QFontMetrics
        w = QFontMetrics(fm_font).horizontalAdvance(words) + px * 2.4
        self.setFixedSize(QSize(int(w), int(px * 3.2)))

    def set_lit(self, lit: bool) -> None:
        if lit != self._lit:
            self._lit = lit
            self.update()

    def lit(self) -> bool:
        return self._lit

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = float(self.width()), float(self.height())
        drop = 0.0 if self._lit else 3.0
        face = QRectF(1, 1 + (3.0 - drop), w - 2, h - 5)
        side = QColor(T.ACCENT if self._lit else T.BORDER_STRONG)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(side)
        p.drawRoundedRect(face.translated(0, drop), 10, 10)
        p.setBrush(QColor(tokens.BLUE_TINT if self._lit else T.SURFACE))
        p.setPen(QPen(QColor(T.ACCENT if self._lit else T.BORDER_STRONG), 1.2))
        p.drawRoundedRect(face, 10, 10)
        p.setFont(_font(self._px, 600))
        p.setPen(QColor(T.ACCENT if self._lit else T.TEXT))
        p.drawText(face, Qt.AlignmentFlag.AlignCenter, self._words)
        p.end()


# ── panel content: the website's cards ────────────────────────────────────────

class GradientTile(QWidget):
    """A kind of work's app-style tile: its two-stop gradient and a white glyph."""

    def __init__(self, kind: str, size: int = 56):
        super().__init__()
        self._kind = kind
        self._a, self._b = WORK_TILES.get(kind, WORK_TILES["ledger"])
        self._dim = False
        self.setFixedSize(size, size)

    def set_dim(self, dim: bool) -> None:
        self._dim = dim
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = float(self.width())
        g = QLinearGradient(0, 0, s, s)
        a, b = QColor(self._a), QColor(self._b)
        if self._dim:
            a, b = QColor(P.off_a), QColor(P.off_b)
        g.setColorAt(0, a)
        g.setColorAt(1, b)
        p.setBrush(QBrush(g))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(0, 0, s, s), s * 0.26, s * 0.26)
        _glyph(p, self._kind, s)
        p.end()


def _glyph(p: QPainter, kind: str, s: float) -> None:
    pen = QPen(QColor(tokens.ON_COLOUR), s * 0.055)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    k = s / 16.0

    def L(x1, y1, x2, y2):
        p.drawLine(QPointF(x1 * k, y1 * k), QPointF(x2 * k, y2 * k))

    if kind == "ledger":
        p.drawRoundedRect(QRectF(4 * k, 4 * k, 8 * k, 8 * k), k, k)
        L(4, 7, 12, 7)
        L(8, 7, 8, 12)
    elif kind == "briefcase":
        p.drawRoundedRect(QRectF(3.5 * k, 6 * k, 9 * k, 6 * k), k, k)
        p.drawRect(QRectF(6.5 * k, 4.2 * k, 3 * k, 1.8 * k))
    elif kind == "cap":
        path = QPainterPath(QPointF(2.8 * k, 7 * k))
        path.lineTo(8 * k, 4.5 * k)
        path.lineTo(13.2 * k, 7 * k)
        path.lineTo(8 * k, 9.5 * k)
        path.closeSubpath()
        p.drawPath(path)
        L(5.2, 8.3, 5.2, 11)
        L(10.8, 8.3, 10.8, 11)
        L(5.2, 11, 10.8, 11)
    elif kind == "code":
        p.drawPolyline([QPointF(6 * k, 5 * k), QPointF(3.5 * k, 8 * k), QPointF(6 * k, 11 * k)])
        p.drawPolyline([QPointF(10 * k, 5 * k), QPointF(12.5 * k, 8 * k), QPointF(10 * k, 11 * k)])
    elif kind == "chart":
        L(4.5, 12, 4.5, 9)
        L(8, 12, 8, 5)
        L(11.5, 12, 11.5, 7.5)
    elif kind == "video":
        p.drawRoundedRect(QRectF(3 * k, 5 * k, 7 * k, 6 * k), k, k)
        p.drawPolyline([QPointF(10 * k, 7 * k), QPointF(13 * k, 5.5 * k),
                        QPointF(13 * k, 10.5 * k), QPointF(10 * k, 9 * k)])
    elif kind == "calendar":
        p.drawRoundedRect(QRectF(3.5 * k, 4.5 * k, 9 * k, 8 * k), k, k)
        L(3.5, 7, 12.5, 7)
    elif kind == "mic":
        p.drawRoundedRect(QRectF(6.5 * k, 3.5 * k, 3 * k, 6 * k), 1.5 * k, 1.5 * k)
        p.drawArc(QRectF(4.5 * k, 5 * k, 7 * k, 6.5 * k), 200 * 16, 140 * 16)
        L(8, 11.5, 8, 13)
    else:  # wallet
        p.drawRoundedRect(QRectF(3 * k, 5 * k, 10 * k, 7 * k), k, k)
        L(10, 8.5, 11, 8.5)


class MicBars(QWidget):
    """A live waveform: thin bars that rise and settle smoothly with the
    voice, newest on the right, mirrored about the middle line. Small dots
    at rest; the accent while someone is speaking."""

    COUNT = 44

    def __init__(self):
        super().__init__()
        self.setFixedHeight(72)
        self.setMinimumWidth(380)
        self._target = [0.0] * self.COUNT
        self._shown = [0.0] * self.COUNT
        self._phase = 0.0
        from PyQt6.QtCore import QTimer
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def showEvent(self, e) -> None:
        self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e) -> None:
        self._timer.stop()
        super().hideEvent(e)

    def push(self, level: float) -> None:
        """A new loudness, 0..1. It enters on the right with a little natural
        unevenness, so speech reads as speech and not as a block."""
        import math
        import random
        lv = max(0.0, min(1.0, level))
        self._phase += 0.9
        shaped = lv * (0.65 + 0.35 * abs(math.sin(self._phase))) * (0.85 + 0.3 * random.random())
        self._target = self._target[1:] + [min(1.0, shaped)]
        if not self._timer.isActive():
            self._shown = list(self._target)
            self.update()

    def _tick(self) -> None:
        # Rise fast, fall slower: how a level meter feels.
        moved = False
        for i, (s, t) in enumerate(zip(self._shown, self._target)):
            k = 0.45 if t > s else 0.16
            n = s + (t - s) * k
            if abs(n - s) > 1e-3:
                moved = True
            self._shown[i] = n
        if moved:
            self.update()

    def heard(self) -> bool:
        return max(self._target) > 0.25

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bw, gap = 3.0, 4.6
        total = self.COUNT * bw + (self.COUNT - 1) * gap
        x = (self.width() - total) / 2
        h = float(self.height())
        mid = h / 2
        p.setPen(Qt.PenStyle.NoPen)
        for i, lv in enumerate(self._shown):
            # bars at the edges are quieter: the wave fades in and out
            edge = min(1.0, (min(i, self.COUNT - 1 - i) + 1) / 7)
            v = lv * edge
            bh = max(bw, (h - 6) * v)
            colour = mix(T.BORDER_STRONG, T.ACCENT, min(1.0, v * 4))
            p.setBrush(colour)
            p.drawRoundedRect(QRectF(x, mid - bh / 2, bw, bh), bw / 2, bw / 2)
            x += bw + gap
        p.end()


# ── panel content: the website's cards ────────────────────────────────────────

class SkillCard(QWidget):
    """A starter skill's card (after the website's "Made for your work"):
    the tile of the work it suits, its name, what you'd say to use it. It
    slides in and comes into colour when shown; set_on(False) greys it."""

    def __init__(self, kind: str, name: str, phrase: str, skill_id: str = ""):
        super().__init__()
        self.kind = kind
        self.skill_id = skill_id
        self._name = name
        self._phrase = phrase
        self._on = 1.0
        self.setFixedSize(310, 66)
        self.tile = GradientTile(kind, 42)        # kept for callers that check _dim
        self.tile.hide()

    def set_on(self, on: bool) -> None:
        self.tile.set_dim(not on)
        tween(self, "_on", 1.0 if on else 0.0)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self._on
        p.translate(14 * (1 - t), 0)
        r = QRectF(1, 1, self.width() - 16, self.height() - 2)
        p.setBrush(QColor(255, 255, 255, int(150 + 105 * t)))
        p.setPen(QPen(QColor(0, 0, 0, int(8 + 12 * t)), 1))
        p.drawRoundedRect(r, 14, 14)
        s = 42.0
        tile = QRectF(r.left() + 12, r.center().y() - s / 2, s, s)
        a, b = WORK_TILES.get(self.kind, WORK_TILES["ledger"])
        g = QLinearGradient(tile.topLeft(), tile.bottomRight())
        g.setColorAt(0, mix(P.off_a, a, t))
        g.setColorAt(1, mix(P.off_b, b, t))
        p.setBrush(QBrush(g))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(tile, s * 0.26, s * 0.26)
        p.save()
        p.translate(tile.topLeft())
        _glyph(p, self.kind, s)
        p.restore()
        p.setFont(_font(14, 700))
        p.setPen(mix(P.faint, INK_ON_PALE, t))
        p.drawText(QRectF(tile.right() + 12, r.top() + 12, r.width(), 20),
                   Qt.AlignmentFlag.AlignLeft, self._name)
        p.setFont(_font(12, 400))
        p.setPen(mix(P.ghost, P.dim, t))
        p.drawText(QRectF(tile.right() + 12, r.top() + 33, r.width(), 20),
                   Qt.AlignmentFlag.AlignLeft, f"“{self._phrase}”")
        p.end()


class CapabilityCard(QWidget):
    """A capability's gradient card, as the website's tour draws it. Grey
    and sat back while not chosen; it lifts and floods with its colours as
    it is - Wispr Flow's polaroids coming into colour."""

    def __init__(self, key: str, title: str, example: str):
        super().__init__()
        self.key = key
        self._title = title
        self._example = example
        self._on = 0.0
        self.setFixedSize(220, 150)

    def set_on(self, on: bool) -> None:
        tween(self, "_on", 1.0 if on else 0.0)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self._on
        scale = 0.94 + 0.06 * t
        p.translate(self.width() / 2, self.height() / 2 + 4 * (1 - t))
        p.scale(scale, scale)
        p.translate(-self.width() / 2, -self.height() / 2)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        wash, blobs = CAPABILITY[self.key]
        path = QPainterPath()
        path.addRoundedRect(r, 16, 16)
        p.setClipPath(path)
        p.fillRect(r, QColor(P.sunk))
        if t > 0.01:
            c = QColor(wash)
            c.setAlphaF(t)
            p.fillRect(r, c)
            for (fx, fy), colour in zip(((0.1, 0.1), (0.95, 0.3), (0.5, 1.1)), blobs):
                g = QRadialGradient(QPointF(r.width() * fx, r.height() * fy), r.width() * 0.75)
                cc = QColor(colour)
                cc.setAlpha(int(220 * t))
                g.setColorAt(0, cc)
                cc.setAlpha(0)
                g.setColorAt(1, cc)
                p.fillRect(r, QBrush(g))
        p.setClipping(False)
        p.setPen(QPen(QColor(0, 0, 0, 22), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, 16, 16)
        ink = mix(P.ghost, INK_ON_PALE, t)
        p.setPen(ink)
        p.setFont(_font(20, 700, -0.3))
        p.drawText(QRectF(18, 16, r.width() - 36, 30), Qt.AlignmentFlag.AlignLeft, self._title)
        bubble = QRectF(14, r.height() - 58, r.width() - 28, 42)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, int(170 + 65 * t)))
        p.drawRoundedRect(bubble, 12, 12)
        p.setPen(ink)
        p.setFont(_font(12, 500))
        p.drawText(bubble.adjusted(12, 0, -12, 0),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, f"“{self._example}”")
        p.end()


# ── "you stay in charge": the peeker ──────────────────────────────────────────

class ControlVisual(QWidget):
    """The website's peeker (heytora-web home/privacy.tsx), on Tora's
    approval card. "Ask me first": she is awake, eyes following the pointer,
    and the card waits for your OK. "Go ahead": her eyes close, content,
    and the card shows the job done - still asking before anything that
    pays, sends, posts or deletes."""

    BODY = tokens.BLUE

    def __init__(self):
        super().__init__()
        from PyQt6.QtCore import QTimer
        self._awake = 1.0
        self._done = 0.0
        self._look = QPointF(0, 0)
        self.setMinimumSize(420, 380)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._follow)

    def set_mode(self, ask_first: bool) -> None:
        tween(self, "_awake", 1.0 if ask_first else 0.0)
        tween(self, "_done", 0.0 if ask_first else 1.0)

    def showEvent(self, e) -> None:
        self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e) -> None:
        self._timer.stop()
        super().hideEvent(e)

    def _geometry(self):
        w, h = float(self.width()), float(self.height())
        card = QRectF(w / 2 - 170, h / 2 - 40, 340, 170)
        head = QRectF(card.center().x() - 70, card.top() - 92, 140, 105)
        return card, head

    def _follow(self) -> None:
        """Pupils follow the pointer while awake, as on the website."""
        from PyQt6.QtGui import QCursor
        if self._awake < 0.5:
            target = QPointF(0, 0)
        else:
            _card, head = self._geometry()
            here = self.mapFromGlobal(QCursor.pos())
            d = QPointF(here.x() - head.center().x(), here.y() - (head.top() + 50))
            n = max(1.0, (d.x() ** 2 + d.y() ** 2) ** 0.5)
            k = min(5.0, n / 12) / n
            target = QPointF(d.x() * k, d.y() * k)
        new = self._look + (target - self._look) * 0.35
        if (new - self._look).manhattanLength() > 0.05:
            self._look = new
            self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        card, head = self._geometry()
        s = head.width() / 160.0
        ink = QColor(INK_ON_PALE)
        body = QColor(self.BODY)
        # the head: a soft dome, most of it behind the card
        p.save()
        p.translate(head.topLeft())
        p.scale(s, s)
        dome = QPainterPath(QPointF(12, 120))
        dome.lineTo(12, 74)
        dome.cubicTo(QPointF(12, 36), QPointF(42, 8), QPointF(80, 8))
        dome.cubicTo(QPointF(118, 8), QPointF(148, 36), QPointF(148, 74))
        dome.lineTo(148, 120)
        dome.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(body)
        p.drawPath(dome)
        for cx, cy in ((58, 58), (102, 58)):
            p.setBrush(QColor(P.card))
            p.drawEllipse(QPointF(cx, cy), 16, 16)
            p.setBrush(ink)
            p.drawEllipse(QPointF(cx + self._look.x(), cy + 1 + self._look.y()), 7.5, 7.5)
            p.setBrush(QColor(P.card))
            p.drawEllipse(QPointF(cx + 2.5 + self._look.x(), cy - 2 + self._look.y()), 2, 2)
            # the lid, in the head's colour, lowering as she closes her eyes
            lid = 36 * (1 - self._awake)
            p.setBrush(body)
            p.drawRect(QRectF(cx - 18, cy - 18, 36, lid))
            if self._awake < 0.6:
                pen = QPen(QColor(INK_ON_PALE), 2.5)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                c = QColor(INK_ON_PALE)
                c.setAlphaF(0.85 * (1 - self._awake / 0.6))
                pen.setColor(c)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                arc = QPainterPath(QPointF(cx - 10, cy + 1))
                arc.quadTo(QPointF(cx, cy + 9), QPointF(cx + 10, cy + 1))
                p.drawPath(arc)
                p.setPen(Qt.PenStyle.NoPen)
        p.restore()
        # the card, in front of the head
        soft_shadow(p, card, tokens.RADIUS_LG, "float")
        p.setBrush(QColor(P.card))
        p.setPen(QPen(QColor(0, 0, 0, 18), 1))
        p.drawRoundedRect(card, 16, 16)
        # fingers on the rim
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(body)
        # Two hands, four fingers each, curled over the card's top edge.
        for side in (-1, 1):
            for i in range(4):
                fx = card.center().x() + side * (24 + i * 13) - 6
                top = card.top() - 8 + (3 if i in (0, 3) else 0)
                p.drawRoundedRect(QRectF(fx, top, 12, 19), 6, 6)
        self._card_words(p, card)
        p.end()

    def _card_words(self, p: QPainter, card: QRectF) -> None:
        def words(r, s, px, weight, colour, alpha):
            c = QColor(colour)
            c.setAlphaF(alpha)
            p.setFont(_font(px, weight, 1.5 if px < 11 else 0))
            p.setPen(c)
            p.drawText(r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, s)

        inner = card.adjusted(20, 18, -20, -16)
        ask, done = 1 - self._done, self._done
        if ask > 0.01:
            words(QRectF(inner.left(), inner.top(), inner.width(), 14), "TORA WANTS TO", 10, 600, P.faint, ask)
            words(QRectF(inner.left(), inner.top() + 18, inner.width(), 24),
                  "Export March's invoices", 16, 700, INK_ON_PALE, ask)
            words(QRectF(inner.left(), inner.top() + 44, inner.width(), 40),
                  "Saves invoices-march.xlsx to Documents.", 12, 400, P.dim, ask)
            allow = QRectF(inner.right() - 74, inner.bottom() - 30, 74, 30)
            no = QRectF(allow.left() - 86, allow.top(), 78, 30)
            p.setOpacity(ask)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(P.fill))
            p.drawRoundedRect(no, 15, 15)
            p.setBrush(QColor(tokens.BLUE))
            p.drawRoundedRect(allow, 15, 15)
            p.setFont(_font(12, 600))
            p.setPen(QColor(INK_ON_PALE))
            p.drawText(no, Qt.AlignmentFlag.AlignCenter, "Not now")
            p.setFont(_font(12, 700))
            p.setPen(QColor(tokens.ON_COLOUR))
            p.drawText(allow, Qt.AlignmentFlag.AlignCenter, "Allow")
            p.setOpacity(1.0)
        if done > 0.01:
            words(QRectF(inner.left(), inner.top(), inner.width(), 14), "TORA DID", 10, 600, P.faint, done)
            words(QRectF(inner.left(), inner.top() + 18, inner.width(), 24),
                  "Exported March's invoices", 16, 700, INK_ON_PALE, done)
            words(QRectF(inner.left(), inner.top() + 44, inner.width(), 40),
                  "Still asks first before paying, sending, posting or deleting.", 12, 400, P.dim, done)
            bar = QRectF(inner.left(), inner.bottom() - 10, inner.width(), 6)
            p.setOpacity(done)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(P.fill))
            p.drawRoundedRect(bar, 3, 3)
            p.setBrush(QColor(P.ok))
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * done, 6), 3, 3)
            p.setOpacity(1.0)


# ── Learn: things Tora can look at and answer ─────────────────────────────────
#
# The first build used a spreadsheet with "how do I share this sheet?"
# written above it. Tora reads the whole screen, so she read the instruction
# out instead of looking at the sheet. These are self-explaining - a sum
# with a mistake in it - and the words above
# them hide while the shortcut is held (ui/setup_wizard.py).

class _Paper(QWidget):
    """A sheet of squared paper on a white card - homework."""

    def __init__(self, width: int = 532, height: int = 312):
        super().__init__()
        self.setFixedSize(width, height)

    def _card(self, p: QPainter) -> QRectF:
        # Inset far enough that the whole shadow is inside the widget: at
        # 6 px it ran past the bottom edge and was cut off square.
        r = QRectF(16, 6, self.width() - 32, self.height() - 34)
        soft_shadow(p, r, 14, "card", 0.6)
        p.setBrush(QColor(P.card))
        p.setPen(QPen(QColor(0, 0, 0, 22), 1))
        p.drawRoundedRect(r, 14, 14)
        p.save()
        clip = QPainterPath()
        clip.addRoundedRect(r, 14, 14)
        p.setClipPath(clip)
        p.setPen(QPen(QColor(P.grid), 1))
        x = r.left() + 22.0
        while x < r.right():
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            x += 22
        y = r.top() + 22.0
        while y < r.bottom():
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            y += 22
        p.restore()
        return r


class MathSheet(_Paper):
    """A worked equation with one wrong line: "where did I go wrong?" has a
    single right answer Tora can point at - line 2, where +6 should be -6."""

    LINES = ("2x + 6 = 14", "2x = 14 + 6", "2x = 20", "x = 10")

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self._card(p)
        p.setPen(QColor(P.dim))
        p.setFont(_font(12, 600, 1.4))
        p.drawText(QRectF(r.left() + 28, r.top() + 18, 300, 18), Qt.AlignmentFlag.AlignLeft,
                   "SOLVE FOR X")
        p.setFont(_font(30, 500))
        p.setPen(QColor(P.pen))
        for i, line in enumerate(self.LINES):
            p.drawText(QRectF(r.left() + 60, r.top() + 50 + i * 50, r.width() - 120, 44),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, line)
        p.end()


# ── privacy: the peeker, asleep until you come to her ─────────────────────────

class PrivacyVisual(ControlVisual):
    """The website's privacy card, as it is there: one claim on a plain card,
    the peeker asleep over its rim. Move onto the card and she wakes and
    watches you; leave and her eyes close. We look when you ask."""

    CLAIM = "We look when you ask. Not before."

    def __init__(self):
        super().__init__()
        self._awake = 0.0
        self.setMouseTracking(True)

    def set_mode(self, ask_first: bool) -> None:      # not used here
        pass

    def _follow(self) -> None:
        from PyQt6.QtGui import QCursor
        card, _head = self._geometry()
        inside = card.adjusted(-30, -110, 30, 20).contains(QPointF(self.mapFromGlobal(QCursor.pos())))
        want = 1.0 if inside else 0.0
        if (want == 1.0) != (self._awake > 0.5):
            tween(self, "_awake", want)
        super()._follow()

    def _card_words(self, p: QPainter, card: QRectF) -> None:
        inner = card.adjusted(24, 22, -24, -18)
        p.setFont(_font(10, 600, 1.5))
        p.setPen(QColor(P.faint))
        p.drawText(QRectF(inner.left(), inner.top(), inner.width(), 14), Qt.AlignmentFlag.AlignLeft,
                   "PRIVACY")
        p.setFont(_font(19, 700, -0.3))
        p.setPen(QColor(INK_ON_PALE))
        p.drawText(QRectF(inner.left(), inner.top() + 22, inner.width(), 60),
                   Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, self.CLAIM)
        p.setFont(_font(12, 400))
        p.setPen(QColor(P.dim))
        p.drawText(QRectF(inner.left(), inner.top() + 84, inner.width(), 40),
                   Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
                   "Nothing is seen while you're not asking.")


# ── make Tora yours: her colour and her voice ─────────────────────────────────

def play_voice(name: str) -> bool:
    """Play the bundled hello in this voice (assets/voices/). Never the
    network: choosing a voice must not spend talk messages."""
    path = tokens.assets_dir() / "voices" / f"{name.lower()}.wav"
    if not path.exists():
        return False
    try:
        import wave

        import numpy as np
        import sounddevice as sd
        with wave.open(str(path), "rb") as w:
            rate = w.getframerate()
            data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        sd.stop()
        sd.play(data, rate)
        return True
    except Exception:  # noqa: BLE001 - a preview that can't play is not an error
        return False


class Swatch(QWidget):
    """One of Tora's colours: a round chip that rings when chosen."""

    picked = pyqtSignal(str)

    def __init__(self, key: str, name: str, hex_colour: str):
        super().__init__()
        self.key, self._name, self._hex = key, name, hex_colour
        self._on = 0.0
        self.setFixedSize(46, 46)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(name)

    def set_on(self, on: bool) -> None:
        tween(self, "_on", 1.0 if on else 0.0, tokens.DURATION_FAST)

    def mouseReleaseEvent(self, _e) -> None:
        self.picked.emit(self.key)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(23, 23)
        if self._on > 0.01:
            ring = QColor(self._hex)
            ring.setAlphaF(self._on)
            p.setPen(QPen(ring, 2.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, 20, 20)
        g = QLinearGradient(QPointF(8, 8), QPointF(38, 38))
        g.setColorAt(0, QColor(self._hex).lighter(130))
        g.setColorAt(1, QColor(self._hex).darker(110))
        p.setBrush(QBrush(g))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(c, 15 - 1.5 * self._on, 15 - 1.5 * self._on)
        p.end()


class ToraPreview(QWidget):
    """Tora's pointer in the chosen colour, breathing, with her name bubble -
    what the user will actually see next to their cursor."""

    def __init__(self):
        super().__init__()
        from PyQt6.QtCore import QTimer
        self._hex = tokens.BLUE
        self._t = 0.0
        self.setFixedHeight(88)
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)

    def set_colour(self, hex_colour: str) -> None:
        self._hex = hex_colour
        self.update()

    def showEvent(self, e) -> None:
        self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e) -> None:
        self._timer.stop()
        super().hideEvent(e)

    def _tick(self) -> None:
        self._t += 0.04
        self.update()

    def paintEvent(self, _e) -> None:
        import math
        from ui import buddy
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        saved = (buddy.BLUE_LIGHT, buddy.BLUE, buddy.BLUE_DEEP)
        buddy.set_colour(self._hex)
        try:
            cx, cy = 24.0, 18.0                      # the bubble hangs below and right
            breath = 0.5 + 0.5 * math.sin(self._t * 2.4)
            buddy.draw_pointer(p, cx, cy, rotation=-35, scale=1.3, breath=breath)
            buddy.draw_bubble(p, cx, cy, "Tora")
        finally:
            buddy.BLUE_LIGHT, buddy.BLUE, buddy.BLUE_DEEP = saved
        p.end()


class FilledRibbon(Swoosh):
    """The closing screen's ground: the same ribbon, filled with the site's
    colours at full strength - the one loud moment, at the end."""

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = float(self.width()), float(self.height())
        p.fillRect(self.rect(), QColor(T.BG))
        path = QPainterPath(QPointF(-0.1 * w, 0.85 * h))
        path.cubicTo(QPointF(0.20 * w, 0.20 * h), QPointF(0.48 * w, 1.10 * h), QPointF(0.66 * w, 0.52 * h))
        path.cubicTo(QPointF(0.80 * w, 0.18 * h), QPointF(0.96 * w, 0.12 * h), QPointF(1.12 * w, 0.24 * h))
        grad = QLinearGradient(QPointF(0, 0), QPointF(w, 0))
        for stop, colour in zip((0.0, 0.3, 0.55, 0.78, 1.0), tokens.RIBBON_FILLED):
            grad.setColorAt(stop, QColor(colour))
        pen = QPen(QBrush(grad), max(130.0, h * 0.26))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.end()


# ── Make Tora yours: her voice ────────────────────────────────────────────────

class VoiceCarousel(QWidget):
    """One voice at a time, big: her name, a waveform in that voice's
    colour that moves with the actual clip as it plays, and arrows (or the
    arrow keys) to the next. Changing voice plays it."""

    changed = pyqtSignal(str)

    def __init__(self, voices: Sequence[str], current: str = ""):
        super().__init__()
        from PyQt6.QtCore import QTimer
        self._voices = list(voices)
        self._index = self._voices.index(current) if current in self._voices else 0
        self._slide = 1.0                  # 0 -> 1 as a new name arrives
        self._dir = 1
        self._envelope: list[float] = []   # the clip's loudness, 30 per second
        self._started = 0.0
        self._phase = 0.0
        self.setFixedSize(560, 236)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

    def voice(self) -> str:
        return self._voices[self._index]

    def colour(self) -> str:
        return VOICE_COLOURS[self._index % len(VOICE_COLOURS)]

    def showEvent(self, e) -> None:
        self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e) -> None:
        self._timer.stop()
        super().hideEvent(e)

    def go(self, step: int, *, play: bool = True) -> None:
        self._index = (self._index + step) % len(self._voices)
        self._dir = 1 if step >= 0 else -1
        self._slide = 0.0
        tween(self, "_slide", 1.0)
        self.changed.emit(self.voice())
        if play:
            self.play()

    def play(self) -> None:
        import time
        self._envelope = _clip_envelope(self.voice())
        self._started = time.monotonic()
        play_voice(self.voice())

    def _tick(self) -> None:
        self._phase += 0.08
        self.update()

    def _arrows(self) -> tuple[QRectF, QRectF]:
        y = 136.0
        return QRectF(8, y - 24, 48, 48), QRectF(self.width() - 56, y - 24, 48, 48)

    def mouseReleaseEvent(self, e) -> None:
        left, right = self._arrows()
        if left.contains(e.position()):
            self.go(-1)
        elif right.contains(e.position()):
            self.go(1)
        else:
            self.play()

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Left:
            self.go(-1)
        elif e.key() == Qt.Key.Key_Right:
            self.go(1)
        elif e.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return):
            self.play()
        else:
            super().keyPressEvent(e)

    def _level(self, i: int, count: int) -> float:
        """Bar i's height: the clip's loudness while it plays, a slow breath
        when it doesn't."""
        import math
        import time
        if self._envelope:
            t = time.monotonic() - self._started
            k = int(t * 30) - (count - 1 - i)
            if 0 <= k < len(self._envelope):
                return min(1.0, self._envelope[k] * 1.4)
            if int(t * 30) < len(self._envelope) + count:
                return 0.06
            self._envelope = []
        return 0.10 + 0.06 * math.sin(self._phase + i * 0.35)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = float(self.width())
        colour = self.colour()
        # the name, sliding in from the side it came from
        off = (1 - self._slide) * 40 * self._dir
        p.setOpacity(0.25 + 0.75 * self._slide)
        p.setFont(_font(12, 600, 1.5))
        p.setPen(QColor(T.TEXT_DIM))
        p.drawText(QRectF(off, 4, w, 18), Qt.AlignmentFlag.AlignHCenter,
                   f"VOICE {self._index + 1} OF {len(self._voices)}")
        p.setFont(_font(46, 700, -1.0))
        p.setPen(QColor(T.TEXT))
        p.drawText(QRectF(off, 24, w, 60), Qt.AlignmentFlag.AlignHCenter, self.voice())
        p.setOpacity(1.0)
        # the waveform, in this voice's colour
        count, bw, gap = 34, 5.0, 6.0
        total = count * bw + (count - 1) * gap
        x = (w - total) / 2
        mid = 136.0
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(count):
            edge = min(1.0, (min(i, count - 1 - i) + 1) / 6)
            v = self._level(i, count) * edge
            bh = max(bw, 80 * v)
            c = QColor(colour)
            c.setAlpha(int(110 + 145 * min(1.0, v * 2)))
            p.setBrush(c)
            p.drawRoundedRect(QRectF(x, mid - bh / 2, bw, bh), bw / 2, bw / 2)
            x += bw + gap
        # arrows
        for r, left in zip(self._arrows(), (True, False)):
            p.setBrush(QColor(T.SURFACE))
            p.setPen(QPen(QColor(T.BORDER_STRONG), 1))
            p.drawEllipse(r)
            pen = QPen(QColor(T.TEXT), 2.2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            cx, cy = r.center().x(), r.center().y()
            k = -1 if left else 1
            p.drawPolyline([QPointF(cx - 3 * k, cy - 7), QPointF(cx + 4 * k, cy), QPointF(cx - 3 * k, cy + 7)])
        # dots
        n = len(self._voices)
        dx = w / 2 - n * 7
        for i in range(n):
            on = i == self._index
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(colour) if on else QColor(T.BORDER_STRONG))
            p.drawEllipse(QPointF(dx + i * 14 + 7, 204), 4 if on else 3, 4 if on else 3)
        p.setFont(_font(12, 500))
        p.setPen(QColor(T.TEXT_FAINT))
        p.drawText(QRectF(0, 216, w, 18), Qt.AlignmentFlag.AlignHCenter, "Click to hear her again")
        p.end()


def _clip_envelope(name: str) -> list[float]:
    """The bundled clip's loudness, 30 values a second, for the waveform."""
    path = tokens.assets_dir() / "voices" / f"{name.lower()}.wav"
    try:
        import wave

        import numpy as np
        with wave.open(str(path), "rb") as w:
            rate = w.getframerate()
            data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype("float32") / 32768
        hop = max(1, rate // 30)
        frames = [float(np.sqrt(np.mean(np.square(data[i:i + hop])))) for i in range(0, len(data), hop)]
        top = max(frames) or 1.0
        return [f / top for f in frames]
    except Exception:  # noqa: BLE001
        return []


def stylesheet() -> str:
    """The onboarding's own rules, on top of app_theme.qss()."""
    return f"""
    QDialog {{ background: {T.BG}; }}
    QWidget#column {{ background: {T.SURFACE}; }}
    QPushButton#chip {{
        background: {T.SURFACE}; border: 1px solid {T.BORDER_STRONG}; border-radius: {tokens.RADIUS_SM}px;
        color: {T.TEXT}; font-size: 13px; font-weight: 500; padding: 8px 10px;
    }}
    QPushButton#chip:hover {{ background: {T.SURFACE_HOVER}; }}
    QPushButton#chip:checked {{ background: {T.ACCENT_SOFT}; border-color: {T.ACCENT}; }}
    QPushButton#continue {{
        background: {T.ACCENT}; border: none; border-radius: {tokens.pill(tokens.BUTTON_H["lg"])}px;
        color: {tokens.ON_COLOUR}; font-size: 14px; font-weight: 700; padding: 0px 24px;
        min-height: {tokens.BUTTON_H["lg"]}px;
    }}
    QPushButton#continue:hover {{ background: {T.ACCENT_HOVER}; }}
    QPushButton#continue:disabled {{ background: {T.BORDER_STRONG}; color: {T.SURFACE}; }}
    QPushButton#back, QPushButton#skip {{
        background: transparent; border: none; color: {T.TEXT_DIM};
        font-size: 13px; font-weight: 600; padding: 4px 0px;
    }}
    QPushButton#back:hover, QPushButton#skip:hover {{ color: {T.TEXT}; }}
    QPushButton#answer {{
        background: {T.SURFACE_HOVER}; border: none; border-radius: {tokens.pill(tokens.BUTTON_H["sm"])}px; color: {T.TEXT};
        font-size: 13px; font-weight: 600; min-height: 18px; padding: 6px 16px;
    }}
    QPushButton#answer:hover {{ background: {T.BORDER}; }}
    QPushButton#answerYes {{
        background: {T.ACCENT}; border: none; border-radius: {tokens.pill(tokens.BUTTON_H["sm"])}px;
        color: {tokens.ON_COLOUR};
        font-size: 13px; font-weight: 700; min-height: 18px; padding: 6px 18px;
    }}
    QPushButton#answerYes:hover {{ background: {T.ACCENT_HOVER}; }}
    QPushButton#answerYes:disabled {{ background: {T.BORDER_STRONG}; color: {T.SURFACE}; }}
    """
