"""MCP servers behind the broker (CI-18, the Massive Direction Plan).

A server the person added (Claude Desktop's config shape) is started with
no window, its tools listed (pages and all) and registered as
mcp_<server>_<tool>; a tool the server says only reads runs on the job's
policy, any other asks every time; a job that names the server gets its
tools; one server failing never stops the others. The server here is a
small Python script on stdio - nothing goes online.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from agents.core import ApprovalRequest, RiskLevel, ToolCall, ToolContext
from agents.tools import Registry
from agents.tools import mcp_tools
from integrations import mcp

FAKE = str(Path(__file__).with_name("fake_mcp_server.py"))


def _config(tmp_path, servers):
    (tmp_path / mcp.CONFIG).write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return mcp.load_config(tmp_path)


def test_claude_desktops_config_shape_is_read(tmp_path):
    cfgs = _config(tmp_path, {"GitHub": {"command": "npx", "args": ["-y", "server-github"], "env": {"T": "x"}},
                              "broken": {"args": ["no command"]}})
    assert [(c.name, c.command, c.args, c.env) for c in cfgs] == [("github", "npx", ["-y", "server-github"],
                                                                   {"T": "x"})]


def test_a_server_is_started_and_its_tools_go_through_the_registry(tmp_path):
    cfgs = _config(tmp_path, {"github": {"command": sys.executable, "args": [FAKE]},
                              "dead": {"command": str(tmp_path / "no-such-program.exe")}})

    async def go():
        hub = mcp.McpHub(cfgs)
        await hub.start()
        try:
            assert "dead" in hub.failed and hub.servers["github"].running, "one failing never stops the others"
            reg = Registry([])
            names = mcp_tools.attach(reg, hub)
            assert names == ["mcp_github_search_issues", "mcp_github_create_issue"], "both pages of tools"
            assert reg.get("mcp_github_search_issues").spec.risk is RiskLevel.MEDIUM
            assert reg.get("mcp_github_create_issue").always_ask

            asked = []

            async def approver(req: ApprovalRequest):
                asked.append(req.reason)
                return True
            ctx = ToolContext(workspace=str(tmp_path))
            ctx.approver = approver
            ctx.policy.max_auto_risk = RiskLevel.MEDIUM
            found = await reg.execute(ToolCall("mcp_github_search_issues", {"q": "crash"}), ctx)
            assert found.ok and found.output == "2 issues match 'crash'" and not asked
            made = await reg.execute(ToolCall("mcp_github_create_issue", {"title": "Login fails"}), ctx)
            assert made.ok and "opened #12" in made.output and asked == [
                "Use github: create_issue with {'title': 'Login fails'}"]
            bad = await reg.execute(ToolCall("mcp_github_search_issues", {}), ctx)
            assert not bad.ok and "invalid arguments" in bad.error, "its schema is checked like any tool's"
            picked = mcp_tools.for_goal("open an issue on GitHub about the crash", reg)
            assert sorted(picked) == sorted(names) and picked[0] == "mcp_github_create_issue", "best fit first"
            assert mcp_tools.for_goal("write a poem", reg) == []
        finally:
            await hub.stop()
    asyncio.run(go())


def test_a_job_that_names_the_server_gets_its_tools(tmp_path):
    from agents.core import Task
    from tests.agents.test_manager import make_manager
    cfgs = _config(tmp_path, {"github": {"command": sys.executable, "args": [FAKE]}})
    mgr = make_manager(tmp_path, tools=Registry([]))
    try:
        hub = mcp.McpHub(cfgs)
        mgr.runtime.call(hub.start())
        mcp_tools.attach(mgr.tools, hub)
        spec = mgr._default_spec(Task(title="t", goal="list my github issues about login", workspace=str(tmp_path)))
        assert "mcp_github_search_issues" in spec.tools
        plain = mgr._default_spec(Task(title="t", goal="build a website", workspace=str(tmp_path)))
        assert plain.tools == []
        mgr.runtime.call(hub.stop())
    finally:
        mgr.stop()
