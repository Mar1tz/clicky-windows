"""A fair queue with caps (OR-10, the Massive Direction Plan).

One job per folder (one writer per artifact), one browser session and one
job driving the screen at a time; and a job waiting for one of those never
holds up a quick job queued behind it.
"""
from __future__ import annotations

import time

from agents.core import Task
from tests.agents.test_manager import ListPlanner, SlowWorker, make_manager


def _job(tmp_path, folder, worker="coding"):
    (tmp_path / folder).mkdir(exist_ok=True)
    return Task(title=folder, goal="g", workspace=str(tmp_path / folder), worker_type=worker)


def test_one_writer_per_folder_and_the_queue_skips_what_must_wait(tmp_path):
    mgr = make_manager(tmp_path, worker=SlowWorker(), planner=ListPlanner([("Work", [])]), max_concurrent=3)
    try:
        a = mgr.submit_task(_job(tmp_path, "site"))
        b = mgr.submit_task(Task(title="site again", goal="g", workspace=str(tmp_path / "site")))
        c = mgr.submit_task(_job(tmp_path, "notes"))
        time.sleep(0.15)
        running = {t.title for t in mgr.list_tasks() if t.status.value in ("planning", "running", "verifying")}
        assert "site" in running and "notes" in running, "a free slot is used by a job that can run"
        assert "site again" not in running, "two jobs never write the same folder at once"
        for t in (a, b, c):
            mgr.wait(t.id, 15)
        assert all(mgr.get_task(t.id).status.value == "completed" for t in (a, b, c))
    finally:
        mgr.stop()


def test_one_browser_session_at_a_time(tmp_path):
    mgr = make_manager(tmp_path, worker=SlowWorker(), planner=ListPlanner([("Work", [])]), max_concurrent=3)
    try:
        a = mgr.submit_task(_job(tmp_path, "flights", "browser"))
        b = mgr.submit_task(_job(tmp_path, "hotels", "browser"))
        time.sleep(0.15)
        assert mgr.get_task(b.id).status.value == "pending", "the second waits for the browser"
        assert mgr._busy_claims(mgr.get_task(b.id)) == ["browser"]
        mgr.wait(a.id, 15)
        mgr.wait(b.id, 15)
        assert mgr.get_task(b.id).status.value == "completed"
        assert mgr._held == {}, "and everything is let go"
    finally:
        mgr.stop()


def test_jobs_in_the_shared_projects_folder_still_run_side_by_side(tmp_path):
    """Voice jobs work in the projects folder, each in its own subfolder."""
    mgr = make_manager(tmp_path, worker=SlowWorker(), planner=ListPlanner([("Work", [])]), max_concurrent=3)
    try:
        a = Task(title="cafe site", goal="g", workspace=str(tmp_path), metadata={"subdir": "Cafe"})
        b = Task(title="bakery site", goal="g", workspace=str(tmp_path), metadata={"subdir": "Bakery"})
        c = Task(title="no folder named", goal="g", workspace=str(tmp_path))
        for t in (a, b, c):
            mgr.submit_task(t)
        time.sleep(0.15)
        assert not any(mgr._busy_claims(t) for t in (a, b, c))
        assert sum(t.status.value in ("planning", "running", "verifying") for t in mgr.list_tasks()) == 3
    finally:
        mgr.stop()
