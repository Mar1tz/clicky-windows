"""UC-02: every app on the Start menu, openable by name.

223 apps on this machine, each with an AppUserModelID Windows will launch
through the AppsFolder shell path — the same door the Start menu uses. No
install path to find, no shortcut to parse, no file written per app.

Nothing here touches the real machine: the app list is injected, and
`conftest` points the cache at tmp_path so a test can never write the
user's own `installed_apps.json`.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from agents.control.model import Unverified
from agents.control.providers import launcher
from agents.control.registry import AppControl

ENTRIES = [
    {"name": "Notepad", "id": "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"},
    {"name": "Calculator", "id": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"},
    {"name": "Microsoft Word", "id": "Microsoft.Office.WINWORD.EXE.15"},
    {"name": "WordPad", "id": r"{6D809377}\wordpad.exe"},
    {"name": "Antigravity", "id": "Google.Antigravity"},
    {"name": "Antigravity", "id": "electron.app.Antigravity"},
]


@pytest.fixture
def control():
    return AppControl(adapters=[], providers=[launcher.LauncherProvider(ENTRIES)])


def run(coro):
    return asyncio.run(coro)


# ── the app list ─────────────────────────────────────────────────────────────

def test_every_start_menu_app_becomes_addressable(control):
    apps = {a["app"] for a in control.apps()}
    assert {"notepad", "calculator", "microsoft_word", "wordpad"} <= apps


def test_one_name_is_one_app(control):
    """"Antigravity" is listed twice — a packaged build and an Electron one.
    Offering a person two identical choices is worse than taking the one
    Windows lists first."""
    assert [a["app"] for a in control.apps()].count("antigravity") == 1


def test_a_discovered_app_says_where_it_came_from(control):
    entry = [a for a in control.apps() if a["app"] == "notepad"][0]
    assert entry["via"] == "launcher" and entry["name"] == "Notepad"


# ── naming, which is where this gets dangerous ───────────────────────────────

@pytest.mark.parametrize("said, expect", [
    ("Notepad", "notepad"),
    ("notepad", "notepad"),
    ("calculator", "calculator"),
    ("Microsoft Word", "microsoft_word"),
    ("word", "microsoft_word"),        # a whole word beats a substring
    ("wordpad", "wordpad"),
    ("note", "notepad"),               # a prefix is fine
])
def test_apps_are_found_by_what_a_person_would_say(control, said, expect):
    assert control.resolve_app(said) == expect


@pytest.mark.parametrize("said", ["a", "xy", "photoshop", ""])
def test_a_bad_guess_is_no_answer_rather_than_the_wrong_app(control, said):
    """With 223 apps, a loose substring rule lets one letter pick whatever
    sorts first. Saying "I don't know that app" is the better failure."""
    assert control.resolve_app(said) == ""


# ── opening ──────────────────────────────────────────────────────────────────

def test_opening_goes_through_the_start_menus_own_door(control, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(launcher.os, "startfile", lambda target: opened.append(target))
    monkeypatch.setattr(launcher, "_window_titles", lambda: ["Notepad"])

    out = run(control.run("notepad", "open"))
    assert out.ok
    assert opened == [r"shell:AppsFolder\Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"]


def test_opening_asks_first(control):
    action = control.find("notepad", "open")
    assert action.risk.needs_confirmation, "a window appearing is the user's screen changing"


def test_an_app_that_never_appears_is_not_reported_as_opened(control, monkeypatch):
    """DC-24 again: "opened Notepad" when nothing opened is the lie."""
    monkeypatch.setattr(launcher.os, "startfile", lambda target: None)
    monkeypatch.setattr(launcher, "_window_titles", lambda: [])
    monkeypatch.setattr(launcher, "APPEAR_TIMEOUT_S", 0.6)

    out = run(control.run("notepad", "open"))
    assert not out.ok and "did not put a window up" in out.message


def test_an_app_that_was_already_open_says_so(control, monkeypatch):
    monkeypatch.setattr(launcher.os, "startfile", lambda target: None)
    monkeypatch.setattr(launcher, "_window_titles", lambda: ["Notepad - untitled"])
    out = run(control.run("notepad", "open"))
    assert out.ok and "already open" in out.message


def test_an_app_nobody_has_gets_no_actions(control):
    assert control.actions(app="photoshop") == []


# ── the cache, because 1.4s is not a per-minute cost ─────────────────────────

def test_the_app_list_is_scanned_once_and_kept(tmp_path, monkeypatch):
    path = tmp_path / "apps.json"
    monkeypatch.setenv("TORA_APPS_CACHE", str(path))
    scans = []

    def fake_scan():
        scans.append(1)
        return ENTRIES

    monkeypatch.setattr(launcher, "scan_installed", fake_scan)
    assert launcher.installed() == ENTRIES
    assert launcher.installed() == ENTRIES
    assert len(scans) == 1, "asking Windows costs 1.4s; do it once"
    assert json.loads(path.read_text(encoding="utf-8")) == ENTRIES


def test_a_stale_cache_is_refreshed(tmp_path, monkeypatch):
    path = tmp_path / "apps.json"
    path.write_text(json.dumps([{"name": "Old", "id": "old"}]), encoding="utf-8")
    old = time.time() - (launcher.CACHE_MAX_AGE_S + 60)
    import os as _os
    _os.utime(path, (old, old))
    monkeypatch.setenv("TORA_APPS_CACHE", str(path))
    monkeypatch.setattr(launcher, "scan_installed", lambda: ENTRIES)
    assert launcher.installed() == ENTRIES


def test_a_corrupt_cache_rescans_rather_than_crashing(tmp_path, monkeypatch):
    path = tmp_path / "apps.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("TORA_APPS_CACHE", str(path))
    monkeypatch.setattr(launcher, "scan_installed", lambda: ENTRIES)
    assert launcher.installed() == ENTRIES


def test_no_powershell_means_no_apps_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("TORA_APPS_CACHE", str(tmp_path / "apps.json"))

    def boom():
        raise OSError("powershell is not on this machine")

    monkeypatch.setattr(launcher, "scan_installed", boom)
    assert launcher.installed() == []


# ── the 77 apps that used to be invisible ────────────────────────────────────

PACKAGED = ENTRIES + [
    {"name": "Camera", "id": "Microsoft.WindowsCamera_8wekyb3d8bbwe!App"},
    {"name": "Films & TV", "id": "Microsoft.ZuneVideo_8wekyb3d8bbwe!Microsoft.ZuneVideo"},
]


@pytest.fixture
def listed(monkeypatch):
    monkeypatch.setattr(launcher, "installed", lambda refresh=False: PACKAGED)


def test_a_store_app_is_found_even_though_it_has_no_shortcut(listed):
    """The complaint: "it keeps saying this app was not found in the
    installed list". Two lists existed — a walk of the Start Menu's .lnk
    files, and Windows' own. A packaged app has no .lnk, so 77 apps on this
    machine were invisible to the first one: Calculator, Mail, Camera,
    Clock, Copilot."""
    found = launcher.find_app("camera")
    assert found and found[0] == "Camera"


@pytest.mark.parametrize("said, expect", [
    ("Calculator", "Calculator"),
    ("calculator", "Calculator"),
    ("films", "Films & TV"),
    ("word", "Microsoft Word"),
    ("note", "Notepad"),
])
def test_an_app_is_found_by_whatever_it_is_called(listed, said, expect):
    found = launcher.find_app(said)
    assert found and found[0] == expect


@pytest.mark.parametrize("said", ["", "zz", "photoshop"])
def test_an_app_that_is_not_here_is_not_invented(listed, said):
    assert launcher.find_app(said) is None


def test_a_near_miss_is_offered_rather_than_a_flat_no(listed):
    assert "Calculator" in launcher.near_matches("calulator")


def test_the_agent_tool_sees_packaged_apps_too(listed):
    """open_app reached its own Start-Menu walk, which had the same hole."""
    from agents.tools.app_tools import find_app
    got = find_app("camera")
    assert got and got[0] == "Camera"
    assert got[1].startswith("shell:AppsFolder")
