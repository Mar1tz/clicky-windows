"""The connector gateway: where an app's connector reaches HeyTora.

A TCP server on 127.0.0.1 only, one JSON-RPC message per line. The design
names a WebSocket or a named pipe; plain lines over a loopback socket are
the same messages with nothing to install on either side - Node's `net`
is in every VS Code fork's extension host, whatever its Node version, and
asyncio's server is stdlib here.

Who may talk: the first message must be `hello` carrying the per-install
token from the discovery file (devlink.json in HeyTora's data folder,
which only this Windows user can read), compared in constant time. A
browser tab can send bytes to a localhost port, so anything that looks
like HTTP is closed at once, as is a first message that is not a hello or
any line over MAX_LINE_BYTES. Nothing is answered before the hello.

The gateway authenticates and frames; what a message means is the hub's.
"""
from __future__ import annotations

import asyncio
import hmac
import itertools
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any, Optional

from . import protocol as P

_log = logging.getLogger("tora.devlink")

DEFAULT_PORT = 47123
HELLO_TIMEOUT_S = 5.0
REQUEST_TIMEOUT_S = 4.0
DISCOVERY = "devlink.json"
_HTTP = (b"GET ", b"POST ", b"PUT ", b"HEAD ", b"OPTIONS ", b"DELETE ", b"PATCH ", b"CONNECT ")


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class Connection:
    """One connected app window (VS Code runs one extension host per window)."""

    _ids = itertools.count(1)

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader, self.writer = reader, writer
        self.id = next(self._ids)
        self.source = "vscode"
        self.app = self.app_version = self.connector_version = ""
        self.workspace_id = self.name = ""
        self.folders: list[str] = []
        self.trusted = True
        self.remote = ""
        self._pending: dict[int, asyncio.Future] = {}
        self._next = itertools.count(1)
        self._lock = asyncio.Lock()
        self.closed = False
        self.focused_at = 0.0           # when its window last had the focus

    async def send(self, message: dict) -> None:
        if self.closed:
            return
        async with self._lock:
            try:
                self.writer.write(P.encode(message))
                await self.writer.drain()
            except (ConnectionError, OSError):
                self.closed = True

    async def notify(self, method: str, params: Optional[dict] = None) -> None:
        await self.send(P.notify(method, params))

    async def request(self, method: str, params: Optional[dict] = None,
                      timeout: float = REQUEST_TIMEOUT_S) -> Any:
        """Ask the connector; its result, or RpcError (its error, a timeout,
        or the window having closed)."""
        if self.closed:
            raise RpcError(P.INVALID_REQUEST, "that editor window has closed")
        rid = next(self._next)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self.send(P.request(rid, method, params))
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise RpcError(P.INVALID_REQUEST, f"the editor did not answer {method} in {timeout:.0f}s") from None
        finally:
            self._pending.pop(rid, None)

    def answered(self, message: dict) -> None:
        fut = self._pending.get(message.get("id"))
        if fut is None or fut.done():
            return
        if "error" in message:
            err = message["error"] or {}
            fut.set_exception(RpcError(int(err.get("code", -32000)), str(err.get("message", "error"))))
        else:
            fut.set_result(message.get("result"))

    def close(self) -> None:
        self.closed = True
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RpcError(P.INVALID_REQUEST, "that editor window has closed"))
        try:
            self.writer.close()
        except Exception:  # noqa: BLE001
            pass

    def describe(self) -> dict:
        return {"id": self.id, "app": self.app, "app_version": self.app_version, "workspace_id": self.workspace_id,
                "name": self.name, "folders": self.folders, "trusted": self.trusted, "remote": self.remote,
                "connector_version": self.connector_version}


