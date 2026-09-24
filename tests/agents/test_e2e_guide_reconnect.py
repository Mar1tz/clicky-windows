"""GW-15: a walkthrough survives a reconnect.

Measured before anything was built - live, on a blank session after a
failed resume, with the recap and the real per-turn context: the model
already knew the goal, the current step and how many were left (that
context rides on every question), and it restarted a running walkthrough
0 times in 12. What it did not know was what was BEHIND the user. Steps
are spoken in Tora's own voice, not the live model's, so they are in no
conversation history - resumed session or not. Asked "what have I done so
far?" on a plan nobody could guess, it named the step actually taken 0
times in 6 and made history up instead ("added your logo"). With the steps
behind in the context: 6 in 6.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from ai.gemini_planner import Guide, Step

INVOICE = ["Click the Tools menu.", "Choose Layout presets.",
           "Pick Classic B from the list.", "Click Apply."]


def _manager(instructions, at, monkeypatch, *, epoch=1):
    from ai import screen_summary
    from companion_manager import CompanionManager

    monkeypatch.setattr(screen_summary, "describe",
                        lambda **kw: SimpleNamespace(names=[], pointables=[],
                                                     line='Screen: "Invoicer".'))
    plan = Guide(goal="set up the invoice template",
                 steps=[Step(instruction=t, target=f"T{i}") for i, t in enumerate(instructions)])
    mgr = CompanionManager.__new__(CompanionManager)
    mgr._guide_runner = SimpleNamespace(running=True, index=at)
    mgr._lesson_plan = plan
    mgr._lesson_step_idx = at
    mgr._lesson_steps = list(instructions)
    mgr._resume = None
    mgr._privacy_guard = None
    mgr._ref_fresh = lambda: False
    mgr._last_ref = None
    mgr._pending_confirmation_line = lambda: ""
    mgr._attached_docs = []
    mgr.agent_context_line = lambda: ""
    mgr._realtime = SimpleNamespace(epoch=epoch)
    mgr._ctx_memo = (-1, "", 0)
    return mgr


def _ask(mgr) -> str:
    """The context that goes out with the next question."""
    return asyncio.run(mgr._screen_context())


def test_the_steps_behind_them_ride_with_every_question(monkeypatch):
    text = _ask(_manager(INVOICE, 2, monkeypatch))
    assert ('Steps already behind them (done or skipped): 1. "Click the Tools '
            'menu."; 2. "Choose Layout presets."') in text
    assert 'The current step is: "Pick Classic B from the list."' in text


def test_on_step_one_nothing_is_behind(monkeypatch):
    text = _ask(_manager(INVOICE, 0, monkeypatch))
    assert "already behind" not in text
    assert 'The current step is: "Click the Tools menu."' in text


def test_a_long_walkthrough_names_only_the_last_three_behind(monkeypatch):
    steps = [f"Do thing {i + 1}." for i in range(7)]
    text = _ask(_manager(steps, 5, monkeypatch))
    assert '3. "Do thing 3."; 4. "Do thing 4."; 5. "Do thing 5." (and 2 before those)' in text
    assert '"Do thing 1."' not in text


def test_the_walkthrough_is_never_folded_into_same_screen(monkeypatch):
    """The screen line is shortened to "same screen" on repeat turns. The
    walkthrough must not be: it is how a session that starts blank learns
    one is running, on its very first question."""
    mgr = _manager(INVOICE, 3, monkeypatch)
    first, second = _ask(mgr), _ask(mgr)
    assert "Same screen as a moment ago" in second
    for text in (first, second):
        assert 'toward: "set up the invoice template"' in text
        assert "step 4 of 4" in text and '3. "Pick Classic B from the list."' in text


def test_a_blank_session_hears_everything_on_its_first_question(monkeypatch):
    mgr = _manager(INVOICE, 3, monkeypatch, epoch=1)
    _ask(mgr)
    _ask(mgr)                              # the screen line is now "same screen"
    mgr._realtime.epoch = 2                # resume refused: a blank session
    text = _ask(mgr)
    assert 'Screen: "Invoicer".' in text, "the screen, in full"
    assert 'toward: "set up the invoice template"' in text, "the goal"
    assert 'The current step is: "Click Apply."' in text, "where they are"
    assert '3. "Pick Classic B from the list."' in text, "and what is behind them"


def test_losing_the_session_leaves_the_walkthrough_running():
    """The runner does not depend on the live session: the steps are Tora's
    own voice. Losing the session reconnects in the background and touches
    nothing else."""
    from companion_manager import AppState, CompanionManager
    from tutor_features.guide import GuideRunner

    async def nothing(*_):
        return None

    plan = Guide(goal="g", steps=[Step(instruction=t) for t in INVOICE])
    runner = GuideRunner(plan, speak_step=nothing, check=nothing, say=nothing,
                         is_busy=lambda: False, on_stop=lambda: None)
    runner._idx = 2
    runner._task = SimpleNamespace(done=lambda: False)
    mgr = CompanionManager.__new__(CompanionManager)
    mgr._guide_runner = runner
    mgr._lesson_plan = plan
    mgr._state = AppState.IDLE
    submitted = []
    mgr._submit = submitted.append

    mgr._on_realtime_lost("closed")

    assert mgr._guide_runner is runner and runner.running and runner.index == 2
    assert runner._pending is None, "no command was sent to the walkthrough"
    assert mgr._lesson_plan is plan
    assert [c.__name__ for c in submitted] == ["_realtime_reconnect"]
    for coro in submitted:
        coro.close()
