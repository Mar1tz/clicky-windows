"""The tray: a left-click opens HeyTora, a right-click opens the popup, and
every row in the popup does what it says (ui/tray.py, ui/tray_popup.py)."""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QSystemTrayIcon

from config import cfg


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def tray(qapp, tmp_path, monkeypatch):
    from ui.tray import TrayManager
    t = TrayManager()
    yield t
    t._popup.hide()
    t.hide_icon()


def test_a_left_click_opens_the_app(tray):
    opened = []
    tray.on_open_app.connect(lambda: opened.append(True))
    tray._on_activated(QSystemTrayIcon.ActivationReason.Trigger)
    assert opened == [True], "a single click on the tray icon opens HeyTora"


def test_a_right_click_opens_the_popup_not_a_windows_menu(tray):
    assert tray._tray.contextMenu() is None
    tray._on_activated(QSystemTrayIcon.ActivationReason.Context)
    assert tray._popup.isVisible()
    texts = tray.menu_texts()
    for row in ("Open HeyTora", "Stop talking", "Agents", "Web search",
                "Always listening", "Settings", "Check for updates", "Quit HeyTora"):
        assert row in texts, row


def test_settings_and_agents_open_their_pages(tray):
    pages = []
    tray.on_open_app_page.connect(pages.append)
    tray._build_menu()
    tray._popup.entry("Settings").action()
    tray._popup.entry("Agents").action()
    assert pages == ["settings", "agents"]


def test_a_popup_switch_is_the_same_switch_as_the_apps(tray):
    said = []
    tray.on_toggle_search.connect(said.append)
    tray._build_menu()
    row = tray._popup.entry("Web search")
    row.action(not tray.search_enabled)
    assert said == [tray.search_enabled]
    tray.apply_switch("web_search", True)
    assert tray.search_enabled is True


def test_running_agents_are_counted_and_can_be_stopped(tray):
    tray.set_agents_running(2)
    texts = tray.menu_texts()
    assert "Stop all agents" in texts
    assert tray._popup.entry("Agents").trailing == "2 running"
    tray.set_agents_running(0)
    assert "Stop all agents" not in tray.menu_texts()


def test_the_floating_panel_switch_shows_the_truth(tray):
    tray.panel_visible = lambda: True
    tray._build_menu()
    assert tray._popup.entry("Floating panel").on is True
    hidden = []
    tray.on_hide_panel.connect(lambda: hidden.append(True))
    tray._popup.entry("Floating panel").action(False)
    assert hidden == [True]


def test_the_theme_choice_is_saved_and_restyles(qapp, tmp_path, monkeypatch):
    from ui import app_theme, tokens
    monkeypatch.setenv("TORA_THEME", "system")      # restored after the test
    calls = []
    app_theme.on_change(lambda: calls.append(app_theme.PALETTE.name))
    try:
        cfg.set_theme("dark")
        app_theme.refresh(cfg.theme())
        assert cfg.theme() == "dark" and app_theme.BG == tokens.DARK.bg
        cfg.set_theme("light")
        app_theme.refresh(cfg.theme())
        assert app_theme.BG == tokens.LIGHT.bg
        assert calls[-2:] == ["dark", "light"]
        cfg.set_theme("purple")
        assert cfg.theme() == "system"
    finally:
        app_theme._listeners.clear()
        app_theme.apply(tokens.LIGHT)
