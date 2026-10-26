"""Routines that report missed runs (WX-10, the Massive Direction Plan).

A daily routine whose 08:00 passed while the PC was asleep runs once when
it wakes - not once per missed day - and the job says it ran late, on the
jobs page and in its timeline.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from agents.core import EventBus, EventKind, Task, TaskStatus
from agents.manager.scheduler import ScheduledJob, Scheduler
from agents.skills.schedule import Schedule


class _Manager:
    def __init__(self):
        self.events, self.tasks = EventBus(), {}

    def submit_task(self, task, agent=None):
        self.tasks[task.id] = task
        return task

    def get_task(self, tid):
        return self.tasks.get(tid)


def _job():
    return ScheduledJob("chase", "Chase invoices", Schedule(every="daily", at="08:00"),
                        lambda extra: (Task(title="Chase invoices", goal="chase", workspace="."), None))


def test_a_routine_missed_while_asleep_runs_once_and_says_it_was_late(tmp_path):
    mgr = _Manager()
    logs = []
    mgr.events.subscribe(lambda ev: logs.append(ev.message) if ev.kind is EventKind.LOG else None)
    created = datetime(2026, 10, 20, 7, 0).timestamp()
    sched = Scheduler(mgr, state_path=str(tmp_path / "s.json"), clock=lambda: created)
    sched.add(_job())
    woke = datetime(2026, 10, 22, 9, 40).timestamp()              # asleep through two 08:00s
    fired = sched.tick(now=woke)
    assert len(fired) == 1, "once, not once per missed day"
    late = fired[0].metadata["ran_late"]
    assert late["due"] == "2026-10-22T08:00" and late["late_by_s"] == 100 * 60
    assert late["words"] == "Ran late - it was due at 08:00; the PC was asleep or off"
    assert any("Ran late - it was due at 08:00" in m for m in logs)
    fired[0].status = TaskStatus.COMPLETED
    assert sched.tick(now=woke + 60) == [], "and not again"


def test_on_time_is_not_late(tmp_path):
    mgr = _Manager()
    created = datetime(2026, 10, 20, 7, 0).timestamp()
    sched = Scheduler(mgr, state_path=str(tmp_path / "s.json"), clock=lambda: created)
    sched.add(_job())
    [task] = sched.tick(now=datetime(2026, 10, 20, 8, 0, 30).timestamp())
    assert "ran_late" not in task.metadata


def test_the_jobs_page_says_it(monkeypatch):
    from ui.app_bridge import AppBridge
    t = Task(title="Chase invoices", goal="chase", workspace=".")
    t.metadata["ran_late"] = {"words": "Ran late - it was due at 08:00; the PC was asleep or off"}
    br = AppBridge.__new__(AppBridge)
    br.workforce = lambda: SimpleNamespace(list_tasks=lambda **kw: [t])
    assert br.tasks()[0]["late"].startswith("Ran late")
