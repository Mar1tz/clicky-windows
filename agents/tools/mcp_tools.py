"""The person's MCP servers' tools, as registry tools (CI-18).

Each tool a server offers becomes "mcp_<server>_<tool>" in the registry, so
it passes through the same choke point as every built-in tool: argument
checks, approvals, time limits, output caps. What it returns reaches the
model as data, never instructions (agents/workers/base.as_data).

Trust: a server's word that a tool only reads (annotations.readOnlyHint)
makes it MEDIUM; anything else is HIGH and asks the person every time.
A job gets a server's tools when it names the server ("in Notion", "my
GitHub issues") - every server's tools on every job would bury the model.
"""
from __future__ import annotations

import re
from typing import Any

from agents.core import RiskLevel, ToolContext, ToolResult, ToolSpec

from .base import Tool

CATEGORY = "mcp"
PREFIX = "mcp_"


def tool_name(server: str, tool: str) -> str:
    return (PREFIX + re.sub(r"[^a-zA-Z0-9_]", "_", f"{server}_{tool}"))[:64]


class McpTool(Tool):
    def __init__(self, server, tool: dict) -> None:
        self.server = server
        self.remote = str(tool.get("name") or "")
        annotations = tool.get("annotations") or {}
        self.read_only = annotations.get("readOnlyHint") is True
        self.always_ask = not self.read_only            # a tool that may change something asks, every time
        schema = tool.get("inputSchema") or {"type": "object", "properties": {}}
        self.spec = ToolSpec(
            name=tool_name(server.cfg.name, self.remote),
            description=(f"[{server.cfg.name} - an MCP server the person added] "
                         + str(tool.get("description") or self.remote))[:900],
            parameters=schema if schema.get("type") == "object" else {"type": "object", "properties": {}},
            risk=RiskLevel.MEDIUM if self.read_only else RiskLevel.HIGH,
            category=CATEGORY)

    def ask_reason_for(self, call) -> str:
        return f"Use {self.server.cfg.name}: {self.remote} with {str(call.arguments)[:200]}"

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from integrations.mcp import McpError
        try:
            ok, text = await self.server.call(self.remote, args)
        except McpError as exc:
            return self.fail(str(exc))
        return self.ok(text, server=self.server.cfg.name) if ok else self.fail(text[:2000] or "the server said no")


def attach(registry, hub) -> list[str]:
    """Register every running server's tools; their names."""
    names = []
    for server in hub.servers.values():
        if not server.running:
            continue
        for tool in server.tools:
            t = McpTool(server, tool)
            registry.register(t)
            names.append(t.name)
    return names


def for_goal(goal: str, registry) -> list[str]:
    """The MCP tools of the servers a job names ("open an issue on github")."""
    words = set(re.findall(r"[a-z0-9]+", (goal or "").lower()))
    out = []
    for name in registry.names():
        if not name.startswith(PREFIX):
            continue
        tool = registry.get(name)
        server = getattr(getattr(tool, "server", None), "cfg", None)
        if server is not None and set(server.name.split("_")) & words:
            out.append(name)
    return out
