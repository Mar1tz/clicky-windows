"""The agents at work, top-right of the screen - one card each.

A card says what the job is, what the agent is doing right now and how far
it has got, without hovering: the job's name, its current step underneath,
and a ring with the percentage on the left. Planning, with no progress yet,
carries a slow comet instead, so "working, no number yet" never reads as
"stuck". Paused is amber, a failure red; a finished job turns green -
"Done · click to see it" - stays a few seconds, and slides away. A click
opens the job in the HeyTora app (main.py wires `clicked`).

It used to be a column of dark discs with one letter in each, and the
name, the step and the percentage only in a hover tooltip (10 Oct 2026:
"improve the agents at the top right"). And a finished one never left:
the animation timer stopped once the ring had filled, so the hold before
fading was never checked again. A card now keeps its timer until it has
gone.

The look is the answer card's (ui/flow_bar.py): near-black glass, a
hairline edge, a soft layered shadow, the app's font. Everything animates
through one timer that runs only while something moves; window flags copy
`ui/overlay.py` - frameless, on top, tool window, no focus stealing.
"""
from __future__ import annotations

import math
import time
from typing import Optional

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush, QColor, QConicalGradient, QFont, QFontMetricsF, QPainter, QPainterPath, QPen,
)
from PyQt6.QtWidgets import QApplication, QToolTip, QWidget

from agents.core import AgentState, AgentStatus

CARD_W = 328             # one card
CARD_H = 60
GAP = 10                 # between cards
PAD = 16                 # room around the cards in the window, for the shadow
MARGIN = 6               # from the screen's top-right corner (the shadow pads it too)
RADIUS = 18
ORB = 34                 # the progress ring's diameter
RING = 3.6               # its thickness
WIDTH = CARD_W + 2 * PAD
MAX_SHOWN = 5            # more than this many agents: the rest wait their turn
HOLD_DONE_S = 5.0        # a finished card stays this long, then slides away
HOLD_FAIL_S = 9.0
HOLD_STOPPED_S = 1.5
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


def _font(px: float, weight: int = 500) -> QFont:
    """The app's face (ui/tokens.py), as the answer card sets it."""
    try:
        from ui import tokens
        family = tokens.font_family()
    except Exception:  # noqa: BLE001 - a card is never stopped by a font
        family = "Segoe UI"
    f = QFont(family)
    f.setPixelSize(max(1, int(round(px))))
    f.setWeight(QFont.Weight(weight))
    return f


class Orb:
    """One agent's card: what it says and where its animation is. Pure
    data; the widget paints it. (The name is the old one, kept for its
    users.)"""

    def __init__(self, state: AgentState):
        self.agent_id = state.id
        self.task_id = getattr(state, "current_task_id", None)
        self.name = state.spec.name
        self.task_title = state.current_task_title
        self.action = state.current_action
        self.status = state.status
        self.target = max(0.0, min(1.0, state.progress))
        self.shown_progress = 0.0
        self.appear = 0.0               # 0 -> 1 on entry, -> 0 on exit
        self.leaving = False
        self.phase = 0.0                # comet / pulse phase
        self.finished_at: Optional[float] = None
        self.hovered = False

    def update(self, state: AgentState) -> None:
        self.name = state.spec.name
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
            # Started again (a new job for the same agent): it stays.
            self.finished_at = None
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

    def hold(self) -> float:
        return HOLD_FAIL_S if self.failed else HOLD_DONE_S if self.done else HOLD_STOPPED_S

    def tick(self, dt: float) -> bool:
        """Advance one frame. True while it still needs frames - which
        includes the hold of a finished card: the timer must be running
        when the hold ends, or the card never leaves."""
        moving = False
        if self.leaving:
            self.appear = max(0.0, self.appear - dt * 3.2)
            moving = self.appear > 0
        elif self.appear < 1.0:
            self.appear = min(1.0, self.appear + dt * 4.0)
            moving = True
        if abs(self.shown_progress - self.target) > 0.002:
            self.shown_progress = _ease(self.shown_progress, self.target)
            moving = True
        else:
            self.shown_progress = self.target
        if self.status is AgentStatus.RUNNING:
            self.phase = (self.phase + dt) % 1000.0
            moving = True
        if self.finished_at is not None and not self.leaving:
            if time.monotonic() - self.finished_at > self.hold():
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

    # -- what the card says ----------------------------------------------------

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


