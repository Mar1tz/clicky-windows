"""The person's own browser tab, through HeyTora's extension (CI-04, CI-05).

Reading needs the extension connected; every click and every typing is
asked first, one action at a time; a password, code or card field is
refused (by the extension; here its answer is passed through).
"""
from __future__ import annotations

import asyncio

from agents.core import ApprovalRequest, RiskLevel, ToolCall, ToolContext
from agents.tools import default_registry
from integrations import browser_bridge


class FakeBridge:
    connected = True

    def __init__(self):
        self.asked = []

    async def arequest(self, msg, timeout=15.0):
        self.asked.append(msg)
        if msg["type"] == "read":
            return {"ok": True, "title": "Inbox - Gmail", "text": "Primary",
                    "fields": [{"ref": "h1", "tag": "input", "type": "text", "label": "Search mail", "value": ""},
                               {"ref": "h2", "tag": "input", "type": "password", "label": "Password", "secret": True}]}
        if msg.get("ref") == "h2":
            return {"ok": False, "error": "that is a password, code or card field - it stays yours to fill"}
        return {"ok": True}


def _ctx(tmp_path, answers):
    asked = []

    async def approver(req: ApprovalRequest):
        asked.append(req)
        return answers
    ctx = ToolContext(workspace=str(tmp_path))
    ctx.approver = approver
    ctx.policy.max_auto_risk = RiskLevel.MEDIUM
    return ctx, asked


def _run(call, ctx):
    return asyncio.run(default_registry().execute(call, ctx))


def test_reading_their_tab(tmp_path, monkeypatch):
    bridge = FakeBridge()
    monkeypatch.setattr(browser_bridge, "_bridge", bridge)
    ctx, asked = _ctx(tmp_path, True)
    r = _run(ToolCall("tab_read", {}), ctx)
    assert r.ok and "Tab: Inbox - Gmail" in r.output and "h1: input/text 'Search mail'" in r.output
    assert "[password/code/card - theirs to fill]" in r.output and not asked


def test_every_click_and_typing_is_asked_first(tmp_path, monkeypatch):
    bridge = FakeBridge()
    monkeypatch.setattr(browser_bridge, "_bridge", bridge)
    ctx, asked = _ctx(tmp_path, False)
    r = _run(ToolCall("tab_type", {"ref": "h1", "text": "invoice March", "what": "Search mail"}), ctx)
    assert not r.ok and "permission denied" in r.error and asked[0].reason.startswith("Type 'invoice March'")
    assert not [m for m in bridge.asked if m["type"] == "type"], "nothing typed without the yes"
    ctx, asked = _ctx(tmp_path, True)
    assert _run(ToolCall("tab_click", {"ref": "h1", "what": "Search"}), ctx).ok and asked


def test_a_secret_field_stays_theirs(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_bridge, "_bridge", FakeBridge())
    ctx, _ = _ctx(tmp_path, True)
    r = _run(ToolCall("tab_type", {"ref": "h2", "text": "hunter2"}), ctx)
    assert not r.ok and "stays yours" in r.error


def test_without_the_extension_it_says_how(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_bridge, "_bridge", None)
    ctx, _ = _ctx(tmp_path, True)
    r = _run(ToolCall("tab_read", {}), ctx)
    assert not r.ok and "set up the browser extension" in r.error
