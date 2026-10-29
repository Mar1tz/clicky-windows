"""The first launch, as HeyClicky does its own (docs/ui/HEYCLICKY_ONBOARDING.md):
a panel that grows out of the pill to the middle of the screen, one step at
a time, every step the product working - not a page about it.

    ┌ ‹ Back          • • ━ • • • •            ♪   Skip ┐
    │                 First, can I hear you?            │
    │            one or two grey lines                  │
    │                                                   │
    │                 the live thing                    │
    │                                                   │
    │                   [ Continue ]                    │
    └───────────────────────────────────────────────────┘

Phase A (this file): the frame. The steps are `Step`s - each says its title,
its lines, whether it is wide (the interview is), and when it is done
(`done()` lights Continue). Phases B-E fill it with the real steps; until
then the app keeps ui/setup_wizard.py, and this frame opens from
`python -m ui.onboarding_shape` with stand-in steps.

The motion is the shape's (ui/shape.py): the window is made once, big
enough for every size, and what grows is painted inside it - from the
pill, then from one step's size to the next's. The page fades in as the
shape finishes. It follows light and dark, as every solid surface does.

The same signals as the wizard it replaces: `completed` at the end (or
Skip), `ready` once the steps that need nothing of the running app are
done, `attach(live)` when the app is running.
"""
from __future__ import annotations

from typing import Callable, Optional, Sequence

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PyQt6.QtWidgets import (QAbstractButton, QGraphicsOpacityEffect, QHBoxLayout, QSizePolicy,
                             QStackedWidget, QVBoxLayout, QWidget)

from ui import app_theme as T
from ui import kit, tokens
from ui.app_widgets import button, icon, label
from ui.shape import FADE, FADE_FROM, GROW, GROW_PART, LIFT, PAD, _lerp, _part, animations_on

NARROW = (480, 600)
WIDE = (720, 600)
HEAD_H = 44
SIDE_W = 96


def free_voice(on: bool) -> None:
    """While the tour is up her voice is free (migration 0014): the account
    asks for onboarding passes, and the server decides how many it gives."""
    try:
        from account.service import account
        if on and not account().signed_in():
            return
        account().onboarding(on)
    except Exception:  # noqa: BLE001 - an own key, or no account at all
        pass


def keep_out_of_pictures(window: QWidget) -> bool:
    """On screen, but not in a screenshot: the tour stays where it is while
    they ask about their screen (29 Oct: hiding it was in the way), and the
    picture she is handed is theirs alone. Windows 10 2004 and later
    (WDA_EXCLUDEFROMCAPTURE); elsewhere nothing changes."""
    import sys
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(int(window.winId()), 0x11))
    except Exception:  # noqa: BLE001
        return False


class Step(QWidget):
    """One step. Subclasses fill `body` and call `done()` when it is proven
    (Continue lights up); `enter()` / `leave()` start and stop what is live."""

    TITLE = ""
    LINES = ""
    WIDE = False
    # Continue lit from the start: a step with nothing to prove.
    READY = False
    # What the button says on this step ("Show me" on the first).
    GO = "Continue"

    finished = pyqtSignal()                  # done: Continue may be pressed

    def __init__(self):
        super().__init__()
        self.ready = self.READY
        self.panel: Optional["OnboardingPanel"] = None     # set when it is shown
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 6, 32, 0)
        outer.setSpacing(6)
        head = label(self.TITLE, "obTitle", wrap=True)
        head.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        outer.addWidget(head)
        if self.LINES:
            lines = label(self.LINES, "obLines", wrap=True)
            lines.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            outer.addWidget(lines)
        outer.addSpacing(10)
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        outer.addLayout(self.body)
        # The spare room goes under the step, so its words stay at the top
        # whatever it holds (an empty body pulled them to the middle).
        outer.addStretch(1)

    def done(self) -> None:
        if not self.ready:
            self.ready = True
            self.finished.emit()

    def enter(self) -> None:
        """Shown: start what is live (a microphone, a timer)."""

    def leave(self) -> None:
        """Gone: stop it."""


