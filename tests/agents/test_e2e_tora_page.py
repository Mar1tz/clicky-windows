"""The Tora page's own tabs (DESIGN.md §3E): Voice & look, How she works,
Teach her - each shows only its own cards."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication, QLabel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


class Bridge:
    def __init__(self):
        from PyQt6.QtCore import QObject, pyqtSignal

        class S(QObject):
            changed = pyqtSignal(str)
        self._s = S()
        self.changed = self._s.changed

    def voice(self): return "Kore"
    def switch(self, _name): return False
    def set_switch(self, *_a): pass
    def instructions(self): return ""
    def recording(self): return False
    def capturing(self): return False


@pytest.mark.parametrize("tab, shows, hides", [
    ("look", {"Her voice", "Her colour", "Her sound"}, {"Her modes", "Record a lesson"}),
    ("works", {"Her modes", "Instructions for Tora"}, {"Her voice", "Record a lesson"}),
    ("teach", {"Record a lesson", "Show her what you did"}, {"Her voice", "Her modes"}),
])
def test_each_tab_shows_its_own_cards(qapp, tab, shows, hides):
    from ui.app_pages import ToraPage
    page = ToraPage(Bridge())
    page.show_tab(tab)
    texts = {lbl.text() for lbl in page.findChildren(QLabel)}
    assert shows <= texts and not (hides & texts)


def test_it_opens_on_voice_and_look(qapp):
    from ui.app_pages import ToraPage
    page = ToraPage(Bridge())
    assert page._tab == "look" and [k for k, _w in ToraPage.TABS] == ["look", "works", "teach"]


def test_an_ampersand_in_a_tab_is_drawn(qapp):
    """A button reads "&" as a shortcut: "Voice & look" drew as "Voice look"."""
    from ui.app_parts import TabBar
    bar = TabBar([("look", "Voice & look")], "look")
    assert bar.buttons["look"].text() == "Voice && look"
