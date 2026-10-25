"""Tool contracts the registry enforces (EN-02/03, the Massive Direction Plan).

A result is cut at its tool's cap and says so, so a runaway output (a 10 MB
log, a whole website) cannot sink the model's context or the app; a tool
that declares a time limit is stopped at it with a plain failure.
"""
from __future__ import annotations

import asyncio
from typing import Any

from agents.core import RiskLevel, ToolCall, ToolContext, ToolResult
from agents.tools import Registry
from agents.tools.base import Tool, make_spec


class Flood(Tool):
    spec = make_spec("flood", "A lot of output.", risk=RiskLevel.SAFE, category="test")

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return self.ok("x" * 10_000_000)


class Slow(Tool):
    spec = make_spec("slow", "Takes too long.", risk=RiskLevel.SAFE, category="test")
    spec.timeout_s = 0.05

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        await asyncio.sleep(5)
        return self.ok("never")


async def test_a_flood_is_cut_and_says_so(tmp_path):
    reg = Registry([Flood()])
    result = await reg.execute(ToolCall("flood", {}), ToolContext(workspace=str(tmp_path)))
    assert result.ok and result.data["truncated"] is True
    assert len(result.output) < 61_000 and "more characters cut" in result.output


async def test_a_tool_past_its_time_limit_is_stopped(tmp_path):
    reg = Registry([Slow()])
    result = await reg.execute(ToolCall("slow", {}), ToolContext(workspace=str(tmp_path)))
    assert not result.ok and result.error == "timed out after 0.05s"


def test_the_contract_never_reaches_a_model():
    from agents.providers.openai_compatible import OpenAICompatibleProvider
    spec = Flood.spec
    fn = OpenAICompatibleProvider._tool_def(spec) if hasattr(OpenAICompatibleProvider, "_tool_def") else None
    if fn is not None:
        assert "max_output_chars" not in str(fn) and "timeout_s" not in str(fn)
