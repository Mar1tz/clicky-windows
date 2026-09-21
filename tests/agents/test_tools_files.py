"""File tools: each behaviour plus the outside-workspace guard on every tool."""
import os

import pytest

from agents.core import PermissionPolicy, RiskLevel, ToolCall, ToolContext
from agents.tools import default_registry


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    return root


@pytest.fixture
def ctx(ws):
    return ToolContext(workspace=str(ws), policy=PermissionPolicy(max_auto_risk=RiskLevel.HIGH))


@pytest.fixture
def run(ctx):
    reg = default_registry()

    async def _run(_tool, **args):
        return await reg.execute(ToolCall(_tool, args), ctx)
    return _run


# -- workspace guard -----------------------------------------------------------

OUTSIDE_CALLS = {
    "create_folder": lambda p: {"path": p},
    "create_file": lambda p: {"path": p, "content": "x"},
    "write_file": lambda p: {"path": p, "content": "x"},
    "edit_file": lambda p: {"path": p, "old_string": "a", "new_string": "b"},
    "read_file": lambda p: {"path": p},
    "list_dir": lambda p: {"path": p},
    "move_path": lambda p: {"source": p, "destination": "inside.txt"},
    "delete_path": lambda p: {"path": p},
}


@pytest.mark.parametrize("tool", sorted(OUTSIDE_CALLS))
async def test_outside_workspace_refused(tool, tmp_path, ws, run):
    outside = str(tmp_path / "elsewhere" / "victim.txt")
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "victim.txt").write_text("keep me")
    res = await run(tool, **OUTSIDE_CALLS[tool](outside))
    assert not res.ok, tool
    assert res.error == f"outside workspace: {outside}", tool
    assert (tmp_path / "elsewhere" / "victim.txt").read_text() == "keep me"
    # relative escape is caught too
    res = await run(tool, **OUTSIDE_CALLS[tool]("../elsewhere/victim.txt"))
    assert not res.ok and res.error.startswith("outside workspace: "), tool


async def test_move_destination_outside_refused(tmp_path, ws, run):
    (ws / "a.txt").write_text("a")
    res = await run("move_path", source="a.txt", destination=str(tmp_path / "out.txt"))
    assert not res.ok and res.error.startswith("outside workspace: ")
    assert (ws / "a.txt").exists()


async def test_workspace_roots_policy_extends_fence(tmp_path, ws):
    other = tmp_path / "other"
    other.mkdir()
    ctx = ToolContext(workspace=str(ws), policy=PermissionPolicy(
        max_auto_risk=RiskLevel.HIGH, workspace_roots=[str(ws), str(other)]))
    res = await default_registry().execute(
        ToolCall("create_file", {"path": str(other / "ok.txt"), "content": "y"}), ctx)
    assert res.ok and (other / "ok.txt").read_text() == "y"


# -- create_folder -------------------------------------------------------------

async def test_create_folder(ws, run):
    res = await run("create_folder", path="MyApp/src/components")
    assert res.ok and res.data["created"] is True
    assert (ws / "MyApp" / "src" / "components").is_dir()
    assert res.data["path"] == str(ws / "MyApp" / "src" / "components")
    res = await run("create_folder", path="MyApp")
    assert res.ok and res.data["created"] is False
    (ws / "file").write_text("")
    res = await run("create_folder", path="file")
    assert not res.ok and "file already exists" in res.error


# -- create_file / write_file --------------------------------------------------

async def test_create_file_and_overwrite_rule(ws, run):
    res = await run("create_file", path="MyApp/package.json", content='{"name": "x"}\n')
    assert res.ok and res.data["bytes"] == 14
    assert (ws / "MyApp" / "package.json").read_bytes() == b'{"name": "x"}\n'   # LF kept
    res = await run("create_file", path="MyApp/package.json", content="new")
    assert not res.ok and "already exists" in res.error
    assert (ws / "MyApp" / "package.json").read_text() == '{"name": "x"}\n'
    res = await run("create_file", path="MyApp/package.json", content="new", overwrite=True)
    assert res.ok and (ws / "MyApp" / "package.json").read_text() == "new"
    res = await run("create_file", path="empty.txt")
    assert res.ok and (ws / "empty.txt").read_text() == ""


