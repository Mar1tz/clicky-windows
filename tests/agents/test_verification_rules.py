"""RuleVerifier against a real tmp_path layout."""
import os
import shutil
import subprocess
from typing import Any, Optional

import pytest

from agents.core import (
    AgentContext, AgentProvider, AgentSpec, EventBus, ProviderCapabilities,
    ProviderResponse, RunControl, Step, StepStatus, Task, ToolCall, ToolRegistry,
    ToolResult,
)
from agents.verification import RuleVerifier


class FakeRegistry(ToolRegistry):
    def register(self, tool) -> None: ...
    def get(self, name): raise KeyError(name)
    def names(self) -> list[str]: return []
    def specs(self, names=None): return []
    async def execute(self, call: ToolCall, ctx) -> ToolResult:
        return ToolResult.success(call, "ok")


class FakeProvider(AgentProvider):
    name = "fake"
    async def complete(self, messages, tools=None, **kw) -> ProviderResponse:
        return ProviderResponse()
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities()


def _ctx(workspace: str, steps: Optional[list[Step]] = None) -> AgentContext:
    task = Task(title="t", goal="g", workspace=workspace, steps=steps or [])
    return AgentContext(task=task, agent=AgentSpec(name="A", goal="g"), tools=FakeRegistry(),
                        provider=FakeProvider(), events=EventBus(), control=RunControl())


def _done(title: str, *calls: tuple[str, dict[str, Any]], ok: bool = True,
          data: Optional[dict[str, Any]] = None) -> Step:
    """A step that claims to have run `calls` successfully (or not)."""
    step = Step(title, status=StepStatus.DONE)
    for name, args in calls:
        call = ToolCall(name, args)
        step.tool_calls.append(call)
        res = (ToolResult.success(call, "ok", **(data or {})) if ok
               else ToolResult.failure(call, "tool said no"))
        step.results.append(res)
    return step


def _scaffold(ws) -> list[Step]:
    """Create MyApp/{package.json, src/App.jsx} for real, return matching steps."""
    (ws / "MyApp" / "src").mkdir(parents=True)
    (ws / "MyApp" / "package.json").write_text('{"name": "myapp"}', encoding="utf-8")
    (ws / "MyApp" / "src" / "App.jsx").write_text("export default function App() {}", encoding="utf-8")
    return [
        _done("Create folder", ("create_folder", {"path": "MyApp"})),
        _done("Create package.json", ("create_file", {"path": "MyApp/package.json", "content": "{}"})),
        _done("Create App.jsx", ("write_file", {"path": "MyApp/src/App.jsx", "content": "x"})),
        _done("Verify", ("list_dir", {"path": "MyApp", "depth": 2})),
    ]


async def test_verify_step_passes_on_real_layout(tmp_path):
    steps = _scaffold(tmp_path)
    ctx = _ctx(str(tmp_path), steps)
    v = RuleVerifier()
    for step in steps:
        res = await v.verify_step(step, ctx)
        assert res.ok, res.summary
    res = await v.verify_step(steps[3], ctx)
    assert res.checks[0].detail == "no rule"            # list_dir has no rule but is visible
    res = await v.verify_step(steps[1], ctx)
    assert res.checks[0].name == "create_file MyApp/package.json" and "bytes" in res.checks[0].detail


async def test_verify_step_fails_when_file_missing_or_empty(tmp_path):
    ctx = _ctx(str(tmp_path))
    v = RuleVerifier()
    missing = _done("x", ("create_file", {"path": "MyApp/nope.txt", "content": "hi"}))
    res = await v.verify_step(missing, ctx)
    assert not res.ok and "file missing" in res.checks[0].detail and "failed:" in res.summary

    (tmp_path / "empty.txt").write_text("", encoding="utf-8")
    empty = _done("y", ("create_file", {"path": "empty.txt", "content": "should have text"}))
    res = await v.verify_step(empty, ctx)
    assert not res.ok and "empty" in res.checks[0].detail
    allowed_empty = _done("z", ("create_file", {"path": "empty.txt", "content": ""}))
    assert (await v.verify_step(allowed_empty, ctx)).ok

    no_dir = _done("d", ("create_folder", {"path": "not/there"}))
    assert not (await v.verify_step(no_dir, ctx)).ok


async def test_failed_tool_result_and_failed_step_always_fail(tmp_path):
    (tmp_path / "MyApp").mkdir()
    ctx = _ctx(str(tmp_path))
    v = RuleVerifier()
    step = _done("x", ("create_folder", {"path": "MyApp"}), ok=False)   # exists, but tool said no
    res = await v.verify_step(step, ctx)
    assert not res.ok and res.checks[0].detail == "tool said no"

    failed = Step("boom", status=StepStatus.FAILED, error="timeout")
    res = await v.verify_step(failed, ctx)
    assert not res.ok and res.checks[0].name == "status" and res.checks[0].detail == "timeout"

    unexecuted = Step("never ran", tool_calls=[ToolCall("create_file", {"path": "a"})])
    res = await v.verify_step(unexecuted, ctx)
    assert not res.ok and res.checks[0].name == "executed"

    empty = Step("llm step did nothing", status=StepStatus.DONE)
    res = await v.verify_step(empty, ctx)
    assert res.ok and res.checks[0].name == "no-op"


