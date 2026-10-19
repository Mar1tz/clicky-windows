"""Worker: scripted path, ReAct path, max_iterations, pause/cancel."""
import asyncio

import pytest

from agents.core import (
    ProviderResponse, Step, StepStatus, ToolCall, ToolResult,
)
from agents.providers import MockProvider
from agents.workers import CodingWorker, FileWorker, default_workers

from ._fakes import FakeRegistry, make_context


# -- scripted path -----------------------------------------------------------

def test_scripted_step_runs_prefilled_calls_in_order():
    reg = FakeRegistry()
    reg.add("create_folder")
    reg.add("create_file")
    ctx = make_context(tools=reg)
    step = Step("scaffold", tool_calls=[
        ToolCall("create_folder", {"path": "MyApp"}),
        ToolCall("create_file", {"path": "MyApp/package.json"}),
    ])
    out = asyncio.run(CodingWorker().execute_step(step, ctx))
    assert out.status is StepStatus.DONE
    assert [c.tool_name for c in reg.executed] == ["create_folder", "create_file"]
    assert len(out.results) == 2 and all(r.ok for r in out.results)
    assert out.started_at and out.finished_at


def test_scripted_step_stops_and_fails_on_first_failure():
    reg = FakeRegistry()
    reg.add("create_folder")
    reg.add("boom", lambda a, c: ToolResult.failure(ToolCall("boom"), error="nope"))
    reg.add("after")
    ctx = make_context(tools=reg)
    step = Step("s", tool_calls=[ToolCall("create_folder"), ToolCall("boom"),
                                 ToolCall("after")])
    out = asyncio.run(CodingWorker().execute_step(step, ctx))
    assert out.status is StepStatus.FAILED and "nope" in out.error
    assert [c.tool_name for c in reg.executed] == ["create_folder", "boom"]  # stopped


# -- ReAct path --------------------------------------------------------------

def test_react_loop_runs_tools_then_finishes_on_prose():
    reg = FakeRegistry()
    reg.add("write_file", lambda a, c: ToolResult.success(ToolCall("write_file"),
                                                          output="wrote"))
    provider = MockProvider(script=[
        ProviderResponse(tool_calls=[ToolCall("write_file", {"path": "a.txt"})],
                         finish_reason="tool_calls"),
        ProviderResponse(text="all done"),          # prose -> step complete
    ])
    ctx = make_context(provider=provider, tools=reg)
    step = Step("write the file")                    # no prefilled calls -> ReAct
    out = asyncio.run(FileWorker().execute_step(step, ctx))
    assert out.status is StepStatus.DONE
    assert out.summary == "all done"
    assert [c.tool_name for c in out.tool_calls] == ["write_file"]
    assert out.results[0].output == "wrote"
    # provider saw the tool result fed back as a tool message on the 2nd call
    assert provider.calls[1].messages[-1].role == "tool"


def test_react_respects_allowed_tool_names_from_default_tools():
    reg = FakeRegistry()
    for n in ("create_file", "run_command", "click"):
        reg.add(n)
    provider = MockProvider(script=[ProviderResponse(text="ok")])
    ctx = make_context(provider=provider, tools=reg)      # agent.tools empty
    asyncio.run(FileWorker().execute_step(Step("x"), ctx))
    # FileWorker.default_tools excludes run_command/click
    offered = {s.name for s in provider.calls[0].tools}
    assert "create_file" in offered
    assert "run_command" not in offered and "click" not in offered


def test_react_agent_tools_override_worker_defaults():
    reg = FakeRegistry()
    for n in ("create_file", "read_file", "run_command"):
        reg.add(n)
    provider = MockProvider(script=[ProviderResponse(text="ok")])
    ctx = make_context(provider=provider, tools=reg, agent_tools=["run_command"])
    asyncio.run(FileWorker().execute_step(Step("x"), ctx))
    assert {s.name for s in provider.calls[0].tools} == {"run_command"}


