"""BrowserWorker — drives a web page: navigate, read, click, type.

Distinct from research (which only reads): this worker interacts — filling a
form, clicking through a flow — so it carries the page-action tools on top of
navigation and extraction.
"""
from __future__ import annotations

from agents.core import AgentContext

from .base import Worker


class BrowserWorker(Worker):
    worker_type = "browser"
    default_tools = [
        "open_page", "extract_information", "click_element", "fill_field",
        "screenshot_page", "close_browser", "ask_to_sign_in",
    ]

    def system_prompt(self, ctx: AgentContext) -> str:
        return (
            f"You are {ctx.agent.name}, a browser agent. Accomplish the goal by "
            "navigating and interacting with web pages: open the page, read what "
            "is there, then click and type to move the flow forward. Confirm each "
            "action had the effect you expected before the next one — read the "
            "page again if unsure. Never claim you clicked or typed something you "
            "did not do with a tool. Summarise the outcome when the step is done."
        )
