"""The agents at work, top-right of the screen - one small living orb each.

Small, so they never cover what the user is doing, and alive, so they are
noticed: a glass disc with the agent's kind drawn in it (</> for building,
a magnifier for research, a folder for files...), a progress ring that
fills blue -> cyan -> green, a light that runs round the ring and two
sparks orbiting it while the agent works, a slow comet while it plans and
has no number yet. It springs in when an agent starts. When the job is
done a green shockwave rolls out, a tick draws itself, and the orb pops
away by itself; a failure shakes, red, and stays a little longer.

Hover one for its job, its current step and how far it has got - a small
glass label beside it, which never catches the mouse. Click one to open
that job in the HeyTora app (main.py wires `opened`).

10 Oct 2026: the orbs were plain discs with a letter; for a day they were
wide cards ("the big thing takes too much of the screen") - now small
again, but animated. And a finished one used to stay: the animation timer
stopped once the ring had filled, so the hold before fading was never
checked again. An orb now keeps its timer until it has gone.

Everything animates through one timer that runs only while something
moves; window flags copy `ui/overlay.py` - frameless, on top, tool window,
no focus stealing.
"""
from __future__ import annotations

import math
import time
from typing import Optional

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush, QColor, QConicalGradient, QFont, QFontMetricsF, QPainter, QPainterPath, QPen,
    QRadialGradient,
)
from PyQt6.QtWidgets import QApplication, QWidget

from agents.core import AgentState, AgentStatus

ORB = 40                 # disc diameter, px
RING = 3.4               # ring thickness
GAP = 14                 # vertical gap between orbs
GLOW = 14                # halo and sparks beyond the ring
MARGIN = 10              # from the screen's top-right corner
WIDTH = ORB + 2 * GLOW + 12
TOP = GLOW + 6
MAX_SHOWN = 6
HOLD_DONE_S = 2.8        # the shockwave and the tick, then it pops away
HOLD_FAIL_S = 6.0
HOLD_STOPPED_S = 1.2
FPS_MS = 16

# Progress 0 -> 1 sweeps blue -> cyan -> green.
_C_START = QColor(0, 120, 255)
_C_MID = QColor(0, 210, 230)
_C_END = QColor(50, 220, 110)
_AMBER = QColor(255, 184, 0)
_RED = QColor(255, 84, 84)


