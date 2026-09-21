"""M18: tasks survive the process.

A crash cannot be staged cleanly inside one test process, so the gate
test does the next best thing: run a task to completion, then rewrite
the stored record to look exactly like a task caught mid-flight - three
steps done, one RUNNING, the rest PENDING, the later files missing - and
build a fresh manager on the same database. Recovery must show it
paused; resume must finish only the remaining steps.
"""
from __future__ import annotations

import os

import pytest

from agents.bootstrap import build_agent_manager
from agents.core import AgentStatus, EventKind, StepStatus, Task, TaskStatus

REQUEST = "Create a new folder called MyApp and build the foundation of a React application inside it."


def _manager(tmp_path, db="agents.db"):
    return build_agent_manager(workspace_root=str(tmp_path), db_path=str(tmp_path / db),
                               offline=True)


def _stage_interruption(store, task, *, done: int):
    """Rewrite a completed task as if it died after `done` steps."""
    task.status = TaskStatus.RUNNING
    task.progress = done / len(task.steps)
    task.error = ""
    for i, step in enumerate(task.steps):
        if i < done:
            continue
        step.status = StepStatus.RUNNING if i == done else StepStatus.PENDING
        step.results.clear()
        step.error = ""
    store.save_task(task)


def _delete_outputs(task, tmp_path, *, from_step: int):
    """Remove the files the later steps created, so resume has work to do."""
    for step in task.steps[from_step:]:
        for call in step.tool_calls:
            path = call.arguments.get("path")
            if path and call.tool_name == "create_file":
                full = tmp_path / path
                if full.is_file():
                    full.unlink()


# -- recovery ----------------------------------------------------------------

def test_in_flight_tasks_come_back_paused(tmp_path):
    first = _manager(tmp_path).start()
    try:
        task = first.wait(first.submit(REQUEST).id, 60)
        assert task.status is TaskStatus.COMPLETED
        _stage_interruption(first.store, task, done=3)
    finally:
        first.stop()

    second = _manager(tmp_path).start()
    try:
        back = second.get_task(task.id)
        assert back is not None
        assert back.status is TaskStatus.PAUSED
        assert back.metadata.get("interrupted") == second.INTERRUPTED_NOTE
        assert "interrupted" in back.current_action
        # the panel would show it as a paused agent with a Resume button
        agent = second.get_agent(back.agent_id)
        assert agent is not None and agent.status is AgentStatus.PAUSED
        assert agent.step_index == 3 and agent.step_total == len(back.steps)
        assert back in second.list_tasks()
    finally:
        second.stop()


def test_finished_tasks_are_left_alone(tmp_path):
    first = _manager(tmp_path).start()
    try:
        task = first.wait(first.submit(REQUEST).id, 60)
        assert task.status is TaskStatus.COMPLETED
    finally:
        first.stop()

    second = _manager(tmp_path).start()
    try:
        assert second.recover() == []
        assert second.get_task(task.id).status is TaskStatus.COMPLETED
        assert second.list_agents() == []
    finally:
        second.stop()


def test_nothing_is_restarted_automatically(tmp_path):
    """The process may have died BECAUSE of the task; re-running it unasked
    would loop. Recovery pauses; a human resumes."""
    first = _manager(tmp_path).start()
    try:
        task = first.wait(first.submit(REQUEST).id, 60)
        _stage_interruption(first.store, task, done=2)
    finally:
        first.stop()

    second = _manager(tmp_path).start()
    try:
        started = []
        second.events.subscribe(started.append, kinds=[EventKind.STEP_STARTED])
        import time
        time.sleep(0.3)
        assert started == [], "a recovered task ran without being resumed"
        assert second.get_task(task.id).status is TaskStatus.PAUSED
    finally:
        second.stop()


# -- the gate ----------------------------------------------------------------

