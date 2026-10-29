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
from PyQt6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLineEdit, QMenu, QPushButton,
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
        self.body.addSpacing(36)
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
    return voice


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
        self.body.addSpacing(40)
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


# ── I'm curious about you ─────────────────────────────────────────────────────

class _Said(QWidget):
    """One line of the conversation: hers on the left, theirs on the right."""

    def __init__(self, hers: bool, text: str = ""):
        super().__init__()
        self.hers = hers
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.text = label(text, "obHers" if hers else "obYours", wrap=True)
        self.text.setMaximumWidth(460)
        if not hers:
            row.addStretch(1)
        row.addWidget(self.text)
        if hers:
            row.addStretch(1)

    def add(self, piece: str) -> None:
        self.text.setText(self.text.text() + piece)

    def set(self, text: str) -> None:
        self.text.setText(" ".join(text.split()))


class InterviewStep(_HeldKeys):
    """Four questions, out loud: her lines and theirs as chat bubbles, a
    status line, and an answer by holding the keys or typing. Proven when
    she has all four (interview_done) - saved as the profile, the starter
    skills and her memory (onboarding_interview.save). Without a voice it
    says so and lets them carry on: Settings has the same answers."""

    TITLE = "I'm curious about you."
    LINES = "Four quick questions, so I can set myself up for you."
    WIDE = True

    STATES = {
        "connecting": "Getting ready…",
        "speaking": "Tora is speaking…",
        "thinking": "Thinking…",
        "listening": "Listening…",
        "your_turn": "Your turn - hold the keys, or type",
    }

    def __init__(self):
        super().__init__()
        self.chat = QWidget()
        self.chat_lay = QVBoxLayout(self.chat)
        self.chat_lay.setContentsMargins(4, 4, 4, 4)
        self.chat_lay.setSpacing(8)
        self.chat_lay.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("obChat")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.chat)
        self.scroll.setMinimumHeight(200)
        self.body.addSpacing(10)
        self.body.addWidget(self.scroll, 1)
        self.status = label(self.STATES["connecting"], "obStatus")
        self.status.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.body.addSpacing(10)
        self.body.addLayout(_centred(self.status))
        self.body.addSpacing(10)
        row, self.keys = self.keycaps("Hold", "to answer")
        self.body.addLayout(row)
        self.body.addSpacing(8)
        self.box = QLineEdit()
        self.box.setObjectName("obType")
        self.box.setPlaceholderText("Or type here, then press Enter")
        self.box.returnPressed.connect(self._typed)
        self.skip_link = button("Skip these questions", "link")
        self.skip_link.clicked.connect(self.skip)
        answer = QHBoxLayout()
        answer.addWidget(self.box, 1)
        answer.addSpacing(10)
        answer.addWidget(self.skip_link)
        self.body.addLayout(answer)
        self.body.addSpacing(12)
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
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(
            self.scroll.verticalScrollBar().maximum()))
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
                self.status.setText("I can't reach my voice right now")
                self._note("I couldn't connect just now - carry on, and tell me about "
                           "yourself any time.")
                self.done()
            elif not self.ready:
                self.status.setText(self.STATES.get(str(value), ""))
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
            self.status.setText(f"Got it{', ' + name if name else ''} - thank you!")
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
        self.status.setText("No problem - let's move on.")
        self.done()
        QTimer.singleShot(self.MOVE_ON_MS, self._move_on)

    def _move_on(self) -> None:
        if self.panel is not None and self.panel.step() is self:
            self.panel.next()

    def _note(self, words: str) -> None:
        self._said(True).set(words)

    def _typed(self) -> None:
        text = self.box.text().strip()
        if not text:
            return
        self.box.clear()
        self._said(False).set(text)
        if self._voice is not None:
            self._voice.type(text)

    def hold(self, down: bool, now: Optional[float] = None) -> None:
        for cap in self.keys:
            cap.set_lit(down)
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
        if self.trail is not None:
            self.trail.hide()

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


class HelpStep(_AsksAboutScreen):
    """A question made from the interview, asked about their real screen."""

    TITLE = "Here's how I'd help you"
    LINES = "I see what you see. Hold the keys, ask, and watch your screen."

    def __init__(self):
        super().__init__()
        row, self.keys = self.keycaps("Hold", "and ask")
        self.body.addSpacing(36)
        self.body.addLayout(row)
        self.body.addSpacing(14)
        self.suggestion = Bubble(self.suggest())
        self.body.addLayout(_centred(self.suggestion))
        self.body.addSpacing(8)
        self.build_tail()

    @staticmethod
    def suggest(uses: Sequence[str] = ()) -> str:
        """A question that fits what they do, from the old first launch's list."""
        from ui.setup_wizard import TRY
        for use in uses:
            if use in TRY:
                return TRY[use]
        return "What am I looking at here?"

    def enter(self) -> None:
        super().enter()
        answers = getattr(self._voice, "answers", None)
        self.suggestion.text.setText(f"“{self.suggest(getattr(answers, 'uses', ()) or ())}”")


