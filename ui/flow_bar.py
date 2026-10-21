"""The flow bar: where Tora lives at the edge of the screen (after Wispr Flow).

A small black pill on a side of the screen - the bottom by default - that
shows what Tora is doing, and a card beside it with what she says:

    idle        a tiny pill; bigger under the mouse ("hold the shortcut")
    listening   white bars that move with the voice; hands-free (a tap of
                the shortcut, or a click on the pill) adds ✕ to cancel and ✓
                to finish
    thinking    faint dots and a turning circle
    speaking    her voice in the bars, and the ANSWER CARD: a fixed-size
                black card with her answer, word by word in time with her
                voice (set_voice_clock), ✕ to close and Copy

Drag it to any side of any monitor: it snaps to that edge, stands upright on
the left and right, and the card opens towards the middle of the screen. The
side is kept (dock() / dock_changed: "bottom|0.500|0,0" - edge, place along
it, the monitor's corner).

Nothing here grows with what Tora says: the pill has one size per state, and
the card keeps its size while its text rolls (the user's rule: no surfaces
that grow as she talks). Neither window takes the keyboard focus, so a text
box the user was typing in keeps it. The top island (ui/caption.py) is
separate and stays.
"""
from __future__ import annotations

import math
import re
import time
from typing import Callable, Optional

from PyQt6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QColor, QCursor, QFont, QFontMetricsF, QGuiApplication, QPainter,
                         QPainterPath, QPen)
from PyQt6.QtWidgets import QWidget

EDGES = ("bottom", "top", "left", "right")
DEFAULT_DOCK = "bottom|0.500|"
MARGIN = 12                    # the pill's edge, from the screen's work area
PAD = 16                       # room around the pill in its window, for the shadow
CARD_GAP = 12                  # between the pill and the card

# (long side, short side) of the pill in each look; upright docks swap them.
SIZES = {
    "idle": (46.0, 10.0),
    "hover": (92.0, 28.0),
    "listening": (112.0, 38.0),
    "hands_free": (160.0, 42.0),
    "thinking": (112.0, 38.0),
    "speaking": (112.0, 38.0),
}
LONGEST = max(s[0] for s in SIZES.values())
THICKEST = max(s[1] for s in SIZES.values())
BARS = 11
CARD_W, CARD_H = 460.0, 182.0
_MARKS = re.compile(r"[*•#`]+")
CARD_HOLD_S = 12.0             # the card stays this long after she has finished
CHARS_PER_S = 42.0             # the card's pace with no voice to keep time with
VOICE_LEAD_S = 0.15            # words show a hair before they are heard
DRAG_SLOP = 5                  # px before a press becomes a drag


def _ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def _font(px: float, weight: int = 500) -> QFont:
    try:
        from ui import tokens
        family = tokens.font_family()
    except Exception:  # noqa: BLE001
        family = "Segoe UI"
    f = QFont(family)
    f.setPixelSize(max(1, int(round(px))))
    f.setWeight(QFont.Weight(weight))
    return f


def _window_flags(w: QWidget) -> None:
    w.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                     | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus
                     | Qt.WindowType.NoDropShadowWindowHint)
    w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    w.setMouseTracking(True)


def _screens():
    return QGuiApplication.screens()


def _screen_key(screen) -> str:
    g = screen.geometry()
    return f"{g.x()},{g.y()}"


def _screen_for(key: str):
    for s in _screens():
        if _screen_key(s) == key:
            return s
    return QGuiApplication.primaryScreen()


def parse_dock(text: str) -> tuple[str, float, str]:
    """"bottom|0.500|0,0" -> ("bottom", 0.5, "0,0"); anything else is the default."""
    try:
        edge, frac, key = (str(text or "").split("|") + ["", "", ""])[:3]
        if edge in EDGES:
            return edge, max(0.0, min(1.0, float(frac))), key
    except ValueError:
        pass
    return "bottom", 0.5, ""


AVOID_FOR_S = 20.0             # a drawing older than this no longer pushes the card away
# SP-12: with the overlay to ask (FlowBar.watch), a note of a drawing only
# covers the moment before the overlay has it - it is on screen exactly as
# long as the overlay shows it, a walkthrough's minute included.
JUST_DRAWN_S = 2.0
CARD_GLIDE_S = 0.25            # SP-11: off a drawing, the card slides; it never jumps
AVOID_PAD = 24.0               # room kept around what Tora drew