def test_resume_finishes_only_the_remaining_steps(tmp_path):
    first = _manager(tmp_path).start()
    try:
        task = first.wait(first.submit(REQUEST).id, 60)
        assert task.status is TaskStatus.COMPLETED
        total = len(task.steps)
        _stage_interruption(first.store, task, done=3)
        _delete_outputs(task, tmp_path, from_step=3)
    finally:
        first.stop()

    # precondition: the later outputs are really gone, the early ones remain
    assert (tmp_path / "MyApp" / "package.json").is_file()      # step 2
    assert not (tmp_path / "MyApp" / "src" / "App.jsx").exists()  # later step

    second = _manager(tmp_path).start()
    try:
        started: list[str] = []
        second.events.subscribe(lambda e: started.append(e.message),
                                kinds=[EventKind.STEP_STARTED], task_id=task.id)
        assert second.resume(task.id)
        done = second.wait(task.id, 60)
        assert done.status is TaskStatus.COMPLETED, done.error

        # exactly the unfinished steps ran: the RUNNING one plus the PENDINGs
        assert len(started) == total - 3, started
        assert "Create project folder" not in started
        assert all(s.status is StepStatus.DONE for s in done.steps)
        assert (tmp_path / "MyApp" / "src" / "App.jsx").is_file()
        assert "interrupted" not in done.metadata
        assert second.get_agent(done.agent_id).status is AgentStatus.IDLE
    finally:
        second.stop()


def test_resumed_step_that_already_wrote_its_file_still_passes(tmp_path):
    """The idempotency the whole scheme rests on: the step that was RUNNING
    at the crash may have finished its write. Re-running create_file on
    identical content must succeed, not fail with 'already exists'."""
    first = _manager(tmp_path).start()
    try:
        task = first.wait(first.submit(REQUEST).id, 60)
        # interrupt AT step 2 (package.json) but leave its file in place
        _stage_interruption(first.store, task, done=1)
    finally:
        first.stop()
    assert (tmp_path / "MyApp" / "package.json").is_file()

    second = _manager(tmp_path).start()
    try:
        assert second.resume(task.id)
        done = second.wait(task.id, 60)
        assert done.status is TaskStatus.COMPLETED, done.error
        step2 = done.steps[1]
        assert step2.status is StepStatus.DONE
        assert step2.results and step2.results[0].ok
        assert step2.results[0].data.get("created") is False
    finally:
        second.stop()


def test_recovered_pending_task_starts_from_the_top(tmp_path):
    """A task queued but never started has no plan; resume plans it."""
    first = _manager(tmp_path).start()
    try:
        t = Task(title="Never ran", goal="Create a folder called Later",
                 workspace=str(tmp_path))
        first.store.save_task(t)                   # PENDING, no steps
    finally:
        first.stop()

    second = _manager(tmp_path).start()
    try:
        back = second.get_task(t.id)
        assert back.status is TaskStatus.PAUSED
        assert second.resume(t.id)
        done = second.wait(t.id, 30)
        assert done.status is TaskStatus.COMPLETED, done.error
        assert (tmp_path / "Later").is_dir()
    finally:
        second.stop()


def test_recovery_can_be_disabled(tmp_path):
    first = _manager(tmp_path).start()
    try:
        task = first.wait(first.submit(REQUEST).id, 60)
        _stage_interruption(first.store, task, done=1)
    finally:
        first.stop()
    second = _manager(tmp_path).start(recover=False)
    try:
        assert second.list_tasks() == []
        assert second.store.get_task(task.id).status is TaskStatus.RUNNING  # untouched
    finally:
        second.stop()


def test_create_file_is_idempotent_on_identical_content(tmp_path):
    import asyncio

    from agents.core import ToolCall, ToolContext
    from agents.tools import default_registry
    reg = default_registry()
    ctx = ToolContext(workspace=str(tmp_path))
    first = asyncio.run(reg.execute(ToolCall("create_file", {"path": "a.txt", "content": "hi"}), ctx))
    again = asyncio.run(reg.execute(ToolCall("create_file", {"path": "a.txt", "content": "hi"}), ctx))
    differ = asyncio.run(reg.execute(ToolCall("create_file", {"path": "a.txt", "content": "bye"}), ctx))
    assert first.ok and first.data.get("created", True)
    assert again.ok and again.data.get("created") is False
    assert not differ.ok and "already exists" in differ.error
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "hi"
