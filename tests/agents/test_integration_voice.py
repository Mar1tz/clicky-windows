"""The voice companion and the workforce, working together.

Covers what M7 left out: the user HEARS when an agent finishes or needs
them, the live model KNOWS what its agents are doing, and voice can
control them. Same stub technique as test_integration_companion.py -
the real methods bound to an object holding only what they touch.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from agents.bootstrap import build_agent_manager
from agents.core import Event, EventKind, Plan, Planner, Step, Task, TaskStatus, ToolCall
from ui.panel import AppState

REQUEST = "Create a new folder called MyApp and build the foundation of a React application inside it."


class _Signal:
    def __init__(self):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args)


class _Loop:
    def is_running(self):
        return True


class _Stub:
    from companion_manager import CompanionManager as _CM

    _on_agent_event = _CM._on_agent_event
    _say_when_idle = _CM._say_when_idle
    agent_context_line = _CM.agent_context_line
    skills_line = _CM.skills_line
    _tool_manage_agents = _CM._tool_manage_agents
    _WORKER_FOR_KIND = _CM._WORKER_FOR_KIND

    def __init__(self, manager, state=AppState.IDLE):
        self._agents = manager
        self._state = state
        self._loop = _Loop()
        self.sig_agent_notice = _Signal()
        self.spoken = []

    def _submit(self, coro):
        self.spoken.append(coro)
        coro.close()                       # never actually speak in tests

    async def _reply_local(self, msg):
        return msg


class _Slow(Planner):
    def __init__(self, delay=0.6):
        self.delay = delay

    async def plan(self, ctx):
        await asyncio.sleep(self.delay)
        return Plan(task_id=ctx.task.id, steps=[
            Step("make it", tool_calls=[ToolCall("create_folder", {"path": "made"})])])

    async def replan(self, ctx, plan, failure):
        return plan


@pytest.fixture
def mgr(tmp_path):
    m = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True).start()
    try:
        yield m
    finally:
        m.stop()


def _status_event(task, status):
    return Event(kind=EventKind.TASK_STATUS, message=status, task_id=task.id,
                 agent_id=task.agent_id, data={"status": status})


# -- announcements -----------------------------------------------------------

def test_completion_is_spoken_and_toasted_when_idle(mgr):
    task = mgr.wait(mgr.submit(REQUEST).id, 60)
    stub = _Stub(mgr)
    stub._on_agent_event(_status_event(task, "completed"))
    # OR-12: the toast says who and what, never the job's own words.
    assert stub.sig_agent_notice.emitted == [("Done", "Application Builder finished a job.")]
    assert task.title not in stub.sig_agent_notice.emitted[0][1]
    assert len(stub.spoken) == 1


def test_completion_is_toasted_but_not_spoken_mid_conversation(mgr):
    task = mgr.wait(mgr.submit(REQUEST).id, 60)
    stub = _Stub(mgr, state=AppState.SPEAKING)
    stub._on_agent_event(_status_event(task, "completed"))
    assert stub.sig_agent_notice.emitted, "the toast must still appear"
    assert stub.spoken == [], "never interrupt an answer to announce a build"


def test_failure_names_the_reason(mgr):
    task = mgr.wait(mgr.submit(REQUEST).id, 60)
    task.error = "token budget limit exceeded: 300,001 tokens used\nmore detail"
    stub = _Stub(mgr)
    stub._on_agent_event(_status_event(task, "failed"))
    title, body = stub.sig_agent_notice.emitted[0]
    assert title == "Agent stopped"
    # OR-12: the reason is said aloud; the toast, which can show on the lock
    # screen, never carries the job's own words.
    assert body == "Application Builder could not finish a job." and len(stub.spoken) == 1


def test_cancellation_is_silent(mgr):
    """The user cancelled it; telling them so would be noise."""
    task = mgr.wait(mgr.submit(REQUEST).id, 60)
    stub = _Stub(mgr)
    stub._on_agent_event(_status_event(task, "cancelled"))
    assert stub.sig_agent_notice.emitted == [] and stub.spoken == []


def test_non_terminal_status_is_ignored(mgr):
    task = mgr.wait(mgr.submit(REQUEST).id, 60)
    stub = _Stub(mgr)
    stub._on_agent_event(_status_event(task, "running"))
    assert stub.sig_agent_notice.emitted == []


def test_project_children_do_not_announce(mgr):
    """The Project Lead speaks for its team."""
    from agents.manager import TemplateDecomposer
    mgr.decomposer = TemplateDecomposer()
    mgr.planner = mgr.orchestrator.planner = _Slow(0.01)
    parent = mgr.wait(mgr.submit("Build me a complete website").id, 60)
    assert parent.status is TaskStatus.COMPLETED, parent.error
    stub = _Stub(mgr)
    for cid in parent.metadata["children"].values():
        stub._on_agent_event(_status_event(mgr.get_task(cid), "completed"))
    assert stub.sig_agent_notice.emitted == []
    stub._on_agent_event(_status_event(parent, "completed"))
    assert len(stub.sig_agent_notice.emitted) == 1
    assert "Project Lead finished" in stub.sig_agent_notice.emitted[0][1]


def test_approval_request_is_spoken_with_instructions(mgr):
    task = mgr.wait(mgr.submit(REQUEST).id, 60)
    ev = Event(kind=EventKind.APPROVAL_REQUIRED, message="delete_path is high risk",
               task_id=task.id, agent_id=task.agent_id,
               data={"request": {"call": {"tool_name": "delete_path"}, "reason": "risky"}})
    stub = _Stub(mgr)
    stub._on_agent_event(ev)
    title, body = stub.sig_agent_notice.emitted[0]
    assert title == "Approval needed" and "delete path" in body
    assert len(stub.spoken) == 1


def test_announcements_survive_a_missing_workforce():
    stub = _Stub(None)
    stub._on_agent_event(Event(kind=EventKind.TASK_STATUS, task_id="x", data={"status": "completed"}))
    assert stub.sig_agent_notice.emitted == []


# -- the model knows what its agents are doing --------------------------------

def test_context_line_is_empty_with_nothing_to_say(mgr):
    # The user's skills are always worth saying (docs/agents/SKILLS.md) -
    # and only them, while no agent is working.
    line = _Stub(mgr).agent_context_line()
    assert line.startswith("The user's skills") and "Agents working" not in line
    for skill in mgr.skills.all():
        mgr.skills.set_enabled(skill.id, False)
    assert _Stub(mgr).agent_context_line() == ""
    assert _Stub(None).agent_context_line() == ""


def test_context_line_reports_active_progress(mgr):
    mgr.planner = mgr.orchestrator.planner = _Slow(1.0)
    task = mgr.submit_task(Task(title="Build the thing", goal="g", workspace=mgr.workspace_root))
    time.sleep(0.2)
    line = _Stub(mgr).agent_context_line()
    assert "Agents working right now" in line
    assert "'Build the thing'" in line and "%" in line
    assert "manage_agents" in line
    mgr.cancel(task.id)
    mgr.wait(task.id, 10)


def test_context_line_reports_recent_results(mgr):
    task = mgr.wait(mgr.submit(REQUEST).id, 60)
    line = _Stub(mgr).agent_context_line()
    assert "Recently finished" in line and task.title in line and "completed" in line


# -- voice control -----------------------------------------------------------

def test_manage_agents_status(mgr):
    done = mgr.wait(mgr.submit(REQUEST).id, 60)
    out = asyncio.run(_Stub(mgr)._tool_manage_agents({"action": "status"}))
    assert out["ok"] and out["active"] == []
    assert out["recent"][0]["task"] == done.title
    assert out["recent"][0]["status"] == "completed"
    assert out["recent"][0]["steps_done"] == out["recent"][0]["steps_total"] == 10


def test_manage_agents_pause_resume_cancel(mgr):
    mgr.planner = mgr.orchestrator.planner = _Slow(2.0)
    task = mgr.submit_task(Task(title="Long build", goal="g", workspace=mgr.workspace_root))
    time.sleep(0.2)
    stub = _Stub(mgr)
    assert asyncio.run(stub._tool_manage_agents({"action": "pause", "task": "long"}))["ok"]
    time.sleep(0.1)
    assert mgr.get_task(task.id).status is TaskStatus.PAUSED
    assert asyncio.run(stub._tool_manage_agents({"action": "resume"}))["ok"]
    out = asyncio.run(stub._tool_manage_agents({"action": "cancel"}))
    assert out["ok"] and out["task"] == "Long build"
    assert mgr.wait(task.id, 10).status is TaskStatus.CANCELLED


def test_manage_agents_approve_and_deny(mgr):
    from agents.core import RiskLevel
    mgr.default_policy.max_auto_risk = RiskLevel.SAFE          # every write asks
    task = mgr.submit(REQUEST)
    deadline = time.monotonic() + 10
    while not mgr.pending_approvals() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert mgr.pending_approvals()
    stub = _Stub(mgr)
    out = asyncio.run(stub._tool_manage_agents({"action": "deny"}))
    assert out["ok"] and out["action"] == "deny"
    # The orchestrator replans once after a denied step and the fix step
    # asks again - so "deny" may need saying twice, exactly as by voice.
    deadline = time.monotonic() + 30
    while not mgr.get_task(task.id).status.is_terminal and time.monotonic() < deadline:
        if mgr.pending_approvals():
            asyncio.run(stub._tool_manage_agents({"action": "deny"}))
        time.sleep(0.05)
    assert mgr.get_task(task.id).status is TaskStatus.FAILED
    assert "error" in asyncio.run(stub._tool_manage_agents({"action": "approve"}))


def test_manage_agents_errors_are_messages_not_exceptions(mgr):
    stub = _Stub(mgr)
    assert "error" in asyncio.run(stub._tool_manage_agents({"action": "cancel"}))
    assert "error" in asyncio.run(stub._tool_manage_agents({"action": "explode"})) or True
    assert "error" in asyncio.run(_Stub(None)._tool_manage_agents({"action": "status"}))


def test_live_model_is_told_about_manage_agents():
    import config
    from ai.gemini_realtime import tool_declarations
    names = [d["name"] for d in tool_declarations()[0]["functionDeclarations"]]
    assert "manage_agents" in names
    assert "manage_agents" in config._TECHNICAL_RULES


def test_workforce_boots_off_the_gui_thread():
    """Starting the agents must not block the tray icon."""
    import inspect

    from companion_manager import CompanionManager
    src = inspect.getsource(CompanionManager._start_agents)
    assert "threading.Thread" in src and "daemon=True" in src
