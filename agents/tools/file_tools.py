"""Filesystem tools. Everything stays inside ToolContext.workspace.

Design notes:
  * Every tool resolves its path through Tool.guard() first - the
    workspace fence is the one hard safety rule for an agent that edits
    the user's disk, so it is applied before any other check.
  * Text is UTF-8. Reads decode with errors="replace" so a stray byte
    never blocks the agent; writes are byte-exact (newline="") so the
    LLM's "\\n" produces LF files, which is what web tooling expects.
  * edit_file matches against LF-normalised text and writes back in the
    file's own line-ending style: the LLM only ever sees "\\n" (read_file
    normalises), so its old_string must match CRLF files too.
  * read_file / list_dir cap their output so one huge file cannot blow
    the provider's context.
"""
from __future__ import annotations

import os
import shutil
import stat
from typing import Any, Callable

from agents.core import RiskLevel, ToolContext, ToolResult

from .base import Tool, make_spec
from .process import OUTPUT_CAP, truncate

CATEGORY = "file"
MAX_READ_BYTES = 20 * 1024 * 1024
LIST_MAX_ENTRIES = 200                          # per directory
LIST_SKIP_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".tora_tmp", ".venv", "venv",
    "dist", "build", ".next", ".pytest_cache", ".mypy_cache",
})

_PATH = {"type": "string", "description": "Path, relative to the workspace or absolute (must be inside it)."}


def _write_text(path: str, content: str) -> int:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    data = content.encode("utf-8")
    with open(path, "wb") as f:
        f.write(data)
    return len(data)


def _read_text(path: str, *, raw: bool = False) -> str:
    with open(path, encoding="utf-8", errors="replace", newline="" if raw else None) as f:
        return f.read()


def _rmtree(path: str) -> None:
    """rmtree that copes with read-only files (git objects on Windows)."""
    def _onexc(func: Callable[..., Any], p: str, _exc: BaseException) -> None:
        os.chmod(p, stat.S_IWRITE)
        func(p)
    shutil.rmtree(path, onexc=_onexc)


