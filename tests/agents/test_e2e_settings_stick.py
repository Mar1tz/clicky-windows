"""APP-03: settings stick.

The tray's tutor and privacy switches were held only in memory, so every
launch put privacy guard, OCR, web search, slow mode, code mode, the
journal and auto-advance back to their defaults - whatever the user had
chosen. Two other settings (custom instructions, reply language) were
saved, but beside companion_manager.py: in the packaged app that is
_internal\\, where nothing reads them.

Every test here writes to its own .env (conftest), never the real one.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from dotenv import load_dotenv

import config as config_module
from config import cfg

SWITCHES = list(config_module.Config.SWITCHES)


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _restart(tmp_path, monkeypatch):
    """What a new launch sees: the process environment gone, the .env read."""
    for env, _ in config_module.Config.SWITCHES.values():
        monkeypatch.delenv(env, raising=False)
    load_dotenv(tmp_path / ".env", override=True)


@pytest.mark.parametrize("name", SWITCHES)
def test_every_switch_survives_a_restart(name, tmp_path, monkeypatch):
    env, default = config_module.Config.SWITCHES[name]
    cfg.set_switch(name, not default)
    _restart(tmp_path, monkeypatch)
    assert cfg.switch(name) is (not default)
    assert f"{env}=" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_an_unsaved_switch_is_its_default(tmp_path):
    assert cfg.switch("privacy_guard") is True
    assert cfg.switch("slow_mode") is False


def _bare_manager():
    from companion_manager import CompanionManager

    mgr = CompanionManager.__new__(CompanionManager)
    mgr._submit = lambda coro: coro.close()
    return mgr


@pytest.mark.parametrize("setter, name", [
    ("set_web_search", "web_search"), ("set_slow_mode", "slow_mode"),
    ("set_guide_auto_advance", "auto_advance"), ("set_privacy_guard", "privacy_guard"),
    ("set_code_mode_auto", "code_mode"), ("set_journal", "journal"),
    ("set_ocr_enabled", "ocr"),
])
def test_the_tray_switch_saves_what_it_changes(setter, name, tmp_path, monkeypatch):
    mgr = _bare_manager()
    default = config_module.Config.SWITCHES[name][1]
    getattr(mgr, setter)(not default)
    _restart(tmp_path, monkeypatch)
    assert cfg.switch(name) is (not default)


def test_quiz_mode_is_never_brought_back_by_a_restart():
    assert "quiz_mode" not in config_module.Config.SWITCHES
    import inspect
    from companion_manager import CompanionManager
    src = inspect.getsource(CompanionManager.__init__)
    assert "self._quiz_mode = False" in src


def test_the_manager_starts_as_it_was_left(tmp_path, monkeypatch):
    import inspect
    from agents import bootstrap
    from companion_manager import CompanionManager

    # Each saved switch is read where its owner starts: the companion's in
    # its __init__, the workforce's (auto_approve, APP-14) when it is built,
    # the updater's (auto_update) in main() before anything is shown.
    from pathlib import Path
    starts = (inspect.getsource(CompanionManager.__init__)
              + inspect.getsource(bootstrap.build_agent_manager)
              + (Path(__file__).resolve().parents[2] / "main.py").read_text(encoding="utf-8"))
    for name in SWITCHES:
        assert (f'cfg.switch("{name}")' in starts
                or f'_switched_on("{name}")' in starts), f"{name} is not read at start"


def test_the_tray_shows_what_was_saved(qapp, tmp_path, monkeypatch):
    from ui.tray import TrayManager

    cfg.set_switch("privacy_guard", False)
    cfg.set_switch("slow_mode", True)
    tray = TrayManager()
    try:
        assert tray._privacy_enabled is False and tray._slow_enabled is True
        texts = []

        def walk(menu):
            for action in menu.actions():
                texts.append(action.text())
                if action.menu() is not None:
                    walk(action.menu())

        walk(tray._tray.contextMenu())
        assert not any("Multilingual" in t for t in texts), \
            "a switch that did nothing is not offered"
    finally:
        tray.hide_icon()


@pytest.mark.parametrize("setter, key, value", [
    ("set_custom_instructions", "CUSTOM_INSTRUCTIONS", "Only help with Excel"),
    ("set_response_language", "RESPONSE_LANGUAGE", "en"),
])
def test_the_other_settings_are_saved_where_they_are_read(setter, key, value, tmp_path):
    mgr = _bare_manager()
    mgr.sig_error = SimpleNamespace(emit=lambda *a: None)
    getattr(mgr, setter)(value)
    saved = (tmp_path / ".env").read_text(encoding="utf-8")
    assert key in saved, "saved next to the app, not inside it"
