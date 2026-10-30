"""Settings, closer to HeyClicky (31 Oct 2026, the owner's walkthrough).

"Ours has a lot of fluff": Account is the plan card and three rows - no
name box, no picture, no "how Tora thinks". General: a taskbar button and
launch at login (the floating panel's switch flipped itself off and went),
and the welcome tour again. Type to Tora is not a switch. Agents is two
short groups, the limits one step in. The shape makes a soft sound as it
opens and grows into Settings, which can be turned off.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PyQt6")

from tests.agents.test_e2e_shape import _pill_at, qapp, shape  # noqa: E402,F401 - fixtures

SUMMARY = {"plan": "free", "plan_name": "Free", "plan_status": "active",
           "limits": {"talk": 25, "agent": 25}, "usage": {"talk": 3, "agent": 0},
           "period_end": "2026-11-28T00:00:00+00:00"}


def _page(shape, monkeypatch, key):
    from PyQt6.QtWidgets import QLabel
    monkeypatch.setattr(shape.bridge, "account_state", lambda: {
        "signed_in": True, "mode": "account", "email": "you@example.com", "configured": True,
        "error": "", "busy": False, "fresh": True, "own_key": False, "website": "", "summary": SUMMARY})
    monkeypatch.setattr(shape.bridge, "refresh_account", lambda force=False: None)
    shape.open_from("bottom", _pill_at("bottom"))
    shape.show_settings(key)
    page = shape.settings_stack.currentWidget()
    return page, [lbl.text() for lbl in page.findChildren(QLabel)]


def test_account_is_the_plan_and_three_rows(shape, monkeypatch):
    from PyQt6.QtWidgets import QLineEdit, QPushButton
    page, words = _page(shape, monkeypatch, "account")
    assert "You're on the Free plan." in words and "3 / 25" in words
    assert {"Log out", "Delete account", "Quit HeyTora", "you@example.com"} <= set(words)
    assert not page.findChildren(QLineEdit), "no name box"
    assert not {"Name", "Picture", "Profile", "How Tora thinks"} & set(words)
    went = []
    page.navigate.connect(went.append)
    next(b for b in page.findChildren(QPushButton) if b.text() == "Upgrade").click()
    assert went == ["settings/plans"]


def test_general_has_the_taskbar_and_the_tour_not_the_floating_panel(shape, monkeypatch):
    _page_, words = _page(shape, monkeypatch, "system")
    assert {"Show in taskbar", "Launch at login", "Replay the welcome tour"} <= set(words)
    assert "Show the floating panel" not in words


def test_type_to_tora_has_no_switch(shape, monkeypatch):
    from ui.app_widgets import Switch
    page, words = _page(shape, monkeypatch, "shortcuts")
    assert "Type to Tora" in words and not page.findChildren(Switch)


def test_agents_is_short_and_the_limits_are_one_step_in(shape, monkeypatch):
    page, words = _page(shape, monkeypatch, "agents")
    assert {"Agent folder", "Approve automatically", "Show me the plan first", "Limits & models"} <= set(words)
    assert not {"Jobs at once", "Time limit", "Token budget"} & set(words)
    went = []
    page.navigate.connect(went.append)
    from PyQt6.QtCore import QEvent, QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    from ui.settings_parts import SettingRow
    row = next(r for r in page.findChildren(SettingRow) if r.title.text() == "Limits & models")
    click = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(5, 5), QPointF(5, 5),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    row.mouseReleaseEvent(click)
    assert went == ["settings/agent-limits"]
    _limits, more = _page(shape, monkeypatch, "agent-limits")
    assert {"Jobs at once", "Time limit", "Token budget", "Run without asking, up to"} <= set(more)
    assert "Approve automatically" not in more, "not twice"


def test_interface_sounds_play_as_it_opens_and_can_be_turned_off(shape, monkeypatch):
    from config import cfg
    from ui import sounds
    played = []
    monkeypatch.setattr(sounds, "_effect", lambda key, volume=sounds.VOLUME:
                        type("Fx", (), {"stop": lambda self: None,
                                        "play": lambda self: played.append(key)})())
    monkeypatch.setenv("TORA_UI_SOUNDS", "1")
    shape.open_from("bottom", _pill_at("bottom"))
    shape.show_settings("voice")
    shape.show_settings("cursor")                      # already in Settings: no second sound
    assert played == [sounds.UI["open"], sounds.UI["settings"]]
    monkeypatch.setenv("TORA_UI_SOUNDS", "0")
    assert not cfg.switch("ui_sounds") and sounds.ui("open") is False


def test_the_taskbar_button_follows_its_switch_and_opens_the_shape(qapp, monkeypatch):
    from ui import taskbar
    monkeypatch.setenv("TORA_TASKBAR", "1")
    opened = []
    b = taskbar.install(lambda: opened.append(1))
    try:
        assert b.isVisible() and b.isMinimized()
        b.clicked.emit()
        assert opened == [1]
        taskbar.apply(False)
        assert not b.isVisible()
        taskbar.apply(True)
        assert b.isVisible()
    finally:
        taskbar.apply(False)
        taskbar._button.clear()
        b.deleteLater()


def test_main_puts_heytora_in_the_taskbar():
    from pathlib import Path
    main = (Path(__file__).resolve().parents[2] / "main.py").read_text(encoding="utf-8")
    assert "taskbar.install(_toggle_shape)" in main
