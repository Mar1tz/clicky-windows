"""The website's five "What it does" scenes, in Qt, for the welcome screen.

A port of heytora-web/src/components/home/tour-scenes.tsx: the same five
scenes (Ask, Show me, Teach me, Do it, Build it), the same words
(heytora-web lib/site.ts tourScenes), the same colours, beats and
coordinates on the same 560 x 350 canvas, scaled to fit. The site's rules
carry over: the first frame is already full, moves are slow with one thing
at a time, and windows stay sparse.

What does not carry over is other companies' marks: where the site draws
the Sheets, Spotify, Notion and Chrome logos, these draw plain glyphs.

Pieces, as on the site:
    Clock      steps through a scene's beats (name, milliseconds)
    Values     floats that tween to a target on one of the site's curves
    the pill   rests as a small capsule, opens to listen (typing what you
               said, a waveform) or to reply (typing her answer)
    Backdrop   a band of shapes drifting behind the windows, one per scene
    Cursor     the visitor's own pointer: you click, Tora only points
"""

from __future__ import annotations

import math
import time
from typing import Callable, Optional

from PyQt6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
    QRadialGradient,
)
from PyQt6.QtWidgets import QSizePolicy, QWidget

from ui import tokens

W, H = 560.0, 350.0
# The scenes are drawn content: paper greys, light in both themes (tokens §Paper).
INK = tokens.PAPER.ink
SOFT = tokens.PAPER.dim
LINE = tokens.PAPER.line
FAINT = tokens.PAPER.ground
WHITE = tokens.PAPER.card
ON = tokens.ON_COLOUR
TYPE_MS = 50

# The site's three curves.
EASE = (0.22, 1.0, 0.36, 1.0)          # arrivals: a long, soft settle
GLIDE = (0.65, 0.0, 0.35, 1.0)         # things that travel
ISLAND = (0.32, 0.72, 0.0, 1.0)        # the pill opening and closing
LINEAR = None

# Per-scene colours (what.tsx LOOK): tile, pane, deep.
LOOKS = tokens.SCENE_LOOKS

# The apps the scenes picture wear their own colours - a music player's
# green, a spreadsheet's green, a PDF's red, album covers, a florist's site.
# A depicted application is not a surface of ours (heytora-web BRAND.md §2):
# its colours stop at its window's edge, and they live here and nowhere else.
DEPICTED = {
    "sheet": "#188038",
    "pdf": "#E5252A",
    "music": "#1DB954",
    "bluetooth": "#0A84FF",
    "browser": "#4285F4",
    "covers": (("#F29BD8", "#F5C451"), ("#6A55E8", "#00B6FD")),
    "suns": ("#FFE7A8", "#FFF1F6", "#E6F4FF"),
    "hero": ("#FFE9EF", "#FFE2C2"),
    "blooms": (("#FFB3C7", "#F06A93"), ("#FFE08A", "#F5B731")),
    "tiles": ("#FDE2EA", "#FFF0D1", "#E8F5E9"),
}


def typing(text: str, start: int = 0, rest: int = 1000) -> int:
    """How long a beat needs for `text` to type itself out, plus a moment."""
    return (len(text) - start) * TYPE_MS + rest


_curves: dict = {}


def _ease(curve, p: float) -> float:
    if curve is None:
        return p
    c = _curves.get(curve)
    if c is None:
        c = QEasingCurve(QEasingCurve.Type.BezierSpline)
        c.addCubicBezierSegment(QPointF(curve[0], curve[1]), QPointF(curve[2], curve[3]),
                                QPointF(1.0, 1.0))
        _curves[curve] = c
    return c.valueForProgress(p)


def _now() -> float:
    return time.monotonic()


class Values:
    """Named floats that tween to targets, read at paint time."""

    def __init__(self):
        self._v: dict[str, tuple] = {}

    def get(self, key: str, default: float = 0.0) -> float:
        v = self._v.get(key)
        if v is None:
            return default
        a, b, t0, dur, curve = v
        t = _now()
        if t <= t0:
            return a
        if dur <= 0:
            return b
        p = min(1.0, (t - t0) / dur)
        return a + (b - a) * _ease(curve, p)

    def set(self, key: str, to: float, ms: int = 0, curve=EASE, delay: int = 0) -> None:
        cur = self.get(key, to)
        self._v[key] = (cur, float(to), _now() + delay / 1000.0, ms / 1000.0, curve)

    def snap(self, key: str, to: float) -> None:
        self._v[key] = (float(to), float(to), 0.0, 0.0, None)

    def clear(self) -> None:
        self._v.clear()


class Clock:
    """Steps through (beat, ms) pairs; `done` once the last has run out."""

    def __init__(self, beats):
        self.beats = beats
        self.names = [b[0] for b in beats]
        self.reset()

    def reset(self) -> None:
        self.i = 0
        self.t0 = _now()
        self.done = False

    @property
    def beat(self) -> str:
        return self.names[self.i]

    def since(self) -> float:
        return _now() - self.t0

    def tick(self) -> Optional[str]:
        """The new beat's name when one starts, else None."""
        if self.done or self.since() * 1000 < self.beats[self.i][1]:
            return None
        if self.i + 1 >= len(self.beats):
            self.done = True
            return None
        self.i += 1
        self.t0 = _now()
        return self.beat

    def is_(self, *names) -> bool:
        return self.beat in names

    def after(self, name: str) -> bool:
        return self.i >= self.names.index(name)


# ── drawing helpers ───────────────────────────────────────────────────────────

def font(px: float, weight: int = 400, italic: bool = False, serif: bool = False) -> QFont:
    from ui import kit
    return kit.font(px, weight, italic=italic, family="Georgia" if serif else "")


def text(p: QPainter, x: float, y: float, words: str, px: float, weight: int = 400,
         colour: str = INK, w: float = 400, align=Qt.AlignmentFlag.AlignLeft, alpha: float = 1.0,
         italic: bool = False, serif: bool = False) -> None:
    """Words with their top-left at (x, y)."""
    c = QColor(colour)
    c.setAlphaF(max(0.0, min(1.0, alpha)) * c.alphaF())
    p.setPen(c)
    p.setFont(font(px, weight, italic, serif))
    lines = words.count("\n") + 1
    p.drawText(QRectF(x, y, w, px * 1.5 * lines), align | Qt.AlignmentFlag.AlignTop, words)


def rrect(p: QPainter, r: QRectF, radius: float, fill, edge: Optional[str] = None,
          alpha: float = 1.0) -> None:
    c = QColor(fill) if isinstance(fill, str) else QColor(fill)
    c.setAlphaF(c.alphaF() * alpha)
    p.setBrush(c)
    if edge:
        e = QColor(edge)
        e.setAlphaF(e.alphaF() * alpha)
        p.setPen(QPen(e, 1))
    else:
        p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(r, radius, radius)


def card(p: QPainter, r: QRectF, alpha: float = 1.0) -> None:
    """A plain white window: soft-edged, no chrome (the site's Card)."""
    from ui import kit
    kit.soft_shadow(p, r, tokens.RADIUS_LG, "float", alpha)
    rrect(p, r, tokens.RADIUS_LG, WHITE, alpha=alpha)


