"""Tora opens sites in the browser you're using, and answers what you just said
(5 Oct 2026, a tester's report).

"I asked it to open Gmail; it opened Gmail in Microsoft Edge, not in the
browser I already had open, which was Google Chrome." The draw-up of the
desktop tool named edge, brave and opera but not chrome, and a site with no
browser named went to Windows' default. Now "browser" is the one the user
was in most recently (their default if none is open), a site opens there
as a new tab, and a named browser is kept only when the user named it.

"If the user asks a question, don't get stuck on the past thing it was
doing." A box waiting for a yes told the model, every turn, that whatever
was said was a reply to the box; an unanswered box announced "you didn't
approve" minutes later; a walkthrough ran on under a different task; an
abandoned lesson stayed "in progress". Each is fixed and tested here.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest


# -- the browser in use -----------------------------------------------------------------------------------------

def test_the_browser_in_use_is_the_one_used_last_then_the_default(monkeypatch):
    from agents.control.apps import browsers as b
    monkeypatch.setattr(b, "browser_windows", lambda: [("chrome", r"C:\Chrome\chrome.exe"),
                                                       ("edge", r"C:\Edge\msedge.exe")])
    assert b.in_use() == ("chrome", r"C:\Chrome\chrome.exe")
    monkeypatch.setattr(b, "browser_windows", lambda: [])
    monkeypatch.setattr(b, "default_browser", lambda: "firefox")
    assert b.in_use() == ("firefox", "")


def _spawned(monkeypatch):
    from agents.control.apps import browsers as b
    started = []
    monkeypatch.setattr(b, "_spawn", started.append)
    return b, started


def test_a_site_opens_as_a_new_tab_in_the_browser_already_open(monkeypatch):
    b, started = _spawned(monkeypatch)
    monkeypatch.setattr(b, "in_use", lambda: ("chrome", r"C:\Chrome\chrome.exe"))
    where = b.open_in_use("https://mail.google.com")
    assert started == [[r"C:\Chrome\chrome.exe", "https://mail.google.com"]]
    assert where == "Google Chrome, the browser you have open"


def test_firefox_gets_a_new_tab_too(monkeypatch):
    b, started = _spawned(monkeypatch)
    monkeypatch.setattr(b, "in_use", lambda: ("firefox", r"C:\FF\firefox.exe"))
    b.open_in_use("https://mail.google.com")
    assert started == [[r"C:\FF\firefox.exe", "-new-tab", "https://mail.google.com"]]


def test_with_no_browser_open_the_default_one_opens_it(monkeypatch):
    b, started = _spawned(monkeypatch)
    monkeypatch.setattr(b, "in_use", lambda: ("edge", ""))
    monkeypatch.setattr(b.Browser, "path", lambda self: r"C:\Edge\msedge.exe")
    assert b.open_in_use("https://example.com") == "Microsoft Edge, your default browser"
    assert started == [[r"C:\Edge\msedge.exe", "https://example.com"]]
    opened = []
    monkeypatch.setattr(b, "in_use", lambda: ("", ""))
    monkeypatch.setattr(b, "open_uri", opened.append)
    assert b.open_in_use("https://example.com") == "your default browser" and opened == ["https://example.com"]


def test_a_browser_that_was_not_running_is_never_waited_on_or_stopped():
    """run_cli's 10 s timeout used to kill a browser it had just started."""
    import inspect
    from agents.control.apps import browsers as b
    src = inspect.getsource(b.Browser._launch)
    assert "_spawn(argv)" in src and "run_cli(" not in src


def test_browser_is_an_app_tora_can_name_and_windows_uses_it_too(monkeypatch):
    from agents.control.apps import browsers as b
    from agents.control.apps import windows_os
    adapter = b.adapters()[0]
    assert adapter.app == "browser" and {a.name for a in adapter.actions()} == {"open_url", "search"}
    seen = []
    monkeypatch.setattr(b, "open_in_use", lambda url, **_k: seen.append(url) or "Google Chrome, the browser you have open")
    win = next(a for a in windows_os.adapters() if a.app == "windows")
    open_url = next(a for a in win.actions() if a.name == "open_url")
    out = asyncio.run(open_url.run("gmail.com"))
    assert seen == ["https://gmail.com"] and "Google Chrome" in out


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="Windows windows")
def test_finding_browser_windows_never_fails():
    from agents.control.apps import browsers as b
    found = b.browser_windows()
    assert isinstance(found, list) and all(app in b.NAMES for app, _p in found)


# -- a browser the user did not name ----------------------------------------------------------------------------

def _manager(heard):
    from companion_manager import CompanionManager
    m = CompanionManager.__new__(CompanionManager)
    m._heard_buf = [heard] if heard else []
    return m


