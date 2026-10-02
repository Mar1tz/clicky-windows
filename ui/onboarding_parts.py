"""The pieces the first launch is built from (docs/ui/DESIGN.md §7).

The onboarding has exactly two layouts, after Wispr Flow's:

* a QUESTION screen: a white left column (title, one line, choices, a
  Continue pinned to the bottom) beside a gradient picture panel that
  reacts to the answer;
* a TEST screen: centred title and one line over the logo's swoosh, and a
  single floating card holding the live check (mic bars, the keycap, the
  pretend app Tora points into).

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
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

from ui import app_theme as T
from ui import tokens

# ── the website's gradient families (heytora-web/src/components/home) ─────────

# what-gradient.tsx: one family per capability. (wash, blobs)
CAPABILITY = {
    "ask":   ("#D7EBFF", ("#7CCBFF", "#00B6FD", "#1E7FE8", "#8FE3F0", "#E3B0FF")),
    "show":  ("#D5DDFF", ("#2B63D6", "#6F7CF5", "#4F86E8", "#A48CFF", "#CFE9FF")),
    "teach": ("#F1E0FA", ("#B89CFF", "#F0A6FF", "#E48AD8", "#FFC6DE", "#D9D2FF")),
    "do":    ("#D6F4F0", ("#7FB6FF", "#39D5C6", "#2FB8C8", "#8FE8D6", "#BDF2E6")),
    "build": ("#FBF3DD", ("#9CE9DA", "#FFE08A", "#F6B96B", "#FFF0C4", "#B6EE7A")),
}

# how.tsx: one family per step, used in order through the phases.
STEP = (
    ("#BDEBFF", ("#00B6FD", "#3D7BFF", "#7FE3D0", "#BDEBFF")),
    ("#FFE3C4", ("#FF9A3C", "#FFC08A", "#DDF08A", "#FFE3C4")),
    ("#D9D2FF", ("#8C7CFF", "#E9A2FF", "#FFC6DE", "#D9D2FF")),
    ("#D6F7EE", ("#2FD0C0", "#B6EE7A", "#7CC8FF", "#D6F7EE")),
)

# packs.tsx: two-stop pack tiles.
PACK = {
    "ledger":    ("#00B6FD", "#3D7BFF"),
    "briefcase": ("#FFA24C", "#FF6B6B"),
    "cap":       ("#7CC4FF", "#00A3E8"),
    "code":      ("#4A505B", "#1F2329"),
    "chart":     ("#5B8CFF", "#1E4FD6"),
    "video":     ("#FF6B6B", "#E0314B"),
    "calendar":  ("#A99BFF", "#6F5BF5"),
    "mic":       ("#C9A0FF", "#8C5BF5"),
    "wallet":    ("#FFC27A", "#F28C3A"),
}

INK_ON_PALE = "#292929"


def _font(px: int, weight: int = 400, spacing: float = 0.0) -> QFont:
    f = QFont(tokens.font_family())
    f.setPixelSize(px)
    f.setWeight(QFont.Weight(weight))
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return f


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
        # One fixed layout of blobs, so a step always looks the same.
        spots = ((0.10, 0.12, 0.75), (0.95, 0.20, 0.65), (0.80, 1.00, 0.80),
                 (0.15, 0.95, 0.70), (0.55, 0.50, 0.55))
        for (fx, fy, fr), colour in zip(spots, self._blobs):
            r = max(w, h) * fr
            g = QRadialGradient(QPointF(w * fx, h * fy), r)
            c = QColor(colour)
            c.setAlpha(210)
            g.setColorAt(0.0, c)
            c2 = QColor(colour)
            c2.setAlpha(0)
            g.setColorAt(1.0, c2)
            p.fillRect(self.rect(), QBrush(g))
        # A pale veil keeps the cards on top readable.
        veil = QColor(255, 255, 255, 40)
        p.fillRect(self.rect(), veil)
        p.end()


class Swoosh(QWidget):
    """The test screens' ground: the logo's swoosh, huge and barely there -
    Wispr Flow does this with its own mark's loops."""

    def __init__(self, dark: bool = False):
        super().__init__()
        self._dark = dark
        self._svg = None
        try:
            from PyQt6.QtSvg import QSvgRenderer
            src = (tokens.assets_dir() / "brand" / "wordmark.svg").read_text(encoding="utf-8")
            self._src = src
            self._renderer_cls = QSvgRenderer
        except Exception:  # noqa: BLE001
            self._src = ""

    def set_dark(self, dark: bool) -> None:
        self._dark = dark
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        ground = "#141414" if self._dark else T.BG
        p.fillRect(self.rect(), QColor(ground))
        if self._src:
            swoosh = "#1F1F1F" if self._dark else T.SURFACE_HOVER
            svg = self._src.replace("{INK}", "none").replace("{SWOOSH}", swoosh)
            r = self._renderer_cls(QByteArray(svg.encode("utf-8")))
            w = self.width() * 2.4
            h = w * r.viewBoxF().height() / r.viewBoxF().width()
            p.translate(self.width() / 2, self.height() / 2)
            p.rotate(-14)
            r.render(p, QRectF(-w / 2, -h / 2, w, h))
        p.end()


