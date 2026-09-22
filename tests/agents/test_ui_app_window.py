"""The single-window app shell (ui/app_window).

A design draft: nine pages that hold what is currently nine separate
floating windows and three levels of tray menu. Nothing in it is wired to
the companion yet, so there is no behaviour to test — what these guard is
that every page still builds, that the navigation reaches all of them, and
the two rendering traps that cost an afternoon:

  * a QWidget SUBCLASS does not paint its stylesheet background unless
    WA_StyledBackground is set, which left the page header sitting on the
    translucent window with its title half-transparent;
  * QPushButton eats "&" as a mnemonic, so "Voice & models" rendered as
    "Voice  models".
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import Qt            # noqa: E402
from PyQt6.QtWidgets import QApplication, QPushButton  # noqa: E402

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
    """Nine pages, and the rail can get to all of them."""
    for key, *_rest in PAGES:
        window.show_page(key)
        assert window._stack.currentWidget() is window._pages[key], key
    assert len(window._pages) == len(PAGES) == 9


def test_pages_are_built_only_when_asked_for(qapp):
    """Nine pages of cards is a visible pause on a cold start, and most
    sessions open one of them."""
    w = ToraApp()
    try:
        assert len(w._pages) == 1, "only the landing page at startup"
        w.show_page("accounting")
        assert set(w._pages) == {"home", "accounting"}
    finally:
        w.deleteLater()


def test_the_selected_page_is_the_lit_one_in_the_rail(window):
    window.show_page("journal")
    assert window._buttons["journal"].isChecked()
    assert not window._buttons["home"].isChecked(), "exactly one at a time"


def test_an_ampersand_in_a_nav_label_is_not_a_mnemonic(window):
    """QPushButton reads "&m" as a shortcut and swallows the ampersand."""
    item = window._buttons["voice"]
    assert "&&" in item.text(), "the label has to be escaped"
    assert "Voice & models" in item.text().replace("&&", "&")


def test_the_window_paints_its_own_background(window):
    """Without WA_StyledBackground a QWidget subclass ignores the
    stylesheet's background, and a translucent window shows through."""
    assert window.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    for page in window._pages.values():
        assert page.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)


def test_every_page_has_an_icon_that_actually_draws(qapp):
    """The Unicode glyphs this replaced fell back to three different fonts,
    one of them a colour emoji."""
    for key, *_rest in PAGES:
        px = nav_icon(key, "#FFFFFF")
        assert not px.isNull() and px.width() > 0, key


def test_the_state_pill_follows_the_companion(window):
    window.set_state("LISTENING")
    assert window._state_label.text() == "Listening"
    window.set_state("")
    assert window._state_label.text() == "Idle", "an unknown state reads as idle"


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
