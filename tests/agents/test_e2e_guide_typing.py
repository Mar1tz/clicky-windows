"""GW-10: a typing step finishes on the typed text, read without looking.

A step that asks for text had two ways to finish: a signal the planner
guessed (often already true, so muted) or the vision model comparing two
screenshots. Both are poor for typing. UI Automation already knows what is
in the focused field, locally and instantly.

The trap: the value changes on the FIRST keystroke. "Changed" alone would
advance the guide at the "y" of "youtube.com" while the person is still
typing. So a typing step is done when the field holds something new AND
has stopped changing.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from ai.verification import ADVANCE, AnyOf, TYPING_SETTLE_S, TypedText, Verdict


class Field:
    """A focused text field whose value the test types into, on a fake clock."""

    def __init__(self, value: str | None = "") -> None:
        self.value = value
        self.t = 0.0

    def read(self):
        return self.value

    def clock(self):
        return self.t


def _typed(field):
    return TypedText(read=field.read, clock=field.clock)


# ── never on the first keystroke ─────────────────────────────────────────────

def test_typing_is_not_finished_while_the_person_is_still_typing():
    field = Field("")
    check = _typed(field)
    assert check.arm() == ["typed_text"]
    for i, ch in enumerate("youtube.com"):
        field.value += ch
        field.t = i * 0.15                          # a normal typing pace
        assert not check().done, f"advanced at {field.value!r} - mid-word"


def test_it_finishes_once_the_text_has_settled():
    field = Field("")
    check = _typed(field)
    check.arm()
    field.value, field.t = "youtube.com", 1.0
    assert not check().done                          # the change itself
    field.t = 1.0 + TYPING_SETTLE_S - 0.1
    assert not check().done                          # not settled yet
    field.t = 1.0 + TYPING_SETTLE_S + 0.1
    verdict = check()
    assert verdict.done and verdict.method == "typed_text"
    assert verdict.confidence >= ADVANCE


def test_text_that_was_already_there_is_not_new_text():
    field = Field("already here")
    check = _typed(field)
    check.arm()
    field.t = 10.0
    assert not check().done


def test_clearing_the_field_is_not_typing_something():
    field = Field("old text")
    check = _typed(field)
    check.arm()
    field.value, field.t = "", 1.0
    check()
    field.t = 5.0
    assert not check().done


def test_with_no_focused_field_there_is_nothing_to_watch():
    field = Field(None)
    check = _typed(field)
    assert check.arm() == [] and "typed_text" in check.muted
    assert not check().done


# ── a password is never read ─────────────────────────────────────────────────

def test_a_password_field_is_never_read(monkeypatch):
    from ai import hybrid_pointer

    read = []

    class Pattern:
        @property
        def Value(self):
            read.append("value")
            return "hunter2"

    node = SimpleNamespace(IsPassword=True, GetPattern=lambda pid: Pattern())
    fake = SimpleNamespace(GetFocusedControl=lambda: node,
                           PatternId=SimpleNamespace(ValuePattern=1))
    monkeypatch.setitem(sys.modules, "uiautomation", fake)
    assert hybrid_pointer.focused_value() is None
    assert read == [], "not even its length"


def test_an_ordinary_field_is_read(monkeypatch):
    from ai import hybrid_pointer
    node = SimpleNamespace(IsPassword=False,
                           GetPattern=lambda pid: SimpleNamespace(Value="hello"))
    fake = SimpleNamespace(GetFocusedControl=lambda: node,
                           PatternId=SimpleNamespace(ValuePattern=1))
    monkeypatch.setitem(sys.modules, "uiautomation", fake)
    assert hybrid_pointer.focused_value() == "hello"


# ── which steps are typing steps ─────────────────────────────────────────────

@pytest.mark.parametrize("text, typing", [
    ("Type your name into the box.", True),
    ("Enter your email address.", True),
    ("Paste the link into the address bar.", True),
    ("Fill in the subject line.", True),
    ("Type youtube.com and press Enter.", False),   # Enter is the finish line
    ("Type your query and hit return.", False),
    ("Click Save.", False),
    ("Entertain yourself.", False),                 # "enter " needs the space
])
def test_a_typing_step_is_recognised_and_one_ending_in_enter_is_not(text, typing):
    from companion_manager import _is_typing_step
    assert _is_typing_step(text) is typing


def test_a_typing_step_watches_the_text_alongside_its_other_signals():
    """Beside, not instead: a typing step usually also has the next step's
    target as a signal, and that is often already on screen and muted."""
    from companion_manager import CompanionManager
    from ai.verification import Expectation

    mgr = CompanionManager.__new__(CompanionManager)
    mgr._dpi_scale = lambda: 1.0
    step = SimpleNamespace(instruction="Type your name.", target="Name",
                           expected=Expectation(control_appears="Welcome"))
    w = mgr._make_step_watcher(step, next_target="Save")
    parts = w._local_check.checks
    assert any(isinstance(p, TypedText) for p in parts)
    assert any(type(p).__name__ == "ArmedExpectation" for p in parts)


def test_an_ordinary_step_is_not_given_a_typing_check():
    from companion_manager import CompanionManager
    from ai.verification import Expectation

    mgr = CompanionManager.__new__(CompanionManager)
    mgr._dpi_scale = lambda: 1.0
    step = SimpleNamespace(instruction="Click Save.", target="Save",
                           expected=Expectation(control_appears="Saved"))
    w = mgr._make_step_watcher(step)
    assert not isinstance(w._local_check, AnyOf)


# ── several finish lines, first one crossed wins ─────────────────────────────

class Fixed:
    def __init__(self, conf, kept=("x",)):
        self.conf, self.kept = conf, list(kept)

    def arm(self):
        return self.kept

    @property
    def muted(self):
        return set() if self.kept else {"x"}

    def __call__(self):
        return Verdict(confidence=self.conf, method=f"c{self.conf}")


def test_any_of_takes_the_first_finish_line_crossed():
    check = AnyOf([Fixed(0.0), Fixed(0.9), None])
    assert check.arm() == ["x", "x"]
    assert check().done and check().method == "c0.9"


def test_any_of_with_nothing_crossed_is_not_done():
    assert not AnyOf([Fixed(0.0), Fixed(0.3)])().done


def test_one_stale_part_does_not_mute_the_others():
    check = AnyOf([Fixed(0.0, kept=()), Fixed(0.0)])
    assert check.arm() == ["x"]
    assert check.muted == {"x"}