class CreateFolder(Tool):
    spec = make_spec(
        "create_folder", "Create a directory (and any missing parents) inside the workspace.",
        risk=RiskLevel.LOW, category=CATEGORY, properties={"path": _PATH}, required=["path"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        target, err = self.guard(ctx, args["path"])
        if err:
            return err
        if os.path.isdir(target):
            return self.ok(f"folder already exists: {target}", path=target, created=False)
        if os.path.exists(target):
            return self.fail(f"a file already exists at {target}")
        try:
            os.makedirs(target)
        except OSError as exc:
            return self.fail(f"cannot create {target}: {exc}")
        return self.ok(f"created folder {target}", path=target, created=True)


class CreateFile(Tool):
    spec = make_spec(
        "create_file",
        "Create a new UTF-8 text file with the given content. Fails if the file exists "
        "unless overwrite is true. Parent folders are created.",
        risk=RiskLevel.LOW, category=CATEGORY,
        properties={
            "path": _PATH,
            "content": {"type": "string", "description": "Full file content."},
            "overwrite": {"type": "boolean", "description": "Replace an existing file. Default false."},
        },
        required=["path"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        target, err = self.guard(ctx, args["path"])
        if err:
            return err
        if os.path.isdir(target):
            return self.fail(f"a folder exists at {target}")
        content = args.get("content", "") or ""
        if os.path.exists(target) and not args.get("overwrite", False):
            # Idempotent on identical content: a step interrupted after it
            # wrote the file (app closed, machine slept) re-runs on resume,
            # and "already exists" would fail work that is actually done.
            # Different content is still a real conflict.
            try:
                with open(target, encoding="utf-8", errors="replace") as fh:
                    if fh.read() == content:
                        return self.ok(f"already has this content: {target}",
                                       path=target, bytes=len(content.encode("utf-8")),
                                       created=False)
            except OSError:
                pass
            return self.fail(f"file already exists: {target} (pass overwrite=true or use edit_file)")
        try:
            n = _write_text(target, content)
        except OSError as exc:
            return self.fail(f"cannot write {target}: {exc}")
        return self.ok(f"wrote {n} bytes to {target}", path=target, bytes=n)


class WriteFile(Tool):
    spec = make_spec(
        "write_file",
        "Write (create or fully replace) a UTF-8 text file. Parent folders are created.",
        risk=RiskLevel.LOW, category=CATEGORY,
        properties={"path": _PATH, "content": {"type": "string", "description": "Full file content."}},
        required=["path", "content"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        target, err = self.guard(ctx, args["path"])
        if err:
            return err
        if os.path.isdir(target):
            return self.fail(f"a folder exists at {target}")
        existed = os.path.exists(target)
        try:
            n = _write_text(target, args["content"])
        except OSError as exc:
            return self.fail(f"cannot write {target}: {exc}")
        return self.ok(f"wrote {n} bytes to {target}", path=target, bytes=n, created=not existed)


class EditFile(Tool):
    spec = make_spec(
        "edit_file",
        "Replace one exact occurrence of old_string with new_string in a text file. "
        "old_string must appear exactly once - include enough surrounding lines to be unique.",
        risk=RiskLevel.LOW, category=CATEGORY,
        properties={
            "path": _PATH,
            "old_string": {"type": "string", "description": "Exact text to find (must be unique)."},
            "new_string": {"type": "string", "description": "Replacement text."},
        },
        required=["path", "old_string", "new_string"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        target, err = self.guard(ctx, args["path"])
        if err:
            return err
        if not os.path.isfile(target):
            return self.fail(f"file not found: {target}")
        old = args["old_string"].replace("\r\n", "\n")
        new = args["new_string"].replace("\r\n", "\n")
        if not old:
            return self.fail("old_string must not be empty")
        try:
            raw = _read_text(target, raw=True)
        except OSError as exc:
            return self.fail(f"cannot read {target}: {exc}")
        crlf = "\r\n" in raw
        text = raw.replace("\r\n", "\n")
        count = text.count(old)
        if count == 0:
            return self.fail(f"old_string not found in {target}")
        if count > 1:
            return self.fail(f"old_string occurs {count} times in {target}; "
                             "include more surrounding context so it matches exactly once")
        index = text.index(old)
        line = text.count("\n", 0, index) + 1
        updated = text[:index] + new + text[index + len(old):]
        if crlf:
            updated = updated.replace("\n", "\r\n")
        try:
            n = _write_text(target, updated)
        except OSError as exc:
            return self.fail(f"cannot write {target}: {exc}")
        return self.ok(f"edited {target} at line {line}", path=target, line=line, bytes=n)


class ReadFile(Tool):
    spec = make_spec(
        "read_file",
        "Read a UTF-8 text file, optionally a 1-based inclusive line range. "
        f"Output is capped at {OUTPUT_CAP} characters.",
        risk=RiskLevel.SAFE, category=CATEGORY,
        properties={
            "path": _PATH,
            "start_line": {"type": "integer", "description": "First line to return (1-based)."},
            "end_line": {"type": "integer", "description": "Last line to return (inclusive)."},
        },
        required=["path"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        target, err = self.guard(ctx, args["path"])
        if err:
            return err
        if os.path.isdir(target):
            return self.fail(f"{target} is a folder; use list_dir")
        if not os.path.isfile(target):
            return self.fail(f"file not found: {target}")
        size = os.path.getsize(target)
        if size > MAX_READ_BYTES:
            return self.fail(f"file too large to read ({size} bytes): {target}")
        try:
            text = _read_text(target)
        except OSError as exc:
            return self.fail(f"cannot read {target}: {exc}")
        lines = text.splitlines(keepends=True)
        total = len(lines)
        start = max(1, int(args.get("start_line") or 1))
        end = int(args.get("end_line") or total)
        end = max(start, min(end, total)) if total else 0
        if total and start > total:
            return self.fail(f"start_line {start} is beyond the end of the file ({total} lines)")
        chunk = "".join(lines[start - 1:end]) if total else ""
        truncated = len(chunk) > OUTPUT_CAP
        return self.ok(truncate(chunk), path=target, bytes=size, line_count=total,
                       start_line=start if total else 0, end_line=end, truncated=truncated)


class ListDir(Tool):
    spec = make_spec(
        "list_dir",
        "List a folder as a tree. depth=1 shows direct children; larger values recurse. "
        "Heavy folders (node_modules, .git, ...) are shown but not expanded.",
        risk=RiskLevel.SAFE, category=CATEGORY,
        properties={
            "path": {"type": "string", "description": "Folder to list. Default: the workspace root."},
            "depth": {"type": "integer", "description": "Levels to descend (1-8). Default 2."},
        },
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        target, err = self.guard(ctx, args.get("path") or ".")
        if err:
            return err
        if not os.path.isdir(target):
            return self.fail(f"folder not found: {target}")
        depth = max(1, min(int(args.get("depth") or 2), 8))
        counts = {"dirs": 0, "files": 0, "truncated": False}
        lines = [(os.path.basename(target.rstrip("\\/")) or target) + "/"]
        self._walk(target, depth, "", lines, counts)
        output = "\n".join(lines)
        truncated = counts["truncated"] or len(output) > OUTPUT_CAP
        return self.ok(truncate(output), path=target, dirs=counts["dirs"], files=counts["files"],
                       depth=depth, truncated=truncated)

    def _walk(self, folder: str, depth: int, prefix: str, lines: list[str],
              counts: dict[str, Any]) -> None:
        try:
            entries = sorted(os.scandir(folder), key=lambda e: (not e.is_dir(), e.name.lower()))
        except OSError as exc:
            lines.append(f"{prefix}└── [error: {exc}]")
            return
        shown = entries[:LIST_MAX_ENTRIES]
        if len(entries) > LIST_MAX_ENTRIES:
            counts["truncated"] = True
        for i, entry in enumerate(shown):
            last = i == len(shown) - 1 and len(entries) <= LIST_MAX_ENTRIES
            branch = "└── " if last else "├── "
            child_prefix = prefix + ("    " if last else "│   ")
            if entry.is_dir(follow_symlinks=False):
                counts["dirs"] += 1
                if entry.name in LIST_SKIP_DIRS:
                    lines.append(f"{prefix}{branch}{entry.name}/ (not expanded)")
                elif depth > 1:
                    lines.append(f"{prefix}{branch}{entry.name}/")
                    self._walk(entry.path, depth - 1, child_prefix, lines, counts)
                else:
                    lines.append(f"{prefix}{branch}{entry.name}/")
            else:
                counts["files"] += 1
                try:
                    size = entry.stat(follow_symlinks=False).st_size
                    lines.append(f"{prefix}{branch}{entry.name}  ({_human(size)})")
                except OSError:
                    lines.append(f"{prefix}{branch}{entry.name}")
        if len(entries) > LIST_MAX_ENTRIES:
            lines.append(f"{prefix}└── ... {len(entries) - LIST_MAX_ENTRIES} more entries")


def _human(n: float) -> str:
    if n < 1024:
        return f"{n:.0f} B"
    for unit in ("KB", "MB", "GB"):
        n /= 1024
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
    return f"{n:.1f} GB"


class MovePath(Tool):
    spec = make_spec(
        "move_path",
        "Move or rename a file or folder inside the workspace. destination is the new full path.",
        risk=RiskLevel.LOW, category=CATEGORY,
        properties={
            "source": _PATH,
            "destination": _PATH,
            "overwrite": {"type": "boolean", "description": "Replace an existing destination file. Default false."},
        },
        required=["source", "destination"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        src, err = self.guard(ctx, args["source"])
        if err:
            return err
        dst, err = self.guard(ctx, args["destination"])
        if err:
            return err
        if not os.path.exists(src):
            return self.fail(f"source not found: {src}")
        if os.path.exists(dst):
            if not args.get("overwrite", False):
                return self.fail(f"destination already exists: {dst} (pass overwrite=true)")
            if os.path.isdir(dst):
                return self.fail(f"destination is a folder: {dst}; delete it first")
            try:
                os.remove(dst)
            except OSError as exc:
                return self.fail(f"cannot replace {dst}: {exc}")
        try:
            os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
            shutil.move(src, dst)
        except OSError as exc:
            return self.fail(f"cannot move {src} -> {dst}: {exc}")
        return self.ok(f"moved {src} -> {dst}", source=src, destination=dst)


class DeletePath(Tool):
    spec = make_spec(
        "delete_path",
        "Delete a file or folder inside the workspace. Folders need recursive=true unless empty. "
        "The workspace root itself cannot be deleted.",
        risk=RiskLevel.HIGH, category=CATEGORY,
        properties={
            "path": _PATH,
            "recursive": {"type": "boolean", "description": "Delete a folder and everything in it."},
        },
        required=["path"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        target, err = self.guard(ctx, args["path"])
        if err:
            return err
        roots = [ctx.resolve_path(r) for r in (ctx.policy.workspace_roots or [ctx.workspace])]
        roots.append(ctx.resolve_path(ctx.workspace))
        if any(os.path.normcase(target) == os.path.normcase(r) for r in roots):
            return self.fail(f"refusing to delete the workspace root: {target}")
        if not os.path.lexists(target):
            return self.fail(f"not found: {target}")
        try:
            if os.path.isdir(target) and not os.path.islink(target):
                if os.listdir(target) and not args.get("recursive", False):
                    return self.fail(f"folder is not empty: {target} (pass recursive=true)")
                _rmtree(target)
                kind = "folder"
            else:
                os.remove(target)
                kind = "file"
        except OSError as exc:
            return self.fail(f"cannot delete {target}: {exc}")
        return self.ok(f"deleted {kind} {target}", path=target, kind=kind)


def tools() -> list[Tool]:
    return [CreateFolder(), CreateFile(), WriteFile(), EditFile(), ReadFile(), ListDir(),
            MovePath(), DeletePath()]
