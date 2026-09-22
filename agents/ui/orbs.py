"""Agent orbs - the workforce, at a glance, in the top-right of the screen.

One orb per working agent, stacked downward as more start. Each is a
dark disc with the agent's initial, wrapped in a progress ring that
sweeps clockwise and shifts colour as the job advances - blue when it
starts, through cyan, to green as it finishes. A ring with nothing to
show yet (planning) carries a slow comet instead of a static arc, so
"working, no progress yet" never looks like "stuck". Paused turns amber
and stops breathing; failure flashes red and holds long enough to be
noticed; completion fills green with a tick and fades out.

Hover an orb for the agent's name and what it is doing right now. Click
it to open the Active Agents panel.

Everything animates through one 60 fps timer that only runs while
something is actually moving, so an idle desktop costs nothing. Frame
order and window flags copy `ui/overlay.py` - frameless, on top, tool
window, no focus stealing - so it behaves like the cursor overlay the
app already has rather than like a second application.
"""
from __future__ import annotations

import math
import time
from typing import Optional

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush, QColor, QConicalGradient, QFont, QPainter, QPainterPath, QPen,
    QRadialGradient,
)
from PyQt6.QtWidgets import QApplication, QToolTip, QWidget

from agents.core import AgentState, AgentStatus
from ui import design

ORB = 46                 # disc diameter, px
RING = 4.5               # ring thickness
GAP = 12                 # vertical gap between orbs
MARGIN = 14              # from the screen's top-right corner
GLOW = 10                # halo radius beyond the ring
WIDTH = ORB + 2 * GLOW + 8
HOLD_DONE_S = 2.6        # how long a finished orb stays before fading
HOLD_FAIL_S = 5.0
FPS_MS = 16

# Progress 0 -> 1 sweeps blue -> cyan -> green.
_C_START = QColor(0, 120, 255)
_C_MID = QColor(0, 210, 230)
_C_END = QColor(50, 220, 110)


