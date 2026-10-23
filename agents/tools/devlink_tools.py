"""The code editor, read as data (HeyTora DevLink, devlink/).

What the user's VS Code knows - the project and file they are in, its
errors, git, what their terminal commands printed, where the debugger
stopped - for an agent working on their code, without a screenshot. Three
tools rather than eleven: the context packet, the logs, the code. All SAFE:
DevLink is read-only, and its own policy still decides per workspace what
may be read (nothing until the user allows it), redacts secrets and logs
every answer.

The hub lives in the app; an agent run without it (tests, a headless
runner) gets a plain failure saying DevLink is off.
"""
from __future__ import annotations

from typing import Any

from agents.core import RiskLevel, ToolContext, ToolResult

from .base import Tool, make_spec

CATEGORY = "devlink"
BUDGET = 8000
CAP = 12000


async def _answer(args: dict[str, Any]) -> dict:
    from devlink import tools as devlink_tools
    return await devlink_tools.answer(args, budget=BUDGET, cap=CAP)


class _DevLinkTool(Tool):
    async def _run(self, args: dict[str, Any]) -> ToolResult:
        out = await _answer(args)
        if out.get("ok"):
            return self.ok(f"[{out.get('workspace', '')}] {out['note']}\n\n{out['text']}", workspace=out.get("workspace", ""))
        return self.fail(out.get("error", "DevLink could not answer"))


class DevLinkContext(_DevLinkTool):
    spec = make_spec(
        "devlink_context",
        "What the user is doing in their code editor right now: the workspace and stack, git branch and "
        "changes, the active file and the code round the cursor, errors and warnings, the last terminal "
        "command and its output, recent failures, the debugger's stop. Start here for any job on the "
        "user's open project.",
        risk=RiskLevel.SAFE, category=CATEGORY,
        properties={"question": {"type": "string", "description": "What you need to know, to rank the packet."}},
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return await self._run({"action": "overview", "question": args.get("question") or ""})


class DevLinkLogs(_DevLinkTool):
    spec = make_spec(
        "devlink_logs",
        "The user's terminals, tasks and debug consoles as captured by their editor: the last command with "
        "its exit code and output, extracted failures (tests, compiler errors, crashes) with file and line, "
        "a stream's last lines, a full-text search, or the list of streams.",
        risk=RiskLevel.SAFE, category=CATEGORY,
        properties={
            "action": {"type": "string", "enum": ["last_command", "failures", "tail", "search", "streams"]},
            "query": {"type": "string", "description": "Words to find (search)."},
            "stream": {"type": "string", "description": "A terminal, task or debug session by name (tail, last_command)."},
            "lines": {"type": "integer", "description": "How many lines (tail, last_command). Default 40."},
            "since_minutes": {"type": "number", "description": "How far back (failures, search)."},
        },
        required=["action"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        action = {"search": "search_logs"}.get(args.get("action"), args.get("action"))
        return await self._run(dict(args) | {"action": action})


class DevLinkCode(_DevLinkTool):
    spec = make_spec(
        "devlink_code",
        "The code in the user's open workspace, through their editor (unsaved edits included): a file's "
        "lines, workspace symbols by name, the references to a symbol, or a text search across the "
        "project. Secrets files are never returned.",
        risk=RiskLevel.SAFE, category=CATEGORY,
        properties={
            "action": {"type": "string", "enum": ["file", "symbols", "references", "search"]},
            "path": {"type": "string", "description": "A file, relative to the project (file)."},
            "line": {"type": "integer", "description": "A line to centre on (file), 1 = the first."},
            "query": {"type": "string", "description": "A symbol's name (symbols, references) or words (search)."},
        },
        required=["action"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        action = {"search": "search_code"}.get(args.get("action"), args.get("action"))
        return await self._run(dict(args) | {"action": action})


def tools() -> list[Tool]:
    return [DevLinkContext(), DevLinkLogs(), DevLinkCode()]