def partial(p: QPainter, path: QPainterPath, t: float, pen: QPen) -> None:
    """Draw the first `t` of a path, as an SVG pathLength animation does."""
    if t <= 0.001:
        return
    n = 48
    sub = QPainterPath(path.pointAtPercent(0.0))
    for k in range(1, int(n * min(1.0, t)) + 1):
        sub.lineTo(path.pointAtPercent(min(1.0, k / n)))
    sub.lineTo(path.pointAtPercent(min(1.0, t)))
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(sub)


def tick_mark(p: QPainter, c: QPointF, size: float, colour: str = ON, t: float = 1.0) -> None:
    path = QPainterPath(QPointF(c.x() - size * 0.29, c.y() + size * 0.02))
    path.lineTo(c.x() - size * 0.10, c.y() + size * 0.21)
    path.lineTo(c.x() + size * 0.29, c.y() - size * 0.21)
    pen = QPen(QColor(colour), size * 0.16)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    partial(p, path, t, pen)


def spinner(p: QPainter, c: QPointF, colour: str) -> None:
    r = QRectF(c.x() - 7, c.y() - 7, 14, 14)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(LINE), 1.5))
    p.drawEllipse(r)
    pen = QPen(QColor(colour), 1.5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    start = int(-(_now() * 330) % 360 * 16)
    p.drawArc(r, start, 90 * 16)


def glyph_tile(p: QPainter, r: QRectF, fill: str, mark: str = "") -> None:
    """A plain app glyph where the site draws a brand's logo."""
    rrect(p, r, r.width() * 0.24, fill)
    if mark:
        text(p, r.left(), r.top() + r.height() * 0.16, mark, r.height() * 0.5, 700, ON,
             r.width(), Qt.AlignmentFlag.AlignHCenter)


def header(p: QPainter, x: float, y: float, title: str, fill: str, mark: str = "") -> None:
    glyph_tile(p, QRectF(x, y, 20, 20), fill, mark)
    text(p, x + 30, y + 0.5, title, 14, 600)


# ── the scene machinery ───────────────────────────────────────────────────────

class Scene:
    """One capability's scene. Subclasses give BEATS, PATTERN, enter() and
    the pages they draw."""

    BEATS: tuple = ()
    PATTERN = "capsules"
    FROM = 0

    def __init__(self, look):
        self.tile, self.pane, self.deep = look
        self.clock = Clock(self.BEATS)
        self.v = Values()
        self.reset()

    # the clock
    def reset(self) -> None:
        self.v.clear()
        self.page = 0
        self.prev_page: Optional[int] = None
        self.v.snap("pin", 1.0)
        self.v.snap("pout", 1.0)
        self._island = ("rest", "", 0, TYPE_MS, _now())
        self.v.snap("iw", 116)
        self.v.snap("ih", 32)
        self.v.snap("iopen", 0)
        self.lively = False
        self.clock.reset()
        self.start()
        self.enter(self.clock.beat)

    def start(self) -> None:
        """Snap the scene's own values to their first frame."""

    def advance(self) -> None:
        beat = self.clock.tick()
        if beat is not None:
            self.enter(beat)

    @property
    def done(self) -> bool:
        return self.clock.done

    def enter(self, beat: str) -> None:            # pragma: no cover - per scene
        pass

    # pages: the old one slides out left, the new in from the right
    def page_to(self, n: int) -> None:
        if n == self.page:
            return
        self.prev_page, self.page = self.page, n
        self.v.snap("pout", 0.0)
        self.v.set("pout", 1.0, 700, GLIDE)
        self.v.snap("pin", 0.0)
        self.v.set("pin", 1.0, 1100, GLIDE, delay=350)

    # the pill
    def island(self, mode: str, words: str = "", start: int = 0, speed: int = 0) -> None:
        self._island = (mode, words, start if mode == "listen" else 0,
                        speed or (TYPE_MS if mode == "listen" else 18), _now())
        open_ = mode != "rest"
        self.v.set("iw", 392 if open_ else 116, 850, ISLAND)
        self.v.set("ih", 46 if open_ else 32, 850, ISLAND)
        self.v.set("iopen", 1 if open_ else 0, 350, EASE, delay=300 if open_ else 0)
        self.lively = mode == "listen"

    # painting
    def paint(self, p: QPainter) -> None:
        draw_backdrop(p, self.PATTERN, self.tile, self.lively)
        if self.prev_page is not None and self.v.get("pout") < 1.0:
            t = self.v.get("pout")
            self._page(p, self.prev_page, -72 * t, 1 - t, 1 - 0.02 * t)
        t = self.v.get("pin")
        if t > 0.0:
            self._page(p, self.page, 72 * (1 - t), t, 0.98 + 0.02 * t)
        self.paint_island(p)

    def _page(self, p: QPainter, n: int, dx: float, alpha: float, scale: float) -> None:
        p.save()
        p.translate(W / 2 + dx, H / 2)
        p.scale(scale, scale)
        p.translate(-W / 2, -H / 2)
        p.setOpacity(max(0.0, min(1.0, alpha)))
        self.draw_page(p, n)
        p.restore()

    def draw_page(self, p: QPainter, n: int) -> None:   # pragma: no cover - per scene
        pass

    def paint_island(self, p: QPainter) -> None:
        mode, words, start, speed, t0 = self._island
        w, h = self.v.get("iw", 116), self.v.get("ih", 32)
        r = QRectF(W / 2 - w / 2, 14, w, h)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(6):
            g = 2 + i * 3.0
            p.setBrush(QColor(0, 0, 0, 7))
            p.drawRoundedRect(r.adjusted(-g * 0.4, g * 0.3 + 3, g * 0.4, g * 0.6 + 3), h / 2 + g, h / 2 + g)
        rrect(p, r, h / 2, WHITE)
        op = self.v.get("iopen")
        dot = QColor(tokens.BLUE)
        if op < 0.99:
            p.setBrush(dot)
            p.setOpacity(1 - op)
            p.drawEllipse(QPointF(W / 2, r.center().y()), 4, 4)
            p.setOpacity(1.0)
        if op > 0.01:
            p.save()
            clip = QPainterPath()
            clip.addRoundedRect(r, h / 2, h / 2)
            p.setClipPath(clip)
            p.setOpacity(op)
            left = W / 2 - 392 / 2 + 20
            pulse = 0.5 + 0.5 * math.sin(_now() * 3.4) if mode == "listen" else 1.0
            dot.setAlphaF(0.55 + 0.45 * pulse)
            p.setBrush(dot)
            p.drawEllipse(QPointF(left + 4, r.center().y()), 4, 4)
            shown = start + int((_now() - t0) * 1000 / max(1, speed))
            typed = words[:max(0, min(len(words), shown))]
            text(p, left + 20, r.center().y() - 10, typed, 13.5,
                 400 if mode == "listen" else 500, SOFT if mode == "listen" else INK, 300)
            if mode == "listen":
                bars = (0.4, 0.75, 0.55, 1.0, 0.65, 0.85, 0.35)
                bx = W / 2 + 392 / 2 - 20 - 40
                p.setBrush(QColor(INK))
                for i, hh in enumerate(bars):
                    k = hh * (0.65 + 0.35 * math.sin(_now() * (7.4 - i * 0.4) + i))
                    bh = 16 * max(0.18, k)
                    p.drawRoundedRect(QRectF(bx + i * 6 + 1, r.center().y() - bh / 2, 3, bh), 1.5, 1.5)
            p.restore()


PULSE = (62, 80, 70, 96, 78, 88, 66, 92, 74, 86, 68, 94, 76, 84, 64, 90)
BAND_Y = 196.0


def draw_backdrop(p: QPainter, kind: str, colour: str, lively: bool) -> None:
    """The scene's band of shapes, drifting behind the windows."""
    c = QColor(colour)
    p.setPen(Qt.PenStyle.NoPen)
    t = _now()
    if kind == "pulse":
        hgt = 140.0
        beat = 0.5 + 0.5 * math.sin(t * 2 * math.pi / 0.86)
        lift = 1.08 if lively else 1.0
        n = len(PULSE)
        gap = (W - 16 - n * 22) / (n - 1)
        for i, hh in enumerate(PULSE):
            bh = hgt * hh / 100 * (0.72 + 0.28 * beat) * lift
            c.setAlphaF(0.42)
            p.setBrush(c)
            x = 8 + i * (22 + gap)
            p.drawRoundedRect(QRectF(x, BAND_Y - bh / 2, 22, bh), 11, 11)
        return
    spec = {"capsules": (62, 108, 30), "lines": (14, 150, 34),
            "circles": (72, 110, 32), "blocks": (40, 110, 30)}[kind]
    period, height, seconds = spec
    off = (t * W / seconds) % (period * 2)
    top = BAND_Y - height / 2
    i0 = -2
    x = -period * 2 + off
    i = i0
    while x < W + period:
        if kind == "capsules":
            c.setAlphaF(0.42)
            p.setBrush(c)
            p.drawRoundedRect(QRectF(x + 8, top, 46, height), 23, 23)
        elif kind == "lines":
            c.setAlphaF((0.62, 0.48, 0.34, 0.48)[i % 4])
            p.setBrush(c)
            p.drawRoundedRect(QRectF(x + 5, top, 3, height), 1.5, 1.5)
        elif kind == "circles":
            c.setAlphaF(0.28 if i % 2 else 0.46)
            p.setBrush(c)
            p.drawEllipse(QRectF(x + 9, top + height / 2 - 27, 54, 54))
        else:   # blocks
            for y in ((40,) if i % 2 else (0, 80)):
                c.setAlphaF(0.3 if y == 40 else 0.46)
                p.setBrush(c)
                p.drawRoundedRect(QRectF(x + 5, top + y, 30, 30), 9, 9)
        x += period
        i += 1


def draw_cursor(p: QPainter, x: float, y: float, scale: float = 1.0) -> None:
    """The visitor's own pointer (the site's Cursor): white, ink-edged."""
    p.save()
    p.translate(x, y)
    p.scale(scale * 0.9, scale * 0.9)
    path = QPainterPath(QPointF(2, 2))
    for px, py in ((2, 19), (6.7, 14.5), (9.9, 21.5), (13.3, 19.9), (10.1, 13.4), (16.5, 13.2)):
        path.lineTo(px, py)
    path.closeSubpath()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, 50))
    p.drawPath(path.translated(0, 2))
    p.setBrush(QColor(WHITE))
    pen = QPen(QColor(INK), 1.3)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.drawPath(path)
    p.restore()


