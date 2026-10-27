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
            category=CATEGORY, replay_safe=self.read_only)

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
        names += attach_server(registry, server)
    return names


def attach_server(registry, server) -> list[str]:
    """One server's tools ("connect Linear" while HeyTora runs); any it had before go first."""
    detach(registry, server.cfg.name)
    if not server.running:
        return []
    names = []
    for tool in server.tools:
        t = McpTool(server, tool)
        registry.register(t)
        names.append(t.name)
    return names


def detach(registry, server_name: str) -> int:
    """Forget a server's tools, so nothing is left for a model to call."""
    gone = 0
    for name in list(registry.names()):
        if name.startswith(PREFIX):
            cfg = getattr(getattr(registry.get(name), "server", None), "cfg", None)
            if cfg is not None and cfg.name == server_name:
                gone += bool(registry.unregister(name))
    return gone


# CI-30, the tool router: a big server (GitHub has dozens of tools, a Zapier
# link hundreds) would bury the planner and cost on every call. A job gets
# the ones that fit its goal, best first, at most this many per server.
MAX_PER_SERVER = 15
_GOAL_STOP = {"the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "my", "me", "it", "this", "that", "with",
              "please", "can", "you", "i", "from", "at", "by", "is", "be", "all", "new"}


def _words_of(text: str, *, split_camel: bool = False) -> set[str]:
    """Lower-case words. A tool's name splits at its capitals (createIssue);
    the person's words never do - "GitHub" is one word, not "git hub"."""
    if split_camel:
        text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text or "")
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 1 and w not in _GOAL_STOP}


def _fit(goal_words: set[str], tool) -> float:
    return (2.0 * len(goal_words & _words_of(tool.remote, split_camel=True))
            + 0.5 * len(goal_words & _words_of(tool.spec.description)))


def for_goal(goal: str, registry) -> list[str]:
    """The MCP tools a job gets: of the servers its goal names ("open an issue
    on github", "my jira tickets" - a service's other names, catalog.py),
    and of a link server (Zapier, Composio) the tools that fit even when
    the goal names only the app ("post in Slack"). Never more than
    MAX_PER_SERVER of one server: the ones that fit the goal best."""
    from integrations.catalog import SERVICES, names_for_jobs
    words = _words_of(goal)
    known = names_for_jobs()
    links = {s.id for s in SERVICES.values() if s.how == "link"}
    servers: dict[str, list] = {}
    for name in registry.names():
        if name.startswith(PREFIX):
            tool = registry.get(name)
            cfg = getattr(getattr(tool, "server", None), "cfg", None)
            if cfg is not None:
                servers.setdefault(cfg.name, []).append(tool)
    out: list[str] = []
    for server, tools in servers.items():
        named = bool((set(server.split("_")) | set(known.get(server, ()))) & words)
        if not named and server not in links:
            continue
        ranked = sorted(tools, key=lambda t: -_fit(words, t))
        if not named:
            ranked = [t for t in ranked if _fit(words, t) >= 2.0]      # a link: only what the goal is about
        out += [t.name for t in ranked[:MAX_PER_SERVER]]
    return out
