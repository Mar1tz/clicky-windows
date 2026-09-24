"""GW-05: plan against the app's real control names.

Across fourteen walkthroughs the pointer fell back to the vision model 52
times. The cause: the planner named each target by reading the screenshot,
and the pointer then searched for that guess — "Check for updates" against
an app that says "Check for Updates...". When the guess is not the app's
exact label the local tiers miss, and every miss is two to four seconds and
a paid screenshot.

UC-04 reads the real labels of any window in a fraction of a second. These
tests pin that the planner is given them, told they are the truth, and that
a near-miss is corrected to the real name afterwards — without "correcting"
a later step's target into some unrelated control that happens to share a
word.
"""
from __future__ import annotations

import asyncio

import pytest

from ai import gemini_planner
from ai.gemini_planner import Guide, Step, snap_targets

CALCULATOR = ["Five", "Multiply by", "Equals", "Clear", "Plus", "Minus",
              "Display is 0", "Open Navigation", "Check for Updates..."]


def _plan_reply(*targets):
    return {"goal": "g", "feasible": True, "preamble": "", "steps": [
        {"instruction": f"do {t}", "target": t, "done_when": "", "possible_actions": [],
         "expected_state": {}, "completion": {"kind": "none", "value": ""}}
        for t in targets]}


def test_the_planner_is_shown_the_real_labels(monkeypatch):
    seen = {}

    async def fake(model, contents, schema, **kw):
        seen["ask"] = contents[0]["parts"][-1]["text"]
        seen["system"] = kw.get("system", "")
        return _plan_reply("Five")

    monkeypatch.setattr(gemini_planner.gemini, "generate_json", fake)
    asyncio.run(gemini_planner.plan("multiply 5 by 5", window_title="Calculator",
                                    controls=CALCULATOR))
    assert "Multiply by" in seen["ask"] and "Equals" in seen["ask"]
    assert "use these exact names" in seen["ask"]
    assert "character for character" in seen["system"]


def test_a_plan_with_no_controls_still_works(monkeypatch):
    async def fake(model, contents, schema, **kw):
        assert "Controls on screen" not in contents[0]["parts"][-1]["text"]
        return _plan_reply("File")

    monkeypatch.setattr(gemini_planner.gemini, "generate_json", fake)
    guide = asyncio.run(gemini_planner.plan("save it", window_title="Notepad"))
    assert guide.steps[0].target == "File"


def test_a_long_control_list_is_capped(monkeypatch):
    many = [f"Row {i}" for i in range(500)]
    seen = {}

    async def fake(model, contents, schema, **kw):
        seen["ask"] = contents[0]["parts"][-1]["text"]
        return _plan_reply("Row 1")

    monkeypatch.setattr(gemini_planner.gemini, "generate_json", fake)
    asyncio.run(gemini_planner.plan("pick one", controls=many))
    assert f"Row {gemini_planner.MAX_CONTROLS - 1}" in seen["ask"]
    assert f"Row {gemini_planner.MAX_CONTROLS + 5}" not in seen["ask"]


# ── correcting the model's reading to the app's own label ────────────────────

def _guide(*targets):
    return Guide(goal="g", steps=[Step(instruction="x", target=t) for t in targets])


def test_a_near_miss_is_corrected_to_the_real_label():
    guide = _guide("Check for updates", "multiply by", "equals")
    changed = snap_targets(guide, CALCULATOR)
    assert [s.target for s in guide.steps] == \
        ["Check for Updates...", "Multiply by", "Equals"]
    assert len(changed) == 3


def test_an_exact_label_is_left_exactly_as_it_is():
    guide = _guide("Five", "Equals")
    assert snap_targets(guide, CALCULATOR) == []
    assert [s.target for s in guide.steps] == ["Five", "Equals"]


def test_a_later_step_naming_something_not_yet_on_screen_is_left_alone():
    """Step 2 of "open the menu, then pick Scientific" names a menu item
    that does not exist until step 1 runs. Snapping it to some current
    control that shares a word would point at the wrong thing."""
    guide = _guide("Open Navigation", "Scientific")
    snap_targets(guide, CALCULATOR)
    assert guide.steps[1].target == "Scientific"


def test_a_step_with_no_target_is_left_alone():
    guide = _guide("", "Five")
    snap_targets(guide, CALCULATOR)
    assert guide.steps[0].target == ""


# ── the manager feeds it the window the user is actually in ──────────────────

def test_the_manager_reads_the_foreground_window_and_passes_it_on(monkeypatch):
    from companion_manager import CompanionManager
    from agents.control import uia

    monkeypatch.setattr(uia, "controls", lambda title, limit=40, query="": [
        {"name": "Five", "type": "Button", "actions": ["invoke"], "enabled": True},
        {"name": "Greyed", "type": "Button", "actions": ["invoke"], "enabled": False},
        {"name": "Five", "type": "Button", "actions": ["invoke"], "enabled": True},
        {"name": "", "type": "Pane", "actions": ["invoke"], "enabled": True},
    ])
    names = CompanionManager._foreground_controls("Calculator")
    assert names == ["Five"], "enabled, named, and each once"


def test_no_window_or_no_tree_means_no_list_not_a_crash(monkeypatch):
    from companion_manager import CompanionManager
    from agents.control import uia

    def broken(*a, **k):
        raise RuntimeError("UIA is not available here")

    monkeypatch.setattr(uia, "controls", broken)
    assert CompanionManager._foreground_controls("Calculator") == []
    assert CompanionManager._foreground_controls("") == []
