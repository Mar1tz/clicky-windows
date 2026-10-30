"""HeyTora's button in the Windows taskbar (31 Oct 2026).

HeyClicky sits in the taskbar, and some people never find a pill on the
edge of the screen - the taskbar is where they look for an app. The shape
itself is a tool window (it must not take a taskbar slot of its own while
it grows and shrinks), so the button belongs to a window of its own: a
tiny one, off every screen, that lives minimised. Clicking a minimised
window's button restores it; this one says it was clicked and goes back
to being minimised, and main.py opens or closes the shape.

Settings > General > Show in taskbar turns it off (cfg switch "taskbar").
"""
from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtWidgets import QWidget

from ui.app_widgets import later


class TaskbarButton(QWidget):
    clicked = pyqtSignal()

    def __init__(self):
        # An ordinary window (a frameless one has no minimise box, and Windows
        # treats its taskbar button differently): it is never seen anyway.
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle("HeyTora")                 # the button's tooltip; the icon is the app's
        self.setFixedSize(1, 1)
        self.setWindowOpacity(0.0)
        self._on = False

    def set_shown(self, on: bool) -> None:
        """In the taskbar, or not."""
        self._on = on
        if on:
            self.move(-32000, -32000)                   # off every screen, should it ever show
            self.showMinimized()
        else:
            self.hide()

    def changeEvent(self, e) -> None:
        super().changeEvent(e)
        # getattr: the title set in __init__ arrives here before _on exists,
        # and an error inside a Qt event aborts the whole app.
        if (getattr(self, "_on", False) and e.type() == QEvent.Type.WindowStateChange
                and not self.isMinimized() and self.isVisible()):
            # Its button was clicked: say so, and back to the taskbar.
            self.clicked.emit()
            later(self, self.showMinimized)


_button: list[TaskbarButton] = []          # the one main.py made


def install(on_click) -> TaskbarButton:
    """main.py: make the button, wire it, and show it if the switch is on."""
    from config import cfg
    b = TaskbarButton()
    b.clicked.connect(on_click)
    _button[:] = [b]
    b.set_shown(cfg.switch("taskbar"))
    return b


def apply(on: bool) -> None:
    """Settings > General's switch, at once."""
    for b in _button:
        b.set_shown(on)