# ── 1 · Ask ───────────────────────────────────────────────────────────────────

class AskScene(Scene):
    SPOKEN = "why won't this total add up?"
    PSPOKEN = "what does page 3 say about leaving early?"
    FROM = 9
    BEATS = (("listen", typing(SPOKEN, 9)), ("answer", 3200), ("fix", 3200),
             ("pdf", 1500), ("plisten", typing(PSPOKEN, 0, 900)), ("panswer", 3800))
    PATTERN = "capsules"
    ROWS = (("Flights", "£420"), ("Hotel", "£615"), ("Car hire", "£180"))

    def start(self) -> None:
        for k in ("ring", "fix", "roll", "tint", "hl0", "hl1", "grey"):
            self.v.snap(k, 0.0)

    def enter(self, beat: str) -> None:
        if beat == "listen":
            self.island("listen", self.SPOKEN, self.FROM)
        elif beat == "answer":
            self.island("reply", "Car hire is typed as text, so it's skipped.")
            self.v.set("ring", 1, 800)
        elif beat == "fix":
            self.island("reply", "Fixed. It's £1,215 now.")
            self.v.set("fix", 1, 1100, GLIDE)
            self.v.set("roll", 1, 800, GLIDE, delay=500)
            self.v.set("tint", 1, 1000, EASE, delay=500)
        elif beat == "pdf":
            self.island("rest")
            self.v.set("ring", 0, 400)
            self.page_to(1)
        elif beat == "plisten":
            self.island("listen", self.PSPOKEN)
        elif beat == "panswer":
            self.island("reply", "Two months' notice, in writing.")
            self.v.set("hl0", 1, 900, GLIDE, delay=400)
            self.v.set("hl1", 1, 900, GLIDE, delay=900)
            self.v.set("grey", 1, 800, EASE, delay=600)

    def draw_page(self, p: QPainter, n: int) -> None:
        if n == 0:
            r = QRectF(120, 80, 320, 226)
            card(p, r)
            header(p, r.left() + 24, r.top() + 24, "Trip budget", DEPICTED["sheet"], "▦")
            y = r.top() + 60
            for k, (item, cost) in enumerate(self.ROWS):
                text(p, r.left() + 24, y + 9, item, 13.5)
                cell = QRectF(r.right() - 24 - 120, y, 120, 36)
                if k == 2:
                    ring = self.v.get("ring")
                    if ring > 0.01:
                        s = 1.1 - 0.1 * ring
                        cr = cell.adjusted(3, 3, -3, -3)
                        cr = QRectF(cr.center().x() - cr.width() * s / 2, cr.center().y() - cr.height() * s / 2,
                                    cr.width() * s, cr.height() * s)
                        halo = QColor(self.tile)
                        halo.setAlphaF(0.25 * ring)
                        p.setPen(QPen(halo, 8))
                        p.setBrush(Qt.BrushStyle.NoBrush)
                        p.drawRoundedRect(cr, 8, 8)
                        d = QColor(self.deep)
                        d.setAlphaF(ring)
                        p.setPen(QPen(d, 2))
                        p.drawRoundedRect(cr, 8, 8)
                    shift = -(120 - 28 - 44) * (1 - self.v.get("fix"))
                    text(p, cell.right() - 14 - 44 + shift, y + 9, cost, 13.5, w=44,
                         align=Qt.AlignmentFlag.AlignRight)
                else:
                    text(p, cell.right() - 14 - 60, y + 9, cost, 13.5, w=60, align=Qt.AlignmentFlag.AlignRight)
                p.setPen(QPen(QColor(FAINT), 1))
                p.drawLine(QPointF(r.left() + 24, y + 36), QPointF(r.right() - 24, y + 36))
                y += 36
            tot = QRectF(r.left() + 16, y + 8, r.width() - 32, 38)
            tint = QColor(self.tile)
            tint.setAlphaF(0.18 * self.v.get("tint"))
            rrect(p, tot, 10, tint)
            text(p, tot.left() + 8, tot.top() + 10, "Total", 13.5, 600)
            roll = self.v.get("roll")
            p.save()
            p.setClipRect(QRectF(tot.right() - 130, tot.top() + 9, 120, 20))
            text(p, tot.right() - 14 - 90, tot.top() + 9 - 20 * roll, "£1,035", 13.5, 600, w=90,
                 align=Qt.AlignmentFlag.AlignRight, alpha=1 - roll)
            text(p, tot.right() - 14 - 90, tot.top() + 9 + 20 * (1 - roll), "£1,215", 13.5, 600, w=90,
                 align=Qt.AlignmentFlag.AlignRight, alpha=roll)
            p.restore()
        else:
            r = QRectF(140, 74, 280, 256)
            card(p, r)
            rrect(p, QRectF(r.left() + 24, r.top() + 24, 26, 20), 4, DEPICTED["pdf"])
            text(p, r.left() + 24, r.top() + 28.5, "PDF", 8.5, 700, ON, 26, Qt.AlignmentFlag.AlignHCenter)
            text(p, r.left() + 60, r.top() + 24.5, "Flat lease.pdf", 14, 600)
            text(p, r.left() + 24, r.top() + 48, "Page 3 of 12", 11, 400, SOFT)
            text(p, r.left() + 24, r.top() + 74, "4. Ending the lease early", 13, 600)
            y = r.top() + 100
            for i, frac in enumerate((100, 94, 97, 62, 0, 100, 91, 96, 70)):
                if frac == 0:
                    y += 10
                    continue
                wl = (r.width() - 48) * frac / 100
                if i in (5, 6):
                    hl = self.v.get("hl0" if i == 5 else "hl1")
                    if hl > 0.01:
                        c = QColor(self.tile)
                        c.setAlphaF(0.4)
                        rrect(p, QRectF(r.left() + 18, y - 5, (wl + 12) * hl, 17), 4, c)
                grey = self.v.get("grey") if i in (5, 6) else 0
                col = QColor(LINE)
                if grey:
                    from ui import kit
                    col = kit.mix(LINE, tokens.PAPER.faint, grey)
                rrect(p, QRectF(r.left() + 24, y, wl, 7), 3.5, col)
                y += 16


