"""HeyTora DevLink (devlink/, connectors/vscode): the code editor, read as data.

The design of 23 Oct 2026, phases 1 and 2: a connector inside VS Code
streams what the editor knows - the file and cursor, problems, git, what
terminal commands printed - to a local hub in HeyTora, which keeps the
permissions, redacts secrets, keeps a searchable log and answers Tora's
questions with a small packet instead of a screenshot. Read-only; nothing
is read until a workspace allows it; every answer is in the audit log.

The last tests run the real extension (under Node, against a mock of the
vscode API) talking to the real hub, so the two halves are proven to speak
the same protocol.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

import devlink
from devlink import protocol as P  # noqa: E402
from devlink import tools as dtools
from devlink.context import ContextEngine, fit, index_project, search_code
from devlink.hub import Hub
from devlink.logs import LogHub, clean, level_of, summarise
from devlink.policy import LOCAL_ONLY, Policy, workspace_id
from devlink.redact import MASK, ignore_rules, is_ignored, is_secret_path, redact
from devlink.store import Store

ROOT = Path(__file__).resolve().parents[2]

# Made-up secrets in the shapes the redactor knows - each built from two
# pieces, so no secret scanner takes this file for a leak (GitHub's push
# protection did, 23 Oct).
FAKE = {
    "google": "AIza" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q",
    "openai": "sk-" + "proj-abcdefghijklmnopqrstuvwx1234",
    "anthropic": "sk-" + "ant-api03-abcdefghijklmnopqrstu",
    "github": "ghp" + "_abcdefghijklmnopqrstuvwxyz0123456789",
    "slack": "xox" + "b-1234567890-abcdefghij",
    "stripe": "sk" + "_live_abcdefghijklmnop1234",
    "aws": "AKIA" + "IOSFODNN7EXAMPLE",
    "supabase": "sbp" + "_0123456789abcdef0123456789abcdef01234567",
    "jwt": "eyJ" + "hbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
}
HARNESS = ROOT / "connectors" / "vscode" / "test" / "harness.js"


# -- redaction: before anything is kept or sent ----------------------------------------------------------------------

@pytest.mark.parametrize("secret", list(FAKE.values()), ids=list(FAKE))
def test_known_token_shapes_are_redacted(secret):
    assert secret not in redact(f"value: {secret} end")


@pytest.mark.parametrize("line", [
    'password = "hunter2hunter2"', "DB_PASSWORD=correct-horse-battery", "export API_KEY=abcdef123456",
    '"client_secret": "s3cr3t-value-here"', "postgres://admin:Sup3rS3cret@db.example.com:5432/app",
    "STRIPE=Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MGFiY2RlZmdo",            # random-looking, named nothing secret
])
def test_assignments_and_random_values_are_redacted(line):
    out = redact(line)
    assert MASK in out or "REDACTED" in out, out


def test_a_private_key_block_goes_whole():
    block = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nabc\n-----END RSA PRIVATE KEY-----"
    assert "MIIEow" not in redact("key:\n" + block)


@pytest.mark.parametrize("line", [
    "const total = items.reduce((a, b) => a + b, 0);", "commit 3f2a9c1d8e7b6a5f4e3d2c1b0a9f8e7d6c5b4a3f",
    "id = 550e8400-e29b-41d4-a716-446655440000", "path = src/components/Button/Button.test.tsx",
    "Tests: 1 failed, 12 passed, 13 total",
])
def test_ordinary_code_and_output_is_left_alone(line):
    assert redact(line) == line


@pytest.mark.parametrize("path, secret", [
    (".env", True), ("app/.env.local", True), ("prod.env", True), ("certs/server.pem", True),
    ("C:/Users/me/.ssh/config", True), ("id_ed25519.pub", True), (".aws/credentials", True),
    ("src/env.ts", False), ("environment.py", False), ("README.md", False), ("keyboard.js", False),
])
def test_files_that_are_secrets_by_nature(path, secret):
    assert is_secret_path(path) is secret


def test_heytoraignore_patterns(tmp_path):
    (tmp_path / ".heytoraignore").write_text("# private\ncustomers/\n*.sqlite\n", encoding="utf-8")
    rules = ignore_rules([str(tmp_path)])
    assert is_ignored(str(tmp_path / "customers" / "list.csv"), rules)
    assert is_ignored(str(tmp_path / "data" / "app.sqlite"), rules)
    assert not is_ignored(str(tmp_path / "src" / "app.py"), rules)


# -- the store: ring buffers, retention, search -----------------------------------------------------------------------

def test_a_stream_is_a_ring_buffer(monkeypatch):
    from devlink import store as store_mod
    monkeypatch.setattr(store_mod, "RING_LINES", 100)
    s = Store(":memory:")
    s.open_stream("t1", "ws", "terminal", "pwsh")
    for i in range(0, 250, 50):
        s.add_lines("t1", [(time.time(), "info", f"line {i + k}", "") for k in range(50)])
    left = s.tail("t1", 1000)
    assert len(left) <= 100 and left[-1]["text"] == "line 249", "the newest kept, the oldest gone"


def test_old_lines_and_events_are_purged(tmp_path):
    s = Store(tmp_path / "d.db")
    s.open_stream("t1", "ws", "terminal", "pwsh")
    s.add_lines("t1", [(time.time() - 8 * 86400, "info", "old", ""), (time.time(), "info", "new", "")])
    s.add_event("ws", "build.failed", "old one", ts=time.time() - 8 * 86400)
    s.purge()
    assert [r["text"] for r in s.tail("t1", 10)] == ["new"]
    assert s.timeline("ws") == []


def test_full_text_search_and_a_query_fts_cannot_parse(tmp_path):
    s = Store(tmp_path / "d.db")
    s.open_stream("t1", "ws", "terminal", "pwsh")
    s.add_lines("t1", [(time.time(), "error", "Error: connect ECONNREFUSED 127.0.0.1:5432", ""),
                       (time.time(), "info", "Server ready", "")])
    assert [r["text"] for r in s.search("ECONNREFUSED")] == ["Error: connect ECONNREFUSED 127.0.0.1:5432"]
    assert s.search('"unbalanced AND (') == [], "a spoken question can't trip FTS syntax"


# -- policy: nothing until allowed ------------------------------------------------------------------------------------

def test_nothing_is_allowed_until_granted_and_scopes_last_as_long_as_they_say(tmp_path):
    store = Store(tmp_path / "d.db")
    pol = Policy(store)
    assert not pol.allows("ws", P.READ_CODE)
    assert pol.allows("ws", None), "a focus change carries nothing of theirs"
    pol.grant("ws", P.READ_CODE, P.ALWAYS)
    pol.grant("ws", P.READ_TERMINALS, P.SESSION)
    pol.grant("ws", P.READ_DEBUG, P.ONCE)
    assert pol.use("ws", P.READ_DEBUG) and not pol.allows("ws", P.READ_DEBUG), "once is one answer"
    again = Policy(store)                                    # HeyTora restarted
    assert again.allows("ws", P.READ_CODE) and not again.allows("ws", P.READ_TERMINALS)


def test_pause_and_local_only(tmp_path):
    pol = Policy(Store(tmp_path / "d.db"))
    pol.grant("ws", P.READ_CODE, P.ALWAYS)
    pol.set_paused(True)
    assert not pol.allows("ws", P.READ_CODE) and not pol.sends
    pol.set_paused(False)
    pol.set_data_policy(LOCAL_ONLY)
    assert pol.allows("ws", P.READ_CODE) and not pol.sends
    assert Policy(pol.store).data_policy == LOCAL_ONLY


def test_writing_cannot_be_granted_yet(tmp_path):
    pol = Policy(Store(tmp_path / "d.db"))
    for capability in P.LATER_CAPABILITIES:
        with pytest.raises(ValueError):
            pol.grant("ws", capability, P.ALWAYS)


def test_the_same_folders_are_the_same_workspace():
    assert workspace_id([r"C:\Code\Shop", r"C:\Code\Lib"]) == workspace_id([r"c:\code\lib", r"C:\Code\Shop\\"])
    assert workspace_id([r"C:\Code\Shop"]) != workspace_id([r"C:\Code\Other"])


# -- the log hub ------------------------------------------------------------------------------------------------------

def test_terminal_output_is_cleaned():
    raw = "\x1b[32mPASS\x1b[0m ok\r\n\x1b]633;C\x07Downloading 10%\rDownloading 100%\n"
    assert clean(raw) == "PASS ok\nDownloading 100%\n"
    assert level_of("npm ERR! missing script") == "error" and level_of("DeprecationWarning: x") == "warn"


def _hub_logs():
    store = Store(":memory:")
    return store, LogHub(store)


@pytest.mark.parametrize("line, kind, check", [
    ("FAILED tests/test_cart.py::test_total - AssertionError: 3 != 4", "test.failed",
     {"file": "tests/test_cart.py", "name": "test_total"}),
    ("FAIL src/cart.test.ts", "test.failed", {"file": "src/cart.test.ts"}),
    ("  ● cart › adds up the total", "test.failed", {"name": "cart › adds up the total"}),
    ("--- FAIL: TestTotal (0.00s)", "test.failed", {"name": "TestTotal"}),
    ("src/billing/invoice.ts(12,5): error TS2322: Type 'string' is not assignable to type 'number'.", "build.error",
     {"file": "src/billing/invoice.ts", "line": 12, "code": "TS2322"}),
    ("src/a.ts:4:1 - error TS2304: Cannot find name 'foo'.", "build.error", {"line": 4, "code": "TS2304"}),
    ("main.c:10:5: error: expected ';' before 'return'", "build.error", {"file": "main.c", "line": 10}),
    ("Error: listen EADDRINUSE: address already in use :::3000", "port.in_use", {"port": 3000}),
    ("npm ERR! Missing script: \"start\"", "error", {}),
])
def test_failures_are_pulled_out_of_output(line, kind, check):
    _store, logs = _hub_logs()
    found = logs.output("ws", "ws:terminal:1", "terminal", "pwsh", line + "\n")
    assert found and found[0]["kind"] == kind, found
    for key, value in check.items():
        assert found[0][key] == value


def test_a_python_traceback_is_one_crash_with_its_place():
    _store, logs = _hub_logs()
    found = logs.output("ws", "s", "terminal", "pwsh", 'Traceback (most recent call last):\n  File "app/main.py", '
                        'line 42, in run\n    total = price * qty\nTypeError: unsupported operand type(s)\n')
    assert [f["kind"] for f in found] == ["crash"]
    assert found[0]["file"] == "app/main.py" and found[0]["line"] == 42 and found[0]["type"] == "TypeError"


def test_a_rust_error_gets_its_location_from_the_next_line():
    _store, logs = _hub_logs()
    found = logs.output("ws", "s", "terminal", "pwsh", "error[E0308]: mismatched types\n --> src/main.rs:4:18\n")
    assert found[0]["kind"] == "build.error" and found[0]["file"] == "src/main.rs" and found[0]["line"] == 4


def test_a_partial_line_waits_for_the_rest():
    store, logs = _hub_logs()
    logs.command_started("ws", "s", "terminal", "pwsh", "npm test", "", "c1")
    logs.output("ws", "s", "terminal", "pwsh", "half a li")
    assert logs.tail("s") == []
    logs.output("ws", "s", "terminal", "pwsh", "ne\n")
    assert logs.tail("s") == ["half a line"]


def test_a_failed_command_is_one_summed_up_event():
    store, logs = _hub_logs()
    logs.command_started("ws", "s", "terminal", "pwsh", "npx tsc --noEmit", "C:/shop", "c1")
    logs.output("ws", "s", "terminal", "pwsh", "".join(
        f"src/billing/{n}.ts({i},1): error TS2322: Type 'x' is not assignable.\n" for i, n in enumerate("abc", 1)))
    event = logs.command_ended("ws", "s", 2, "npx tsc --noEmit", "c1")
    assert event["kind"] == "build.failed"
    assert "3 errors, all TS2322 in src/billing/" in event["summary"] and "exit 2" in event["summary"]
    last = logs.last_command("ws")
    assert last["command"] == "npx tsc --noEmit" and last["exit_code"] == 2 and len(last["output"]) == 3


def test_a_test_run_that_passes_is_remembered_too():
    store, logs = _hub_logs()
    logs.command_started("ws", "s", "terminal", "pwsh", "pytest -q", "", "c1")
    assert logs.command_ended("ws", "s", 0, "pytest -q", "c1")["kind"] == "build.passed"
    assert logs.command_ended("ws", "s", 0, "ls", "c2") is None, "an ordinary command is not news"


def test_secrets_in_output_are_never_stored():
    store, logs = _hub_logs()
    logs.output("ws", "s", "terminal", "pwsh", "GEMINI_API_KEY=" + FAKE["google"] + "\n")
    assert FAKE["google"][:8] not in json.dumps(store.tail("s", 10))
    assert store.search(FAKE["google"]) == []


def test_summaries_read_well():
    assert summarise([{"kind": "test.failed", "name": "a", "file": "t/x.py", "line": 3}]).startswith("1 failure")
    assert summarise([{"kind": "build.error", "code": "TS2322", "file": "a/b.ts"},
                      {"kind": "build.error", "code": "TS2304", "file": "c/d.ts"}]).startswith("2 errors;")


# -- the context engine -----------------------------------------------------------------------------------------------

def test_hot_state_follows_the_editor(tmp_path):
    ctx = ContextEngine()
    ws = "w1"
    ctx.state(ws).folders = [str(tmp_path)]
    a = str(tmp_path / "src" / "app.ts")
    ctx.apply(ws, "editor.active", {"path": a, "language": "typescript", "line": 4, "character": 2, "line_count": 90})
    ctx.apply(ws, "editor.selection", {"path": a, "line": 5, "character": 0, "text": f"token = '{FAKE['openai']}'"})
    s = ctx.state(ws)
    assert s.active["line"] == 5 and s.active["language"] == "typescript", "a selection keeps what it knew"
    assert FAKE["openai"][:14] not in s.selection
    noted = ctx.apply(ws, "diagnostics.changed", {"files": [{"path": a, "items": [
        {"severity": "error", "message": "Cannot find name 'x'.", "line": 9, "source": "ts", "code": 2304}]}]})
    assert noted[0] == "problems" and "src/app.ts:10" in noted[1]
    ctx.apply(ws, "git.changed", {"root": str(tmp_path), "branch": "main", "commit": "aaaa1111"})
    noted = ctx.apply(ws, "git.changed", {"root": str(tmp_path), "branch": "feature/billing", "commit": "bbbb2222"})
    assert noted == ("git.branch", "switched branch to feature/billing", {"from": "main", "to": "feature/billing"})
    noted = ctx.apply(ws, "debug.stopped", {"session": "s1", "path": a, "line": 11, "reason": "exception"})
    assert noted[1] == "debugger stopped at src/app.ts:12 (exception)"


def test_a_secrets_file_is_named_but_never_read():
    ctx = ContextEngine()
    ctx.apply("w", "editor.selection", {"path": "C:/shop/.env", "line": 1, "text": "STRIPE_KEY=" + FAKE["stripe"]})
    assert ctx.state("w").active == {"path": "C:/shop/.env", "secret": True} and ctx.state("w").selection == ""


def test_the_project_is_read_from_its_own_files(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({
        "name": "shop", "workspaces": ["packages/*"], "scripts": {"test": "vitest", "dev": "next dev", "x": "y"},
        "dependencies": {"next": "15", "react": "19"}, "devDependencies": {"vitest": "2"}}), encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / "packages" / "ui").mkdir(parents=True)
    (tmp_path / "packages" / "ui" / "package.json").write_text(json.dumps({"name": "@shop/ui"}), encoding="utf-8")
    api = tmp_path / "services" / "api"
    api.mkdir(parents=True)
    (api / "pyproject.toml").write_text('[project]\nname="api"\ndependencies=["fastapi>=0.1"]\n[tool.pytest.ini_options]\n',
                                        encoding="utf-8")
    p = index_project([str(tmp_path)])
    assert "Node.js / TypeScript" in p["stack"] and "Python" in p["stack"]
    assert {"next", "react", "vitest", "fastapi"} <= set(p["frameworks"])
    assert p["package_managers"] == ["pnpm"] and p["scripts"] == {"test": "vitest", "dev": "next dev"}
    assert "CI: GitHub Actions" in p["notes"] and "monorepo (workspaces)" in p["notes"]
    assert "packages/ui (@shop/ui)" in p["packages"] and "services/api" in p["packages"]


def test_code_search_skips_secrets_ignored_and_binary_files(tmp_path):
    (tmp_path / "app.py").write_text("def total(items):\n    return sum(items)\n", encoding="utf-8")
    (tmp_path / ".env").write_text("TOTAL_SECRET=1\n", encoding="utf-8")
    (tmp_path / "private.txt").write_text("total\n", encoding="utf-8")
    (tmp_path / "blob.bin").write_bytes(b"total\0\0\0")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("total", encoding="utf-8")
    (tmp_path / ".heytoraignore").write_text("private.txt\n", encoding="utf-8")
    hits = search_code([str(tmp_path)], "total", rules=ignore_rules([str(tmp_path)]))
    assert [Path(h["path"]).name for h in hits] == ["app.py"] and hits[0]["line"] == 1


def test_a_packet_fits_its_budget_most_important_first():
    sections = [("Where they are", "Workspace: shop"), ("Problems", "x" * 600), ("Project", "y" * 3000)]
    packet = fit(sections, 1000)
    assert packet.startswith("## Where they are") and len(packet) <= 1100
    assert "Not included (ask for them): Project" in packet or "(cut short)" in packet


# -- the hub, through a real socket -----------------------------------------------------------------------------------

class Client:
    """A connector speaking the protocol over the gateway's socket."""

    def __init__(self, reader, writer):
        self.r, self.w, self.next, self.notes, self.answering = reader, writer, 1, [], None
        self.files: dict[str, str] = {}

    async def send(self, msg):
        self.w.write((json.dumps(msg) + "\n").encode())
        await self.w.drain()

    async def call(self, method, params):
        rid = self.next
        self.next += 1
        await self.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            msg = json.loads(await asyncio.wait_for(self.r.readline(), 5))
            if msg.get("id") == rid and "method" not in msg:
                return msg
            self.notes.append(msg)

    async def events(self, *events):
        await self.send({"jsonrpc": "2.0", "method": "events",
                         "params": {"events": [{"type": t, "payload": p} for t, p in events]}})
        await asyncio.sleep(0.05)

    def serve(self, answers):
        """Answer the hub's requests in the background: method -> result."""
        async def loop():
            while True:
                line = await self.r.readline()
                if not line:
                    return
                msg = json.loads(line)
                if msg.get("method") and "id" in msg:
                    if msg["method"] in answers:
                        result = answers[msg["method"]]
                        await self.send({"jsonrpc": "2.0", "id": msg["id"],
                                         "result": result(msg["params"]) if callable(result) else result})
                    else:
                        await self.send({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "no"}})
                else:
                    self.notes.append(msg)
        self.answering = asyncio.ensure_future(loop())