# ── choices ───────────────────────────────────────────────────────────────────

def chip(words: str) -> QPushButton:
    """A checkable pill. Chosen = the accent's pale fill, as Wispr Flow's are."""
    b = QPushButton(words.replace("&", "&&"))
    b.setProperty("value", words)
    b.setCheckable(True)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setObjectName("chip")
    return b


class OptionCard(QFrame):
    """A checkable card: a title, a line, a tick box. For the choices that
    need a sentence of explanation, where a chip would not do."""

    toggled = pyqtSignal(bool)

    def __init__(self, title: str, sub: str, *, checked: bool = False, value: str = ""):
        super().__init__()
        self.value = value or title
        self.setObjectName("option")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._on = checked
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(14)
        self._tick = _Tick(checked)
        lay.addWidget(self._tick, 0, Qt.AlignmentFlag.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(4)
        words.addWidget(text(title, 14, 600))
        words.addWidget(text(sub, 13, 400, T.TEXT_DIM))
        lay.addLayout(words, 1)
        self._restyle()

    def isChecked(self) -> bool:
        return self._on

    def setChecked(self, on: bool) -> None:
        if on != self._on:
            self._on = on
            self._tick.set_on(on)
            self._restyle()
            self.toggled.emit(on)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._on)

    def _restyle(self) -> None:
        border = T.ACCENT if self._on else T.BORDER_STRONG
        fill = T.ACCENT_SOFT if self._on else T.SURFACE
        self.setStyleSheet(f"QFrame#option {{ background: {fill}; border: 1px solid {border};"
                           f" border-radius: 12px; }}")


