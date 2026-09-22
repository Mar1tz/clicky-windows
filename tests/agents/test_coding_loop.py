"""M22: the autonomous coding loop.

Gate: a repo with one deliberately failing test is fixed within three
attempts, and an unfixable one is given up on cleanly - FAILED, carrying
the last test report. The model is scripted (MockProvider) so the loop's
discipline is what is under test, not a model's coding ability.
"""
from __future__ import annotations

import os
import asyncio
import textwrap

import pytest

from agents.core import (
    EventKind, PermissionPolicy, Plan, Planner, ProviderResponse, RiskLevel, Step,
    StepStatus, Task, TaskKind, TaskStatus, ToolCall, ToolContext,
)
from agents.providers import MockProvider
from agents.tools import default_registry
from agents.tools.test_tools import compose_report, detect_runner, summarise_pytest

CALC = textwrap.dedent("""
    def add(a, b):
        return a - b      # bug: should be a + b
""")
TEST_CALC = textwrap.dedent("""
    from calc import add

    def test_add():
        assert add(2, 3) == 5

    def test_add_zero():
        assert add(0, 0) == 0
""")


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "calc.py").write_text(CALC, encoding="utf-8")
    (tmp_path / "test_calc.py").write_text(TEST_CALC, encoding="utf-8")
    (tmp_path / "conftest.py").write_text("", encoding="utf-8")   # makes tmp_path importable
    return tmp_path


def run_tool(name, ws, **args):
    ctx = ToolContext(workspace=str(ws), policy=PermissionPolicy(max_auto_risk=RiskLevel.HIGH))
    return asyncio.run(default_registry().execute(ToolCall(name, args), ctx))


# -- the tool ----------------------------------------------------------------

def test_detects_pytest_and_npm(tmp_path):
    assert detect_runner(str(tmp_path)) is None
    (tmp_path / "test_x.py").write_text("", encoding="utf-8")
    assert detect_runner(str(tmp_path))[0] == "pytest"
    (tmp_path / "package.json").write_text('{"scripts": {"test": "vitest"}}', encoding="utf-8")
    assert detect_runner(str(tmp_path))[0] == "vitest"
    (tmp_path / "package.json").write_text(
        '{"scripts": {"test": "echo \\"Error: no test specified\\" && exit 1"}}', encoding="utf-8")
    assert detect_runner(str(tmp_path))[0] == "pytest", "npm's placeholder script is not a runner"


def test_report_is_precise_when_red(repo):
    r = run_tool("run_tests", repo)
    assert not r.ok
    assert r.data["runner"] == "pytest"
    assert r.data["failed"] == 1 and r.data["passed"] >= 0
    assert "FAIL (pytest): 1 failed" in r.output
    assert "test_add" in r.output
    assert "assert" in r.output.lower()                      # the assertion message
    assert "test_calc.py:" in r.output                        # file:line
    assert "run_tests again" in r.output                      # what to do next
    assert len(r.output) < 1200, "the report must be readable, not a dump"


def test_report_is_short_when_green(repo):
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    r = run_tool("run_tests", repo)
    assert r.ok, r.output
    assert r.output.startswith("PASS (pytest): 2 passed")
    assert r.data["failed"] == 0


def test_no_runner_is_a_clear_failure(tmp_path):
    r = run_tool("run_tests", tmp_path)
    assert not r.ok and "no test runner found" in r.error


def test_summary_parser_on_real_pytest_output():
    out = textwrap.dedent("""
        F.                                                                       [100%]
        =================================== FAILURES ===================================
        ___________________________________ test_add ___________________________________
        test_calc.py:5: in test_add
            assert add(2, 3) == 5
        E   assert -1 == 5
        E    +  where -1 = add(2, 3)
        =========================== short test summary info ============================
        FAILED test_calc.py::test_add - assert -1 == 5
        ========================= 1 failed, 1 passed in 0.03s ==========================
    """)
    s = summarise_pytest(out)
    assert s["failed"] == 1 and s["passed"] == 1
    assert s["failures"][0]["name"] == "test_calc.py::test_add"
    assert s["failures"][0]["message"] == "assert -1 == 5"
    assert s["failures"][0]["where"].startswith("test_calc.py:5")
    report = compose_report("pytest", 1, s, out)
    assert "✕ test_calc.py::test_add — assert -1 == 5" in report


