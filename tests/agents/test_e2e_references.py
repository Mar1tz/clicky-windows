"""What "it" means (OR-06, the Massive Direction Plan).

Explicit name, then the last thing said, then the active job, then a
recent object, else ask - each answer with the evidence that decided it.
"""
from __future__ import annotations

from ai.references import Thing, resolve

NOW = 1_000_000.0
SAVE = Thing("control", "Save button", at=NOW - 20)
JOB = Thing("job", "Budget sheet", "job-1", at=NOW - 300)
TRACE = Thing("trace", "Q3 revenue chart", at=NOW - 60)
OLD_FILE = Thing("file", "notes.txt", "C:/w/notes.txt", at=NOW - 5000)


def test_a_name_beats_everything():
    r = resolve("summarise the revenue chart", last_said=SAVE, active_job=JOB, recent=(TRACE,), now=NOW)
    assert r.thing is TRACE and r.link == "name" and "names 'Q3 revenue chart'" in r.evidence


def test_then_the_last_thing_said():
    r = resolve("click it", last_said=SAVE, active_job=JOB, recent=(TRACE,), now=NOW)
    assert r.thing is SAVE and r.link == "last_said" and "20s ago" in r.evidence


def test_then_the_active_job():
    stale = Thing("control", "Save button", at=NOW - 600)
    r = resolve("make it longer", last_said=stale, active_job=JOB, recent=(TRACE,), now=NOW)
    assert r.thing is JOB and r.link == "active_job"


def test_then_something_recent():
    r = resolve("summarise this", recent=(OLD_FILE, TRACE), now=NOW)
    assert r.thing is TRACE and r.link == "recent"


def test_else_ask():
    r = resolve("send that", recent=(OLD_FILE,), now=NOW)
    assert r.thing is None and r.link == "ask"


def test_no_reference_no_resolution():
    assert resolve("what's the weather", last_said=SAVE, now=NOW).link == "none"


def _voice(monkeypatch):
    import asyncio
    import time
    from types import SimpleNamespace

    import companion_manager as cm
    started = []

    def submit(task):
        started.append(task)
        task.agent_id = "a"
        return task
    agents = SimpleNamespace(list_tasks=lambda **kw: [], submit_task=submit, get_agent=lambda i: None,
                             resume=lambda i: True, skills=None, workspace_root="C:/jobs")
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    mgr._agents, mgr._trace_readings, mgr._last_ref = agents, [], None

    async def no_wait(task):
        return None
    mgr._wait_for_plan = no_wait
    run = lambda goal: asyncio.run(mgr._tool_delegate_task({"goal": goal}))
    return mgr, started, run, SimpleNamespace, time


def test_a_job_is_told_what_it_means(monkeypatch):
    mgr, started, run, NS, time = _voice(monkeypatch)
    mgr._last_ref = NS(label="Quarterly report.docx", at=time.monotonic() - 5)
    run("turn that into a one-page summary")
    assert started and "What they refer to: the control 'Quarterly report.docx'" in started[0].goal
    assert started[0].metadata["reference"]["link"] == "last_said"


def test_with_nothing_it_could_mean_she_asks(monkeypatch):
    mgr, started, run, NS, time = _voice(monkeypatch)
    out = run("send it to the team")
    assert not started and "ask them which thing" in out["error"]


def test_a_request_that_names_its_own_it_is_not_a_reference():
    from ai.references import refers
    assert not refers("Create a new folder called MyApp and build the foundation of a React application inside it.")
    assert not refers("check that the site builds")
    assert refers("send it to the team") and refers("turn that into a one-page summary")
    assert refers("please could you summarise this")