@pytest.fixture
async def hub(tmp_path):
    h = Hub(tmp_path / "data", port=0)
    await h.start()
    devlink.set_current(h)
    yield h
    devlink.set_current(None)
    await h.stop()


@pytest.fixture
def project(tmp_path):
    proj = tmp_path / "shop"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "cart.ts").write_text("export function total(items) {\n  return items.length;\n}\n", encoding="utf-8")
    (proj / ".env").write_text("STRIPE_KEY=" + FAKE["stripe"] + "\n", encoding="utf-8")
    (proj / "package.json").write_text(json.dumps({"name": "shop", "scripts": {"test": "jest"}}), encoding="utf-8")
    return proj


async def _connect(hub, folders, token=None):
    info = json.loads((hub.folder / "devlink.json").read_text(encoding="utf-8"))
    r, w = await asyncio.open_connection("127.0.0.1", info["port"])
    c = Client(r, w)
    hello = await c.call("hello", {"token": token or info["token"], "protocol": 1, "app": "Visual Studio Code",
                                   "appVersion": "1.143.1", "workspace": {"name": "shop", "folders": folders}})
    return c, hello


async def test_a_connection_without_the_token_is_refused(hub, project):
    c, hello = await _connect(hub, [str(project)], token="x" * 43)
    assert hello["error"]["code"] == P.NOT_AUTHORISED and hub.gateway.connections == []