def shape_bounds(shape: dict) -> Optional[QRectF]:
    """Where an overlay drawing sits, in logical px, with room around it -
    from the same shape dicts the overlay draws (companion_manager)."""
    try:
        k = shape.get("kind")
        if k == "circle":
            x, y, r = shape["x"], shape["y"], shape["r"]
            rect = QRectF(x - r, y - r, 2 * r, 2 * r)
        elif k == "rect":
            rect = QRectF(QPointF(shape["x1"], shape["y1"]), QPointF(shape["x2"], shape["y2"])).normalized()
        elif k in ("line", "arrow", "poly"):
            xs = [p[0] for p in shape["pts"]]
            ys = [p[1] for p in shape["pts"]]
            rect = QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
        elif k == "underline":
            rect = QRectF(shape["x"], shape["y"] - 6, shape["w"], 12)
        elif k == "angle":
            s = shape["s"]
            rect = QRectF(shape["x"] - s, shape["y"] - s, 2 * s, 2 * s)
        elif k == "text":
            rect = QRectF(shape["x"], shape["y"] - 30, 320, 40)
        else:
            return None
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    return rect.adjusted(-AVOID_PAD, -AVOID_PAD, AVOID_PAD, AVOID_PAD)


def point_bounds(x: float, y: float) -> QRectF:
    """Where a pointed-at target sits with its ring and its name bubble."""
    return QRectF(x - 70, y - 70, 140 + 240, 140)


def overlay_bounds(overlay) -> list[QRectF]:
    """What the overlay shows now (CursorOverlay.drawn_now), as rectangles."""
    shapes, ring = overlay.drawn_now()
    rects = [r for r in (shape_bounds(sh) for sh in shapes) if r is not None]
    if ring is not None:
        rects.append(point_bounds(*ring))
    return rects


def place_card(edge: str, pill: QRectF, geo: QRect, cw: float, ch: float,
               avoid: tuple = ()) -> tuple[float, float]:
    """Top-left of the card: beside the pill, towards the middle, on the
    screen - and off what Tora drew. It covered the knight its own arrow
    started from in the wave-1 demo, and a user's board in the field
    (docs/SHOW_THE_NEXT_MOVE.md, SN-09). The usual place when it is clear;
    else slid to the screen's left or right, or to the far side; else the
    one that covers least."""
    if edge == "bottom":
        x, y = pill.center().x() - cw / 2, pill.top() - CARD_GAP - ch
    elif edge == "top":
        x, y = pill.center().x() - cw / 2, pill.bottom() + CARD_GAP
    elif edge == "left":
        x, y = pill.right() + CARD_GAP, pill.center().y() - ch / 2
    else:
        x, y = pill.left() - CARD_GAP - cw, pill.center().y() - ch / 2

    def clamp(cx: float, cy: float) -> tuple[float, float]:
        return (max(geo.left() + 8, min(geo.right() - 8 - cw, cx)),
                max(geo.top() + 8, min(geo.bottom() - 8 - ch, cy)))

    first = clamp(x, y)
    if not avoid:
        return first
    left, right = geo.left() + 8, geo.right() - 8 - cw
    top, bottom = geo.top() + 8, geo.bottom() - 8 - ch
    if edge in ("top", "bottom"):
        others = [clamp(left, y), clamp(right, y), clamp(x, top if edge == "bottom" else bottom)]
    else:
        others = [clamp(x, top), clamp(x, bottom), clamp(right if edge == "left" else left, y)]
    candidates = [first] + others

    def covered(c: tuple[float, float]) -> float:
        r = QRectF(c[0], c[1], cw, ch)
        total = 0.0
        for a in avoid:
            i = r.intersected(a)
            total += max(0.0, i.width()) * max(0.0, i.height())
        return total
    return min(candidates, key=lambda c: (covered(c) > 0, covered(c), candidates.index(c)))


def nearest_edge(geo: QRect, p: QPoint) -> tuple[str, float]:
    """The side of `geo` nearest `p`, and where along it (0-1)."""
    d = {"left": p.x() - geo.left(), "right": geo.right() - p.x(),
         "top": p.y() - geo.top(), "bottom": geo.bottom() - p.y()}
    edge = min(d, key=d.get)
    if edge in ("top", "bottom"):
        frac = (p.x() - geo.left()) / max(1, geo.width())
    else:
        frac = (p.y() - geo.top()) / max(1, geo.height())
    return edge, max(0.0, min(1.0, frac))


# ── the pill ───────────────────────────────────────────────────────────────────────

