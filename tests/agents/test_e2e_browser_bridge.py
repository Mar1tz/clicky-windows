"""The browser companion's bridge (CI-02..CI-05, the Massive Direction Plan).

127.0.0.1 only; the token from its config first; the first extension is
paired by id and its Origin must agree; nothing over 1 MB. It tells HeyTora
only which known service the active tab is; it reads and acts when asked.
The real background.js is driven under Node against the real bridge.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from integrations.browser_bridge import BrowserBridge, install

EXT = "abcdefghijklmnopabcdefghijklmnop"
HARNESS = Path(__file__).resolve().parents[2] / "connectors" / "browser" / "test" / "harness.js"


@pytest.fixture
def bridge(tmp_path):
    b = BrowserBridge(tmp_path / "data", port=0)
    b.start()
    yield b
    b.stop()


async def _talk(port, hello, origin=f"chrome-extension://{EXT}", then=None):
    from websockets.asyncio.client import connect
    async with connect(f"ws://127.0.0.1:{port}/", origin=origin) as ws:
        await ws.send(json.dumps(hello))
        try:
            first = json.loads(await asyncio.wait_for(ws.recv(), 3))
        except Exception as exc:  # noqa: BLE001
            return {"closed": getattr(exc, "rcvd", None) and exc.rcvd.reason}
        if then:
            return await then(ws, first)
        return first


def _hello(bridge, ext=EXT):
    return {"type": "hello", "token": bridge.token, "extension": ext, "version": "0.1.0"}


def test_only_heytoras_extension_gets_in(bridge):
    bad = asyncio.run(_talk(bridge.port, {**_hello(bridge), "token": "guess"}))
    assert bad == {"closed": "not HeyTora's extension"}
    wrong_origin = asyncio.run(_talk(bridge.port, _hello(bridge), origin="https://evil.example"))
    assert wrong_origin == {"closed": "origin does not match"}
    assert asyncio.run(_talk(bridge.port, _hello(bridge))) == {"type": "welcome"}
    assert bridge.extension_id == EXT, "paired on first use"
    other = "z" * 32
    assert asyncio.run(_talk(bridge.port, _hello(bridge, other), origin=f"chrome-extension://{other}")) == \
        {"closed": "another extension is paired"}


def test_detection_says_the_service_only_for_known_hosts(bridge):
    async def then(ws, first):
        await ws.send(json.dumps({"type": "tab", "tabId": 3, "host": "mail.google.com"}))
        await asyncio.sleep(0.1)
        seen = dict(bridge.tab or {})
        await ws.send(json.dumps({"type": "tab", "tabId": 4, "host": "mail.google.com.example.test"}))
        await asyncio.sleep(0.1)
        return seen, bridge.tab
    seen, after = asyncio.run(_talk(bridge.port, _hello(bridge), then=then))
    assert seen == {"tabId": 3, "host": "mail.google.com", "service": "gmail"} and after is None


def test_a_request_is_answered_and_a_huge_message_closes_it(bridge):
    answers = {}

    async def then(ws, first):
        def ask():
            answers["read"] = bridge.request({"type": "read", "tabId": 3}, timeout=3)
        t = threading.Thread(target=ask)
        t.start()
        req = json.loads(await asyncio.wait_for(ws.recv(), 3))
        await ws.send(json.dumps({"id": req["id"], "ok": True, "title": "Inbox"}))
        await asyncio.to_thread(t.join, 3)
        try:
            await ws.send("x" * 1_100_000)
            await asyncio.wait_for(ws.recv(), 3)
        except Exception as exc:  # noqa: BLE001
            return type(exc).__name__
    closed = asyncio.run(_talk(bridge.port, _hello(bridge), then=then))
    assert answers["read"]["title"] == "Inbox" and closed.startswith("ConnectionClosed")


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
@pytest.mark.parametrize("url,service", [("https://mail.google.com/mail/u/0/#inbox", "gmail"),
                                          ("https://example.com/", None)])
def test_the_real_extension_end_to_end(bridge, tmp_path, url, service):
    folder = install(bridge, tmp_path / "ext")
    cfg = json.loads((folder / "config.json").read_text())
    assert cfg["port"] == bridge.port and "mail.google.com" in cfg["hosts"]
    proc = subprocess.Popen(["node", str(HARNESS), str(folder / "config.json"), url, EXT],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            env={**__import__("os").environ, "HARNESS_S": "3"})
    deadline = time.time() + 3
    while time.time() < deadline and not bridge.connected:
        time.sleep(0.05)
    time.sleep(0.4)
    assert bridge.connected, proc.stderr.read() if proc.poll() is not None else "not connected"
    assert (bridge.tab or {}).get("service") == service
    if service:
        page = bridge.request({"type": "read", "tabId": 7}, timeout=3)
        assert page["ok"] and page["title"].endswith("Gmail") and page["fields"][0]["label"] == "Search mail"
    out, _ = proc.communicate(timeout=5)
    lines = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    if service:
        assert any(l.get("executed") == "heytoraRead" for l in lines)


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_password_code_and_card_fields_stay_theirs():
    script = (
        "const e=require(process.argv[1]);const s=(a)=>e.isSecretField(a);"
        "console.log(JSON.stringify([s({type:'password'}),s({autocomplete:'one-time-code'}),"
        "s({name:'cardnumber'}),s({label:'CVV'}),s({id:'otp'}),s({label:'Email address'}),s({name:'q'})]))")
    out = subprocess.run(["node", "-e", script, str(HARNESS.parents[1] / "background.js")],
                         capture_output=True, text=True, timeout=10).stdout
    assert json.loads(out) == [True, True, True, True, True, False, False]
