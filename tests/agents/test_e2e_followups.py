"""Follow-ups find their job (OR-07, the Massive Direction Plan).

"Make that longer" and "continue the website" attach to the right job, and
the same request said again while it is being built never starts a second.
"""
from __future__ import annotations

import time

import pytest

from agents.core import Task, TaskStatus
from agents.manager.followups import match, revision_goal

NOW = time.time()


def _job(title, goal, status=TaskStatus.RUNNING, age=60.0):
    t = Task(title=title, goal=goal, workspace=f"C:/jobs/{title.split()[0].lower()}")
    t.status = status
    t.created_at = t.updated_at = NOW - age
    return t


SITE = _job("Portfolio website", "build me a portfolio website with a gallery")
ESSAY = _job("Essay on rivers", "write a 500 word essay on rivers", TaskStatus.COMPLETED, age=300)
OLD = _job("Budget sheet", "make a budget spreadsheet", TaskStatus.COMPLETED, age=7200)


@pytest.mark.parametrize("asked,job,how", [
    ("build me a portfolio website with a gallery", SITE, "duplicate"),
    ("continue the website", SITE, "continue"),
    ("finish the portfolio", SITE, "continue"),
    ("make the essay longer", ESSAY, "revise"),
    ("make it shorter", SITE, "revise"),                 # the latest job
    ("add a conclusion to the essay about rivers", ESSAY, "revise"),
    ("write a poem about the sea", None, ""),            # something new
    ("write a new essay on lakes", None, ""),            # new, though it shares a word
    ("update the budget spreadsheet", None, ""),         # two hours ago: not a follow-up
])
def test_a_request_finds_its_job(asked, job, how):
    got, kind = match(asked, [SITE, ESSAY, OLD], now=NOW)
    assert (got, kind) == (job, how)


def test_a_revision_works_on_what_is_there():
    goal = revision_goal("make the essay longer", ESSAY)
    assert "Essay on rivers" in goal and "do not start again" in goal


def test_the_voice_tool_does_not_start_a_duplicate(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    import companion_manager as cm
    started = []
    agents = SimpleNamespace(list_tasks=lambda **kw: [SITE], submit_task=lambda t: started.append(t),
                             get_agent=lambda i: None, resume=lambda i: True, skills=None,
                             workspace_root="C:/jobs")
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    mgr._agents = agents
    out = asyncio.run(mgr._tool_delegate_task({"goal": "build me a portfolio website with a gallery"}))
    assert not started and out["already_running"] == SITE.id