# ── 2 · Show me ───────────────────────────────────────────────────────────────

class ShowScene(Scene):
    SPOKEN = "how do I export this?"
    FROM = 7
    BEATS = (("listen", typing(SPOKEN, 7, 800)), ("s1", 1500), ("c1", 1000), ("k1", 500),
             ("s2", 1300), ("c2", 1000), ("k2", 500), ("s3", 1300), ("c3", 1000), ("k3", 1700),
             ("done", 2600))
    PATTERN = "lines"
    TARGETS = ((6, 7, 48, 24, 30, 20), (10, 114, 152, 34, 64, 131), (226, 164, 90, 38, 272, 184))
    REST = (360, 222)
    TIPS = ("Open File", "Choose Export…", "Press Export")
    REPLIES = ("First, open the File menu.", "Now choose Export.", "Last one: press Export.")

    def start(self) -> None:
        tx, ty, tw, th, _cx, _cy = self.TARGETS[0]
        for k, v in (("rx", tx), ("ry", ty), ("rw", tw), ("rh", th), ("ring", 0), ("tip", 0),
                     ("menu", 0), ("dialog", 0), ("exp", 0), ("cx", self.REST[0]), ("cy", self.REST[1]),
                     ("press", 0)):
            self.v.snap(k, v)
        self.step = 0
        self.exported = False

    def _aim(self, k: int) -> None:
        tx, ty, tw, th, _cx, _cy = self.TARGETS[k]
        for key, val in (("rx", tx), ("ry", ty), ("rw", tw), ("rh", th)):
            self.v.set(key, val, 850, GLIDE)

    def _cursor_to(self, k: int) -> None:
        _tx, _ty, _tw, _th, cx, cy = self.TARGETS[k]
        self.v.set("cx", cx, 900, GLIDE)
        self.v.set("cy", cy - 2, 900, GLIDE)

    def _press(self) -> None:
        self.v.snap("press", 0)
        self.v.set("press", 1, 360, None)

    def enter(self, beat: str) -> None:
        if beat == "listen":
            self.island("listen", self.SPOKEN, self.FROM)
            return
        if beat[0] in "sck" and beat[1:].isdigit():
            k = int(beat[1]) - 1
            self.step = k
            self.island("reply", self.REPLIES[k])
            if beat[0] == "s":
                self._aim(k)
                self.v.set("ring", 1, 450)
                self.v.set("tip", 1, 400)
            elif beat[0] == "c":
                self._cursor_to(k)
            else:
                self._press()
                self.v.set("tip", 0, 300)
                if k == 0:
                    self.v.set("menu", 1, 450)
                elif k == 1:
                    self.v.set("menu", 0, 300)
                    self.v.set("dialog", 1, 600)
                else:
                    self.v.set("exp", 1, 1300, None, delay=300)
        elif beat == "done":
            self.exported = True
            self.v.set("ring", 0, 450)
            self.island("reply", "Done. It's in your Videos folder.")

    def draw_page(self, p: QPainter, n: int) -> None:
        win = QRectF(62, 72, 436, 258)
        p.save()
        p.translate(win.center())
        p.scale(0.9, 0.9)
        p.translate(-win.width() / 2, -win.height() / 2)
        local = QRectF(0, 0, win.width(), win.height())
        card(p, local)
        # menu bar
        for i, m in enumerate(("File", "Edit", "View", "Help")):
            text(p, 8 + i * 44, 11, m, 12, 400, INK, 44, Qt.AlignmentFlag.AlignHCenter)
        text(p, 180, 12, "Holiday edit", 11, 400, SOFT, 120, Qt.AlignmentFlag.AlignHCenter)
        rrect(p, QRectF(local.right() - 64, 9, 54, 22), 11, FAINT)
        text(p, local.right() - 64, 13, "Share", 11, 500, INK, 54, Qt.AlignmentFlag.AlignHCenter)
        p.setPen(QPen(QColor(LINE), 1))
        p.drawLine(QPointF(0, 38), QPointF(local.width(), 38))
        # preview and timeline
        prev = QRectF(16, 50, 404, 124)
        g = QLinearGradient(prev.topLeft(), prev.bottomRight())
        g.setColorAt(0, QColor(self.pane))
        g.setColorAt(1, QColor(self.tile))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawRoundedRect(prev, 10, 10)
        p.setBrush(QColor(255, 255, 255, 217))
        p.drawEllipse(prev.center(), 20, 20)
        tri = QPainterPath(prev.center() + QPointF(-4, -6))
        tri.lineTo(prev.center() + QPointF(7, 0))
        tri.lineTo(prev.center() + QPointF(-4, 6))
        tri.closeSubpath()
        p.setBrush(QColor(INK))
        p.drawPath(tri)
        for row, widths in enumerate(((112, 80, 140, 54), (66, 168, 90))):
            x = 16.0
            for k, wd in enumerate(widths):
                c = QColor(self.tile)
                c.setAlphaF((0.55 - k * 0.08) if row == 0 else 0.28)
                rrect(p, QRectF(x, 188 + row * 26, wd, 18), 5, c)
                x += wd + 6
        rrect(p, QRectF(168, 182, 2, 56), 1, self.deep)
        # file menu
        m = self.v.get("menu")
        if m > 0.01:
            p.setOpacity(m)
            mr = QRectF(8, 34 + (1 - m) * -4, 156, 4 * 26 + 12)
            card(p, mr, 0.9)
            for i, item in enumerate(("New project", "Open…", "Save", "Export…")):
                text(p, mr.left() + 16, mr.top() + 6 + i * 26 + 5, item, 12)
            p.setOpacity(1.0)
        # export dialog
        d = self.v.get("dialog")
        if d > 0.01:
            p.setOpacity(d)
            rrect(p, local, tokens.RADIUS_LG, QColor(0, 0, 0, 18))
            dr = QRectF(108, 58 + 8 * (1 - d), 220, 156)
            card(p, dr)
            text(p, dr.left() + 16, dr.top() + 16, "Export video", 13, 600)
            for i, (k, val) in enumerate((("Format", "MP4"), ("Quality", "1080p"))):
                y = dr.top() + 46 + i * 28
                text(p, dr.left() + 16, y, k, 11.5, 400, SOFT)
                rrect(p, QRectF(dr.right() - 66, y - 2, 50, 20), 6, FAINT)
                text(p, dr.right() - 66, y + 1, val, 11.5, 400, INK, 50, Qt.AlignmentFlag.AlignHCenter)
            btn = QRectF(dr.right() - 16 - 82, dr.bottom() - 16 - 30, 82, 30)
            rrect(p, btn, 8, INK)
            e = self.v.get("exp")
            if e > 0.01:
                rrect(p, QRectF(btn.left(), btn.top(), btn.width() * e, btn.height()), 8, self.deep)
            text(p, btn.left(), btn.top() + 7, "Exported" if self.exported else "Export", 12, 500,
                 WHITE, btn.width(), Qt.AlignmentFlag.AlignHCenter)
            p.setOpacity(1.0)
        # Tora's mark: the ring that travels, and the step it is on
        ring = self.v.get("ring")
        rr = QRectF(self.v.get("rx"), self.v.get("ry"), self.v.get("rw"), self.v.get("rh"))
        if ring > 0.01:
            halo = QColor(self.tile)
            halo.setAlphaF(0.27 * ring)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(halo, 10))
            p.drawRoundedRect(rr, 9, 9)
            c = QColor(self.deep)
            c.setAlphaF(ring)
            p.setPen(QPen(c, 2))
            p.drawRoundedRect(rr, 9, 9)
        tip = self.v.get("tip")
        if tip > 0.01:
            p.setOpacity(tip)
            words = self.TIPS[self.step]
            p.setFont(font(11.5))
            tw = p.fontMetrics().horizontalAdvance(words) + 38
            tr = QRectF(rr.left(), rr.bottom() + 8, tw, 26)
            rrect(p, tr, 13, INK)
            p.setBrush(QColor(self.tile))
            p.drawEllipse(QPointF(tr.left() + 13, tr.center().y()), 9, 9)
            text(p, tr.left() + 4, tr.top() + 6, str(self.step + 1), 10.5, 600, INK, 18, Qt.AlignmentFlag.AlignHCenter)
            text(p, tr.left() + 28, tr.top() + 5.5, words, 11.5, 400, ON, tw)
            p.setOpacity(1.0)
        press = self.v.get("press")
        s = 1 - 0.18 * math.sin(math.pi * press) if press < 1 else 1.0
        draw_cursor(p, self.v.get("cx"), self.v.get("cy"), s)
        p.restore()