class AgentOrbs(QWidget):
    """The floating stack of agent cards. Feed it AgentStates."""

    clicked = pyqtSignal(str)            # agent_id: open its job
    opened = pyqtSignal(str, str)        # agent_id, task_id (or "")

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
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._orbs: list[Orb] = []
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
        height = n * CARD_H + (n - 1) * GAP + 2 * PAD
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

    def _card(self, index: int, orb: Optional[Orb] = None) -> QRectF:
        """Card `index`'s rectangle; sliding in and out from the right."""
        slide = (1.0 - orb.appear) * 40 if orb is not None else 0.0
        return QRectF(PAD + slide, PAD + index * (CARD_H + GAP), CARD_W, CARD_H)

    def _centre(self, index: int) -> QPointF:
        """The ring's centre on card `index`."""
        r = self._card(index)
        return QPointF(r.left() + 16 + ORB / 2, r.center().y())

    def _orb_at(self, pos) -> Optional[Orb]:
        for i, orb in enumerate(self._shown()):
            if self._card(i).adjusted(-2, -2, 2, 2).contains(QPointF(pos)):
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
        self.update()
        if not moving:
            self._timer.stop()

    # -- painting ------------------------------------------------------------

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        for i, orb in enumerate(self._shown()):
            self._paint_card(p, orb, self._card(i, orb))
        p.end()

    def _paint_card(self, p: QPainter, orb: Orb, r: QRectF) -> None:
        a = orb.appear
        if a <= 0:
            return
        p.setOpacity(a)
        # shadow, as the answer card's
        p.setPen(Qt.PenStyle.NoPen)
        for i, al in enumerate((30, 18, 9)):
            g = 2.0 + i * 3.0
            p.setBrush(QColor(0, 0, 0, al))
            p.drawRoundedRect(r.adjusted(-g, -g + 3, g, g + 3), RADIUS + g, RADIUS + g)
        # the glass, tinted by how it ended
        edge = QColor(255, 255, 255, 60 if orb.hovered else 26)
        fill = QColor(0, 0, 0, 236)
        if orb.done:
            edge = QColor(50, 220, 110, 120)
        elif orb.failed:
            edge = QColor(255, 84, 84, 120)
        p.setBrush(fill)
        p.setPen(QPen(edge, 1.0))
        p.drawRoundedRect(r, RADIUS, RADIUS)
        if orb.hovered:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 12))
            p.drawRoundedRect(r, RADIUS, RADIUS)

        # the ring, on the left
        c = QPointF(r.left() + 16 + ORB / 2, r.center().y())
        self._paint_ring(p, orb, c)

        # the job, and what it is doing now
        left = r.left() + 16 + ORB + 12
        width = r.right() - 30 - left
        title_font = _font(13.5, 600)
        line_font = _font(12, 500)
        p.setFont(title_font)
        p.setPen(QColor(244, 244, 247))
        title = QFontMetricsF(title_font).elidedText(orb.title(), Qt.TextElideMode.ElideRight, width)
        p.drawText(QRectF(left, r.top() + 10, width, 20),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        p.setFont(line_font)
        p.setPen(orb.line_colour())
        line = QFontMetricsF(line_font).elidedText(orb.line(), Qt.TextElideMode.ElideRight, width)
        p.drawText(QRectF(left, r.top() + 31, width, 18),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line)

        # ›  - it opens
        chev = QPen(QColor(255, 255, 255, 200 if orb.hovered else 90), 1.8)
        chev.setCapStyle(Qt.PenCapStyle.RoundCap)
        chev.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(chev)
        p.setBrush(Qt.BrushStyle.NoBrush)
        x, y = r.right() - 18, r.center().y()
        path = QPainterPath(QPointF(x - 3.5, y - 6))
        path.lineTo(QPointF(x + 2.5, y))
        path.lineTo(QPointF(x - 3.5, y + 6))
        p.drawPath(path)
        p.setOpacity(1.0)

    def _paint_ring(self, p: QPainter, orb: Orb, c: QPointF) -> None:
        rad = ORB / 2
        rect = QRectF(c.x() - rad, c.y() - rad, 2 * rad, 2 * rad)
        colour = orb.ring_colour()
        track = QPen(QColor(255, 255, 255, 34), RING)
        p.setPen(track)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect)
        if orb.done or orb.failed:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(colour.red(), colour.green(), colour.blue(), 40))
            p.drawEllipse(rect)
        pen = QPen(colour, RING)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        planning = orb.status is AgentStatus.RUNNING and orb.shown_progress <= 0.005
        if planning:
            grad = QConicalGradient(c, -orb.phase * 220 % 360)
            faint = QColor(colour)
            faint.setAlpha(0)
            grad.setColorAt(0.0, colour)
            grad.setColorAt(0.35, faint)
            grad.setColorAt(1.0, faint)
            p.setPen(QPen(QBrush(grad), RING, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawEllipse(rect)
        else:
            p.setPen(pen)
            span = int(-360 * 16 * (1.0 if orb.done or orb.failed else orb.shown_progress))
            p.drawArc(rect, 90 * 16, span)
        # in the middle: the percentage, a tick, a cross or a pause
        if orb.done:
            self._tick_glyph(p, c, rad * 0.42, QColor(_C_END))
        elif orb.failed:
            self._cross_glyph(p, c, rad * 0.34, QColor(_RED))
        elif orb.status is AgentStatus.PAUSED:
            self._pause_glyph(p, c, rad * 0.36, QColor(_AMBER))
        elif not planning:
            p.setFont(_font(10.5, 700))
            p.setPen(QColor(244, 244, 247))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"{int(round(orb.shown_progress * 100))}%")

    @staticmethod
    def _tick_glyph(p, c, s, colour):
        pen = QPen(colour, 2.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath(QPointF(c.x() - s, c.y()))
        path.lineTo(QPointF(c.x() - s * 0.25, c.y() + s * 0.7))
        path.lineTo(QPointF(c.x() + s, c.y() - s * 0.7))
        p.drawPath(path)

    @staticmethod
    def _cross_glyph(p, c, s, colour):
        pen = QPen(colour, 2.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
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
        if changed:
            self.update()
        if orb is not None:
            # The card says it all, unless a long name was cut short.
            text = orb.title()
            if orb.action and orb.status is AgentStatus.RUNNING:
                text += f"\n{orb.action}"
            QToolTip.showText(e.globalPosition().toPoint(), text, self)
        else:
            QToolTip.hideText()

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
        QToolTip.hideText()
        self.update()

    # -- for tests -----------------------------------------------------------

    @property
    def orbs(self) -> list[Orb]:
        return list(self._orbs)

    def close(self) -> bool:
        if self._bridge is not None:
            self._bridge.close()
        self._timer.stop()
        return super().close()
