"""EN-30: a job's history as actions and observations.

The calls and results every step already saves, read as a typed stream;
a resumed step rebuilds its conversation from it, so the model carries on
from what it did instead of starting the step's thinking over.
"""
from __future__ import annotations

import asyncio

from agents.core import (
    AgentContext, AgentSpec, EventBus, ProviderCapabilities, ProviderResponse, RunControl, Step,
    StepStatus, Task, ToolCall, ToolResult,
)
from agents.core.stream import Action, Observation, answered, kind_of, of_step, of_task
from agents.tools import default_registry
from agents.workers.file import FileWorker


def _step():
    a, b, c = (ToolCall("read_file", {"path": "a.txt"}), ToolCall("write_file", {"path": "b.txt", "content": "x"}),
               ToolCall("run_command", {"command": "npm test"}))
    return Step("Fix it", status=StepStatus.RUNNING, tool_calls=[a, b, c], results=[
        ToolResult(call_id=a.id, tool_name="read_file", ok=True, output="hello\nworld"),
        ToolResult(call_id=b.id, tool_name="write_file", ok=False, error="disk full")])


def test_the_saved_calls_read_as_actions_and_observations():
    step = _step()
    stream = of_step(step)
    assert [type(e).__name__ for e in stream] == ["Action", "Observation", "Action", "Observation", "Action"]
    assert [kind_of(n) for n in ("read_file", "write_file", "run_command", "type_text", "mcp_github_x",
                                 "ask_user", "wait_until", "made_up")] == \
        ["read", "edit", "run", "operate", "use", "ask", "wait", "act"]
    pairs = answered(stream)
    assert [(a.tool, o.ok, o.text) for a, o in pairs] == [("read_file", True, "hello"),
                                                         ("write_file", False, "disk full")]
    assert pairs[0][0].signature == 'read_file({"path": "a.txt"})'
    assert len(of_task(Task(title="t", goal="g", workspace=".", steps=[step, step]))) == 10


class _Record:
    name = "record"

    def __init__(self):
        self.seen = []

    async def complete(self, messages, tools=None, *, system="", model=None, temperature=0.2, max_tokens=4096):
        self.seen.append([(m.role, m.content, [c.tool_name for c in (m.tool_calls or [])]) for m in messages])
        return ProviderResponse(text="carried on")

    def capabilities(self):
        return ProviderCapabilities(local=True, cost_tier=0)


def test_a_resumed_step_carries_on_from_what_it_did(tmp_path):
    step = _step()
    provider = _Record()
    task = Task(title="t", goal="fix the build", workspace=str(tmp_path), steps=[step])
    ctx = AgentContext(task=task, agent=AgentSpec(name="A", goal="g"), tools=default_registry(),
                       provider=provider, events=EventBus(), control=RunControl())
    asyncio.run(FileWorker().execute_step(step, ctx))
    sent = provider.seen[0]
    roles = [r for r, _, _ in sent]
    assert roles == ["user", "assistant", "tool", "assistant", "tool", "user"]
    assert sent[1][2] == ["read_file"] and "hello\nworld" in sent[2][1] and "disk full" in sent[4][1]
    assert sent[-1][1] == FileWorker.RESUMED_NOTE
    assert "run_command" not in str(sent), "a call that never answered is the recovery's to judge"
    assert step.status is StepStatus.DONE


def test_a_fresh_step_starts_as_before(tmp_path):
    step = Step("Fresh", status=StepStatus.PENDING)
    assert FileWorker._carried_on(step) == []