async def test_write_file_creates_and_replaces(ws, run):
    res = await run("write_file", path="a/b/c.txt", content="héllo")
    assert res.ok and res.data["created"] is True and res.data["bytes"] == 6
    assert (ws / "a" / "b" / "c.txt").read_text(encoding="utf-8") == "héllo"
    res = await run("write_file", path="a/b/c.txt", content="bye")
    assert res.ok and res.data["created"] is False
    assert (ws / "a" / "b" / "c.txt").read_text() == "bye"
    res = await run("write_file", path="a", content="x")
    assert not res.ok and "folder" in res.error


# -- edit_file -----------------------------------------------------------------

async def test_edit_file_unique_match(ws, run):
    f = ws / "app.py"
    f.write_text("a = 1\nb = 2\nc = 3\n")
    res = await run("edit_file", path="app.py", old_string="b = 2", new_string="b = 20")
    assert res.ok and res.data["line"] == 2
    assert f.read_text() == "a = 1\nb = 20\nc = 3\n"
    res = await run("edit_file", path="app.py", old_string="zzz", new_string="y")
    assert not res.ok and "not found" in res.error
    f.write_text("x\nx\n")
    res = await run("edit_file", path="app.py", old_string="x", new_string="y")
    assert not res.ok and "occurs 2 times" in res.error
    assert f.read_text() == "x\nx\n"
    res = await run("edit_file", path="missing.py", old_string="a", new_string="b")
    assert not res.ok and "not found" in res.error
    res = await run("edit_file", path="app.py", old_string="", new_string="b")
    assert not res.ok


async def test_edit_file_preserves_crlf(ws, run):
    f = ws / "win.txt"
    f.write_bytes(b"one\r\ntwo\r\nthree\r\n")
    res = await run("edit_file", path="win.txt", old_string="two\nthree", new_string="2\n3")
    assert res.ok
    assert f.read_bytes() == b"one\r\n2\r\n3\r\n"


# -- read_file -----------------------------------------------------------------

async def test_read_file_ranges_and_cap(ws, run):
    f = ws / "r.txt"
    f.write_text("".join(f"line {i}\n" for i in range(1, 11)))
    res = await run("read_file", path="r.txt")
    assert res.ok and res.output.startswith("line 1\n") and res.data["line_count"] == 10
    res = await run("read_file", path="r.txt", start_line=3, end_line=4)
    assert res.ok and res.output == "line 3\nline 4\n"
    assert res.data["start_line"] == 3 and res.data["end_line"] == 4
    res = await run("read_file", path="r.txt", start_line=9, end_line=99)
    assert res.ok and res.output == "line 9\nline 10\n"
    res = await run("read_file", path="r.txt", start_line=50)
    assert not res.ok and "beyond" in res.error
    res = await run("read_file", path="nope.txt")
    assert not res.ok and "not found" in res.error
    res = await run("read_file", path=".")
    assert not res.ok and "list_dir" in res.error
    big = ws / "big.txt"
    big.write_text("x" * 30_000)
    res = await run("read_file", path="big.txt")
    assert res.ok and res.data["truncated"] is True
    assert len(res.output) < 30_000 and "[truncated 10000 chars]" in res.output
    (ws / "empty.txt").write_text("")
    res = await run("read_file", path="empty.txt")
    assert res.ok and res.output == "" and res.data["line_count"] == 0


async def test_read_file_normalises_crlf_and_replaces_bad_bytes(ws, run):
    (ws / "w.txt").write_bytes(b"a\r\nb\xff\r\n")
    res = await run("read_file", path="w.txt")
    assert res.ok and res.output == "a\nb�\n"


# -- list_dir ------------------------------------------------------------------

