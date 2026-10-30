"""The first launch's steps (docs/ui/HEYCLICKY_ONBOARDING.md), each the
product working, not a page about it. Phase B: hello, can I hear you, this
is how you talk to me. Phases C-E add the interview and the rest.

Each is a `Step` of ui/onboarding_shape.py: it says its title and lines,
fills its body, calls `done()` when it is proven, and starts and stops what
is live in `enter()` / `leave()`. Phase C: the talk step hears your name
and "I'm curious about you" interviews you - one conversation between them
(tour_voice, ui/onboarding_interview.py), on the tour's free voice.
"""
from __future__ import annotations

import time
from typing import Optional, Sequence

from PyQt6.QtCore import QObject, QPointF, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QAbstractButton, QCheckBox, QFrame, QHBoxLayout, QLineEdit, QMenu, QPushButton,
                             QScrollArea, QVBoxLayout, QWidget)

from ui import app_theme as T
from ui.app_widgets import button, label
from ui.onboarding_interview import Interview, save
from ui.onboarding_shape import Step


def _centred(*widgets: QWidget) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addStretch(1)
    for w in widgets:
        row.addWidget(w)
    row.addStretch(1)
    return row


def hotkey_keys() -> list[str]:
    """The push-to-talk keys as they are written on keycaps: ["Ctrl", "Win"]."""
    from config import cfg
    names = {"ctrl": "Ctrl", "win": "Win", "windows": "Win", "alt": "Alt", "shift": "Shift"}
    return [names.get(k.strip().lower(), k.strip().capitalize()) for k in cfg.hotkey.split("+")]


class Bubble(QWidget):
    """What to say, in a soft bubble: “Testing, testing, 1, 2, 3”."""

    def __init__(self, words: str):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.text = label(f"“{words}”", "obBubble")
        row.addWidget(self.text)


# ── hello ─────────────────────────────────────────────────────────────────────

class HelloStep(Step):
    """Tora says hello in her voice: the bundled hello, so it costs nothing
    (assets/voices). No character on screen - HeyTora has none, and the
    owner chose not to make her cursor one (29 Oct 2026)."""

    TITLE = "Hi, I'm Tora."
    LINES = ("Let me show you around. About three minutes - and you'll try "
             "everything for real.")
    READY = True
    GO = "Show me"

    def __init__(self):
        super().__init__()
        self._played = False

    def enter(self) -> None:
        if self._played or (self.panel is not None and not self.panel.sound_on()):
            return
        self._played = True
        from config import cfg
        from ui.onboarding_parts import play_voice
        QTimer.singleShot(350, lambda: play_voice(cfg.voice or "Zephyr"))


# ── can I hear you ────────────────────────────────────────────────────────────

class MicStep(Step):
    """Bars that move with the voice; proven once it has heard speech for a
    moment. The microphone is a chip: a click lists the others."""

    TITLE = "First, can I hear you?"
    LINES = ("Say anything out loud. If the bars move, your microphone works. "
             "Built-in or wired mics work best.")
    SPEECH = 0.012          # the level that is someone talking (RMS)
    HEARD_S = 0.5           # this much talking proves it

    def __init__(self):
        super().__init__()
        from ui.shape_widgets import LevelBars
        self.bars = LevelBars(bars=22, big=True)
        self.body.addSpacing(18)
        self.body.addLayout(_centred(self.bars))
        self.body.addSpacing(24)
        self.body.addLayout(_centred(Bubble("Testing, testing, 1, 2, 3")))
        self.body.addSpacing(10)
        self.chip = QPushButton("")
        self.chip.setObjectName("obChip")
        self.chip.setToolTip("Choose another microphone")
        self.chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chip.clicked.connect(self._choose)
        self.body.addLayout(_centred(self.chip))
        self.note = label("", "obNote", wrap=True)
        self.note.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.body.addWidget(self.note)
        self._stream = None
        self._level = 0.0
        self._heard = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self._device: Optional[int] = None

    @staticmethod
    def microphones() -> list[tuple[int, str, bool]]:
        """(device, name, is it Windows' default) for every input."""
        try:
            import sounddevice as sd
            default = sd.default.device[0]
            return [(i, d.get("name", ""), i == default)
                    for i, d in enumerate(sd.query_devices()) if d.get("max_input_channels", 0) > 0]
        except Exception:  # noqa: BLE001 - no audio stack
            return []

    def enter(self) -> None:
        from config import cfg
        mics = self.microphones()
        if not mics:
            self.chip.hide()
            self.note.setText("No microphone found. Plug one in - or carry on, and choose it "
                              "later in Settings.")
            self.done()
            return
        now = cfg.mic_device()
        self._device = now if any(i == now for i, _n, _d in mics) else next(
            (i for i, _n, d in mics if d), mics[0][0])
        self._name(dict((i, n) for i, n, _d in mics).get(self._device, ""))
        self._open(self._device)
        self._timer.start()

    def leave(self) -> None:
        self._timer.stop()
        self._close()

    def _name(self, name: str) -> None:
        from PyQt6.QtGui import QIcon
        from ui.app_widgets import icon
        self.chip.setIcon(QIcon(icon("mic", T.TEXT_DIM, 14, self.devicePixelRatioF())))
        self.chip.setText(f"  {name}" if name else "  Choose a microphone")

    def _choose(self) -> None:
        menu = QMenu(self)
        for index, name, default in self.microphones():
            act = menu.addAction(name + ("  (Windows' default)" if default else ""))
            act.setCheckable(True)
            act.setChecked(index == self._device)
            act.triggered.connect(lambda _c=False, i=index, n=name: self._pick(i, n))
        menu.exec(self.chip.mapToGlobal(self.chip.rect().bottomLeft()))

    def _pick(self, index: int, name: str) -> None:
        from config import cfg
        cfg.set_mic_device(index)                # with its name: device numbers move
        self._device = index
        self._name(name)
        self._open(index)

    def _open(self, device: int) -> None:
        self._close()
        try:
            import numpy as np
            import sounddevice as sd

            def hear(data, _frames, _time, _status):
                self._level = float(np.sqrt(np.mean(np.square(data.astype("float32")))))
            self._stream = sd.InputStream(device=device, channels=1, samplerate=16000,
                                          dtype="float32", callback=hear)
            self._stream.start()
            self.note.setText("")
        except Exception:  # noqa: BLE001
            self._stream = None
            self.note.setText("That microphone couldn't be opened. Choose another one.")

    def _close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001
                pass

    def hear(self, level: float, dt: float) -> None:
        """One reading (also how a test feeds it): the bars move, and enough
        talking proves the step."""
        self.bars.level += (min(1.0, level * 9) - self.bars.level) * 0.45
        self.bars.update()
        if level > self.SPEECH:
            self._heard += dt
            if self._heard >= self.HEARD_S:
                self.done()

    def _tick(self) -> None:
        self.hear(self._level, self._timer.interval() / 1000)


# ── the tour's voice ──────────────────────────────────────────────────────────

class _VoiceEvents(QObject):
    """The interview's events, brought from its thread to Qt's."""
    event = pyqtSignal(str, object)


def tour_voice(panel):
    """The one conversation the talk step and the interview share
    (ui/onboarding_interview.py), made when first needed and ended with the
    tour. Its events arrive on Qt's thread as `panel.voice_events.event`."""
    voice = getattr(panel, "voice", None)
    if voice is None:
        events = _VoiceEvents(panel)
        voice = Interview(events.event.emit)
        panel.voice, panel.voice_events = voice, events
        panel.completed.connect(voice.stop)
        voice.can_continue = lambda: bool(panel.step().ready)
        panel.presser = _ContinuePresser(panel)
        events.event.connect(panel.presser.on_event)
    return voice


