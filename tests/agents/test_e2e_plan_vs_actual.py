"""Plan versus actual (OR-09, the Massive Direction Plan).

Every plan a job had is kept on it, apart from what really ran under each;
a replan says in the job's timeline what failed to cause it.
"""
from __future__ import annotations

from agents.core import EventKind, Task
from tests.agents.test_manager import ListPlanner, make_manager


def test_the_plans_and_what_ran_are_kept_apart(tmp_path):
    planner = ListPlanner([("Fetch data", [("boom", {})]), ("Write report", [])])
    mgr = make_manager(tmp_path, planner=planner)
    events = []
    mgr.events.subscribe(lambda ev: events.append(ev))
    try:
        task = mgr.wait(mgr.submit_task(Task(title="t", goal="g", workspace=str(tmp_path))).id, 10)
    finally:
        mgr.stop()
    plans = task.metadata["plans"]
    assert [p["version"] for p in plans] == [1, 2] and plans[1]["revised"]
    assert plans[0]["steps"] == ["Fetch data", "Write report"]
    assert "'Fetch data' failed: boom" in plans[1]["reason"]
    ran = task.metadata["ran"]
    assert ran[0] == {**ran[0], "step": "Fetch data", "status": "failed", "plan": 1}
    assert all(r["plan"] == 2 for r in ran[1:]) and any(r["status"] == "done" for r in ran[1:])
    revised = [e for e in events if e.kind is EventKind.PLAN_CREATED and e.data.get("revised")]
    assert revised and revised[0].message.startswith("Revised plan after 'Fetch data' failed: boom")
