"""What a Settings tab is made of (DESIGN.md §3D), after Wispr Flow's.

Settings read as a wall of words: a card title, a subtitle, then rows with
a heading AND a sentence each, in small grey type. Wispr's are easy to scan
because each group's name sits above a soft block, and each row is a title,
at most one short line, and the control - in larger, darker type. These
are those parts, and the two small dialogs General opens:

    Group("Sound")                 the name, then a filled block
      .row("Launch at login", control=Switch)
      .row("Microphone", "Realtek(R) Audio", control=button("Change"))

    ShortcutDialog    press the new push-to-talk keys; saved and live
    MicDialog         every microphone as a card, the chosen one's level live
"""

from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QPainter
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ui import app_theme as T
from ui import kit, tokens
from ui.app_widgets import Switch, button, icon, label


# ── a group and its rows ──────────────────────────────────────────────────────

class SettingRow(QFrame):
    """A title, at most one short line under it, and the control. With a
    switch, `.switch` is it (and clicking the words flips it too)."""

    def __init__(self, title: str, desc: str = "", control: Optional[QWidget] = None,
                 *, first: bool = False):
        super().__init__()
        self.setObjectName("setRowFirst" if first else "setRow")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(16)
        text = QVBoxLayout()
        text.setSpacing(3)
        self.title = label(title, "setTitle", wrap=True)
        text.addWidget(self.title)
        self.desc = None
        if desc:
            self.desc = label(desc, "setDesc", wrap=True)
            text.addWidget(self.desc)
        lay.addLayout(text, 1)
        self.switch: Optional[Switch] = control if isinstance(control, Switch) else None
        if control is not None:
            lay.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)

    def mouseReleaseEvent(self, e) -> None:
        if (self.switch is not None and e.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(e.position().toPoint())):
            self.switch.setChecked(not self.switch.isChecked())
        super().mouseReleaseEvent(e)


class Group(QWidget):
    """A group's name above a soft block of rows split by hairlines; an
    optional control on the heading's right (Change keys...), and an
    optional note under the block."""

    def __init__(self, name: str = "", trailing: Optional[QWidget] = None):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)
        if name or trailing is not None:
            head = QHBoxLayout()
            head.setContentsMargins(2, 0, 0, 0)
            if name:
                head.addWidget(label(name, "setGroup"), 0, Qt.AlignmentFlag.AlignBottom)
            head.addStretch(1)
            if trailing is not None:
                head.addWidget(trailing)
            outer.addLayout(head)
        self.block = QFrame()
        self.block.setObjectName("setBlock")
        self._rows = QVBoxLayout(self.block)
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(0)
        outer.addWidget(self.block)
        self._outer = outer

    def row(self, title: str, desc: str = "", control: Optional[QWidget] = None) -> SettingRow:
        r = SettingRow(title, desc, control, first=self._rows.count() == 0)
        self._rows.addWidget(r)
        return r

    def toggle(self, title: str, desc: str, on: bool, changed: Callable[[bool], None]) -> SettingRow:
        sw = Switch(on)
        sw.toggled.connect(changed)
        return self.row(title, desc, sw)

    def add(self, widget: QWidget, margins: tuple = (20, 16, 20, 16)) -> QWidget:
        """Something that is not a row (an embedded editor), in the block."""
        holder = QWidget()
        holder.setObjectName("setRowFirst" if self._rows.count() == 0 else "setRow")
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(*margins)
        lay.addWidget(widget)
        self._rows.addWidget(holder)
        return widget

    def note(self, words: str) -> QLabel:
        n = label(words, "setNote", wrap=True)
        n.setContentsMargins(2, 0, 0, 0)
        self._outer.addWidget(n)
        return n


def keycaps(keys: list[str]) -> QWidget:
    """["Ctrl", "Win"] as keycaps, side by side."""
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    for k in keys:
        lay.addWidget(label(k, "keycapSmall"))
    lay.addStretch(1)
    return row


# ── a small dialog over Settings ──────────────────────────────────────────────

