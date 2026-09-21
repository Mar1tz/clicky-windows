"""ComputerWorker — controls the mouse and keyboard directly.

The last-resort worker for native apps with no better handle: it moves the
cursor, clicks and types on the real desktop. Highest-risk of the set, so its
tools are exactly and only the input-control ones.
"""
from __future__ import annotations

from agents.core import AgentContext

from .base import Worker


class ComputerWorker(Worker):
    worker_type = "computer"
    default_tools = [
        "move_cursor", "click", "type_text",
    ]

    def system_prompt(self, ctx: AgentContext) -> str:
        return (
            f"You are {ctx.agent.name}, a computer-control agent. Accomplish the "
            "goal by moving the cursor, clicking and typing on the real screen. "
            "Move deliberately and verify the screen reacted as expected before "
            "the next action — a wrong click on the real desktop has real "
            "consequences. Never claim an input you did not perform with a tool. "
            "Summarise what you did when the step is done."
        )