# ── 3 · Teach me ──────────────────────────────────────────────────────────────

class TeachScene(Scene):
    SPOKEN = "why is c always the longest side?"
    FROM = 11
    BEATS = (("listen", typing(SPOKEN, 11)), ("circle", 1700), ("mark", 2300), ("hold", 1600),
             ("journal", 900), ("saved", 3800))
    PATTERN = "circles"

    def start(self) -> None:
        for k in ("circ", "note1", "hyp", "arrow", "head", "note2", "bg", "ring", "tick", "words"):
            self.v.snap(k, 0.0)

    def enter(self, beat: str) -> None:
        if beat == "listen":
            self.island("listen", self.SPOKEN, self.FROM)
        elif beat == "circle":
            self.island("reply", "The biggest angle faces the longest side.")
            self.v.set("circ", 1, 1100, GLIDE)
            self.v.set("note1", 1, 500, EASE, delay=900)
        elif beat == "mark":
            self.v.set("hyp", 1, 1100, GLIDE)
            self.v.set("arrow", 1, 1100, GLIDE, delay=600)
            self.v.set("head", 1, 250, EASE, delay=1600)
            self.v.set("note2", 1, 500, EASE, delay=1000)
        elif beat == "journal":
            self.island("rest")
            self.page_to(1)
        elif beat == "saved":
            self.island("reply", "Saved to your journal.")
            self.v.set("bg", 1, 700)
            self.v.set("ring", 1, 900, GLIDE, delay=150)
            self.v.set("tick", 1, 550, GLIDE, delay=900)
            self.v.set("words", 1, 700, EASE, delay=1100)

    def draw_page(self, p: QPainter, n: int) -> None:
        if n == 0:
            r = QRectF(70, 70, 420, 254)
            card(p, r)
            text(p, r.left() + 20, r.top() + 20, "Maths homework", 14, 600)
            text(p, r.left() + 20, r.top() + 40, "Q4. Which side of the triangle is longest?", 12, 400, SOFT)
            p.save()
            p.translate(r.topLeft())
            A, B, C = QPointF(70, 214), QPointF(70, 92), QPointF(290, 214)
            tri = QPainterPath(A)
            tri.lineTo(B)
            tri.lineTo(C)
            tri.closeSubpath()
            pen = QPen(QColor(INK), 1.6)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(QColor(FAINT))
            p.drawPath(tri)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(INK), 1.2))
            p.drawPolyline([QPointF(70, 200), QPointF(84, 200), QPointF(84, 214)])
            for x, y, s in ((52, 149, "a"), (232, 222, "b"), (186, 130, "c")):
                text(p, x, y, s, 13, 400, SOFT, italic=True)
            hyp = QPainterPath(QPointF(74, 87))
            hyp.lineTo(QPointF(294, 209))
            hp = QPen(QColor(self.tile), 11)
            hp.setCapStyle(Qt.PenCapStyle.RoundCap)
            c = QColor(self.tile)
            c.setAlphaF(0.55)
            hp.setColor(c)
            partial(p, hyp, self.v.get("hyp"), hp)
            circ = QPainterPath(QPointF(108, 196))
            circ.cubicTo(QPointF(106, 176), QPointF(70, 172), QPointF(58, 190))
            circ.cubicTo(QPointF(47, 207), QPointF(66, 230), QPointF(92, 225))
            circ.cubicTo(QPointF(110, 221), QPointF(114, 205), QPointF(103, 189))
            dp = QPen(QColor(self.deep), 2.6)
            dp.setCapStyle(Qt.PenCapStyle.RoundCap)
            partial(p, circ, self.v.get("circ"), dp)
            text(p, 116, 183, "biggest angle", 13, 600, self.deep, alpha=self.v.get("note1"))
            arrow = QPainterPath(QPointF(208, 191))
            arrow.cubicTo(QPointF(213, 189), QPointF(216, 186), QPointF(216, 181))
            ap = QPen(QColor(self.deep), 2.2)
            ap.setCapStyle(Qt.PenCapStyle.RoundCap)
            partial(p, arrow, self.v.get("arrow"), ap)
            hd = self.v.get("head")
            if hd > 0.01:
                c = QColor(self.deep)
                c.setAlphaF(hd)
                ap.setColor(c)
                ap.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                p.setPen(ap)
                p.drawPolyline([QPointF(211.5, 185), QPointF(216, 180), QPointF(220.5, 185.5)])
            text(p, 234, 111, "longest side", 13, 600, self.deep, alpha=self.v.get("note2"))
            p.restore()
        else:
            r = QRectF(140, 96, 280, 204)
            card(p, r)
            try:
                from ui.app_widgets import wordmark_pixmap
                px = wordmark_pixmap(15, ink=INK)
                p.drawPixmap(QPointF(r.left() + 20, r.top() + 20), px)
                lw = px.width() / px.devicePixelRatio()
            except Exception:  # noqa: BLE001
                text(p, r.left() + 20, r.top() + 18, "heytora", 15, 700)
                lw = 60
            text(p, r.left() + 28 + lw, r.top() + 20, "Journal", 12, 400, SOFT)
            cx, cy = r.center().x(), r.top() + 84
            bg = self.v.get("bg")
            if bg > 0.01:
                c = QColor(self.tile)
                c.setAlphaF(0.35 * bg)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(c)
                rad = 24 * (0.6 + 0.4 * bg)
                p.drawEllipse(QPointF(cx, cy), rad, rad)
            ring = QPainterPath()
            ring.arcMoveTo(QRectF(cx - 17, cy - 17, 34, 34), 90)
            ring.arcTo(QRectF(cx - 17, cy - 17, 34, 34), 90, -360)
            rp = QPen(QColor(self.deep), 2.4)
            rp.setCapStyle(Qt.PenCapStyle.RoundCap)
            partial(p, ring, self.v.get("ring"), rp)
            tick_mark(p, QPointF(cx, cy), 28, self.deep, self.v.get("tick"))
            wd = self.v.get("words")
            text(p, r.left(), cy + 36 + 6 * (1 - wd), "Added to your journal", 15, 600, INK, r.width(),
                 Qt.AlignmentFlag.AlignHCenter, wd)
            text(p, r.left(), cy + 60 + 6 * (1 - wd), "Ready for a quiz whenever you are.", 11.5, 400,
                 SOFT, r.width(), Qt.AlignmentFlag.AlignHCenter, wd)


