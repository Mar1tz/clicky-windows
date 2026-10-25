"""
Tora's blue buddy cursor — a port of the original macOS overlay, redrawn.

Behaves as tora-main/leanring-buddy/OverlayWindow.swift:
  - A blue triangle, rotated -35°, at (+35, +25) from the real cursor
  - States in place:
      idle / speaking → triangle
      listening       → voice bars
      thinking        → turning ring
      pointing        → triangle + the name of the thing, in a bubble
Its look - rounded, a gradient, a soft shadow and a quiet glow instead of a
flat triangle in three stacked blue discs - is ui/buddy.py, chosen by the
user from four shown side by side on 30 Sep 2026.

One window per monitor (_Surface), each exactly covering its monitor, all
drawing the same scene in desktop coordinates. It used to be one window
stretched over every monitor, and Windows gives a window ONE scaling: on
this PC - a 100% monitor between two 125% ones - the cursor, its card and
the drawings came out in the wrong place and size on the other monitors,
and Windows resized the window as it changed hands between them. The top
caption always sits on the main monitor.
"""

import math
import random
import time
from typing import Optional

from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt, QTimer, QPointF, QRect, QRectF
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QPainterPath, QCursor, QFont, QFontMetricsF,
)

from screen import monitors
from ui import buddy
from ui.mist import Mist
from ui.caption import CaptionIsland


MODE_IDLE      = "idle"
MODE_LISTENING = "listening"
MODE_THINKING  = "thinking"
MODE_SPEAKING  = "speaking"


# Exactly matches Swift source: buddyX = swiftUIPosition.x + 35; buddyY = +25
OFFSET_X = 35
OFFSET_Y = 25

# How far the buddy itself reaches from its centre, in any state: the widest
# glow is 24px (listening, loud), the triangle swoops to 1.25x in flight.
BUDDY_REACH = 40.0

# Swift: .rotationEffect(.degrees(-35))
TRI_ROTATION_DEG = -35.0

# #3380FF
CURSOR_BLUE = QColor(0x33, 0x80, 0xFF)


def set_cursor_colour(hex_colour: str) -> None:
    """The colour Tora wears (Make Tora yours): the pointer, its halo and
    rings. Read at paint time, so the next frame shows it."""
    global CURSOR_BLUE
    c = QColor(hex_colour)
    CURSOR_BLUE = c if c.isValid() else QColor(0x33, 0x80, 0xFF)
    buddy.set_colour(CURSOR_BLUE.name())


# Her cursor beside the mouse (Settings > Tora, Win + Alt + H, the first
# launch; asked for on 18 Oct 2026). Hidden, the screen shows only her bar -
# but when she points at something she still flies there, because the arrow
# is the answer, and is gone again once it is done (no flight home).
_CURSOR_SHOWN = True


def set_cursor_shown(on: bool) -> None:
    """Show or hide her cursor. Read every frame, so the next one shows it."""
    global _CURSOR_SHOWN
    _CURSOR_SHOWN = bool(on)


def cursor_shown() -> bool:
    return _CURSOR_SHOWN

# Teaching-annotation palette. A drawing wears TORA's colour - the one the
# user picked for her cursor (Make Tora yours) - unless it names another
# because the colour means something (red for a mistake, green for right).
# "tora", no colour at all, and "blue" (the name her colour had when it was
# always blue) are all hers, worked out at paint time: changing her colour
# recolours what is already on screen. The rest are chosen for contrast on
# both light and dark content.
TORA_COLOUR_NAMES = ("", "tora", "blue")
ANNOT_COLORS = {
    "blue":   QColor(0x33, 0x80, 0xFF),
    "red":    QColor(0xFF, 0x45, 0x55),
    "green":  QColor(0x2E, 0xCC, 0x71),
    "yellow": QColor(0xFF, 0xD5, 0x4F),
    "orange": QColor(0xFF, 0x8C, 0x2E),
    "purple": QColor(0xB5, 0x6C, 0xFF),
    "white":  QColor(0xF5, 0xF7, 0xFA),
    "cyan":   QColor(0x4F, 0xD9, 0xFF),
}


def annotation_colour(name) -> QColor:
    """The colour a drawing is painted in: Tora's own unless it names
    another (see ANNOT_COLORS)."""
    key = str(name or "").strip().lower()
    if key in TORA_COLOUR_NAMES:
        return QColor(CURSOR_BLUE)
    return QColor(ANNOT_COLORS.get(key, CURSOR_BLUE))


def _tint(colour: QColor, white: float) -> QColor:
    """`colour` mixed with `white` (0-1) of white - a pale version of it."""
    return QColor(round(colour.red() + (255 - colour.red()) * white),
                  round(colour.green() + (255 - colour.green()) * white),
                  round(colour.blue() + (255 - colour.blue()) * white))

# Progressive stroke animation — shapes draw at "hand speed" (px/sec), so a
# long line takes visibly longer than a short tick mark. Queued sequentially;
# the buddy cursor rides the pen tip while each stroke draws.
STROKE_SPEED_PX_S   = 420.0
SHAPE_DRAW_MIN_S    = 0.6
SHAPE_DRAW_MAX_S    = 2.2
SHAPE_GAP_SECONDS   = 0.28

# How long a one-off drawing stays on screen before fading itself away.
# Answering "where is X" leaves an arrow behind; it has done its job after a
# few seconds and then it is just clutter on the user's desktop. Drawings made
# DURING a guided walkthrough are exempt — there the drawing IS the guidance
# and has to stay until the step is done. Measured from when each shape starts
# drawing, and it fades over its last quarter rather than blinking out.
ANNOTATION_TTL_S    = 14.0
# When a guide ends, anything it left on screen gets this much longer.
GUIDE_LINGER_S      = 4.0

_TEXT_SIZES = {"s": 12, "m": 17, "l": 26}

# The status card above the buddy (_island_box).
_ISLAND_PAD_X, _ISLAND_PAD_Y, _ISLAND_GAP, _ISLAND_DOT_W = 11, 7, 3, 16
_ISLAND_FONTS: Optional[tuple] = None


def _island_fonts() -> tuple:
    """(head font, sub font, their metrics) - made once, not every frame
    the card is up (UI-04)."""
    global _ISLAND_FONTS
    if _ISLAND_FONTS is None:
        head = QFont("Segoe UI", 9, QFont.Weight.DemiBold)
        sub = QFont("Segoe UI", 8, QFont.Weight.Normal)
        _ISLAND_FONTS = (head, sub, QFontMetricsF(head), QFontMetricsF(sub))
    return _ISLAND_FONTS


