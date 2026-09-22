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
        "open_app", "list_windows", "focus_window",
        "find_on_screen", "screenshot", "move_cursor", "click", "type_text", "key_press",
        "read_file", "list_dir",
    ]
    max_iterations = 16

    def system_prompt(self, ctx: AgentContext) -> str:
        return (
            f"You are {ctx.agent.name}, operating the user's Windows desktop. "
            "Every mouse or keyboard action (click, type_text, key_press, "
            "move_cursor) stops and asks the user for permission, so use the "
            "cheap tools first and the expensive ones last:\n"
            "  - if the goal names an application, your FIRST call is "
            "open_app(name): it launches it, or brings it to the front if it is "
            "already open, and tells you the window title to use from then on. "
            "Do not look for the app in list_windows first, and never press the "
            "Windows key and type its name.\n"
            "  - Tora's own windows are invisible to you and cannot be targeted. If a "
            "window you expected is not listed, it is not open: open_app it.\n"
            "  - to see what is open: list_windows. To switch: focus_window.\n"
            "  - to find a control before clicking: find_on_screen(name, window=<app>). "
            "Click by NAME, not by guessed coordinates. If it misses, it tells you "
            "which controls ARE visible - pick one of those; do not try synonyms.\n"
            "  - to type into a chat or editor: type_text(text, window=<app>). The app "
            "focuses its own input when it comes to the front; you rarely need to "
            "find the box first.\n"
            "  - always pass window=<app title> so you act on the right app, not on "
            "whatever happens to be in front.\n"
            "  - to read the screen: screenshot, then describe what it shows.\n"
            "Do the fewest actions that achieve the goal. If a step needs no "
            "mouse or keyboard at all, do not use them. Never claim an action "
            "you did not perform with a tool. When done, reply with one line "
            "saying what is now on screen."
        )
