"""HeyTora's end of the browser companion (CI-02, CI-03, CI-04, CI-05).

The extension (connectors/browser) connects to ws://127.0.0.1:<port> - this
machine only - and must say, first, the per-install token HeyTora wrote
into its config.json when it set it up. The first extension that does is
remembered by its id (its Origin, chrome-extension://<id>, must agree);
from then on only that one is let in. A message over 1 MB closes the
connection.

What it learns: which known service the active tab is (detection - the
host only). What it can ask: read that tab, click in it, type in it - each
one a request HeyTora's tools make, after the person's approval where the
tool needs one (agents/tools/browser_tab_tools.py).
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import secrets
import shutil
import sys
import threading
from pathlib import Path
from typing import Any, Optional

from integrations import providers

log = logging.getLogger("tora.browser")

DEFAULT_PORT = 47124
MAX_BYTES = 1_000_000
CONFIG = "browser_bridge.json"
FILES = ("manifest.json", "background.js", "icon.png", "LICENSE")


def source_dir() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base / "connectors" / "browser"


class BrowserBridge:
    def __init__(self, folder: Path, port: int = DEFAULT_PORT) -> None:
        self.folder = Path(folder)
        self.want_port = port
        self.port = 0
        self._settings = self._load()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server = None
        self._socket = None
        self._pending: dict[int, asyncio.Future] = {}
        self._ids = itertools.count(1)
        self._ready = threading.Event()
        self.tab: Optional[dict] = None            # {tabId, host, service} of the active known tab
        self.connected = False

    # -- settings: the token and the one extension let in ------------------------------------

    def _load(self) -> dict:
        try:
            data = json.loads((self.folder / CONFIG).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data.setdefault("token", secrets.token_urlsafe(32))
        data.setdefault("extension_id", "")
        return data

    def _save(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / CONFIG).write_text(json.dumps({**self._settings, "port": self.port}), encoding="utf-8")

    @property
    def token(self) -> str:
        return self._settings["token"]

    @property
    def extension_id(self) -> str:
        return self._settings["extension_id"]

    # -- running -------------------------------------------------------------------------

    def start(self) -> int:
        threading.Thread(target=self._run, name="browser-bridge", daemon=True).start()
        self._ready.wait(5)
        return self.port

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())
        self._ready.set()
        self._loop.run_forever()

    async def _serve(self) -> None:
        from websockets.asyncio.server import serve
        for port in (self.want_port, 0):
            try:
                self._server = await serve(self._client, "127.0.0.1", port, max_size=MAX_BYTES)
                break
            except OSError:
                continue
        self.port = self._server.sockets[0].getsockname()[1] if self._server else 0
        self._save()
        log.info("browser bridge on 127.0.0.1:%d", self.port)

    def stop(self) -> None:
        loop = self._loop
        if loop is None:
            return

        async def close():
            if self._server is not None:
                self._server.close()
            loop.stop()
        asyncio.run_coroutine_threadsafe(close(), loop)

    async def _client(self, ws) -> None:
        origin = ws.request.headers.get("Origin", "") if ws.request else ""
        try:
            hello = json.loads(await asyncio.wait_for(ws.recv(), 10))
        except Exception:  # noqa: BLE001 - no hello, no talk
            await ws.close(1008, "hello first")
            return
        ext = str(hello.get("extension") or "")
        if hello.get("type") != "hello" or not secrets.compare_digest(str(hello.get("token") or ""), self.token):
            await ws.close(1008, "not HeyTora's extension")
            return
        if origin not in (f"chrome-extension://{ext}", f"extension://{ext}") or not ext:
            await ws.close(1008, "origin does not match")
            return
        if self.extension_id and ext != self.extension_id:
            await ws.close(1008, "another extension is paired")
            return
        if not self.extension_id:
            self._settings["extension_id"] = ext            # paired: from now on, only this one
            self._save()
        self._socket, self.connected = ws, True
        await ws.send(json.dumps({"type": "welcome"}))
        log.info("browser companion connected (%s)", hello.get("version", "?"))
        try:
            async for raw in ws:
                self._on_message(json.loads(raw))
        except Exception:  # noqa: BLE001 - a broken connection ends it
            pass
        finally:
            if self._socket is ws:
                self._socket, self.connected, self.tab = None, False, None
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_result({"ok": False, "error": "the browser companion went away"})
            self._pending.clear()

    def _on_message(self, msg: dict) -> None:
        if msg.get("type") == "tab":
            host = str(msg.get("host") or "")
            service = providers.service_for("https://" + host) if host else None
            self.tab = {"tabId": msg.get("tabId"), "host": host, "service": service} if service else None
            return
        fut = self._pending.pop(msg.get("id"), None)
        if fut is not None and not fut.done():
            fut.set_result(msg)

    # -- asking the extension -----------------------------------------------------------------

    async def _ask(self, msg: dict, timeout: float) -> dict:
        if self._socket is None:
            return {"ok": False, "error": "the browser companion is not connected"}
        rid = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._socket.send(json.dumps({**msg, "id": rid}))
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            return {"ok": False, "error": "the browser did not answer in time"}

    def request(self, msg: dict, timeout: float = 15.0) -> dict:
        """From any thread: send `msg` to the extension and wait for its answer."""
        if self._loop is None:
            return {"ok": False, "error": "the browser bridge is not running"}
        return asyncio.run_coroutine_threadsafe(self._ask(msg, timeout), self._loop).result(timeout + 1)

    async def arequest(self, msg: dict, timeout: float = 15.0) -> dict:
        """From another event loop."""
        if self._loop is None:
            return {"ok": False, "error": "the browser bridge is not running"}
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self._ask(msg, timeout), self._loop))


def install(bridge: BrowserBridge, dest: Path) -> Path:
    """Copy the extension to `dest` with its config (port, token, known
    hosts). Browsers load it from there: chrome://extensions or
    edge://extensions -> Developer mode -> Load unpacked -> this folder."""
    dest.mkdir(parents=True, exist_ok=True)
    src = source_dir()
    for name in FILES:
        shutil.copyfile(src / name, dest / name)
    hosts = sorted({h for p in providers.PROVIDERS.values() for h in p.hosts})
    (dest / "config.json").write_text(json.dumps({"port": bridge.port, "token": bridge.token, "hosts": hosts}),
                                      encoding="utf-8")
    return dest


_bridge: Optional[BrowserBridge] = None


def current() -> Optional[BrowserBridge]:
    return _bridge


def set_current(bridge: Optional[BrowserBridge]) -> None:
    global _bridge
    _bridge = bridge
