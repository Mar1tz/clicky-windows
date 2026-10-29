"""Tiles and small pieces for the shape's settings pages, laid out as
HeyClicky lays out its own (heyclicky2.0.mp4, 29 Oct 2026) - "a lot more
neater than ours" - in HeyTora's colours, type and light/dark.

    CursorTile   a square with Tora's own cursor glowing in one colour;
                 the chosen one ringed in the accent, with a tick
    VoiceTile    a voice: its colour's little waveform, its name, two words
                 on how it sounds; tapped, it plays the hello and is chosen
    LevelBars    a row of bars that move with the microphone
    Radio        the round mark of a choice in a list
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QRadialGradient
from PyQt6.QtWidgets import QAbstractButton, QWidget

from ui import app_theme as T
from ui import kit, tokens

# How each voice sounds, in Google's own words for the Gemini voices.
VOICE_FEEL = {
    "Zephyr": "Bright", "Puck": "Upbeat", "Charon": "Informative", "Kore": "Firm",
    "Fenrir": "Excitable", "Leda": "Youthful", "Orus": "Firm", "Aoede": "Breezy",
    "Callirrhoe": "Easy-going", "Autonoe": "Bright", "Enceladus": "Breathy", "Iapetus": "Clear",
}


def _tick(p: QPainter, centre: QPointF, r: float) -> None:
    """The chosen mark: an accent disc with a white tick."""
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(T.ACCENT))
    p.drawEllipse(centre, r, r)
    pen = QPen(QColor(tokens.ON_COLOUR), 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    path = QPainterPath(QPointF(centre.x() - r * 0.45, centre.y()))
    path.lineTo(centre.x() - r * 0.1, centre.y() + r * 0.38)
    path.lineTo(centre.x() + r * 0.5, centre.y() - r * 0.35)
    p.drawPath(path)


class _Tile(QAbstractButton):
    """A square choice: a soft plate, an accent ring and tick while chosen."""

    def __init__(self, tip: str):
        super().__init__()
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tip)
        self.setAccessibleName(tip)
        self._hover = False

    def enterEvent(self, e) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, e) -> None:
        self._hover = False
        self.update()

    def _plate(self, p: QPainter) -> QRectF:
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        p.setBrush(QColor(T.SURFACE_HOVER if self._hover and not self.isChecked() else T.SURFACE))
        p.setPen(QPen(QColor(T.ACCENT), 2.0) if self.isChecked() else QPen(QColor(T.BORDER), 1.0))
        p.drawRoundedRect(r, tokens.RADIUS_MD, tokens.RADIUS_MD)
        return r

    def _chosen(self, p: QPainter, r: QRectF) -> None:
        if self.isChecked():
            _tick(p, QPointF(r.right() - 12, r.top() + 12), 7)


class CursorTile(_Tile):
    """Tora's cursor in one colour, glowing on its tile."""

    def __init__(self, key: str, name: str, hex_colour: str):
        super().__init__(name)
        self.key, self.hex = key, hex_colour
        self.setMinimumSize(72, 78)

    def paintEvent(self, _e) -> None:
        from ui import buddy
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self._plate(p)
        c = r.center()
        glow = QRadialGradient(c, min(r.width(), r.height()) * 0.42)
        halo = QColor(self.hex)
        halo.setAlpha(110)
        glow.setColorAt(0.0, halo)
        halo.setAlpha(0)
        glow.setColorAt(1.0, halo)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawEllipse(c, r.width() * 0.42, r.height() * 0.42)
        saved = (buddy.BLUE_LIGHT, buddy.BLUE, buddy.BLUE_DEEP)
        buddy.set_colour(self.hex)
        try:
            buddy.draw_pointer(p, c.x() - 2, c.y() - 4, rotation=-35, scale=1.25)
        finally:
            buddy.BLUE_LIGHT, buddy.BLUE, buddy.BLUE_DEEP = saved
        self._chosen(p, r)
        p.end()


