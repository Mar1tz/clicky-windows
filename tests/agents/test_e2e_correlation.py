"""Correlation IDs (EN-18, the Massive Direction Plan).

One ID follows a job: its events carry it, its tool calls are emitted
under it, and every log line written while it runs - by the orchestrator,
the registry, a tool - starts "[job <id>]". Lines outside a job do not.
"""
from __future__ import annotations

import logging

from agents.core import EventKind, Task
from tests.agents.test_manager import make_manager


def test_one_id_follows_a_job_through_events_and_log_lines(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    mgr = make_manager(tmp_path)
    events = []
    mgr.events.subscribe(lambda ev: events.append(ev))
    try:
        task = mgr.wait(mgr.submit_task(Task(title="t", goal="g", workspace=str(tmp_path))).id, 10)
    finally:
        mgr.stop()
    mine = [e for e in events if e.task_id == task.id]
    assert {EventKind.TOOL_RESULT, EventKind.TASK_STATUS} <= {e.kind for e in mine}
    lines = [r.getMessage() for r in caplog.records if r.name.startswith("agents.")]
    in_job = [l for l in lines if l.startswith(f"[job {task.id}]")]
    assert any("plan ready" in l for l in in_job), lines
    logging.getLogger("agents.test").info("outside any job")
    assert caplog.records[-1].getMessage() == "outside any job"