def _lerp(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(int(a.red() + (b.red() - a.red()) * t),
                  int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


def progress_colour(p: float) -> QColor:
    return _lerp(_C_START, _C_MID, p * 2) if p < 0.5 else _lerp(_C_MID, _C_END, (p - 0.5) * 2)


def _ease(current: float, target: float, rate: float = 0.16) -> float:
    return current + (target - current) * rate


def _spring(t: float) -> float:
    """0 -> 1 with a little overshoot (easeOutBack): the pop of an orb arriving."""
    t = max(0.0, min(1.0, t))
    c1 = 1.70158
    return 1 + (c1 + 1) * (t - 1) ** 3 + c1 * (t - 1) ** 2


def _font(px: float, weight: int = 500) -> QFont:
    """The app's face (ui/tokens.py), as the answer card sets it."""
    try:
        from ui import tokens
        family = tokens.font_family()
    except Exception:  # noqa: BLE001 - an orb is never stopped by a font
        family = "Segoe UI"
    f = QFont(family)
    f.setPixelSize(max(1, int(round(px))))
    f.setWeight(QFont.Weight(weight))
    return f


def kind_of(name: str) -> str:
    """Which icon an agent wears, from its name (AgentManager.AGENT_NAMES)."""
    n = (name or "").lower()
    for words, kind in ((("builder", "frontend", "backend", "testing", "cod"), "code"),
                        (("research",), "research"), (("vision",), "vision"),
                        (("browser", "web"), "browser"), (("file", "folder"), "file"),
                        (("terminal", "command"), "terminal"), (("computer",), "computer"),
                        (("quick", "fast"), "quick")):
        if any(w in n for w in words):
            return kind
    return "spark"


class Orb:
    """One agent's orb: what it shows and where its animation is. Pure
    data; the widget paints it."""

    def __init__(self, state: AgentState):
        self.agent_id = state.id
        self.task_id = getattr(state, "current_task_id", None)
        self.name = state.spec.name
        self.kind = kind_of(self.name)
        self.task_title = state.current_task_title
        self.action = state.current_action
        self.status = state.status
        self.target = max(0.0, min(1.0, state.progress))
        self.shown_progress = 0.0
        self.appear = 0.0               # 0 -> 1 on entry, -> 0 on exit
        self.leaving = False
        self.phase = 0.0                # the running light, the sparks, the comet
        self.finished_at: Optional[float] = None
        self.hovered = False
        self.hover = 0.0                # eased 0 -> 1

    def update(self, state: AgentState) -> None:
        self.name = state.spec.name
        self.kind = kind_of(self.name)
        self.task_title = state.current_task_title or self.task_title
        self.task_id = getattr(state, "current_task_id", None) or self.task_id
        self.action = state.current_action
        prev = self.status
        self.status = state.status
        self.target = max(0.0, min(1.0, state.progress))
        if self.status in (AgentStatus.IDLE, AgentStatus.ERROR, AgentStatus.STOPPED) \
                and prev not in (AgentStatus.IDLE, AgentStatus.ERROR, AgentStatus.STOPPED):
            self.finished_at = time.monotonic()
            if self.status is AgentStatus.IDLE:
                self.target = 1.0
        elif self.status in (AgentStatus.RUNNING, AgentStatus.PAUSED):
            self.finished_at = None          # a new job for the same agent: it stays
            self.leaving = False

    @property
    def done(self) -> bool:
        return self.status is AgentStatus.IDLE and self.finished_at is not None

    @property
    def failed(self) -> bool:
        return self.status is AgentStatus.ERROR

    @property
    def gone(self) -> bool:
        return self.leaving and self.appear <= 0.01

    def since_finished(self) -> float:
        return time.monotonic() - self.finished_at if self.finished_at is not None else -1.0

    def hold(self) -> float:
        return HOLD_FAIL_S if self.failed else HOLD_DONE_S if self.done else HOLD_STOPPED_S

    def tick(self, dt: float) -> bool:
        """Advance one frame. True while it still needs frames - including
        the hold of a finished orb: the timer must be running when the hold
        ends, or the orb never leaves."""
        moving = False
        if self.leaving:
            self.appear = max(0.0, self.appear - dt * 4.5)
            moving = self.appear > 0
        elif self.appear < 1.0:
            self.appear = min(1.0, self.appear + dt * 2.6)
            moving = True
        if abs(self.shown_progress - self.target) > 0.002:
            self.shown_progress = _ease(self.shown_progress, self.target)
            moving = True
        else:
            self.shown_progress = self.target
        want = 1.0 if self.hovered else 0.0
        if abs(self.hover - want) > 0.01:
            self.hover = _ease(self.hover, want, 0.3)
            moving = True
        else:
            self.hover = want
        if self.status is AgentStatus.RUNNING:
            self.phase = (self.phase + dt) % 1000.0
            moving = True
        if self.finished_at is not None and not self.leaving:
            if self.since_finished() > self.hold():
                self.leaving = True
            moving = True
        return moving

    def ring_colour(self) -> QColor:
        if self.failed:
            return QColor(_RED)
        if self.status is AgentStatus.PAUSED:
            return QColor(_AMBER)
        if self.done:
            return QColor(_C_END)
        return progress_colour(self.shown_progress)

    # -- what the hover label says -------------------------------------------------

    def title(self) -> str:
        return (self.task_title or self.name or "Agent").strip()

    def line(self) -> str:
        if self.done:
            return "Done · click to see it"
        if self.failed:
            return "Didn't finish · click to see why"
        if self.status is AgentStatus.STOPPED:
            return "Stopped"
        if self.status is AgentStatus.PAUSED:
            return "Paused"
        if self.shown_progress <= 0.005 and not self.action:
            return "Planning the steps…"
        return (self.action or "Working…").strip()

    def line_colour(self) -> QColor:
        if self.done:
            return QColor(_C_END)
        if self.failed:
            return QColor(_RED)
        if self.status is AgentStatus.PAUSED:
            return QColor(_AMBER)
        return QColor(170, 170, 178)


class _Label(QWidget):
    """The hover label: glass, beside the orb, never in the mouse's way."""

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus
                            | Qt.WindowType.NoDropShadowWindowHint
                            | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.orb: Optional[Orb] = None
        self.alpha = 0.0

    def show_for(self, orb: Orb, right: float, centre_y: float, alpha: float) -> None:
        self.orb, self.alpha = orb, alpha
        title_f, line_f = _font(13, 600), _font(11.5, 500)
        pct = "" if orb.done or orb.failed else f"{int(round(orb.shown_progress * 100))}%"
        text_w = max(QFontMetricsF(title_f).horizontalAdvance(orb.title()),
                     QFontMetricsF(line_f).horizontalAdvance(orb.line()))
        w = int(min(300, text_w + 34 + (40 if pct else 0)))
        h = 52
        self.resize(w + 20, h + 20)
        self.move(int(right - (w + 20) + 6 - 6 * alpha), int(centre_y - (h + 20) / 2))
        if not self.isVisible():
            self.show()
        self.update()

    def paintEvent(self, _e) -> None:
        orb = self.orb
        if orb is None or self.alpha <= 0.01:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setOpacity(self.alpha)
        r = QRectF(10, 10, self.width() - 20, self.height() - 20)
        p.setPen(Qt.PenStyle.NoPen)
        for i, al in enumerate((26, 14)):
            g = 2.0 + i * 3.0
            p.setBrush(QColor(0, 0, 0, al))
            p.drawRoundedRect(r.adjusted(-g, -g + 2, g, g + 2), 14 + g, 14 + g)
        p.setBrush(QColor(0, 0, 0, 236))
        p.setPen(QPen(QColor(255, 255, 255, 30), 1.0))
        p.drawRoundedRect(r, 14, 14)
        pct = "" if orb.done or orb.failed else f"{int(round(orb.shown_progress * 100))}%"
        text_r = r.adjusted(14, 0, -(46 if pct else 14), 0)
        title_f, line_f = _font(13, 600), _font(11.5, 500)
        p.setFont(title_f)
        p.setPen(QColor(244, 244, 247))
        p.drawText(QRectF(text_r.left(), r.top() + 7, text_r.width(), 20), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetricsF(title_f).elidedText(orb.title(), Qt.TextElideMode.ElideRight, text_r.width()))
        p.setFont(line_f)
        p.setPen(orb.line_colour())
        p.drawText(QRectF(text_r.left(), r.top() + 26, text_r.width(), 18), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetricsF(line_f).elidedText(orb.line(), Qt.TextElideMode.ElideRight, text_r.width()))
        if pct:
            p.setFont(_font(13, 700))
            p.setPen(orb.ring_colour())
            p.drawText(QRectF(r.right() - 48, r.top(), 40, r.height()),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, pct)
        p.end()


class AgentOrbs(QWidget):
    """The floating column of agent orbs. Feed it AgentStates."""

    clicked = pyqtSignal(str)            # agent_id
    opened = pyqtSignal(str, str)        # agent_id, task_id (or "") - open that job

    def __init__(self, manager=None, bridge=None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self._orbs: list[Orb] = []
        self._label: Optional[_Label] = None
        self._timer = QTimer(self)
        self._timer.setInterval(FPS_MS)
        self._timer.timeout.connect(self._tick)
        self._last = time.monotonic()
        self._manager = manager
        self._bridge = None
        if manager is not None:
            from .bridge import AgentEventBridge
            self._bridge = bridge or AgentEventBridge(manager, self)
            self._bridge.agent_changed.connect(self.on_agent_changed)
            self._bridge.task_progress.connect(self.on_task_progress)
            for state in manager.list_agents(active_only=True):
                self.on_agent_changed(state)
        self._relayout()

    # -- feed ----------------------------------------------------------------

    def on_agent_changed(self, state: AgentState) -> None:
        orb = next((o for o in self._orbs if o.agent_id == state.id), None)
        if orb is None:
            if state.status not in (AgentStatus.RUNNING, AgentStatus.PAUSED):
                return               # finished before we ever showed it
            self._orbs.append(Orb(state))
        else:
            orb.update(state)
        self._relayout()
        self._start()

    def on_task_progress(self, task_id: str, fraction: float, action: str) -> None:
        if self._manager is None:
            return
        for orb in self._orbs:
            agent = self._manager.get_agent(orb.agent_id)
            if agent and agent.current_task_id == task_id:
                orb.task_id = task_id
                orb.target = max(0.0, min(1.0, fraction))
                if action:
                    orb.action = action
                self._start()
                return

    # -- geometry ------------------------------------------------------------

    def _shown(self) -> list[Orb]:
        return self._orbs[:MAX_SHOWN]

    def _relayout(self) -> None:
        n = max(1, len(self._shown()))
        height = n * ORB + (n - 1) * GAP + 2 * TOP
        self.resize(WIDTH, height)
        screen = QApplication.primaryScreen()
        if screen is not None:
            rect = screen.availableGeometry()
            self.move(rect.right() - WIDTH - MARGIN + 1, rect.top() + MARGIN)
        visible = bool(self._orbs)
        if visible and not self.isVisible():
            self.show()
        elif not visible and self.isVisible():
            self.hide()
            if self._label is not None:
                self._label.hide()

    def _centre(self, index: int) -> QPointF:
        return QPointF(WIDTH / 2, TOP + ORB / 2 + index * (ORB + GAP))

    def _orb_at(self, pos) -> Optional[Orb]:
        for i, orb in enumerate(self._shown()):
            c = self._centre(i)
            if math.hypot(pos.x() - c.x(), pos.y() - c.y()) <= ORB / 2 + RING + 2:
                return orb
        return None

    # -- animation -----------------------------------------------------------

    def _start(self) -> None:
        if not self._timer.isActive():
            self._last = time.monotonic()
            self._timer.start()

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.1, now - self._last)
        self._last = now
        moving = False
        for orb in self._orbs:
            moving = orb.tick(dt) or moving
        before = len(self._orbs)
        self._orbs = [o for o in self._orbs if not o.gone]
        if len(self._orbs) != before:
            self._relayout()
        self._place_label()
        self.update()
        if not moving:
            self._timer.stop()

    def _place_label(self) -> None:
        shown = self._shown()
        best = max(shown, key=lambda o: o.hover, default=None)
        if best is None or best.hover <= 0.02:
            if self._label is not None and self._label.isVisible():
                self._label.hide()
            return
        if self._label is None:
            self._label = _Label()
        i = shown.index(best)
        c = self.mapToGlobal(self._centre(i).toPoint())
        self._label.show_for(best, c.x() - ORB / 2 - 10, c.y(), best.hover)

    # -- painting ------------------------------------------------------------

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i, orb in enumerate(self._shown()):
            self._paint_orb(p, orb, self._centre(i))
        p.end()

    def _paint_orb(self, p: QPainter, orb: Orb, c: QPointF) -> None:
        a = orb.appear
        if a <= 0:
            return
        scale = _spring(a) if not orb.leaving else 0.55 + 0.45 * a
        scale *= 1.0 + 0.08 * orb.hover
        since = orb.since_finished()
        if orb.failed and 0 <= since < 0.6:            # a short shake
            c = QPointF(c.x() + math.sin(since * 45) * 3.5 * (1 - since / 0.6), c.y())
        r = (ORB / 2) * scale
        colour = orb.ring_colour()
        p.setOpacity(min(1.0, a * 1.4))
        running = orb.status is AgentStatus.RUNNING

        # the glow: breathes while it works
        breath = 0.55 + 0.45 * math.sin(orb.phase * 2.4) if running else 0.6
        halo = QRadialGradient(c, r + GLOW)
        h = QColor(colour)
        h.setAlpha(int(110 * breath))
        halo.setColorAt(0.62, h)
        h2 = QColor(colour)
        h2.setAlpha(0)
        halo.setColorAt(1.0, h2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(halo))
        p.drawEllipse(c, r + GLOW, r + GLOW)

        # done: a shockwave rolls out
        if orb.done and 0 <= since < 0.8:
            t = since / 0.8
            wave = QColor(_C_END)
            wave.setAlpha(int(200 * (1 - t)))
            p.setPen(QPen(wave, 2.4 * (1 - t) + 0.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            rr = r + 4 + 20 * (1 - (1 - t) ** 3)
            p.drawEllipse(c, rr, rr)

        # the disc: dark glass, tinted when it has ended
        inner, outer = QColor(46, 46, 58), QColor(13, 13, 17)
        if orb.done:
            inner, outer = QColor(26, 74, 44), QColor(9, 26, 16)
        elif orb.failed:
            inner, outer = QColor(80, 28, 30), QColor(26, 10, 11)
        disc = QRadialGradient(QPointF(c.x() - r * 0.35, c.y() - r * 0.4), r * 1.5)
        disc.setColorAt(0.0, inner)
        disc.setColorAt(1.0, outer)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(disc))
        p.drawEllipse(c, r - RING * 0.5, r - RING * 0.5)
        # a glint, top left
        glint = QRadialGradient(QPointF(c.x() - r * 0.35, c.y() - r * 0.45), r * 0.6)
        glint.setColorAt(0.0, QColor(255, 255, 255, 34))
        glint.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(QBrush(glint))
        p.drawEllipse(c, r - RING, r - RING)

        # the ring: track, then progress - or a comet while it plans
        rect = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 30), RING))
        p.drawEllipse(rect)
        planning = running and orb.shown_progress <= 0.005
        if planning:
            grad = QConicalGradient(c, -orb.phase * 260 % 360)
            faint = QColor(colour)
            faint.setAlpha(0)
            grad.setColorAt(0.0, colour)
            grad.setColorAt(0.4, faint)
            grad.setColorAt(1.0, faint)
            p.setPen(QPen(QBrush(grad), RING, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawEllipse(rect)
        else:
            pen = QPen(colour, RING)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            span = int(-360 * 16 * (1.0 if orb.done or orb.failed else orb.shown_progress))
            p.drawArc(rect, 90 * 16, span)
            if running:
                # a light runs round the ring
                sheen = QConicalGradient(c, -orb.phase * 200 % 360)
                bright = QColor(255, 255, 255, 230)
                none = QColor(255, 255, 255, 0)
                sheen.setColorAt(0.0, bright)
                sheen.setColorAt(0.08, none)
                sheen.setColorAt(1.0, none)
                p.setPen(QPen(QBrush(sheen), RING * 0.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawEllipse(rect)

        # two sparks orbit it while it works, each with a short tail
        if running:
            for speed, offset, size in ((2.3, 0.0, 2.4), (-1.6, math.pi, 1.9)):
                for k in range(5):
                    ang = orb.phase * speed - k * 0.13 * (1 if speed > 0 else -1) + offset
                    pr = r + 6.5
                    pt = QPointF(c.x() + math.cos(ang) * pr, c.y() + math.sin(ang) * pr)
                    s = QColor(colour if k else QColor(255, 255, 255))
                    s.setAlpha(int(230 * (1 - k / 5)))
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(s)
                    d = max(0.6, size - k * 0.35)
                    p.drawEllipse(pt, d, d)

        # in the middle: its kind, or how it ended
        if orb.done:
            draw = max(0.0, min(1.0, (since - 0.15) / 0.35)) if since >= 0 else 1.0
            self._tick_glyph(p, c, r * 0.4, QColor(_C_END), draw)
        elif orb.failed:
            self._cross_glyph(p, c, r * 0.32, QColor(_RED))
        elif orb.status is AgentStatus.PAUSED:
            self._pause_glyph(p, c, r * 0.33, QColor(_AMBER))
        else:
            self._kind_glyph(p, orb.kind, c, r * 0.36, QColor(240, 240, 246, 225))
        p.setOpacity(1.0)

    # -- glyphs ----------------------------------------------------------------

    @staticmethod
    def _pen(colour: QColor, width: float = 2.0) -> QPen:
        pen = QPen(colour, width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return pen

    @classmethod
    def _kind_glyph(cls, p: QPainter, kind: str, c: QPointF, s: float, colour: QColor) -> None:
        p.setPen(cls._pen(colour, 1.8))
        p.setBrush(Qt.BrushStyle.NoBrush)
        x, y = c.x(), c.y()
        if kind == "code":                       # </>
            path = QPainterPath(QPointF(x - s * 0.35, y - s * 0.6))
            path.lineTo(QPointF(x - s, y))
            path.lineTo(QPointF(x - s * 0.35, y + s * 0.6))
            path.moveTo(QPointF(x + s * 0.35, y - s * 0.6))
            path.lineTo(QPointF(x + s, y))
            path.lineTo(QPointF(x + s * 0.35, y + s * 0.6))
            path.moveTo(QPointF(x + s * 0.15, y - s * 0.8))
            path.lineTo(QPointF(x - s * 0.15, y + s * 0.8))
            p.drawPath(path)
        elif kind == "research":                 # a magnifier
            p.drawEllipse(QPointF(x - s * 0.2, y - s * 0.2), s * 0.55, s * 0.55)
            p.drawLine(QPointF(x + s * 0.2, y + s * 0.2), QPointF(x + s * 0.8, y + s * 0.8))
        elif kind == "vision":                   # an eye
            path = QPainterPath(QPointF(x - s, y))
            path.quadTo(QPointF(x, y - s * 1.1), QPointF(x + s, y))
            path.quadTo(QPointF(x, y + s * 1.1), QPointF(x - s, y))
            p.drawPath(path)
            p.setBrush(colour)
            p.drawEllipse(QPointF(x, y), s * 0.28, s * 0.28)
        elif kind == "browser":                  # a globe
            p.drawEllipse(QPointF(x, y), s * 0.85, s * 0.85)
            p.drawEllipse(QPointF(x, y), s * 0.38, s * 0.85)
            p.drawLine(QPointF(x - s * 0.85, y), QPointF(x + s * 0.85, y))
        elif kind == "file":                     # a folder
            path = QPainterPath(QPointF(x - s, y + s * 0.7))
            path.lineTo(QPointF(x - s, y - s * 0.6))
            path.lineTo(QPointF(x - s * 0.3, y - s * 0.6))
            path.lineTo(QPointF(x - s * 0.1, y - s * 0.35))
            path.lineTo(QPointF(x + s, y - s * 0.35))
            path.lineTo(QPointF(x + s, y + s * 0.7))
            path.closeSubpath()
            p.drawPath(path)
        elif kind == "terminal":                 # >_
            path = QPainterPath(QPointF(x - s * 0.9, y - s * 0.5))
            path.lineTo(QPointF(x - s * 0.3, y))
            path.lineTo(QPointF(x - s * 0.9, y + s * 0.5))
            p.drawPath(path)
            p.drawLine(QPointF(x, y + s * 0.55), QPointF(x + s * 0.85, y + s * 0.55))
        elif kind == "computer":                 # a monitor
            p.drawRoundedRect(QRectF(x - s, y - s * 0.75, 2 * s, s * 1.25), 2, 2)
            p.drawLine(QPointF(x - s * 0.4, y + s * 0.85), QPointF(x + s * 0.4, y + s * 0.85))
        elif kind == "quick":                    # a bolt
            path = QPainterPath(QPointF(x + s * 0.2, y - s))
            path.lineTo(QPointF(x - s * 0.5, y + s * 0.1))
            path.lineTo(QPointF(x, y + s * 0.1))
            path.lineTo(QPointF(x - s * 0.2, y + s))
            path.lineTo(QPointF(x + s * 0.5, y - s * 0.1))
            path.lineTo(QPointF(x, y - s * 0.1))
            path.closeSubpath()
            p.setBrush(colour)
            p.drawPath(path)
        else:                                    # a four-point sparkle
            path = QPainterPath(QPointF(x, y - s))
            for px, py in ((x + s * 0.22, y - s * 0.22), (x + s, y), (x + s * 0.22, y + s * 0.22),
                           (x, y + s), (x - s * 0.22, y + s * 0.22), (x - s, y), (x - s * 0.22, y - s * 0.22)):
                path.lineTo(QPointF(px, py))
            path.closeSubpath()
            p.setBrush(colour)
            p.drawPath(path)

    @classmethod
    def _tick_glyph(cls, p, c, s, colour, drawn: float = 1.0):
        """A tick that draws itself: `drawn` 0 -> 1."""
        p.setPen(cls._pen(colour, 2.8))
        p.setBrush(Qt.BrushStyle.NoBrush)
        a = QPointF(c.x() - s, c.y())
        b = QPointF(c.x() - s * 0.25, c.y() + s * 0.7)
        e = QPointF(c.x() + s, c.y() - s * 0.7)
        first = math.hypot(b.x() - a.x(), b.y() - a.y())
        second = math.hypot(e.x() - b.x(), e.y() - b.y())
        length = drawn * (first + second)
        path = QPainterPath(a)
        if length <= first:
            t = length / first if first else 1
            path.lineTo(QPointF(a.x() + (b.x() - a.x()) * t, a.y() + (b.y() - a.y()) * t))
        else:
            path.lineTo(b)
            t = (length - first) / second if second else 1
            path.lineTo(QPointF(b.x() + (e.x() - b.x()) * t, b.y() + (e.y() - b.y()) * t))
        p.drawPath(path)

    @classmethod
    def _cross_glyph(cls, p, c, s, colour):
        p.setPen(cls._pen(colour, 2.8))
        p.drawLine(QPointF(c.x() - s, c.y() - s), QPointF(c.x() + s, c.y() + s))
        p.drawLine(QPointF(c.x() - s, c.y() + s), QPointF(c.x() + s, c.y() - s))

    @staticmethod
    def _pause_glyph(p, c, s, colour):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(colour)
        w = s * 0.45
        p.drawRoundedRect(QRectF(c.x() - s * 0.7, c.y() - s, w, 2 * s), 1.5, 1.5)
        p.drawRoundedRect(QRectF(c.x() + s * 0.25, c.y() - s, w, 2 * s), 1.5, 1.5)

    # -- interaction ---------------------------------------------------------

    def mouseMoveEvent(self, e) -> None:
        orb = self._orb_at(e.position())
        changed = False
        for o in self._orbs:
            was, o.hovered = o.hovered, o is orb
            changed = changed or was != o.hovered
        self.setCursor(Qt.CursorShape.PointingHandCursor if orb is not None else Qt.CursorShape.ArrowCursor)
        if changed:
            self._start()

    def mousePressEvent(self, e) -> None:
        orb = self._orb_at(e.position())
        if orb is not None and e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(orb.agent_id)
            self.opened.emit(orb.agent_id, orb.task_id or "")
            if orb.done or orb.failed or orb.status is AgentStatus.STOPPED:
                orb.leaving = True           # seen: it can go now
                self._start()

    def leaveEvent(self, _e) -> None:
        for o in self._orbs:
            o.hovered = False
        self._start()

    # -- for tests -----------------------------------------------------------

    @property
    def orbs(self) -> list[Orb]:
        return list(self._orbs)

    def close(self) -> bool:
        if self._bridge is not None:
            self._bridge.close()
        self._timer.stop()
        if self._label is not None:
            self._label.close()
        return super().close()