# ── 4 · Do it ─────────────────────────────────────────────────────────────────

class DoScene(Scene):
    SPOKEN = "next song"
    SSPOKEN = "turn bluetooth on"
    BEATS = (("listen", typing(SPOKEN, 3, 900)), ("skip", 1200), ("reply", 2800),
             ("settings", 1500), ("slisten", typing(SSPOKEN, 0, 800)), ("toggle", 1600),
             ("sreply", 3400))
    PATTERN = "pulse"
    TRACKS = (("Low Orbit", "Ferro", "3:41"), ("Glass Harbour", "Wren", "4:02"),
              ("Second Light", "Calder", "3:18"))

    def start(self) -> None:
        for k in ("seq", "skipglow", "on", "conn"):
            self.v.snap(k, 0.0)
        self._t_track = _now()

    def enter(self, beat: str) -> None:
        if beat == "listen":
            self.island("listen", self.SPOKEN, 3)
        elif beat == "skip":
            self.v.set("seq", 1, 1000, GLIDE)
            self.v.set("skipglow", 1, 500)
            self._t_track = _now()
            self.island("reply", f"Playing {self.TRACKS[1][0]}.")
        elif beat == "reply":
            self.v.set("skipglow", 0, 500)
        elif beat == "settings":
            self.island("rest")
            self.page_to(1)
        elif beat == "slisten":
            self.island("listen", self.SSPOKEN)
        elif beat == "toggle":
            self.island("reply", "Bluetooth's on. Headphones connected.")
            self.v.set("on", 1, 700, GLIDE)
        elif beat == "sreply":
            self.v.set("conn", 1, 800, EASE, delay=600)

    def _cover(self, p: QPainter, r: QRectF, k: int) -> None:
        stops = ((self.deep, self.tile),) + DEPICTED["covers"]
        stops = stops[k % 3]
        suns = DEPICTED["suns"]
        sun = ((0.70, 0.72, suns[0], 0.18), (0.30, 0.34, suns[1], 0.15), (0.64, 0.40, suns[2], 0.17))[k % 3]
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        g.setColorAt(0, QColor(stops[0]))
        g.setColorAt(1, QColor(stops[1]))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawRect(r)
        p.setBrush(QColor(sun[2]))
        p.drawEllipse(QPointF(r.left() + r.width() * sun[0], r.top() + r.height() * sun[1]),
                      r.width() * sun[3], r.width() * sun[3])

    def draw_page(self, p: QPainter, n: int) -> None:
        if n == 0:
            r = QRectF(130, 80, 300, 238)
            card(p, r)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(DEPICTED["music"]))
            p.drawEllipse(QRectF(r.left() + 20, r.top() + 20, 20, 20))
            text(p, r.left() + 50, r.top() + 20.5, "Now playing", 14, 600)
            cover = QRectF(r.left() + 20, r.top() + 56, 84, 84)
            seq = self.v.get("seq")
            p.save()
            clip = QPainterPath()
            clip.addRoundedRect(cover, 12, 12)
            p.setClipPath(clip)
            for k in (0, 1):
                self._cover(p, cover.translated((k - seq) * 84, 0), k)
            p.restore()
            p.save()
            p.setClipRect(QRectF(cover.right() + 16, cover.top() + 21, 170, 42))
            for k in (0, 1):
                y = cover.top() + 21 + (k - seq) * 42
                title, artist, _t = self.TRACKS[k]
                alpha = 1 - abs(k - seq)
                text(p, cover.right() + 16, y, title, 15, 600, alpha=alpha)
                text(p, cover.right() + 16, y + 21, artist, 12, 400, SOFT, alpha=alpha)
            p.restore()
            bar = QRectF(r.left() + 20, cover.bottom() + 20, r.width() - 40, 4)
            rrect(p, bar, 2, FAINT)
            prog = 0.06 + 0.34 * min(1.0, (_now() - self._t_track) / 14)
            rrect(p, QRectF(bar.left(), bar.top(), bar.width() * prog, 4), 2, INK)
            text(p, bar.left(), bar.bottom() + 5, "0:12", 10, 400, SOFT)
            text(p, bar.right() - 40, bar.bottom() + 5, self.TRACKS[1 if seq > 0.5 else 0][2], 10, 400, SOFT,
                 40, Qt.AlignmentFlag.AlignRight)
            cy = bar.bottom() + 40
            cx = r.center().x()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(INK))
            p.drawEllipse(QPointF(cx, cy), 16, 16)
            p.setBrush(QColor(WHITE))
            p.drawRect(QRectF(cx - 5, cy - 6, 3, 12))
            p.drawRect(QRectF(cx + 2, cy - 6, 3, 12))
            glow = self.v.get("skipglow")
            if glow > 0.01:
                c = QColor(self.tile)
                c.setAlphaF(0.55 * glow)
                p.setBrush(c)
                p.drawEllipse(QPointF(cx + 52, cy), 15 * (0.5 + 0.5 * glow), 15 * (0.5 + 0.5 * glow))
            p.setBrush(QColor(INK))
            for sx, flip in ((cx - 52, -1), (cx + 52, 1)):
                tri = QPainterPath(QPointF(sx - 4 * flip, cy - 6))
                tri.lineTo(QPointF(sx + 3 * flip, cy))
                tri.lineTo(QPointF(sx - 4 * flip, cy + 6))
                tri.closeSubpath()
                p.drawPath(tri)
                p.drawRect(QRectF(sx + 3.5 * flip - (1 if flip > 0 else 0.6), cy - 6, 1.6, 12))
        else:
            r = QRectF(120, 80, 320, 232)
            card(p, r)
            p.setPen(QPen(QColor(SOFT), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(r.left() + 30, r.top() + 30), 3, 3)
            p.drawEllipse(QPointF(r.left() + 30, r.top() + 30), 7.5, 7.5)
            text(p, r.left() + 50, r.top() + 20.5, "Settings", 14, 600)
            text(p, r.left() + 20, r.top() + 54, "Bluetooth & devices", 17, 600)
            row = QRectF(r.left() + 20, r.top() + 92, r.width() - 40, 52)
            rrect(p, row, 12, FAINT)
            rrect(p, QRectF(row.left() + 14, row.top() + 13, 26, 26), 13, DEPICTED["bluetooth"])
            text(p, row.left() + 14, row.top() + 16, "ᛒ", 15, 700, ON, 26, Qt.AlignmentFlag.AlignHCenter)
            text(p, row.left() + 52, row.top() + 9, "Bluetooth", 13, 600)
            on = self.v.get("on")
            text(p, row.left() + 52, row.top() + 27, "Off", 11, 400, SOFT, alpha=1 - on)
            text(p, row.left() + 52, row.top() + 27, "On", 11, 400, SOFT, alpha=on)
            sw = QRectF(row.right() - 14 - 40, row.center().y() - 11, 40, 22)
            off_c, on_c = QColor(tokens.PAPER.edge), QColor(self.deep)
            mixc = QColor(int(off_c.red() + (on_c.red() - off_c.red()) * on),
                          int(off_c.green() + (on_c.green() - off_c.green()) * on),
                          int(off_c.blue() + (on_c.blue() - off_c.blue()) * on))
            rrect(p, sw, 11, mixc)
            p.setBrush(QColor(WHITE))
            p.drawEllipse(QRectF(sw.left() + 3 + 18 * on, sw.top() + 3, 16, 16))
            conn = self.v.get("conn")
            if conn > 0.01:
                y = row.bottom() + 10 + 8 * (1 - conn)
                p.setOpacity(conn)
                rrect(p, QRectF(row.left() + 14, y + 7, 26, 26), 13, FAINT)
                p.setPen(QPen(QColor(INK), 1.4))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawArc(QRectF(row.left() + 20, y + 13, 14, 12), 0, 180 * 16)
                text(p, row.left() + 52, y + 12, "Headphones", 13)
                text(p, row.right() - 90, y + 13, "Connected", 11.5, 500, self.deep, 76, Qt.AlignmentFlag.AlignRight)
                p.setOpacity(1.0)


# ── 5 · Build it ──────────────────────────────────────────────────────────────

class BuildScene(Scene):
    SPOKEN = "build me a website for the shop"
    ASPOKEN = "how's it going?"
    FROM = 10
    BEATS = (("listen", typing(SPOKEN, 10)), ("start", 3000), ("agent", 1700), ("w2", 1800),
             ("alisten", typing(ASPOKEN, 0, 800)), ("aanswer", 2600), ("w3", 1700), ("w4", 1700),
             ("site", 1800), ("siteHold", 3400))
    PATTERN = "blocks"
    DONE_AT = {"agent": 1, "w2": 2, "alisten": 2, "aanswer": 2, "w3": 3, "w4": 4}
    STEPS = ("Plan the pages", "Write the pages", "Test it", "Check its work")

    def start(self) -> None:
        for k in ("chip", "bar", "s0", "s1", "s2", "s3", "r0", "r1", "r2", "r3", "r4"):
            self.v.snap(k, 0.0)
        self.done_steps = 1

    def enter(self, beat: str) -> None:
        if beat == "listen":
            self.island("listen", self.SPOKEN, self.FROM)
        elif beat == "start":
            self.island("reply", "On it. I'll work in the background.")
            self.v.set("chip", 1, 800, EASE, delay=800)
        elif beat == "agent":
            self.island("rest")
            self.page_to(1)
        elif beat == "alisten":
            self.island("listen", self.ASPOKEN)
        elif beat == "aanswer":
            self.island("reply", "Testing now. Nearly there.")
        elif beat == "site":
            self.island("reply", "Your website is ready to preview.")
            self.page_to(2)
            for k, delay in (("r0", 400), ("r1", 650), ("r2", 900), ("r3", 1050), ("r4", 1200)):
                self.v.snap(k, 0)
                self.v.set(k, 1, 900, EASE, delay=delay + 350)
        if beat in self.DONE_AT:
            self.done_steps = self.DONE_AT[beat]
            self.v.set("bar", self.done_steps / 4, 1000, GLIDE)
            for k in range(4):
                self.v.set(f"s{k}", 1 if self.done_steps > k else 0, 600)

    def draw_page(self, p: QPainter, n: int) -> None:
        if n == 0:
            r = QRectF(120, 80, 320, 230)
            card(p, r)
            glyph_tile(p, QRectF(r.left() + 24, r.top() + 24, 22, 22), INK, "N")
            text(p, r.left() + 24, r.top() + 56, "Shop ideas", 20, 700)
            for i, line in enumerate(("Tulips up front, in the low crates", "Price cards in chalk, not printed",
                                      "Keep the counter clear from the street")):
                y = r.top() + 92 + i * 24
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(INK))
                p.drawEllipse(QPointF(r.left() + 26, y + 9), 2, 2)
                text(p, r.left() + 36, y, line, 13)
            chip = self.v.get("chip")
            if chip > 0.01:
                cr = QRectF(r.left() + 24, r.bottom() - 16 - 28 + 10 * (1 - chip), 210, 28)
                p.setOpacity(chip)
                c = QColor(self.tile)
                c.setAlphaF(0.22)
                rrect(p, cr, 14, c)
                spinner(p, QPointF(cr.left() + 16, cr.center().y()), self.deep)
                text(p, cr.left() + 30, cr.top() + 6, "Florist website", 11.5, 600)
                text(p, cr.left() + 128, cr.top() + 6, "Planning", 11.5, 400, SOFT)
                p.setOpacity(1.0)
        elif n == 1:
            r = QRectF(140, 80, 280, 226)
            card(p, r)
            text(p, r.left() + 20, r.top() + 20, "Florist website", 15, 600)
            text(p, r.left() + 20, r.top() + 41, "Working in the background", 11, 400, SOFT)
            for k, step in enumerate(self.STEPS):
                y = r.top() + 70 + k * 29
                c = QPointF(r.left() + 28, y + 14)
                done = self.v.get(f"s{k}")
                running = self.done_steps == k
                if running:
                    spinner(p, c, self.deep)
                elif done < 0.5:
                    p.setPen(QPen(QColor(LINE), 1.5))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawEllipse(c, 7, 7)
                if done > 0.01:
                    p.setPen(Qt.PenStyle.NoPen)
                    col = QColor(self.deep)
                    col.setAlphaF(done)
                    p.setBrush(col)
                    rad = 8 * (0.4 + 0.6 * done)
                    p.drawEllipse(c, rad, rad)
                    tick_mark(p, c, 13, ON, done)
                text(p, r.left() + 46, y + 5, step, 13, 400, INK if (done > 0.5 or running) else SOFT)
            bar = QRectF(r.left() + 20, r.top() + 70 + 4 * 29 + 12, r.width() - 40, 5)
            rrect(p, bar, 2.5, FAINT)
            rrect(p, QRectF(bar.left(), bar.top(), bar.width() * self.v.get("bar"), 5), 2.5, self.deep)
        else:
            r = QRectF(70, 72, 420, 256)
            card(p, r)
            top = QRectF(r.left(), r.top(), r.width(), 36)
            p.save()
            clip = QPainterPath()
            clip.addRoundedRect(r, 16, 16)
            p.setClipPath(clip)
            rrect(p, top, 0, FAINT)
            p.restore()
            p.setPen(QPen(QColor(LINE), 1))
            p.drawLine(QPointF(r.left(), top.bottom()), QPointF(r.right(), top.bottom()))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(DEPICTED["browser"]))
            p.drawEllipse(QRectF(r.left() + 14, top.top() + 10, 16, 16))
            url = QRectF(r.left() + 40, top.top() + 7, r.width() - 54, 22)
            rrect(p, url, 11, WHITE, LINE)
            text(p, url.left() + 12, url.top() + 4, "bloomandstem.shop", 11, 400, SOFT)

            def rise(k: str):
                v = self.v.get(k)
                return v, 10 * (1 - v)

            v, dy = rise("r0")
            text(p, r.left() + 16, top.bottom() + 12 + dy, "Bloom & Stem", 14, 600, alpha=v, serif=True)
            for i, nav in enumerate(("Bouquets", "Weddings", "Visit")):
                text(p, r.right() - 180 + i * 56, top.bottom() + 15 + dy, nav, 10.5, 400, SOFT, alpha=v)
            v, dy = rise("r1")
            hero = QRectF(r.left() + 16, top.bottom() + 40 + dy, r.width() - 32, 118)
            p.setOpacity(v)
            g = QLinearGradient(hero.topLeft(), hero.bottomRight())
            g.setColorAt(0, QColor(DEPICTED["hero"][0]))
            g.setColorAt(1, QColor(DEPICTED["hero"][1]))
            p.setBrush(QBrush(g))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(hero, 12, 12)
            text(p, hero.left() + 16, hero.top() + 14, "Fresh flowers,\ncut this morning.", 19, 600, w=200, serif=True)
            rrect(p, QRectF(hero.left() + 16, hero.top() + 72, 92, 24), 12, INK)
            text(p, hero.left() + 16, hero.top() + 77, "Shop bouquets", 10.5, 500, ON, 92, Qt.AlignmentFlag.AlignHCenter)
            for (fx, fy, rad, a, b) in ((hero.right() - 64, hero.bottom() - 47, 27) + DEPICTED["blooms"][0],
                                        (hero.right() - 116, hero.bottom() - 56, 20) + DEPICTED["blooms"][1],
                                        (hero.right() - 34, hero.bottom() - 74, 18, WHITE, self.tile)):
                rg = QRadialGradient(QPointF(fx - rad * 0.2, fy - rad * 0.2), rad)
                rg.setColorAt(0, QColor(a))
                rg.setColorAt(1, QColor(b))
                p.setBrush(QBrush(rg))
                p.drawEllipse(QPointF(fx, fy), rad, rad)
            p.setOpacity(1.0)
            cw = (r.width() - 32 - 20) / 3
            for i, col in enumerate(DEPICTED["tiles"]):
                v, dy = rise(f"r{2 + i}")
                rrect(p, QRectF(r.left() + 16 + i * (cw + 10), hero.bottom() + 10 + dy, cw, 38), 9, col, alpha=v)


