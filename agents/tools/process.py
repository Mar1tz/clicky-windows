"""Subprocess plumbing shared by the terminal and git tools.

One place for the Windows rules - CREATE_NO_WINDOW so nothing flashes a
console over the companion, UTF-8 with errors="replace" so a stray
code-page byte never kills a tool, stdin from DEVNULL so an interactive
prompt fails fast instead of hanging the agent forever - and for
timeouts that kill the *whole* process tree: `proc.kill()` alone
terminates cmd.exe and leaves npm / python running as orphans.

Nothing here raises for a failed or missing command; callers get a
CommandOutput and turn it into a ToolResult.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Optional, Sequence, Union

IS_WINDOWS = sys.platform == "win32"
CREATION_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0
OUTPUT_CAP = 20_000                     # chars kept per stream / per file read
DEFAULT_TIMEOUT = 120.0

Command = Union[str, Sequence[str]]


@dataclass
class CommandOutput:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    duration_ms: int = 0
    command: str = ""
    cwd: str = ""
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def text(self) -> str:
        """What the LLM reads: stdout, then stderr under a separator."""
        parts = []
        if self.stdout.strip():
            parts.append(self.stdout.rstrip())
        if self.stderr.strip():
            parts.append("--- stderr ---\n" + self.stderr.rstrip())
        return "\n".join(parts)

    def to_data(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code, "stdout": self.stdout, "stderr": self.stderr,
            "timed_out": self.timed_out, "command": self.command, "cwd": self.cwd,
            "truncated": self.truncated,
        }


def truncate(text: str, limit: int = OUTPUT_CAP) -> str:
    """Keep the head of `text`; say how much was dropped so nobody is fooled."""
    if len(text) <= limit:
        return text
    dropped = len(text) - limit
    return text[:limit] + f"\n... [truncated {dropped} chars]"


def python_executable() -> str:
    """The interpreter to run scripts / pip with. Inside the PyInstaller
    bundle sys.executable is Tora.exe, so fall back to the PATH."""
    if getattr(sys, "frozen", False):
        return shutil.which("python") or shutil.which("py") or "python"
    return sys.executable


def child_env(extra: Optional[dict[str, str]] = None) -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("NO_COLOR", "1")            # ANSI escapes are noise for the LLM
    if extra:
        env.update(extra)
    return env


async def run_process(
    command: Command,
    *,
    cwd: str,
    timeout: float = DEFAULT_TIMEOUT,
    env: Optional[dict[str, str]] = None,
) -> CommandOutput:
    """Run `command` to completion. A `str` goes through the shell (needed
    for npm/npx .cmd shims on Windows); a sequence is exec'd directly."""
    shell = isinstance(command, str)
    display = command if shell else subprocess.list2cmdline(list(command))
    started = time.perf_counter()
    kwargs: dict[str, Any] = dict(
        cwd=cwd, env=child_env(env), stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    if CREATION_FLAGS:
        kwargs["creationflags"] = CREATION_FLAGS

    try:
        if shell:
            proc = await asyncio.create_subprocess_shell(command, **kwargs)
        else:
            proc = await asyncio.create_subprocess_exec(*command, **kwargs)
    except (OSError, ValueError) as exc:      # not found, bad cwd, bad argv
        return CommandOutput(exit_code=127, stderr=f"{type(exc).__name__}: {exc}",
                             command=display, cwd=cwd)

    timed_out = False
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        timed_out = True
        out_b, err_b = b"", b""
        await _kill_tree(proc)
    except asyncio.CancelledError:
        await _kill_tree(proc)
        raise

    stdout = out_b.decode("utf-8", errors="replace")
    stderr = err_b.decode("utf-8", errors="replace")
    truncated = len(stdout) > OUTPUT_CAP or len(stderr) > OUTPUT_CAP
    if timed_out:
        stderr = (stderr + "\n" if stderr else "") + f"timed out after {timeout:g}s"
    return CommandOutput(
        exit_code=-1 if timed_out else (proc.returncode if proc.returncode is not None else -1),
        stdout=truncate(stdout), stderr=truncate(stderr), timed_out=timed_out,
        duration_ms=int((time.perf_counter() - started) * 1000),
        command=display, cwd=cwd, truncated=truncated,
    )


async def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    if IS_WINDOWS:
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/F", "/T", "/PID", str(proc.pid),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                creationflags=CREATION_FLAGS,
            )
            await asyncio.wait_for(killer.wait(), 10)
        except Exception:  # noqa: BLE001 - best effort, fall through to kill()
            pass
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(proc.wait(), 10)
    except asyncio.TimeoutError:
        pass