class VoiceTile(_Tile):
    """A voice: a little waveform in its colour, its name, how it sounds.
    While its hello plays the waveform moves with the clip itself - its
    loudness, 30 times a second (`play(envelope)`) - and settles after."""

    FPS = 30
    REST = (6, 12, 18, 10, 14)            # the bars at rest, px

    def __init__(self, name: str, colour: str):
        super().__init__(f"{name} - tap to hear")
        self.name, self.colour = name, colour
        self.setMinimumSize(96, 76)
        self._envelope: list[float] = []
        self._frame = 0
        self._timer = None

    def play(self, envelope: list[float]) -> None:
        from PyQt6.QtCore import QTimer
        self._envelope, self._frame = list(envelope), 0
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.setInterval(1000 // self.FPS)
            self._timer.timeout.connect(self._tick)
        if self._envelope:
            self._timer.start()

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
        self._envelope = []
        self.update()

    def playing(self) -> bool:
        return bool(self._envelope) and self._frame < len(self._envelope)

    def _tick(self) -> None:
        self._frame += 1
        if not self.playing():
            self.stop()
            return
        self.update()

    def bar_heights(self) -> list[float]:
        """The bars now: at rest, or each lifted by the clip's loudness here,
        the outer ones a moment behind the middle so it ripples."""
        if not self.playing():
            return [float(h) for h in self.REST]
        out = []
        for i, rest in enumerate(self.REST):
            k = max(0, self._frame - abs(i - 2))
            level = self._envelope[min(k, len(self._envelope) - 1)]
            out.append(4.0 + level * 24.0 * (0.6 + 0.4 * rest / 18))
        return out

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self._plate(p)
        cx = r.center().x()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.colour))
        for i, h in enumerate(self.bar_heights()):              # her waveform, small
            x = cx - 13 + i * 6
            p.drawRoundedRect(QRectF(x, r.top() + 24 - h / 2, 3, h), 1.5, 1.5)
        p.setPen(QColor(T.TEXT))
        p.setFont(kit.font(13, 600))
        p.drawText(QRectF(r.left(), r.top() + 36, r.width(), 18), Qt.AlignmentFlag.AlignHCenter, self.name)
        p.setPen(QColor(T.TEXT_FAINT))
        p.setFont(kit.font(11, 400))
        p.drawText(QRectF(r.left(), r.top() + 53, r.width(), 16), Qt.AlignmentFlag.AlignHCenter,
                   VOICE_FEEL.get(self.name, ""))
        self._chosen(p, r)
        p.end()


class LevelBars(QWidget):
    """Bars that move with the microphone: `level` 0-1, set by the page.
    Small in Settings; `big` in the first launch, where the middle bars
    stand taller as the voice lifts them (HeyClicky's "can I hear you")."""

    BARS = 18

    def __init__(self, bars: int = BARS, big: bool = False):
        super().__init__()
        self.level = 0.0
        self.bars, self.big = bars, big
        self._step, self._w, self._h = (11, 6, 44) if big else (7, 4, 16)
        self.setFixedSize(bars * self._step, self._h + 6)

    def heights(self) -> list[float]:
        """Each bar's height now: all the same, small ones; big ones rise
        in the middle with the level."""
        if not self.big:
            return [float(self._h)] * self.bars
        mid = (self.bars - 1) / 2
        out = []
        for i in range(self.bars):
            near = 1.0 - abs(i - mid) / mid
            out.append(self._h * (0.32 + 0.68 * min(1.0, self.level * (0.45 + 0.9 * near))))
        return out

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        lit = round(self.level * self.bars)
        p.setPen(Qt.PenStyle.NoPen)
        cy = self.height() / 2
        for i, h in enumerate(self.heights()):
            on = (self.level > 0.04) if self.big else i < lit
            p.setBrush(QColor(T.ACCENT if on else T.BORDER_STRONG))
            p.drawRoundedRect(QRectF(i * self._step, cy - h / 2, self._w, h), self._w / 2, self._w / 2)
        p.end()


class Radio(QWidget):
    """The round mark of a choice in a list - filled with a tick when on."""

    def __init__(self, on: bool):
        super().__init__()
        self.on = on
        self.setFixedSize(18, 18)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(9, 9)
        if self.on:
            _tick(p, c, 8)
        else:
            p.setPen(QPen(QColor(T.BORDER_STRONG), 1.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, 7.5, 7.5)
        p.end()