# -- the loop ----------------------------------------------------------------

class OneStep(Planner):
    """A single LLM-driven step: 'make the tests pass'."""

    async def plan(self, ctx):
        return Plan(task_id=ctx.task.id, steps=[Step("Make the tests pass")])

    async def replan(self, ctx, plan, failure):
        return plan


def _tc(name, **args):
    return ProviderResponse(tool_calls=[ToolCall(name, args)], finish_reason="tool_calls")


def _manager(repo, script):
    from agents.bootstrap import build_agent_manager
    mgr = build_agent_manager(workspace_root=str(repo), in_memory=True, offline=True,
                              max_auto_risk=RiskLevel.HIGH)
    mgr.planner = mgr.orchestrator.planner = OneStep()
    mgr.orchestrator.max_replans = 0
    provider = MockProvider(script=script)
    mgr.router.register(provider, kinds=None, priority=99)
    return mgr.start(), provider


def test_agent_fixes_a_failing_test_within_three_attempts(repo):
    """test → read → fix → test → done. Attempt 1 is red, attempt 2 green."""
    script = [
        _tc("run_tests"),
        _tc("read_file", path="calc.py"),
        _tc("edit_file", path="calc.py", old_string="return a - b      # bug: should be a + b",
            new_string="return a + b"),
        _tc("run_tests"),
        ProviderResponse(text="Fixed add(): it subtracted instead of adding."),
    ]
    mgr, provider = _manager(repo, script)
    try:
        attempts = []
        mgr.events.subscribe(lambda e: attempts.append(e.message),
                             kinds=[EventKind.LOG])
        task = mgr.wait(mgr.submit_task(Task(title="fix", goal="make tests pass",
                                             workspace=str(repo), kind=TaskKind.CODING)).id, 120)
        assert task.status is TaskStatus.COMPLETED, task.error
        step = task.steps[0]
        assert step.status is StepStatus.DONE
        assert "Fixed add()" in step.summary
        assert (repo / "calc.py").read_text(encoding="utf-8").strip().endswith("return a + b")

        runs = [r for c, r in zip(step.tool_calls, step.results) if c.tool_name == "run_tests"]
        assert [r.ok for r in runs] == [False, True]
        assert any("Test attempt 1/3" in m for m in attempts)
        assert any("green after 1 fix attempt" in m for m in attempts)
        # the model was shown the precise failure, not a dump
        red_turn = next(m for m in provider.calls[1].messages if m.role == "tool")
        assert "test_add" in red_turn.content and "assert" in red_turn.content
    finally:
        mgr.stop()


def test_agent_gives_up_cleanly_when_it_cannot_fix_it(repo):
    """Three red runs with no effective fix → FAILED with the last report,
    well before the iteration budget would have run out."""
    script = [
        _tc("run_tests"),
        _tc("edit_file", path="calc.py", old_string="a - b", new_string="a - b - 0"),
        _tc("run_tests"),
        _tc("edit_file", path="calc.py", old_string="a - b - 0", new_string="a - b - 0 - 0"),
        _tc("run_tests"),
        # the script would keep going, but the worker must stop here
        _tc("run_tests"),
        _tc("run_tests"),
        ProviderResponse(text="never reached"),
    ]
    mgr, provider = _manager(repo, script)
    try:
        attempts = []
        mgr.events.subscribe(lambda e: attempts.append(e.message), kinds=[EventKind.LOG])
        task = mgr.wait(mgr.submit_task(Task(title="fix", goal="make tests pass",
                                             workspace=str(repo), kind=TaskKind.CODING)).id, 120)
        assert task.status is TaskStatus.FAILED
        step = task.steps[0]
        assert step.status is StepStatus.FAILED
        assert "still failing after 3 attempts" in step.error
        assert "test_add" in step.error, "the last report must travel with the failure"
        runs = [c for c in step.tool_calls if c.tool_name == "run_tests"]
        assert len(runs) == 3, "must stop at the attempt limit, not the iteration limit"
        assert sum("Test attempt" in m for m in attempts) == 3
        assert "3/3" in attempts[-1]
    finally:
        mgr.stop()