async def test_a_browser_poking_the_port_gets_nothing(hub):
    info = json.loads((hub.folder / "devlink.json").read_text(encoding="utf-8"))
    r, w = await asyncio.open_connection("127.0.0.1", info["port"])
    w.write(b"POST / HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n{\"jsonrpc\":\"2.0\"}\n")
    await w.drain()
    assert await asyncio.wait_for(r.read(), 5) == b"", "closed without a word"


async def test_the_discovery_file_keeps_its_token_and_drops_the_port_on_stop(tmp_path):
    h = Hub(tmp_path / "d", port=0)
    await h.start()
    first = json.loads((tmp_path / "d" / "devlink.json").read_text(encoding="utf-8"))
    await h.stop()
    after = json.loads((tmp_path / "d" / "devlink.json").read_text(encoding="utf-8"))
    assert after == {"token": first["token"], "protocol": 1}
    h2 = Hub(tmp_path / "d", port=0)
    assert h2.gateway.token == first["token"], "one token per install"
    h2.store.close()


async def test_nothing_is_taken_in_or_answered_before_it_is_allowed(hub, project):
    c, hello = await _connect(hub, [str(project)])
    assert hello["result"]["grants"] == {} and set(hello["result"]["capabilities"]) == set(P.READ_CAPABILITIES)
    await c.events(("editor.active", {"path": str(project / "src" / "cart.ts"), "line": 1}),
                   ("terminal.output", {"terminal": "1", "text": "secret build output\n"}))
    ws = hello["result"]["workspaceId"]
    assert hub.context.state(ws).active == {} and hub.store.search("secret") == [], "dropped, not stored"
    out = await dtools.answer({"action": "overview"})
    assert "is not allowed to read" in out["error"] and "action allow" in out["error"], "she asks, then allows"
    assert hub.store.audit_log() == [], "nothing was sent"


