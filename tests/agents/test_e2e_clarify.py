"""Clarify only when it matters (OR-05, the Massive Direction Plan).

Only the person, file, amount or account of an action that sends, pays,
deletes or posts is worth a question - asked once, together.
"""
from __future__ import annotations

import pytest

from ai.clarify import missing, question


@pytest.mark.parametrize("text,slots", [
    ("send the report", ["recipient"]),
    ("send the report to Sam", []),
    ("email it to sam@example.com", []),
    ("pay the invoice", ["amount", "recipient"]),
    ("pay Sam 40 dollars", []),
    ("transfer £200 to my savings", []),
    ("delete it", ["file"]),
    ("delete the old draft file", []),
    ("delete notes.txt", []),
    ("post this", ["account"]),
    ("post this on LinkedIn", []),
    ("what's the weather", []),
    ("summarise this page", []),
])
def test_only_what_would_change_the_outcome_is_asked(text, slots):
    assert missing(text) == slots


def test_a_file_already_known_is_not_asked_for():
    assert missing("delete it", has_referent=True) == []


def test_one_bundled_question():
    assert question(["amount", "recipient"]) == "Before I do that: how much and who should it go to?"
    assert question(["recipient", "file", "account"]) == \
        "Before I do that: who should it go to, which file and on which account?"
    assert question([]) == ""


def test_a_job_that_would_send_to_nobody_asks_first():
    import asyncio
    from types import SimpleNamespace

    import companion_manager as cm
    started = []
    agents = SimpleNamespace(list_tasks=lambda **kw: [], submit_task=lambda t: started.append(t),
                             get_agent=lambda i: None, resume=lambda i: True, skills=None,
                             workspace_root="C:/jobs")
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    mgr._agents, mgr._trace_readings, mgr._last_ref = agents, [], None
    out = asyncio.run(mgr._tool_delegate_task({"goal": "write up the minutes and email them"}))
    assert not started and "who should it go to?" in out["error"]


def test_the_voice_is_told_to_ask_once():
    import config
    assert "ASK ONCE, AND ONLY WHEN IT MATTERS" in config._REALTIME_RULES