class _ContinuePresser(QObject):
    """ "Press Continue for me": the mouse glides to the button and it is
    pressed - her press_continue. Moving the mouse on the way stops it."""

    GLIDE_MS = 800
    YOURS_PX = 10

    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._from = QPointF()
        self._put: Optional[QPointF] = None
        self._at = 0.0

    def on_event(self, kind: str, value) -> None:
        if kind == "press" and value == "continue":
            self.press()

    def press(self) -> None:
        if not DriveStep.MOVE_MOUSE:
            self._click()
            return
        from PyQt6.QtGui import QCursor
        self._from, self._put = QPointF(QCursor.pos()), None
        self._at = time.monotonic()
        self._timer.start()

    def _tick(self) -> None:
        from PyQt6.QtGui import QCursor
        if self._put is not None and (QPointF(QCursor.pos()) - self._put).manhattanLength() > self.YOURS_PX:
            self._timer.stop()                    # theirs again
            return
        go = self.panel.go
        to = QPointF(go.mapToGlobal(go.rect().center()))
        k = min((time.monotonic() - self._at) * 1000 / self.GLIDE_MS, 1.0)
        pos = self._from + (to - self._from) * (1 - (1 - k) ** 3)
        QCursor.setPos(pos.toPoint())
        self._put = QPointF(pos.toPoint())
        if k >= 1.0:
            self._timer.stop()
            QTimer.singleShot(150, self._click)

    def _click(self) -> None:
        if self.panel.go.isEnabled():
            self.panel.go.click()


# ── holding the keys ──────────────────────────────────────────────────────────

class _HeldKeys(Step):
    """A step answered by holding the push-to-talk keys. With the app
    running its shortcut is borrowed while the step is up (holding it
    lights the keys instead of starting a conversation); before then the
    keys are watched directly. Subclasses say what a hold does (`hold`)."""

    pressed = pyqtSignal(bool)                  # from the hook's thread

    def __init__(self):
        super().__init__()
        self.pressed.connect(self.hold)
        self._down_at: Optional[float] = None
        self._poll = QTimer(self)
        self._poll.setInterval(40)
        self._poll.timeout.connect(self._watch)
        self._was_down = False

    def keycaps(self, before: str, after: str) -> tuple[QHBoxLayout, list]:
        from ui.onboarding_parts import Keycap
        caps = [Keycap(k, px=20) for k in hotkey_keys()]
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(label(before, "obNote"))
        for cap in caps:
            row.addWidget(cap)
        row.addWidget(label(after, "obNote"))
        row.addStretch(1)
        return row, caps

    def enter(self) -> None:
        live = self.panel.live if self.panel is not None else None
        if live is not None and hasattr(live, "claim_hotkey"):
            live.claim_hotkey(lambda: self.pressed.emit(True), lambda: self.pressed.emit(False))
        else:
            self._poll.start()                   # before the app runs: watch the keys

    def attached(self) -> None:
        """The app started while this step was up: borrow its shortcut."""
        self._poll.stop()
        _HeldKeys.enter(self)

    def leave(self) -> None:
        self._poll.stop()
        live = self.panel.live if self.panel is not None else None
        if live is not None and hasattr(live, "release_hotkey"):
            live.release_hotkey()

    def _watch(self) -> None:
        try:
            from hotkey import _is_down
            down = all(_is_down(k.lower()) for k in hotkey_keys())
        except Exception:  # noqa: BLE001 - no keyboard hook here
            return
        if down != self._was_down:
            self._was_down = down
            self.hold(down)

    def hold(self, down: bool, now: Optional[float] = None) -> None:
        raise NotImplementedError


# ── this is how you talk to me ────────────────────────────────────────────────

class TalkStep(_HeldKeys):
    """Hold the keys - they light up while held - say hi and your name, let
    go. Proven by a real hold and release; the tour's voice hears it too and
    says hello by name (heard_name). With no voice - offline, signed out -
    the hold alone proves it."""

    TITLE = "This is how you talk to me"
    LINES = ("Hold the keys down and say hi - I'm listening the whole time you hold "
             "them. Let go when you're done.")
    HOLD_S = 0.4            # a real hold, not a brush of the keys

    def __init__(self):
        super().__init__()
        row, self.keys = self.keycaps("Hold", "and say")
        self.body.addSpacing(18)
        self.body.addLayout(row)
        self.body.addSpacing(18)
        self.body.addLayout(_centred(Bubble("Hi Tora, I'm …your name")))
        self.status = label("waiting for you…", "obNote")
        self.status.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.body.addSpacing(6)
        self.body.addWidget(self.status)
        self.reply = label("", "obReply", wrap=True)
        self.reply.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.body.addSpacing(14)
        self.body.addWidget(self.reply)
        self.name = ""
        self._voice = None

    def enter(self) -> None:
        super().enter()
        if self.panel is not None and self._voice is None:
            self._voice = tour_voice(self.panel)
            self.panel.voice_events.event.connect(self._event)
        if self._voice is not None:
            self._voice.start()

    def _event(self, kind: str, value) -> None:
        if self.panel is None or self.panel.step() is not self:
            return
        if kind == "name":
            self.name = str(value)
            self.status.setText(f"Nice to meet you, {self.name}.")
        elif kind == "tora":
            self.reply.setText(self.reply.text() + str(value))
        elif kind == "tora_end":
            self.reply.setText(str(value).strip())

    def hold(self, down: bool, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        for cap in self.keys:
            cap.set_lit(down)
        if self._voice is not None:
            self._voice.hold(down)
        if down:
            self._down_at = now
            self.reply.setText("")
            self.status.setText("listening…")
            return
        held = now - self._down_at if self._down_at is not None else 0.0
        self._down_at = None
        if held >= self.HOLD_S:
            if not self.name:
                self.status.setText("That's it - that's all there is to it.")
            self.done()
        else:
            self.status.setText("Hold them a little longer, then let go.")


# ── typing to her: a messaging app's box ──────────────────────────────────────

def _soft(hex_colour: str, alpha: float) -> str:
    from PyQt6.QtGui import QColor
    c = QColor(hex_colour)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha:.2f})"