class SubDialog(QWidget):
    """A card over the Settings dialog (Wispr's "Shortcuts", "Microphone"):
    a title and line, an X, the body, buttons at the foot. Escape, the X or
    a click outside closes it."""

    closed = pyqtSignal()
    W = 540

    def __init__(self, over: QWidget, title: str, sub: str = ""):
        super().__init__(over)
        self.setGeometry(over.rect())
        self.card = QFrame(self)
        self.card.setObjectName("subDialog")
        kit.drop_shadow(self.card, "hero")
        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(26, 22, 26, 22)
        lay.setSpacing(14)
        head = QHBoxLayout()
        words = QVBoxLayout()
        words.setSpacing(4)
        words.addWidget(label(title, "subTitle"))
        if sub:
            words.addWidget(label(sub, "setDesc", wrap=True))
        head.addLayout(words, 1)
        x = QPushButton()
        x.setObjectName("rowAction")
        x.setFixedSize(30, 30)
        from PyQt6.QtGui import QIcon
        x.setIcon(QIcon(icon("x", T.TEXT_DIM, 16)))
        x.setToolTip("Close")
        x.clicked.connect(self.close_dialog)
        head.addWidget(x, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        lay.addLayout(self.body, 1)
        self.foot = QHBoxLayout()
        self.foot.setSpacing(8)
        lay.addLayout(self.foot)
        over.installEventFilter(self)

    def open(self) -> None:
        self._place()
        self.show()
        self.raise_()
        self.setFocus()

    def _place(self) -> None:
        self.setGeometry(self.parentWidget().rect())
        self.card.adjustSize()
        w = min(self.W, self.width() - 60)
        h = min(self.card.sizeHint().height(), self.height() - 60)
        self.card.setGeometry(QRect((self.width() - w) // 2, (self.height() - h) // 2, w, h))

    def close_dialog(self) -> None:
        self.hide()
        self.closed.emit()
        self.deleteLater()

    def eventFilter(self, obj, event) -> bool:
        from PyQt6.QtCore import QEvent
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self._place()
        return False

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 50 if not T.is_dark() else 100))
        p.end()

    def mousePressEvent(self, e) -> None:
        if not self.card.geometry().contains(e.position().toPoint()):
            self.close_dialog()
            return
        super().mousePressEvent(e)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.close_dialog()
            return
        super().keyPressEvent(e)


# ── the shortcut ──────────────────────────────────────────────────────────────

_QT_MODS = ((Qt.KeyboardModifier.ControlModifier, "ctrl"), (Qt.KeyboardModifier.AltModifier, "alt"),
            (Qt.KeyboardModifier.ShiftModifier, "shift"), (Qt.KeyboardModifier.MetaModifier, "win"))
_MOD_KEYS = {Qt.Key.Key_Control: "ctrl", Qt.Key.Key_Alt: "alt", Qt.Key.Key_Shift: "shift",
             Qt.Key.Key_Meta: "win", Qt.Key.Key_Super_L: "win", Qt.Key.Key_Super_R: "win"}
_ORDER = ("ctrl", "alt", "shift", "win")


def combo_name(mods: set, key: str = "") -> str:
    parts = [m for m in _ORDER if m in mods] + ([key] if key else [])
    return "+".join(parts)


class ShortcutDialog(SubDialog):
    """Push to talk: the keys now, and a new set - pressed, not typed. While
    it listens, the old shortcut does nothing (so Tora does not start)."""

    def __init__(self, over: QWidget, bridge, on_done: Callable[[], None]):
        super().__init__(over, "Shortcut", "Hold these keys and speak - Tora listens until you let go.")
        self.bridge, self._on_done = bridge, on_done
        self._recording = False
        self._held: set = set()
        self._peak = ("", set())          # the most held at once: (key, modifiers)
        box = QFrame()
        box.setObjectName("setBlock")
        bl = QHBoxLayout(box)
        bl.setContentsMargins(20, 16, 16, 16)
        words = QVBoxLayout()
        words.setSpacing(3)
        words.addWidget(label("Push to talk", "setTitle"))
        self._hint = label("Hold to ask something", "setDesc")
        words.addWidget(self._hint)
        bl.addLayout(words, 1)
        self._keys_host = QHBoxLayout()
        bl.addLayout(self._keys_host)
        self._change = button("Change")
        self._change.clicked.connect(self._listen)
        bl.addWidget(self._change)
        self.body.addWidget(box)
        self._said = label("", "setNote", wrap=True)
        self.body.addWidget(self._said)
        reset = button("Reset to Ctrl + Win")
        reset.clicked.connect(lambda: self._save("ctrl+win"))
        self.foot.addWidget(reset)
        self.foot.addStretch(1)
        done = button("Done", "primary")
        done.clicked.connect(self.close_dialog)
        self.foot.addWidget(done)
        self._show_keys(bridge.hotkey_keys())

    def _show_keys(self, keys: list[str]) -> None:
        while self._keys_host.count():
            w = self._keys_host.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self._keys_host.addWidget(keycaps(keys))

    def _listen(self) -> None:
        self._recording = True
        self._held, self._peak = set(), ("", set())
        self.bridge.hold_hotkey(True)
        self._hint.setText("Press the keys you want, then let go")
        self._show_keys(["…"])
        self._change.setText("Listening…")
        self._change.setEnabled(False)
        self.setFocus()
        self.grabKeyboard()

    def _stop_listening(self) -> None:
        if self._recording:
            self._recording = False
            self.releaseKeyboard()
            self.bridge.hold_hotkey(False)
            self._change.setText("Change")
            self._change.setEnabled(True)
            self._hint.setText("Hold to ask something")

    def keyPressEvent(self, e) -> None:
        if not self._recording:
            super().keyPressEvent(e)
            return
        if e.key() == Qt.Key.Key_Escape:
            self._stop_listening()
            self._show_keys(self.bridge.hotkey_keys())
            return
        if e.isAutoRepeat():
            return
        k = e.key()
        if k in _MOD_KEYS:
            self._held.add(_MOD_KEYS[k])
        else:
            name = QKeySequence(k).toString().lower()
            if name:
                self._held.add("key:" + name)
        mods = {m for m in self._held if not m.startswith("key:")}
        keys = [m[4:] for m in self._held if m.startswith("key:")]
        if len(mods) + len(keys) >= len(self._peak[1]) + (1 if self._peak[0] else 0):
            self._peak = (keys[-1] if keys else "", mods)
        self._show_keys([p.capitalize() for p in combo_name(*reversed(self._peak)).split("+") if p])

    def keyReleaseEvent(self, e) -> None:
        if not self._recording or e.isAutoRepeat():
            super().keyReleaseEvent(e)
            return
        k = e.key()
        self._held.discard(_MOD_KEYS.get(k, "key:" + QKeySequence(k).toString().lower()))
        if not self._held:
            key, mods = self._peak
            self._stop_listening()
            self._save(combo_name(mods, key))

    def _save(self, combo: str) -> None:
        why = self.bridge.set_hotkey(combo)
        if why:
            self._said.setText(why)
            self._show_keys(self.bridge.hotkey_keys())
            return
        self._said.setText("Saved - hold it to talk to Tora.")
        self._show_keys(self.bridge.hotkey_keys())
        self._on_done()

    def close_dialog(self) -> None:
        self._stop_listening()
        super().close_dialog()


# ── the microphone ────────────────────────────────────────────────────────────

class _Level(QWidget):
    """Wispr's little level: a stack of bars that light with your voice."""

    BARS = 7

    def __init__(self):
        super().__init__()
        self.level = 0.0
        self.setFixedSize(18, 46)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        lit = round(self.level * self.BARS)
        h = (self.height() - (self.BARS - 1) * 2) / self.BARS
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(self.BARS):
            on = (self.BARS - i) <= lit
            p.setBrush(QColor(T.ACCENT) if on else QColor(T.BORDER))
            p.drawRoundedRect(QRectF(0, i * (h + 2), self.width(), h), 2, 2)
        p.end()


class _MicCard(QFrame):
    clicked = pyqtSignal()

    def __init__(self, title: str, sub: str, on: bool):
        super().__init__()
        self.setObjectName("micCardOn" if on else "micCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 14, 14, 14)
        col = QVBoxLayout()
        col.setSpacing(3)
        col.addWidget(label(title, "setTitle", wrap=True))
        if sub:
            col.addWidget(label(sub, "setDesc"))
        lay.addLayout(col, 1)
        self.level: Optional[_Level] = None
        if on:
            self.level = _Level()
            lay.addWidget(self.level, 0, Qt.AlignmentFlag.AlignVCenter)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class MicDialog(SubDialog):
    """Every microphone as a card; the one Tora listens on is marked and its
    level moves as you talk - so you can see it is the right one."""

    def __init__(self, over: QWidget, bridge, on_done: Callable[[], None]):
        super().__init__(over, "Microphone", "The one Tora listens on. Talk - the bars should move.")
        self.bridge, self._on_done = bridge, on_done
        self._stream = None
        self._level = 0.0
        self._list = QVBoxLayout()
        self._list.setSpacing(8)
        self.body.addLayout(self._list)
        self.foot.addStretch(1)
        done = button("Done", "primary")
        done.clicked.connect(self.close_dialog)
        self.foot.addWidget(done)
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._tick)
        self._fill()

    def _fill(self) -> None:
        while self._list.count():
            w = self._list.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        mics = self.bridge.mics()
        now = self.bridge.mic_index()
        chosen = now if any(i == now for i, _n, _d in mics) else next(
            (i for i, _n, d in mics if d), None)
        self._card = None
        if not mics:
            self._list.addWidget(label("No microphone found - plug one in and open this again.",
                                       "setDesc", wrap=True))
        for index, name, default in mics:
            card = _MicCard(name, "System default" if default else "", index == chosen)
            card.clicked.connect(lambda i=index: self._pick(i))
            self._list.addWidget(card)
            if index == chosen:
                self._card = card
        self._listen(chosen)
        self._place()

    def _pick(self, index: int) -> None:
        self.bridge.set_mic(index)
        self._on_done()
        QTimer.singleShot(0, self._fill)

    def _listen(self, device: Optional[int]) -> None:
        self._stop()
        if device is None or self._card is None:
            return
        try:
            import numpy as np
            import sounddevice as sd

            def hear(data, _frames, _time, _status):
                self._level = float(np.sqrt(np.mean(np.square(data.astype("float32")))))

            self._stream = sd.InputStream(device=device, channels=1, samplerate=16000,
                                          dtype="float32", callback=hear)
            self._stream.start()
            self._timer.start()
        except Exception:  # noqa: BLE001 - shown as still bars; the choice still saves
            self._stream = None

    def _tick(self) -> None:
        if self._card is not None and self._card.level is not None:
            shown = self._card.level.level
            target = min(1.0, self._level * 9)
            self._card.level.level = shown + (target - shown) * 0.5
            self._card.level.update()

    def _stop(self) -> None:
        self._timer.stop()
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001
                pass

    def close_dialog(self) -> None:
        self._stop()
        super().close_dialog()