SCENES = (("Ask", AskScene), ("Show me", ShowScene), ("Teach me", TeachScene),
          ("Do it", DoScene), ("Build it", BuildScene))


class TourCarousel(QWidget):
    """The five scenes, one after another: each plays through once, then
    the next fades in. The dots under it jump straight to a scene."""

    changed = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self._scenes = [cls(LOOKS[i]) for i, (_n, cls) in enumerate(SCENES)]
        self._index = 0
        self._fade = 1.0
        self._prev: Optional[int] = None
        self._fade_t0 = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self.setMinimumSize(440, 340)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def index(self) -> int:
        return self._index

    def scene(self) -> Scene:
        return self._scenes[self._index]

    def showEvent(self, e) -> None:
        self.scene().reset()
        self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e) -> None:
        self._timer.stop()
        super().hideEvent(e)

    def go(self, i: int) -> None:
        i %= len(self._scenes)
        if i == self._index:
            return
        self._prev, self._index = self._index, i
        self._fade_t0 = _now()
        self.scene().reset()
        self.changed.emit(i)

    def next(self) -> None:
        self.go(self._index + 1)

    def _tick(self) -> None:
        self.scene().advance()
        if self.scene().done:
            self.next()
        self.update()

    def _stage(self) -> QRectF:
        w = float(self.width()) - 24
        h = float(self.height()) - 64
        scale = min(w / W, h / H)
        sw, sh = W * scale, H * scale
        return QRectF((self.width() - sw) / 2, (h - sh) / 2 + 8, sw, sh)

    def _dot_rects(self) -> list[QRectF]:
        n = len(self._scenes)
        y = self._stage().bottom() + 46.0
        x = self.width() / 2 - (n * 22) / 2
        return [QRectF(x + i * 22, y - 8, 22, 16) for i in range(n)]

    def mouseReleaseEvent(self, e) -> None:
        for i, r in enumerate(self._dot_rects()):
            if r.contains(e.position()):
                self.go(i)
                return

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        st = self._stage()
        # the pane: the scene's own soft tint, rounded, under everything
        clip = QPainterPath()
        clip.addRoundedRect(st, 18, 18)
        p.save()
        p.setClipPath(clip)
        fade = min(1.0, (_now() - self._fade_t0) / 0.5) if self._prev is not None else 1.0
        for idx, alpha in ((self._prev, 1 - fade), (self._index, fade)):
            if idx is None or alpha <= 0.0:
                continue
            p.save()
            p.setOpacity(alpha)
            p.fillRect(st, QColor(LOOKS[idx][1]))
            p.translate(st.topLeft())
            p.scale(st.width() / W, st.height() / H)
            self._scenes[idx].paint(p)
            p.restore()
        p.restore()
        if fade >= 1.0:
            self._prev = None
        # the label and the dots
        name = SCENES[self._index][0]
        p.setFont(font(11, 600))
        f = p.font()
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.6)
        p.setFont(f)
        p.setPen(QColor(INK))
        p.drawText(QRectF(0, st.bottom() + 12, self.width(), 18), Qt.AlignmentFlag.AlignHCenter, name.upper())
        for i, d in enumerate(self._dot_rects()):
            on = i == self._index
            w = 18.0 if on else 6.0
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(LOOKS[i][2]) if on else QColor(0, 0, 0, 60))
            p.drawRoundedRect(QRectF(d.center().x() - w / 2, d.center().y() - 3, w, 6), 3, 3)
        p.end()
