"""CodingWorker — scaffolds and edits code projects.

The generalist of the set: it needs the file tools to lay out a project, the
terminal tools to install and run, and git to initialise a repo. Most
"build me an app" tasks land here.
"""
from __future__ import annotations

from agents.core import AgentContext

from .base import Worker


class CodingWorker(Worker):
    worker_type = "coding"
    default_tools = [
        "create_folder", "create_file", "write_file", "edit_file", "read_file",
        "list_dir", "move_path", "delete_path",
        "run_command", "install_package", "execute_script",
        "git_init", "git_status", "git_diff", "git_log", "git_add",
        "git_commit", "git_branch",
    ]

    def system_prompt(self, ctx: AgentContext) -> str:
        return (
            f"You are {ctx.agent.name}, an autonomous software engineer working "
            f"inside {ctx.workspace}. Build exactly what the goal asks for and no "
            "more. Prefer small, verifiable steps: create files, then run the "
            "project to check it works. Use real, runnable code — never a "
            "placeholder or a TODO you leave unfilled. Every path you touch stays "
            "inside the workspace. Never claim an action you did not perform with "
            "a tool. When the step is complete, reply with a one-line summary."
        )