def test_react_max_iterations_marks_failed():
    reg = FakeRegistry()
    reg.add("read_file")
    # provider ALWAYS asks for another tool call -> never terminates on its own
    provider = MockProvider(script=[ProviderResponse(
        tool_calls=[ToolCall("read_file", {"path": "a"})], finish_reason="tool_calls")])
    ctx = make_context(provider=provider, tools=reg)
    worker = FileWorker()
    worker.max_iterations = 3
    out = asyncio.run(worker.execute_step(Step("loop"), ctx))
    assert out.status is StepStatus.FAILED and "max iterations" in out.error
    assert provider.call_count == 3


def test_worker_never_raises_on_tool_context_error():
    class Boom(FakeRegistry):
        async def execute(self, call, ctx):
            raise RuntimeError("registry exploded")
    reg = Boom()
    reg.add("x")
    provider = MockProvider(script=[ProviderResponse(
        tool_calls=[ToolCall("x")], finish_reason="tool_calls")])
    ctx = make_context(provider=provider, tools=reg)
    out = asyncio.run(FileWorker().execute_step(Step("s"), ctx))
    assert out.status is StepStatus.FAILED and "registry exploded" in out.error


# -- pause / cancel ----------------------------------------------------------

def test_cancel_before_step_raises_cancelled_and_marks_failed():
    reg = FakeRegistry()
    reg.add("create_file")
    ctx = make_context(tools=reg)
    ctx.control.cancel()
    step = Step("s", tool_calls=[ToolCall("create_file")])
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(CodingWorker().execute_step(step, ctx))
    assert step.status is StepStatus.FAILED and step.error == "cancelled"
    assert reg.executed == []                          # never ran the tool


def test_pause_blocks_then_resume_completes():
    reg = FakeRegistry()
    reg.add("create_file")
    ctx = make_context(tools=reg)
    ctx.control.pause()
    step = Step("s", tool_calls=[ToolCall("create_file")])

    async def scenario():
        task = asyncio.create_task(CodingWorker().execute_step(step, ctx))
        await asyncio.sleep(0.05)
        assert not task.done() and reg.executed == []  # parked at checkpoint
        ctx.control.resume()
        return await asyncio.wait_for(task, 1.0)

    out = asyncio.run(scenario())
    assert out.status is StepStatus.DONE and len(reg.executed) == 1


# -- registry ----------------------------------------------------------------

def test_default_workers_has_all_six_types():
    reg = default_workers()
    assert set(reg.types()) == {"coding", "file", "terminal", "research",
                                "browser", "computer"}
    assert reg.get("terminal").worker_type == "terminal"


def test_worker_registry_unknown_falls_back_to_coding():
    reg = default_workers()
    assert reg.get("nonexistent").worker_type == "coding"


# -- AG-05: a turn of reads runs side by side ---------------------------------

class _SlowReads(FakeRegistry):
    async def execute(self, call, ctx):
        await asyncio.sleep(0.2)
        self.executed.append(call)
        return ToolResult.success(call, output=f"{call.tool_name} {call.arguments.get('path', '')}")


def _turn(calls):
    return MockProvider(script=[ProviderResponse(tool_calls=calls, finish_reason="tool_calls"),
                                ProviderResponse(text="read them")])


def test_three_reads_in_one_turn_take_one_reads_time_and_keep_their_order():
    import time
    reg = _SlowReads()
    for name in ("read_file", "list_dir", "git_status"):
        reg.add(name)
    calls = [ToolCall("read_file", {"path": "a.py"}), ToolCall("list_dir", {"path": "src"}),
             ToolCall("git_status")]
    ctx = make_context(provider=_turn(calls), tools=reg)
    started = time.monotonic()
    out = asyncio.run(FileWorker().execute_step(Step("look around"), ctx))
    assert time.monotonic() - started < 0.45, "side by side, not 0.6 s in a row"
    assert [r.output for r in out.results] == ["read_file a.py", "list_dir src", "git_status "]


def test_a_turn_that_writes_runs_in_order():
    import time
    reg = _SlowReads()
    for name in ("read_file", "write_file"):
        reg.add(name)
    calls = [ToolCall("read_file", {"path": "a.py"}), ToolCall("write_file", {"path": "a.py"})]
    ctx = make_context(provider=_turn(calls), tools=reg)
    started = time.monotonic()
    asyncio.run(FileWorker().execute_step(Step("edit"), ctx))
    assert time.monotonic() - started >= 0.4 and [c.tool_name for c in reg.executed] == ["read_file", "write_file"]
