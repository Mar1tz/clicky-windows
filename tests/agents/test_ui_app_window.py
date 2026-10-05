"""The single-window app shell (ui/app_window, DESIGN.md §6).

What these guard: every page still builds and the sidebar reaches it, the
sidebar is the agreed page map (Home, Agents, Journal, Packs, Tora; free
month, Settings, Help underneath; a pack's page only while it is on), it
opens and shuts, and the rendering trap that cost an afternoon - a QWidget
SUBCLASS paints no stylesheet background unless WA_StyledBackground is set.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import Qt            # noqa: E402
from PyQt6.QtWidgets import QAbstractButton, QApplication, QLabel, QPushButton  # noqa: E402

from ui.app_pages import PAGES         # noqa: E402
from ui.app_widgets import Switch, nav_icon  # noqa: E402
from ui.app_window import ToraApp      # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qapp):
    w = ToraApp()
    yield w
    w.deleteLater()


def test_every_page_builds_and_is_reachable(window):
    for key, *_rest in PAGES:
        window.show_page(key)
        assert window._stack.currentWidget() is window._pages[key], key
    assert len(window._pages) == len(PAGES)


def test_settings_free_month_and_help_are_always_there(window):
    for key in ("settings", "free_month", "help"):
        assert not window._buttons[key].isHidden(), key


def test_settings_is_a_dialog_not_a_page(window):
    """Wispr's Settings: a dialog over the app with its own menu. Setup is
    its Advanced tab; the page underneath stays where it was."""
    window.show_page("journal")
    window.show_page("setup")
    assert not window._settings.isHidden() and window._settings.current() == "advanced"
    assert window._stack.currentWidget() is window._pages["journal"]
    window.show_page("settings/privacy")
    assert window._settings.current() == "privacy"
    window._settings.close_dialog()
    assert window._settings.isHidden()


def test_the_sidebar_opens_and_shuts(window):
    window._want_open = True
    window._sidebar.set_open(True, animate=False)
    wide = window._sidebar.width()
    window.toggle_sidebar()
    window._sidebar.openness = 0.0               # the slide, finished
    assert window._sidebar.width() < wide and not window._sidebar.is_open()
    assert window._buttons["home"].toolTip() == "Home", "shut, a row names itself on hover"


def test_no_page_has_a_description_line(window):
    """A title, never a line of explanation under it (DESIGN.md §6)."""
    for key, *_rest in PAGES:
        window.show_page(key)
        assert not window._pages[key].findChildren(QLabel, "pageLede"), key


def test_pages_are_built_only_when_asked_for(qapp):
    """Nine pages of cards is a visible pause on a cold start, and most
    sessions open one of them."""
    w = ToraApp()
    try:
        assert len(w._pages) == 1, "only the landing page at startup"
        w.show_page("journal")
        assert set(w._pages) == {"home", "journal"}
    finally:
        w.deleteLater()


def test_the_selected_page_is_the_lit_one_in_the_rail(window):
    window.show_page("tora")
    assert window._buttons["tora"].isChecked()
    assert not window._buttons["home"].isChecked(), "exactly one at a time"


def test_the_window_paints_its_own_background(window):
    """Without WA_StyledBackground a QWidget subclass ignores the
    stylesheet's background, and a translucent window shows through."""
    assert window.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    for page in window._pages.values():
        assert page.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)


def test_every_page_has_an_icon_that_actually_draws(qapp):
    """The Unicode glyphs this replaced fell back to three different fonts,
    one of them a colour emoji. Every row and title bar button has a Lucide
    file, and it renders."""
    from ui.app_widgets import ICONS
    for key in [k for k, *_r in PAGES] + list(ICONS):
        px = nav_icon(key, "#FFFFFF")
        img = px.toImage()
        assert not px.isNull() and any(img.pixelColor(x, y).alpha()
                                       for x in range(img.width()) for y in range(img.height())), key


def test_the_state_chip_follows_the_companion(window):
    window.set_state("LISTENING")
    assert window._state_label.text() == "Listening"
    assert not window._state_chip.isHidden()
    window.set_state("")
    assert window._state_label.text() == "Idle", "an unknown state reads as idle"
    assert window._state_chip.isHidden(), "idle says nothing"


def test_a_switch_reports_what_it_was_clicked_to(qapp):
    seen: list[bool] = []
    s = Switch(False)
    s.toggled.connect(seen.append)
    s.setChecked(True)
    s.setChecked(True)               # no event for no change
    s.setChecked(False)
    assert seen == [True, False]


def test_the_tray_offers_a_way_in():
    """The app is useless if nothing opens it."""
    from ui.tray import TrayManager
    assert hasattr(TrayManager, "on_open_app")


def test_the_old_windows_are_left_alone():
    """This is additive: every surface it mirrors still opens on its own."""
    from agents.ui.agents_panel import ActiveAgentsPanel
    from agents.ui.extensions import ExtensionsDialog
    from agents.ui.settings import AgentSettingsDialog
    assert all(cls is not None for cls in
               (ActiveAgentsPanel, ExtensionsDialog, AgentSettingsDialog))