class _Dots(QWidget):
    """Where they are: a dot a step, the current one a short bar."""

    def __init__(self, count: int):
        super().__init__()
        self.count, self.at = count, 0
        self.setFixedSize(count * 12 + 14, 14)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        x = 0.0
        for i in range(self.count):
            on = i == self.at
            p.setBrush(QColor(T.ACCENT if i <= self.at else T.BORDER_STRONG))
            w = 18.0 if on else 5.0
            p.drawRoundedRect(QRectF(x, 4.5, w, 5), 2.5, 2.5)
            x += w + 7
        p.end()


class _SoundButton(QAbstractButton):
    """♪: her voice and sounds in the tour, on or off."""

    def __init__(self):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(True)
        self.setFixedSize(26, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Sound on")
        self.toggled.connect(lambda on: self.setToolTip("Sound on" if on else "Sound off"))

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        ink = T.TEXT_DIM if self.isChecked() else T.TEXT_FAINT
        p.drawPixmap(4, 4, icon("megaphone", ink, 18, self.devicePixelRatioF()))
        if not self.isChecked():
            p.setPen(QPen(QColor(ink), 1.6))
            p.drawLine(QPointF(4, 22), QPointF(22, 4))
        p.end()


class OnboardingPanel(QWidget):
    """open_from(pill) / back() / next() / skip(). `steps` are made when the
    panel is; `pill` is where it grows from (the flow bar's, or the middle
    of the top of the screen when there is none)."""

    completed = pyqtSignal()
    ready = pyqtSignal()
    step_changed = pyqtSignal(int)

    def __init__(self, steps: Sequence[Callable[[], Step]], *, ready_after: int = -1):
        super().__init__()
        self.setWindowTitle("HeyTora")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._ready_after = ready_after
        self._sent_ready = False
        self.steps: list[Step] = [make() for make in steps]
        self.at = 0
        self._a = self._b = QRectF()
        self._from_pill = True
        self._t = 0.0
        self._anim: Optional[QVariantAnimation] = None
        self._pill = QRectF()
        self.live = None                            # the running app, once attach() is called

        self.body = QWidget(self)
        self.body.setObjectName("obBody")
        from ui import onboarding_steps
        self.body.setStyleSheet(T.qss() + stylesheet() + onboarding_steps.stylesheet())
        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(16, 10, 16, 22)
        lay.setSpacing(0)
        lay.addWidget(self._build_head())
        self.stack = QStackedWidget()
        for step in self.steps:
            step.finished.connect(self._light)
            self.stack.addWidget(step)
        lay.addWidget(self.stack, 1)
        foot = QHBoxLayout()
        foot.addStretch(1)
        self.go = button("Continue", "primary")
        self.go.setMinimumWidth(140)
        self.go.clicked.connect(self.next)
        foot.addWidget(self.go)
        foot.addStretch(1)
        lay.addLayout(foot)
        self._fade = QGraphicsOpacityEffect(self.body)
        self._fade.setOpacity(0.0)
        self.body.setGraphicsEffect(self._fade)
        self.body.hide()

    # ── the head ─────────────────────────────────────────────────────────────
    def _build_head(self) -> QWidget:
        head = QWidget()
        head.setFixedHeight(HEAD_H)
        row = QHBoxLayout(head)
        row.setContentsMargins(0, 0, 0, 0)
        # Both ends as wide as each other, so the dots sit in the middle -
        # and Back keeps its room while hidden on the first step.
        left, right = QWidget(), QWidget()
        for side in (left, right):
            side.setFixedWidth(SIDE_W)
        lrow, rrow = QHBoxLayout(left), QHBoxLayout(right)
        for r in (lrow, rrow):
            r.setContentsMargins(0, 0, 0, 0)
        self.back_button = button("‹ Back", "link")
        self.back_button.clicked.connect(self.back)
        keep = self.back_button.sizePolicy()
        keep.setRetainSizeWhenHidden(True)
        self.back_button.setSizePolicy(keep)
        lrow.addWidget(self.back_button)
        lrow.addStretch(1)
        row.addWidget(left)
        row.addStretch(1)
        self.dots = _Dots(len(self.steps))
        row.addWidget(self.dots)
        row.addStretch(1)
        self.sound = _SoundButton()
        rrow.addStretch(1)
        rrow.addWidget(self.sound)
        self.skip_button = button("Skip", "link")
        self.skip_button.setToolTip("Skip the tour - everything here is in Settings too")
        self.skip_button.clicked.connect(self.skip)
        rrow.addWidget(self.skip_button)
        row.addWidget(right)
        return head

    def sound_on(self) -> bool:
        return self.sound.isChecked()

    # ── steps ────────────────────────────────────────────────────────────────
    def step(self) -> Step:
        return self.steps[self.at]

    def _show_step(self, i: int) -> None:
        old = self.step()
        if old is not self.steps[i]:
            old.leave()
        self.at = i
        self.stack.setCurrentIndex(i)
        self.dots.at = i
        self.dots.update()
        self.back_button.setVisible(i > 0)
        self._light()
        self.go.setText(self.step().GO)
        self.step_changed.emit(i)
        self.step().panel = self
        self.step().enter()

    def _light(self) -> None:
        self.go.setEnabled(self.step().ready)
        if (not self._sent_ready and self._ready_after >= 0 and self.at >= self._ready_after
                and self.step().ready):
            self._sent_ready = True
            self.ready.emit()

    def next(self) -> None:
        if not self.step().ready:
            return
        if self.at + 1 >= len(self.steps):
            self.finish()
            return
        self._move(self.at + 1)

    def back(self) -> None:
        if self.at > 0:
            self._move(self.at - 1)

    def skip(self) -> None:
        self.finish()

    def finish(self) -> None:
        self.step().leave()
        free_voice(False)
        if not self._sent_ready:
            self._sent_ready = True
            self.ready.emit()
        self._shrink()
        self.completed.emit()

    def attach(self, live) -> None:
        """The running app (the wizard's interface): steps that need it look
        for it here."""
        self.live = live
        hook = getattr(self.step(), "attached", None)
        if hook is not None:
            hook()

    # ── sizes and motion ─────────────────────────────────────────────────────
    def _rect_for(self, step: Step) -> QRectF:
        w, h = WIDE if step.WIDE else NARROW
        screen = QGuiApplication.screenAt(self._pill.center().toPoint()) or QGuiApplication.primaryScreen()
        g = screen.availableGeometry()
        return QRectF(round(g.center().x() - w / 2), round(g.center().y() - h / 2), w, h)

    def _span(self) -> QRectF:
        screen = QGuiApplication.screenAt(self._pill.center().toPoint()) or QGuiApplication.primaryScreen()
        g = screen.availableGeometry()
        wide = QRectF(round(g.center().x() - WIDE[0] / 2), round(g.center().y() - WIDE[1] / 2), *WIDE)
        return wide.united(self._pill).adjusted(-PAD, -PAD, PAD, PAD)

    def open_from(self, pill: Optional[QRectF] = None) -> None:
        """Grow out of `pill` (global) to the first step."""
        if pill is None or pill.isNull():
            g = QGuiApplication.primaryScreen().availableGeometry()
            pill = QRectF(g.center().x() - 23, g.top() + 12, 46, 10)
        self._pill = QRectF(pill)
        free_voice(True)
        self.setGeometry(self._span().toRect())        # once: it never moves while it grows
        self._a, self._b, self._from_pill = QRectF(self._pill), self._rect_for(self.steps[0]), True
        self.body.setGeometry(self._local(self._b).toRect())
        self._step_to(0.0)
        self.show()
        keep_out_of_pictures(self)
        self.raise_()
        self.activateWindow()
        self._show_step(0)
        self._run(1.0, tokens.SHAPE_OPEN)

    def _move(self, i: int) -> None:
        now = _lerp(self._a, self._b, self._grow())
        target = self._rect_for(self.steps[i])
        self._show_step(i)
        if target == self._b:
            return                                    # same size: the page simply changes
        self._a, self._b, self._from_pill = now, target, False
        self.body.setGeometry(self._local(self._b).toRect())
        self._t = 0.0
        self._step_to(0.0)
        self._run(1.0, tokens.SHAPE_OPEN)

    def _shrink(self) -> None:
        now = _lerp(self._a, self._b, self._grow())
        self._a, self._b, self._from_pill, self._t = QRectF(self._pill), now, True, 1.0
        self._closing = True
        self._run(0.0, tokens.SHAPE_CLOSE)

    def _run(self, to: float, ms: int) -> None:
        if self._anim is not None:
            self._anim.stop()
        if not animations_on():
            self._step_to(to)
            return
        a = QVariantAnimation(self)
        a.setStartValue(self._t)
        a.setEndValue(to)
        a.setDuration(max(1, int(ms * abs(to - self._t))))
        a.valueChanged.connect(lambda v: self._step_to(float(v)))
        a.start()
        self._anim = a

    def _grow(self) -> float:
        return GROW.valueForProgress(_part(self._t, 0.0, GROW_PART))

    def _step_to(self, t: float) -> None:
        self._t = t
        fade = FADE.valueForProgress(_part(t, FADE_FROM, 1.0 - FADE_FROM))
        self._fade.setOpacity(fade)
        self._fade.setEnabled(fade < 1.0)
        self.body.setVisible(fade > 0.0)
        full = self._local(self._b).toRect()
        self.body.move(full.x(), full.y() + round(LIFT * (1.0 - fade)))
        self.update()
        if t <= 0.0 and getattr(self, "_closing", False):
            self.hide()

    def _local(self, r: QRectF) -> QRectF:
        return r.translated(-self.x(), -self.y())

    def panel_rect(self) -> QRectF:
        """Where the panel is painted now, in this window."""
        return self._local(_lerp(self._a, self._b, self._grow()))

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.panel_rect()
        g = self._grow() if self._from_pill else 1.0
        radius = min(r.height(), r.width()) / 2 * (1 - g) + tokens.RADIUS_XL * g
        kit.soft_shadow(p, r, radius, "hero", strength=g)
        p.setBrush(kit.mix(tokens.PILL, T.BG, g))
        p.setPen(QPen(QColor(T.BORDER), 1.0) if T.is_dark() else Qt.PenStyle.NoPen)
        p.drawRoundedRect(r, radius, radius)
        p.end()

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.next()
            return
        super().keyPressEvent(e)


def stylesheet() -> str:
    return f"""
    QLabel#obTitle {{ font-size: 22px; font-weight: 700; letter-spacing: -0.3px; }}
    QLabel#obLines {{ color: {T.TEXT_DIM}; font-size: 13px; }}
    """


# ── a look at the frame, before the steps exist ───────────────────────────────

class _StandIn(Step):
    """A step to see the frame with (python -m ui.onboarding_shape)."""

    def __init__(self, title: str, lines: str, wide: bool, ready: bool):
        self.TITLE, self.LINES, self.WIDE, self.READY = title, lines, wide, ready
        super().__init__()
        if not ready:
            prove = button("Pretend it worked", "ghost")
            prove.clicked.connect(self.done)
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(prove)
            row.addStretch(1)
            self.body.addStretch(1)
            self.body.addLayout(row)
            self.body.addStretch(1)


def stand_ins() -> list[Callable[[], Step]]:
    return [
        lambda: _StandIn("First, can I hear you?", "Say anything out loud. If the bars move, your "
                         "microphone works.", False, False),
        lambda: _StandIn("This is how you talk to me", "Hold the keys, say your name, then let go.",
                         False, False),
        lambda: _StandIn("I'm curious about you.", "Four quick questions, so I can make this useful "
                         "for you.", True, True),
        lambda: _StandIn("Pick my voice", "Tap one to hear it.", False, True),
    ]


if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    tokens.load_fonts()
    import logging
    from config import data_dir
    logging.basicConfig(filename=str(data_dir() / "onboarding-preview.log"), level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    from ui.onboarding_steps import steps
    panel = OnboardingPanel(steps() + stand_ins()[3:])
    panel.completed.connect(lambda: QTimer.singleShot(400, app.quit))
    panel.open_from()
    sys.exit(app.exec())
