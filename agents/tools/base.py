"""Shared base for the concrete tools.

BaseTool.run(args, ctx) never sees the ToolCall, so a tool cannot fill
call_id itself. Tools build results through ok()/fail() with an empty
call_id and Registry.execute() - the single choke point - stamps
call_id / tool_name / duration_ms on the way out. That keeps every tool
a few lines of "check path, do thing, report".

Every file-touching tool goes through guard(): it is the only place the
workspace boundary is checked, so nothing can forget to.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from agents.core import BaseTool, RiskLevel, ToolContext, ToolResult, ToolSpec

from .process import CommandOutput


def make_spec(
    name: str,
    description: str,
    *,
    risk: RiskLevel,
    category: str,
    properties: Optional[dict[str, dict[str, Any]]] = None,
    required: Optional[list[str]] = None,
) -> ToolSpec:
    """ToolSpec with a JSON-schema object built from `properties`."""
    return ToolSpec(
        name=name, description=description, risk=risk, category=category,
        parameters={
            "type": "object",
            "properties": dict(properties or {}),
            "required": list(required or []),
        },
    )


class Tool(BaseTool):
    """BaseTool plus the result / path helpers every concrete tool uses."""

    spec: ToolSpec

    def ok(self, output: str = "", **data: Any) -> ToolResult:
        return ToolResult(call_id="", tool_name=self.name, ok=True, output=output, data=data)

    def fail(self, error: str, output: str = "", **data: Any) -> ToolResult:
        return ToolResult(call_id="", tool_name=self.name, ok=False, output=output,
                          error=error, data=data)

    def guard(self, ctx: ToolContext, path: str) -> tuple[str, Optional[ToolResult]]:
        """(absolute path, None) when `path` is inside the workspace, else
        ("", failure). Use as: target, err = self.guard(ctx, p); if err: return err."""
        if not isinstance(path, str):
            return "", self.fail(f"path must be a string, got {type(path).__name__}")
        if not ctx.is_inside_workspace(path):
            return "", self.fail(f"outside workspace: {path}")
        return ctx.resolve_path(path), None

    def from_process(self, out: CommandOutput, **extra: Any) -> ToolResult:
        """ToolResult for a finished subprocess: ok iff exit 0, output kept
        either way so the LLM can read the error."""
        data = {**out.to_data(), **extra}
        if out.timed_out:
            return self.fail(f"timed out: {out.command}", output=out.text(), **data)
        if out.exit_code != 0:
            return self.fail(f"exit code {out.exit_code}", output=out.text(), **data)
        return self.ok(out.text(), **data)


@contextmanager
def replacing(path: str) -> Iterator[str]:
    """OR-16: write to a temporary name beside `path` (its extension kept,
    for writers that go by it) and rename it into place only once the write
    finished - a crash or a full disk leaves the old file, never half a new
    one. The temporary file is gone either way."""
    folder = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(folder, exist_ok=True)
    stem, ext = os.path.splitext(os.path.basename(path))
    tmp = os.path.join(folder, f".{stem}.heytora-tmp{ext}")
    try:
        yield tmp
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
