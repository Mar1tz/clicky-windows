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
from typing import Optional

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QHBoxLayout, QLineEdit, QMenu, QPushButton, QScrollArea, QVBoxLayout,
                             QWidget)

from ui import app_theme as T
from ui.app_widgets import label
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
        self.scroll.setMinimumHeight(220)
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
        self.body.addWidget(self.box)
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


def phase_b() -> list:
    """The steps built so far, in order."""
    return [HelloStep, MicStep, TalkStep, InterviewStep]


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
    """
