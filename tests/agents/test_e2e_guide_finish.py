"""GW-16: finishing is an event.

Before this, a finished walkthrough ended on one spoken line and vanished:
nothing on screen, nothing kept, and the live model - which never hears
the steps, they are Tora's own voice - did not know one had just ended.
Now it closes with a line, leaves a one-line summary on screen, and is kept
in the journal with a plan that can be replayed.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from ai.gemini_planner import Guide, Step, guide_from_dict, guide_to_dict
from ai.verification import Expectation
from tutor_features import journal
from tutor_features.guide import GuideRunner


@pytest.fixture
def journal_db(tmp_path, monkeypatch):
    path = tmp_path / "journal.db"
    monkeypatch.setattr(journal, "_db_path", lambda: path)
    return path


def _plan():
    return Guide(goal="Set up the invoice template", steps=[
        Step(instruction="Click the Tools menu.", target="Tools",
             possible_actions=["Press Alt+T"]),
        Step(instruction="Pick Classic B from the list.", target="Classic B",
             expected=Expectation(control_appears="Classic B selected")),
        Step(instruction="Wait for the preview to load.", kind="wait"),
    ])


# ── the plan can be kept and brought back ────────────────────────────────────

def test_a_plan_comes_back_exactly_as_it_was_saved():
    plan = _plan()
    back = guide_from_dict(guide_to_dict(plan))
    assert guide_to_dict(back) == guide_to_dict(plan)
    assert [s.target for s in back.steps] == ["Tools", "Classic B", ""]
    assert back.steps[1].expected.control_appears == "Classic B selected"
    assert back.steps[2].waiting and back.steps[0].possible_actions == ["Press Alt+T"]


def test_the_journal_keeps_a_walkthrough_as_an_entry_and_a_plan(journal_db):
    plan = _plan()
    entry = journal.log_walkthrough(plan.goal, [s.instruction for s in plan.steps],
                                    plan=guide_to_dict(plan), app_key="invoicer",
                                    seconds=102.0)
    row = [e for e in journal.entries_all() if e["id"] == entry][0]
    assert row["question"] == "How do I set up the invoice template?"
    assert row["answer"].splitlines() == ["1. Click the Tools menu.",
                                          "2. Pick Classic B from the list.",
                                          "3. Wait for the preview to load."]
    assert row["tags"] == "walkthrough" and row["next_review_at"], \
        "it comes back for review like anything else Tora taught"
    replay = guide_from_dict(journal.walkthrough_plan(entry))
    assert [s.instruction for s in replay.steps] == [s.instruction for s in plan.steps]


def test_a_goal_already_phrased_as_a_question_is_not_asked_twice(journal_db):
    entry = journal.log_walkthrough("how do I export the video", ["Click Export."])
    row = [e for e in journal.entries_all() if e["id"] == entry][0]
    assert row["question"] == "How do I export the video?"


def test_what_did_i_learn_today_says_which_entries_are_walkthroughs(journal_db):
    journal.log_walkthrough("Set up the invoice template", ["Click Tools."])
    journal.log_qa("What is a pivot table?", "A summary table.")
    text = journal.summarise(journal.entries_today())
    assert "How do I set up the invoice template? (walkthrough)" in text
    assert "What is a pivot table?" in text and "pivot table? (walkthrough)" not in text


# ── the summary line ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("steps, secs, line", [
    (4, 102, "Set up the invoice template · 4 steps · 1 min 42 s"),
    (1, 42, "Set up the invoice template · 1 step · 42 s"),
    (3, 120, "Set up the invoice template · 3 steps · 2 min"),
    (9, 3900, "Set up the invoice template · 9 steps · 1 h 5 min"),
])
def test_the_summary_line(steps, secs, line):
    from companion_manager import _walkthrough_summary
    assert _walkthrough_summary("set up the invoice template.", steps, secs) == line


# ── the ending itself ────────────────────────────────────────────────────────

def _manager(monkeypatch, *, journal_on=True):
    import companion_manager as cm

    monkeypatch.setattr(cm, "active_window_title", lambda: "Invoicer - Templates")
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    mgr._lesson_plan = _plan()
    mgr._guide_stats = {"started": time.monotonic() - 102}
    mgr._guide_last = None
    mgr._journal_enabled = journal_on
    mgr.replies = []

    async def reply_local(msg, *, shown=""):
        mgr.replies.append((msg, shown))

    mgr._reply_local = reply_local
    return mgr


def test_finishing_says_done_shows_the_summary_and_keeps_it(monkeypatch, journal_db):
    mgr = _manager(monkeypatch)
    asyncio.run(mgr._guide_finished(False))
    (spoken, shown), = mgr.replies
    assert spoken == "That's it - all done! I've saved the steps to your journal."
    assert shown.splitlines() == [spoken,
                                  "✓ Set up the invoice template · 3 steps · 1 min 42 s"]
    saved = [e for e in journal.entries_all() if e["tags"] == "walkthrough"]
    assert len(saved) == 1 and saved[0]["app_key"], "kept, against the app it was for"
    assert journal.walkthrough_plan(saved[0]["id"])["goal"] == "Set up the invoice template"


def test_getting_there_ahead_is_also_a_finish(monkeypatch, journal_db):
    mgr = _manager(monkeypatch)
    asyncio.run(mgr._guide_finished(True))
    assert mgr.replies[0][0].startswith("Looks like you're already there")
    assert len(journal.entries_all()) == 1


def test_with_the_journal_off_nothing_is_kept_or_promised(monkeypatch, journal_db):
    mgr = _manager(monkeypatch, journal_on=False)
    asyncio.run(mgr._guide_finished(False))
    assert mgr.replies[0][0] == "That's it - all done!"
    assert journal.entries_all() == []


def test_a_journal_that_cannot_be_written_does_not_spoil_the_ending(monkeypatch):
    mgr = _manager(monkeypatch)

    def broken(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(journal, "log_walkthrough", broken)
    asyncio.run(mgr._guide_finished(False))
    assert mgr.replies[0][0] == "That's it - all done!", "no promise it was saved"


# ── the runner hands its ending over ─────────────────────────────────────────

def _run_to_the_end(on_finish=None):
    said, calls = [], {"n": 0}

    async def speak(idx):
        pass

    async def check(step):
        calls["n"] += 1
        return calls["n"] > 1                 # false at the start, then done

    async def say(text):
        said.append(text)

    ended = []
    runner = GuideRunner(Guide(goal="g", steps=[Step(instruction="Click A.")]),
                         speak_step=speak, check=check, say=say,
                         is_busy=lambda: False, on_stop=lambda: None,
                         on_event=lambda k, d: ended.append(d) if k == "ended" else None,
                         on_finish=on_finish, poll_s=0.01, present_delay_s=0.0)

    async def scenario():
        await runner.start()
        for _ in range(300):
            if not runner.running:
                break
            await asyncio.sleep(0.01)

    asyncio.run(scenario())
    return runner, said, ended


def test_the_runner_hands_the_ending_to_on_finish():
    finished = []

    async def on_finish(ahead):
        finished.append(ahead)

    runner, said, ended = _run_to_the_end(on_finish)
    assert finished == [False] and said == [], "one closing, the app's"
    assert runner.completed and ended[0]["reason"] == "finished"


def test_without_on_finish_the_plain_line_is_kept():
    _, said, _ = _run_to_the_end()
    assert said == ["That's the last step — you're all done!"]


def test_a_failing_on_finish_still_ends_with_a_line():
    async def on_finish(ahead):
        raise RuntimeError("boom")

    runner, said, _ = _run_to_the_end(on_finish)
    assert said == ["That's the last step — you're all done!"] and runner.completed


# ── the model is told it just finished ───────────────────────────────────────

def _context_after(monkeypatch, finished_ago: float, saved=True) -> str:
    from ai import screen_summary
    from companion_manager import CompanionManager

    monkeypatch.setattr(screen_summary, "describe",
                        lambda **kw: SimpleNamespace(names=[], pointables=[], line="x"))
    mgr = CompanionManager.__new__(CompanionManager)
    mgr._guide_runner = None
    mgr._resume = None
    mgr._lesson_steps = []
    mgr._guide_last = (_plan(), time.monotonic() - finished_ago, saved)
    mgr._privacy_guard = None
    mgr._context_head = lambda line: "[context]"
    mgr._ref_fresh = lambda: False
    mgr._last_ref = None
    mgr._pending_confirmation_line = lambda: ""
    mgr._attached_docs = []
    mgr.agent_context_line = lambda: ""
    return asyncio.run(mgr._screen_context())


def test_right_after_finishing_the_model_knows_what_was_done(monkeypatch):
    text = _context_after(monkeypatch, 30)
    assert 'A walkthrough toward "Set up the invoice template" just finished' in text
    assert '2. "Pick Classic B from the list."' in text
    assert "steps are saved in their Tora journal" in text and "do not call guide_step" in text


def test_a_while_later_it_is_no_longer_mentioned(monkeypatch):
    from companion_manager import FINISHED_CONTEXT_S
    text = _context_after(monkeypatch, FINISHED_CONTEXT_S + 1)
    assert "just finished" not in text
