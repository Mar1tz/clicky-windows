"""OR-33: a passing failure is tried again at the tool, backing off - only
for tools that may run again; jobs of a kind wait their turn in a named
queue (four on the internet, two building) instead of all running at once.
"""
from __future__ import annotations

import asyncio

from agents.core import RetryPolicy, RiskLevel, Task, ToolCall, ToolContext
from agents.tools import Registry, make_spec
from agents.tools.base import Tool


class _Busy(Tool):
    """Answers 503 twice, then works."""
    spec = make_spec("busy_read", "Read from a busy server.", risk=RiskLevel.SAFE, category="test")

    def __init__(self, fails=2):
        self.tries, self.fails = 0, fails

    async def run(self, args, ctx):
        self.tries += 1
        if self.tries <= self.fails:
            return self.fail("503 Service Unavailable")
        return self.ok("the page")


class _BusySend(_Busy):
    spec = make_spec("busy_send", "Send to a busy server.", risk=RiskLevel.HIGH, category="test")


def test_policies_follow_replay_safety():
    assert _Busy.spec.retry_policy().attempts == 2
    assert _BusySend.spec.retry_policy().attempts == 1, "never repeated blindly"
    p = RetryPolicy(attempts=3)
    assert p.retries("failed", "429 Too Many Requests") and p.retries("timeout", "")
    assert not p.retries("invalid_input", "503") and not p.retries("failed", "file not found")


def _run(tool, monkeypatch):
    from agents.tools import registry
    monkeypatch.setattr(registry, "RETRY_PAUSE_S", 0)
    ctx = ToolContext(workspace=".")
    ctx.policy.max_auto_risk = RiskLevel.HIGH
    return asyncio.run(Registry([tool]).execute(ToolCall(tool.spec.name, {}), ctx))


def test_a_busy_server_is_tried_again_with_backoff(monkeypatch):
    tool = _Busy(fails=1)
    result = _run(tool, monkeypatch)
    assert result.ok and tool.tries == 2 and result.data["attempts"] == 2
    tool = _Busy(fails=5)
    tool.spec = make_spec("busy_read", "Read.", risk=RiskLevel.SAFE, category="test")
    tool.spec.retry = RetryPolicy(attempts=4)
    result = _run(tool, monkeypatch)
    assert not result.ok and tool.tries == 4, "its own policy: four tries, then the step decides"


def test_a_send_is_never_repeated(monkeypatch):
    tool = _BusySend(fails=1)
    result = _run(tool, monkeypatch)
    assert not result.ok and tool.tries == 1


def test_jobs_wait_their_turn_in_a_named_queue(tmp_path):
    from agents.bootstrap import build_agent_manager
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    builds = [Task(title=f"b{i}", goal="g", workspace=str(tmp_path), worker_type="terminal") for i in range(3)]
    for t in builds[:2]:
        assert not mgr._busy_claims(t)
        mgr._take_claims(t)
    assert mgr._busy_claims(builds[2]) == ["queue:cpu"], "two heavy runs at once"
    coding = Task(title="c", goal="g", workspace=str(tmp_path))
    assert not mgr._busy_claims(coding), "coding jobs are not queued"
    assert "two at a time" in mgr._claim_words("queue:cpu")
    research = Task(title="r", goal="g", workspace=str(tmp_path), worker_type="research")
    assert not mgr._busy_claims(research), "another queue is not held up"
    mgr._release_claims(builds[0].id)
    assert not mgr._busy_claims(builds[2])


def test_the_same_job_twice_is_one_job(tmp_path):
    """After trigger.dev's idempotency keys: the same key while its job runs,
    or within its window, returns that job; after the window it is new."""
    from agents.bootstrap import build_agent_manager
    from agents.core import now
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True).start()
    try:
        a = mgr.submit_task(Task(title="a", goal="make a folder called Keys", workspace=str(tmp_path),
                                 metadata={"idempotency_key": "voice:k1", "idempotency_ttl_s": 30.0}))
        b = mgr.submit_task(Task(title="a", goal="make a folder called Keys", workspace=str(tmp_path),
                                 metadata={"idempotency_key": "voice:k1", "idempotency_ttl_s": 30.0}))
        assert b.id == a.id
        mgr.wait(a.id, 30)
        a.created_at = now() - 60
        c = mgr.submit_task(Task(title="a", goal="make a folder called Keys", workspace=str(tmp_path),
                                 metadata={"idempotency_key": "voice:k1", "idempotency_ttl_s": 30.0}))
        assert c.id != a.id, "asked again after it finished, outside the window: a new job"
    finally:
        mgr.stop()


def test_a_slot_fired_twice_starts_one_job(tmp_path):
    from datetime import datetime
    from agents.manager.scheduler import ScheduledJob, Scheduler
    from agents.skills.schedule import Schedule
    from agents.bootstrap import build_agent_manager
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True).start()
    try:
        sched = Scheduler(mgr, state_path=str(tmp_path / "s.json"))
        job = ScheduledJob("j", "J", Schedule(every="daily", at="08:00"),
                           lambda extra: (Task(title="J", goal="make a folder called Slot", workspace=str(tmp_path)),
                                          None))
        slot = {"slot": datetime(2026, 10, 27, 8, 0).isoformat(timespec="minutes")}
        first, second = sched.fire(job, extra=slot), sched.fire(job, extra=slot)
        assert first.id == second.id
        mgr.wait(first.id, 30)
    finally:
        mgr.stop()