class _Pill(QWidget):
    def __init__(self, bar: "FlowBar"):
        super().__init__()
        _window_flags(self)
        self.bar = bar
        self.setToolTip("Hold Ctrl + Win to talk to Tora · drag me to any side")
        self._press: Optional[QPoint] = None
        self._dragging = False

    # where the pill sits in this window
    def pill_rect(self) -> QRectF:
        b = self.bar
        long_, short = b._size
        w, h = self.width(), self.height()
        if b.upright():
            r = QRectF(0, h / 2 - long_ / 2, short, long_)
            if b._dragging:
                r.moveLeft(w / 2 - short / 2)
            elif b._edge == "left":
                r.moveLeft(PAD)
            else:
                r.moveLeft(w - PAD - short)
        else:
            r = QRectF(w / 2 - long_ / 2, 0, long_, short)
            if b._dragging:
                r.moveTop(h / 2 - short / 2)
            elif b._edge == "top":
                r.moveTop(PAD)
            else:
                r.moveTop(h - PAD - short)
        return r

    def _buttons(self) -> tuple[Optional[QRectF], Optional[QRectF]]:
        """(✕, ✓) hit areas while hands-free, else (None, None)."""
        b = self.bar
        if b._state != "listening" or not b._hands_free or b._buttons_a < 0.5:
            return None, None
        r = self.pill_rect()
        d = 28.0
        if b.upright():
            x = r.center().x() - d / 2
            return QRectF(x, r.top() + 7, d, d), QRectF(x, r.bottom() - 7 - d, d, d)
        y = r.center().y() - d / 2
        return QRectF(r.left() + 7, y, d, d), QRectF(r.right() - 7 - d, y, d, d)

    # ── input ──
    def enterEvent(self, _e) -> None:
        self.bar._hover = True
        self.bar._wake()

    def leaveEvent(self, _e) -> None:
        self.bar._hover = False
        self.bar._wake()

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = e.globalPosition().toPoint()
            self._dragging = False

    def mouseMoveEvent(self, e) -> None:
        if self._press is None:
            return
        g = e.globalPosition().toPoint()
        if not self._dragging and (g - self._press).manhattanLength() > DRAG_SLOP:
            self._dragging = True
            self.bar._begin_drag()
        if self._dragging:
            self.bar._drag_to(g)

    def mouseReleaseEvent(self, e) -> None:
        if self._press is None:
            return
        g = e.globalPosition().toPoint()
        if self._dragging:
            self.bar._end_drag(g)
        else:
            self.bar._click(QPointF(e.position()))
        self._press = None
        self._dragging = False

    # ── painting ──
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.bar._paint_pill(p, self.pill_rect())
        p.end()


# ── the answer card ────────────────────────────────────────────────────────────────

class _Card(QWidget):
    def __init__(self, bar: "FlowBar"):
        super().__init__()
        _window_flags(self)
        self.bar = bar
        self.resize(int(CARD_W + 2 * PAD), int(CARD_H + 2 * PAD))

    def card_rect(self) -> QRectF:
        return QRectF(PAD, PAD, CARD_W, CARD_H)

    def close_rect(self) -> QRectF:
        r = self.card_rect()
        return QRectF(r.right() - 16 - 30, r.top() + 13, 30, 30)

    def copy_rect(self) -> QRectF:
        r = self.card_rect()
        return QRectF(r.right() - 18 - 78, r.bottom() - 16 - 36, 78, 36)

    def enterEvent(self, _e) -> None:
        self.bar._card_hover = True
        self.bar._wake()

    def leaveEvent(self, _e) -> None:
        self.bar._card_hover = False
        self.bar._wake()

    def mouseMoveEvent(self, e) -> None:
        pos = QPointF(e.position())
        over = self.close_rect().contains(pos) or self.copy_rect().contains(pos)
        self.setCursor(Qt.CursorShape.PointingHandCursor if over else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, e) -> None:
        pos = QPointF(e.position())
        if self.close_rect().contains(pos):
            self.bar.close_card()
        elif self.copy_rect().contains(pos):
            self.bar.copy_answer()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self.bar._paint_card(p, self)
        p.end()


# ── the controller ─────────────────────────────────────────────────────────────────

