"""AG-10: a job waiting on an approval is said once more, not left to sit.

8 of 21 jobs were cancelled, some after waiting on an approval nobody heard
about: Tora was busy when it came, so it was only ever a toast.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import companion_manager as cm
from agents.core import TaskStatus


def _hub(status, state=cm.AppState.IDLE):
    m = cm.CompanionManager.__new__(cm.CompanionManager)
    m.APPROVAL_REMIND_S, m.REMIND_QUIET_WAIT_S = 0.05, 0.3
    m._state = state
    task = SimpleNamespace(title="Create poster", status=status)
    m._agents = SimpleNamespace(get_task=lambda _id: task)
    m.notices, m.said = [], []
    m.sig_agent_notice = SimpleNamespace(emit=lambda title, body: m.notices.append(title))
    m._say_when_idle = lambda words: m.said.append(words)
    return m, task


def test_still_waiting_after_the_wait_is_said_again():
    m, _task = _hub(TaskStatus.WAITING_APPROVAL)
    asyncio.run(m._remind_approval("t", "The design agent still needs your approval to render design."))
    assert m.notices == ["Still waiting"] and m.said and "still needs your approval" in m.said[0]


def test_an_answered_approval_is_not_brought_up():
    m, _task = _hub(TaskStatus.RUNNING)
    asyncio.run(m._remind_approval("t", "x"))
    assert m.notices == [] and m.said == []


def test_the_reminder_waits_for_a_quiet_moment():
    m, task = _hub(TaskStatus.WAITING_APPROVAL, state=cm.AppState.SPEAKING)

    async def go():
        reminder = asyncio.ensure_future(m._remind_approval("t", "x"))
        await asyncio.sleep(0.12)
        assert m.said == [], "not over her own answer"
        m._state = cm.AppState.IDLE
        await reminder
    m.REMIND_QUIET_WAIT_S = 30
    import companion_manager as _cm

    async def fast_sleep(s, _real=asyncio.sleep):
        await _real(min(s, 0.02))
    _cm.asyncio.sleep, real = fast_sleep, asyncio.sleep
    try:
        asyncio.run(go())
    finally:
        _cm.asyncio.sleep = real
    assert m.said == ["x"]
