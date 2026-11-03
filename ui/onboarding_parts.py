"""The pieces the sign-in window, the tour and Settings > Tora share.

The old first launch (docs/ui/DESIGN.md §7) was built from these: question
screens beside a gradient picture, test screens over the logo's swoosh. Its
pages went in revamp step 6b; what is left is what is still drawn - the
swoosh behind the sign-in window, the keycap the tour lights, her voice
carousel and colour swatches, and the voice samples they play.
Colours and gradients come from app_theme / tokens.
"""

from __future__ import annotations

from typing import Sequence

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient,
)
from PyQt6.QtWidgets import QLabel, QPushButton, QWidget

from ui import app_theme as T
from ui import kit, tokens
from ui.kit import tween
from ui.tokens import STAGE, VOICE_COLOURS


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
