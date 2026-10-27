"""OR-32 by voice: a job's question is said aloud, and the answer goes back.

A job that calls ask_user makes a waitpoint; Tora says the question when
she is free, the model's context says a job is waiting for an answer, and
manage_agents(action="answer") hands the person's words to the job.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import companion_manager as cm
from agents.core import Event, EventBus, EventKind, WaitKind
from agents.manager.waitpoints import Waitpoints


def _companion():
    hub = Waitpoints(EventBus())
    task = SimpleNamespace(title="Invoice run", metadata={}, agent_id="", status=None)
    mgr = SimpleNamespace(waitpoints=hub, get_task=lambda tid: task, list_tasks=lambda: [],
                          list_agents=lambda active_only=False: [], pending_approvals=lambda: [],
                          answer=lambda text, wid="": hub.answer(text, wid), skills=None)
    c = cm.CompanionManager.__new__(cm.CompanionManager)
    said, notices = [], []
    c._agents = mgr
    c._say_when_idle = said.append
    c.sig_agent_notice = SimpleNamespace(emit=lambda *a: notices.append(a))
    return c, hub, said, notices


def test_the_question_is_said_and_the_answer_goes_back():
    c, hub, said, notices = _companion()
    wp = hub.create(WaitKind.ANSWER, "t1", prompt="Which month should I invoice?")
    c._on_waitpoint(Event(kind=EventKind.WAITPOINT, message="", data={"waitpoint": wp.to_dict()}))
    assert said == ["A quick question for Invoice run: Which month should I invoice?"] and notices
    assert "waiting for their answer to: Which month should I invoice?" in c.agent_context_line()
    out = asyncio.run(c._tool_manage_agents({"action": "answer", "answer": "October"}))
    assert out["ok"] and wp.output == "October" and hub.pending() == []
    again = asyncio.run(c._tool_manage_agents({"action": "answer", "answer": "x"}))
    assert "no job is waiting" in again["error"]


def test_time_waits_are_not_spoken():
    c, hub, said, _ = _companion()
    wp = hub.create(WaitKind.TIME, "t1", prompt="until 09:00", until=0)
    c._on_waitpoint(Event(kind=EventKind.WAITPOINT, message="", data={"waitpoint": wp.to_dict()}))
    assert said == []