class Gateway:
    """Listens, authenticates, frames. `hub` gets on_hello / on_event /
    on_request / on_close."""

    def __init__(self, hub: Any, folder: Path | str, port: int = DEFAULT_PORT):
        self.hub = hub
        self.folder = Path(folder)
        self.want_port = port
        self.port = 0
        self.token = self._token()
        self.connections: list[Connection] = []
        self._server: Optional[asyncio.base_events.Server] = None

    def _token(self) -> str:
        """The per-install token: made once, kept in the discovery file."""
        try:
            saved = json.loads((self.folder / DISCOVERY).read_text(encoding="utf-8")).get("token")
            if isinstance(saved, str) and len(saved) >= 32:
                return saved
        except (OSError, ValueError):
            pass
        return secrets.token_urlsafe(32)

    async def start(self) -> int:
        for port in (self.want_port, 0):
            try:
                self._server = await asyncio.start_server(self._serve, "127.0.0.1", port,
                                                          limit=P.MAX_LINE_BYTES)
                break
            except OSError:
                if port == 0:
                    raise
                _log.info("devlink: port %d taken - another one", port)
        self.port = self._server.sockets[0].getsockname()[1]
        self._write_discovery()
        _log.info("devlink: listening on 127.0.0.1:%d", self.port)
        return self.port

    def _write_discovery(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / DISCOVERY
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"port": self.port, "token": self.token, "pid": os.getpid(),
                                   "protocol": P.PROTOCOL_VERSION}), encoding="utf-8")
        os.replace(tmp, path)

    async def stop(self) -> None:
        for conn in list(self.connections):
            conn.close()
        if self._server is not None:
            self._server.close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), 2.0)
            except asyncio.TimeoutError:
                pass
        try:   # the token stays (it is per install); the port and pid go
            path = self.folder / DISCOVERY
            data = json.loads(path.read_text(encoding="utf-8"))
            path.write_text(json.dumps({"token": data.get("token"), "protocol": P.PROTOCOL_VERSION}), encoding="utf-8")
        except (OSError, ValueError):
            pass

    async def broadcast(self, method: str, params: dict, workspace_id: Optional[str] = None) -> None:
        for conn in list(self.connections):
            if workspace_id is None or conn.workspace_id == workspace_id:
                await conn.notify(method, params)

    # ── one connection ─────────────────────────────────────────────────────

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        conn = Connection(reader, writer)
        try:
            if not await self._hello(conn):
                return
            self.connections.append(conn)
            while not conn.closed:
                line = await reader.readline()
                if not line:
                    break
                await self._dispatch(conn, line)
        except (asyncio.LimitOverrunError, ValueError):
            _log.warning("devlink: a message over %d bytes - connection closed", P.MAX_LINE_BYTES)
        except (ConnectionError, OSError, asyncio.IncompleteReadError):
            pass
        finally:
            if conn in self.connections:
                self.connections.remove(conn)
                try:
                    self.hub.on_close(conn)
                except Exception:  # noqa: BLE001
                    _log.exception("devlink: on_close failed")
            conn.close()

    async def _hello(self, conn: Connection) -> bool:
        try:
            line = await asyncio.wait_for(conn.reader.readline(), HELLO_TIMEOUT_S)
        except (asyncio.TimeoutError, asyncio.LimitOverrunError, ValueError, ConnectionError, OSError):
            return False
        if not line or line.startswith(_HTTP):
            return False
        try:
            msg = json.loads(line)
        except ValueError:
            return False
        params = msg.get("params") if isinstance(msg, dict) else None
        if not (isinstance(msg, dict) and msg.get("method") == "hello" and isinstance(params, dict)
                and hmac.compare_digest(str(params.get("token", "")).encode(), self.token.encode())):
            await conn.send(P.error(msg.get("id") if isinstance(msg, dict) else None, P.NOT_AUTHORISED,
                                    "not authorised"))
            _log.warning("devlink: a connection without the token - refused")
            return False
        try:
            answer = self.hub.on_hello(conn, params)
        except RpcError as e:
            await conn.send(P.error(msg.get("id"), e.code, e.message))
            return False
        await conn.send(P.result(msg.get("id"), answer))
        return True

    async def _dispatch(self, conn: Connection, line: bytes) -> None:
        try:
            msg = json.loads(line)
        except ValueError:
            await conn.send(P.error(None, P.PARSE_ERROR, "not JSON"))
            return
        if not isinstance(msg, dict):
            return
        method = msg.get("method")
        if method is None:
            conn.answered(msg)                                   # a response to one of ours
            return
        params = msg.get("params") or {}
        if "id" not in msg:                                      # a notification: events
            if method == "event":
                self.hub.on_event(conn, params)
            elif method == "events" and isinstance(params.get("events"), list):
                for one in params["events"][:500]:
                    self.hub.on_event(conn, one)
            return
        try:
            value = await self.hub.on_request(conn, method, params)
            await conn.send(P.result(msg["id"], value))
        except RpcError as e:
            await conn.send(P.error(msg["id"], e.code, e.message))
        except Exception as e:  # noqa: BLE001 - never take the connection down
            _log.exception("devlink: %s failed", method)
            await conn.send(P.error(msg["id"], -32000, str(e)[:200]))