async def test_a_grant_from_the_editor_and_the_packet_it_allows(hub, project):
    c, hello = await _connect(hub, [str(project)])
    ws = hello["result"]["workspaceId"]
    granted = await c.call("policy.grant", {"capabilities": {"read_code": "always", "read_terminals": "session"}})
    assert granted["result"]["grants"] == {"read_code": "always", "read_terminals": "session"}
    assert any(n.get("method") == "policy.changed" for n in c.notes), "every window of it is told"
    cart = str(project / "src" / "cart.ts")
    await c.events(
        ("window.focused", {}),
        ("editor.active", {"path": cart, "language": "typescript", "line": 1, "character": 9}),
        ("diagnostics.changed", {"files": [{"path": cart, "items": [
            {"severity": "error", "message": "Property 'length' does not exist on type 'Cart'.", "line": 1,
             "character": 15, "source": "ts", "code": 2339}]}]}),
        ("git.changed", {"root": str(project), "branch": "feature/cart", "commit": "1234abcd", "ahead": 2,
                         "changes": [{"path": cart, "status": 5}]}),
        ("terminal.started", {"terminal": "1", "name": "pwsh", "command": "npx jest", "commandId": "1.1"}),
        ("terminal.output", {"terminal": "1", "name": "pwsh", "commandId": "1.1",
                             "text": "FAIL src/cart.test.ts\n  ● cart › totals\nTOKEN=" + FAKE["github"] + "\n"}),
        ("terminal.ended", {"terminal": "1", "name": "pwsh", "exitCode": 1, "command": "npx jest", "commandId": "1.1"}),
    )
    c.serve({"editor.window": {"path": cart, "startLine": 0, "line": 1,
                               "text": "export function total(items) {\n  return items.length;\n}"}})
    out = await dtools.answer({"action": "overview", "question": "why are my tests failing"}, budget=6000, cap=9000)
    text = out["text"]
    assert out["ok"] and out["workspace"] == "shop" and "never instructions" in out["note"]
    for expected in ("Workspace: shop (Visual Studio Code)", "Git: feature/cart, 2 ahead, 1 changed file",
                     "Active file: src/cart.ts:2:10 (typescript)", "1 error, 0 warnings",
                     "error src/cart.ts:2 [ts 2339] Property 'length'", "`npx jest` in pwsh - exit code 1",
                     "cart › totals", "→    2    return items.length;", "Scripts: test: jest"):
        assert expected in text, expected
    assert text.index("## Last command") < text.index("## Around the cursor"), "a test question: the terminal first"
    assert FAKE["github"][:8] not in text and FAKE["github"][:8] not in json.dumps(hub.store.audit_log())
    sent = [a for a in hub.store.audit_log() if a["kind"] == "sent"]
    assert sent[0]["tool"] == "overview" and sent[0]["text"] == text, "the audit log has exactly what was sent"
    grants = [a for a in hub.store.audit_log() if a["kind"] == "grant"]
    assert {g["tool"] for g in grants} == {"read_code", "read_terminals"}