class _Tick(QWidget):
    def __init__(self, on: bool):
        super().__init__()
        self._on = on
        self.setFixedSize(20, 20)

    def set_on(self, on: bool) -> None:
        self._on = on
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(1, 1, 18, 18)
        if self._on:
            p.setBrush(QColor(T.ACCENT))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(r, 5, 5)
            pen = QPen(QColor("#FFFFFF"), 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.drawPolyline([QPointF(5.5, 10), QPointF(8.5, 13), QPointF(14.5, 7)])
        else:
            p.setBrush(QColor(T.SURFACE))
            p.setPen(QPen(QColor(T.BORDER_STRONG), 1.4))
            p.drawRoundedRect(r, 5, 5)
        p.end()


# ── the test card and what goes in it ─────────────────────────────────────────

class TestCard(QFrame):
    """The one floating card on a test screen."""

    def __init__(self, question: str, width: int = 560):
        super().__init__()
        self.setObjectName("testCard")
        self.setFixedWidth(width)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(48)
        shadow.setOffset(0, 12)
        shadow.setColor(QColor(0, 0, 0, 45))
        self.setGraphicsEffect(shadow)
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


class MicBars(QWidget):
    """Thirteen bars that rise with the voice. Pale at rest; the accent while
    someone is speaking - the "do you see the bars?" of the mic test."""

    COUNT = 13

    def __init__(self):
        super().__init__()
        self.setFixedHeight(64)
        self.setMinimumWidth(360)
        self._levels = [0.0] * self.COUNT

    def push(self, level: float) -> None:
        """A new loudness, 0..1. The newest bar enters on the right."""
        self._levels = self._levels[1:] + [max(0.0, min(1.0, level))]
        self.update()

    def heard(self) -> bool:
        return max(self._levels) > 0.25

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bw, gap = 14.0, 12.0
        total = self.COUNT * bw + (self.COUNT - 1) * gap
        x = (self.width() - total) / 2
        h = float(self.height())
        p.setPen(Qt.PenStyle.NoPen)
        for lv in self._levels:
            bh = max(bw, h * (0.35 + 0.65 * lv))
            p.setBrush(QColor(T.ACCENT) if lv > 0.12 else QColor(T.BORDER))
            p.drawRoundedRect(QRectF(x, (h - bh) / 2, bw, bh), bw / 2, bw / 2)
            x += bw + gap
        p.end()


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
        p.setBrush(QColor("#EAF2FF" if self._lit else T.SURFACE))
        p.setPen(QPen(QColor(T.ACCENT if self._lit else T.BORDER_STRONG), 1.2))
        p.drawRoundedRect(face, 10, 10)
        p.setFont(_font(self._px, 600))
        p.setPen(QColor(T.ACCENT if self._lit else T.TEXT))
        p.drawText(face, Qt.AlignmentFlag.AlignCenter, self._words)
        p.end()


# ── the pretend app Tora points into ──────────────────────────────────────────

class PretendApp(QFrame):
    """A small invoices app, drawn for the Learn screens. It is on the real
    screen, so when the user asks "where do I export this?" the real Tora
    looks at it and points at the real Export button - the product working,
    not a recording of it."""

    def __init__(self):
        super().__init__()
        self.setObjectName("pretend")
        self.setFixedSize(560, 300)
        self.setStyleSheet(f"""
            QFrame#pretend {{ background: #FFFFFF; border: 1px solid #DCDAD6; border-radius: 12px; }}
            QFrame#pretendBar {{ background: #F4F3F0; border: none; border-top-left-radius: 12px;
                                 border-top-right-radius: 12px; border-bottom: 1px solid #E6E4E0; }}
            QLabel {{ color: #292929; background: transparent; }}
            QPushButton#tool {{ background: #FFFFFF; color: #292929; border: 1px solid #DCDAD6;
                                border-radius: 6px; padding: 4px 10px; font-size: 12px; font-weight: 600; }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = QFrame()
        bar.setObjectName("pretendBar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 10, 14, 10)
        bl.setSpacing(8)
        name = text("Ledgerly  ·  Invoices, March", 13, 700, "#292929", wrap=False)
        bl.addWidget(name)
        bl.addStretch(1)
        self.buttons: dict[str, QPushButton] = {}
        for word in ("New", "Filter", "Share", "Export"):
            b = QPushButton(word)
            b.setObjectName("tool")
            self.buttons[word] = b
            bl.addWidget(b)
        lay.addWidget(bar)
        rows = QVBoxLayout()
        rows.setContentsMargins(16, 10, 16, 12)
        rows.setSpacing(0)
        for i, (who, when, amount, state) in enumerate((
                ("Northwind Traders", "2 Mar", "R 12 400.00", "Paid"),
                ("Contoso Ltd", "9 Mar", "R 4 980.00", "Due"),
                ("Fabrikam Studio", "14 Mar", "R 18 250.00", "Due"),
                ("Tailspin Toys", "21 Mar", "R 2 310.00", "Draft"),
                ("Adventure Works", "28 Mar", "R 9 060.00", "Paid"))):
            row = QHBoxLayout()
            row.setContentsMargins(0, 9, 0, 9)
            for words, stretch, weight in ((who, 3, 600), (when, 1, 400), (amount, 2, 400), (state, 1, 600)):
                lab = text(words, 12, weight, "#292929" if weight == 600 else "#5C5C5C", wrap=False)
                row.addWidget(lab, stretch)
            holder = QFrame()
            holder.setObjectName("prow")
            holder.setLayout(row)
            # By name: a bare QFrame rule reaches the QLabels inside (a QLabel is a QFrame).
            holder.setStyleSheet("QFrame#prow { border: none; border-top: 1px solid #EEEDEA; }" if i else "")
            rows.addWidget(holder)
        lay.addLayout(rows)
        lay.addStretch(1)


# ── panel content: the website's cards ────────────────────────────────────────

class GradientTile(QWidget):
    """A pack's app-style tile: its two-stop gradient and a white glyph."""

    def __init__(self, kind: str, size: int = 56):
        super().__init__()
        self._kind = kind
        self._a, self._b = PACK.get(kind, PACK["ledger"])
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
            a, b = QColor("#D9D7D3"), QColor("#BDBAB5")
        g.setColorAt(0, a)
        g.setColorAt(1, b)
        p.setBrush(QBrush(g))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(0, 0, s, s), s * 0.26, s * 0.26)
        _glyph(p, self._kind, s)
        p.end()


def _glyph(p: QPainter, kind: str, s: float) -> None:
    pen = QPen(QColor("#FFFFFF"), s * 0.055)
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


class PackCard(QFrame):
    """A white chip-card the panel shows: tile, name, the phrase you'd say -
    the website's "Made for your work" chips."""

    def __init__(self, kind: str, name: str, phrase: str):
        super().__init__()
        self.setObjectName("packCard")
        self.kind = kind
        self.setFixedWidth(300)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 12, 16, 12)
        lay.setSpacing(12)
        self.tile = GradientTile(kind, 44)
        lay.addWidget(self.tile)
        words = QVBoxLayout()
        words.setSpacing(2)
        self._name = text(name, 14, 700, INK_ON_PALE, wrap=False)
        self._phrase = text(f"“{phrase}”", 12, 400, "#5C5C5C", wrap=False)
        words.addWidget(self._name)
        words.addWidget(self._phrase)
        lay.addLayout(words, 1)
        self.set_on(True)

    def set_on(self, on: bool) -> None:
        self.tile.set_dim(not on)
        alpha = 255 if on else 150
        self.setStyleSheet(f"QFrame#packCard {{ background: rgba(255,255,255,{alpha});"
                           f" border: 1px solid rgba(0,0,0,{18 if on else 8}); border-radius: 14px; }}")
        self._name.setStyleSheet(f"color: {INK_ON_PALE if on else '#8A8884'}; background: transparent;"
                                 " font-size: 14px; font-weight: 700;")