async def test_list_dir_tree(ws, run):
    (ws / "src").mkdir()
    (ws / "src" / "App.jsx").write_text("x")
    (ws / "src" / "deep").mkdir()
    (ws / "src" / "deep" / "inner.js").write_text("y")
    (ws / "node_modules" / "react").mkdir(parents=True)
    (ws / "package.json").write_text("{}")
    res = await run("list_dir")
    assert res.ok
    out = res.output
    assert out.splitlines()[0] == "ws/"
    assert "├── node_modules/ (not expanded)" in out
    assert "├── src/" in out and "App.jsx" in out and "package.json" in out
    assert "deep/" in out and "inner.js" not in out           # default depth 2
    assert res.data["dirs"] == 3 and res.data["files"] == 2
    res = await run("list_dir", path="src", depth=1)
    assert res.ok and res.output.splitlines()[0] == "src/"
    assert "App.jsx" in res.output and "inner.js" not in res.output
    res = await run("list_dir", path="src", depth=5)
    assert "inner.js" in res.output
    res = await run("list_dir", path="package.json")
    assert not res.ok
    res = await run("list_dir", path="missing")
    assert not res.ok and "not found" in res.error


# -- move_path -----------------------------------------------------------------

async def test_move_path(ws, run):
    (ws / "a.txt").write_text("A")
    res = await run("move_path", source="a.txt", destination="dir/b.txt")
    assert res.ok and (ws / "dir" / "b.txt").read_text() == "A" and not (ws / "a.txt").exists()
    (ws / "c.txt").write_text("C")
    res = await run("move_path", source="c.txt", destination="dir/b.txt")
    assert not res.ok and "already exists" in res.error
    res = await run("move_path", source="c.txt", destination="dir/b.txt", overwrite=True)
    assert res.ok and (ws / "dir" / "b.txt").read_text() == "C"
    res = await run("move_path", source="dir", destination="renamed")
    assert res.ok and (ws / "renamed" / "b.txt").exists()
    res = await run("move_path", source="ghost", destination="x")
    assert not res.ok and "source not found" in res.error


# -- delete_path ---------------------------------------------------------------

async def test_delete_path(ws, run):
    (ws / "f.txt").write_text("f")
    res = await run("delete_path", path="f.txt")
    assert res.ok and res.data["kind"] == "file" and not (ws / "f.txt").exists()
    (ws / "d" / "sub").mkdir(parents=True)
    (ws / "d" / "sub" / "x.txt").write_text("x")
    res = await run("delete_path", path="d")
    assert not res.ok and "not empty" in res.error and (ws / "d").exists()
    res = await run("delete_path", path="d", recursive=True)
    assert res.ok and res.data["kind"] == "folder" and not (ws / "d").exists()
    (ws / "empty").mkdir()
    res = await run("delete_path", path="empty")
    assert res.ok and not (ws / "empty").exists()
    res = await run("delete_path", path="gone")
    assert not res.ok and "not found" in res.error
    res = await run("delete_path", path=".", recursive=True)
    assert not res.ok and "workspace root" in res.error and ws.exists()
    res = await run("delete_path", path=str(ws), recursive=True)
    assert not res.ok and ws.exists()


async def test_delete_path_handles_readonly_files(ws, run):
    import stat
    (ws / "ro").mkdir()
    f = ws / "ro" / "locked.bin"
    f.write_bytes(b"1")
    os.chmod(f, stat.S_IREAD)
    res = await run("delete_path", path="ro", recursive=True)
    assert res.ok and not (ws / "ro").exists()


async def test_delete_is_high_risk_and_needs_approval(ws):
    from agents.core import auto_deny
    (ws / "f.txt").write_text("f")
    ctx = ToolContext(workspace=str(ws), policy=PermissionPolicy(max_auto_risk=RiskLevel.MEDIUM),
                      approver=auto_deny)
    res = await default_registry().execute(ToolCall("delete_path", {"path": "f.txt"}), ctx)
    assert not res.ok and res.error.startswith("permission denied") and (ws / "f.txt").exists()