def test_a_green_run_resets_the_attempt_count(repo):
    """Four red runs in one step - but never three in a row, because a
    green run sits in the middle - must complete, not fail. Otherwise a
    step that fixes one bug and then uncovers another is punished for
    making progress."""
    fixed_add = "def add(a, b):" + chr(10) + "    return a + b" + chr(10)
    with_mul = fixed_add + chr(10) + "def mul(a, b):" + chr(10) + "    return a * b" + chr(10)
    mul_test = ("from calc import mul" + chr(10) * 2 + "def test_mul():" + chr(10)
                + "    assert mul(2, 3) == 6" + chr(10))
    script = [
        _tc("run_tests"),                                          # red 1
        _tc("run_tests"),                                          # red 2
        _tc("write_file", path="calc.py", content=fixed_add),
        _tc("run_tests"),                                          # GREEN - resets
        _tc("create_file", path="test_mul.py", content=mul_test),  # a new failing test
        _tc("run_tests"),                                          # red 1 (fresh budget)
        _tc("run_tests"),                                          # red 2
        _tc("write_file", path="calc.py", content=with_mul),
        _tc("run_tests"),                                          # GREEN
        ProviderResponse(text="Fixed add and added mul."),
    ]
    mgr, _ = _manager(repo, script)
    try:
        attempts = []
        mgr.events.subscribe(lambda e: attempts.append(e.message), kinds=[EventKind.LOG])
        task = mgr.wait(mgr.submit_task(Task(title="fix", goal="make tests pass",
                                             workspace=str(repo), kind=TaskKind.CODING)).id, 180)
        assert task.status is TaskStatus.COMPLETED, task.error
        reds = [m for m in attempts if "Test attempt" in m]
        assert len(reds) == 4
        assert [m.split(":")[0][-3:] for m in reds] == ["1/3", "2/3", "1/3", "2/3"], reds
        assert sum("green after" in m for m in attempts) == 2
    finally:
        mgr.stop()


def test_progress_line_is_human_readable(repo):
    """The M8 follow-up: 'Creating calc.py', not 'coding: create_file'."""
    script = [
        _tc("create_file", path="notes.txt", content="hi"),
        ProviderResponse(text="done"),
    ]
    mgr, _ = _manager(repo, script)
    try:
        actions = []
        mgr.events.subscribe(lambda e: actions.append(e.data.get("current_action", "")),
                             kinds=[EventKind.TASK_PROGRESS])
        mgr.wait(mgr.submit_task(Task(title="t", goal="g", workspace=str(repo),
                                      kind=TaskKind.CODING)).id, 60)
        assert "Creating notes.txt" in actions
        assert not any(a.startswith("coding:") for a in actions)
    finally:
        mgr.stop()


@pytest.mark.skipif(os.name != "nt", reason="the cmd.exe / PowerShell split is Windows-only")
def test_florist_replay_a_powershell_check_no_longer_fails_a_finished_build(tmp_path):
    """Field report 4, replayed with the live model's own calls: write the
    page, check it with Get-Item | Select-Object, list the folder, say done.
    Before the fix: 'Get-Item is not recognized' (cmd.exe) -> step failed ->
    replan -> FAILED with the site on disk. Now: COMPLETED."""
    page = "<!DOCTYPE html><html><body><h1>Bloom &amp; Petal</h1></body></html>"
    script = [
        _tc("create_folder", path="FloristWebsite"),
        _tc("write_file", path="FloristWebsite/index.html", content=page),
        _tc("run_command", command=f'Get-Item "{tmp_path / "FloristWebsite" / "index.html"}" '
                                   f'| Select-Object Name, Length, LastWriteTime'),
        _tc("list_dir", path="FloristWebsite"),
        ProviderResponse(text="Created the florist website."),
    ]
    mgr, _provider = _manager(tmp_path, script)
    try:
        task = mgr.wait(mgr.submit_task(Task(title="Create florist website", goal="Create a florist website.",
                                             workspace=str(tmp_path), kind=TaskKind.CODING)).id, 120)
        assert task.status is TaskStatus.COMPLETED, task.error
        look = next(r for c, r in zip(task.steps[0].tool_calls, task.steps[0].results)
                    if c.tool_name == "run_command")
        assert look.ok and look.data["shell"] == "powershell" and "index.html" in look.output
    finally:
        mgr.stop()
