"""Approval receipts (EN-08, the Massive Direction Plan of 25 Oct 2026).

A yes covers exactly what was shown - the tool and its arguments, hashed on
the request - and nothing else: changed between the question and the run,
it is asked again. A screen or money action approved long after it was
asked (the screen may have moved on) is not run on that old yes.
"""
from __future__ import annotations

from typing import Any

from agents.core import RiskLevel, ToolCall, ToolContext, ToolResult
from agents.tools import Registry
from agents.tools import registry as reg_mod
from agents.tools.base import Tool, make_spec


class Click(Tool):
    spec = make_spec("click_send", "Click Send.", risk=RiskLevel.HIGH, category="test",
                     properties={"to": {"type": "string"}})
    always_ask = True

    def __init__(self):
        self.ran = []

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        self.ran.append(dict(args))
        return self.ok("sent")


def _run(tmp_path, approver):
    tool = Click()
    reg = Registry([tool])
    ctx = ToolContext(workspace=str(tmp_path), approver=approver)
    return tool, reg, ctx


async def test_a_yes_runs_exactly_what_was_shown(tmp_path):
    seen = []

    async def yes(request):
        seen.append(request)
        return True
    tool, reg, ctx = _run(tmp_path, yes)
    result = await reg.execute(ToolCall("click_send", {"to": "sam@example.com"}), ctx)
    assert result.ok and tool.ran == [{"to": "sam@example.com"}]
    assert len(seen[0].payload_hash) == 64 and seen[0].decided_at >= seen[0].created_at


async def test_an_action_changed_after_the_yes_is_asked_again(tmp_path):
    async def yes_but_changed(request):
        request.call.arguments["to"] = "everyone@example.com"     # what runs is no longer what was shown
        return True
    tool, reg, ctx = _run(tmp_path, yes_but_changed)
    result = await reg.execute(ToolCall("click_send", {"to": "sam@example.com"}), ctx)
    assert not result.ok and "changed after it was approved" in result.error and tool.ran == []


async def test_a_screen_action_approved_too_late_is_not_run(tmp_path):
    async def yes_much_later(request):
        request.created_at -= reg_mod.APPROVAL_FRESH_S + 60
        return True
    tool, reg, ctx = _run(tmp_path, yes_much_later)
    result = await reg.execute(ToolCall("click_send", {"to": "sam@example.com"}), ctx)
    assert not result.ok and "screen may have changed" in result.error and tool.ran == []
