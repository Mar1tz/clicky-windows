"""Computer-control tools - specs only in Phase 1.

Phase 3 bridges these to ai/hybrid_pointer.py (the same Gemini-guided
pointer the companion already uses for "click that button"), which owns
the multi-monitor / DPI resolution - do not reimplement screen maths
here. Until then run() reports "not available in Phase 1". HIGH risk:
moving the mouse and typing touches everything on the user's desktop,
not just the workspace.
"""
from __future__ import annotations

from typing import Any

from agents.core import RiskLevel, ToolContext, ToolResult

from .base import Tool, make_spec

CATEGORY = "computer"
NOT_AVAILABLE = "not available in Phase 1"

_X = {"type": "integer", "description": "Screen x in physical pixels (virtual desktop coordinates)."}
_Y = {"type": "integer", "description": "Screen y in physical pixels (virtual desktop coordinates)."}


class _ComputerStub(Tool):
    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return self.fail(NOT_AVAILABLE, phase=1)


class MoveCursor(_ComputerStub):
    spec = make_spec(
        "move_cursor", "Move the mouse cursor to a screen position.",
        risk=RiskLevel.HIGH, category=CATEGORY,
        properties={"x": _X, "y": _Y}, required=["x", "y"],
    )


class Click(_ComputerStub):
    spec = make_spec(
        "click", "Click the mouse at the current position, or at x/y if given.",
        risk=RiskLevel.HIGH, category=CATEGORY,
        properties={
            "x": _X, "y": _Y,
            "button": {"type": "string", "enum": ["left", "right", "middle"], "description": "Default left."},
            "double": {"type": "boolean", "description": "Double-click. Default false."},
        },
    )


class TypeText(_ComputerStub):
    spec = make_spec(
        "type_text", "Type text into the focused window as keyboard input.",
        risk=RiskLevel.HIGH, category=CATEGORY,
        properties={"text": {"type": "string", "description": "Text to type."}},
        required=["text"],
    )


def tools() -> list[Tool]:
    return [MoveCursor(), Click(), TypeText()]
