"""Settings as a dialog with a menu (ui/settings_dialog.py, DESIGN.md §3D),
and the Data & privacy tab's controls over what Tora keeps."""
from __future__ import annotations

import json
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QKeyEvent, QMouseEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp):
    from ui.app_window import ToraApp
    w = ToraApp()
    w.resize(1280, 820)
    yield w
    w.deleteLater()


def test_the_menu_is_the_agreed_tabs(window):
    from ui.settings_tabs import TABS
    assert [(sec, [k for k, *_r in rows]) for sec, rows in TABS] == [
        ("Settings", ["general", "system", "agents", "advanced"]),
        ("Account", ["account", "usage", "plans", "privacy"])]
    window.open_settings()
    words = [r.text() for r in window._settings._rows.values()]
    assert words == ["General", "System", "Agents", "Advanced",
                     "Account", "Usage", "Plans & billing", "Data & privacy"]


def test_every_tab_builds(window):
    from ui.settings_tabs import TABS
    window.open_settings()
    for _sec, rows in TABS:
        for key, *_r in rows:
            window._settings.select(key)
            assert window._settings.current() == key
            assert window._settings._rows[key].isChecked()


def test_escape_and_a_click_outside_close_it(window, qapp):
    window.show()
    window.open_settings("system")
    dlg = window._settings
    QApplication.sendEvent(dlg, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                          Qt.KeyboardModifier.NoModifier))
    assert dlg.isHidden()
    window.open_settings()
    qapp.processEvents()
    outside = QPointF(5, 5)
    QApplication.sendEvent(dlg, QMouseEvent(QEvent.Type.MouseButtonPress, outside, outside,
                                            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                                            Qt.KeyboardModifier.NoModifier))
    assert dlg.isHidden(), "the dimmed app behind it closes it"
    window.hide()


# ── Data & privacy: what Tora keeps, and the controls over it ────────────────

def test_the_journal_exports_and_deletes(tmp_path):
    from tutor_features import journal
    journal.log_qa("What's wrong with this equation?", "Subtract the 6.", app_key="Excel")
    journal.log_walkthrough("Export the video", ["Click File.", "Click Export."])
    out = tmp_path / "history.json"
    assert journal.export_all(out) == 2
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["kind"] == "journal" and {e["question"] for e in data["entries"]} >= {
        "What's wrong with this equation?"}
    assert journal.clear_all() == 2
    assert journal.recent(10) == [] and journal.walkthrough_seconds() == 0.0


def test_the_privacy_tab_says_where_the_journal_lives_and_links_the_policy(qapp):
    from ui.settings_tabs import PrivacyTab

    class Bridge:
        def __init__(self):
            self.done = []

        def switch(self, _name): return True
        def set_switch(self, *a): self.done.append(("switch",) + a)
        def grants(self): return []
        def open_journal_folder(self): self.done.append("folder")
        def open_web(self, path): self.done.append(("web", path))

    bridge = Bridge()
    page = PrivacyTab(bridge)
    texts = [l.text() for l in page.findChildren(QLabel)]
    assert "Stored on this PC only" in texts and "Delete your history" in texts
    from PyQt6.QtWidgets import QPushButton
    buttons = {b.text(): b for b in page.findChildren(QPushButton)}
    buttons["Privacy policy"].click()
    buttons["Terms"].click()
    buttons["Open folder"].click()
    assert bridge.done == [("web", "/privacy"), ("web", "/terms"), "folder"]


def test_plans_match_the_website_and_mark_yours(qapp):
    from PyQt6.QtWidgets import QPushButton
    from ui.settings_tabs import PLANS, PlansTab

    class Bridge:
        def __init__(self):
            self.done = []

        def account_state(self):
            return {"signed_in": True, "mode": "account", "summary": {"plan": "pro"}}

        def open_web(self, path):
            self.done.append(path)

    assert [(p["name"], p["price"]) for p in PLANS] == [("Free", "$0"), ("Pro", "$20"), ("Max", "$100")]
    bridge = Bridge()
    page = PlansTab(bridge)
    buttons = {b.text(): b for b in page.findChildren(QPushButton)}
    assert not buttons["Your plan"].isEnabled(), "yours is marked, not offered"
    assert "Choose Pro" not in buttons and {"Choose Free", "Choose Max"} <= set(buttons)
    buttons["Choose Max"].click()
    buttons["Open billing"].click()
    assert bridge.done == ["/pricing", "/pricing"]


def test_the_agents_panels_lose_their_dark_sheet(qapp):
    """Drawn for the old dark windows; in Settings they take the app's look."""
    from PyQt6.QtWidgets import QWidget
    from ui.app_pages import _tidy
    w = QWidget()
    w.setStyleSheet("QLineEdit { background: rgb(40, 40, 50); }")
    w.setObjectName("panel")
    _tidy(w)
    assert w.styleSheet() == "" and w.objectName() == "embedded"



# ── General: the shortcut and the microphone list ─────────────────────────────

@pytest.mark.parametrize("combo, ok", [
    ("ctrl+win", True), ("ctrl+alt+space", True), ("alt+q", True),
    ("ctrl", False), ("shift+a", False), ("ctrl+c", False), ("ctrl+a+b", False)])
def test_a_shortcut_must_be_one_you_cannot_type_by_accident(combo, ok):
    from ui.app_bridge import check_shortcut
    assert (check_shortcut(combo) == "") is ok, combo


def test_a_new_shortcut_goes_live_through_the_running_app(monkeypatch):
    from ui import app_bridge
    seen = []
    monkeypatch.setitem(app_bridge.HOTKEY_HOOKS, "rebind", lambda combo: seen.append(combo) or "")
    assert app_bridge.AppBridge().set_hotkey("ctrl+alt+space") == ""
    assert seen == ["ctrl+alt+space"]
    assert app_bridge.AppBridge().set_hotkey("shift+a") != "" and seen == ["ctrl+alt+space"], \
        "a bad one is refused before it reaches the hotkey"


def test_the_microphone_list_is_each_mic_once(monkeypatch):
    """Windows lists one headset once per audio system, beside its routing
    entries; the list keeps the default's audio system, each name once."""
    import sounddevice as sd
    from ui.app_bridge import AppBridge
    devices = [{"name": "Microsoft Sound Mapper - Input", "hostapi": 0, "max_input_channels": 2},
               {"name": "Microphone (Realtek(R) Audio)", "hostapi": 0, "max_input_channels": 2},
               {"name": "Headset (Jabra)", "hostapi": 0, "max_input_channels": 1},
               {"name": "Speakers", "hostapi": 0, "max_input_channels": 0},
               {"name": "Microphone (Realtek(R) Audio)", "hostapi": 1, "max_input_channels": 2},
               {"name": "Input ()", "hostapi": 3, "max_input_channels": 2}]
    monkeypatch.setattr(sd, "query_devices", lambda: devices)
    monkeypatch.setattr(sd.default, "device", (1, 3))
    assert AppBridge().mics() == [(1, "Microphone (Realtek(R) Audio)", True), (2, "Headset (Jabra)", False)]