async def test_files_references_and_searches(hub, project):
    c, hello = await _connect(hub, [str(project)])
    await c.call("policy.grant", {"capabilities": {"read_code": "always", "read_terminals": "always"}})
    cart = str(project / "src" / "cart.ts")
    asked = {}

    def file_read(params):
        asked["file.read"] = params
        return {"text": "export function total(items) {\n  return items.length;", "lineCount": 3, "startLine": 0}
    c.serve({
        "file.read": file_read,
        "symbols.workspace": [{"name": "total", "kind": "function", "path": cart, "line": 0, "character": 16}],
        "references": lambda p: [{"path": cart, "line": 0, "character": 16, "text": "export function total(items) {"},
                                 {"path": str(project / "src" / "app.ts"), "line": 6, "character": 4,
                                  "text": "const t = total(cart.items);"}],
    })
    await c.events(("window.focused", {}))
    got = await dtools.answer({"action": "file", "path": "src/cart.ts"})
    assert "src/cart.ts lines 1-2 of 3" in got["text"] and "    1  export function total(items) {" in got["text"]
    assert asked["file.read"]["startLine"] == 0, "the protocol counts lines from 0"
    refs = await dtools.answer({"action": "references", "query": "total"})
    assert "2 references to total" in refs["text"] and "src/app.ts:7: const t = total(cart.items);" in refs["text"]
    syms = await dtools.answer({"action": "symbols", "query": "total"})
    assert "function total - src/cart.ts:1" in syms["text"]
    found = await dtools.answer({"action": "search_code", "query": "items.length"})
    assert "src/cart.ts:2: return items.length;" in found["text"]
    secret = await dtools.answer({"action": "file", "path": ".env"})
    assert "never reads it" in secret["error"]
    outside = await dtools.answer({"action": "file", "path": "../../Windows/win.ini"})
    assert "not a file in the open workspace" in outside["error"]


