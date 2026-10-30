"""The shape's Settings, neater (29 Oct 2026, heyclicky2.0.mp4): "their
sidebar's a lot less cluttered, ours is very, very full ... how they
customise their cursor and their shortcuts and a lot of their toggles all
look a lot more neater than ours".

Their layout in our colours: a lighter sidebar of thirteen rows with four
pages one step in, a grey line under every title, caps labels over compact
blocks, tiles for the cursor and the voice, keys at the end of a row, the
microphones as a list with a test.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PyQt6")


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def shape(qapp, monkeypatch):
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QGuiApplication
    from ui import shape as S
    from ui.app_bridge import AppBridge
    monkeypatch.setattr(S, "animations_on", lambda: False)
    s = S.Shape(AppBridge())
    g = QGuiApplication.primaryScreen().availableGeometry()
    s.open_from("bottom", QRectF(g.center().x() - 23, g.bottom() - 22, 46, 10))
    yield s
    s.hide()
    s.deleteLater()


def test_the_sidebar_is_thirteen_light_rows(shape):
    from ui import shape as S
    rows = [k for _h, group in S.SETTINGS for k, _n, _i in group]
    assert len(rows) == 13
    for inner in ("plans", "privacy", "advanced", "devlink"):
        assert inner not in rows and inner in S.INNER, "one step in, not a row"
    assert all(r.height() == S._NavRow.H for r in shape._rows.values())


@pytest.mark.parametrize("key,lit", [("plans", "account"), ("privacy", "system"),
                                     ("advanced", "system"), ("devlink", "integrations")])
def test_a_page_one_step_in_lights_the_row_it_is_under(shape, qapp, key, lit):
    from ui import shape as S
    from ui import tokens
    tokens.load_fonts()
    shape.show_settings(key)
    qapp.processEvents()
    page = shape.settings_stack.currentWidget()
    assert shape.current_setting() == key and page.HEADING == S.INNER[key][0]
    assert shape._rows[lit].isChecked()
    assert page.body.widget().minimumSizeHint().width() <= S.SETTINGS_W - S.NAV_W


def test_general_and_integrations_lead_one_step_in(shape):
    from ui.settings_parts import SettingRow
    shape.show_settings("system")
    privacy = next(r for r in shape.settings_stack.currentWidget().findChildren(SettingRow)
                   if r.title.text() == "Privacy")
    privacy._on_click()
    assert shape.current_setting() == "privacy"
    shape.show_settings("integrations")
    editors = next(r for r in shape.settings_stack.currentWidget().findChildren(SettingRow)
                   if r.title.text() == "VS Code and its forks")
    editors._on_click()
    assert shape.current_setting() == "devlink"


def test_every_page_has_its_line_under_the_title(shape):
    from PyQt6.QtWidgets import QLabel
    from ui import shape as S
    for _h, group in S.SETTINGS:
        for key, _n, _i in group:
            if key == "invite":
                continue
            shape.select(key)
            page = shape.settings_stack.currentWidget()
            ledes = [l.text() for l in page.findChildren(QLabel) if l.objectName() == "pageLede"]
            assert ledes == [S.LEDES[key]], key


def test_a_cursor_tile_is_her_colour(shape, monkeypatch):
    from ui import tokens
    from ui.shape_widgets import CursorTile
    picked = []
    monkeypatch.setattr(shape.bridge, "set_cursor_colour", picked.append)
    shape.show_settings("cursor")
    tiles = shape.settings_stack.currentWidget().findChildren(CursorTile)
    assert [t.key for t in tiles] == [k for k, _n, _h in tokens.CURSOR_COLOURS]
    tiles[2].click()
    assert picked == [tiles[2].key]
    assert [t.isChecked() for t in tiles].count(True) == 1 and tiles[2].isChecked()


def test_a_voice_tile_plays_its_hello_and_is_hers(shape, monkeypatch):
    from ui import onboarding_parts
    from ui.shape_widgets import VOICE_FEEL, VoiceTile
    played, chosen = [], []
    monkeypatch.setattr(onboarding_parts, "play_voice", played.append)
    monkeypatch.setattr(shape.bridge, "set_voice", chosen.append)
    shape.show_settings("voice")
    tiles = shape.settings_stack.currentWidget().findChildren(VoiceTile)
    assert len(tiles) >= 12 and all(t.name in VOICE_FEEL for t in tiles[:12])
    tiles[3].click()
    assert played == [tiles[3].name] and chosen == [tiles[3].name], "the bundled hello: no talk message"
    assert tiles[3].isChecked()


def test_the_microphones_are_a_list_to_pick_from(shape, monkeypatch):
    from ui.settings_parts import SettingRow
    from ui.shape_widgets import Radio
    mics = [(0, "Microphone (Realtek)", True), (3, "Headset", False)]
    picked = []
    monkeypatch.setattr(shape.bridge, "mics", lambda: mics)
    monkeypatch.setattr(shape.bridge, "mic_index", lambda: 0)
    monkeypatch.setattr(shape.bridge, "set_mic", picked.append)
    shape.show_settings("microphone")
    page = shape.settings_stack.currentWidget()
    marks = page.findChildren(Radio)
    assert [m.on for m in marks] == [True, False]
    headset = next(r for r in page.findChildren(SettingRow) if r.title.text() == "Headset")
    headset._on_click()
    assert picked == [3]


def test_reset_puts_push_to_talk_back(shape, monkeypatch):
    from PyQt6.QtWidgets import QPushButton
    set_to = []
    monkeypatch.setattr(shape.bridge, "set_hotkey", lambda combo: set_to.append(combo) or "")
    shape.show_settings("shortcuts")
    reset = next(b for b in shape.settings_stack.currentWidget().findChildren(QPushButton) if b.text() == "Reset")
    reset.click()
    assert set_to == ["ctrl+win"]


def test_a_choice_row_has_no_chevron_and_a_link_row_has_one():
    from PyQt6.QtWidgets import QLabel
    from ui.settings_parts import Group
    from ui.shape_widgets import Radio
    g = Group("x")
    link = g.link("Privacy", "", lambda: None)
    choice = g.choice("Headset", "", lambda: None, Radio(False))
    has_pixmap = lambda row: any(l.pixmap() is not None and not l.pixmap().isNull()  # noqa: E731
                                 for l in row.findChildren(QLabel))
    assert has_pixmap(link) and not has_pixmap(choice)


def test_a_voice_tiles_waveform_moves_with_its_clip(qapp):
    """HeyClicky's sound animations: the bars follow the hello as it plays."""
    from ui.shape_widgets import VoiceTile
    tile = VoiceTile("Aoede", "#FF9A3C")
    rest = tile.bar_heights()
    tile.play([0.1, 0.9, 1.0, 0.2, 0.0])
    tile._frame = 2
    loud = tile.bar_heights()
    assert loud != rest and max(loud) > max(rest), "lifted by the clip"
    assert loud[2] >= loud[0], "the middle leads, the outer ones follow"
    for _ in range(10):
        tile._tick()
    assert not tile.playing() and tile.bar_heights() == rest, "settles when the clip ends"