class CapabilityCard(QWidget):
    """A capability's gradient card, as the website's tour draws it. Greyed
    while not chosen - Wispr Flow's polaroids that come into colour."""

    def __init__(self, key: str, title: str, example: str):
        super().__init__()
        self.key = key
        self._title = title
        self._example = example
        self._on = False
        self.setFixedSize(220, 150)

    def set_on(self, on: bool) -> None:
        self._on = on
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        wash, blobs = CAPABILITY[self.key]
        path = QPainterPath()
        path.addRoundedRect(r, 16, 16)
        p.setClipPath(path)
        if self._on:
            p.fillRect(r, QColor(wash))
            for (fx, fy), colour in zip(((0.1, 0.1), (0.95, 0.3), (0.5, 1.1)), blobs):
                g = QRadialGradient(QPointF(r.width() * fx, r.height() * fy), r.width() * 0.75)
                c = QColor(colour)
                c.setAlpha(220)
                g.setColorAt(0, c)
                c.setAlpha(0)
                g.setColorAt(1, c)
                p.fillRect(r, QBrush(g))
        else:
            p.fillRect(r, QColor("#ECEAE6"))
        p.setClipping(False)
        p.setPen(QPen(QColor(0, 0, 0, 22), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, 16, 16)
        ink = QColor(INK_ON_PALE if self._on else "#9A9894")
        p.setPen(ink)
        p.setFont(_font(20, 700, -0.3))
        p.drawText(QRectF(18, 16, r.width() - 36, 30), Qt.AlignmentFlag.AlignLeft, self._title)
        # the example ask, on a white bubble
        bubble = QRectF(14, r.height() - 58, r.width() - 28, 42)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 235 if self._on else 170))
        p.drawRoundedRect(bubble, 12, 12)
        p.setPen(ink)
        p.setFont(_font(12, 500))
        p.drawText(bubble.adjusted(12, 0, -12, 0),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, f"“{self._example}”")
        p.end()


def stylesheet() -> str:
    """The onboarding's own rules, on top of app_theme.qss()."""
    return f"""
    QDialog {{ background: {T.BG}; }}
    QWidget#column {{ background: {T.SURFACE}; }}
    QPushButton#chip {{
        background: {T.SURFACE}; border: 1px solid {T.BORDER_STRONG}; border-radius: 10px;
        color: {T.TEXT}; font-size: 13px; font-weight: 500; padding: 8px 10px;
    }}
    QPushButton#chip:hover {{ background: {T.SURFACE_HOVER}; }}
    QPushButton#chip:checked {{ background: {T.ACCENT_SOFT}; border-color: {T.ACCENT}; }}
    QPushButton#continue {{
        background: {T.ACCENT}; border: none; border-radius: 22px; color: #FFFFFF;
        font-size: 14px; font-weight: 700; padding: 0px 24px; min-height: 44px;
    }}
    QPushButton#continue:hover {{ background: {T.ACCENT_HOVER}; }}
    QPushButton#continue:disabled {{ background: {T.BORDER_STRONG}; color: {T.SURFACE}; }}
    QPushButton#back, QPushButton#skip {{
        background: transparent; border: none; color: {T.TEXT_DIM};
        font-size: 13px; font-weight: 600; padding: 4px 0px;
    }}
    QPushButton#back:hover, QPushButton#skip:hover {{ color: {T.TEXT}; }}
    QPushButton#answer {{
        background: {T.SURFACE_HOVER}; border: none; border-radius: 15px; color: {T.TEXT};
        font-size: 13px; font-weight: 600; min-height: 18px; padding: 6px 16px;
    }}
    QPushButton#answer:hover {{ background: {T.BORDER}; }}
    QPushButton#answerYes {{
        background: {T.ACCENT}; border: none; border-radius: 15px; color: #FFFFFF;
        font-size: 13px; font-weight: 700; min-height: 18px; padding: 6px 18px;
    }}
    QPushButton#answerYes:hover {{ background: {T.ACCENT_HOVER}; }}
    QPushButton#answerYes:disabled {{ background: {T.BORDER_STRONG}; color: {T.SURFACE}; }}
    """
