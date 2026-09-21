"""RuleVerifier: "did it really happen?" checks against the file system.

An LLM (or a buggy tool) can claim success without producing the effect.
The verifier ignores what was said and looks at what is there: a folder
the step created must exist, a written file must be non-empty, a command
must have exited 0, a commit must show up in `git log`. One Check per
ToolResult keeps the report readable in the timeline ("create_file
MyApp/src/App.jsx ✓").

Rules are keyed by tool name so worker 1 can add tools without touching
this file; an unknown tool passes with detail "no rule", because a
missing rule must not fail a task - but it is visible in the report so
a rule can be added later. A ToolResult with ok=False always fails,
regardless of the rule.
"""
from __future__ import annotations

import asyncio
import glob
import importlib.util
import os
import re
import subprocess
import sys
from typing import Any, Callable, Optional

from agents.core import (
    AgentContext, Check, Step, StepStatus, ToolCall, ToolResult, VerificationResult, Verifier,
)

Rule = Callable[[dict[str, Any], ToolResult, AgentContext], Check]

_NODE_MANAGERS = {"npm", "pnpm", "yarn", "bun"}
_PY_MANAGERS = {"pip", "pip3", "uv", "poetry", "pipenv", "conda"}


class RuleVerifier(Verifier):
    def __init__(self, rules: Optional[dict[str, Rule]] = None) -> None:
        self.rules: dict[str, Rule] = {
            "create_folder": _rule_folder_exists,
            "create_file": _rule_file_written,
            "write_file": _rule_file_written,
            "edit_file": _rule_file_edited,
            "delete_path": _rule_path_gone,
            "run_command": _rule_exit_code,
            "install_package": _rule_installed,
            "git_init": _rule_git_init,
            "git_commit": _rule_git_commit,
        }
        if rules:
            self.rules.update(rules)

    # -- Verifier ------------------------------------------------------------

    async def verify_step(self, step: Step, ctx: AgentContext) -> VerificationResult:
        checks = await asyncio.to_thread(self._checks_for_step, step, ctx)
        return _result(checks, none_msg="nothing to verify")

    async def verify_task(self, ctx: AgentContext) -> VerificationResult:
        checks: list[Check] = []
        ws = ctx.workspace
        checks.append(Check("workspace", os.path.isdir(ws),
                            ws if os.path.isdir(ws) else f"workspace missing: {ws}"))
        verified = 0
        for step in ctx.task.steps:
            if step.status is StepStatus.SKIPPED:
                continue
            verified += 1
            for c in await asyncio.to_thread(self._checks_for_step, step, ctx):
                checks.append(Check(f"{step.title}: {c.name}", c.passed, c.detail))
        failed = sum(1 for c in checks if not c.passed)
        summary = (f"{verified} step(s) verified, {len(checks)} check(s), {failed} failed"
                   if failed else f"{verified} step(s) verified, {len(checks)} check(s) passed")
        return VerificationResult(ok=failed == 0, checks=checks, summary=summary)

    # -- internals -----------------------------------------------------------

    def _checks_for_step(self, step: Step, ctx: AgentContext) -> list[Check]:
        checks: list[Check] = []
        if step.status is StepStatus.FAILED:
            checks.append(Check("status", False, step.error or "step reported failure"))
        if step.tool_calls and not step.results:
            detail = ("step never ran" if step.status is StepStatus.PENDING
                      else f"{len(step.tool_calls)} scripted call(s) but no results recorded")
            checks.append(Check("executed", False, detail))
        calls = {c.id: c for c in step.tool_calls}
        # A red test run inside a fix loop is information, not a failure:
        # the step's claim is "tests pass NOW", so only the last run_tests
        # result counts. Every other tool's failure still fails the step.
        last_test_run = next((r for r in reversed(step.results) if r.tool_name == "run_tests"), None)
        for res in step.results:
            if res.tool_name == "run_tests" and res is not last_test_run:
                continue
            call = calls.get(res.call_id)
            args = dict(call.arguments) if call else {}
            # Tools echo the path they acted on in data; useful when the
            # call is missing (LLM loop appended only the result).
            for k in ("path", "cwd"):
                if k not in args and k in res.data:
                    args[k] = res.data[k]
            checks.append(self._check_result(args, res, ctx))
        return checks

    def _check_result(self, args: dict[str, Any], res: ToolResult, ctx: AgentContext) -> Check:
        name = f"{res.tool_name} {args.get('path') or args.get('command') or ''}".strip()
        if not res.ok:
            return Check(name, False, res.error or "tool reported failure")
        rule = self.rules.get(res.tool_name)
        if rule is None:
            return Check(name, True, "no rule")
        try:
            c = rule(args, res, ctx)
        except Exception as exc:  # noqa: BLE001 - a broken rule must not crash verification
            return Check(name, False, f"rule error: {exc}")
        return Check(name, c.passed, c.detail) if not c.name else c