class FlowBar(QObject):
    """The flow bar and its answer card. All calls on the Qt thread."""

    cancel_requested = pyqtSignal()     # ✕ while listening
    done_requested = pyqtSignal()       # ✓ while listening: that's the question
    talk_requested = pyqtSignal()       # a click on the resting pill: listen, hands-free
    dock_changed = pyqtSignal(str)      # dragged to another side: keep it

    def __init__(self, dock: str = DEFAULT_DOCK, hotkey: str = "Ctrl + Win"):
        super().__init__()
        self._edge, self._frac, self._screen_key = parse_dock(dock)
        self._state = "idle"
        self._hands_free = False
        self._hover = False
        self._card_hover = False
        self._dragging = False
        self._size = SIZES["idle"]
        self._size_from, self._size_to, self._size_t0 = self._size, self._size, 0.0
        self._buttons_a = 0.0
        self._level = 0.0                # 0-1, smoothed
        self._level_in = 0.0
        self._phase = 0.0
        self._spin = 0.0
        self._snap: Optional[tuple[QPoint, QPoint, float]] = None
        # the answer
        self._text = ""
        self._shown = 0.0
        self._sealed = False
        self._finished_at = 0.0
        self._card_a = 0.0
        self._card_want = False
        self._copied_at = 0.0
        self._clock: Optional[Callable[[], Optional[tuple[float, float]]]] = None
        self._last = time.monotonic()
        self._painted: tuple = ()        # what the pill looked like when last painted
        self._card_painted: tuple = ()   # and the card (UI-03)
        self._avoid: list[tuple[float, QRectF]] = []   # (when, where) Tora drew - the card keeps off
        # SP-12: what is drawn now, asked of the overlay (watch); None: the notes alone.
        self.drawn_now: Optional[Callable[[], list[QRectF]]] = None
        self._card_glide: Optional[tuple[QPoint, QPoint, float]] = None   # (from, to, since)

        self.pill = _Pill(self)
        self.pill.setToolTip(f"Hold {hotkey} to talk to Tora · drag me to any side")
        self.card = _Card(self)
        self._fit_window()
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    # ── public ──────────────────────────────────────────────────────────────────
    def show(self) -> None:
        self._place()
        self.pill.show()
        self._timer.start()

    def hide(self) -> None:
        self._timer.stop()
        self.pill.hide()
        self.card.hide()

    def _wake(self) -> None:
        """Something may change: tick again. The timer stops itself once
        the bar is at rest (UI-02, the speed review of 17 Oct 2026) - it
        used to wake 60 times a second all day for a pill that never moved."""
        if not self._timer.isActive() and self.pill.isVisible():
            self._last = time.monotonic()
            self._timer.start()

    def close(self) -> None:
        self.hide()
        self.pill.close()
        self.card.close()

    def upright(self) -> bool:
        return self._edge in ("left", "right")

    def dock(self) -> str:
        return f"{self._edge}|{self._frac:.3f}|{self._screen_key}"

    def set_dock(self, dock: str) -> None:
        self._edge, self._frac, self._screen_key = parse_dock(dock)
        self._fit_window()
        self._place()

    def set_state(self, name: str) -> None:
        """"idle" | "listening" | "thinking" | "speaking" (AppState names work too)."""
        name = str(name or "idle").lower()
        if name not in ("idle", "listening", "thinking", "speaking"):
            name = "idle"
        if name == self._state:
            return
        was, self._state = self._state, name
        if name == "listening":
            # A new question: the last answer goes.
            self._card_want = False
            self._text, self._shown, self._sealed = "", 0.0, False
        if name != "listening":
            self._hands_free = False
        if name == "idle" and was == "speaking" and not self._finished_at:
            self._finished_at = time.monotonic()
        self._retarget()

    def set_hands_free(self, on: bool) -> None:
        """Listening without the shortcut held: show ✕ and ✓."""
        self._hands_free = bool(on)
        self._retarget()

    def set_level(self, rms: float) -> None:
        """The microphone (or Tora's voice) right now - an RMS, ~0-0.1."""
        self._level_in = max(0.0, min(1.0, float(rms) * 14.0))

    def set_voice_clock(self, clock: Optional[Callable[[], Optional[tuple[float, float]]]]) -> None:
        """() -> (seconds of her voice heard, seconds received) - the card keeps time with it."""
        self._clock = clock

    def push_answer(self, text: str) -> None:
        # The voice's transcript can carry list marks ("• Black has 7
        # pawns", 5 Oct): the card shows words, never bullets or stars.
        text = _MARKS.sub("", text or "")
        if not text:
            return
        if self._sealed:                  # a new answer after a finished one
            self._text, self._shown, self._sealed = "", 0.0, False
        self._text += text
        self._finished_at = 0.0
        self._card_want = True
        self._wake()

    def finish_answer(self, text: str = "") -> None:
        if text and len(text.strip()) > len(self._text.strip()):
            self._text = text
        self._sealed = True
        self._finished_at = time.monotonic()
        if self._text.strip():
            self._card_want = True
        self._wake()

    def close_card(self) -> None:
        self._card_want = False
        self._wake()

    def copy_answer(self) -> None:
        QGuiApplication.clipboard().setText(self._text.strip())
        self._copied_at = time.monotonic()
        self._wake()

    # ── layout ──────────────────────────────────────────────────────────────────
    def _fit_window(self) -> None:
        if self.upright():
            self.pill.resize(int(THICKEST + 2 * PAD), int(LONGEST + 2 * PAD))
        else:
            self.pill.resize(int(LONGEST + 2 * PAD), int(THICKEST + 2 * PAD))

    def _screen(self):
        return _screen_for(self._screen_key)

    def _home(self) -> QPoint:
        """Where the pill's window goes for the current dock."""
        geo = self._screen().availableGeometry()
        w, h = self.pill.width(), self.pill.height()
        if self._edge in ("top", "bottom"):
            cx = geo.left() + self._frac * geo.width()
            cx = max(geo.left() + w / 2, min(geo.right() - w / 2, cx))
            x = cx - w / 2
            y = geo.top() + MARGIN - PAD if self._edge == "top" else geo.bottom() + 1 - MARGIN - (h - PAD)
        else:
            cy = geo.top() + self._frac * geo.height()
            cy = max(geo.top() + h / 2, min(geo.bottom() - h / 2, cy))
            y = cy - h / 2
            x = geo.left() + MARGIN - PAD if self._edge == "left" else geo.right() + 1 - MARGIN - (w - PAD)
        return QPoint(int(round(x)), int(round(y)))

    def _place(self) -> None:
        self.pill.move(self._home())
        self._place_card()

    def watch(self, overlay) -> None:
        """Keep the card off what the overlay shows now (SP-12)."""
        self.drawn_now = lambda: overlay_bounds(overlay)

    def _avoiding(self, now: float) -> list[QRectF]:
        keep = JUST_DRAWN_S if self.drawn_now is not None else AVOID_FOR_S
        self._avoid = [(t, a) for t, a in self._avoid if now - t < keep]
        rects = [a for _t, a in self._avoid]
        if self.drawn_now is not None:
            try:
                rects += list(self.drawn_now())
            except Exception:  # noqa: BLE001 - the card is placed regardless
                pass
        return rects

    def _place_card(self, glide: bool = False) -> None:
        """Beside the pill, towards the middle of the screen, on the screen
        the pill is ACTUALLY on - not the one its saved dock names, which a
        changed monitor layout can make another (a user's card ran off the
        screen's edge) - and clear of what Tora drew (SN-09). glide: it is
        showing and a drawing moved it - slide there (SP-11)."""
        r = self.pill.pill_rect().translated(QPointF(self.pill.pos()))
        screen = QGuiApplication.screenAt(r.center().toPoint()) or self._screen()
        geo = screen.availableGeometry()
        now = time.monotonic()
        x, y = place_card(self._edge, r, geo, CARD_W, CARD_H, tuple(self._avoiding(now)))
        # Where it lands may be a screen of another size: keep it on that one.
        on = QGuiApplication.screenAt(QPoint(int(x + CARD_W / 2), int(y + CARD_H / 2)))
        if on is not None and on is not screen:
            g = on.availableGeometry()
            x = max(g.left() + 8, min(g.right() - 8 - CARD_W, x))
            y = max(g.top() + 8, min(g.bottom() - 8 - CARD_H, y))
        to = QPoint(int(round(x - PAD)), int(round(y - PAD)))
        if glide and self.card.isVisible() and self.card.pos() != to:
            self._card_glide = (QPoint(self.card.pos()), to, now)
            self._wake()
        else:
            self._card_glide = None
            self.card.move(to)

    # ── what Tora drew: the card keeps clear of it ─────────────────────────────
    def note_drawing(self, shape: dict) -> None:
        """Slot for CompanionManager.sig_draw."""
        rect = shape_bounds(shape) if isinstance(shape, dict) else None
        if rect is not None:
            self._avoid.append((time.monotonic(), rect))
            if self.card.isVisible():
                self._place_card(glide=True)

    def note_point(self, x: float, y: float, label: str = "") -> None:
        """Slot for CompanionManager.sig_point_at: the target, its ring and its name bubble."""
        self._avoid.append((time.monotonic(), point_bounds(x, y)))
        if self.card.isVisible():
            self._place_card(glide=True)

    def clear_drawings(self) -> None:
        """Slot for CompanionManager.sig_clear_drawings."""
        self._avoid.clear()

    # ── dragging ────────────────────────────────────────────────────────────────
    def _begin_drag(self) -> None:
        self._dragging = True
        self._snap = None
        self.card.hide()                  # back beside the pill once it has landed
        self._wake()

    def _drag_to(self, g: QPoint) -> None:
        screen = QGuiApplication.screenAt(g) or self._screen()
        edge, _frac = nearest_edge(screen.availableGeometry(), g)
        if (edge in ("left", "right")) != self.upright():
            self._edge = edge
            self._fit_window()
        self._edge = edge
        self.pill.move(g.x() - self.pill.width() // 2, g.y() - self.pill.height() // 2)
        self.pill.update()

    def _end_drag(self, g: QPoint) -> None:
        screen = QGuiApplication.screenAt(g) or self._screen()
        self._edge, self._frac = nearest_edge(screen.availableGeometry(), g)
        self._screen_key = _screen_key(screen)
        self._dragging = False
        self._fit_window()
        start = QPoint(g.x() - self.pill.width() // 2, g.y() - self.pill.height() // 2)
        self.pill.move(start)
        self._snap = (start, self._home(), time.monotonic())
        self.dock_changed.emit(self.dock())
        self._wake()

    def _click(self, pos: QPointF) -> None:
        cancel, done = self.pill._buttons()
        if cancel is not None and cancel.contains(pos):
            self.cancel_requested.emit()
        elif done is not None and done.contains(pos):
            self.done_requested.emit()
        elif self._state == "idle":
            self.talk_requested.emit()

    # ── animation ───────────────────────────────────────────────────────────────
    def _look(self) -> str:
        if self._state == "listening":
            return "hands_free" if self._hands_free else "listening"
        if self._state == "idle":
            return "hover" if (self._hover or self._dragging) else "idle"
        return self._state

    def _retarget(self) -> None:
        want = SIZES[self._look()]
        if want != self._size_to:
            self._size_from, self._size_to, self._size_t0 = self._size, want, time.monotonic()
            self._wake()

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.05, now - self._last)
        self._last = now
        self._retarget()
        k = _ease((now - self._size_t0) / 0.26)
        self._size = tuple(a + (b - a) * k for a, b in zip(self._size_from, self._size_to))
        want_b = 1.0 if (self._state == "listening" and self._hands_free) else 0.0
        self._buttons_a += (want_b - self._buttons_a) * min(1.0, dt * 12)
        # The bars: the microphone while listening; her voice while speaking.
        target = self._level_in
        if self._state == "speaking":
            target = self._voice_level(now)
        elif self._state != "listening":
            target = 0.0
        rate = 18.0 if target > self._level else 6.0
        self._level += (target - self._level) * min(1.0, dt * rate)
        self._phase += dt * (7.0 if self._state in ("listening", "speaking") else 2.0)
        self._spin = (self._spin + dt * 300.0) % 360.0
        if self._snap is not None:
            start, end, t0 = self._snap
            u = _ease((now - t0) / 0.24)
            self.pill.move(QPoint(int(start.x() + (end.x() - start.x()) * u),
                                  int(start.y() + (end.y() - start.y()) * u)))
            if u >= 1.0:
                self._snap = None
                self._place_card()
        if self._card_glide is not None:
            start, end, t0 = self._card_glide
            u = _ease((now - t0) / CARD_GLIDE_S)
            self.card.move(QPoint(int(start.x() + (end.x() - start.x()) * u),
                                  int(start.y() + (end.y() - start.y()) * u)))
            if u >= 1.0:
                self._card_glide = None
        self._tick_card(now, dt)
        # Idle (or hovered) and settled, the pill does not change: painting it
        # 60 times a second anyway kept Windows composing it 60 times a second.
        look = self._look()
        key = (look, self._edge, tuple(round(v, 1) for v in self._size), round(self._buttons_a, 2))
        if look not in ("idle", "hover") or key != self._painted or self._snap is not None:
            self._painted = key
            self.pill.update()
        # At rest - idle or hovered, its size reached, nothing moving or
        # showing - nothing changes until something happens (_wake).
        if (look in ("idle", "hover") and now - self._size_t0 >= 0.3 and self._snap is None
                and self._card_glide is None and not self.card.isVisible()
                and self._buttons_a < 0.01 and self._level < 0.005):
            self._timer.stop()

    def _voice_level(self, now: float) -> float:
        """Her voice, for the bars: lively while it plays (no level is given
        for the voice itself, so a speech-like movement)."""
        return 0.45 + 0.35 * math.sin(now * 9.0) * math.sin(now * 3.1 + 1.0) ** 2

    def _tick_card(self, now: float, dt: float) -> None:
        text_len = len(self._text)
        clock = self._clock() if self._clock is not None else None
        if clock and clock[1] > 0 and not self._sealed:
            heard, received = clock
            target = (heard + VOICE_LEAD_S) / received * text_len
        else:
            target = self._shown + CHARS_PER_S * dt * (2.0 if self._sealed else 1.0)
        self._shown = max(self._shown, min(float(text_len), target))
        if self._finished_at and not self._card_hover and self._state != "speaking" \
                and now - self._finished_at > CARD_HOLD_S:
            self._card_want = False
        want = 1.0 if (self._card_want and not self._dragging and self._text.strip()) else 0.0
        self._card_a += (want - self._card_a) * min(1.0, dt * (10 if want else 7))
        if self._card_a > 0.02:
            if not self.card.isVisible():
                self._place_card()
                self.card.show()
            # Only when what it shows has changed (UI-03): laying the words
            # out 60 times a second for a card that sat still was waste.
            key = (round(self._card_a, 2), self._state == "speaking" and not self._sealed,
                   int(self._shown), len(self._text), now - self._copied_at < 1.6)
            if key != self._card_painted:
                self._card_painted = key
                self.card.update()
        elif self.card.isVisible():
            self.card.hide()
            self._card_painted = ()

    # ── painting the pill ───────────────────────────────────────────────────────
    def _paint_pill(self, p: QPainter, r: QRectF) -> None:
        radius = min(r.width(), r.height()) / 2
        idle = self._look() == "idle"
        # a soft shadow, then the black glass
        for i, a in enumerate((26, 16, 9)):
            g = 2.0 + i * 2.5
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, a))
            p.drawRoundedRect(r.adjusted(-g, -g + 1.5, g, g + 1.5), radius + g, radius + g)
        p.setBrush(QColor(0, 0, 0, 200 if idle else 240))
        p.setPen(QPen(QColor(255, 255, 255, 46 if idle else 30), 1.0))
        p.drawRoundedRect(r, radius, radius)
        if idle:
            return
        look = self._look()
        if look == "hover":
            self._paint_bars(p, r, level=0.0, alpha=110, count=7)
        elif self._state == "thinking":
            self._paint_thinking(p, r)
        else:
            inner = QRectF(r)
            if self._state == "listening" and self._buttons_a > 0.02:
                self._paint_buttons(p, r)
                cut = 40.0
                inner = r.adjusted(0, cut, 0, -cut) if self.upright() else r.adjusted(cut, 0, -cut, 0)
            self._paint_bars(p, inner, level=self._level, alpha=255, count=BARS)

    def _paint_bars(self, p: QPainter, r: QRectF, *, level: float, alpha: int, count: int) -> None:
        up = self.upright()
        span = (r.height() if up else r.width()) - 30.0
        thick = (r.width() if up else r.height())
        step = span / max(1, count - 1)
        dot = 2.6
        max_len = thick * 0.52
        mid = (count - 1) / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, alpha))
        for i in range(count):
            bell = 1.0 - (abs(i - mid) / (mid + 1)) ** 2 * 0.75
            wobble = 0.55 + 0.45 * math.sin(self._phase * 1.3 + i * 0.95)
            length = dot + (max_len - dot) * max(0.0, min(1.0, level * bell * wobble * 1.6))
            if up:
                cy = r.top() + 15.0 + i * step
                rr = QRectF(r.center().x() - length / 2, cy - dot / 2, length, dot)
            else:
                cx = r.left() + 15.0 + i * step
                rr = QRectF(cx - dot / 2, r.center().y() - length / 2, dot, length)
            p.drawRoundedRect(rr, dot / 2, dot / 2)

    def _paint_thinking(self, p: QPainter, r: QRectF) -> None:
        up = self.upright()
        # faint dots, then the turning circle at the far end
        count = 7
        span = (r.height() if up else r.width()) - 52.0
        step = span / (count - 1)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(count):
            a = 70 + int(40 * math.sin(self._phase * 2.2 - i * 0.6))
            p.setBrush(QColor(255, 255, 255, max(30, a)))
            if up:
                c = QPointF(r.center().x(), r.top() + 14.0 + i * step)
            else:
                c = QPointF(r.left() + 14.0 + i * step, r.center().y())
            p.drawEllipse(c, 1.4, 1.4)
        c = QPointF(r.center().x(), r.bottom() - 19.0) if up else QPointF(r.right() - 19.0, r.center().y())
        for i in range(10):
            ang = math.radians(self._spin + i * 36.0)
            a = int(255 * (0.15 + 0.85 * (i / 9)))
            pen = QPen(QColor(255, 255, 255, a), 1.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(c.x() + 4.0 * math.cos(ang), c.y() + 4.0 * math.sin(ang)),
                       QPointF(c.x() + 8.0 * math.cos(ang), c.y() + 8.0 * math.sin(ang)))

    def _paint_buttons(self, p: QPainter, r: QRectF) -> None:
        cancel, done = self.pill._buttons()
        if cancel is None:
            return
        a = self._buttons_a
        # ✕: a grey disc with a dark cross
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(110, 110, 114, int(255 * a)))
        p.drawEllipse(cancel)
        pen = QPen(QColor(20, 20, 22, int(255 * a)), 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        c, k = cancel.center(), 4.6
        p.drawLine(QPointF(c.x() - k, c.y() - k), QPointF(c.x() + k, c.y() + k))
        p.drawLine(QPointF(c.x() + k, c.y() - k), QPointF(c.x() - k, c.y() + k))
        # ✓: a white disc with a dark tick
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, int(255 * a)))
        p.drawEllipse(done)
        pen = QPen(QColor(10, 10, 12, int(255 * a)), 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        c = done.center()
        tick = QPainterPath(QPointF(c.x() - 5.0, c.y() + 0.2))
        tick.lineTo(QPointF(c.x() - 1.5, c.y() + 3.8))
        tick.lineTo(QPointF(c.x() + 5.2, c.y() - 3.6))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(tick)

    # ── painting the card ───────────────────────────────────────────────────────
    def _paint_card(self, p: QPainter, w: _Card) -> None:
        a = self._card_a
        p.setOpacity(a)
        r = w.card_rect()
        for i, al in enumerate((34, 20, 10)):
            g = 2.0 + i * 3.0
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, al))
            p.drawRoundedRect(r.adjusted(-g, -g + 3, g, g + 3), 24 + g, 24 + g)
        p.setBrush(QColor(0, 0, 0, 242))
        p.setPen(QPen(QColor(255, 255, 255, 26), 1.0))
        p.drawRoundedRect(r, 24, 24)
        # her mark: four bars, top left
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255))
        for i, hgt in enumerate((7.0, 13.0, 9.0, 15.0, 6.0)):
            x = r.left() + 22 + i * 4.6
            p.drawRoundedRect(QRectF(x, r.top() + 28 - hgt / 2, 2.4, hgt), 1.2, 1.2)
        # what she is doing, in grey
        speaking = self._state == "speaking" and not self._sealed
        p.setFont(_font(13, 500))
        p.setPen(QColor(150, 150, 156))
        p.drawText(QRectF(r.left() + 60, r.top() + 14, r.width() - 130, 28),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   "Tora is answering" if speaking else "Tora")
        # ✕
        c = w.close_rect()
        p.setPen(QPen(QColor(255, 255, 255, 220), 1.4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(c.adjusted(1, 1, -1, -1))
        k = 4.4
        pen = QPen(QColor(255, 255, 255), 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        cc = c.center()
        p.drawLine(QPointF(cc.x() - k, cc.y() - k), QPointF(cc.x() + k, cc.y() + k))
        p.drawLine(QPointF(cc.x() + k, cc.y() - k), QPointF(cc.x() - k, cc.y() + k))
        # the answer, as far as her voice has got - the newest lines when it is long
        body = QRectF(r.left() + 24, r.top() + 54, r.width() - 48, r.height() - 54 - 60)
        shown = self._text[: int(self._shown)].strip()
        font = _font(16, 500)
        p.setFont(font)
        p.setPen(QColor(255, 255, 255))
        lines = _wrap(shown, QFontMetricsF(font), body.width())
        fm = QFontMetricsF(font)
        fits = max(1, int(body.height() // fm.lineSpacing()))
        for i, line in enumerate(lines[-fits:]):
            p.drawText(QPointF(body.left(), body.top() + fm.ascent() + i * fm.lineSpacing()), line)
        # Copy
        cr = w.copy_rect()
        copied = time.monotonic() - self._copied_at < 1.6
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(92, 92, 96))
        p.drawRoundedRect(cr, 12, 12)
        p.setFont(_font(14, 600))
        p.setPen(QColor(235, 235, 238))
        p.drawText(cr, Qt.AlignmentFlag.AlignCenter, "Copied" if copied else "Copy")
        p.setOpacity(1.0)


def _wrap(text: str, fm: QFontMetricsF, width: float) -> list[str]:
    """Word-wrap `text` to `width`."""
    lines: list[str] = []
    for para in text.split("\n"):
        line = ""
        for word in para.split():
            trial = f"{line} {word}" if line else word
            if fm.horizontalAdvance(trial) <= width or not line:
                line = trial
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return [ln for ln in lines if ln] or [""]
