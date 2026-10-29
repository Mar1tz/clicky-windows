"""Ctrl, tapped twice: type to Tora (29 Oct 2026). "On HeyClicky in the
shortcuts it has an option for the user to double click Ctrl for the type
menu to pop up."

The detector is fed key events by hand here - the keyboard hook is the
only part not under test. What must never fire: push to talk (Ctrl + Win),
a tap then Ctrl + C, a held Ctrl, two taps far apart.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("keyboard")


def _tap():
    from hotkey import DoubleTap
    fired = []
    return DoubleTap(lambda: fired.append(1)), fired


def _press(d, name, at, held=0.08):
    d.feed(name, True, at)
    return d.feed(name, False, at + held)


def test_two_quick_taps_fire_once():
    d, fired = _tap()
    assert not _press(d, "left ctrl", 0.0)
    assert _press(d, "left ctrl", 0.25)
    assert fired == [1]
    assert not _press(d, "left ctrl", 0.50), "a third tap starts over"


def test_push_to_talk_is_never_a_tap():
    d, fired = _tap()
    for at in (0.0, 1.0):                        # Ctrl + Win, twice
        d.feed("left ctrl", True, at)
        d.feed("left windows", True, at + 0.05)
        d.feed("left windows", False, at + 0.6)
        d.feed("left ctrl", False, at + 0.62)
    _press(d, "left ctrl", 1.7)
    assert fired == []


def test_a_tap_then_ctrl_c_does_nothing():
    d, fired = _tap()
    _press(d, "left ctrl", 0.0)
    d.feed("left ctrl", True, 0.2)
    d.feed("c", True, 0.25)
    d.feed("c", False, 0.3)
    d.feed("left ctrl", False, 0.32)
    assert fired == []


def test_another_key_between_the_taps_cancels():
    d, fired = _tap()
    _press(d, "ctrl", 0.0)
    d.feed("a", True, 0.15)
    d.feed("a", False, 0.18)
    _press(d, "ctrl", 0.25)
    assert fired == []


def test_a_held_ctrl_is_not_a_tap_and_its_repeats_are_one_press():
    d, fired = _tap()
    d.feed("left ctrl", True, 0.0)
    for k in range(1, 10):                       # key repeat while held
        d.feed("left ctrl", True, k * 0.03)
    d.feed("left ctrl", False, 0.6)              # held too long to be a tap
    _press(d, "left ctrl", 0.75)
    assert fired == []


def test_taps_far_apart_do_nothing():
    d, fired = _tap()
    _press(d, "right ctrl", 0.0)
    _press(d, "right ctrl", 0.9)
    assert fired == []


def test_either_ctrl_counts():
    d, fired = _tap()
    _press(d, "left ctrl", 0.0)
    _press(d, "right ctrl", 0.3)
    assert fired == [1]


def test_it_is_a_switch_on_by_default():
    from config import cfg
    assert cfg.SWITCHES["type_tap"] == ("TORA_TYPE_TAP", True)


def test_the_shape_opens_on_chat_ready_to_type(monkeypatch):
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from ui import shape as S
    from ui.app_bridge import AppBridge
    from ui.shape_pages import ChatTab
    monkeypatch.setattr(S, "animations_on", lambda: False)
    s = S.Shape(AppBridge())
    g = QGuiApplication.primaryScreen().availableGeometry()
    s.open_to_type("bottom", QRectF(g.center().x() - 23, g.bottom() - 22, 46, 10))
    app.processEvents()
    assert s.is_open() and isinstance(s.stack.currentWidget(), ChatTab)
    assert s._tabs["chat"].isChecked()
    s.hide()
    s.deleteLater()