def _shape_path_pts(shape: dict):
    """(pts, closed) polyline for stroke-able shapes; None for circle/text."""
    kind = shape.get("kind")
    if kind in ("line", "arrow"):
        return list(shape["pts"]), False
    if kind == "poly":
        return list(shape["pts"]), True
    if kind == "rect":
        x1, y1, x2, y2 = shape["x1"], shape["y1"], shape["x2"], shape["y2"]
        return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)], True
    if kind == "underline":
        return [(shape["x"], shape["y"]),
                (shape["x"] + shape["w"], shape["y"])], False
    if kind == "angle":
        s = shape.get("s", 22)
        rot = math.radians(shape.get("rot", 0))
        x, y = shape["x"], shape["y"]
        d1 = (math.cos(rot), math.sin(rot))
        d2 = (math.cos(rot + math.pi / 2), math.sin(rot + math.pi / 2))
        return [(x + d1[0] * s, y + d1[1] * s),
                (x + (d1[0] + d2[0]) * s, y + (d1[1] + d2[1]) * s),
                (x + d2[0] * s, y + d2[1] * s)], False
    return None, False


def _shape_length(shape: dict) -> float:
    """Approximate stroke length in logical px — drives draw duration."""
    kind = shape.get("kind")
    if kind == "circle":
        return 2 * math.pi * shape.get("r", 30)
    if kind == "text":
        return 90.0   # fixed short reveal
    pts, closed = _shape_path_pts(shape)
    if not pts or len(pts) < 2:
        return 60.0
    if closed:
        pts = pts + [pts[0]]
    return sum(math.hypot(b[0] - a[0], b[1] - a[1])
               for a, b in zip(pts, pts[1:]))


def _partial_pts(pts, closed, u):
    """First u-fraction of a polyline. Returns the drawn point list."""
    if closed and len(pts) >= 3:
        pts = list(pts) + [pts[0]]
    if len(pts) < 2:
        return list(pts)
    seg_lens = [math.hypot(b[0] - a[0], b[1] - a[1])
                for a, b in zip(pts, pts[1:])]
    total = sum(seg_lens)
    if total <= 0:
        return [pts[0]]
    budget = total * min(1.0, max(0.0, u))
    drawn = [pts[0]]
    for (a, b), L in zip(zip(pts, pts[1:]), seg_lens):
        if budget <= 0:
            break
        if L <= budget:
            drawn.append(b)
            budget -= L
        else:
            t = budget / L
            drawn.append((a[0] + (b[0] - a[0]) * t,
                          a[1] + (b[1] - a[1]) * t))
            budget = 0
    return drawn


def _stroke_tip(shape: dict, u: float):
    """Current pen-tip position of a shape at progress u — where the buddy
    hovers while 'drawing'. Returns (x, y) in logical screen coords."""
    kind = shape.get("kind")
    if kind == "circle":
        a = math.radians(90 - 360 * u)   # clockwise from 12 o'clock
        return (shape["x"] + shape["r"] * math.cos(a),
                shape["y"] - shape["r"] * math.sin(a))
    if kind == "text":
        return (shape["x"], shape["y"])
    pts, closed = _shape_path_pts(shape)
    if not pts:
        return None
    drawn = _partial_pts(pts, closed, u)
    return drawn[-1] if drawn else None

# SP-11: the buddy rides the pen tip, so when an arrow is finished it sat on
# the arrowhead - covering the very thing it drew, for 2.5 s. It rests this
# far back along the last stroke instead, the head in clear view.
REST_BACK_PX = 40.0


def _rest_point(shape: dict):
    """Where the buddy rests when a stroke is done: for a line or an arrow,
    REST_BACK_PX back along its last segment (at most half of it); for any
    other shape, where the pen stopped."""
    if shape.get("kind") in ("arrow", "line"):
        pts, _closed = _shape_path_pts(shape)
        if pts and len(pts) >= 2:
            (px, py), (ex, ey) = pts[-2], pts[-1]
            seg = math.hypot(ex - px, ey - py)
            if seg > 0:
                back = min(REST_BACK_PX, seg / 2)
                return ex - (ex - px) / seg * back, ey - (ey - py) / seg * back
    return _stroke_tip(shape, 1.0)


# Pointing phrases from the original
POINTER_PHRASES = (
    "right here!", "this one!", "over here!",
    "click this!", "here it is!", "found it!",
)

# Teacher-pace pointing timings (seconds)
# Flight duration scales with distance but stays slow enough to follow visually.
FLY_DURATION_MIN = 1.6
FLY_DURATION_MAX = 2.8
DWELL_SECONDS    = 4.0    # sits on the element while the LLM explains
# UI-01: the clock. 60 a second while anything moves; at rest under a still
# mouse - most of the day - 20 a second, which paints the glow's slow breath
# just as it was (every third 60 Hz frame), with a third of the wake-ups.
TICK_MS          = 16
REST_TICK_MS     = 50
REST_AFTER       = 30     # quiet frames (half a second) before slowing
RETURN_DURATION  = 1.4

# A walkthrough step's arrow is a signpost, not a lecture (LG-02). Teacher
# pace - a 1.6-2.8 s flight, 4 s on the target, then back to the cursor - is
# for pointing while Tora explains something. A step's arrow at that pace
# landed ~2 s after the step and was gone again before a slow reader got
# there. So it arrives in a blink and stays until the step changes.
GUIDE_FLY_S = 0.35

# PT-17, "you're in control": during a walkthrough the mouse is theirs. While
# they move it, Tora's arrow holds still where it is - a flight pauses, and
# goes on once the mouse has rested this long - and its bubble says so.
IN_CONTROL_S = 0.6
IN_CONTROL_PX = 3.0
IN_CONTROL_CUE = "You're in control"

# Pointing state machine
_PHASE_FOLLOW    = "follow"
_PHASE_FLYING    = "flying"
_PHASE_DWELLING  = "dwelling"
_PHASE_RETURNING = "returning"


class _Surface(QWidget):
    """One monitor's share of the overlay: a transparent, click-through
    window covering exactly that monitor, so Windows draws it at THAT
    monitor's scaling. It paints the whole scene in desktop coordinates and
    its own edges clip it."""

    def __init__(self, overlay: "CursorOverlay", screen):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._overlay = overlay
        self.qscreen = screen
        self.rects: list[QRect] = []     # what was drawn on it at the last frame
        self.place(screen)

    def place(self, screen) -> None:
        self.qscreen = screen
        geo = screen.geometry()
        # Leave the bottom 2 px: a topmost window covering a whole monitor
        # stops Windows' auto-hide taskbar from appearing on hover.
        geo.setBottom(geo.bottom() - 2)
        if hasattr(self, "setScreen"):
            self.setScreen(screen)
        self.setGeometry(geo)

    def area(self) -> QRectF:
        return QRectF(self.geometry())

    def paintEvent(self, event):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.translate(-self.x(), -self.y())        # draw in desktop coordinates
            self._overlay._paint(p)
        finally:
            p.end()


