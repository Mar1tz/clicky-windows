"""Git tools: thin wrappers around the git CLI, cwd = workspace.

Shelling out (argv, no shell) rather than a library keeps the bundle
small and behaves exactly like the user's own git - same config, same
credential helper. GIT_TERMINAL_PROMPT=0 makes a push that would ask
for credentials fail instantly instead of hanging the agent thread.

git_commit falls back to a "Tora Agent" identity only when the repo has
no user.email configured, so a fresh tmp workspace can still commit
without touching the user's global config.

Every tool takes an optional `path` (the repo folder, inside the
workspace, default workspace root) so an agent can work in a sub-project.
"""
from __future__ import annotations

import os
import shutil
from typing import Any, Optional

from agents.core import RiskLevel, ToolContext, ToolResult

from .base import Tool, make_spec
from .process import CommandOutput, run_process, truncate

CATEGORY = "git"
GIT_TIMEOUT = 60.0
PUSH_TIMEOUT = 180.0
_FALLBACK_IDENTITY = ["-c", "user.name=Tora Agent", "-c", "user.email=tora@localhost"]
_GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat", "PAGER": "cat"}

_REPO = {"type": "string", "description": "Repository folder inside the workspace. Default: workspace root."}


async def git(cwd: str, *argv: str, timeout: float = GIT_TIMEOUT) -> CommandOutput:
    return await run_process(["git", *argv], cwd=cwd, timeout=timeout, env=_GIT_ENV)


class _GitTool(Tool):
    def _repo(self, ctx: ToolContext, args: dict[str, Any]) -> tuple[str, Optional[ToolResult]]:
        cwd, err = self.guard(ctx, args.get("path") or ".")
        if err:
            return "", err
        if not os.path.isdir(cwd):
            return "", self.fail(f"folder not found: {cwd}")
        if shutil.which("git") is None:
            return "", self.fail("git is not installed or not on PATH")
        return cwd, None


