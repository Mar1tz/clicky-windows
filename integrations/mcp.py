"""MCP servers behind the broker (CI-18) - integrations by the thousand.

A Model Context Protocol server is a small program that offers tools - for
GitHub, Notion, Slack, a database, a folder - over JSON-RPC on its stdin and
stdout. The person lists the ones they want in mcp_servers.json in the data
folder, in the same shape Claude Desktop uses:

    {"mcpServers": {"github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
                               "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "..."}}}}

or, for a server on the internet, its URL (integrations/mcp_http.py):

    {"mcpServers": {"linear": {"url": "https://mcp.linear.app/mcp", "auth": "oauth"}}}

A key or a secret URL is never written here: "secret": "vault" says the
URL and headers are in Windows Credential Manager (integrations/vault.py).

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
    command: str = ""
    args: list = field(default_factory=list)
    env: dict = field(default_factory=dict)
    enabled: bool = True
    url: str = ""                    # a remote server (integrations/mcp_http.py)
    headers: dict = field(default_factory=dict)
    transport: str = ""              # "http", "sse", or "" - chosen by the URL
    auth: str = ""                   # "oauth": integrations/mcp_oauth.py signs in
    secret: str = ""                 # "vault": URL and headers are in Credential Manager

    @property
    def remote(self) -> bool:
        return bool(self.url or self.secret)


def server_id(name: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", str(name).lower())[:24]


def load_config(folder: Path) -> list[ServerConfig]:
    """mcp_servers.json - Claude Desktop's shape ({"mcpServers": {...}}),
    with Cursor's and VS Code's "url" entries for servers on the internet."""
    try:
        data = json.loads((Path(folder) / CONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for name, spec in (data.get("mcpServers") or data.get("servers") or {}).items():
        if isinstance(spec, dict) and (spec.get("command") or spec.get("url") or spec.get("secret")):
            out.append(load_config_entry(name, spec))
    return out


def load_config_entry(name: str, spec: dict) -> ServerConfig:
    """One entry of mcp_servers.json - also a server added while HeyTora runs."""
    kind = str(spec.get("type") or spec.get("transport") or "").lower()
    return ServerConfig(server_id(name), str(spec.get("command") or ""),
                        [str(a) for a in spec.get("args") or []],
                        {str(k): str(v) for k, v in (spec.get("env") or {}).items()},
                        bool(spec.get("enabled", True)),
                        url=str(spec.get("url") or ""),
                        headers={str(k): str(v) for k, v in (spec.get("headers") or {}).items()},
                        transport="sse" if kind == "sse" else "http" if kind in ("http", "streamable-http") else "",
                        auth=str(spec.get("auth") or ""), secret=str(spec.get("secret") or ""))


def save_server(folder: Path, name: str, entry: dict) -> None:
    """Add or replace one server in mcp_servers.json, keeping the rest as written."""
    path = Path(folder) / CONFIG
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.setdefault("mcpServers", {})[name] = entry
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def remove_server(folder: Path, name: str) -> bool:
    path = Path(folder) / CONFIG
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    gone = (data.get("mcpServers") or {}).pop(name, None) is not None
    if gone:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return gone


def read_result(result: dict) -> tuple[bool, str]:
    """(ok, text) from a tools/call result: its text content, or its error."""
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
        return read_result(await self.request("tools/call", {"name": tool, "arguments": arguments},
                                              timeout=timeout))

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


def make_server(cfg: ServerConfig, *, transport=None):
    """A local server (a program) or a remote one (a URL) for this entry."""
    if not cfg.remote:
        return McpServer(cfg)
    from . import mcp_oauth
    from .mcp_http import McpHttpServer
    if cfg.secret == "vault":
        from .vault import vault
        held = vault().get(mcp_oauth.vault_ref(cfg.name)) or {}
        cfg.url = cfg.url or str(held.get("url") or "")
        cfg.headers = {**cfg.headers, **{str(k): str(v) for k, v in (held.get("headers") or {}).items()}}
    token = (lambda force: mcp_oauth.token(cfg.name, force=force)) if cfg.auth == "oauth" else None
    return McpHttpServer(cfg, token=token, transport=transport)


class McpHub:
    """Every configured server, started together, on one event loop."""

    def __init__(self, configs: list[ServerConfig]) -> None:
        self.servers = {c.name: make_server(c) for c in configs if c.enabled}
        self.failed: dict[str, str] = {}

    async def start(self) -> None:
        await asyncio.gather(*(self._start_one(n, s) for n, s in self.servers.items()))

    async def _start_one(self, name, server) -> bool:
        try:
            await server.start()
            self.failed.pop(name, None)
            return True
        except Exception as exc:  # noqa: BLE001 - one bad server never stops the others
            self.failed[name] = str(exc)[:200]
            log.warning("mcp %s did not start: %s", name, exc)
            return False

    async def add(self, server) -> bool:
        """One more server while HeyTora runs ("connect Linear"); the old one of that name goes."""
        old = self.servers.pop(server.cfg.name, None)
        if old is not None:
            await old.stop()
        self.servers[server.cfg.name] = server
        return await self._start_one(server.cfg.name, server)

    async def remove(self, name: str) -> bool:
        server = self.servers.pop(name, None)
        if server is not None:
            await server.stop()
        return server is not None

    async def stop(self) -> None:
        await asyncio.gather(*(s.stop() for s in self.servers.values()))
