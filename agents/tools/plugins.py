"""Hooks around every tool call (EN-32, after SwarmClaw's SwarmForge).

`Registry.execute()` is the one door every tool call goes through; a
plugin stands at it. `before_tool_call` may veto a call (it fails as
"blocked", error class blocked) - it can never let one skip its approval,
which comes after. `after_tool_call` may change what a result says before
anyone reads it.

Two ship with HeyTora:

* shell_gate - commands no job should ever run are refused outright, not
  asked about: wiping a disk, deleting the system, the boot settings, the
  shadow copies. Everything merely risky still reaches the person (the
  command check in terminal_tools.py).
* redact_output - keys and tokens a tool happened to print (an .env read,
  a command's output) are blanked before the model, the log or the
  timeline sees them.

(SwarmClaw's token limiter is already HeyTora's per-job budget,
agents/manager/limits.py.) Loading people's own plugin files is left for
later: code from a data folder running inside HeyTora needs its own yes.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from agents.core import ToolCall, ToolContext, ToolResult

log = logging.getLogger("agents.plugins")


class Plugin:
    name = "plugin"

    def before_tool_call(self, call: ToolCall, ctx: ToolContext) -> Optional[str]:
        """A reason to refuse the call, or None."""
        return None

    def after_tool_call(self, call: ToolCall, result: ToolResult, ctx: ToolContext) -> ToolResult:
        return result


# Never run by a job, whoever asks: the disk, the system, the way back.
_NEVER = re.compile(
    r"\bformat(\.com)?\s+[a-z]:|\bdiskpart\b|\bbcdedit\b|\bvssadmin\b.*\bdelete\b|\bcipher\s+/w\b|"
    r"\brm\s+-[a-z]*r[a-z]*\s+(/|~|\$home|c:[\\/]?)(\s|$)|"
    r"\b(del|erase|rmdir|rd)\b[^\n]*\s/s\b[^\n]*\b[a-z]:\\?(windows|\s|$)|"
    r"\bremove-item\b[^\n]*\b[a-z]:\\(windows)?\s*(-|$)[^\n]*-recurse|"
    r"\b(stop-process|taskkill)\b[^\n]*\b(csrss|wininit|lsass|svchost)\b", re.I)


class ShellGate(Plugin):
    name = "shell_gate"
    TOOLS = ("run_command", "execute_script")

    def before_tool_call(self, call, ctx):
        if call.tool_name not in self.TOOLS:
            return None
        text = str((call.arguments or {}).get("command") or (call.arguments or {}).get("code") or "")
        if _NEVER.search(text):
            return "that command could wipe a disk or break Windows - HeyTora never runs it"
        return None


class RedactOutput(Plugin):
    name = "redact_output"

    def after_tool_call(self, call, result, ctx):
        try:
            from logsafe import redact
        except Exception:  # noqa: BLE001 - outside the app (tests of agents alone)
            return result
        if result.output:
            clean = redact(result.output)
            if clean != result.output:
                result.output = clean
                result.data["redacted"] = True
        if result.error:
            result.error = redact(result.error)
        return result


_plugins: list[Plugin] = [ShellGate(), RedactOutput()]


def active() -> list[Plugin]:
    return list(_plugins)


def use(plugins: list[Plugin]) -> None:
    """Tests (and later, the person's own plugins) set the list."""
    global _plugins
    _plugins = list(plugins)


def before(call: ToolCall, ctx: ToolContext) -> Optional[str]:
    for p in _plugins:
        try:
            veto = p.before_tool_call(call, ctx)
        except Exception:  # noqa: BLE001 - a broken plugin refuses, it never waves through
            log.exception("plugin %s failed before %s", p.name, call.tool_name)
            veto = f"the {p.name} check failed"
        if veto:
            return f"{p.name}: {veto}"
    return None


def after(call: ToolCall, result: ToolResult, ctx: ToolContext) -> ToolResult:
    for p in _plugins:
        try:
            result = p.after_tool_call(call, result, ctx) or result
        except Exception:  # noqa: BLE001 - the result stands as it was
            log.exception("plugin %s failed after %s", p.name, call.tool_name)
    return result
