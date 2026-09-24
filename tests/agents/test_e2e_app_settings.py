"""APP-11: the Settings and Tutor pages are live.

Both were drawings (M29): switches that did nothing, "Realme Buds" as the
microphone, a hard-coded hotkey, a "What she may see" choice that no code
behind it had ever read. Every control now drives the same handler the
tray does; controls with nothing behind them are gone or replaced by the
real feature (the instructions box; starting with Windows).
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication, QLabel, QPlainTextEdit, QPushButton  # noqa: E402

from ui.app_bridge import AppBridge                  # noqa: E402
from ui.app_widgets import Select, Switch, ToggleRow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class FakeBridge(AppBridge):
    """Real data, recorded actions."""

    def __init__(self, **state):
        super().__init__()
        self.state_ = {"switches": {}, "grants": [], "mics": [], "mic": None,
                       "panel": False, "autostart": False, "instructions": "",
                       "workforce": None, **state}
        self.done: list[tuple] = []

    def switch(self, name):
        return self.state_["switches"].get(name, False)

    def set_switch(self, name, on):
        self.done.append(("switch", name, on))

    def instructions(self):
        return self.state_["instructions"]

    def set_instructions(self, text):
        self.done.append(("instructions", text))

    def grants(self):
        return self.state_["grants"]

    def forget_grant(self, key):
        self.done.append(("forget", key))

    def mics(self):
        return self.state_["mics"]

    def mic_index(self):
        return self.state_["mic"]

    def set_mic(self, index):
        self.done.append(("mic", index))

    def panel_visible(self):
        return self.state_["panel"]

    def set_panel_visible(self, on):
        self.done.append(("panel", on))

    def autostart(self):
        return self.state_["autostart"]

    def set_autostart(self, on):
        self.done.append(("autostart", on))
        return True

    def workforce(self):
        return self.state_["workforce"]

    def hotkey(self):
        return "Ctrl + Win"


def _texts(w):
    return [lbl.text() for lbl in w.findChildren(QLabel) if lbl.isVisibleTo(w)]


def _toggle_row(page, heading) -> ToggleRow:
    for row in page.findChildren(ToggleRow):
        if heading in [lbl.text() for lbl in row.findChildren(QLabel)]:
            return row
    raise AssertionError(f"no switch called {heading!r}")


# ── Tutor ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("heading, name", [
    ("Slow mode", "slow_mode"), ("Advance by itself", "auto_advance"),
    ("Quiz mode", "quiz_mode"), ("Code mode", "code_mode"),
    ("Privacy guard", "privacy_guard"), ("Read text on screen (OCR)", "ocr"),
    ("Web search", "web_search"),
])
def test_every_tutor_switch_is_real(qapp, heading, name):
    from ui.app_pages import TutorPage

    bridge = FakeBridge(switches={name: True})
    page = TutorPage(bridge)             # kept: Qt deletes an orphaned page's children
    row = _toggle_row(page, heading)
    assert row.switch.isChecked() is True, "shows what is true"
    row.switch.setChecked(False)
    assert bridge.done == [("switch", name, False)], "and changes it for real"


def test_the_tutor_page_offers_nothing_that_does_nothing(qapp):
    from ui.app_pages import TutorPage

    texts = _texts(TutorPage(FakeBridge()))
    assert "Answer in my language" not in texts, "Tora is English-only"
    assert "What she may see" not in texts, "no code ever read that choice"


def test_the_instructions_box_saves_through_the_tray(qapp):
    from ui.app_pages import TutorPage

    bridge = FakeBridge(instructions="Only help with Excel")
    page = TutorPage(bridge)
    (box,) = page.findChildren(QPlainTextEdit)
    assert box.toPlainText() == "Only help with Excel"
    box.setPlainText("Keep answers short")
    save = next(b for b in page.findChildren(QPushButton) if b.text() == "Save instructions")
    save.click()
    assert bridge.done == [("instructions", "Keep answers short")]
    assert "Saved - used from the next answer." in _texts(page)


# ── Settings ─────────────────────────────────────────────────────────────────

def test_allowed_desktop_actions_are_listed_and_can_be_forgotten(qapp):
    from ui.app_pages import SettingsPage

    bridge = FakeBridge(grants=["spotify.next", "windows.volume"])
    page = SettingsPage(bridge)
    assert {"spotify.next", "windows.volume"} <= set(_texts(page))
    forget = [b for b in page.findChildren(QPushButton) if b.text() == "Forget"]
    forget[0].click()
    assert bridge.done == [("forget", "spotify.next")]
    assert "spotify.next" not in _texts(page), "the row goes with it"


def test_with_nothing_allowed_it_says_so(qapp):
    from ui.app_pages import SettingsPage
    assert "Nothing is allowed without asking" in _texts(SettingsPage(FakeBridge()))


def test_the_real_microphones_are_offered_and_one_can_be_picked(qapp):
    from ui.app_pages import SettingsPage

    bridge = FakeBridge(mics=[(1, "Microphone Array", True), (4, "Headset", False)], mic=4)
    page = SettingsPage(bridge)
    select = next(s for s in page.findChildren(Select) if s.count() == 2)
    assert select.currentText() == "Headset", "the one in use"
    assert "Realme" not in " ".join(select.itemText(i) for i in range(select.count()))
    select.setCurrentIndex(0)
    assert bridge.done == [("mic", 1)]


@pytest.mark.parametrize("heading, action", [
    ("Always listening", ("switch", "wake_word", True)),
    ("Show the floating panel", ("panel", True)),
    ("Start Tora when Windows starts", ("autostart", True)),
])
def test_the_settings_switches_are_real(qapp, heading, action):
    from ui.app_pages import SettingsPage

    bridge = FakeBridge()
    page = SettingsPage(bridge)          # kept: Qt deletes an orphaned page's children
    _toggle_row(page, heading).switch.setChecked(True)
    assert bridge.done == [action]


def test_the_page_says_so_when_the_agents_did_not_start(qapp):
    from ui.app_pages import SettingsPage
    assert "The agents did not start" in _texts(SettingsPage(FakeBridge()))


def test_the_real_agent_settings_are_embedded(qapp, tmp_path):
    from agents.bootstrap import build_agent_manager
    from agents.ui.settings import AgentSettingsDialog
    from ui.app_pages import SettingsPage

    wf = build_agent_manager(workspace_root=str(tmp_path), in_memory=True,
                             offline=True).start()
    try:
        page = SettingsPage(FakeBridge(workforce=wf))
        (embedded,) = page.findChildren(AgentSettingsDialog)
        buttons = {b.text() for b in embedded.findChildren(QPushButton)
                   if b.isVisibleTo(embedded)}
        assert "Save" in buttons and "Close" not in buttons, \
            "a Close inside a page would leave an empty card"
    finally:
        wf.stop()


# ── the pieces behind them ───────────────────────────────────────────────────

def test_starting_with_windows_is_written_to_the_run_key(monkeypatch):
    from ui import autostart

    registry = {}
    monkeypatch.setattr(autostart, "_read_value", lambda: registry.get("Tora", ""))
    monkeypatch.setattr(autostart, "_write_value", lambda v: registry.__setitem__("Tora", v))
    monkeypatch.setattr(autostart, "_delete_value", lambda: registry.pop("Tora", None))
    assert autostart.enabled() is False
    assert autostart.set_enabled(True) is True and autostart.enabled() is True
    assert "main.py" in registry["Tora"] or registry["Tora"].endswith('.exe"')
    assert autostart.set_enabled(False) is True and registry == {}


def test_another_toras_entry_is_not_mistaken_for_this_one(monkeypatch):
    from ui import autostart
    monkeypatch.setattr(autostart, "_read_value", lambda: '"C:\\Old\\Tora.exe"')
    assert autostart.enabled() is False


def test_picking_a_microphone_in_the_tray_ticks_it(qapp):
    from ui.tray import TrayManager

    tray = TrayManager()
    try:
        picked = []
        tray.on_set_mic_device.connect(picked.append)
        tray.pick_mic(4)
        assert tray._active_mic_index == 4 and picked == [4]
    finally:
        tray.hide_icon()


def test_the_bridge_sends_settings_down_the_trays_path(qapp):
    sent = []
    tray = SimpleNamespace(
        on_set_custom_instructions=SimpleNamespace(emit=lambda t: sent.append(("instr", t))),
        pick_mic=lambda i: sent.append(("mic", i)),
        on_show_panel=SimpleNamespace(emit=lambda: sent.append(("show",))),
        on_hide_panel=SimpleNamespace(emit=lambda: sent.append(("hide",))),
    )
    bridge = AppBridge(None, tray)
    bridge.set_instructions("  Only Excel  ")
    bridge.set_mic(3)
    bridge.set_panel_visible(True)
    bridge.set_panel_visible(False)
    assert sent == [("instr", "Only Excel"), ("mic", 3), ("show",), ("hide",)]


def test_forgetting_a_grant_revokes_it_in_the_workforce(qapp):
    revoked = []
    mgr = SimpleNamespace(agents=SimpleNamespace(revoke_desktop_action=revoked.append,
                                                 desktop_grants=lambda: ["a.b"]))
    bridge = AppBridge.__new__(AppBridge)
    AppBridge.__init__(bridge)
    bridge.manager = mgr
    assert bridge.grants() == ["a.b"]
    bridge.forget_grant("a.b")
    assert revoked == ["a.b"]
