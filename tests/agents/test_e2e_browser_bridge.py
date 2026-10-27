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
        page = bridge.request({"type": "read", "tabId": None}, timeout=3)      # the tab they are looking at
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


# -- BR-03: job tabs -------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_a_job_works_only_in_its_own_tabs_and_closes_only_them():
    out = json.loads(subprocess.run(["node", str(HARNESS.parent / "spaces.js")], capture_output=True, text=True,
                                    timeout=15).stdout)
    assert out["opened"] and out["badUrl"] is False and out["ownTab"] and out["activeTab"]
    for refused in (out["otherJob"], out["theirTab"], out["noJob"]):
        assert "not this job's" in refused
    assert out["spaces"] == [{"tabId": 50, "job": "j1", "url": "https://shop.example.com/orders", "alive": True},
                             {"tabId": 51, "job": "j1", "url": "https://shop.example.com/invoice", "alive": False}]
    assert out["closed"] == 1 and out["theirsStill"] and out["left"] == 0


def test_the_bridge_keeps_the_job_tabs_and_hears_one_taken_back(tmp_path):
    b = BrowserBridge(tmp_path / "data", port=0)
    taken = []
    b.on_taken = lambda job, tab: taken.append((job, tab))
    b._on_message({"type": "spaces", "spaces": [{"tabId": 50, "job": "j1", "url": "u", "alive": True},
                                                 {"tabId": 51, "job": "j1", "url": "u2", "alive": False}]})
    assert b.job_tab("j1") == 50 and b.job_tab("j2") is None, "a gone tab is never worked in"
    b._on_message({"type": "taken", "tabId": 50, "job": "j1"})
    b._on_message({"type": "gone", "tabId": 50, "job": "j1"})
    assert taken == [("j1", 50)] and b.job_tab("j1") is None


def test_tab_tools_work_in_the_jobs_own_tab(tmp_path, monkeypatch):
    from agents.core import ToolContext
    from agents.tools import browser_tab_tools as tt
    from integrations import browser_bridge
    b = BrowserBridge(tmp_path / "data", port=0)
    b.connected = True
    sent = []

    async def ask(msg, timeout=15.0):
        sent.append(msg)
        return {"ok": True, "title": "t", "fields": [], "text": ""}
    monkeypatch.setattr(b, "arequest", ask)
    monkeypatch.setattr(browser_bridge, "_bridge", b)
    ctx = ToolContext(workspace=str(tmp_path), task_id="j1")
    asyncio.run(tt.TabRead().run({}, ctx))
    b.spaces[50] = {"job": "j1", "url": "u", "alive": True}
    asyncio.run(tt.TabRead().run({}, ctx))
    assert sent[0]["tabId"] is None and sent[1] == {"type": "read", "tabId": 50, "job": "j1"}


def test_the_doctor_says_what_is_wrong(tmp_path, monkeypatch):
    import health
    from integrations import browser_bridge
    monkeypatch.setattr(browser_bridge, "_bridge", None)
    assert health.browser_doctor()[2] == "bad"
    b = BrowserBridge(tmp_path / "data", port=0)
    b.port = 47124
    monkeypatch.setattr(browser_bridge, "_bridge", b)
    assert health.browser_doctor()[2] == "warn" and "not connected" in health.browser_doctor()[1]
    b.connected, b.version = True, "0.0.1"
    assert "reload it" in health.browser_doctor()[1]
    b.version = browser_bridge.packaged_version()
    assert health.browser_doctor()[2] == "ok"
