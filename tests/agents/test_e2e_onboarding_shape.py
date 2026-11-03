"""The new first launch, phase A (29 Oct 2026, docs/ui/HEYCLICKY_ONBOARDING.md):
the frame that grows out of the pill - Back, step dots, a sound switch,
Skip, Continue lit only when the step is proven - and the pill's glow while
Tora thinks, speaks and listens, as HeyClicky's notch does.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PyQt6")


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


# ── the pill's glow ────────────────────────────────────────────────────────

@pytest.mark.parametrize("state", ["thinking", "speaking", "listening"])
def test_the_pill_glows_in_the_states_colour(qapp, state):
    from ui import tokens
    from ui.flow_bar import FlowBar
    b = FlowBar()
    b.set_state(state)
    colour, strength = b.glow()
    assert colour == tokens.STATE_GLOW[state] and strength > 0
    b.close()


def test_no_glow_at_rest_and_it_follows_the_voice(qapp):
    from ui.flow_bar import FlowBar
    b = FlowBar()
    assert b.glow() == ("", 0.0)
    b.set_state("speaking")
    b._level = 0.0
    quiet = b.glow()[1]
    b._level = 0.9
    assert b.glow()[1] > quiet, "louder, brighter"
    b.close()


# ── the frame ──────────────────────────────────────────────────────────────

@pytest.fixture
def panel(qapp, monkeypatch):
    import ui.onboarding_shape as O
    monkeypatch.setattr(O, "animations_on", lambda: False)
    seen = []

    def step(title, wide=False, ready=False):
        def make():
            s = O._StandIn(title, "", wide, ready)
            s.enter = lambda: seen.append(("enter", title))
            s.leave = lambda: seen.append(("leave", title))
            return s
        return make
    p = O.OnboardingPanel([step("mic"), step("talk"), step("interview", wide=True, ready=True)], ready_after=1)
    p.seen = seen
    p.open_from()
    yield p
    from tests.agents.test_e2e_onboarding_steps import throw_away
    throw_away(p)


def test_continue_waits_for_the_step_to_be_proven(panel):
    assert panel.at == 0 and not panel.go.isEnabled()
    panel.next()
    assert panel.at == 0, "not proven: Continue does nothing"
    panel.step().done()
    assert panel.go.isEnabled()
    panel.next()
    assert panel.at == 1 and not panel.go.isEnabled()


def test_back_skip_and_the_signals(panel):
    got = []
    panel.ready.connect(lambda: got.append("ready"))
    panel.completed.connect(lambda: got.append("completed"))
    assert panel.back_button.isHidden(), "nothing to go back to"
    panel.step().done()
    panel.next()
    assert not panel.back_button.isHidden()
    panel.back()
    assert panel.at == 0 and panel.go.isEnabled(), "a proven step stays proven"
    panel.skip()
    assert got == ["ready", "completed"], "Skip still lets the app start"


def test_ready_once_the_steps_that_need_nothing_are_done(panel):
    got = []
    panel.ready.connect(lambda: got.append("ready"))
    panel.step().done()
    panel.next()
    assert got == []
    panel.step().done()
    assert got == ["ready"]


def test_steps_are_told_when_they_show_and_go(panel):
    panel.step().done()
    panel.next()
    assert ("enter", "mic") in panel.seen and ("leave", "mic") in panel.seen
    assert panel.seen[-1] == ("enter", "talk")


def test_a_wide_step_grows_the_panel_and_the_window_never_moves(panel):
    from ui.onboarding_shape import NARROW, WIDE
    where = panel.geometry()
    assert panel.panel_rect().width() == NARROW[0]
    for _ in range(2):
        panel.step().done()
        panel.next()
    assert panel.at == 2 and panel.panel_rect().width() == WIDE[0]
    assert panel.geometry() == where


def test_the_last_continue_completes(panel):
    got = []
    panel.completed.connect(lambda: got.append(1))
    for _ in range(3):
        panel.step().done()
        panel.next()
    assert got == [1]


def test_it_takes_its_colours_from_the_tokens():
    from tests.agents.test_e2e_design_tokens import ROOT, _code_hexes
    assert not _code_hexes(ROOT / "ui/onboarding_shape.py")
