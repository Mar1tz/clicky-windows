"""LG-07 and LG-08: every step is one thing, with something to point at -
and a shortcut step is done the moment its keys are pressed.

From the 24 Sep, 23:02 walkthrough ("open a new tab and search for laptops
on Takealot"):

  * "Type takealot.com into the address bar and press Enter" came with no
    target, so it had no arrow at all.
  * "Click the search bar on Takealot, type laptops, and press Enter" was
    three things in one step: one arrow, and the check said "not done yet"
    twice while the user was halfway through it.
  * "Press Ctrl and T together to open a new tab" took a 4 s vision check
    to be seen done.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ai import verification
from ai.gemini_planner import Guide, Step, split_compound_steps


def _plan(*steps):
    guide = Guide(goal="g", steps=list(steps))
    split_compound_steps(guide, [])
    return [(s.instruction, s.target) for s in guide.steps]


# ── LG-07: one thing per step, something to point at ─────────────────────────

def test_a_click_then_typing_is_two_steps_on_the_same_field():
    assert _plan(Step(instruction="Click the search bar on Takealot, type laptops, and press Enter.",
                      target="Search")) == [
        ("Click the search bar on Takealot.", "Search"),
        ("Type laptops, and press Enter.", "Search"),
    ]


def test_a_typing_step_points_at_the_field_it_names():
    assert _plan(Step(instruction="Type takealot.com into the address bar and press Enter.")) == [
        ("Type takealot.com into the address bar and press Enter.", "address bar")]
    assert _plan(Step(instruction="Enter your email in the search box.")) == [
        ("Enter your email in the search box.", "search box")]


@pytest.mark.parametrize("instruction", [
    "Type youtube.com and press Enter.",        # one intent, and no field named
    "Press Ctrl and T together to open a new tab.",
])
def test_what_is_already_one_thing_is_left_alone(instruction):
    assert _plan(Step(instruction=instruction)) == [(instruction, "")]


def test_a_named_target_is_never_overwritten():
    assert _plan(Step(instruction="Type laptops into the search bar.", target="Search products")) == [
        ("Type laptops into the search bar.", "Search products")]


def test_a_wait_step_gets_no_target():
    step = Step(instruction="Type nothing - wait in the box while it exports.")
    step.kind = "wait"
    assert _plan(step) == [(step.instruction, "")]


# ── LG-08: the keys a step names ─────────────────────────────────────────────

@pytest.mark.parametrize("instruction, keys", [
    ("Press Ctrl and T together to open a new tab.", "ctrl+t"),
    ("Press Ctrl+Shift+N.", "ctrl+shift+n"),
    ("Hit Alt + Tab to switch windows.", "alt+tab"),
    ("Press Enter.", "enter"),
    ("Type takealot.com into the address bar and press Enter.", "enter"),
    ("press F5 to reload", "f5"),
    ("Press the Windows key.", "windows"),
    ("Click Captions.", ""),
    ("Press the Export button.", ""),           # a button, not a key
])
def test_the_keys_a_step_names(instruction, keys):
    assert verification.shortcut_in(instruction) == keys


class _Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_the_step_is_done_the_moment_its_keys_are_pressed():
    clock = _Clock()
    hooked, released = {}, []

    def hook(combo, on_press):
        hooked[combo] = on_press
        return lambda: released.append(combo)

    check = verification.KeysPressed("ctrl+t", hook=hook, clock=clock)
    assert check.arm() == ["keys"] and "ctrl+t" in hooked
    assert not check().done, "not pressed yet"
    hooked["ctrl+t"]()                         # the user presses Ctrl+T
    assert not check().done, "a moment for the tab to open"
    clock.now += verification.KEYS_SETTLE_S
    verdict = check()
    assert verdict.done and verdict.method == "keys"
    check.close()
    assert released == ["ctrl+t"], "released when the step ends"


def test_no_keyboard_hook_is_no_signal_not_a_crash():
    def broken(combo, on_press):
        raise OSError("no hook here")

    check = verification.KeysPressed("ctrl+t", hook=broken)
    assert check.arm() == [] and check.muted == {"keys"}


def test_the_step_watcher_releases_the_keys_when_the_step_ends():
    from tutor_features import step_watch
    released = []
    keys = verification.KeysPressed("ctrl+t", hook=lambda c, cb: (lambda: released.append(c)))
    keys.arm()

    async def never(*a):
        return {"done": False}

    watcher = step_watch.StepWatcher("Press Ctrl and T together.", judge=never,
                                     local_check=verification.AnyOf([None, keys]))
    watcher.end()
    assert released == ["ctrl+t"]


def test_a_shortcut_step_watches_for_its_keys():
    from companion_manager import CompanionManager
    mgr = CompanionManager.__new__(CompanionManager)
    mgr._dpi_scale = lambda: 1.0
    watcher = mgr._make_step_watcher(Step(instruction="Press Ctrl and T together to open a new tab."))
    checks = getattr(watcher._local_check, "checks", [])
    assert any(isinstance(c, verification.KeysPressed) and c.combo == "ctrl+t" for c in checks)