async def test_edit_delete_command_and_install_rules(tmp_path):
    ctx = _ctx(str(tmp_path))
    v = RuleVerifier()
    f = tmp_path / "a.py"
    f.write_text("print('new')\n", encoding="utf-8")
    ok = _done("e", ("edit_file", {"path": "a.py", "old_string": "old", "new_string": "print('new')"}))
    bad = _done("e", ("edit_file", {"path": "a.py", "old_string": "x", "new_string": "absent"}))
    assert (await v.verify_step(ok, ctx)).ok
    assert not (await v.verify_step(bad, ctx)).ok

    gone = _done("d", ("delete_path", {"path": "nothing.txt"}))
    still = _done("d", ("delete_path", {"path": "a.py"}))
    assert (await v.verify_step(gone, ctx)).ok
    assert not (await v.verify_step(still, ctx)).ok

    ran = _done("r", ("run_command", {"command": "echo hi", "cwd": "."}), data={"exit_code": 0})
    crashed = _done("r", ("run_command", {"command": "boom"}), data={"exit_code": 1})
    silent = _done("r", ("run_command", {"command": "x"}))
    assert (await v.verify_step(ran, ctx)).ok
    assert not (await v.verify_step(crashed, ctx)).ok
    assert (await v.verify_step(silent, ctx)).ok                 # no exit_code: trust ok=True

    (tmp_path / "MyApp").mkdir()
    (tmp_path / "MyApp" / "package.json").write_text("{}", encoding="utf-8")
    npm = _done("i", ("install_package", {"manager": "npm", "packages": ["react"], "cwd": "MyApp"}))
    assert not (await v.verify_step(npm, ctx)).ok
    (tmp_path / "MyApp" / "node_modules").mkdir()
    assert (await v.verify_step(npm, ctx)).ok

    pip = _done("i", ("install_package", {"manager": "pip", "packages": ["definitely_not_installed_xyz"]}))
    assert not (await v.verify_step(pip, ctx)).ok
    (tmp_path / ".venv" / "Lib" / "site-packages").mkdir(parents=True)
    assert (await v.verify_step(pip, ctx)).ok
    pip_std = _done("i", ("install_package", {"manager": "pip", "packages": ["json>=1"]}))
    assert (await v.verify_step(pip_std, _ctx(str(tmp_path / "MyApp")))).ok   # importable counts


async def test_git_rules(tmp_path):
    ctx = _ctx(str(tmp_path))
    v = RuleVerifier()
    init = _done("g", ("git_init", {"path": "repo"}))
    assert not (await v.verify_step(init, ctx)).ok
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    assert (await v.verify_step(init, ctx)).ok

    commit = _done("c", ("git_commit", {"cwd": "repo", "message": "m"}))
    assert not (await v.verify_step(commit, ctx)).ok          # fake .git, no log
    if not shutil.which("git"):
        pytest.skip("git not installed")
    shutil.rmtree(tmp_path / "repo")
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    for cmd in (["git", "init", "-q"], ["git", "commit", "-q", "--allow-empty", "-m", "first"]):
        subprocess.run(cmd, cwd=repo, check=True, env=env, capture_output=True)
    res = await v.verify_step(commit, ctx)
    assert res.ok and "first" in res.checks[0].detail


async def test_verify_task_reruns_steps_and_skips_skipped(tmp_path):
    steps = _scaffold(tmp_path)
    skipped = _done("Broken", ("create_file", {"path": "MyApp/never.txt", "content": "x"}))
    skipped.status = StepStatus.SKIPPED
    steps.insert(1, skipped)
    ctx = _ctx(str(tmp_path), steps)
    v = RuleVerifier()
    res = await v.verify_task(ctx)
    assert res.ok, res.summary
    assert res.checks[0].name == "workspace" and res.checks[0].passed
    assert "4 step(s) verified" in res.summary
    assert not any("never.txt" in c.name for c in res.checks)

    os.remove(tmp_path / "MyApp" / "src" / "App.jsx")
    res = await v.verify_task(ctx)
    assert not res.ok and "1 failed" in res.summary
    bad = [c for c in res.checks if not c.passed]
    assert len(bad) == 1 and bad[0].name.startswith("Create App.jsx: write_file")

    res = await v.verify_task(_ctx(str(tmp_path / "missing-ws")))
    assert not res.ok and res.checks[0].name == "workspace"


async def test_a_failed_look_by_command_does_not_fail_a_step_that_did_its_work(tmp_path):
    """Field report 4: the florist site's index.html was written; the agent's
    own `Get-Item ...` check then failed and the whole task was FAILED. A
    command that only looks is information, like a SAFE tool - only
    commands that act can fail a step by failing."""
    (tmp_path / "Site").mkdir()
    (tmp_path / "Site" / "index.html").write_text("<!DOCTYPE html>", encoding="utf-8")
    step = Step("Create index.html", status=StepStatus.DONE)
    write = ToolCall("write_file", {"path": "Site/index.html", "content": "<!DOCTYPE html>"})
    look = ToolCall("run_command", {"command": 'Get-Item "Site/index.html" | Select-Object Name'})
    step.tool_calls += [write, look]
    step.results += [ToolResult.success(write, "wrote 15 bytes"),
                     ToolResult(call_id=look.id, tool_name="run_command", ok=False, error="exit code 255",
                                data={"read_only": True, "exit_code": 255})]
    res = await RuleVerifier().verify_step(step, _ctx(str(tmp_path), [step]))
    assert res.ok, res.summary

    act = ToolCall("run_command", {"command": "npm run build"})
    step.tool_calls.append(act)
    step.results.append(ToolResult(call_id=act.id, tool_name="run_command", ok=False, error="exit code 1",
                                   data={"read_only": False, "exit_code": 1}))
    res = await RuleVerifier().verify_step(step, _ctx(str(tmp_path), [step]))
    assert not res.ok, "a command that acts and fails still fails the step"