def _lerp(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(int(a.red() + (b.red() - a.red()) * t),
                  int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


def progress_colour(p: float) -> QColor:
    return _lerp(_C_START, _C_MID, p * 2) if p < 0.5 else _lerp(_C_MID, _C_END, (p - 0.5) * 2)


def _ease(current: float, target: float, rate: float = 0.16) -> float:
    return current + (target - current) * rate


class Orb:
    """Animation state for one agent. Pure data; the widget paints it."""

    def __init__(self, state: AgentState):
        self.agent_id = state.id
        self.name = state.spec.name
        self.task_title = state.current_task_title
        self.action = state.current_action
        self.status = state.status
        self.target = max(0.0, min(1.0, state.progress))
        self.shown_progress = 0.0
        self.appear = 0.0               # 0 -> 1 on entry, -> 0 on exit
        self.leaving = False
        self.phase = 0.0                # breathing / comet phase
        self.finished_at: Optional[float] = None
        self.hovered = False

    def update(self, state: AgentState) -> None:
        self.name = state.spec.name
        self.task_title = state.current_task_title
        self.action = state.current_action
        prev = self.status
        self.status = state.status
        self.target = max(0.0, min(1.0, state.progress))
        if self.status in (AgentStatus.IDLE, AgentStatus.ERROR, AgentStatus.STOPPED) \
                and prev not in (AgentStatus.IDLE, AgentStatus.ERROR, AgentStatus.STOPPED):
            self.finished_at = time.monotonic()
            if self.status is AgentStatus.IDLE:
                self.target = 1.0

    @property
    def done(self) -> bool:
        return self.status is AgentStatus.IDLE and self.finished_at is not None

    @property
    def failed(self) -> bool:
        return self.status is AgentStatus.ERROR

    @property
    def gone(self) -> bool:
        return self.leaving and self.appear <= 0.01

    def tick(self, dt: float) -> bool:
        """Advance one frame. Returns True while still animating."""
        moving = False
        if self.leaving:
            self.appear = max(0.0, self.appear - dt * 4.0)
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
            hold = HOLD_FAIL_S if self.failed else (HOLD_DONE_S if self.done else 0.6)
            if time.monotonic() - self.finished_at > hold:
                self.leaving = True
                moving = True
        return moving

    def ring_colour(self) -> QColor:
        if self.failed:
            return QColor(design.ERROR)
        if self.status is AgentStatus.PAUSED:
            return QColor(design.WARNING)
        if self.done:
            return QColor(_C_END)
        return progress_colour(self.shown_progress)


class AgentOrbs(QWidget):
    """The floating strip. Feed it AgentStates; it does the rest."""

    clicked = pyqtSignal(str)            # agent_id, for opening the panel

    def __init__(self, manager=None, bridge=None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self._orbs: list[Orb] = []
        self._timer = QTimer(self)
        self._timer.setInterval(FPS_MS)
        self._timer.timeout.connect(self._tick)
        self._last = time.monotonic()
        self._font = QFont("Segoe UI", 13, QFont.Weight.DemiBold)
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
                orb.target = max(0.0, min(1.0, fraction))
                if action:
                    orb.action = action
                self._start()
                return

    # -- geometry ------------------------------------------------------------

    def _relayout(self) -> None:
        n = max(1, len(self._orbs))
        height = n * ORB + (n - 1) * GAP + 2 * GLOW + 8
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

    def _centre(self, index: int) -> QPointF:
        y = GLOW + 4 + ORB / 2 + index * (ORB + GAP)
        return QPointF(WIDTH / 2, y)

    def _orb_at(self, pos) -> Optional[Orb]:
        for i, orb in enumerate(self._orbs):
            c = self._centre(i)
            if math.hypot(pos.x() - c.x(), pos.y() - c.y()) <= ORB / 2 + RING:
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
        for i, orb in enumerate(self._orbs):
            self._paint_orb(p, orb, self._centre(i))
        p.end()

    def _paint_orb(self, p: QPainter, orb: Orb, c: QPointF) -> None:
        a = orb.appear
        if a <= 0:
            return
        scale = 0.6 + 0.4 * a                    # pops in, shrinks out
        r = (ORB / 2) * scale
        colour = orb.ring_colour()
        p.setOpacity(a)

        # halo: breathes while running, steady otherwise
        breath = 0.55 + 0.45 * math.sin(orb.phase * 2.2) if orb.status is AgentStatus.RUNNING else 0.5
        halo = QRadialGradient(c, r + GLOW)
        h = QColor(colour)
        h.setAlpha(int(90 * breath))
        halo.setColorAt(0.72, h)
        h2 = QColor(colour)
        h2.setAlpha(0)
        halo.setColorAt(1.0, h2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(halo))
        p.drawEllipse(c, r + GLOW, r + GLOW)

        # disc
        disc = QRadialGradient(QPointF(c.x() - r * 0.3, c.y() - r * 0.35), r * 1.4)
        disc.setColorAt(0.0, QColor(44, 44, 54))
        disc.setColorAt(1.0, QColor(18, 18, 22))
        p.setBrush(QBrush(disc))
        p.drawEllipse(c, r - RING * 0.4, r - RING * 0.4)

        # ring track
        rect = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
        track = QPen(QColor(255, 255, 255, 28), RING * scale)
        track.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(track)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect)

        # ring progress, or a comet while there is no progress yet
        pen = QPen(colour, RING * scale)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        if orb.shown_progress > 0.005 or orb.done or orb.status is not AgentStatus.RUNNING:
            span = int(-360 * 16 * (1.0 if orb.done else orb.shown_progress))
            p.drawArc(rect, 90 * 16, span)
        else:
            grad = QConicalGradient(c, -orb.phase * 220 % 360)
            faint = QColor(colour)
            faint.setAlpha(0)
            grad.setColorAt(0.0, colour)
            grad.setColorAt(0.35, faint)
            grad.setColorAt(1.0, faint)
            p.setPen(QPen(QBrush(grad), RING * scale, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            p.drawEllipse(rect)

        # glyph: initial, tick, or cross
        p.setPen(QColor(design.TEXT_PRIMARY))
        f = QFont(self._font)
        f.setPointSizeF(13 * scale)
        p.setFont(f)
        if orb.done:
            self._tick_glyph(p, c, r * 0.42, QColor(_C_END))
        elif orb.failed:
            self._cross_glyph(p, c, r * 0.36, QColor(design.ERROR))
        elif orb.status is AgentStatus.PAUSED:
            self._pause_glyph(p, c, r * 0.36, QColor(design.WARNING))
        else:
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, (orb.name[:1] or "A").upper())
        p.setOpacity(1.0)

    @staticmethod
    def _tick_glyph(p, c, s, colour):
        pen = QPen(colour, 3.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        path = QPainterPath(QPointF(c.x() - s, c.y()))
        path.lineTo(QPointF(c.x() - s * 0.25, c.y() + s * 0.7))
        path.lineTo(QPointF(c.x() + s, c.y() - s * 0.7))
        p.drawPath(path)

    @staticmethod
    def _cross_glyph(p, c, s, colour):
        pen = QPen(colour, 3.2)
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
        for o in self._orbs:
            o.hovered = o is orb
        if orb is not None:
            pct = int(orb.shown_progress * 100)
            state = {AgentStatus.PAUSED: "paused", AgentStatus.ERROR: "failed",
                     AgentStatus.IDLE: "done"}.get(orb.status, f"{pct}%")
            text = f"{orb.name} — {state}"
            if orb.task_title:
                text += f"\n{orb.task_title}"
            if orb.action and orb.status is AgentStatus.RUNNING:
                text += f"\n{orb.action}"
            QToolTip.showText(e.globalPosition().toPoint(), text, self)
        else:
            QToolTip.hideText()

    def mousePressEvent(self, e) -> None:
        orb = self._orb_at(e.position())
        if orb is not None and e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(orb.agent_id)

    def leaveEvent(self, _e) -> None:
        for o in self._orbs:
            o.hovered = False
        QToolTip.hideText()

    # -- for tests -----------------------------------------------------------

    @property
    def orbs(self) -> list[Orb]:
        return list(self._orbs)

    def close(self) -> bool:
        if self._bridge is not None:
            self._bridge.close()
        self._timer.stop()
        return super().close()
