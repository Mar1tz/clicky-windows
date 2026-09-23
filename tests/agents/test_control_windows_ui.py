"""UC-04: every named control in an open window becomes an action.

The milestone that makes "do anything on my PC" true on a machine nobody
configured. Eight hand-written adapters cover eight apps; on anyone else's
computer those are mostly absent. UI Automation is not: every Win32,
WinForms, WPF and WinUI app publishes its controls and says what can be
done to each. Measured here: 43 actions in Notepad, 60 in Brave, in
0.2-0.4s, none of them written by hand.

Nothing in this file touches the real desktop — the window list and the
control reader are both injected.
"""
from __future__ import annotations

import asyncio

import pytest

from agents.control.model import Risk
from agents.control.providers.windows_ui import (
    MAX_ACTIONS, WindowsUIProvider, risk_for,
)
from agents.control.registry import AppControl

WINDOWS = [
    {"hwnd": 1, "title": "Untitled - Notepad", "exe": "notepad.exe", "app": "notepad"},
    {"hwnd": 2, "title": "Inbox - Mail", "exe": "olk.exe", "app": "olk"},
    {"hwnd": 3, "title": "Drafts - Mail", "exe": "olk.exe", "app": "olk"},
]

CONTROLS = {
    "Untitled - Notepad": [
        {"name": "Save", "type": "Button", "actions": ["invoke"], "enabled": True},
        {"name": "Word wrap", "type": "CheckBox", "actions": ["toggle"], "enabled": True},
        {"name": "Text editor", "type": "Edit", "actions": ["set_value"], "enabled": True},
        {"name": "Print", "type": "Button", "actions": ["invoke"], "enabled": False},
        {"name": "Ln 1, Col 1", "type": "Text", "actions": ["read"], "enabled": True},
    ],
    "Inbox - Mail": [
        {"name": "Delete", "type": "Button", "actions": ["invoke"], "enabled": True},
        {"name": "Send", "type": "Button", "actions": ["invoke"], "enabled": True},
        {"name": "Sender", "type": "Text", "actions": ["read"], "enabled": True},
    ],
}


