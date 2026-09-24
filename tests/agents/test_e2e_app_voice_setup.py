"""APP-12: the Voice & models and Setup pages are live.

Both were drawings (M29): a voice picker with one option, model pickers
that picked nothing, three endpoints typed in by hand, keys shown as
"Connected" whatever their state, and diagnostics that said "Realme Buds"
and "3 monitors" on every PC. Now they read the running app, and every
button does what the tray's does.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from ui.app_bridge import AppBridge                            # noqa: E402
from ui.app_widgets import Select                              # noqa: E402

REAL_KEY = "FAKE-TEST-KEY-not-a-real-one-1234Plig"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class FakeBridge(AppBridge):
    def __init__(self, **state):
        super().__init__()
        self.s = {"voice": "Kore", "route": "realtime", "problem": "",
                  "keys": [{"env": "GOOGLE_API_KEY", "label": "Google (Gemini)",
                            "set": True, "tail": "Plig"},
                           {"env": "TAVILY_API_KEY", "label": "Tavily (web search)",
                            "set": False, "tail": ""}],
                  "diagnostics": [("Microphone", "Headset", "ok"),
                                  ("Live session", "Refused - the API key", "bad")],
                  "workforce": None, **state}
        self.done = []

    def voices(self):
        return ("Zephyr", "Puck", "Kore")

    def voice(self):
        return self.s["voice"]

    def set_voice(self, name):
        self.done.append(("voice", name))

    def route(self):
        return self.s["route"]

    def set_route(self, mode):
        self.done.append(("route", mode))

    def models(self):
        return {"realtime": "gemini-live-x", "vision_fast": "gemini-flash-x", "tts": "tts-x"}

    def refresh_models(self):
        self.done.append(("refresh",))

    def problem(self):
        return self.s["problem"]

    def keys(self):
        return self.s["keys"]

    def open_keys(self):
        self.done.append(("keys",))

    def diagnostics(self):
        return self.s["diagnostics"]

    def run_setup(self):
        self.done.append(("wizard",))

    def save_report(self):
        self.done.append(("report",))

    def quit(self):
        self.done.append(("quit",))

    def workforce(self):
        return self.s["workforce"]


def _texts(w):
    return [lbl.text() for lbl in w.findChildren(QLabel) if lbl.isVisibleTo(w)]


def _button(page, text):
    return next(b for b in page.findChildren(QPushButton) if b.text() == text)


# ── Voice & models ───────────────────────────────────────────────────────────

def test_the_voice_in_use_is_selected_and_can_be_changed(qapp):
    from ui.app_pages import VoicePage

    bridge = FakeBridge()
    page = VoicePage(bridge)
    voice = next(s for s in page.findChildren(Select) if s.count() == 3)
    assert voice.currentText() == "Kore"
    voice.setCurrentIndex(1)
    assert bridge.done == [("voice", "Puck")]


def test_the_route_can_be_changed(qapp):
    from ui.app_pages import VoicePage

    bridge = FakeBridge(route="cascade")
    page = VoicePage(bridge)
    route = next(s for s in page.findChildren(Select) if s.count() == 2)
    assert route.currentText().startswith("Classic")
    route.setCurrentIndex(0)
    assert bridge.done == [("route", "realtime")]


def test_the_real_models_are_shown_and_the_list_refreshes(qapp):
    from ui.app_pages import VoicePage

    bridge = FakeBridge()
    page = VoicePage(bridge)
    texts = _texts(page)
    assert {"gemini-live-x", "gemini-flash-x", "tts-x"} <= set(texts)
    assert "claude-sonnet-4.6" not in texts, "no model typed in by hand"
    _button(page, "Refresh model list").click()
    assert bridge.done == [("refresh",)]


def test_the_real_endpoint_editor_is_embedded(qapp, tmp_path):
    from agents.bootstrap import build_agent_manager
    from agents.ui.accounts import ConnectedAccountsDialog
    from ui.app_pages import VoicePage

    wf = build_agent_manager(workspace_root=str(tmp_path), in_memory=True,
                             offline=True).start()
    try:
        page = VoicePage(FakeBridge(workforce=wf))
        (embedded,) = page.findChildren(ConnectedAccountsDialog)
        visible = {b.text() for b in embedded.findChildren(QPushButton)
                   if b.isVisibleTo(embedded)}
        assert "Add endpoint" in visible and "Close" not in visible
    finally:
        wf.stop()


def test_without_agents_the_endpoints_say_why(qapp):
    from ui.app_pages import VoicePage
    assert "The agents did not start" in _texts(VoicePage(FakeBridge()))


# ── Setup ────────────────────────────────────────────────────────────────────

def test_keys_show_their_real_state_never_the_key(qapp):
    from ui.app_pages import SetupPage

    texts = _texts(SetupPage(FakeBridge()))
    assert "Set · ends in Plig" in texts
    assert "Not set - web search uses the free engine" in texts


@pytest.mark.parametrize("problem, words", [
    ("key", "but Google refused it"), ("billing", "the credits have run out")])
def test_a_refused_key_is_not_called_connected(qapp, problem, words):
    from ui.app_pages import SetupPage

    texts = " ".join(_texts(SetupPage(FakeBridge(problem=problem))))
    assert words in texts


def test_the_diagnostics_are_the_real_ones(qapp):
    from ui.app_pages import SetupPage

    texts = _texts(SetupPage(FakeBridge()))
    assert "Refused - the API key" in texts and "Headset" in texts
    assert "Realme Buds Air 5 Pro · 16 kHz" not in texts


@pytest.mark.parametrize("button, action", [
    ("Change keys…", ("keys",)), ("Run the setup wizard again", ("wizard",)),
    ("Save diagnostics report", ("report",)), ("Quit Tora", ("quit",))])
def test_every_setup_button_does_what_the_trays_does(qapp, button, action):
    from ui.app_pages import SetupPage

    bridge = FakeBridge()
    page = SetupPage(bridge)
    _button(page, button).click()
    assert bridge.done == [action]


# ── the bridge and the tray behind them ──────────────────────────────────────

def test_the_bridge_never_hands_out_a_key(qapp, monkeypatch):
    from config import cfg
    monkeypatch.setattr(cfg, "google_api_key", REAL_KEY)
    keys = AppBridge().keys()
    google = next(k for k in keys if k["env"] == "GOOGLE_API_KEY")
    assert google["set"] and google["tail"] == "Plig"
    assert REAL_KEY not in repr(keys)


def test_the_diagnostics_are_local_and_honest(qapp, monkeypatch):
    mgr = SimpleNamespace(_realtime=SimpleNamespace(ready=False), _problem_kind="billing",
                          agents=None)
    bridge = AppBridge.__new__(AppBridge)
    AppBridge.__init__(bridge)
    bridge.manager = mgr
    monkeypatch.setattr(bridge, "mic_name", lambda: "")
    rows = {name: (detail, level) for name, detail, level in bridge.diagnostics()}
    assert rows["Microphone"][1] == "bad"
    assert rows["Live session"] == ("Refused - out of Gemini credit", "bad")
    assert rows["Agent workforce"][1] == "bad"


@pytest.mark.parametrize("method, signal, arg", [
    ("pick_voice", "on_set_voice", "Puck"), ("pick_route", "on_set_conversation_mode", "cascade")])
def test_the_tray_shows_a_new_voice_or_route_straight_away(qapp, method, signal, arg):
    from ui.tray import TrayManager

    tray = TrayManager()
    try:
        emitted, rebuilt = [], []
        getattr(tray, signal).connect(emitted.append)
        original = tray._build_menu
        tray._build_menu = lambda: (rebuilt.append(True), original())
        getattr(tray, method)(arg)
        assert emitted == [arg] and rebuilt, "the menu is rebuilt after the change"
    finally:
        tray.hide_icon()