class CursorOverlay(QWidget):
    """The buddy, its drawings and the top caption: the state, the 60 fps
    tick and the painting. Not a window itself - main.py's show() shows one
    _Surface per monitor, which calls back into _paint."""

    def __init__(self):
        super().__init__()

        # Spring follow state
        self._display_pos = QPointF(0, 0)
        self._vel = QPointF(0, 0)
        self._pointer_screen = None            # the monitor the pointer was on last frame
        self._rest_frames = 0                  # frames in a row with nothing moving (_tick)
        self._mode: str = MODE_IDLE
        self._audio_level: float = 0.0
        # TF-02: the ghost trail while the shortcut is held, and its haze.
        self._mist = Mist()
        self._phase: float = 0.0

        # PT-17: when the user last moved the mouse, and where it was
        self._user_moved_at = 0.0
        self._last_real: Optional[QPointF] = None
        self._last_tick_at = 0.0

        # Pointing / speech bubble
        self._locked_pos: Optional[QPointF] = None
        self._bubble_text: str = ""
        self._bubble_alpha: float = 0.0
        self._bubble_scale: float = 0.5
        self._rotation_deg: float = TRI_ROTATION_DEG

        # Bezier flight state (teacher pace)
        self._flight_phase: str = _PHASE_FOLLOW
        self._fly_start_pos = QPointF(0, 0)
        self._fly_end_pos   = QPointF(0, 0)
        self._fly_control   = QPointF(0, 0)
        self._fly_t0: float = 0.0
        self._fly_duration: float = 1.8
        self._flight_scale: float = 1.0
        self._dwell_until: float = 0.0
        # When True, dwell never expires — manager releases after TTS finishes
        self._hold_dwell: bool = False

        # Slow mode — doubles flight + dwell so Tora feels more like a teacher
        self._slow_mode: bool = False

        # ── AI status island ────────────────────────────────────────────────
        # What the assistant is doing right now, in the user's words, plus
        # where they are in a guide. The rule this exists to enforce: if the
        # assistant is busy for more than a moment, SOMETHING on screen must
        # change, or it reads as frozen.
        self._activity_key: str = ""
        self._activity_text: str = ""
        self._activity_at: float = 0.0
        self._guide_cur: int = 0
        self._guide_total: int = 0
        self._guide_label: str = ""
        self._island_alpha: float = 0.0

        # ── Top caption island ──────────────────────────────────────────────
        # The words Tora is saying, at the top of the MAIN monitor, always -
        # the user's choice: it used to follow the window in front, and with
        # three monitors it turned up on whichever one that was. Fed by
        # sig_response_chunk; see ui/caption.py. The screen is looked up once
        # per utterance.
        self._caption = CaptionIsland(self)
        self._cap_screen: Optional[QRectF] = None

        # Optional highlight ring around detected element (x, y, radius)
        self._ring: Optional[tuple] = None
        self._ring_phase: float = 0.0

        # Teaching annotations — list of shape dicts the overlay paints each
        # tick. Shapes animate in sequentially (progressive strokes) and
        # persist until clear_annotations() — a lesson stays on screen while
        # the student studies it.
        self._annotations: list[dict] = []
        self._ann_painted = 0               # how many were on screen at the last frame
        self._draw_queue_end: float = 0.0   # when the last queued stroke finishes
        self._last_tip = None               # pen position held between strokes
        self._last_stroke = None            # the shape it was last drawing (SP-11)

        # Thinking spinner phase
        self._spin_phase: float = 0.0

        # One window per monitor, kept in step with the monitors: plugged in,
        # unplugged, rearranged or rescaled, the overlay follows.
        self._surfaces: list[_Surface] = []
        self._shown = False
        self._watched_screens: set = set()
        self._sync_pending = False
        self._sync_surfaces()
        app = QApplication.instance()
        if app is not None:
            app.screenAdded.connect(self._screens_changed)
            app.screenRemoved.connect(self._screens_changed)
            app.primaryScreenChanged.connect(self._screens_changed)

        # Seed position so we don't flash at (0, 0)
        qp = QCursor.pos()
        self._display_pos = QPointF(qp.x() + OFFSET_X, qp.y() + OFFSET_Y)

        # 60 FPS
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start(16)

        # Release bubble / pointing
        self._lock_timer = QTimer(self)
        self._lock_timer.setSingleShot(True)
        self._lock_timer.timeout.connect(self._release_lock)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_mode(self, mode: str):
        self._mode = mode
        if mode in (MODE_SPEAKING, MODE_IDLE):
            self._mist.end_haze(time.monotonic())         # she is answering: the haze has done its job

    def set_tracing(self, state: str) -> None:
        """The shortcut's trail (docs/TRACE_TO_FOCUS.md): "on" when it goes
        down, "keep" when it comes up after showing something - the traced
        area stays as a haze until she answers - and "drop" for a tap."""
        now = time.monotonic()
        if state == "on":
            try:
                from config import cfg
                style = "line" if cfg.switch("trail_line") else "mist"
            except Exception:  # noqa: BLE001 - no config: the default
                style = "mist"
            self._mist.start(now, style)
        else:
            self._mist.stop(now, keep=state == "keep")
        self._refresh()

    def set_audio_level(self, rms: float):
        # Match Swift easing: eased = pow(min(rms*2.85, 1), 0.76)
        norm = max(rms - 0.008, 0.0)
        eased = min(norm * 2.85, 1.0) ** 0.76
        self._audio_level = self._audio_level * 0.55 + eased * 0.45
        self._caption.note_audio_level(self._audio_level)

    def guide_point(self, x: float, y: float, label: str = ""):
        """A walkthrough step's arrow: there in GUIDE_FLY_S (doubled in slow
        mode), and held on the target until guide_release() - a step is not
        over until it is done."""
        self._hold_dwell = True
        self._locked_pos = QPointF(x, y)
        self._bubble_text = label or random.choice(POINTER_PHRASES)
        self._bubble_scale = 0.5
        self._bubble_alpha = 0.0
        self._ring = (x, y, 26.0)
        self._ring_phase = 0.0
        self._lock_timer.stop()
        self._begin_flight(self._display_pos, self._locked_pos, _PHASE_FLYING,
                           duration=GUIDE_FLY_S)

    def guide_release(self):
        """The step changed or the walkthrough ended: the arrow goes home."""
        self.release_point()

    def point_at(self, x: float, y: float, label: str = ""):
        """Fly to an on-screen target at teacher pace, dwell, then fly back.

        (x, y) is the EXACT pixel of the UI element in logical screen space
        (same space Qt's QCursor.pos() uses). The buddy lands with the tip of
        its triangle on that pixel — the highlight ring marks the exact spot."""
        # Buddy's tip should sit on the target pixel. The triangle is drawn
        # centred on _display_pos, so we just plant _display_pos there.
        self._locked_pos = QPointF(x, y)
        self._bubble_text = label or random.choice(POINTER_PHRASES)
        self._bubble_scale = 0.5
        self._bubble_alpha = 0.0
        # Halo ring around the actual target pixel
        self._ring = (x, y, 26.0)
        self._ring_phase = 0.0
        self._lock_timer.stop()   # dwell controlled by phase machine, not timer
        self._begin_flight(self._display_pos, self._locked_pos, _PHASE_FLYING)

    def set_activity(self, key: str, message: str):
        """Show what the assistant is doing ("" clears it)."""
        if key != self._activity_key or message != self._activity_text:
            self._activity_at = time.monotonic()
        self._activity_key = key or ""
        self._activity_text = message or ""

    def set_guide_progress(self, current: int, total: int, label: str = ""):
        """Step N of M for the island. total == 0 hides it; a negative total
        means the plan is still being written, and shows "Step N" alone."""
        was_running = self._guide_total != 0
        self._guide_cur = int(current)
        self._guide_total = int(total)
        self._guide_label = label or ""
        if was_running and self._guide_total == 0:
            # Guide just ended. Anything it drew was exempt from expiry while
            # it ran; give it a short grace period and then let it fade, so a
            # finished walkthrough does not leave marks on the desktop.
            now = time.monotonic()
            for ann in self._annotations:
                if ann.get("ttl") is None:
                    ann["start"] = now
                    ann["dur"] = min(ann.get("dur", 0.6), 0.2)
                    ann["ttl"] = GUIDE_LINGER_S

    def _island_lines(self):
        """(headline, subline) for the island, or None when idle.

        The headline escalates on its own the longer one activity runs. A
        static "Thinking…" that sits there for six seconds is exactly what
        makes an assistant feel hung; changing the words proves it is alive
        even when there is nothing new to report.
        """
        head = self._activity_text
        if head:
            age = time.monotonic() - self._activity_at
            if age > 8.0:
                head = "Still working on it…"
            elif age > 3.5:
                head = f"{head.rstrip('.… ')} — still going…"
        sub = ""
        if self._guide_total != 0:
            sub = (f"Step {self._guide_cur} of {self._guide_total}"
                   if self._guide_total > 0 else f"Step {self._guide_cur}")
            if self._guide_label:
                sub += f" · {self._guide_label}"
        if not head and not sub:
            return None
        return head or "Working…", sub

    # ── Caption island slots ──────────────────────────────────────────────
    # Reached ONLY through Qt signal connections in main.py. The emitters run
    # on the asyncio loop thread; a queued connection delivers here on the
    # main thread. Never call these directly from a coroutine.

    def caption_chunk(self, text: str) -> None:
        """Slot for CompanionManager.sig_response_chunk."""
        self._caption.push(text)

    def caption_done(self, text: str = "") -> None:
        """Slot for CompanionManager.sig_response_done."""
        self._caption.finish(text)

    def caption_state(self, state_name: str) -> None:
        """Takes the AppState NAME. overlay.py deliberately does not import
        AppState from ui.panel."""
        self._caption.on_state(state_name)

    def set_voice_clock(self, clock) -> None:
        """CompanionManager.voice_clock: how far Tora's live voice has got,
        so the caption's words keep time with it. Called on this thread."""
        self._caption.set_voice_clock(clock)

    def _caption_screen_rect(self) -> QRectF:
        """The MAIN monitor's work area in logical px - the island is always
        at the top of the main screen. availableGeometry, not geometry, so a
        taskbar docked at the top never hides it. (It used to follow the
        window in front, matched to a screen by device name - and Qt names
        most screens after the monitor model, so that match failed and it
        followed the mouse instead.)"""
        return QRectF(QApplication.primaryScreen().availableGeometry())

    def set_slow_mode(self, enabled: bool):
        """Doubles flight + dwell duration so students can track the motion."""
        self._slow_mode = enabled

    def set_point_hold(self, hold: bool):
        """Called by manager when TTS starts (True) / ends (False).
        While held, dwell never auto-expires — the buddy stays on the element
        the entire time Tora speaks."""
        self._hold_dwell = hold
        if hold and self._flight_phase == _PHASE_DWELLING:
            self._dwell_until = float("inf")

    def release_point(self):
        """Manager signals that TTS is done — fly buddy back to cursor now.
        Teaching drawings are NOT cleared here — they persist so the student
        can keep studying them; the manager clears them on the next query."""
        self._hold_dwell = False
        if self._flight_phase == _PHASE_DWELLING:
            self._dwell_until = 0.0   # expires this tick → triggers return
        # Fade ring on next paint
        self._ring = None

    # ── Teaching annotations ─────────────────────────────────────────────────

    def add_shape(self, shape: dict):
        """Queue a shape for progressive drawing. Logical screen coords.

        shape: {"kind": "line"|"arrow"|"circle"|"rect"|"poly"|"text"|
                        "underline"|"angle",
                ...kind-specific fields...,
                "color": palette name (optional),
                "ttl": seconds or None (None = persist until cleared)}

        Shapes animate in arrival order: each waits for the previous stroke
        to finish, like a hand drawing on a board.
        """
        now = time.monotonic()
        start = max(now, self._draw_queue_end)
        shape = dict(shape)
        # Draw at hand speed — long strokes take visibly longer
        dur = _shape_length(shape) / STROKE_SPEED_PX_S
        dur = max(SHAPE_DRAW_MIN_S, min(SHAPE_DRAW_MAX_S, dur))
        shape["start"] = start
        shape["dur"] = dur
        shape.setdefault("color", "tora")             # Tora's colour, read when it is painted
        # Auto-expiry unless a guide is running. Nothing ever passed a ttl, so
        # every drawing used to persist until the next question — which is why
        # arrows and circles piled up on screen long after they were relevant.
        if shape.get("ttl") is None:
            shape["ttl"] = None if self._guide_total != 0 else ANNOTATION_TTL_S
        self._draw_queue_end = start + dur + SHAPE_GAP_SECONDS
        self._annotations.append(shape)

    # Legacy single-shape helpers (kept for compatibility with old signals)
    def add_arrow(self, x1, y1, x2, y2, ttl=None):
        self.add_shape({"kind": "arrow", "pts": [(x1, y1), (x2, y2)], "ttl": ttl})

    def add_circle(self, x, y, radius=30.0, ttl=None):
        self.add_shape({"kind": "circle", "x": x, "y": y, "r": radius, "ttl": ttl})

    def add_underline(self, x, y, width, ttl=None):
        self.add_shape({"kind": "underline", "x": x, "y": y, "w": width, "ttl": ttl})

    def add_text(self, x, y, text, ttl=None):
        self.add_shape({"kind": "text", "x": x, "y": y, "text": text,
                        "size": "s", "ttl": ttl})

    def clear_annotations(self):
        self._annotations = []
        self._draw_queue_end = 0.0
        self._last_tip = None
        self._last_stroke = None

    def _active_stroke_tip(self):
        """Pen tip of the currently-animating shape, or None. The buddy
        rides this point so Tora visibly draws each stroke."""
        now = time.monotonic()
        active = None
        for ann in self._annotations:
            if ann["start"] <= now < ann["start"] + ann["dur"]:
                if active is None or ann["start"] > active["start"]:
                    active = ann
        if active is None:
            return None
        self._last_stroke = active
        u = (now - active["start"]) / active["dur"]
        return _stroke_tip(active, u)

    def drawn_now(self):
        """What is on screen now, for the answer card to keep off (SP-12):
        every drawing still showing, and where the pointing ring is (or
        None)."""
        now = time.monotonic()
        shapes = [a for a in self._annotations if a.get("ttl") is None or now - a["start"] <= a["ttl"]]
        ring = (self._ring[0], self._ring[1]) if self._ring is not None else None
        return shapes, ring

    def _begin_flight(self, start: QPointF, end: QPointF, phase: str,
                      duration: Optional[float] = None):
        self._pace(TICK_MS)                 # a flight starts on the next 16 ms, not 50
        dx, dy = end.x() - start.x(), end.y() - start.y()
        dist = math.hypot(dx, dy)
        if duration is not None:
            # Walkthrough pace (LG-02): a fixed blink, doubled in slow mode.
            dur = duration * (2.0 if self._slow_mode else 1.0)
        else:
            mult = 1.7 if self._slow_mode else 1.0
            # Scale duration by distance so short hops don't feel sluggish
            dur = max(FLY_DURATION_MIN, min(FLY_DURATION_MAX, FLY_DURATION_MIN + dist / 700.0))
            if phase == _PHASE_RETURNING:
                dur = RETURN_DURATION
            dur *= mult
        # Arc up over the midpoint
        mid = QPointF((start.x() + end.x()) / 2, (start.y() + end.y()) / 2)
        arc_height = min(dist * 0.22, 90.0)
        self._fly_control   = QPointF(mid.x(), mid.y() - arc_height)
        self._fly_start_pos = QPointF(start.x(), start.y())
        self._fly_end_pos   = QPointF(end.x(), end.y())
        self._fly_t0 = time.monotonic()
        self._fly_duration = dur
        self._flight_phase = phase
        self._vel = QPointF(0, 0)

    def hide_cursor(self):
        self._release_lock()
        self.set_mode(MODE_IDLE)

    # ── Internal ──────────────────────────────────────────────────────────────

    # ── One window per monitor ───────────────────────────────────────────────

    def show(self):
        """Show the overlay: every monitor's window. This widget itself is
        only the controller and is never shown."""
        self._shown = True
        for s in self._surfaces:
            s.show()
        self._refresh(force=True)

    def hide(self):
        self._shown = False
        for s in self._surfaces:
            s.hide()

    _layout_seen = False

    def _screens_changed(self, *_args) -> None:
        # Coalesced: plugging a monitor in fires several of these at once.
        if not self._sync_pending:
            self._sync_pending = True
            QTimer.singleShot(0, self._sync_surfaces)

    def _sync_surfaces(self) -> None:
        """One window for each monitor there is now, each over its monitor."""
        self._sync_pending = False
        screens = list(QApplication.screens())
        mine = {id(s.qscreen): s for s in self._surfaces}
        surfaces = []
        for scr in screens:
            surf = mine.pop(id(scr), None) or _Surface(self, scr)
            surf.place(scr)
            if self._shown:
                surf.show()
            surfaces.append(surf)
            if id(scr) not in self._watched_screens:
                self._watched_screens.add(id(scr))
                scr.geometryChanged.connect(self._screens_changed)
                scr.availableGeometryChanged.connect(self._screens_changed)
        for gone in mine.values():                 # unplugged
            gone.hide()
            gone.deleteLater()
        self._surfaces = surfaces
        # Tell the rest of Tora where Qt puts each monitor, so its arrows
        # land in the same place the overlay draws them (screen/monitors.py).
        moved = monitors.publish([(g.x(), g.y(), g.width(), g.height())
                                  for g in (s.geometry() for s in screens)])
        if moved and self._layout_seen:
            # PT-10: marks drawn for the old layout point at the wrong place
            # now - gone before the next one is drawn.
            self.clear_annotations()
            self.release_point()
        self._layout_seen = True
        self._cap_screen = None                    # the main monitor may have changed
        self._caption.needs_screen = self._caption.active()
        self._refresh(force=True)

    def _annotations_moving(self) -> bool:
        """A drawing is being drawn in or fading out, so it changes every
        frame wherever it is. One that has been drawn and sits there does not."""
        now = time.monotonic()
        for a in self._annotations:
            start = a["start"]
            if start - 0.1 <= now <= start + a.get("dur", 0.6) + 0.1:
                return True
            ttl = a.get("ttl")
            if ttl is not None and now - start > ttl * 0.75:
                return True
        return False

    def _regions(self) -> Optional[list]:
        """Where on the desktop something is drawn now - every pixel of it,
        with a margin - or None when the drawings change and every monitor
        has to be painted whole."""
        if len(self._annotations) != self._ann_painted or self._annotations_moving():
            return None
        d = self._display_pos
        cx, cy = d.x(), d.y()
        out = []
        if self._buddy_visible():
            out.append(QRectF(cx - BUDDY_REACH, cy - BUDDY_REACH, 2 * BUDDY_REACH, 2 * BUDDY_REACH))
            if self._locked_pos is not None and self._bubble_text:
                for words in {self._bubble_text, IN_CONTROL_CUE}:
                    out.append(buddy.bubble_rect(cx, cy, words).adjusted(-8, -8, 8, 10))
            lines = self._island_lines()
            if lines and self._island_alpha >= 0.02:
                out.append(self._island_box(cx, cy, *lines)[0].adjusted(-12, -12, 30, 12))
        if self._ring is not None:
            rx, ry, r = self._ring
            out.append(QRectF(rx - r - 30, ry - r - 30, 2 * r + 60, 2 * r + 60))
        if self._caption.active():
            out.append(self._caption.bounds())
        if self._mist.active():
            mist = self._mist.bounds()
            if mist is not None:
                out.append(mist)
        return out

    def _refresh(self, force: bool = False) -> None:
        """Repaint what is drawn now and what was drawn at the last frame, to
        rub it out - and nothing else.

        It used to repaint every monitor with anything on it, whole, 60 times
        a second - and the buddy is always on one. These are see-through
        windows, so each frame Windows took a full-monitor picture from Tora
        and composed it into the desktop: a third of a core here at 1536x864,
        many times that on a 4K screen or three monitors, and the desktop
        compositor (DWM) shared out with every app. On slower PCs the whole
        screen stopped keeping up and the PC looked frozen. The buddy is
        80px across; now that is what is painted."""
        regions = None if force else self._regions()
        self._ann_painted = len(self._annotations)
        for s in self._surfaces:
            area = s.area()
            if regions is None:
                rects = [QRect(0, 0, s.width(), s.height())]
            else:
                rects = []
                for r in regions:
                    hit = r.intersected(area)
                    if not hit.isEmpty():
                        rects.append(hit.translated(-area.x(), -area.y()).toAlignedRect())
            for r in rects + s.rects:
                s.update(r)
            s.rects = rects

    def _buddy_visible(self) -> bool:
        """Is she drawn now? Always, unless her cursor is hidden - and then
        only while she flies to, or rests on, what she points at."""
        return _CURSOR_SHOWN or self._flight_phase in (_PHASE_FLYING, _PHASE_DWELLING)

    def _release_lock(self):
        self._locked_pos = None
        self._bubble_text = ""
        self._bubble_alpha = 0.0
        self._bubble_scale = 0.5
        self._flight_phase = _PHASE_FOLLOW
        self._flight_scale = 1.0
        self._rotation_deg = TRI_ROTATION_DEG
        self._hold_dwell = False
        self._ring = None

    def in_control(self, now: Optional[float] = None) -> bool:
        """PT-17: a walkthrough is on and the user is moving the mouse."""
        now = time.monotonic() if now is None else now
        return self._guide_total != 0 and now - self._user_moved_at < IN_CONTROL_S

    def _bubble_now(self) -> str:
        """The bubble's words: the target's, or that the mouse is theirs."""
        if self._bubble_text and self._flight_phase in (_PHASE_FLYING, _PHASE_DWELLING) and self.in_control():
            return IN_CONTROL_CUE
        return self._bubble_text

    def _tick(self):
        qp = QCursor.pos()
        real = QPointF(qp.x(), qp.y())
        now = time.monotonic()
        last = self._last_real
        if last is not None and abs(real.x() - last.x()) + abs(real.y() - last.y()) > IN_CONTROL_PX:
            self._user_moved_at = now
        self._last_real = real
        since_tick = now - self._last_tick_at if self._last_tick_at else 0.0
        self._last_tick_at = now
        # Before any phase returns early: the mist follows the real mouse.
        self._mist.tick(real.x(), real.y(), time.monotonic())

        # ── Caption island ── before the flight machine: it returns early in
        # the pointing states, which is precisely when Tora is talking.
        cap = self._caption
        if cap.active():
            try:
                if cap.needs_screen or self._cap_screen is None:
                    self._cap_screen = self._caption_screen_rect()
                    cap.set_screen_width(self._cap_screen.width())
                    cap.needs_screen = False
                cap.tick(time.monotonic(), self._cap_screen, 0.0, 0.0)   # desktop coordinates
            except Exception:
                cap.clear()

        # State that only changes with time, advanced here once a frame - not
        # in painting, which now runs once per monitor.
        lines = self._island_lines()
        self._island_alpha += ((1.0 if lines else 0.0) - self._island_alpha) * 0.18
        self._prune_annotations()

        # ── Pointing phase machine ──
        if self._flight_phase == _PHASE_FLYING and self.in_control(now):
            self._fly_t0 += since_tick      # PT-17: the flight waits where it is
        if self._flight_phase in (_PHASE_FLYING, _PHASE_RETURNING):
            elapsed = time.monotonic() - self._fly_t0
            lp = min(1.0, elapsed / max(0.001, self._fly_duration))
            # Smoothstep easing — gentle start and end, teacher-friendly
            t = lp * lp * (3.0 - 2.0 * lp)
            omt = 1.0 - t
            bx = omt * omt * self._fly_start_pos.x() \
                 + 2 * omt * t * self._fly_control.x() \
                 + t * t * self._fly_end_pos.x()
            by = omt * omt * self._fly_start_pos.y() \
                 + 2 * omt * t * self._fly_control.y() \
                 + t * t * self._fly_end_pos.y()
            self._display_pos = QPointF(bx, by)
            # Rotate to tangent so the triangle "leans into" the flight
            tgx = 2 * omt * (self._fly_control.x() - self._fly_start_pos.x()) \
                  + 2 * t * (self._fly_end_pos.x() - self._fly_control.x())
            tgy = 2 * omt * (self._fly_control.y() - self._fly_start_pos.y()) \
                  + 2 * t * (self._fly_end_pos.y() - self._fly_control.y())
            self._rotation_deg = math.degrees(math.atan2(tgy, tgx)) + 90.0
            # Swoop scale pulse at midpoint
            self._flight_scale = 1.0 + math.sin(lp * math.pi) * 0.25
            # Bubble eases in during the flight
            if self._flight_phase == _PHASE_FLYING:
                self._bubble_alpha = min(1.0, lp * 1.4)
                self._bubble_scale = 0.5 + lp * 0.5
            if lp >= 1.0:
                if self._flight_phase == _PHASE_FLYING:
                    self._flight_phase = _PHASE_DWELLING
                    if self._hold_dwell:
                        self._dwell_until = float("inf")
                    else:
                        mult = 1.7 if self._slow_mode else 1.0
                        self._dwell_until = time.monotonic() + DWELL_SECONDS * mult
                    self._flight_scale = 1.0
                    self._rotation_deg = TRI_ROTATION_DEG
                else:  # RETURNING
                    self._release_lock()
            self._phase += 0.10
            self._pace(TICK_MS)
            self._refresh()
            return

        if self._flight_phase == _PHASE_DWELLING:
            # Gentle breathing pulse while the LLM explains
            breathe = 1.0 + 0.05 * math.sin(self._phase * 1.4)
            self._flight_scale = breathe
            self._bubble_alpha = min(1.0, self._bubble_alpha + 0.05)
            self._bubble_scale = self._bubble_scale + (1.0 - self._bubble_scale) * 0.15
            # Stay planted on the element
            self._display_pos = QPointF(self._locked_pos.x(), self._locked_pos.y())
            if time.monotonic() >= self._dwell_until:
                if not _CURSOR_SHOWN:
                    self._release_lock()        # hidden: done pointing, she is simply gone
                else:
                    cursor_target = QPointF(real.x() + OFFSET_X, real.y() + OFFSET_Y)
                    self._begin_flight(self._display_pos, cursor_target, _PHASE_RETURNING)
                    # Fade bubble out during return
                    self._bubble_alpha = 0.0
            self._phase += 0.10
            self._pace(TICK_MS)
            self._refresh()
            return

        # ── Drawing mode: buddy rides the pen tip of the active stroke, and
        # holds at the last tip between strokes while the lesson is still
        # playing (no yo-yo back to the mouse cursor mid-lesson) ──
        tip = self._active_stroke_tip()
        if tip is not None:
            self._last_tip = tip
        elif time.monotonic() >= self._draw_queue_end + 2.5:
            self._last_tip = None   # lesson over — resume cursor follow
            self._last_stroke = None
        elif self._last_tip is not None and self._last_stroke is not None:
            # Between strokes and after the last: off the arrowhead (SP-11).
            self._last_tip = _rest_point(self._last_stroke) or self._last_tip
        hold = tip or self._last_tip
        if hold is not None:
            # Strong pull toward the pen tip — smooth but keeps up with it
            tx, ty = hold[0] + 6, hold[1] + 6
            self._display_pos = QPointF(
                self._display_pos.x() + (tx - self._display_pos.x()) * 0.45,
                self._display_pos.y() + (ty - self._display_pos.y()) * 0.45,
            )
            self._vel = QPointF(0, 0)
            self._phase += 0.10
            self._pace(TICK_MS)
            self._refresh()
            return

        # ── Normal cursor-follow spring ──
        target = QPointF(real.x() + OFFSET_X, real.y() + OFFSET_Y)
        stiffness, damping = 0.28, 0.62

        # Onto another monitor: jump there instead of gliding. Monitors at
        # different scalings do not meet edge to edge in Qt's coordinates -
        # between a 100% and a 125% monitor there is a strip no window
        # covers - so a glide vanished into it and lagged in from the side.
        scr = QApplication.screenAt(qp)
        if scr is not None and scr is not self._pointer_screen:
            if self._pointer_screen is not None:
                self._display_pos = QPointF(target)
                self._vel = QPointF(0, 0)
            self._pointer_screen = scr

        # Spring
        ax = (target.x() - self._display_pos.x()) * stiffness
        ay = (target.y() - self._display_pos.y()) * stiffness
        self._vel = QPointF(self._vel.x() * damping + ax,
                            self._vel.y() * damping + ay)
        self._display_pos = QPointF(
            self._display_pos.x() + self._vel.x(),
            self._display_pos.y() + self._vel.y(),
        )

        self._phase += 0.10
        self._spin_phase += 0.14  # ~0.8s per rev @ 60fps matches Swift
        self._ring_phase += 0.08

        # At rest under a still mouse, with nothing else on show, all that
        # changes is the glow's slow breath (~3s): every third frame shows it
        # just as well, at a third of the cost - and Tora is at rest most of
        # the day.
        settled = (abs(self._vel.x()) + abs(self._vel.y()) < 0.05
                   and abs(target.x() - self._display_pos.x())
                   + abs(target.y() - self._display_pos.y()) < 0.5)
        quiet = (settled and self._mode in (MODE_IDLE, MODE_SPEAKING)
                 and not self._annotations and not self._caption.active() and not self._mist.active()
                 and self._island_alpha < 0.02 and self._locked_pos is None)
        self._rest_frames = self._rest_frames + 1 if quiet else 0
        if self._rest_frames > REST_AFTER:
            self._pace(REST_TICK_MS)        # every frame painted: 20 a second
        else:
            self._pace(TICK_MS)
            if self._rest_frames % 3:
                return
        self._refresh()

    def _pace(self, ms: int) -> None:
        """The overlay's clock: fast while anything moves, slow at rest."""
        timer = self._tick_timer
        if timer.interval() != ms:
            timer.setInterval(ms)

    # ── Painting ──────────────────────────────────────────────────────────────

    def _paint(self, p: QPainter) -> None:
        """The whole scene, in desktop coordinates. Called by each monitor's
        window with its painter already moved to that monitor; the window's
        edges clip the rest. Painting only - nothing here changes state, as
        it runs once per monitor per frame."""
        # The mist goes under everything: it is where they pointed, not hers.
        self._mist.draw(p, time.monotonic())

        # Ring goes UNDER the buddy
        if self._ring is not None and self._flight_phase in (
            _PHASE_DWELLING, _PHASE_FLYING
        ):
            self._draw_ring(p)

        # Whiteboard annotations — drawn under the buddy too
        if self._annotations:
            self._draw_annotations(p)

        cx = self._display_pos.x()
        cy = self._display_pos.y()

        if self._buddy_visible():
            # The buddy's look is ui/buddy.py (the polished triangle, chosen 30 Sep).
            if self._mode == MODE_LISTENING:
                buddy.draw_listening(p, cx, cy, self._audio_level, self._phase * 0.5)
            elif self._mode == MODE_THINKING:
                buddy.draw_thinking(p, cx, cy, self._spin_phase * 0.36)
            else:
                # idle, speaking, pointing: its glow breathes, slowly
                buddy.draw_pointer(p, cx, cy, rotation=self._rotation_deg, scale=self._flight_scale,
                                   breath=0.5 + 0.5 * math.sin(self._phase * 0.37))

            if self._locked_pos is not None and self._bubble_text:
                buddy.draw_bubble(p, cx, cy, self._bubble_now(),
                                  alpha=self._bubble_alpha, scale=self._bubble_scale)

            self._draw_island(p, cx, cy)

        # Top caption — last, so its save/restore is never leaked into
        # by another pass's pen or clip.
        if self._caption.active():
            try:
                self._caption.draw(p)
            except Exception:
                pass

    @staticmethod
    def _island_box(cx: float, cy: float, head: str, sub: str):
        """(card rect, head font, sub font) of the status card above the
        buddy at (cx, cy) - one place, so what is painted and what is
        repainted (_regions) cannot disagree."""
        head_font, sub_font, fm_h, fm_s = _island_fonts()
        w = _ISLAND_DOT_W + max(
            fm_h.horizontalAdvance(head),
            fm_s.horizontalAdvance(sub) if sub else 0,
        ) + _ISLAND_PAD_X * 2
        h = fm_h.height() + (fm_s.height() + _ISLAND_GAP if sub else 0) + _ISLAND_PAD_Y * 2
        # Sit above the buddy, nudged so it never covers what we point at.
        return QRectF(cx + 16, cy - h - 20, w, h), head_font, sub_font

    def _draw_island(self, p, cx, cy):
        """A small status card above the buddy: what it's doing, and where
        you are in the guide. Fades in and out so it never snaps (the fade
        is advanced in _tick)."""
        lines = self._island_lines()
        if self._island_alpha < 0.02 or not lines:
            return
        head, sub = lines
        box, head_font, sub_font = self._island_box(cx, cy, head, sub)
        fm_h = QFontMetricsF(head_font)
        fm_s = QFontMetricsF(sub_font)
        pad_x, pad_y, gap = _ISLAND_PAD_X, _ISLAND_PAD_Y, _ISLAND_GAP
        dot_w = _ISLAND_DOT_W
        x, y, w, h = box.x(), box.y(), box.width(), box.height()
        a = self._island_alpha

        # Soft shadow + dark glass card
        shadow = QColor(0, 0, 0, int(70 * a))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(shadow))
        p.drawRoundedRect(QRectF(x - 2, y + 2, w + 4, h + 4), 12, 12)

        card = QColor(18, 20, 28, int(232 * a))
        p.setBrush(QBrush(card))
        p.drawRoundedRect(QRectF(x, y, w, h), 11, 11)
        edge = QColor(CURSOR_BLUE)
        edge.setAlpha(int(110 * a))
        p.setPen(QPen(edge, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(x, y, w, h), 11, 11)

        # Breathing sparkle — the proof-of-life
        t = time.monotonic()
        pulse = 0.55 + 0.45 * math.sin(t * 3.4)
        dot = QColor(CURSOR_BLUE)
        dot.setAlpha(int(255 * a))
        halo = QColor(CURSOR_BLUE)
        halo.setAlpha(int(90 * a * pulse))
        dcx, dcy = x + pad_x - 1, y + pad_y + fm_h.height() / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(halo))
        p.drawEllipse(QPointF(dcx, dcy), 6.5 * pulse + 2.5, 6.5 * pulse + 2.5)
        p.setBrush(QBrush(dot))
        p.drawEllipse(QPointF(dcx, dcy), 3.2, 3.2)

        tx = x + pad_x + dot_w
        p.setFont(head_font)
        p.setPen(QPen(QColor(238, 241, 248, int(255 * a)), 1))
        p.drawText(
            QRectF(tx, y + pad_y, w - dot_w - pad_x * 2, fm_h.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, head,
        )
        if sub:
            p.setFont(sub_font)
            sub_col = _tint(CURSOR_BLUE, 0.5)              # her colour, pale (the blue's was 150,190,255)
            sub_col.setAlpha(int(230 * a))
            p.setPen(QPen(sub_col, 1))
            p.drawText(
                QRectF(tx, y + pad_y + fm_h.height() + gap,
                       w - dot_w - pad_x * 2, fm_s.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, sub,
            )

    def _draw_annotations(self, p):
        """Render teaching shapes with progressive draw-in animation.

        Each shape animates from 0→100% over SHAPE_DRAW_SECONDS starting at
        its queued "start" time. Shapes with ttl=None persist at full alpha;
        shapes with a ttl fade out over their last 25%.
        """
        now = time.monotonic()
        for ann in self._annotations:
            u = (now - ann["start"]) / ann.get("dur", 0.6)
            if u <= 0:
                continue             # queued, not started yet
            u = min(1.0, u)

            alpha = 1.0
            ttl = ann.get("ttl")
            if ttl is not None:
                age = now - ann["start"]
                if age > ttl:
                    continue         # expired - _prune_annotations drops it
                if age > ttl * 0.75:
                    alpha = max(0.0, 1.0 - (age - ttl * 0.75) / (ttl * 0.25))

            col = annotation_colour(ann.get("color"))
            col.setAlpha(int(235 * alpha))
            kind = ann["kind"]
            try:
                if kind in ("line", "arrow"):
                    self._paint_stroke_path(p, ann["pts"], False, u, col,
                                            arrow=(kind == "arrow"))
                elif kind == "poly":
                    self._paint_stroke_path(p, ann["pts"], True, u, col)
                elif kind == "rect":
                    x1, y1, x2, y2 = ann["x1"], ann["y1"], ann["x2"], ann["y2"]
                    pts = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
                    self._paint_stroke_path(p, pts, True, u, col)
                elif kind == "circle":
                    self._paint_circle(p, ann, u, col, alpha)
                elif kind == "underline":
                    pts = [(ann["x"], ann["y"]), (ann["x"] + ann["w"], ann["y"])]
                    self._paint_stroke_path(p, pts, False, u, col)
                elif kind == "angle":
                    self._paint_angle(p, ann, u, col)
                elif kind == "text":
                    self._paint_text(p, ann, u, col, alpha)
            except Exception:
                continue   # a malformed shape must never break the paint loop

    def _prune_annotations(self) -> None:
        """Drop drawings whose time is up (once a frame, from _tick)."""
        if not self._annotations:
            return
        now = time.monotonic()
        self._annotations = [a for a in self._annotations
                             if a.get("ttl") is None or now - a["start"] <= a["ttl"]]

    def _pens(self, col, width=4.0):
        """(under, main) pen pair — dark under-stroke keeps lines readable
        over light content, like a marker outline."""
        under_col = QColor(0, 0, 0)
        under_col.setAlpha(min(110, col.alpha()))
        under = QPen(under_col, width + 2.5)
        under.setCapStyle(Qt.PenCapStyle.RoundCap)
        under.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        main = QPen(col, width)
        main.setCapStyle(Qt.PenCapStyle.RoundCap)
        main.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return under, main

    def _paint_stroke_path(self, p, pts, closed, u, col, arrow=False, width=4.0):
        """Draw the first u-fraction of a polyline (progressive stroke)."""
        pts = list(pts)
        drawn = _partial_pts(pts, closed, u)
        if len(drawn) < 2:
            return
        if closed and len(pts) >= 3:
            pts = pts + [pts[0]]
        path = QPainterPath(QPointF(*drawn[0]))
        for pt in drawn[1:]:
            path.lineTo(QPointF(*pt))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for pen in self._pens(col, width):
            p.setPen(pen)
            p.drawPath(path)
        # Arrowhead appears once the line is fully drawn
        if arrow and u >= 1.0 and len(pts) >= 2:
            (x1, y1), (x2, y2) = pts[-2], pts[-1]
            ang = math.atan2(y2 - y1, x2 - x1)
            head = 15
            for pen in self._pens(col, width):
                p.setPen(pen)
                for sign in (-1, 1):
                    ax = x2 - head * math.cos(ang + sign * math.radians(28))
                    ay = y2 - head * math.sin(ang + sign * math.radians(28))
                    p.drawLine(QPointF(x2, y2), QPointF(ax, ay))

    def _paint_circle(self, p, ann, u, col, alpha):
        x = ann["x"]
        y = ann["y"]
        r = ann["r"]
        rect = QRectF(x - r, y - r, 2 * r, 2 * r)
        p.setBrush(Qt.BrushStyle.NoBrush)
        # Sweep the arc clockwise from 12 o'clock as it draws in
        span = int(-360 * 16 * u)
        for pen in self._pens(col, 3.5):
            p.setPen(pen)
            p.drawArc(rect, 90 * 16, span)
        label = ann.get("label", "")
        if label and u >= 1.0:
            f = QFont("Segoe UI", 11, QFont.Weight.Bold)
            p.setFont(f)
            fm = p.fontMetrics()
            tx = x - fm.horizontalAdvance(label) / 2
            ty = y - r - 8
            self._outlined_text(p, tx, ty, label, col, alpha)

    def _paint_angle(self, p, ann, u, col):
        """Right-angle marker: a small square corner at (x,y), rotated rot°."""
        x, y = ann["x"], ann["y"]
        s = ann.get("s", 22)
        rot = math.radians(ann.get("rot", 0))
        d1 = (math.cos(rot), math.sin(rot))
        d2 = (math.cos(rot + math.pi / 2), math.sin(rot + math.pi / 2))
        p1 = (x + d1[0] * s, y + d1[1] * s)
        p2 = (x + (d1[0] + d2[0]) * s, y + (d1[1] + d2[1]) * s)
        p3 = (x + d2[0] * s, y + d2[1] * s)
        self._paint_stroke_path(p, [p1, p2, p3], False, u, col, width=3.0)

    def _paint_text(self, p, ann, u, col, alpha):
        x = ann["x"]
        y = ann["y"]
        pt_size = _TEXT_SIZES.get(ann.get("size", "m"), 17)
        font = QFont("Segoe UI", pt_size, QFont.Weight.Bold)
        p.setFont(font)
        # Ease in: fade + slight rise
        fade = u * alpha
        y += (1.0 - u) * 6
        self._outlined_text(p, x, y, ann["text"], col, fade)

    def _outlined_text(self, p, x, y, text, col, alpha):
        """Text with a dark outline so it reads on any background."""
        shadow = QColor(0, 0, 0)
        shadow.setAlpha(int(170 * alpha))
        p.setPen(QPen(shadow, 1))
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (1, 1)):
            p.drawText(QPointF(x + dx, y + dy), text)
        c = QColor(col)
        c.setAlpha(int(255 * alpha))
        p.setPen(QPen(c, 1))
        p.drawText(QPointF(x, y), text)

    def _draw_ring(self, p):
        """Pulsing blue ring around the detected UI element."""
        rx, ry, base_r = self._ring
        pulse = (math.sin(self._ring_phase) + 1) / 2   # 0..1
        r = base_r + pulse * 6
        # Outer halo
        glow = QColor(CURSOR_BLUE)
        glow.setAlpha(60)
        pen = QPen(glow, 4)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(rx, ry), r + 3, r + 3)
        # Crisp inner ring
        inner = QColor(CURSOR_BLUE)
        inner.setAlpha(190)
        pen = QPen(inner, 2)
        p.setPen(pen)
        p.drawEllipse(QPointF(rx, ry), r, r)