class FakeControls:
    """Stands in for uia.controls, and remembers what it was asked."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def __call__(self, window, limit=40, query=""):
        self.asked.append(window)
        return list(CONTROLS.get(window, []))


@pytest.fixture
def provider():
    reader = FakeControls()
    return WindowsUIProvider(windows=list(WINDOWS), controls=reader), reader


@pytest.fixture
def control(provider):
    p, reader = provider
    return AppControl(adapters=[], providers=[p]), reader


def run(coro):
    return asyncio.run(coro)


# ── which app is which ───────────────────────────────────────────────────────

def test_an_app_is_its_executable_not_its_window_title(provider):
    """"Inbox - Mail" and "Drafts - Mail" are one app. Titles are localised
    and change every minute; olk.exe does not, which is exactly what makes
    this work the same on a PC in another language."""
    p, _ = provider
    apps = p.apps()
    assert [a.app for a in apps].count("olk") == 1
    assert len(apps) == 2


def test_the_tail_of_a_title_becomes_a_name_a_person_would_say(provider):
    p, _ = provider
    mail = [a for a in p.apps() if a.app == "olk"][0]
    assert mail.display_name == "Mail" and "Mail" in mail.aliases
    assert mail.running is True


def test_a_running_app_is_addressable_by_that_name(control):
    c, _ = control
    assert c.resolve_app("Mail") == "olk"
    assert c.resolve_app("notepad") == "notepad"


# ── controls become actions ──────────────────────────────────────────────────

def test_every_named_control_becomes_something_that_can_be_asked_for(control):
    c, _ = control
    names = {a.name for a in c.actions(app="notepad")}
    assert names == {"click_save", "toggle_word_wrap", "set_text_editor",
                     "read_ln_1_col_1"}


def test_a_control_that_is_greyed_out_is_not_offered(control):
    c, _ = control
    assert "click_print" not in {a.name for a in c.actions(app="notepad")}


def test_setting_a_value_asks_for_the_value(control):
    c, _ = control
    action = c.find("notepad", "set_text_editor")
    assert [p.name for p in action.params] == ["value"]
    assert action.params[0].required


def test_an_action_reaches_the_real_control(control, monkeypatch):
    from agents.control.providers import windows_ui
    calls = []

    def fake_act(window, ctl, verb, value=""):
        calls.append((window, ctl, verb, value))
        return "pressed it"

    monkeypatch.setattr(windows_ui.uia, "act", fake_act)
    c, _ = control
    out = run(c.run("notepad", "click_save"))
    assert out.ok and calls == [("Untitled - Notepad", "Save", "invoke", "")]


def test_a_control_that_vanished_is_a_refusal_not_a_crash(control, monkeypatch):
    from agents.control.providers import windows_ui

    def gone(window, ctl, verb, value=""):
        raise LookupError("no control called 'Save' any more")

    monkeypatch.setattr(windows_ui.uia, "act", gone)
    c, _ = control
    out = run(c.run("notepad", "click_save"))
    assert not out.ok and "Save" in out.message


# ── risk, which is the whole reason this is safe to ship ─────────────────────

@pytest.mark.parametrize("name, kind, expect", [
    ("Delete", "Button", Risk.SENSITIVE),
    ("Send", "Button", Risk.SENSITIVE),
    ("Pay now", "Button", Risk.SENSITIVE),
    ("Close account", "Button", Risk.SENSITIVE),
    ("Uninstall", "MenuItem", Risk.SENSITIVE),
    ("Sender", "Button", Risk.CHANGES),          # whole words only
    ("Save", "Button", Risk.CHANGES),
    ("Word wrap", "CheckBox", Risk.CHANGES),
])
def test_a_name_that_sounds_final_confirms_harder(name, kind, expect):
    assert risk_for(name, kind, "invoke") is expect


def test_reading_is_free(control):
    c, _ = control
    assert c.find("notepad", "read_ln_1_col_1").risk is Risk.PASSIVE


def test_nothing_synthesised_ever_runs_without_asking(control):
    """Nobody judged these actions by hand, so the floor is "confirm"."""
    c, _ = control
    for app in ("notepad", "olk"):
        for action in c.actions(app=app):
            if action.risk is Risk.PASSIVE:
                continue
            assert action.risk.needs_confirmation, action.key


def test_the_dangerous_ones_cannot_be_waved_through_by_voice(control):
    c, _ = control
    assert c.find("olk", "click_send").risk is Risk.SENSITIVE
    assert c.find("olk", "click_delete").risk is Risk.SENSITIVE


# ── cost ─────────────────────────────────────────────────────────────────────

def test_no_window_is_read_until_its_app_is_named(control):
    c, reader = control
    c.apps()
    c.resolve_app("notepad")
    assert reader.asked == [], "reading every window every turn costs more than the answer"
    c.actions(app="notepad")
    assert reader.asked == ["Untitled - Notepad"]


def test_a_window_that_will_not_be_read_is_not_a_crash():
    def refuse(*_args, **_kwargs):
        raise RuntimeError("the window went away")

    p = WindowsUIProvider(windows=list(WINDOWS), controls=refuse)
    assert p.actions("notepad") == []


def test_an_app_with_no_window_open_offers_nothing(control):
    c, _ = control
    assert c.actions(app="photoshop") == []


def test_a_window_full_of_rows_does_not_flood_the_model():
    """Four hundred list rows is a data set, not a menu."""
    many = [{"name": f"Row {i}", "type": "ListItem", "actions": ["select"],
             "enabled": True} for i in range(400)]
    p = WindowsUIProvider(
        windows=[{"hwnd": 1, "title": "Big", "exe": "big.exe", "app": "big"}],
        controls=lambda *a, **k: many)
    assert len(p.actions("big")) == MAX_ACTIONS