class GitInit(_GitTool):
    spec = make_spec(
        "git_init", "Initialise a git repository (no-op if one exists).",
        risk=RiskLevel.LOW, category=CATEGORY,
        properties={
            "path": _REPO,
            "initial_branch": {"type": "string", "description": "Name of the first branch. Default main."},
        },
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        cwd, err = self._repo(ctx, args)
        if err:
            return err
        existed = os.path.isdir(os.path.join(cwd, ".git"))
        branch = args.get("initial_branch") or "main"
        out = await git(cwd, "init", "-b", branch)
        return self.from_process(out, path=cwd, existed=existed, branch=branch)


class GitStatus(_GitTool):
    spec = make_spec(
        "git_status", "Show the branch and changed / untracked files (porcelain format).",
        risk=RiskLevel.SAFE, category=CATEGORY, properties={"path": _REPO},
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        cwd, err = self._repo(ctx, args)
        if err:
            return err
        out = await git(cwd, "status", "--porcelain=v1", "--branch")
        if not out.ok:
            return self.from_process(out, path=cwd)
        lines = out.stdout.splitlines()
        branch = ""
        entries: list[dict[str, str]] = []
        for line in lines:
            if line.startswith("## "):
                head = line[3:]
                if head.startswith("No commits yet on "):
                    branch = head[len("No commits yet on "):]
                else:
                    branch = head.split("...", 1)[0].split(" ", 1)[0]
            elif len(line) >= 4:
                entries.append({"status": line[:2], "path": line[3:]})
        text = out.stdout.rstrip() if entries else f"## {branch}\nworking tree clean"
        return self.ok(text, path=cwd, branch=branch, entries=entries, clean=not entries)


class GitDiff(_GitTool):
    spec = make_spec(
        "git_diff", "Show unstaged (or, with staged=true, staged) changes as a unified diff.",
        risk=RiskLevel.SAFE, category=CATEGORY,
        properties={
            "path": _REPO,
            "staged": {"type": "boolean", "description": "Diff the index instead of the working tree."},
            "paths": {"type": "array", "items": {"type": "string"}, "description": "Limit to these paths."},
            "stat": {"type": "boolean", "description": "Summary (--stat) instead of the full diff."},
        },
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        cwd, err = self._repo(ctx, args)
        if err:
            return err
        argv = ["diff"]
        if args.get("staged"):
            argv.append("--cached")
        if args.get("stat"):
            argv.append("--stat")
        if args.get("paths"):
            argv += ["--", *args["paths"]]
        out = await git(cwd, *argv)
        if not out.ok:
            return self.from_process(out, path=cwd)
        diff = out.stdout
        return self.ok(truncate(diff) if diff.strip() else "no changes", path=cwd,
                       staged=bool(args.get("staged")), empty=not diff.strip(),
                       truncated=out.truncated)


class GitLog(_GitTool):
    spec = make_spec(
        "git_log", "List recent commits (newest first).",
        risk=RiskLevel.SAFE, category=CATEGORY,
        properties={
            "path": _REPO,
            "limit": {"type": "integer", "description": "Max commits. Default 20."},
        },
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        cwd, err = self._repo(ctx, args)
        if err:
            return err
        limit = max(1, min(int(args.get("limit") or 20), 500))
        out = await git(cwd, "log", f"-n{limit}", "--format=%h%x1f%an%x1f%ar%x1f%s")
        if not out.ok:
            if "does not have any commits yet" in out.stderr or "bad default revision" in out.stderr:
                return self.ok("no commits yet", path=cwd, commits=[])
            return self.from_process(out, path=cwd)
        commits = []
        for line in out.stdout.splitlines():
            parts = line.split("\x1f")
            if len(parts) == 4:
                commits.append({"hash": parts[0], "author": parts[1], "when": parts[2],
                                "subject": parts[3]})
        text = "\n".join(f"{c['hash']}  {c['when']:<16} {c['subject']}  ({c['author']})"
                         for c in commits) or "no commits yet"
        return self.ok(text, path=cwd, commits=commits)


class GitAdd(_GitTool):
    spec = make_spec(
        "git_add", "Stage files. Default: everything in the repo (\".\").",
        risk=RiskLevel.LOW, category=CATEGORY,
        properties={
            "path": _REPO,
            "paths": {"type": ["array", "string"], "items": {"type": "string"},
                      "description": "Files or folders to stage, relative to the repo."},
        },
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        cwd, err = self._repo(ctx, args)
        if err:
            return err
        raw = args.get("paths") or ["."]
        paths = [raw] if isinstance(raw, str) else [str(p) for p in raw]
        out = await git(cwd, "add", "--", *paths)
        return self.from_process(out, path=cwd, paths=paths)


class GitCommit(_GitTool):
    spec = make_spec(
        "git_commit", "Commit staged changes (all=true also stages tracked modifications).",
        risk=RiskLevel.MEDIUM, category=CATEGORY,
        properties={
            "path": _REPO,
            "message": {"type": "string", "description": "Commit message."},
            "all": {"type": "boolean", "description": "Pass -a to include modified tracked files."},
        },
        required=["message"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        cwd, err = self._repo(ctx, args)
        if err:
            return err
        message = (args.get("message") or "").strip()
        if not message:
            return self.fail("commit message must not be empty")
        ident = await git(cwd, "config", "user.email")
        prefix = [] if ident.ok and ident.stdout.strip() else _FALLBACK_IDENTITY
        argv = [*prefix, "commit", "-m", message] + (["-a"] if args.get("all") else [])
        out = await git(cwd, *argv)
        if not out.ok:
            if "nothing to commit" in out.stdout or "nothing added to commit" in out.stdout:
                return self.fail("nothing to commit", output=out.text(), **out.to_data(), path=cwd)
            return self.from_process(out, path=cwd)
        rev = await git(cwd, "rev-parse", "--short", "HEAD")
        commit = rev.stdout.strip() if rev.ok else ""
        return self.ok(out.text(), **out.to_data(), path=cwd, commit=commit, message=message)


class GitBranch(_GitTool):
    spec = make_spec(
        "git_branch",
        "Without name: list branches. With name: switch to it, creating it if needed.",
        risk=RiskLevel.LOW, category=CATEGORY,
        properties={
            "path": _REPO,
            "name": {"type": "string", "description": "Branch to create / switch to."},
        },
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        cwd, err = self._repo(ctx, args)
        if err:
            return err
        name = (args.get("name") or "").strip()
        if not name:
            out = await git(cwd, "branch", "--list", "--format=%(HEAD) %(refname:short)")
            if not out.ok:
                return self.from_process(out, path=cwd)
            branches, current = [], ""
            for line in out.stdout.splitlines():
                # "%(HEAD) %(refname:short)" -> "* main" / "  feature" (HEAD is a space)
                mark, ref = line[:1], line[2:].strip()
                if ref:
                    branches.append(ref)
                    if mark == "*":
                        current = ref
            text = "\n".join(("* " if b == current else "  ") + b for b in branches) or "no branches yet"
            return self.ok(text, path=cwd, branches=branches, current=current)
        exists = await git(cwd, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}")
        created = not exists.ok
        out = await git(cwd, "checkout", *(["-b", name] if created else [name]))
        return self.from_process(out, path=cwd, branch=name, created=created)


class GitPush(_GitTool):
    spec = make_spec(
        "git_push", "Push the current (or given) branch to a remote.",
        risk=RiskLevel.HIGH, category=CATEGORY,
        properties={
            "path": _REPO,
            "remote": {"type": "string", "description": "Remote name. Default origin."},
            "branch": {"type": "string", "description": "Branch to push. Default: current branch."},
            "set_upstream": {"type": "boolean", "description": "Pass -u. Default true."},
        },
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        cwd, err = self._repo(ctx, args)
        if err:
            return err
        remote = args.get("remote") or "origin"
        branch = (args.get("branch") or "").strip()
        if not branch:
            cur = await git(cwd, "branch", "--show-current")
            branch = cur.stdout.strip() if cur.ok else ""
            if not branch:
                return self.fail("no current branch (detached HEAD?); pass branch=")
        argv = ["push"] + (["-u"] if args.get("set_upstream", True) else []) + [remote, branch]
        out = await git(cwd, *argv, timeout=PUSH_TIMEOUT)
        return self.from_process(out, path=cwd, remote=remote, branch=branch)


def tools() -> list[Tool]:
    return [GitInit(), GitStatus(), GitDiff(), GitLog(), GitAdd(), GitCommit(), GitBranch(),
            GitPush()]
