"""FileWorker — creates and edits files and folders, nothing that runs.

Deliberately has no terminal or git tools: a step routed here can only touch
the filesystem inside the workspace, which keeps "make me these files" tasks
from ever launching a process.
"""
from __future__ import annotations

from agents.core import AgentContext

from .base import Worker


class FileWorker(Worker):
    worker_type = "file"
    default_tools = [
        "create_folder", "create_file", "write_file", "edit_file", "read_file",
        "list_dir", "move_path", "delete_path",
        "read_workbook", "write_workbook", "clean_data", "reconcile_sheets", "check_formulas", "compare_periods",
        "render_design",
    ]

    def system_prompt(self, ctx: AgentContext) -> str:
        return (
            f"You are {ctx.agent.name}, a file-management agent working inside "
            f"{ctx.workspace}. Create, read, edit, move and organise files and "
            "folders to satisfy the goal. Stay inside the workspace. Read before "
            "you overwrite. Never claim a change you did not make with a tool. "
            "For a poster, flyer, invitation, social media graphic, banner or any "
            "other picture, design it as one HTML page and make it with "
            "render_design (a PDF too when it is for print); look at the preview "
            "it returns and fix what is wrong before you finish. "
            "Reply with a short summary when the step is done."
        )
