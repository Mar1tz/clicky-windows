"""Tool framework: the Registry and every built-in tool.

    from agents.tools import default_registry
    reg = default_registry()            # everything registered
    result = await reg.execute(ToolCall("read_file", {"path": "README.md"}), ctx)

Modules: registry (the choke point), base (Tool helpers), schema
(argument validation), process (subprocess plumbing), and one module per
category - file_tools, spreadsheet_tools, design_tools, terminal_tools, test_tools, git_tools,
browser_tools, computer_tools, devlink_tools - each exposing tools() -> list[Tool].
"""
from __future__ import annotations

from . import (
    app_tools, browser_tab_tools, browser_tools, browser_verbs, computer_tools, control_tools, design_tools, devlink_tools,
    file_tools, git_tools, spreadsheet_tools, terminal_tools, test_tools, wait_tools,
)
from .base import Tool, make_spec
from .registry import Registry

_MODULES = (file_tools, spreadsheet_tools, design_tools, terminal_tools, test_tools, git_tools, browser_tools,
            browser_verbs,
            browser_tab_tools, app_tools, control_tools, computer_tools, devlink_tools, wait_tools)


def all_tools() -> list[Tool]:
    """Fresh instances of every built-in tool, in category order."""
    return [tool for module in _MODULES for tool in module.tools()]


def default_registry() -> Registry:
    """A Registry with every built-in tool registered."""
    return Registry(all_tools())


__all__ = ["Registry", "Tool", "make_spec", "all_tools", "default_registry"]
