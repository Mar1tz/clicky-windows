"""TerminalWorker — runs commands, installs packages, executes scripts.

The worker for "install X", "run the build", "start the dev server". It reads
files (to know what to run) but does its work through the process tools.
"""
from __future__ import annotations

from agents.core import AgentContext

from .base import Worker


class TerminalWorker(Worker):
    worker_type = "terminal"
    default_tools = [
        "open_app", "list_windows", "focus_window",
        "run_command", "install_package", "execute_script",
        "read_file", "list_dir",
    ]

    def system_prompt(self, ctx: AgentContext) -> str:
        return (
            f"You are {ctx.agent.name}, a terminal agent working inside "
            f"{ctx.workspace}. Run commands to accomplish the goal. Check a "
            "command's output before deciding it worked; if it failed, read the "
            "error and try a corrected command rather than repeating it. Prefer "
            "non-interactive flags. Never claim a command ran that you did not "
            "run with a tool. Summarise the outcome when the step is done."
        )
