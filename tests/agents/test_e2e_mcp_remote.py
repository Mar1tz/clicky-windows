"""MCP servers on the internet, and connecting a service by name.

A remote server speaks JSON-RPC over Streamable HTTP (answers as JSON or a
short event stream, a session id carried) or the older HTTP+SSE; one that
says 401 is signed in to with the MCP authorization flow (discovery,
dynamic registration, PKCE, `resource`), its tokens only in the vault and
refreshed once on a 401. "Connect GitHub": a key copied, checked, kept in
the vault, wiped from the clipboard - never written to mcp_servers.json.
Every server here is an httpx MockTransport: nothing goes online.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from agents.tools import Registry, mcp_tools
from integrations import catalog, mcp, mcp_http, mcp_oauth
from integrations.vault import vault

TOOLS = [{"name": "list_issues", "description": "Issues", "annotations": {"readOnlyHint": True},
          "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}}},
         {"name": "create_issue", "description": "New issue", "inputSchema": {"type": "object", "properties": {}}}]


def _answer(msg):
    m = msg.get("method")
    if m == "initialize":
        return {"protocolVersion": mcp.PROTOCOL, "capabilities": {"tools": {}}, "serverInfo": {"name": "fake"}}
    if m == "tools/list":
        return {"tools": TOOLS}
    if m == "tools/call":
        return {"content": [{"type": "text", "text": f"{msg['params']['name']}: {json.dumps(msg['params']['arguments'])}"}]}
    return {}


def _streamable(seen, *, bearer=None):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if bearer is not None and request.headers.get("authorization") != f"Bearer {bearer[0]}":
            return httpx.Response(401, headers={"WWW-Authenticate": 'Bearer resource_metadata="https://mcp.t.example/.well-known/oauth-protected-resource"'})
        if request.method == "DELETE":
            return httpx.Response(200)
        msg = json.loads(request.content)
        if "id" not in msg:
            return httpx.Response(202)
        body = {"jsonrpc": "2.0", "id": msg["id"], "result": _answer(msg)}
        if msg["method"] == "tools/list":            # this one as an event stream
            note = json.dumps({"jsonrpc": "2.0", "method": "notifications/progress", "params": {}})
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=f"data: {note}\n\nevent: message\ndata: {json.dumps(body)}\n\n".encode())
        headers = {"mcp-session-id": "s-1"} if msg["method"] == "initialize" else {}
        return httpx.Response(200, json=body, headers=headers)
    return httpx.MockTransport(handler)


def _remote(url="https://mcp.t.example/mcp", **kw):
    return mcp.load_config_entry("tracker", {"url": url, **kw})


def test_a_streamable_http_server_is_listed_called_and_left(tmp_path):
    seen = []
    server = mcp_http.McpHttpServer(_remote(), transport=_streamable(seen))

    async def go():
        await server.start()
        out = await server.call("list_issues", {"q": "bug"})
        await server.stop()
        return out
    ok, text = asyncio.run(go())
    assert ok and text == 'list_issues: {"q": "bug"}' and server.mode == "http"
    assert [t["name"] for t in server.tools] == ["list_issues", "create_issue"]
    later = [r for r in seen if r.method == "POST"][2:]
    assert all(r.headers["mcp-session-id"] == "s-1" for r in later), "the session id goes back every time"
    assert all(r.headers["mcp-protocol-version"] == mcp.PROTOCOL for r in later)
    assert "text/event-stream" in seen[0].headers["accept"] and "application/json" in seen[0].headers["accept"]
    assert seen[-1].method == "DELETE"


def test_an_old_sse_server_is_spoken_to_its_way():
    """POST to the URL is refused (405): the old transport - a GET stream
    says where to post, and the answers come back on the stream."""
    async def go():
        answers: asyncio.Queue = asyncio.Queue()

        async def stream():
            yield b"event: endpoint\ndata: /messages?session=7\n\n"
            while True:
                body = await answers.get()
                yield f"event: message\ndata: {json.dumps(body)}\n\n".encode()

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream())
            if request.url.path == "/mcp":
                return httpx.Response(405)
            assert request.url.path == "/messages" and request.url.params["session"] == "7"
            msg = json.loads(request.content)
            if "id" in msg:
                await answers.put({"jsonrpc": "2.0", "id": msg["id"], "result": _answer(msg)})
            return httpx.Response(202)

        server = mcp_http.McpHttpServer(_remote(), transport=httpx.MockTransport(handler))
        await server.start()
        out = await server.call("create_issue", {})
        mode = server.mode
        await server.stop()
        return out, mode
    (ok, text), mode = asyncio.run(go())
    assert ok and text == "create_issue: {}" and mode == "sse"


# -- signing in ------------------------------------------------------------------

def _auth_server(seen):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path == "/.well-known/oauth-protected-resource":
            return httpx.Response(200, json={"resource": "https://mcp.t.example/mcp",
                                             "authorization_servers": ["https://auth.t.example"]})
        if path == "/.well-known/oauth-authorization-server":
            return httpx.Response(200, json={"authorization_endpoint": "https://auth.t.example/authorize",
                                             "token_endpoint": "https://auth.t.example/token",
                                             "registration_endpoint": "https://auth.t.example/register",
                                             "code_challenge_methods_supported": ["S256"]})
        if path == "/register":
            body = json.loads(request.content)
            assert body["token_endpoint_auth_method"] == "none" and body["redirect_uris"][0].startswith("http://127.0.0.1")
            return httpx.Response(201, json={"client_id": "dyn-1"})
        if path == "/token":
            form = parse_qs(request.content.decode())
            if form["grant_type"] == ["refresh_token"]:
                return httpx.Response(200, json={"access_token": "at-2", "expires_in": 3600})
            assert form["code"] == ["c-1"] and form["client_id"] == ["dyn-1"] and form["code_verifier"]
            assert form["resource"] == ["https://mcp.t.example/mcp"]
            return httpx.Response(200, json={"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


class _Catcher:
    """The browser and 127.0.0.1 together: the sign-in page answers at once."""

    def __init__(self, handler, opened, deny=False):
        self.handler, self.opened, self.deny = handler, opened, deny

    def start(self):
        return "http://127.0.0.1:5555/heytora/mcp-connected"

    def stop(self):
        pass

    def browse(self, url):
        self.opened.append(url)
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        self.handler({"state": q["state"], "error": "access_denied"} if self.deny
                     else {"state": q["state"], "code": "c-1"})


def _sign_in(name="tracker", deny=False):
    seen, opened, made = [], [], []

    def factory(handler):
        made.append(_Catcher(handler, opened, deny))
        return made[0]
    mcp_oauth.connect(name, "https://mcp.t.example/mcp",
                      'Bearer resource_metadata="https://mcp.t.example/.well-known/oauth-protected-resource"',
                      open_browser=lambda u: made[0].browse(u), transport=_auth_server(seen),
                      catcher_factory=factory)
    return seen, opened


def test_signing_in_registers_heytora_and_keeps_the_tokens_only_in_the_vault():
    seen, opened = _sign_in()
    q = {k: v[0] for k, v in parse_qs(urlsplit(opened[0]).query).items()}
    assert opened[0].startswith("https://auth.t.example/authorize?")
    assert q["client_id"] == "dyn-1" and q["code_challenge_method"] == "S256" and q["resource"] == "https://mcp.t.example/mcp"
    held = vault().get(mcp_oauth.vault_ref("tracker"))
    assert held["access_token"] == "at-1" and held["refresh_token"] == "rt-1" and held["client_id"] == "dyn-1"
    assert mcp_oauth.token("tracker") == "at-1"
    assert mcp_oauth.token("tracker", force=True, transport=_auth_server([])) == "at-2"
    assert vault().get(mcp_oauth.vault_ref("tracker"))["refresh_token"] == "rt-1", "kept when not replaced"


def test_a_no_at_the_sign_in_keeps_nothing():
    with pytest.raises(mcp_oauth.Refused) as no:
        _sign_in(name="other", deny=True)
    assert no.value.denied and vault().get(mcp_oauth.vault_ref("other")) is None


def test_a_401_gets_one_fresh_token_then_works():
    tokens = ["old"]
    refreshed = []

    def token(force):
        if force:
            refreshed.append(1)
            tokens[0] = "new"
        return tokens[0]
    seen = []
    server = mcp_http.McpHttpServer(_remote(auth="oauth"), token=token, transport=_streamable(seen, bearer=["new"]))

    async def go():
        await server.start()
        return await server.call("list_issues", {})
    ok, _ = asyncio.run(go())
    assert ok and refreshed == [1] and seen[-1].headers["authorization"] == "Bearer new"


# -- the catalog and the voice -------------------------------------------------------

def test_services_are_found_by_their_names_and_keys_checked():
    assert catalog.find("jira").id == "atlassian" and catalog.find("my github").id == "github"
    assert catalog.find("Zapier").how == "link" and catalog.find("myspace") is None
    gh = catalog.SERVICES["github"]
    assert catalog.accepts(gh, "github_pat_" + "a" * 30) and not catalog.accepts(gh, "hello there")
    assert catalog.accepts(catalog.SERVICES["zapier"], "https://mcp.zapier.com/api/mcp/s/abc/mcp")
    assert not catalog.accepts(catalog.SERVICES["zapier"], "https://mcp.zapier.com.evil.test/x")
    assert catalog.custom_url_ok("https://mcp.example.com/mcp")
    assert not catalog.custom_url_ok("https://user@evil.test/mcp") and not catalog.custom_url_ok("http://x.com/mcp")


def test_a_job_naming_jira_gets_the_atlassian_tools():
    reg = Registry()
    server = SimpleNamespace(cfg=SimpleNamespace(name="atlassian"), running=True, tools=TOOLS)
    names = mcp_tools.attach_server(reg, server)
    assert mcp_tools.for_goal("move my jira ticket to done", reg) == names
    assert mcp_tools.for_goal("tidy my desktop", reg) == []
    assert mcp_tools.detach(reg, "atlassian") == 2 and not [n for n in reg.names() if n.startswith("mcp_")]


def _manager(monkeypatch, tmp_path, clipboard=""):
    import companion_manager as cm
    from agents.control import system
    import config
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    said, opened, board = [], [], [clipboard]
    mgr._agents = SimpleNamespace(mcp=mcp.McpHub([]), tools=Registry())
    mgr._say_when_idle = said.append
    mgr._connect_in_background = lambda label, work: said.append(work())
    started = []
    mgr._mcp_start = lambda entry, name, gives, label: (mcp.save_server(tmp_path, name, entry),
                                                        started.append(name), f"{label} is connected")[2]
    monkeypatch.setattr(cm.os, "startfile", opened.append)
    monkeypatch.setattr(system, "clipboard_read", lambda limit=20000: board[0])
    monkeypatch.setattr(system, "clipboard_write", lambda text: board.__setitem__(0, text))
    return mgr, said, opened, board, started


def test_connect_github_key_page_then_the_copied_key(monkeypatch, tmp_path):
    key = "github_pat_" + "K" * 40
    mgr, said, opened, board, started = _manager(monkeypatch, tmp_path, clipboard="not a key")
    out = asyncio.run(mgr._tool_connect_account({"service": "github"}))
    assert opened == [catalog.SERVICES["github"].setup] and out["waiting_for_copy"] == "github"
    assert "read a key out loud" in out["note"]
    bad = asyncio.run(mgr._tool_connect_account({"service": "github", "pasted": True}))
    assert "not a GitHub key" in bad["error"] and "not a key" not in json.dumps(bad), "never echoed"
    board[0] = key
    asyncio.run(mgr._tool_connect_account({"service": "github", "pasted": True}))
    assert said == ["GitHub is connected"] and started == ["github"] and board[0] == "", "wiped from the clipboard"
    held = vault().get(mcp_oauth.vault_ref("github"))
    assert held == {"url": "https://api.githubcopilot.com/mcp/", "headers": {"Authorization": f"Bearer {key}"}}
    written = (tmp_path / mcp.CONFIG).read_text(encoding="utf-8")
    assert key not in written and json.loads(written)["mcpServers"]["github"] == {"secret": "vault"}
    cfg = mcp.load_config(tmp_path)[0]
    server = mcp.make_server(cfg)
    assert server.cfg.url == "https://api.githubcopilot.com/mcp/" and server.cfg.headers["Authorization"].endswith(key)


def test_unknown_services_say_what_can_be_connected(monkeypatch, tmp_path):
    mgr, *_ = _manager(monkeypatch, tmp_path)
    out = asyncio.run(mgr._tool_connect_account({"service": "myspace"}))
    assert "No ready connection" in out["error"] and "Zapier" in out["ready"] and "custom" in out["note"]


def test_disconnect_forgets_the_key_and_the_entry(monkeypatch, tmp_path):
    mgr, *_ = _manager(monkeypatch, tmp_path)
    vault().put(mcp_oauth.vault_ref("zapier"), {"url": "https://mcp.zapier.com/x", "headers": {}})
    mcp.save_server(tmp_path, "zapier", {"secret": "vault"})
    mgr._agents.runtime = SimpleNamespace(submit=lambda coro: asyncio.run_coroutine_threadsafe(coro, _loop()))
    out = asyncio.run(mgr._tool_disconnect_account({"service": "zapier"}))
    assert out["ok"] and vault().get(mcp_oauth.vault_ref("zapier")) is None and mcp.load_config(tmp_path) == []


_LOOP = []


def _loop():
    if not _LOOP:
        import threading
        loop = asyncio.new_event_loop()
        threading.Thread(target=loop.run_forever, daemon=True).start()
        _LOOP.append(loop)
    return _LOOP[0]
