"""run_tests - the tool that turns "I wrote code" into "the code works".

SWE-agent's central finding (docs/agents/FRAMEWORK_RESEARCH.md) is that
agents succeed or fail on tool *ergonomics* more than on model choice.
A test runner that dumps 4,000 lines of output is a tool the model
cannot use; one that says "2 failed: test_total (expected 10, got 8) in
calc.py:12" is a tool it can act on in one step. So this tool runs the
suite, then does the reading for the model: it names the failing tests,
extracts each assertion message and the file:line it points at, and
returns the raw tail only as a fallback.

Runner detection is by evidence, cheapest first: an explicit `runner`
argument, then pytest markers (pytest.ini, pyproject with [tool.pytest],
a tests/ folder or test_*.py), then package.json's "test" script (npm,
and vitest when the script says so).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from typing import Any, Optional

from agents.core import RiskLevel, ToolContext, ToolResult

from .base import Tool, make_spec

CATEGORY = "terminal"
DEFAULT_TIMEOUT_S = 300
MAX_TAIL = 3000
MAX_FAILURES = 8

_PYTEST_SUMMARY = re.compile(r"=+ (.*?) =+\s*$", re.M)
_PYTEST_COUNT = re.compile(r"(\d+) (passed|failed|error|errors|skipped|xfailed)")
_PYTEST_FAIL_LINE = re.compile(r"^(?:FAILED|ERROR) (\S+)(?: - (.*))?$", re.M)
_PYTEST_LOCATION = re.compile(r"^([\w./\\-]+\.py):(\d+):", re.M)
_ASSERT_LINE = re.compile(r"^E\s+(.*)$", re.M)

_JS_TOTALS = re.compile(r"Tests:\s+(?:(\d+) failed, )?(?:(\d+) passed, )?(\d+) total")
_VITEST_TOTALS = re.compile(r"Tests\s+(?:(\d+) failed\s*\|?\s*)?(?:(\d+) passed)?")
_JS_FAIL = re.compile(r"^\s*(?:✕|×|✗|FAIL|●)\s+(.+?)\s*(?:\(\d+ ?ms\))?$", re.M)


def detect_runner(workspace: str) -> Optional[tuple[str, list[str]]]:
    """(runner name, command) for this workspace, or None."""
    ws = workspace
    has = lambda *p: os.path.exists(os.path.join(ws, *p))  # noqa: E731

    pkg = os.path.join(ws, "package.json")
    if os.path.isfile(pkg):
        try:
            with open(pkg, encoding="utf-8") as fh:
                scripts = (json.load(fh).get("scripts") or {})
        except (OSError, ValueError):
            scripts = {}
        test_script = str(scripts.get("test") or "")
        if test_script and "no test specified" not in test_script:
            name = "vitest" if "vitest" in test_script else "npm"
            return name, ["npm", "test", "--", "--run"] if name == "vitest" else ["npm", "test"]

    pytest_markers = (has("pytest.ini") or has("conftest.py") or has("tests")
                      or has("tox.ini") or has("setup.cfg"))
    if not pytest_markers and has("pyproject.toml"):
        try:
            with open(os.path.join(ws, "pyproject.toml"), encoding="utf-8") as fh:
                pytest_markers = "[tool.pytest" in fh.read()
        except OSError:
            pass
    if not pytest_markers:
        try:
            pytest_markers = any(n.startswith("test_") and n.endswith(".py")
                                 for n in os.listdir(ws))
        except OSError:
            pass
    if pytest_markers:
        return "pytest", [sys.executable, "-m", "pytest", "-q", "-x", "--no-header",
                          "-p", "no:cacheprovider", "--tb=short"]
    return None


def summarise_pytest(output: str) -> dict[str, Any]:
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    # With -q the final line is bare ("2 passed in 0.02s"); with the
    # default verbosity it is banner-wrapped. Read the last few lines
    # rather than trusting either shape.
    tail_lines = [ln for ln in output.strip().splitlines()[-4:] if " in " in ln or "passed" in ln
                  or "failed" in ln or "error" in ln]
    for line in reversed(tail_lines):
        found = _PYTEST_COUNT.findall(line)
        if found:
            for n, word in found:
                key = "errors" if word.startswith("error") else word
                if key in counts:
                    counts[key] += int(n)
            break
    failures = []
    for name, message in _PYTEST_FAIL_LINE.findall(output):
        failures.append({"name": name, "message": (message or "").strip()[:300]})
    # Attach the first file:line and the E-lines for each failure section.
    sections = re.split(r"^_{3,} (.+?) _{3,}\s*$", output, flags=re.M)
    detail_by_name: dict[str, str] = {}
    for i in range(1, len(sections) - 1, 2):
        title, body = sections[i].strip(), sections[i + 1]
        loc = _PYTEST_LOCATION.search(body)
        asserts = [a.strip() for a in _ASSERT_LINE.findall(body)][:4]
        detail = (f"{loc.group(1)}:{loc.group(2)}  " if loc else "") + " | ".join(asserts)
        detail_by_name[title] = detail[:400]
    for f in failures:
        short = f["name"].rsplit("::", 1)[-1]
        for title, detail in detail_by_name.items():
            if short in title:
                f["where"] = detail
                break
    return {**counts, "failures": failures[:MAX_FAILURES]}


def summarise_js(output: str) -> dict[str, Any]:
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    m = _JS_TOTALS.search(output) or _VITEST_TOTALS.search(output)
    if m:
        groups = m.groups()
        counts["failed"] = int(groups[0] or 0)
        counts["passed"] = int(groups[1] or 0)
    names = []
    for name in _JS_FAIL.findall(output):
        name = name.strip()
        if name and name not in names and len(names) < MAX_FAILURES:
            names.append(name)
    return {**counts, "failures": [{"name": n, "message": ""} for n in names]}


def compose_report(runner: str, exit_code: int, summary: dict[str, Any], output: str) -> str:
    """What the model reads. Short when green, precise when red."""
    if exit_code == 0 and not summary["failed"] and not summary["errors"]:
        return (f"PASS ({runner}): {summary['passed']} passed"
                + (f", {summary['skipped']} skipped" if summary["skipped"] else ""))
    lines = [f"FAIL ({runner}): {summary['failed']} failed, {summary['passed']} passed"
             + (f", {summary['errors']} errors" if summary["errors"] else "")]
    for f in summary["failures"]:
        line = f"  ✕ {f['name']}"
        if f.get("message"):
            line += f" — {f['message']}"
        if f.get("where"):
            line += f"\n      {f['where']}"
        lines.append(line)
    if not summary["failures"]:
        tail = output.strip()[-MAX_TAIL:]
        lines.append("  (no failures parsed; output tail follows)\n" + tail)
    lines.append("Read the failing test and the code it exercises, fix the code (not the "
                 "test, unless the test is wrong), then run_tests again.")
    return "\n".join(lines)


class RunTests(Tool):
    spec = make_spec(
        "run_tests",
        "Run the project's test suite and report failures precisely: test names, "
        "assertion messages and file:line. Detects pytest / npm test / vitest.",
        risk=RiskLevel.MEDIUM, category=CATEGORY,
        properties={
            "runner": {"type": "string", "enum": ["auto", "pytest", "npm", "vitest"],
                       "description": "Default auto-detect."},
            "path": {"type": "string", "description": "Subfolder to run in. Default: workspace."},
            "args": {"type": "string", "description": "Extra arguments, e.g. a single test file."},
            "timeout": {"type": "integer", "description": "Seconds. Default 300."},
        },
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        rel = str(args.get("path") or ".")
        cwd, err = self.guard(ctx, rel)
        if err:
            return err
        if not os.path.isdir(cwd):
            return self.fail(f"not a folder: {cwd}")

        choice = str(args.get("runner") or "auto")
        detected = detect_runner(cwd)
        if choice != "auto":
            cmd = {"pytest": [sys.executable, "-m", "pytest", "-q", "-x", "--no-header",
                              "-p", "no:cacheprovider", "--tb=short"],
                   "npm": ["npm", "test"], "vitest": ["npm", "test", "--", "--run"]}[choice]
            runner = choice
        elif detected is None:
            return self.fail("no test runner found: no pytest markers, tests/ folder or "
                             "package.json test script. Create tests first.", runner=None)
        else:
            runner, cmd = detected
        extra = str(args.get("args") or "").split()
        cmd = [*cmd, *extra]
        timeout = int(args.get("timeout") or DEFAULT_TIMEOUT_S)

        try:
            proc = await asyncio.to_thread(
                subprocess.run, cmd, cwd=cwd, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
                shell=(runner != "pytest" and os.name == "nt"),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            return self.fail(f"tests timed out after {timeout}s", runner=runner, timed_out=True)
        except FileNotFoundError as exc:
            return self.fail(f"{runner} is not installed or not on PATH ({exc})", runner=runner)

        output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        summary = summarise_pytest(output) if runner == "pytest" else summarise_js(output)
        report = compose_report(runner, proc.returncode, summary, output)
        green = proc.returncode == 0 and not summary["failed"] and not summary["errors"]
        data = {"runner": runner, "exit_code": proc.returncode, **summary,
                "output_tail": output.strip()[-MAX_TAIL:]}
        return self.ok(report, **data) if green else \
            ToolResult(call_id="", tool_name=self.name, ok=False, output=report,
                       error=f"{summary['failed'] or 'some'} test(s) failed", data=data)


def tools() -> list[Tool]:
    return [RunTests()]
