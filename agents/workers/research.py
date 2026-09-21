"""ResearchWorker — gathers information from the web and writes it down.

Opens pages and extracts what the goal asks about, then can write the
findings to a file in the workspace so the result outlives the task.
"""
from __future__ import annotations

from agents.core import AgentContext

from .base import Worker


class ResearchWorker(Worker):
    worker_type = "research"
    default_tools = [
        "open_page", "extract_information",
        "create_file", "write_file", "read_file",
    ]

    def system_prompt(self, ctx: AgentContext) -> str:
        return (
            f"You are {ctx.agent.name}, a research agent. Find accurate, current "
            "information for the goal by opening pages and extracting the "
            "relevant facts. Cross-check anything that matters across more than "
            "one source, and keep track of where each fact came from. If asked "
            "to save the result, write it to a file inside "
            f"{ctx.workspace}. Never invent a source or a figure. Summarise what "
            "you found when the step is done."
        )