async def test_logs_through_the_tools(hub, project):
    c, hello = await _connect(hub, [str(project)])
    await c.call("policy.grant", {"capabilities": {"read_terminals": "always"}})
    await c.events(
        ("terminal.started", {"terminal": "2", "name": "dev server", "command": "npm run dev", "commandId": "2.1"}),
        ("terminal.output", {"terminal": "2", "name": "dev server", "commandId": "2.1",
                             "text": "Error: listen EADDRINUSE: address already in use :::3000\n"}),
        ("task.started", {"name": "build"}), ("task.ended", {"name": "build", "exitCode": 1}),
    )
    assert "port 3000 already in use" in (await dtools.answer({"action": "failures"}))["text"]
    assert "`task build` failed (exit 1)" in (await dtools.answer({"action": "failures"}))["text"]
    assert "EADDRINUSE" in (await dtools.answer({"action": "search_logs", "query": "EADDRINUSE"}))["text"]
    tail = await dtools.answer({"action": "tail", "stream": "dev server", "lines": 5})
    assert tail["text"].startswith("terminal dev server - `npm run dev` (running)")
    assert "npm run dev" in (await dtools.answer({"action": "streams"}))["text"]
    code = await dtools.answer({"action": "problems"})
    assert "code and problems" in code["error"], "terminals allowed is not code allowed"


async def test_paused_local_only_and_once(hub, project):
    c, hello = await _connect(hub, [str(project)])
    ws = hello["result"]["workspaceId"]
    await c.call("policy.grant", {"capabilities": {"read_code": "once"}})
    c.serve({})
    assert (await dtools.answer({"action": "problems"}))["ok"]
    assert "not allowed to read" in (await dtools.answer({"action": "problems"}))["error"], "once was one answer"
    await hub.set_grant(ws, P.READ_CODE, P.ALWAYS)
    await hub.set_paused(True)
    assert "paused" in (await dtools.answer({"action": "problems"}))["error"]
    await asyncio.sleep(0.05)
    assert any(n.get("method") == "policy.changed" and n["params"]["paused"] for n in c.notes), "the editor is told"
    await hub.set_paused(False)
    await hub.set_data_policy(LOCAL_ONLY)
    assert "local only" in (await dtools.answer({"action": "problems"}))["error"]


async def test_no_editor_and_no_hub_say_so():
    devlink.set_current(None)
    assert "turned off in HeyTora" in (await dtools.answer({"action": "overview"}))["error"]


async def test_no_editor_connected_says_look_at_the_screen(hub):
    out = await dtools.answer({"action": "overview"})
    assert "No code editor is connected" in out["error"] and "look_at_screen" in out["error"]


async def test_the_voice_gets_a_short_packet(hub, project):
    c, hello = await _connect(hub, [str(project)])
    await c.call("policy.grant", {"capabilities": {"read_code": "always"}})
    cart = str(project / "src" / "cart.ts")
    await c.events(("editor.active", {"path": cart, "line": 0}), ("diagnostics.changed", {"files": [{"path": cart, "items": [
        {"severity": "error", "message": f"problem number {i} " + "x" * 80, "line": i} for i in range(100)]}]}))
    c.serve({})
    out = await dtools.answer({"action": "overview"})
    assert len(out["text"]) <= dtools.VOICE_CHARS + 60, "19 Oct: a long tool result delays her first word"


async def test_a_message_too_big_closes_the_connection(hub, project, monkeypatch):
    c, hello = await _connect(hub, [str(project)])
    c.w.write(b"{" + b"x" * (P.MAX_LINE_BYTES + 10) + b"\n")
    await c.w.drain()
    assert await asyncio.wait_for(c.r.read(), 5) == b""
    await asyncio.sleep(0.05)
    assert hub.gateway.connections == [] and not hub.context.state(hello["result"]["workspaceId"]).connected


async def test_the_settings_page_sees_and_changes_it(hub, project):
    c, hello = await _connect(hub, [str(project)])
    ws = hello["result"]["workspaceId"]
    await hub.set_grant(ws, P.READ_DEBUG, P.ALWAYS)
    state = await hub.overview_for_settings()
    assert state["workspaces"][0]["name"] == "shop" and state["workspaces"][0]["connected"]
    assert state["workspaces"][0]["grants"] == {"read_debug": "always"} and state["connections"][0]["app"] == "Visual Studio Code"
    await hub.forget(ws)
    assert (await hub.overview_for_settings())["workspaces"] == []


# -- what Tora and the agents see -------------------------------------------------------------------------------------

