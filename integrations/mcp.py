"""MCP servers behind the broker (CI-18) - integrations by the thousand.

A Model Context Protocol server is a small program that offers tools - for
GitHub, Notion, Slack, a database, a folder - over JSON-RPC on its stdin and
stdout. The person lists the ones they want in mcp_servers.json in the data
folder, in the same shape Claude Desktop uses:

    {"mcpServers": {"github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
                               "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "..."}}}}

HeyTora starts each one (no window), asks it for its tools, and hands them
to jobs that name the service (agents/tools/mcp_tools.py) - through the
same registry as every built-in tool: its approvals (a tool that does not
say it only reads asks every time), its time limits and output caps, and
what it returns reaches the model marked as data, never instructions.

No library: the protocol is newline-delimited JSON-RPC 2.0 - initialize,
tools/list, tools/call - and this is a small client of it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("tora.mcp")

PROTOCOL = "2025-06-18"
CONFIG = "mcp_servers.json"
START_S = 60.0              # npx may download the server the first time
CALL_S = 120.0
MAX_LINE = 4_000_000


@dataclass
class ServerConfig:
    name: str
    command: str
    args: list = field(default_factory=list)
    env: dict = field(default_factory=dict)
    enabled: bool = True


def load_config(folder: Path) -> list[ServerConfig]:
    """mcp_servers.json - Claude Desktop's shape ({"mcpServers": {...}})."""
    try:
        data = json.loads((Path(folder) / CONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for name, spec in (data.get("mcpServers") or {}).items():
        if not isinstance(spec, dict) or not spec.get("command"):
            continue
        out.append(ServerConfig(re.sub(r"[^a-z0-9_]", "_", name.lower())[:24], str(spec["command"]),
                                [str(a) for a in spec.get("args") or []],
                                {str(k): str(v) for k, v in (spec.get("env") or {}).items()},
                                bool(spec.get("enabled", True))))
    return out


class McpError(Exception):
    pass


class McpServer:
    """One running server: its process, its tools, calls to them."""

    def __init__(self, cfg: ServerConfig) -> None:
        self.cfg = cfg
        self.tools: list[dict] = []
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._ids = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> list[dict]:
        cmd = [self.cfg.command, *self.cfg.args]
        shell_needed = os.name == "nt" and self.cfg.command.lower() in ("npx", "npm")
        kwargs: dict[str, Any] = {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE,
                                  "stderr": subprocess.DEVNULL, "env": {**os.environ, **self.cfg.env},
                                  "limit": MAX_LINE}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        if shell_needed:
            self._proc = await asyncio.create_subprocess_shell(subprocess.list2cmdline(cmd), **kwargs)
        else:
            self._proc = await asyncio.create_subprocess_exec(*cmd, **kwargs)
        self._reader = asyncio.ensure_future(self._read())
        await self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                          "clientInfo": {"name": "HeyTora", "version": _version()}},
                           timeout=START_S)
        await self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools, cursor = [], None
        while True:
            page = await self.request("tools/list", {"cursor": cursor} if cursor else {}, timeout=START_S)
            tools += page.get("tools") or []
            cursor = page.get("nextCursor")
            if not cursor:
                break
        self.tools = tools
        log.info("mcp %s: %d tools", self.cfg.name, len(tools))
        return tools

    async def call(self, tool: str, arguments: dict, timeout: float = CALL_S) -> tuple[bool, str]:
        """(ok, text) - the tool's text content, or its error."""
        result = await self.request("tools/call", {"name": tool, "arguments": arguments}, timeout=timeout)
        parts = []
        for item in result.get("content") or []:
            if item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif item.get("type") == "resource":
                parts.append(str((item.get("resource") or {}).get("text", "")))
            else:
                parts.append(f"[{item.get('type', 'content')}]")
        if result.get("structuredContent") and not parts:
            parts.append(json.dumps(result["structuredContent"])[:20000])
        return not result.get("isError"), "\n".join(parts)

    async def request(self, method: str, params: dict, timeout: float) -> dict:
        if not self.running:
            raise McpError(f"the {self.cfg.name} server is not running")
        self._ids += 1
        rid = self._ids
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            msg = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            raise McpError(f"{self.cfg.name} did not answer {method} in {timeout:.0f}s") from None
        if "error" in msg:
            raise McpError(str((msg["error"] or {}).get("message") or msg["error"]))
        return msg.get("result") or {}

    async def _send(self, msg: dict) -> None:
        async with self._lock:
            self._proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
            await self._proc.stdin.drain()

    async def _read(self) -> None:
        try:
            while self._proc is not None:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue                        # a server's stray print
                fut = self._pending.pop(msg.get("id"), None) if "id" in msg else None
                if fut is not None and not fut.done():
                    fut.set_result(msg)
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_result({"error": {"message": f"{self.cfg.name} stopped"}})
            self._pending.clear()

    async def stop(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            try:
                self._proc.stdin.close()
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), 5)
            except Exception:  # noqa: BLE001
                pass
        if self._reader is not None:
            self._reader.cancel()


def _version() -> str:
    try:
        from version import __version__
        return __version__
    except Exception:  # noqa: BLE001
        return "dev"


class McpHub:
    """Every configured server, started together, on one event loop."""

    def __init__(self, configs: list[ServerConfig]) -> None:
        self.servers = {c.name: McpServer(c) for c in configs if c.enabled}
        self.failed: dict[str, str] = {}

    async def start(self) -> None:
        async def one(name, server):
            try:
                await server.start()
            except Exception as exc:  # noqa: BLE001 - one bad server never stops the others
                self.failed[name] = str(exc)[:200]
                log.warning("mcp %s did not start: %s", name, exc)
        await asyncio.gather(*(one(n, s) for n, s in self.servers.items()))

    async def stop(self) -> None:
        await asyncio.gather(*(s.stop() for s in self.servers.values()))
