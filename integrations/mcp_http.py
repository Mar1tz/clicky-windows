"""MCP servers on the internet - one URL, a whole service (CI-18, remote).

Most services now run their own MCP server: Linear, Notion, Sentry, GitHub,
Atlassian, Stripe - and Zapier or Composio give one URL that reaches
thousands of apps. Nothing to install: HeyTora talks to the URL.

Two transports, both JSON-RPC 2.0 like the local servers (integrations/mcp.py):

* Streamable HTTP (2025-06-18): every message is a POST; the answer comes
  back as JSON or as a short event stream. The server may hand out a
  session id in `Mcp-Session-Id`, sent back on every later request.
* HTTP+SSE (2024-11-05, still what some services offer at ".../sse"): one
  long GET stream; its first event says where to POST, and the answers
  arrive on the stream.

Auth is a header: a key the person pasted (kept in Windows Credential
Manager, never in mcp_servers.json) or an OAuth token from
integrations/mcp_oauth.py, refreshed once when the server says 401.
Transport injectable: tests never go online.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Optional
from urllib.parse import urljoin

import httpx

from .mcp import CALL_S, PROTOCOL, START_S, McpError, ServerConfig, _version, read_result

log = logging.getLogger("tora.mcp")


class AuthNeeded(McpError):
    """The server wants a sign-in (401). `challenge`: its WWW-Authenticate."""

    def __init__(self, server: str, challenge: str = "") -> None:
        super().__init__(f"{server} needs signing in again")
        self.challenge = challenge


class McpHttpServer:
    """A remote server, with the same face as a local one (McpServer)."""

    def __init__(self, cfg: ServerConfig, *, token: Optional[Callable[[bool], Optional[str]]] = None,
                 transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self.cfg = cfg
        self.tools: list[dict] = []
        self._token = token                      # (force_refresh) -> bearer, for OAuth servers
        self._bearer = ""
        self._transport = transport
        self._http: Optional[httpx.AsyncClient] = None
        self._session = ""
        self._ids = 0
        self._started = False
        self.mode = ""                           # "http" or "sse", once started
        # HTTP+SSE only
        self._post_to = ""
        self._pending: dict[int, asyncio.Future] = {}
        self._stream: Optional[asyncio.Task] = None

    @property
    def running(self) -> bool:
        return self._started and (self.mode != "sse" or (self._stream is not None and not self._stream.done()))

    # -- life ---------------------------------------------------------------

    async def start(self) -> list[dict]:
        self._http = httpx.AsyncClient(transport=self._transport, timeout=httpx.Timeout(CALL_S, connect=20),
                                       follow_redirects=True)
        await self._fresh_bearer(False)
        want = self.cfg.transport or ("sse" if self.cfg.url.rstrip("/").endswith("/sse") else "http")
        if want == "http":
            try:
                await self._initialize("http")
            except McpError as exc:
                if not getattr(exc, "old_transport", False):
                    raise
                want = "sse"                        # the spec's way back: try the old transport
        if want == "sse":
            await self._open_stream()
            await self._initialize("sse")
        tools, cursor = [], None
        while True:
            page = await self.request("tools/list", {"cursor": cursor} if cursor else {}, timeout=START_S)
            tools += page.get("tools") or []
            cursor = page.get("nextCursor")
            if not cursor:
                break
        self.tools = tools
        log.info("mcp %s (%s, %s): %d tools", self.cfg.name, self.cfg.url.split("/")[2], self.mode, len(tools))
        return tools

    async def _initialize(self, mode: str) -> None:
        self.mode = mode
        self._started = True
        await self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                          "clientInfo": {"name": "HeyTora", "version": _version()}},
                           timeout=START_S)
        await self._notify("notifications/initialized")

    async def stop(self) -> None:
        if self._stream is not None:
            self._stream.cancel()
        if self._http is not None:
            if self._session and self.mode == "http":
                try:
                    await self._http.delete(self.cfg.url, headers=self._headers())
                except Exception:  # noqa: BLE001 - leaving politely is best effort
                    pass
            await self._http.aclose()
        self._started = False

    # -- calls --------------------------------------------------------------

    async def call(self, tool: str, arguments: dict, timeout: float = CALL_S) -> tuple[bool, str]:
        return read_result(await self.request("tools/call", {"name": tool, "arguments": arguments},
                                              timeout=timeout))

    async def request(self, method: str, params: dict, timeout: float) -> dict:
        if not self._started or self._http is None:
            raise McpError(f"the {self.cfg.name} server is not connected")
        self._ids += 1
        msg = {"jsonrpc": "2.0", "id": self._ids, "method": method, "params": params}
        try:
            answer = await asyncio.wait_for(self._exchange(msg), timeout)
        except asyncio.TimeoutError:
            self._pending.pop(msg["id"], None)
            raise McpError(f"{self.cfg.name} did not answer {method} in {timeout:.0f}s") from None
        except httpx.HTTPError as exc:
            raise McpError(f"{self.cfg.name} could not be reached: {type(exc).__name__}") from None
        if "error" in answer:
            raise McpError(str((answer["error"] or {}).get("message") or answer["error"]))
        return answer.get("result") or {}

    async def _fresh_bearer(self, force: bool) -> str:
        """The OAuth token (the vault's; refreshed when old or `force`) - off the loop."""
        if self._token is not None:
            self._bearer = await asyncio.to_thread(self._token, force) or ""
        return self._bearer

    async def _exchange(self, msg: dict) -> dict:
        for attempt in (0, 1):
            try:
                return await (self._post_sse(msg) if self.mode == "sse" else self._post_http(msg))
            except AuthNeeded:
                if attempt or self._token is None or not await self._fresh_bearer(True):
                    raise                            # a fresh token once; then it is the person's sign-in
        raise AssertionError("unreachable")

    async def _notify(self, method: str) -> None:
        msg = {"jsonrpc": "2.0", "method": method}
        if self.mode == "sse":
            await self._http.post(self._post_to, json=msg, headers=self._headers())
        else:
            await self._http.post(self.cfg.url, json=msg, headers=self._headers(json_too=True))

    def _headers(self, *, json_too: bool = False) -> dict:
        h = {"Accept": "application/json, text/event-stream" if json_too else "text/event-stream",
             **self.cfg.headers}
        if self._bearer:
            h["Authorization"] = f"Bearer {self._bearer}"
        if self._session:
            h["Mcp-Session-Id"] = self._session
        if self.mode == "http" and self._ids > 1:
            h["MCP-Protocol-Version"] = PROTOCOL
        return h

    # Streamable HTTP --------------------------------------------------------

    async def _post_http(self, msg: dict) -> dict:
        async with self._http.stream("POST", self.cfg.url, json=msg, headers=self._headers(json_too=True)) as r:
            if r.status_code == 401:
                raise AuthNeeded(self.cfg.name, r.headers.get("www-authenticate", ""))
            if r.status_code in (400, 404, 405) and msg.get("method") == "initialize":
                err = McpError(f"{self.cfg.name} does not speak Streamable HTTP ({r.status_code})")
                err.old_transport = True
                raise err
            if r.status_code >= 400:
                raise McpError(f"{self.cfg.name} answered {r.status_code}")
            if r.headers.get("mcp-session-id"):
                self._session = r.headers["mcp-session-id"]
            kind = r.headers.get("content-type", "")
            if "text/event-stream" in kind:
                async for event, data in _aevents(r.aiter_lines()):
                    found = _match(data, msg["id"])
                    if found is not None:
                        return found
                raise McpError(f"{self.cfg.name} ended its answer without one")
            return _match((await r.aread()).decode("utf-8", "replace"), msg["id"]) or {}

    # HTTP+SSE ---------------------------------------------------------------

    async def _open_stream(self) -> None:
        self.mode = "sse"
        ready = asyncio.get_running_loop().create_future()
        self._stream = asyncio.ensure_future(self._listen(ready))
        try:
            self._post_to = await asyncio.wait_for(ready, START_S)
        except asyncio.TimeoutError:
            self._stream.cancel()
            raise McpError(f"{self.cfg.name} did not say where to send messages") from None

    async def _listen(self, ready: asyncio.Future) -> None:
        try:
            async with self._http.stream("GET", self.cfg.url, headers=self._headers(),
                                         timeout=httpx.Timeout(None, connect=20)) as r:
                if r.status_code == 401:
                    raise AuthNeeded(self.cfg.name, r.headers.get("www-authenticate", ""))
                if r.status_code >= 400:
                    raise McpError(f"{self.cfg.name} answered {r.status_code}")
                async for event, data in _aevents(r.aiter_lines()):
                    if event == "endpoint" and not ready.done():
                        ready.set_result(urljoin(self.cfg.url, data.strip()))
                        continue
                    try:
                        answer = json.loads(data)
                    except ValueError:
                        continue
                    fut = self._pending.pop(answer.get("id"), None) if isinstance(answer, dict) else None
                    if fut is not None and not fut.done():
                        fut.set_result(answer)
        except Exception as exc:  # noqa: BLE001 - the stream's end is every waiter's end
            if not ready.done():
                ready.set_exception(exc if isinstance(exc, McpError) else McpError(str(exc)[:200]))
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_result({"error": {"message": f"{self.cfg.name} closed the connection"}})
            self._pending.clear()

    async def _post_sse(self, msg: dict) -> dict:
        fut = asyncio.get_running_loop().create_future()
        self._pending[msg["id"]] = fut
        r = await self._http.post(self._post_to, json=msg, headers=self._headers())
        if r.status_code == 401:
            self._pending.pop(msg["id"], None)
            raise AuthNeeded(self.cfg.name, r.headers.get("www-authenticate", ""))
        if r.status_code >= 400:
            self._pending.pop(msg["id"], None)
            raise McpError(f"{self.cfg.name} answered {r.status_code}")
        return await fut


async def _aevents(lines):
    event, data = "message", []
    async for line in lines:
        line = line.rstrip("\r")
        if line == "":
            if data:
                yield event, "\n".join(data)
            event, data = "message", []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        yield event, "\n".join(data)


def _match(text: str, rid: int) -> Optional[dict]:
    """The answer to request `rid` in a JSON body or event: one message or a batch."""
    try:
        got: Any = json.loads(text)
    except ValueError:
        return None
    for m in got if isinstance(got, list) else [got]:
        if isinstance(m, dict) and m.get("id") == rid and ("result" in m or "error" in m):
            return m
    return None
