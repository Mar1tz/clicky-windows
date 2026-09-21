"""Registry.execute(): validation, policy/approval, dry run, events, containment."""
import asyncio

import pytest

from agents.core import (
    ApprovalRequest, EventBus, EventKind, PermissionPolicy, RiskLevel, RunControl,
    ToolCall, ToolContext, ToolResult, auto_deny,
)
from agents.tools import Registry, Tool, make_spec
from agents.tools.schema import validate


class Echo(Tool):
    spec = make_spec(
        "echo", "echo", risk=RiskLevel.LOW, category="test",
        properties={
            "text": {"type": "string"},
            "times": {"type": "integer"},
            "flag": {"type": "boolean"},
            "mode": {"type": "string", "enum": ["a", "b"]},
            "items": {"type": "array", "items": {"type": "string"}},
        },
        required=["text"],
    )

    async def run(self, args, ctx):
        return self.ok(args["text"] * int(args.get("times", 1)), text=args["text"])


class Boom(Tool):
    spec = make_spec("boom", "raises", risk=RiskLevel.SAFE, category="test")

    async def run(self, args, ctx):
        raise RuntimeError("kaboom")


class Peek(Tool):
    spec = make_spec("peek", "safe read", risk=RiskLevel.SAFE, category="test")

    async def run(self, args, ctx):
        return self.ok("peeked")


class Nuke(Tool):
    spec = make_spec("nuke", "high risk", risk=RiskLevel.HIGH, category="test")

    async def run(self, args, ctx):
        return self.ok("nuked")


def make_registry() -> Registry:
    return Registry([Echo(), Boom(), Peek(), Nuke()])


def make_ctx(tmp_path, **kw) -> ToolContext:
    kw.setdefault("policy", PermissionPolicy(max_auto_risk=RiskLevel.MEDIUM))
    return ToolContext(workspace=str(tmp_path), task_id="t1", agent_id="a1", **kw)


# -- catalogue -----------------------------------------------------------------

def test_register_get_names_specs():
    reg = make_registry()
    assert reg.names() == ["echo", "boom", "peek", "nuke"]
    assert reg.get("echo").name == "echo" and "echo" in reg and len(reg) == 4
    assert [s.name for s in reg.specs(["nuke", "echo", "missing"])] == ["nuke", "echo"]
    assert len(reg.specs()) == 4
    with pytest.raises(KeyError):
        reg.get("missing")


def test_register_replaces_same_name():
    reg = make_registry()
    reg.register(Echo())
    assert len(reg) == 4


# -- schema validator ----------------------------------------------------------

def test_validate_required_and_types():
    schema = Echo.spec.parameters
    assert validate(schema, {"text": "x"}) == []
    assert validate(schema, {"text": "x", "times": 2.0, "flag": True, "items": ["a"]}) == []
    assert any("missing required argument 'text'" in e for e in validate(schema, {}))
    assert any("'times' must be integer" in e for e in validate(schema, {"text": "x", "times": "2"}))
    assert any("'times' must be integer" in e for e in validate(schema, {"text": "x", "times": True}))
    assert any("'flag' must be boolean" in e for e in validate(schema, {"text": "x", "flag": 1}))
    assert any("'mode' must be one of" in e for e in validate(schema, {"text": "x", "mode": "z"}))
    assert any("'items[0]' must be string" in e for e in validate(schema, {"text": "x", "items": [1]}))
    assert validate({"type": "object", "properties": {"p": {"type": ["array", "string"]}}},
                    {"p": "one two"}) == []
    strict = {"type": "object", "properties": {"a": {"type": "string"}}, "additionalProperties": False}
    assert any("unexpected argument 'b'" in e for e in validate(strict, {"a": "x", "b": 1}))


# -- execute -------------------------------------------------------------------

async def test_execute_success_stamps_ids_and_duration(tmp_path):
    reg, ctx = make_registry(), make_ctx(tmp_path)
    call = ToolCall("echo", {"text": "hi", "times": 2})
    res = await reg.execute(call, ctx)
    assert res.ok and res.output == "hihi" and res.data["text"] == "hi"
    assert res.call_id == call.id and res.tool_name == "echo"
    assert isinstance(res.duration_ms, int) and res.duration_ms >= 0


async def test_unknown_tool_is_failure_not_exception(tmp_path):
    res = await make_registry().execute(ToolCall("nope", {}), make_ctx(tmp_path))
    assert not res.ok and "unknown tool" in res.error and "echo" in res.error


async def test_invalid_arguments_rejected_before_run(tmp_path):
    reg, ctx = make_registry(), make_ctx(tmp_path)
    res = await reg.execute(ToolCall("echo", {}), ctx)
    assert not res.ok and "invalid arguments" in res.error and "'text'" in res.error
    res = await reg.execute(ToolCall("echo", {"text": "x", "times": "3"}), ctx)
    assert not res.ok and "'times' must be integer" in res.error