class SendButton(QAbstractButton):
    """The round send arrow, there once something is typed."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(32, 32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Send")

    def paintEvent(self, _e) -> None:  # noqa: N802
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QColor, QPainter, QPen
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(T.ACCENT))
        p.drawEllipse(QRectF(self.rect()).adjusted(1, 1, -1, -1))
        pen = QPen(QColor(T.ON_ACCENT), 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        x, y = self.width() / 2, self.height() / 2
        p.drawLine(QPointF(x, y + 6), QPointF(x, y - 6))
        p.drawLine(QPointF(x - 5, y - 1), QPointF(x, y - 6))
        p.drawLine(QPointF(x + 5, y - 1), QPointF(x, y - 6))


class MessageBox(QFrame):
    """Type, and a send arrow appears in the box - press it, or Enter - as
    in every messaging app (the owner, 30 Oct 2026: not everyone knows to
    press Enter). `active`: being typed in, or holding something typed."""

    sent = pyqtSignal(str)
    active = pyqtSignal(bool)

    def __init__(self, placeholder: str):
        super().__init__()
        self.setObjectName("obMsg")
        self.setFixedHeight(44)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 4, 6, 4)
        row.setSpacing(6)
        self.edit = QLineEdit()
        self.edit.setObjectName("obMsgEdit")
        self.edit.setPlaceholderText(placeholder)
        self.edit.setFrame(False)
        self.send = SendButton()
        self.send.hide()
        row.addWidget(self.edit, 1)
        row.addWidget(self.send)
        self.edit.textChanged.connect(self._changed)
        self.edit.returnPressed.connect(self.send_now)
        self.send.clicked.connect(self.send_now)
        self.edit.installEventFilter(self)
        self._on = False

    def text(self) -> str:
        return self.edit.text()

    def setText(self, text: str) -> None:  # noqa: N802 - as QLineEdit's
        self.edit.setText(text)

    def focus(self) -> None:
        self.edit.setFocus()

    def _changed(self, text: str) -> None:
        self.send.setVisible(bool(text.strip()))
        self._mark()

    def _mark(self) -> None:
        on = self.edit.hasFocus() or bool(self.edit.text().strip())
        if on != self._on:
            self._on = on
            self.setProperty("on", on)
            self.style().unpolish(self)
            self.style().polish(self)
            self.active.emit(on)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        from PyQt6.QtCore import QEvent
        if obj is self.edit and event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self._mark()
        return False

    def send_now(self) -> None:
        text = self.edit.text().strip()
        if text:
            self.edit.clear()
            self.sent.emit(text)


# ── I'm curious about you ─────────────────────────────────────────────────────

class StatusPill(QFrame):
    """Where she is - speaking, thinking, listening, your turn - as one wide
    pill in the voice's own colour, HeyClicky's way. It is also a button: a
    tap starts listening and the next tap sends; a press held longer than
    HOLD_S works as the keys do (30 Oct: a click did nothing - it only
    listened while held). Small and round while they type."""

    held = pyqtSignal(bool)
    HOLD_S = 0.5

    def __init__(self):
        super().__init__()
        self.setObjectName("obPill")
        self.setFixedHeight(44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Tap to talk, tap again to send - or hold the keys")
        self._down_at: Optional[float] = None
        self._open = False                       # listening after a tap, until the next
        row = QHBoxLayout(self)
        self._row = row
        row.setContentsMargins(16, 0, 16, 0)
        row.setSpacing(8)
        row.addStretch(1)
        from PyQt6.QtWidgets import QLabel
        self.icon = QLabel()
        self.words = label("", "obPillText")
        row.addWidget(self.icon)
        row.addWidget(self.words)
        row.addStretch(1)
        self.state = ""
        self.set_state("connecting")

    def words_for(self, state: str) -> str:
        return {
            "connecting": "Getting ready…",
            "speaking": "Tora is speaking…",
            "thinking": "Thinking…",
            "listening": ("Listening… tap when you're done" if self._open
                          else "Listening… let go when you're done"),
            "your_turn": f"Your turn - hold {' + '.join(hotkey_keys())}",
        }.get(state, "")

    def set_state(self, state: str, words: str = "") -> None:
        from PyQt6.QtGui import QPixmap
        from ui import tokens
        from ui.app_widgets import icon
        self.state = state
        self.words.setText(words or self.words_for(state))
        colour = tokens.STATE_GLOW.get(state) or T.ACCENT
        name = {"speaking": "megaphone", "thinking": "sparkles", "connecting": "sparkles",
                "done": "check"}.get(state, "mic")
        self.icon.setPixmap(QPixmap(icon(name, T.TEXT, 16, self.devicePixelRatioF())))
        self.setStyleSheet(f"QFrame#obPill {{ background: {_soft(colour, 0.16)}; "
                           f"border: 1px solid {_soft(colour, 0.6)}; border-radius: 22px; }}")

    def set_compact(self, on: bool) -> None:
        """Round while they type: the icon alone, in the middle."""
        self.words.setVisible(not on)
        self._row.setContentsMargins(*((0, 0, 0, 0) if on else (16, 0, 16, 0)))
        self._row.setSpacing(0 if on else 8)
        if on:
            self.setFixedWidth(44)
        else:
            self.setMinimumWidth(0)
            self.setMaximumWidth(16777215)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self._down_at = time.monotonic()
        if not self._open:
            self.held.emit(True)                 # listening from the press

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton or self._down_at is None:
            return
        held = time.monotonic() - self._down_at
        self._down_at = None
        if self._open:                           # the second tap: send
            self._open = False
            self.held.emit(False)
        elif held >= self.HOLD_S:                # held like the keys: let go to send
            self.held.emit(False)
        else:                                    # a tap: keep listening until the next
            self._open = True
            self.set_state("listening")


class _Said(QWidget):
    """One line of the conversation: hers on the left, theirs on the right."""

    def __init__(self, hers: bool, text: str = ""):
        super().__init__()
        self.hers = hers
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.text = label(text, "obHers" if hers else "obYours", wrap=True)
        self.text.setMaximumWidth(440)
        if not hers:
            row.addStretch(1)
        row.addWidget(self.text)
        if hers:
            row.addStretch(1)

    def add(self, piece: str) -> None:
        self.text.setText(self.text.text() + piece)

    def set(self, text: str) -> None:
        self.text.setText(" ".join(text.split()))


class ChatBoard(QFrame):
    """The conversation on a chat wallpaper - faint doodles, as a messaging
    app's (30 Oct: a dotted grid in a frame read as a canvas) - newest at
    the bottom and always in view: whenever it grows, it follows."""

    TILE = 132

    def __init__(self, height: int = 250):
        super().__init__()
        self.setObjectName("obBoard")
        self.setFixedHeight(height)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        inner = QWidget()
        inner.setObjectName("obBoardInner")
        self.lines = QVBoxLayout(inner)
        self.lines.setContentsMargins(14, 12, 14, 12)
        self.lines.setSpacing(8)
        self.lines.addStretch(1)                      # the newest sits at the bottom
        self.scroll = QScrollArea()
        self.scroll.setObjectName("obChat")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(inner)
        lay.addWidget(self.scroll)
        self.scroll.verticalScrollBar().rangeChanged.connect(self._follow)

    def _follow(self, _low: int, high: int) -> None:
        self.scroll.verticalScrollBar().setValue(high)

    def _tile(self):
        """One square of the wallpaper: little line doodles, faint."""
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
        dpr = self.devicePixelRatioF()
        n = self.TILE
        pm = QPixmap(round(n * dpr), round(n * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        ink = QColor(T.TEXT)
        ink.setAlphaF(0.07)
        pen = QPen(ink, 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        bubble = QPainterPath()                               # a speech bubble
        bubble.addRoundedRect(QRectF(14, 14, 26, 18), 6, 6)
        bubble.moveTo(20, 32)
        bubble.lineTo(17, 38)
        bubble.lineTo(26, 32)
        p.drawPath(bubble)
        p.drawEllipse(QPointF(92, 24), 6, 6)                  # a circle
        p.drawLine(QPointF(64, 64), QPointF(76, 64))          # a plus
        p.drawLine(QPointF(70, 58), QPointF(70, 70))
        wave = QPainterPath(QPointF(14, 96))                  # a squiggle
        wave.cubicTo(QPointF(22, 86), QPointF(30, 106), QPointF(38, 96))
        wave.cubicTo(QPointF(46, 86), QPointF(54, 106), QPointF(60, 96))
        p.drawPath(wave)
        star = QPainterPath(QPointF(104, 92))                 # a sparkle
        for x, y in ((107, 101), (116, 104), (107, 107), (104, 116), (101, 107), (92, 104),
                     (101, 101), (104, 92)):
            star.lineTo(QPointF(x, y))
        p.drawPath(star)
        p.drawRoundedRect(QRectF(48, 22, 10, 10), 3, 3)       # a square
        p.end()
        return pm

    def paintEvent(self, e) -> None:  # noqa: N802
        super().paintEvent(e)
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QPainter, QPainterPath
        if getattr(self, "_tile_theme", None) != T.TEXT:      # made again when the theme changes
            self._tile_pm, self._tile_theme = self._tile(), T.TEXT
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), 16, 16)
        p.setClipPath(clip)
        p.drawTiledPixmap(self.rect(), self._tile_pm)


class InterviewStep(_HeldKeys):
    """Four questions, out loud: her lines and theirs as chat bubbles on a
    dotted board, and under it one row - where she is (StatusPill, held it
    is the keys) and a box to type in (MessageBox) - as HeyClicky's. Proven
    when she has all four (interview_done) - saved as the profile, the
    starter skills and her memory (onboarding_interview.save). Without a
    voice it says so and lets them carry on."""

    TITLE = "I'm curious about you."
    LINES = "Four quick questions, so I can set myself up for you."
    WIDE = True

    def __init__(self):
        super().__init__()
        self.board = ChatBoard()
        self.chat_lay = self.board.lines
        self.scroll = self.board.scroll
        self.body.addWidget(self.board)
        self.body.addSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.pill = StatusPill()
        self.pill.held.connect(self.hold)
        self.status = self.pill.words
        self.box = MessageBox("Or type here")
        self.box.sent.connect(self._typed)
        self.box.active.connect(self.pill.set_compact)
        row.addWidget(self.pill, 1)
        row.addWidget(self.box, 1)
        self.body.addLayout(row)
        self.skip_link = button("Skip these questions", "link")
        self.skip_link.clicked.connect(self.skip)
        skip_row = QHBoxLayout()
        skip_row.addStretch(1)
        skip_row.addWidget(self.skip_link)
        self.body.addLayout(skip_row)
        self._hers: Optional[_Said] = None
        self._yours: Optional[_Said] = None
        self._voice = None
        self.answers = None

    # ── the conversation ─────────────────────────────────────────────────────
    def enter(self) -> None:
        super().enter()
        if self.panel is not None and self._voice is None:
            self._voice = tour_voice(self.panel)
            self.panel.voice_events.event.connect(self._event)
        if self._voice is not None and not self.ready:
            self._voice.start()
            self._voice.begin_interview()

    def _said(self, hers: bool) -> _Said:
        line = _Said(hers)
        self.chat_lay.addWidget(line)
        line.show()
        return line

    def lines(self) -> list[tuple[bool, str]]:
        """(hers, words) for every line shown, oldest first."""
        out = []
        for i in range(self.chat_lay.count()):
            w = self.chat_lay.itemAt(i).widget()
            if isinstance(w, _Said):
                out.append((w.hers, w.text.text()))
        return out

    def _event(self, kind: str, value) -> None:
        if self.panel is None or self.panel.step() is not self:
            return
        if kind == "state":
            if value == "offline":
                self.pill.set_state("offline", "I can't reach my voice right now")
                self._note("I couldn't connect just now - carry on, and tell me about "
                           "yourself any time.")
                self.done()
            elif not self.ready:
                self.pill.set_state(str(value))
        elif kind == "tora":
            if self._hers is None:
                self._hers = self._said(True)
            self._hers.add(str(value))
        elif kind == "tora_end":
            (self._hers or self._said(True)).set(str(value))
            self._hers = None
        elif kind == "you":
            if self._yours is None:
                self._yours = self._said(False)
            self._yours.add(str(value))
        elif kind == "you_end":
            (self._yours or self._said(False)).set(str(value))
            self._yours = None
        elif kind == "done":
            self.answers = value
            save(value)
            name = getattr(value, "name", "")
            self.pill.set_state("done", f"Got it{', ' + name if name else ''} - thank you!")
            self.done()
        elif kind == "skip":
            self._skipped(value)

    MOVE_ON_MS = 1400        # her "no problem", then on to the next step

    def skip(self) -> None:
        """The link: no more questions. Through her voice when there is one,
        so she stops asking too."""
        if self._voice is not None and hasattr(self._voice, "skip"):
            self._voice.skip()
        else:
            self._skipped(None)

    def _skipped(self, answers) -> None:
        if self.ready:
            return
        if answers is not None and (getattr(answers, "uses", None) or getattr(answers, "facts", None)):
            self.answers = answers
            save(answers)                          # what she learned before they skipped
        self.pill.set_state("done", "No problem - let's move on.")
        self.done()
        QTimer.singleShot(self.MOVE_ON_MS, self._move_on)

    def _move_on(self) -> None:
        if self.panel is not None and self.panel.step() is self:
            self.panel.next()

    def _note(self, words: str) -> None:
        self._said(True).set(words)

    def _typed(self, text: str) -> None:
        self._said(False).set(text)
        if self._voice is not None:
            self._voice.type(text)

    def hold(self, down: bool, now: Optional[float] = None) -> None:
        if down and not self.ready:
            self.pill.set_state("listening")
        if self._voice is not None:
            self._voice.hold(down)


# ── phase D: her answers about your own screen ────────────────────────────────

def screen_picture(trail: Sequence[QPointF] = (), max_w: int = 1280) -> str:
    """The screen under the mouse as a base64 JPEG for her, with what they
    drew round (global, logical px) on it in the cursor's blue."""
    import base64
    from PyQt6.QtCore import QBuffer, QIODevice
    from PyQt6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPen, QPolygonF
    screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
    if screen is None:
        return ""
    shot = screen.grabWindow(0)
    if shot.isNull():
        return ""
    if len(trail) > 1:
        origin = QPointF(screen.geometry().topLeft())
        p = QPainter(shot)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(T.ACCENT), 6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawPolyline(QPolygonF([pt - origin for pt in trail]))
        p.end()
    if shot.width() > max_w:
        shot = shot.scaledToWidth(max_w, Qt.TransformationMode.SmoothTransformation)
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    shot.save(buf, "JPEG", 72)
    return base64.b64encode(bytes(buf.data())).decode("ascii")


