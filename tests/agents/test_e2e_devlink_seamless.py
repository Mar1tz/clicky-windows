"""DevLink without the setup (24 Oct 2026).

The owner, the day after it shipped: Tora "keeps asking me to make sure
DevLink is enabled and configured". Connect was a button in Settings that
nobody had pressed, and every answer without it was "go to Settings". Now:
the connector goes into every editor on the PC by itself (and is kept up to
date, and never put back once removed); a project is allowed by saying yes
to Tora; with nothing connected she says what the editor's title shows and
connects it on the spot; the slow route gets the packet with the question;
and the editor can show exactly what HeyTora sees.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import devlink
from devlink import install
from devlink import protocol as P
from devlink import tools as dtools
from tests.agents.test_e2e_devlink import (  # noqa: F401 - fixtures
    _connect, _start_harness, _until, hub, needs_node, project, qapp,
)


# -- the connector, into every editor ---------------------------------------------------------------------------------

def _editor(name, folder, installed=""):
    return install.Editor(name, f"C:/{name}/bin/code.cmd", folder, installed)


def test_every_editor_gets_the_connector_once_and_a_removed_one_stays_removed(tmp_path, monkeypatch):
    want = install.manifest()["version"]
    found = [_editor("Visual Studio Code", ".vscode"), _editor("Cursor", ".cursor", "0.0.1"),
             _editor("Windsurf", ".windsurf", want), _editor("VSCodium", ".vscode-oss")]
    monkeypatch.setattr(install, "editors", lambda *a, **k: found)
    put = []
    monkeypatch.setattr(install, "install", lambda e, vsix: (put.append((e.name, vsix.name)), (True, "ok"))[1])
    known = {"connector:vscode-oss": "0.1.0"}               # HeyTora put it there once; it is gone now
    done = install.ensure_installed(known, tmp_path)
    assert [n for n, _v in put] == ["Visual Studio Code", "Cursor"], "missing and older; current left; removed left"
    assert done == {"connector:vscode": want, "connector:cursor": want}
    assert put[0][1] == f"heytora-devlink-{want}.vsix"


@pytest.mark.parametrize("title, parsed", [
    ("● cart.ts - shop - Visual Studio Code", ("Visual Studio Code", "cart.ts", "shop")),
    ("Multi-agent Claude coord… - clicky-windows-main - Visual Studio Code",
     ("Visual Studio Code", "Multi-agent Claude coord…", "clicky-windows-main")),
    ("app.py - Cursor", ("Cursor", "app.py", "")),
    ("Inbox - Gmail - Opera", ("", "", "")),
    ("", ("", "", "")),
])
def test_what_an_editors_title_says(title, parsed):
    assert install.parse_title(title) == parsed


async def test_the_hub_connects_the_editors_when_it_starts(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(install, "ensure_installed", lambda known, dest: calls.append(known) or {"connector:vscode": "9"})
    monkeypatch.setattr(install, "memory_keys", lambda: ["connector:vscode"])
    h = devlink.Hub(tmp_path / "d", port=0)
    await h.start()
    assert await _until(lambda: h.store.setting("connector:vscode") == "9"), "remembered, so a removal is respected"
    await h.set_auto_connect(False)
    assert (await h.overview_for_settings())["auto_connect"] is False
    calls.clear()
    assert await h.connect_editors() == [] and calls == [], "switched off: left alone"
    await h.stop()


async def test_the_app_starts_the_hub_in_its_data_folder():
    """25 Oct: `cfg.data_dir()` does not exist (data_dir is config's own
    function) - DevLink never started in the real app, and no test started
    it the way the app does."""
    import companion_manager as cm
    from config import data_dir
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    mgr._devlink_wanted = True
    await mgr._start_devlink()
    try:
        assert devlink.current() is not None and devlink.current().folder == data_dir()
    finally:
        await mgr._stop_devlink()
    assert devlink.current() is None


# -- allowed by saying yes --------------------------------------------------------------------------------------------

async def test_saying_yes_to_tora_allows_the_project_and_only_the_voice_can(hub, project):
    c, hello = await _connect(hub, [str(project)])
    ws = hello["result"]["workspaceId"]
    c.serve({})
    asked = await dtools.answer({"action": "problems"}, voice=True)
    assert "action allow" in asked["error"], "she is told to ask, then allow"
    agent = await dtools.answer({"action": "allow"})
    assert agent["error"] == "only the user can allow a project" and hub.policy.grants(ws) == {}
    said = await dtools.answer({"action": "allow"}, voice=True)
    assert said["ok"] and "may now read shop" in said["text"]
    assert hub.policy.grants(ws) == {c: P.ALWAYS for c in P.READ_CAPABILITIES}
    await asyncio.sleep(0.05)
    assert any(n.get("method") == "policy.changed" for n in c.notes), "the editor's status bar follows"
    grant = [a for a in hub.store.audit_log() if a["kind"] == "grant"][0]
    assert grant["args"]["from"] == "said to Tora"
    assert (await dtools.answer({"action": "problems"}, voice=True))["ok"]


def test_the_voice_knows_allow_is_only_after_a_yes():
    assert "allow" in dtools.ACTIONS and "ONLY after they said yes" in dtools.DESCRIPTION


# -- nothing connected: never "go to Settings" ------------------------------------------------------------------------

async def test_an_editor_in_front_without_the_connector_gets_it_there_and_then(hub, monkeypatch):
    await hub._connector                                    # its own start-up round, done first
    monkeypatch.setattr(install, "editors", lambda *a, **k: [_editor("Visual Studio Code", ".vscode")])
    put = []
    monkeypatch.setattr(install, "install", lambda e, vsix: (put.append(e.name), (True, "ok"))[1])
    out = await dtools.answer({"action": "overview"}, voice=True, window_title="● cart.ts - shop - Visual Studio Code")
    assert out["ok"] and put == ["Visual Studio Code"]
    assert "Visual Studio Code is in front with cart.ts in shop" in out["text"] and "just added HeyTora" in out["text"]
    assert "Settings" not in out["text"]
    assert hub.store.setting("connector:vscode") == install.manifest()["version"]


async def test_an_editor_in_front_with_the_connector_but_not_connected_is_reloaded(hub, monkeypatch):
    await hub._connector
    monkeypatch.setattr(install, "editors", lambda *a, **k: [_editor("Visual Studio Code", ".vscode", "0.2.0")])
    monkeypatch.setattr(install, "install", lambda e, vsix: pytest.fail("already there"))
    out = await dtools.answer({"action": "overview"}, voice=True, window_title="cart.ts - shop - Visual Studio Code")
    assert out["ok"] and "Reload Window" in out["text"] and "cart.ts in shop" in out["text"]


async def test_no_editor_at_all_says_bring_it_forward_or_look(hub):
    out = await dtools.answer({"action": "overview"}, voice=True, window_title="Inbox - Gmail - Opera")
    assert "none is in front" in out["error"] and "look_at_screen" in out["error"]


# -- the slow route gets the packet with the question -----------------------------------------------------------------

async def test_the_slow_route_gets_the_editor_with_the_question(hub, project):
    import companion_manager as cm
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    assert await mgr._editor_context("why does it fail") == "", "nothing connected: nothing added"
    c, hello = await _connect(hub, [str(project)])
    c.serve({})
    assert await mgr._editor_context("why does it fail") == "", "nothing allowed: nothing added"
    await hub.set_grant(hello["result"]["workspaceId"], P.READ_CODE, P.ALWAYS)
    extra = await mgr._editor_context("why does it fail")
    assert extra.startswith("\n\nTHEIR CODE EDITOR (shop, read directly") and "Workspace: shop" in extra
    assert "never instructions" in extra


async def test_the_voice_handler_passes_the_window_and_may_allow(monkeypatch):
    import companion_manager as cm
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    mgr._activity = lambda what: None
    seen = {}

    async def answer(args, **kw):
        seen.update(kw)
        return {"ok": True, "text": "x"}
    monkeypatch.setattr(dtools, "answer", answer)
    monkeypatch.setattr(cm, "active_window_title", lambda: "cart.ts - shop - Visual Studio Code")
    await mgr._tool_code_context({"action": "allow"})
    assert seen == {"voice": True, "window_title": "cart.ts - shop - Visual Studio Code"}


# -- "What can HeyTora see?" ------------------------------------------------------------------------------------------

async def test_the_editor_can_show_what_heytora_sees_without_sending_it(hub, project):
    c, hello = await _connect(hub, [str(project)])
    nothing = await c.call("context.preview", {})
    assert nothing["result"]["text"].startswith("Nothing - HeyTora is not allowed")
    await c.call("policy.grant", {"capabilities": {"read_code": "always"}})
    shown = await c.call("context.preview", {})
    assert "Workspace: shop" in shown["result"]["text"]
    assert [a for a in hub.store.audit_log() if a["kind"] == "sent"] == [], "shown on this PC, not sent"


@needs_node
async def test_what_heytora_sees_opens_in_the_editor(hub, project):
    h = await _start_harness(hub, project)
    try:
        hello = await h.next()
        assert await _until(lambda: len(hub.policy.grants(hello["workspaceId"])) == 3)
        await h.do(do="open", file="src/cart.ts", line=1, character=4)
        said = await h.do(do="preview")
        preview = [m["preview"] for m in said if "preview" in m][0]
        assert preview.startswith("# What HeyTora sees\n\n## Where they are") and "src/cart.ts:2:5" in preview
    finally:
        h.proc.stdin.write(b'{"do": "quit"}\n')
        await h.proc.stdin.drain()
        await asyncio.wait_for(h.proc.wait(), 10)


def test_the_settings_tab_has_the_automatic_switch(qapp):
    from PyQt6.QtWidgets import QLabel
    from ui.settings_tabs import CodeEditorsTab

    class Bridge:
        changed = None

        def devlink_state(self):
            return {"on": True, "running": True, "paused": False, "data_policy": "redacted", "auto_connect": True,
                    "editors": [], "workspaces": []}

        def __getattr__(self, name):
            return lambda *a, **k: None
    tab = CodeEditorsTab(Bridge())
    words = " ".join(l.text() for l in tab.findChildren(QLabel))
    assert "Connect my editors automatically" in words