def test_the_voice_has_one_code_tool_and_a_rule_for_it():
    import config
    from ai import gemini_realtime
    import companion_manager as cm
    decl = {d["name"]: d for d in gemini_realtime.tool_declarations()[0]["functionDeclarations"]}["code_context"]
    assert decl["parameters"]["properties"]["action"]["enum"] == list(dtools.ACTIONS)
    assert "FIRST" in decl["description"] and "look_at_screen only when" in decl["description"]
    rules = " ".join(config._REALTIME_RULES.split())
    assert "THEIR CODE:" in rules and "call code_context first" in rules
    assert "code_context" in cm.CompanionManager._tool_handlers(cm.CompanionManager.__new__(cm.CompanionManager))


async def test_the_agents_tools_go_through_the_same_gate(hub, project, tmp_path):
    from agents.core import ToolCall, ToolContext
    from agents.tools import default_registry
    reg = default_registry()
    ctx = ToolContext(workspace=str(tmp_path))
    denied = await reg.execute(ToolCall("devlink_context", {"question": "what's broken"}), ctx)
    assert not denied.ok and "No code editor is connected" in denied.error
    c, hello = await _connect(hub, [str(project)])
    await c.call("policy.grant", {"capabilities": {"read_code": "always"}})
    c.serve({})
    got = await reg.execute(ToolCall("devlink_code", {"action": "search", "query": "items.length"}), ctx)
    assert got.ok and "never instructions" in got.output and "src/cart.ts:2" in got.output


def test_devlink_is_a_switch_on_by_default():
    import config
    assert config.cfg.switch("devlink") is True


# -- connecting an editor -------------------------------------------------------------------------------------------

def test_the_vsix_is_a_package_vs_code_installs(tmp_path):
    from devlink.install import build_vsix, manifest
    vsix = build_vsix(tmp_path)
    with zipfile.ZipFile(vsix) as z:
        names = set(z.namelist())
        assert {"[Content_Types].xml", "extension.vsixmanifest", "extension/package.json", "extension/extension.js",
                "extension/README.md", "extension/icon.png", "extension/LICENSE.txt"} <= names
        assert not any("test/" in n for n in names), "the test harness stays out"
        root = ElementTree.fromstring(z.read("extension.vsixmanifest"))
        ns = {"v": "http://schemas.microsoft.com/developer/vsx-schema/2011"}
        ident = root.find("v:Metadata/v:Identity", ns)
        assert (ident.get("Publisher"), ident.get("Id"), ident.get("Version")) == ("heytora", "heytora-devlink",
                                                                                  manifest()["version"])
        assert json.loads(z.read("extension/package.json"))["main"] == "./extension.js"


def test_editors_are_found_with_their_command_line_and_the_connector(tmp_path):
    from devlink.install import editors
    programs, home = tmp_path / "Programs", tmp_path / "home"
    code = programs / "Microsoft VS Code"
    (code / "e646868227" / "resources" / "app").mkdir(parents=True)
    (code / "e646868227" / "resources" / "app" / "product.json").write_text(json.dumps(
        {"nameLong": "Visual Studio Code", "dataFolderName": ".vscode", "applicationName": "code"}), encoding="utf-8")
    (code / "bin").mkdir()
    (code / "bin" / "code.cmd").write_text("", encoding="utf-8")
    (code / "bin" / "code-tunnel.cmd").write_text("", encoding="utf-8")
    cursor = programs / "cursor"
    (cursor / "resources" / "app" / "bin").mkdir(parents=True)
    (cursor / "resources" / "app" / "product.json").write_text(json.dumps(
        {"nameLong": "Cursor", "dataFolderName": ".cursor", "applicationName": "cursor"}), encoding="utf-8")
    (cursor / "resources" / "app" / "bin" / "cursor.cmd").write_text("", encoding="utf-8")
    (programs / "Notepad++").mkdir()
    (home / ".vscode" / "extensions" / "heytora.heytora-devlink-0.1.0").mkdir(parents=True)
    found = editors([programs], home)
    assert [(e.name, Path(e.cli).name, e.installed) for e in found] == [
        ("Visual Studio Code", "code.cmd", "0.1.0"), ("Cursor", "cursor.cmd", "")]


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


def test_the_settings_tab_builds_with_the_connector_off_and_on(qapp):
    from PyQt6.QtWidgets import QLabel
    from ui.settings_tabs import CodeEditorsTab

    class Bridge:
        changed = None

        def __init__(self, on):
            self.on = on

        def devlink_state(self):
            return {"on": self.on, "running": self.on, "paused": False, "data_policy": "redacted",
                    "editors": [{"key": "vscode", "name": "Visual Studio Code", "installed": "0.1.0"},
                                {"key": "cursor", "name": "Cursor", "installed": ""}],
                    "workspaces": [{"workspace_id": "w1", "name": "shop", "connected": True, "folders": [],
                                    "grants": {"read_code": "always"}}]}

        def __getattr__(self, name):
            return lambda *a, **k: None
    off = CodeEditorsTab(Bridge(False))
    words = " ".join(l.text() for l in off.findChildren(QLabel))
    assert "Read my code editors" in words and "Editors on this PC" not in words
    on = CodeEditorsTab(Bridge(True))
    words = " ".join(l.text() for l in on.findChildren(QLabel))
    for expected in ("Connected - version 0.1.0", "Not connected yet", "shop · open now", "Code and problems",
                     "Terminals and tasks", "What HeyTora sent", ".heytoraignore"):
        assert expected in words, expected