_C = SimpleNamespace(resolve_app=lambda a: {"edge": "edge", "microsoft edge": "edge", "chrome": "chrome",
                                            "windows": "windows", "spotify": "spotify"}.get(a.lower(), ""))


@pytest.mark.parametrize("heard, app, action, want", [
    ("open gmail", "edge", "open_url", "browser"),              # the tester's case
    ("search for flights to Rome", "chrome", "search", "browser"),
    ("open gmail in edge", "edge", "open_url", "edge"),          # they named it
    ("open gmail in Microsoft Edge", "edge", "open_url", "edge"),
    ("open github in chrome please", "chrome", "open_url", "chrome"),
    ("", "edge", "open_url", "edge"),                            # not known: as asked
    ("open gmail", "windows", "open_url", "windows"),            # already the browser in use
    ("next song", "spotify", "next", "spotify"),
])
def test_a_browser_is_used_only_when_they_named_it(heard, app, action, want):
    assert _manager(heard)._browser_they_meant(_C, app, action) == want


def test_tora_is_told_about_the_browser_in_use():
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    live = re.sub(r'"\s*\n\s*"', "", (root / "ai" / "gemini_realtime.py").read_text(encoding="utf-8"))
    assert "websites (app 'browser'" in live and "ONLY if the user named it" in live
    assert "chrome, edge, firefox, brave, opera" in live, "chrome is on the list"
    assert "open.spotify.com in Edge" not in live
    import config
    assert '"open gmail" - call desktop_action' in config._TECHNICAL_RULES
    assert "open github.com in Edge" not in config._TECHNICAL_RULES


# -- answering what they just said -------------------------------------------------------------------------------

def test_both_routes_are_told_each_turn_is_about_what_they_just_said():
    import config
    for rules in (config._REALTIME_RULES, config._TECHNICAL_RULES):
        assert "EACH TURN IS ABOUT WHAT THEY JUST SAID" in rules
        assert "An earlier request is finished unless they bring it back" in rules


def _with_pending(sensitive=False):
    from companion_manager import CompanionManager
    m = CompanionManager.__new__(CompanionManager)
    req = SimpleNamespace(reason="windows.open_url - open gmail.com", sensitive=sensitive, confirm_word="POST")
    m._agents = SimpleNamespace(pending_approvals=lambda: [req])
    return m._pending_confirmation_line()


def test_a_waiting_box_is_answered_only_by_a_yes_or_no():
    line = _with_pending()
    assert "INSTEAD of answering" not in line and "not asking something new" not in line
    assert "ONLY if their words are a yes or no to it" in line
    assert "Anything else they say is a NEW request - answer it or do it" in line
    assert "Anything else they say is new: answer that." in _with_pending(sensitive=True)


def test_a_box_nobody_answered_is_dropped_quietly():
    from concurrent.futures import Future
    from companion_manager import CompanionManager
    m = CompanionManager.__new__(CompanionManager)
    said = []

    async def speak(text):
        said.append(text)
    m._speak_aside = speak
    found = SimpleNamespace(key="windows.open_url", app="windows", name="open_url")
    lapsed = Future()
    lapsed.set_result(False)
    m.APPROVAL_LAPSED_S = 0.0                                    # it waited its whole time
    asyncio.run(m._after_approval(lapsed, found, {}))
    assert said == [], "not 'you didn't approve opening Gmail' minutes later"
    refused = Future()
    refused.set_result(False)
    m.APPROVAL_LAPSED_S = 999.0                                  # a real, prompt no
    asyncio.run(m._after_approval(refused, found, {}))
    assert said and "did not approve" in said[0]


def test_a_walkthrough_steps_aside_for_something_different():
    import inspect
    from companion_manager import CompanionManager
    src = inspect.getsource(CompanionManager._screen_context)
    assert "If they ask for something DIFFERENT" in src and "guide_step with action stop FIRST" in src


def test_a_lesson_nobody_moved_on_is_dropped(monkeypatch):
    from companion_manager import CompanionManager
    m = CompanionManager.__new__(CompanionManager)
    m._lesson_steps, m._lesson_plan, m._lesson_step_idx = ["Open File", "Click Export"], None, 0
    m._guide_runner = None
    assert m._lesson_stale() is False
    m._lesson_key_at = time.monotonic() - m.LESSON_STALE_S - 1      # ten minutes on, untouched
    assert m._lesson_stale() is True and m._lesson_steps == []
    m._lesson_steps, m._lesson_step_idx = ["A", "B"], 0
    assert m._lesson_stale() is False, "a new lesson starts its own clock"
    m._lesson_step_idx = 1                                        # moved on: the clock restarts
    assert m._lesson_stale() is False
