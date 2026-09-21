"""Browser tools - specs only in Phase 1.

The specs are real so planners, providers and the permission UI can be
built and tested against the final tool names now; run() reports
"not available in Phase 1". Phase 3 evaluates `browser-use` (or a
Playwright bridge) and swaps in the implementation without changing the
contract. HIGH risk: a browser reaches the whole internet with the
user's cookies.
"""
from __future__ import annotations

from typing import Any

from agents.core import RiskLevel, ToolContext, ToolResult

from .base import Tool, make_spec

CATEGORY = "browser"
NOT_AVAILABLE = "not available in Phase 1"


class _BrowserStub(Tool):
    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return self.fail(NOT_AVAILABLE, phase=1)


class OpenPage(_BrowserStub):
    spec = make_spec(
        "open_page", "Open a URL in the agent browser and return the page text.",
        risk=RiskLevel.HIGH, category=CATEGORY,
        properties={"url": {"type": "string", "description": "http(s) URL to open."}},
        required=["url"],
    )


class ExtractInformation(_BrowserStub):
    spec = make_spec(
        "extract_information",
        "Extract specific information from the current (or given) page, described in plain language.",
        risk=RiskLevel.HIGH, category=CATEGORY,
        properties={
            "query": {"type": "string", "description": "What to extract, e.g. 'the pricing table'."},
            "url": {"type": "string", "description": "Page to open first. Default: current page."},
        },
        required=["query"],
    )


def tools() -> list[Tool]:
    return [OpenPage(), ExtractInformation()]