class _CircleDemo(QWidget):
    """Her cursor drawing round a figure on a little page, over and over:
    what circling looks like, before they try it."""

    LOOP_MS = 2600

    def __init__(self):
        super().__init__()
        self.setFixedSize(360, 120)
        self.t = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._started = 0.0

    def start(self) -> None:
        self._started = time.monotonic()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        self.t = ((time.monotonic() - self._started) * 1000 % self.LOOP_MS) / self.LOOP_MS
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        import math
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QColor, QFont, QPainter, QPen
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        card = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(T.BORDER), 1))
        p.setBrush(QColor(T.SURFACE))
        p.drawRoundedRect(card, 12, 12)
        p.setPen(QColor(T.TEXT_DIM))
        f = QFont(self.font())
        f.setPixelSize(12)
        p.setFont(f)
        p.drawText(QRectF(20, 18, 320, 18), "Quarterly report")
        for i, w in enumerate((300, 250, 280)):
            p.fillRect(QRectF(20, 46 + i * 16, w, 6), QColor(T.BORDER))
        f.setPixelSize(15)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(T.TEXT))
        figure = QRectF(220, 14, 110, 26)
        p.drawText(figure, Qt.AlignmentFlag.AlignCenter, "$48,200")
        draw = min(self.t / 0.7, 1.0)                     # the circle, then a pause
        ring = figure.adjusted(-10, -6, 10, 6)
        pen = QPen(QColor(T.ACCENT), 3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(ring, 90 * 16, int(-draw * 360 * 16))
        a = math.radians(90 - draw * 360)
        head = QPointF(ring.center().x() + ring.width() / 2 * math.cos(a),
                       ring.center().y() - ring.height() / 2 * math.sin(a))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(T.ACCENT))
        p.drawEllipse(head, 5, 5)


class CircleStep(_AsksAboutScreen):
    """Draw round anything while you ask: the line is on the screen as you
    draw and on the picture she is handed."""

    TITLE = "Circle anything, ask about it"
    LINES = "Hold the keys, draw round something with your mouse, and ask about it."
    TRAIL = True
    NOTE = ("[The user circled something on their screen while asking - the blue line on this "
            "picture. Answer about what is inside it.]")
    TOO_SHORT = "Draw round something with the mouse while you hold them."

    def __init__(self):
        super().__init__()
        self.demo = _CircleDemo()
        self.body.addSpacing(16)
        self.body.addLayout(_centred(self.demo))
        self.body.addSpacing(16)
        row, self.keys = self.keycaps("Hold", "draw round it, and ask")
        self.body.addLayout(row)
        self.body.addSpacing(8)
        self.build_tail()

    def enter(self) -> None:
        super().enter()
        self.demo.start()

    def leave(self) -> None:
        super().leave()
        self.demo.stop()


# ── I can drive your computer too ─────────────────────────────────────────────

class ListWindow(QFrame):
    """A small "Your list" window inside the panel, for her to fill in."""

    def __init__(self):
        super().__init__()
        self.setObjectName("obList")
        self.setFixedWidth(340)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)
        lay.addWidget(label("Your list", "obListTitle"))
        self.rows = QVBoxLayout()
        self.rows.setSpacing(4)
        lay.addLayout(self.rows)
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
        self.rows.addWidget(tick)
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
    GLIDE_MS = 420
    KEY_MS = 32
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
            QTimer.singleShot(self.AFTER_WORDS_MS, lambda: self.drive(self._last or self.PLAIN))
        elif kind == "state" and value == "offline" and self._waiting and not self.driving:
            self._waiting = False
            self.drive(self._last or self.PLAIN)

    AFTER_WORDS_MS = 600

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
        self._at = time.monotonic()
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
        self.body.addSpacing(40)
        self.body.addLayout(_centred(self.key))
        self.body.addSpacing(10)
        self.hint = label("tap it twice", "obNote")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.body.addWidget(self.hint)
        self.box = QLineEdit()
        self.box.setObjectName("obType")
        self.box.setPlaceholderText("Type to Tora, then press Enter")
        self.box.returnPressed.connect(self._send)
        self.box.hide()
        self.body.addSpacing(12)
        self.body.addWidget(self.box)
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
        QTimer.singleShot(260, lambda: self.key.set_lit(False))
        self.hint.setText("That's it - now type")
        self.box.show()
        self.box.setFocus()

    def _send(self) -> None:
        text = self.box.text().strip()
        if not text:
            return
        self.box.clear()
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
            self.done()
        elif kind == "state" and value == "offline":
            self.reply.setText("I can't reach my voice right now - but that's where my answer goes.")
            self.done()


def steps() -> list:
    """The tour's steps, in order (phases B, C and D)."""
    return [HelloStep, MicStep, TalkStep, InterviewStep, HelpStep, CircleStep, DriveStep, TypeStep]


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
                    border-radius: 14px; padding: 8px 12px; font-size: 13px; }}
    QLabel#obYours {{ background: {T.ACCENT_SOFT}; color: {T.TEXT}; border-radius: 14px;
                     padding: 8px 12px; font-size: 13px; }}
    QLabel#obStatus {{ background: {T.SURFACE}; color: {T.TEXT}; border: 1px solid {T.BORDER};
                      border-radius: {tokens.pill(32)}px; padding: 7px 18px; font-size: 13px;
                      font-weight: 600; }}
    QLineEdit#obType {{ background: {T.SURFACE}; color: {T.TEXT}; border: 1px solid {T.BORDER};
                       border-radius: 10px; padding: 8px 12px; font-size: 13px; }}
    QLineEdit#obType:focus {{ border-color: {T.ACCENT}; }}
    QFrame#obList {{ background: {T.SURFACE}; border: 1px solid {T.BORDER}; border-radius: 12px; }}
    QLabel#obListTitle {{ color: {T.TEXT}; font-size: 13px; font-weight: 700; }}
    QCheckBox#obListItem {{ color: {T.TEXT}; font-size: 13px; }}
    """