class TrailOverlay(QWidget):
    """The line they draw while they hold the keys, over the whole screen -
    clicks go straight through it."""

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.points: list[QPointF] = []

    def begin(self) -> None:
        from PyQt6.QtGui import QCursor, QGuiApplication
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.geometry())
        self.points = []
        self.update()
        self.show()
        from ui.onboarding_shape import keep_out_of_pictures
        keep_out_of_pictures(self)              # it is drawn on her picture already

    def add(self, global_pos: QPointF) -> None:
        if not self.points or (global_pos - self.points[-1]).manhattanLength() >= 2:
            self.points.append(QPointF(global_pos))
            self.update()

    def length(self) -> float:
        return sum((b - a).manhattanLength() for a, b in zip(self.points, self.points[1:]))

    def paintEvent(self, _e) -> None:  # noqa: N802
        if len(self.points) < 2:
            return
        from PyQt6.QtGui import QColor, QPainter, QPen, QPolygonF
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        glow = QColor(T.ACCENT)
        glow.setAlphaF(0.25)
        origin = QPointF(self.geometry().topLeft())
        line = QPolygonF([pt - origin for pt in self.points])
        for colour, width in ((glow, 14), (QColor(T.ACCENT), 5)):
            pen = QPen(colour, width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.drawPolyline(line)


class _AsksAboutScreen(_HeldKeys):
    """Hold the keys and ask about your screen: she is handed the screen as
    the question ends - with what they circled, when TRAIL. The panel stays
    where it is (keep_out_of_pictures leaves it out of her picture), and the
    line they drew stays until she has answered. Saying nothing is fine:
    she is asked about what they marked (QUIET_ASK). Proven by her answer -
    or, with no voice, by trying."""

    HOLD_S = 0.4
    TRAIL = False
    NOTE = "[The user is asking about their screen; this picture is their screen right now.]"
    LINE_STAYS_S = 25       # the line goes after her answer, or after this

    def __init__(self):
        super().__init__()
        self._voice = None
        self._asked = False
        self.trail: Optional[TrailOverlay] = None
        self._follow = QTimer(self)
        self._follow.setInterval(16)
        self._follow.timeout.connect(self._track)

    def build_tail(self) -> None:
        self.status = label("waiting for you…", "obNote")
        self.status.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.body.addWidget(self.status)
        self.reply = label("", "obReply", wrap=True)
        self.reply.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.body.addWidget(self.reply)

    def enter(self) -> None:
        super().enter()
        if self.panel is not None and self._voice is None:
            self._voice = tour_voice(self.panel)
            self.panel.voice_events.event.connect(self._event)
        if self._voice is not None:
            self._voice.start()

    def leave(self) -> None:
        super().leave()
        self._follow.stop()
        trail, self.trail = self.trail, None
        if trail is not None:            # a window of its own: gone with the step, not kept
            trail.hide()
            trail.deleteLater()

    def _clear_line(self) -> None:
        if self.trail is not None:
            self.trail.hide()

    def _track(self) -> None:
        from PyQt6.QtGui import QCursor
        if self.trail is not None:
            self.trail.add(QPointF(QCursor.pos()))

    def drew(self) -> bool:
        return self.trail is not None and self.trail.length() >= 60

    def hold(self, down: bool, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        for cap in self.keys:
            cap.set_lit(down)
        if down:
            self._down_at = now
            self.reply.setText("")
            if self.TRAIL:
                self.trail = self.trail or TrailOverlay()
                self.trail.begin()
                self._follow.start()
            if self._voice is not None:
                self._voice.hold(True)
            return
        held = now - self._down_at if self._down_at is not None else 0.0
        self._down_at = None
        self._follow.stop()
        long_enough = held >= self.HOLD_S and (not self.TRAIL or self.drew())
        look = (screen_picture(self.trail.points if self.TRAIL and self.trail else ()), self.NOTE) \
            if long_enough else None
        if self._voice is not None:
            self._voice.hold(False, look=look)
        if not long_enough:
            self._clear_line()
            self.status.setText(self.TOO_SHORT)
            return
        self._asked = True
        self.status.setText("Thinking…")
        QTimer.singleShot(self.LINE_STAYS_S * 1000, self._clear_line)

    TOO_SHORT = "Hold them a little longer while you ask."

    def _event(self, kind: str, value) -> None:
        if self.panel is None or self.panel.step() is not self:
            return
        if kind == "tora" and self._asked:
            self.reply.setText(self.reply.text() + str(value))
        elif kind == "tora_end" and self._asked:
            self.reply.setText(" ".join(str(value).split()))
            self.status.setText("That's me, on your screen.")
            QTimer.singleShot(1500, self._clear_line)
            self.done()
        elif kind == "state" and value == "offline":
            self._clear_line()
            self.status.setText("I can't reach my voice right now - try it any time after the tour.")
            self.done()


def circle_clip_path():
    """A recording of HeyTora circling something and answering - an
    animated WebP made with tools/clip_to_webp.py. None until there is one."""
    from ui.tokens import assets_dir
    path = assets_dir() / "onboarding" / "circle.webp"
    return path if path.exists() else None


class Clip(QFrame):
    """A short looping recording, as HeyClicky shows its own (the owner, 30
    Oct 2026: a real one, not a drawing). QMovie: no video player to ship."""

    def __init__(self, path, width: int = 420):
        super().__init__()
        from PyQt6.QtCore import QSize
        from PyQt6.QtGui import QMovie
        from PyQt6.QtWidgets import QLabel
        self.setObjectName("obClip")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.view = QLabel()
        self.movie = QMovie(str(path))
        self.movie.jumpToFrame(0)
        first = self.movie.currentImage().size()
        if first.width() > 0:
            self.movie.setScaledSize(QSize(width, round(first.height() * width / first.width())))
            self.view.setFixedSize(self.movie.scaledSize())
        self.view.setMovie(self.movie)
        lay.addWidget(self.view)

    def start(self) -> None:
        self.movie.start()

    def stop(self) -> None:
        self.movie.stop()


class CircleStep(_AsksAboutScreen):
    """Draw round anything while you ask: the line is on the screen as you
    draw and on the picture she is handed. Above it, a real recording of it
    done (circle_clip_path) - when there is one."""

    TITLE = "Circle anything, ask about it"
    LINES = "Hold the keys, draw round something with your mouse, and ask about it."
    TRAIL = True
    NOTE = ("[The user circled something on their screen while asking - the blue line on this "
            "picture. Answer about what is inside it.]")
    TOO_SHORT = "Draw round something with the mouse while you hold them."

    def __init__(self):
        super().__init__()
        path = circle_clip_path()
        self.clip = Clip(path) if path is not None else None
        if self.clip is not None:
            self.body.addSpacing(4)
            self.body.addLayout(_centred(self.clip))
        self.body.addSpacing(12)
        row, self.keys = self.keycaps("Hold", "draw round it, and ask")
        self.body.addLayout(row)
        self.build_tail()

    def enter(self) -> None:
        super().enter()
        if self.clip is not None:
            self.clip.start()

    def leave(self) -> None:
        super().leave()
        if self.clip is not None:
            self.clip.stop()


# ── I can drive your computer too ─────────────────────────────────────────────

class ListWindow(QFrame):
    """A small "Your list" window inside the panel, for her to fill in -
    with room for five rows from the start, so filling it never squashes
    it (30 Oct)."""

    ROOM = 5 * 26

    def __init__(self):
        super().__init__()
        self.setObjectName("obList")
        self.setFixedWidth(340)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)
        lay.addWidget(label("Your list", "obListTitle"))
        rows = QWidget()
        rows.setMinimumHeight(self.ROOM)
        self.rows = QVBoxLayout(rows)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(4)
        self.rows.addStretch(1)
        lay.addWidget(rows)
        self.box = QLineEdit()
        self.box.setObjectName("obType")
        self.box.setPlaceholderText("Add an item")
        self.box.returnPressed.connect(self.add)
        lay.addWidget(self.box)
        self.ticks: list[QCheckBox] = []

    def add(self) -> Optional[QCheckBox]:
        text = self.box.text().strip()
        if not text:
            return None
        tick = QCheckBox(text)
        tick.setObjectName("obListItem")
        self.rows.insertWidget(self.rows.count() - 1, tick)
        tick.show()
        self.ticks.append(tick)
        self.box.clear()
        return tick

    def items(self) -> list[tuple[str, bool]]:
        return [(t.text(), t.isChecked()) for t in self.ticks]

    def clear(self) -> None:
        for tick in self.ticks:
            self.rows.removeWidget(tick)
            tick.deleteLater()
        self.ticks = []
        self.box.clear()


class DriveStep(_HeldKeys):
    """Ask her to make a list and she takes the mouse: it glides to "Your
    list", the items are typed in and two are ticked off. Her voice chooses
    the items (make_list); the moving and typing happen here. Esc, or
    moving the mouse, gives it back at once - and asking again starts a
    fresh list. If she answers an ask without making one (29 Oct: the second
    time she only talked), it drives anyway: an ask never does nothing."""

    TITLE = "I can drive your computer too"
    LINES = ("Ask me to do it and I'll take the mouse while you watch. Move it any time "
             "and it's yours again.")
    ASK = "Please drive my computer and make a list"
    PLAIN = ["Reply to Sam", "Book the dentist", "Buy milk"]   # with no voice
    MOVE_MOUSE = True        # tests drive it without the real cursor
    GLIDE_MS = 800           # slow enough to watch (the owner, 29 Oct: "a little slower")
    KEY_MS = 75
    PAUSE_MS = 250           # a breath between one move and the next
    YOURS_PX = 10            # the mouse this far from where she put it: theirs again

    def __init__(self):
        super().__init__()
        self.list = ListWindow()
        self.body.addSpacing(10)
        self.body.addLayout(_centred(self.list))
        self.body.addSpacing(12)
        row, self.keys = self.keycaps("Hold", "and say")
        self.body.addLayout(row)
        self.body.addSpacing(6)
        self.body.addLayout(_centred(Bubble(self.ASK)))
        self.instead = button("Or just let her do it", "link")
        self.instead.clicked.connect(self._ask_by_click)
        self.body.addLayout(_centred(self.instead))
        self.status = label("", "obNote")
        self.status.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.body.addWidget(self.status)
        self._voice = None
        self._plan: list[tuple] = []
        self._at = 0.0
        self._from = QPointF()
        self._put: Optional[QPointF] = None
        self._typed = 0
        self.driving = False
        self._clock = QTimer(self)
        self._clock.setInterval(16)
        self._clock.timeout.connect(self.tick)
        self._waiting = False                # asked, and no list from her yet
        self._last: list[str] = []

    def enter(self) -> None:
        super().enter()
        if self.panel is not None and self._voice is None:
            self._voice = tour_voice(self.panel)
            self.panel.voice_events.event.connect(self._event)
        if self._voice is not None:
            self._voice.start()

    def leave(self) -> None:
        super().leave()
        self.take_back("")

    def hold(self, down: bool, now: Optional[float] = None) -> None:
        for cap in self.keys:
            cap.set_lit(down)
        if self.driving:
            return                            # the keys don't talk over her driving
        if self._voice is not None:
            self._voice.hold(down)
        if not down:
            self._waiting = True
            self.status.setText("Thinking…")

    def _ask_by_click(self) -> None:
        self._waiting = True
        self.status.setText("Thinking…")
        if self._voice is not None:
            self._voice.type(self.ASK)
        else:
            self.drive(self.PLAIN)

    def _event(self, kind: str, value) -> None:
        if self.panel is None or self.panel.step() is not self:
            return
        if kind == "list":
            self._waiting = False
            self._last = list(value)
            self.drive(self._last)
        elif kind == "tora_end" and self._waiting and not self.driving:
            # Her answer came and no list with it: drive anyway, after her words.
            self._waiting = False
            QTimer.singleShot(self.AFTER_WORDS_MS, self._drive_anyway)
        elif kind == "state" and value == "offline" and self._waiting and not self.driving:
            self._waiting = False
            self.drive(self._last or self.PLAIN)

    AFTER_WORDS_MS = 600

    def _drive_anyway(self) -> None:
        self.drive(self._last or self.PLAIN)

    # ── driving ──────────────────────────────────────────────────────────────
    def drive(self, items: Sequence[str]) -> None:
        """Plan the moves - to the box, each item typed and added, two ticked
        - and start."""
        if self.driving or (self.panel is not None and self.panel.step() is not self):
            return                                # under way, or they have moved on
        self.list.clear()                         # each ask is a fresh list
        plan: list[tuple] = []
        for item in list(items)[:5]:
            plan += [("glide", "box"), ("type", item), ("add",)]
        for i in range(min(2, len(items))):
            plan += [("glide", i), ("tick", i)]
        self._plan = plan
        self.driving = True
        self.status.setText("Driving… press Esc to take it back")
        self._begin()
        self._clock.start()

    def _target(self, where) -> QPointF:
        w = self.list.box if where == "box" else self.list.ticks[where]
        r = w.rect()
        local = r.center() if where == "box" else QPointF(10, r.height() / 2).toPoint()
        return QPointF(w.mapToGlobal(local))

    def _begin(self) -> None:
        from PyQt6.QtGui import QCursor
        self._at = time.monotonic() + (self.PAUSE_MS / 1000 if self.MOVE_MOUSE else 0)
        self._from = QPointF(QCursor.pos()) if self.MOVE_MOUSE else QPointF()
        self._typed = 0

    def _theirs(self) -> bool:
        """They moved the mouse, or pressed Esc."""
        if not self.MOVE_MOUSE:
            return False
        from PyQt6.QtGui import QCursor
        moved = self._put is not None and (QPointF(QCursor.pos()) - self._put).manhattanLength() > self.YOURS_PX
        try:
            from hotkey import _is_down
            esc = _is_down("esc")
        except Exception:  # noqa: BLE001
            esc = False
        return moved or esc

    def tick(self, now: Optional[float] = None) -> None:
        """One frame of the drive (a test calls it until it is over)."""
        if not self.driving:
            return
        if self._theirs():
            self.take_back("It's yours again - that's how quickly you can take over. Ask me again any time.")
            return
        if not self._plan:
            self.take_back("Done. That's what taking the mouse looks like.")
            return
        now = time.monotonic() if now is None else now
        if now < self._at:
            return                                # the pause before this move
        action = self._plan[0]
        if action[0] == "glide":
            k = min((now - self._at) * 1000 / self.GLIDE_MS, 1.0) if self.MOVE_MOUSE else 1.0
            if self.MOVE_MOUSE:
                from PyQt6.QtGui import QCursor
                to = self._target(action[1])
                ease = 1 - (1 - k) ** 3
                pos = self._from + (to - self._from) * ease
                QCursor.setPos(pos.toPoint())
                self._put = QPointF(pos.toPoint())
            if k >= 1.0:
                self._next()
        elif action[0] == "type":
            text = action[1]
            due = int((now - self._at) * 1000 / self.KEY_MS) + 1 if self.MOVE_MOUSE else len(text)
            self._typed = min(len(text), max(self._typed, due))
            self.list.box.setText(text[:self._typed])
            if self._typed >= len(text):
                self._next()
        elif action[0] == "add":
            self.list.add()
            self._next()
        elif action[0] == "tick":
            self.list.ticks[action[1]].setChecked(True)
            self._next()

    def _next(self) -> None:
        self._plan.pop(0)
        self._begin()

    def take_back(self, words: str) -> None:
        was = self.driving
        self.driving = False
        self._clock.stop()
        self._plan = []
        self._put = None
        if was:
            if words:
                self.status.setText(words)
            self.done()


# ── not in the mood to talk? type ─────────────────────────────────────────────

class TypeStep(Step):
    """Ctrl, Ctrl: a box to type to her, and her answer. The app's own
    Ctrl Ctrl (which opens the shape) is borrowed while the step is up."""

    TITLE = "Not in the mood to talk? Type"
    LINES = "Tap Ctrl twice, quickly, and type to me instead."

    tapped = pyqtSignal()                       # from the hook's thread

    def __init__(self):
        super().__init__()
        from ui.onboarding_parts import Keycap
        self.key = Keycap("Ctrl", px=26)
        self.body.addSpacing(18)
        self.body.addLayout(_centred(self.key))
        self.body.addSpacing(10)
        self.hint = label("tap it twice", "obNote")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.body.addWidget(self.hint)
        self.box = MessageBox("Type to Tora")
        self.box.setFixedWidth(380)
        self.box.sent.connect(self._send)
        self.box.hide()
        self.body.addSpacing(10)
        self.body.addLayout(_centred(self.box))
        self.reply = label("", "obReply", wrap=True)
        self.reply.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.body.addWidget(self.reply)
        self.tapped.connect(self.open_box)
        self._tap = None
        self._voice = None
        self._sent = False

    def enter(self) -> None:
        live = self.panel.live if self.panel is not None else None
        if live is not None and hasattr(live, "claim_type_tap"):
            live.claim_type_tap()
        try:
            from hotkey import DoubleTap
            self._tap = DoubleTap(self.tapped.emit)
            self._tap.start()
        except Exception:  # noqa: BLE001 - no keyboard hook: the box opens anyway
            self._tap = None
            self.open_box()
        if self.panel is not None and self._voice is None:
            self._voice = tour_voice(self.panel)
            self.panel.voice_events.event.connect(self._event)
        if self._voice is not None:
            self._voice.start()

    def leave(self) -> None:
        if self._tap is not None:
            self._tap.stop()
            self._tap = None
        live = self.panel.live if self.panel is not None else None
        if live is not None and hasattr(live, "release_type_tap"):
            live.release_type_tap()

    def open_box(self) -> None:
        self.key.set_lit(True)
        QTimer.singleShot(260, self._unlight)
        self.hint.setText("That's it - now type")
        self.box.show()
        self.box.focus()
        self.grew()

    def _unlight(self) -> None:
        self.key.set_lit(False)

    def _send(self, text: str) -> None:
        self._sent = True
        self.reply.setText("…")
        if self._voice is not None:
            self._voice.type(text)
        else:
            self.done()

    def _event(self, kind: str, value) -> None:
        if self.panel is None or self.panel.step() is not self or not self._sent:
            return
        if kind == "tora":
            self.reply.setText(("" if self.reply.text() == "…" else self.reply.text()) + str(value))
        elif kind == "tora_end":
            self.reply.setText(" ".join(str(value).split()))
            self.grew()
            self.done()
        elif kind == "state" and value == "offline":
            self.reply.setText("I can't reach my voice right now - but that's where my answer goes.")
            self.grew()
            self.done()


# ── make me yours: voice and colour ───────────────────────────────────────────

class MakeMeYoursStep(Step):
    """Her voice and her cursor's colour, the Settings tiles - second, as
    HeyClicky asks them first (the owner, 30 Oct 2026), so the rest of the
    tour is in the voice they chose. A tap on a voice plays her hello (the
    bundled clip - nothing spent). Ready from the start: she has both."""

    TITLE = "Make me yours"
    LINES = "Pick my voice and my colour. You can change both any time in Settings."
    WIDE = True
    READY = True
    COLUMNS = 4

    def __init__(self):
        super().__init__()
        from PyQt6.QtWidgets import QGridLayout
        from audio.tts.gemini_tts import VOICES
        from config import cfg
        from ui import tokens
        from ui.shape_widgets import CursorTile, VoiceTile
        now = cfg.voice or "Zephyr"
        self.tiles = []
        voices = QWidget()
        grid = QGridLayout(voices)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        for i, name in enumerate(list(VOICES) + ([now] if now not in VOICES else [])):
            tile = VoiceTile(name, tokens.VOICE_COLOURS[i % len(tokens.VOICE_COLOURS)])
            tile.setChecked(name == now)
            tile.clicked.connect(lambda _c=False, n=name: self.pick(n))
            grid.addWidget(tile, i // self.COLUMNS, i % self.COLUMNS)
            self.tiles.append(tile)
        self.body.addWidget(label("MY VOICE", "obGroup"))
        self.body.addWidget(voices)
        self.body.addSpacing(6)
        self.body.addWidget(label("MY COLOUR", "obGroup"))
        colour = cfg.cursor_colour()
        self.colours = []
        row = QHBoxLayout()
        row.setSpacing(8)
        for key, name, hex_colour in tokens.CURSOR_COLOURS:
            tile = CursorTile(key, name, hex_colour)
            tile.setChecked(key == colour)
            tile.clicked.connect(lambda _c=False, k=key: self.paint(k))
            row.addWidget(tile, 1)
            self.colours.append(tile)
        self.body.addLayout(row)
        self.chosen = now
        self.colour = colour

    def _live(self):
        return self.panel.live if self.panel is not None else None

    def pick(self, name: str) -> None:
        from config import cfg
        from ui.onboarding_parts import _clip_envelope, play_voice
        self.chosen = name
        for tile in self.tiles:
            tile.setChecked(tile.name == name)
            if tile.name != name:
                tile.stop()
        if play_voice(name):
            next(t for t in self.tiles if t.name == name).play(_clip_envelope(name))
        live = self._live()
        if live is not None and hasattr(live, "set_voice"):
            live.set_voice(name)                  # the running app: saved and used at once
        else:
            cfg.set_voice(name)

    def paint(self, key: str) -> None:
        """Her cursor in this colour - at once, beside the mouse."""
        self.colour = key
        for tile in self.colours:
            tile.setChecked(tile.key == key)
        bridge = getattr(self._live(), "bridge", None)
        if bridge is not None and hasattr(bridge, "set_cursor_colour"):
            bridge.set_cursor_colour(key)
        else:
            from config import cfg
            cfg.set_cursor_colour(key)


# ── there's one more thing… ───────────────────────────────────────────────────

class _JobCard(QFrame):
    """A job she made ready: its title and why."""

    def __init__(self, job):
        super().__init__()
        self.setObjectName("obJob")
        self.setFixedWidth(400)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(2)
        lay.addWidget(label("READY TO START", "obJobTag"))
        lay.addWidget(label(job.title, "obJobTitle", wrap=True))
        if job.why:
            lay.addWidget(label(job.why, "obNote", wrap=True))
        from PyQt6.QtWidgets import QGraphicsOpacityEffect
        self._fade = QGraphicsOpacityEffect(self)
        self._fade.setOpacity(0.0)
        self.setGraphicsEffect(self._fade)

    def appear(self, after_ms: int) -> None:
        """In its place from the start (the panel fits it), faded in in turn."""
        QTimer.singleShot(after_ms, self._fade_in)

    def _fade_in(self) -> None:
        from PyQt6.QtCore import QPropertyAnimation
        a = QPropertyAnimation(self._fade, b"opacity", self)
        a.setDuration(260)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.start()


class OneMoreStep(Step):
    """Show me: she makes two or three jobs from the interview (ready_jobs)
    and they wait on Home as "Ready to start" cards (ui/ready_jobs.py). With
    no voice - or if she only talks - they come from the starter skills for
    their work: the step always ends with something to do."""

    TITLE = "There's one more thing…"
    LINES = "From what you told me, I can get a few jobs ready for you."
    FALLBACK_S = 20          # no jobs from her by then: the starter skills' own
    STAGGER_MS = 260

    def __init__(self):
        super().__init__()
        self.show_me = button("Show me", "primary")
        self.show_me.setMinimumWidth(140)
        self.show_me.clicked.connect(self.make)
        self.body.addSpacing(24)
        self.body.addLayout(_centred(self.show_me))
        self.status = label("", "obNote")
        self.status.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.body.addWidget(self.status)
        self.cards = QVBoxLayout()
        self.cards.setSpacing(8)
        self.body.addSpacing(6)
        self.body.addLayout(self.cards)
        self.jobs: list = []
        self._voice = None
        self._asked = False
        self._late = QTimer(self)
        self._late.setSingleShot(True)
        self._late.timeout.connect(self._plain)

    def enter(self) -> None:
        if self.panel is not None and self._voice is None:
            self._voice = tour_voice(self.panel)
            self.panel.voice_events.event.connect(self._event)

    def leave(self) -> None:
        self._late.stop()

    def make(self) -> None:
        if self._asked:
            return
        self._asked = True
        self.show_me.hide()
        self.status.setText("Getting them ready…")
        if self._voice is None:
            self._plain()
            return
        self._voice.start()
        self._voice.one_more_thing()
        self._late.start(self.FALLBACK_S * 1000)

    def _event(self, kind: str, value) -> None:
        if self.panel is None or self.panel.step() is not self or not self._asked or self.jobs:
            return
        if kind == "jobs":
            self.ready_with(list(value))
        elif kind == "tora_end":
            QTimer.singleShot(1500, self._plain)          # she only talked: theirs fill in
        elif kind == "state" and value == "offline":
            self._plain()

    def _uses(self) -> list[str]:
        answers = getattr(self._voice, "answers", None)
        if getattr(answers, "uses", None):
            return list(answers.uses)
        from config import cfg
        return cfg.profile()[1]

    def _plain(self) -> None:
        if not self.jobs:
            from ui.ready_jobs import from_skills
            self.ready_with(from_skills(self._uses()))

    def ready_with(self, jobs: list) -> None:
        from ui import ready_jobs
        if self.jobs or not jobs:
            return
        self._late.stop()
        self.jobs = jobs[:ready_jobs.MOST]
        ready_jobs.save(self.jobs)
        for i, job in enumerate(self.jobs):
            card = _JobCard(job)
            self.cards.addLayout(_centred(card))
            card.appear(i * self.STAGGER_MS)
        self.grew()
        self.status.setText("They're waiting on Home - start one whenever you like.")
        self.done()


# ── plans ─────────────────────────────────────────────────────────────────────

def _plans_page(bridge):
    """The Settings plan cards and their payment note - no page title (the
    step has one) and none of what follows them there (billing, credits)."""
    from PyQt6.QtWidgets import QLabel
    from ui.settings_tabs import PLANS_NOTE, PlansTab

    class TourPlans(PlansTab):
        def build_title(self) -> QWidget:
            return QWidget()

        def build(self) -> None:
            add, done = self.body.add, []

            def only_the_plans(widget, *a, **kw):
                if done:
                    return
                add(widget, *a, **kw)
                if isinstance(widget, QLabel) and widget.text() == PLANS_NOTE:
                    done.append(True)
            self.body.add = only_the_plans
            try:
                super().build()
            finally:
                self.body.add = add

    page = TourPlans(bridge)
    page.body.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    head = page.body.top.itemAt(0).widget() if page.body.top.count() else None
    if head is not None and head.layout() is not None:
        head.layout().setContentsMargins(0, 0, 0, 0)    # the title's room, with no title
    return page


class PlansStep(Step):
    """The plans side by side, theirs marked - the last step, as HeyClicky's.
    Nothing to prove: Free is a fine place to start."""

    TITLE = "Pick a plan"
    LINES = "Start free - or go further with Pro or Max. You can change it any time."
    WIDE = True
    READY = True
    GO = "Start using HeyTora"

    def __init__(self):
        super().__init__()
        self.page: Optional[QWidget] = None

    def enter(self) -> None:
        if self.page is not None:
            return
        live = self.panel.live if self.panel is not None else None
        try:
            self.page = _plans_page(getattr(live, "bridge", None))
        except Exception:  # noqa: BLE001 - plans are in Settings too
            import logging
            logging.getLogger("tora.onboarding").warning("could not show the plans", exc_info=True)
            self.page = label("Plans are in Settings → Plans & billing.", "obNote")
        self.page.setMinimumHeight(self.PAGE_H)      # the step's own stretch would squash it
        self.body.addWidget(self.page, 1)
        self.page.show()             # added to a step on show: hidden until shown, and unmeasured
        self.grew()

    PAGE_H = 430


def steps() -> list:
    """The tour's steps, in order. "Here's how I'd help you" went (30 Oct
    2026: it was the talk step again); voice and colour moved up to second."""
    return [HelloStep, MakeMeYoursStep, MicStep, TalkStep, InterviewStep, CircleStep, DriveStep,
            TypeStep, OneMoreStep, PlansStep]


def stylesheet() -> str:
    from ui import tokens
    return f"""
    QLabel#obBubble {{ background: {T.ACCENT_SOFT}; color: {T.TEXT}; border-radius: 14px;
                      padding: 6px 14px; font-size: 13px; font-weight: 600; }}
    QLabel#obNote {{ color: {T.TEXT_DIM}; font-size: 12px; }}
    QPushButton#obChip {{ background: {T.SURFACE}; border: 1px solid {T.BORDER}; color: {T.TEXT};
                         border-radius: {tokens.pill(30)}px; padding: 6px 14px; font-size: 12px; }}
    QPushButton#obChip:hover {{ background: {T.SURFACE_HOVER}; }}
    QLabel#obReply {{ color: {T.TEXT}; font-size: 13px; }}
    QScrollArea#obChat {{ background: transparent; border: none; }}
    QScrollArea#obChat > QWidget > QWidget {{ background: transparent; }}
    QLabel#obHers {{ background: {T.SURFACE}; color: {T.TEXT}; border: 1px solid {T.BORDER};
                    border-radius: 14px; border-top-left-radius: 4px; padding: 8px 12px;
                    font-size: 13px; }}
    QLabel#obYours {{ background: {T.ACCENT}; color: {T.ON_ACCENT}; border-radius: 14px;
                     border-top-right-radius: 4px; padding: 8px 12px; font-size: 13px; }}
    QLineEdit#obType {{ background: {T.SURFACE}; color: {T.TEXT}; border: 1px solid {T.BORDER};
                       border-radius: 10px; padding: 8px 12px; font-size: 13px; }}
    QLineEdit#obType:focus {{ border-color: {T.ACCENT}; }}
    QFrame#obList {{ background: {T.SURFACE}; border: 1px solid {T.BORDER}; border-radius: 12px; }}
    QLabel#obListTitle {{ color: {T.TEXT}; font-size: 13px; font-weight: 700; }}
    QCheckBox#obListItem {{ color: {T.TEXT}; font-size: 13px; }}
    QFrame#obMsg {{ background: {T.SURFACE}; border: 1px solid {T.BORDER}; border-radius: 22px; }}
    QFrame#obMsg[on="true"] {{ border: 1px solid {T.ACCENT}; }}
    QLineEdit#obMsgEdit {{ background: transparent; border: none; color: {T.TEXT}; font-size: 13px; }}
    QLabel#obPillText {{ color: {T.TEXT}; font-size: 13px; font-weight: 600; }}
    QFrame#obBoard {{ background: {T.SURFACE_SUNK}; border: none; border-radius: 16px; }}
    QWidget#obBoardInner {{ background: transparent; }}
    QLabel#obGroup {{ color: {T.TEXT_DIM}; font-size: 11px; font-weight: 700; letter-spacing: 1px; }}
    QFrame#obClip {{ border: 1px solid {T.BORDER}; border-radius: 12px; }}
    QFrame#obJob {{ background: {T.SURFACE}; border: 1px solid {T.BORDER}; border-radius: 12px; }}
    QLabel#obJobTag {{ color: {T.ACCENT}; font-size: 10px; font-weight: 700; letter-spacing: 1px; }}
    QLabel#obJobTitle {{ color: {T.TEXT}; font-size: 14px; font-weight: 700; }}
    """