async def test_policy_denied_with_auto_deny(tmp_path):
    reg = make_registry()
    ctx = make_ctx(tmp_path, policy=PermissionPolicy(max_auto_risk=RiskLevel.LOW), approver=auto_deny)
    res = await reg.execute(ToolCall("nuke", {}), ctx)
    assert not res.ok
    assert res.error.startswith("permission denied: ")
    assert "high risk" in res.error and "up to low" in res.error
    # a SAFE tool on the same policy runs without asking
    assert (await reg.execute(ToolCall("peek", {}), ctx)).ok


async def test_denied_tools_and_allowed_tools_reasons(tmp_path):
    reg = make_registry()
    ctx = make_ctx(tmp_path, policy=PermissionPolicy(denied_tools=["peek"]), approver=auto_deny)
    res = await reg.execute(ToolCall("peek", {}), ctx)
    assert "denied_tools" in res.error
    ctx = make_ctx(tmp_path, policy=PermissionPolicy(allowed_tools=["echo"]), approver=auto_deny)
    res = await reg.execute(ToolCall("peek", {}), ctx)
    assert "not in allowed_tools" in res.error


async def test_approver_receives_request_and_can_approve(tmp_path):
    seen: list[ApprovalRequest] = []

    async def approver(req: ApprovalRequest) -> bool:
        seen.append(req)
        return True

    reg = make_registry()
    ctx = make_ctx(tmp_path, policy=PermissionPolicy(max_auto_risk=RiskLevel.SAFE), approver=approver)
    call = ToolCall("nuke", {})
    res = await reg.execute(call, ctx)
    assert res.ok and res.output == "nuked"
    assert len(seen) == 1
    req = seen[0]
    assert req.task_id == "t1" and req.agent_id == "a1" and req.call is call
    assert req.approved is True and "nuke" in req.reason


async def test_broken_approver_means_denied(tmp_path):
    async def approver(req):
        raise ValueError("ui gone")

    ctx = make_ctx(tmp_path, policy=PermissionPolicy(max_auto_risk=RiskLevel.SAFE), approver=approver)
    res = await make_registry().execute(ToolCall("nuke", {}), ctx)
    assert not res.ok and "permission denied" in res.error and "ui gone" in res.error


async def test_dry_run_skips_non_safe_tools(tmp_path):
    reg = make_registry()
    ctx = make_ctx(tmp_path, dry_run=True, policy=PermissionPolicy(max_auto_risk=RiskLevel.HIGH))
    res = await reg.execute(ToolCall("nuke", {}), ctx)
    assert res.ok and res.output == "dry run" and res.data["dry_run"] is True
    res = await reg.execute(ToolCall("peek", {}), ctx)
    assert res.ok and res.output == "peeked"        # SAFE tools still run


async def test_tool_exception_becomes_failure(tmp_path):
    res = await make_registry().execute(ToolCall("boom", {}), make_ctx(tmp_path))
    assert not res.ok and res.error == "RuntimeError: kaboom"


async def test_events_emitted_in_order(tmp_path):
    bus = EventBus()
    seen = []
    bus.subscribe(seen.append, kinds=[EventKind.TOOL_CALL, EventKind.TOOL_RESULT])
    reg, ctx = make_registry(), make_ctx(tmp_path, events=bus)
    call = ToolCall("echo", {"text": "e"})
    await reg.execute(call, ctx)
    assert [e.kind for e in seen] == [EventKind.TOOL_CALL, EventKind.TOOL_RESULT]
    assert seen[0].data["call"]["id"] == call.id and seen[0].task_id == "t1"
    assert seen[1].data["result"]["call_id"] == call.id and seen[1].data["result"]["ok"] is True
    assert seen[1].agent_id == "a1" and seen[1].level == "info"
    # failures are warn-level and still produce both events
    seen.clear()
    await reg.execute(ToolCall("boom", {}), ctx)
    assert [e.kind for e in seen] == [EventKind.TOOL_CALL, EventKind.TOOL_RESULT]
    assert seen[1].level == "warn" and "kaboom" in seen[1].message


async def test_checkpoint_blocks_while_paused_and_raises_on_cancel(tmp_path):
    control = RunControl()
    reg, ctx = make_registry(), make_ctx(tmp_path, control=control)
    control.pause()
    pending = asyncio.create_task(reg.execute(ToolCall("peek", {}), ctx))
    await asyncio.sleep(0.02)
    assert not pending.done()
    control.resume()
    assert (await asyncio.wait_for(pending, 1)).ok
    control.cancel()
    with pytest.raises(asyncio.CancelledError):
        await reg.execute(ToolCall("peek", {}), ctx)


async def test_result_roundtrips_through_to_dict(tmp_path):
    res = await make_registry().execute(ToolCall("echo", {"text": "r"}), make_ctx(tmp_path))
    again = ToolResult.from_dict(res.to_dict())
    assert again.ok and again.output == "r" and again.call_id == res.call_id