# -- the real extension, talking to the real hub ----------------------------------------------------------------------

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")


class Harness:
    def __init__(self, proc):
        self.proc, self.heard = proc, []

    async def next(self, timeout=10.0):
        line = await asyncio.wait_for(self.proc.stdout.readline(), timeout)
        assert line, "the harness stopped"
        return json.loads(line)

    async def do(self, **cmd):
        self.proc.stdin.write((json.dumps(cmd) + "\n").encode())
        await self.proc.stdin.drain()
        said = []
        while True:
            msg = await self.next()
            said.append(msg)
            if "done" in msg or "failed" in msg:
                assert "failed" not in msg, msg
                return said


async def _start_harness(hub, project, answer="Allow"):
    env = dict(os.environ, HEYTORA_DEVLINK_DISCOVERY=str(hub.folder / "devlink.json"), PROMPT_ANSWER=answer)
    proc = await asyncio.create_subprocess_exec("node", str(HARNESS), str(project), stdin=asyncio.subprocess.PIPE,
                                                stdout=asyncio.subprocess.PIPE, env=env)
    return Harness(proc)


async def _until(check, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        await asyncio.sleep(0.05)
    return False


@needs_node
async def test_the_extension_asks_then_streams_and_answers(hub, project):
    (project / "src" / "app.ts").write_text("import { total } from './cart';\n\n\n\n\n\nconst t = total(cart.items);\n",
                                            encoding="utf-8")
    h = await _start_harness(hub, project)
    try:
        hello = await h.next()
        assert hello["connected"] and hello["grants"] == {}, "connected, nothing allowed yet"
        ws = hello["workspaceId"]
        assert await _until(lambda: len(hub.policy.grants(ws)) == 3), "asked in VS Code, 'Allow' gave all three"
        await h.do(do="focus")
        await h.do(do="open", file="src/cart.ts", line=1, character=9)
        await h.do(do="select", file="src/cart.ts", line=1, character=22, to=[1, 9])
        await h.do(do="diagnostics", file="src/cart.ts", items=[
            {"severity": 0, "message": "Property 'length' does not exist on type 'Cart'.", "line": 1, "source": "ts",
             "code": 2339}])
        await h.do(do="terminal", command="npx jest", exitCode=1,
                   output=["FAIL src/cart.test.ts\n", "  ● cart › totals\n", "Tests: 1 failed\n"])
        await h.do(do="debugStop", file="src/cart.ts", line=1, reason="exception")
        await h.do(do="canned", symbols=[{"name": "total", "file": "src/cart.ts", "line": 0, "character": 16}],
                   references=[{"file": "src/cart.ts", "line": 0, "character": 16},
                               {"file": "src/app.ts", "line": 6, "character": 10}])
        assert await _until(lambda: hub.logs.last_command(ws) and hub.logs.last_command(ws)["exit_code"] == 1)
        assert await _until(lambda: hub.context.state(ws).diagnostics), "problems come 300 ms after the change, batched"

        out = await dtools.answer({"action": "overview", "question": "why do the tests fail"}, budget=6000, cap=9000)
        text = out["text"]
        for expected in ("Workspace: shop (Visual Studio Code)", "Active file: src/cart.ts:2:23 (typescript)",
                         "Selected:\nitems.length;", "error src/cart.ts:2 [ts 2339] Property 'length'",
                         "`npx jest` in pwsh - exit code 1", "cart › totals",
                         "→    2    return items.length;", "Launch app (node) stopped at src/cart.ts:2 in total - exception"):
            assert expected in text, text
        refs = await dtools.answer({"action": "references", "query": "total"})
        assert "2 references to total" in refs["text"] and "src/app.ts:7: const t = total(cart.items);" in refs["text"]
        f = await dtools.answer({"action": "file", "path": "src/cart.ts"})
        assert "    2    return items.length;" in f["text"]

        status = [m for m in await h.do(do="status") if "status" in m][0]
        assert status["status"] == "$(eye) HeyTora" and "Code and problems" in status["tooltip"]
        await hub.set_paused(True)
        assert await _until(lambda: True)
        await asyncio.sleep(0.2)
        status = [m for m in await h.do(do="status") if "status" in m][0]
        assert status["paused"] and status["status"] == "$(debug-pause) HeyTora", "paused in HeyTora, shown in VS Code"
    finally:
        h.proc.stdin.write(b'{"do": "quit"}\n')
        await h.proc.stdin.drain()
        await asyncio.wait_for(h.proc.wait(), 10)


@needs_node
async def test_not_now_sends_nothing(hub, project):
    h = await _start_harness(hub, project, answer="Not now")
    try:
        hello = await h.next()
        await h.do(do="open", file="src/cart.ts", line=1)
        await asyncio.sleep(0.3)
        assert hub.policy.grants(hello["workspaceId"]) == {} and hub.context.state(hello["workspaceId"]).active == {}
        status = [m for m in await h.do(do="status") if "status" in m][0]
        assert status["status"] == "$(eye-closed) HeyTora" and "HeyTora can read this workspace" in status["prompts"][0]
    finally:
        h.proc.stdin.write(b'{"do": "quit"}\n')
        await h.proc.stdin.drain()
        await asyncio.wait_for(h.proc.wait(), 10)