def _result(checks: list[Check], *, none_msg: str) -> VerificationResult:
    if not checks:
        return VerificationResult(ok=True, checks=[Check("no-op", True, none_msg)], summary=none_msg)
    failed = [c for c in checks if not c.passed]
    summary = (f"{len(checks) - len(failed)}/{len(checks)} checks passed"
               + (f"; failed: {', '.join(c.name for c in failed)}" if failed else ""))
    return VerificationResult(ok=not failed, checks=checks, summary=summary)


# -- path helpers ------------------------------------------------------------

def _resolve(ctx: AgentContext, path: Optional[str]) -> str:
    return ctx.tool_context().resolve_path(str(path) if path else ".")


def _cwd(args: dict[str, Any], ctx: AgentContext) -> str:
    return _resolve(ctx, args.get("cwd") or args.get("path") or ".")


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


# -- rules -------------------------------------------------------------------

def _rule_folder_exists(args: dict[str, Any], res: ToolResult, ctx: AgentContext) -> Check:
    p = _resolve(ctx, args.get("path"))
    return Check("", os.path.isdir(p), p if os.path.isdir(p) else f"directory missing: {p}")


def _rule_file_written(args: dict[str, Any], res: ToolResult, ctx: AgentContext) -> Check:
    p = _resolve(ctx, args.get("path"))
    if not os.path.isfile(p):
        return Check("", False, f"file missing: {p}")
    if args.get("content") and os.path.getsize(p) == 0:
        return Check("", False, f"file is empty: {p}")
    return Check("", True, f"{os.path.getsize(p)} bytes")


def _rule_file_edited(args: dict[str, Any], res: ToolResult, ctx: AgentContext) -> Check:
    p = _resolve(ctx, args.get("path"))
    if not os.path.isfile(p):
        return Check("", False, f"file missing: {p}")
    new = args.get("new_string") or args.get("new") or args.get("replacement")
    if not new:
        return Check("", True, "file exists (no new_string to look for)")
    return Check("", new in _read(p), "new text present" if new in _read(p) else f"new text not found in {p}")


def _rule_path_gone(args: dict[str, Any], res: ToolResult, ctx: AgentContext) -> Check:
    p = _resolve(ctx, args.get("path"))
    gone = not os.path.exists(p)
    return Check("", gone, "removed" if gone else f"still exists: {p}")


def _rule_exit_code(args: dict[str, Any], res: ToolResult, ctx: AgentContext) -> Check:
    code = res.data.get("exit_code", res.data.get("returncode"))
    if code is None:
        return Check("", True, "no exit_code reported; trusting ok=True")
    try:
        ok = int(code) == 0
    except (TypeError, ValueError):
        return Check("", False, f"unreadable exit_code: {code!r}")
    return Check("", ok, f"exit_code={code}")


def _rule_installed(args: dict[str, Any], res: ToolResult, ctx: AgentContext) -> Check:
    manager = str(args.get("manager") or "").lower()
    cwd = _cwd(args, ctx)
    pkgs = args.get("packages") or []
    if isinstance(pkgs, str):
        pkgs = [pkgs]
    if manager in _PY_MANAGERS or (not manager and not os.path.isfile(os.path.join(cwd, "package.json"))):
        return _python_install_evidence(cwd, pkgs)
    nm = os.path.join(cwd, "node_modules")
    if os.path.isdir(nm):
        return Check("", True, nm)
    return Check("", False, f"node_modules missing in {cwd}")


def _python_install_evidence(cwd: str, pkgs: list[str]) -> Check:
    patterns = ["*venv*/Lib/site-packages", "*venv*/lib/python*/site-packages",
                ".venv/Lib/site-packages", ".venv/lib/python*/site-packages"]
    for pat in patterns:
        hits = glob.glob(os.path.join(cwd, pat))
        if hits:
            return Check("", True, hits[0])
    for raw in pkgs:
        mod = re.split(r"[<>=!~\[; ]", str(raw), 1)[0].replace("-", "_")
        if mod and importlib.util.find_spec(mod) is not None:
            return Check("", True, f"{mod} importable")
    return Check("", False, f"no site-packages under {cwd} and none of {pkgs} importable")


def _rule_git_init(args: dict[str, Any], res: ToolResult, ctx: AgentContext) -> Check:
    p = os.path.join(_cwd(args, ctx), ".git")
    return Check("", os.path.exists(p), p if os.path.exists(p) else f".git missing: {p}")


def _rule_git_commit(args: dict[str, Any], res: ToolResult, ctx: AgentContext) -> Check:
    cwd = _cwd(args, ctx)
    try:
        proc = subprocess.run(
            ["git", "log", "-1", "--format=%h %s"], cwd=cwd, capture_output=True,
            encoding="utf-8", errors="replace", timeout=15, creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return Check("", False, f"git log failed: {exc}")
    if proc.returncode != 0:
        return Check("", False, (proc.stderr or proc.stdout).strip() or "git log -1 failed")
    return Check("", True, proc.stdout.strip())


_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
