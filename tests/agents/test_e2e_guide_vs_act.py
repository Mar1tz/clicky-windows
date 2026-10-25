"""Guide versus act (PT-16, the Massive Direction Plan).

A walkthrough shows and the user does: its code never clicks or types,
and while one runs, a tool that acts for them runs only when they asked
her to do it - and the switch is said aloud, never silent. An action on
their screen shows "Doing it for you…" while it runs.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import companion_manager as cm

ROOT = Path(__file__).resolve().parents[2]


def test_the_walkthrough_code_never_clicks_or_types():
    for rel in ("tutor_features/guide.py", "tutor_features/step_watch.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        for acts in ("agents.control", "computer_tools", "SendInput(", "mouse_event(", "keybd_event(",
                     "pyautogui", ".Click(", "SendKeys("):
            assert acts not in src, f"{rel} acts: {acts}"


def _manager(guiding: bool, said: str):
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    mgr._guide_runner = SimpleNamespace(running=guiding)
    mgr._heard_buf = [said]
    shown = []
    mgr.sig_activity = SimpleNamespace(emit=lambda key, words: shown.append(key))
    return mgr, shown


def _run(mgr, name):
    ran = []

    async def act(args):
        ran.append(name)
        return {"ok": True}
    result = asyncio.run(mgr._guide_or_act(name, act)({}))
    return result, ran


def test_while_guiding_she_does_not_do_it_for_them_unasked():
    mgr, _ = _manager(True, "where do I go next")
    result, ran = _run(mgr, "desktop_action")
    assert not ran and "you show, they do" in result["error"]


def test_asked_to_she_does_it_and_says_so():
    mgr, shown = _manager(True, "can you just do it for me")
    result, ran = _run(mgr, "desktop_action")
    assert ran and "did this one step for them" in result["note"]
    assert shown == ["acting"], "the mode is shown"


def test_outside_a_walkthrough_nothing_changes():
    mgr, _ = _manager(False, "play the next song")
    result, ran = _run(mgr, "desktop_action")
    assert ran and "note" not in result
    mgr, _ = _manager(True, "anything")
    result, ran = _run(mgr, "point_at")
    assert ran, "showing is what a walkthrough does"
