"""Git tools: init -> add -> commit -> log/status/diff/branch roundtrip in tmp_path.
No remote is configured, so nothing here can touch the network."""
import shutil

import pytest

from agents.core import PermissionPolicy, RiskLevel, ToolCall, ToolContext, auto_deny
from agents.tools import default_registry

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    return root


@pytest.fixture
def run(ws):
    reg = default_registry()
    ctx = ToolContext(workspace=str(ws), policy=PermissionPolicy(max_auto_risk=RiskLevel.HIGH))

    async def _run(_tool, **args):
        return await reg.execute(ToolCall(_tool, args), ctx)
    return _run


async def test_git_roundtrip(ws, run):
    res = await run("git_init")
    assert res.ok, res.error
    assert (ws / ".git").is_dir() and res.data["existed"] is False and res.data["branch"] == "main"
    res = await run("git_init")
    assert res.ok and res.data["existed"] is True

    res = await run("git_log")
    assert res.ok and res.data["commits"] == [] and res.output == "no commits yet"
    res = await run("git_status")
    assert res.ok and res.data["clean"] is True and res.data["branch"] == "main"

    (ws / "README.md").write_text("# hi\n")
    res = await run("git_status")
    assert res.ok and res.data["clean"] is False
    assert res.data["entries"] == [{"status": "??", "path": "README.md"}]

    res = await run("git_commit", message="empty")
    assert not res.ok and res.error == "nothing to commit"

    res = await run("git_add", paths=["README.md"])
    assert res.ok and res.data["paths"] == ["README.md"]
    res = await run("git_diff", staged=True)
    assert res.ok and "+# hi" in res.output and res.data["empty"] is False

    res = await run("git_commit", message="first commit")
    assert res.ok, res.error
    assert len(res.data["commit"]) >= 7 and res.data["message"] == "first commit"

    res = await run("git_log", limit=5)
    assert res.ok and len(res.data["commits"]) == 1
    assert res.data["commits"][0]["subject"] == "first commit"
    assert res.data["commits"][0]["hash"] == res.data["commits"][0]["hash"].strip()
    assert "first commit" in res.output

    res = await run("git_status")
    assert res.ok and res.data["clean"] is True and "clean" in res.output

    (ws / "README.md").write_text("# hi\nmore\n")
    res = await run("git_diff")
    assert res.ok and "+more" in res.output and res.data["staged"] is False
    res = await run("git_diff", stat=True)
    assert res.ok and "README.md" in res.output and "+more" not in res.output
    res = await run("git_commit", message="second", all=True)
    assert res.ok
    res = await run("git_log")
    assert [c["subject"] for c in res.data["commits"]] == ["second", "first commit"]
    res = await run("git_diff")
    assert res.ok and res.output == "no changes" and res.data["empty"] is True


async def test_git_branch_list_create_switch(ws, run):
    assert (await run("git_init")).ok
    (ws / "a").write_text("a")
    assert (await run("git_add")).ok
    assert (await run("git_commit", message="base")).ok
    res = await run("git_branch")
    assert res.ok and res.data["branches"] == ["main"] and res.data["current"] == "main"
    res = await run("git_branch", name="feature/x")
    assert res.ok and res.data["created"] is True
    res = await run("git_branch")
    assert res.data["current"] == "feature/x" and set(res.data["branches"]) == {"main", "feature/x"}
    res = await run("git_branch", name="main")
    assert res.ok and res.data["created"] is False
    assert (await run("git_branch")).data["current"] == "main"


async def test_git_commit_without_identity_uses_fallback(ws, run):
    assert (await run("git_init")).ok
    (ws / "f").write_text("f")
    assert (await run("git_add")).ok
    # even if this machine has no global identity, the commit must succeed
    res = await run("git_commit", message="anon")
    assert res.ok, res.error
    log = await run("git_log")
    assert log.data["commits"][0]["author"]


async def test_git_tools_respect_workspace_and_missing_repo(tmp_path, ws, run):
    res = await run("git_status", path=str(tmp_path))
    assert not res.ok and res.error.startswith("outside workspace: ")
    res = await run("git_status", path="nope")
    assert not res.ok and "not found" in res.error
    res = await run("git_status")                       # not a repo yet
    assert not res.ok and "not a git repository" in res.output


async def test_git_push_fails_cleanly_without_remote(ws, run):
    assert (await run("git_init")).ok
    (ws / "f").write_text("f")
    assert (await run("git_add")).ok
    assert (await run("git_commit", message="c")).ok
    res = await run("git_push")
    assert not res.ok and res.data["remote"] == "origin" and res.data["branch"] == "main"
    assert "origin" in res.output


async def test_git_push_is_high_risk(ws):
    ctx = ToolContext(workspace=str(ws), policy=PermissionPolicy(max_auto_risk=RiskLevel.MEDIUM),
                      approver=auto_deny)
    res = await default_registry().execute(ToolCall("git_push", {}), ctx)
    assert not res.ok and res.error.startswith("permission denied")
    assert "git_push is high risk" in res.error
