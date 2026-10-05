"""default_registry() contents, risk levels, and the default-policy gate.

Every tool is real now: browser in M19 (test_tools_browser.py), computer
control in M20 (test_tools_computer.py). SAFE exceptions: close_browser
closes rather than reaches; find_on_screen and screenshot only look."""
import pytest

from agents.core import PermissionPolicy, RiskLevel, ToolCall, ToolContext, auto_deny
from agents.tools import Registry, all_tools, default_registry

EXPECTED = {
    "file": {"create_folder", "create_file", "write_file", "edit_file", "read_file", "list_dir",
             "move_path", "delete_path"},
    "terminal": {"run_command", "install_package", "execute_script", "run_tests"},
    "git": {"git_init", "git_status", "git_diff", "git_log", "git_add", "git_commit",
            "git_branch", "git_push"},
    "browser": {"open_page", "extract_information", "click_element", "fill_field",
                "screenshot_page", "close_browser"},
    "computer": {"find_on_screen", "screenshot", "move_cursor", "click", "type_text",
                 "key_press", "open_app", "list_windows", "focus_window"},
    "spreadsheet": {"read_workbook", "write_workbook", "clean_data", "reconcile_sheets", "check_formulas",
                    "compare_periods"},
    "desktop": {"list_app_actions", "app_action", "ui_control", "open_link"},
    "design": {"render_design"},
}


def test_default_registry_has_every_tool():
    reg = default_registry()
    assert isinstance(reg, Registry)
    by_cat: dict[str, set[str]] = {}
    for spec in reg.specs():
        by_cat.setdefault(spec.category, set()).add(spec.name)
    assert by_cat == EXPECTED
    assert len(all_tools()) == sum(len(v) for v in EXPECTED.values())


def test_specs_are_valid_function_definitions():
    for spec in default_registry().specs():
        assert spec.description
        assert spec.parameters["type"] == "object"
        props = spec.parameters["properties"]
        for req in spec.parameters["required"]:
            assert req in props, f"{spec.name}: required {req} not in properties"
        for name, prop in props.items():
            assert "type" in prop, f"{spec.name}.{name} has no type"


def test_risk_levels():
    reg = default_registry()
    risk = {s.name: s.risk for s in reg.specs()}
    assert risk["read_file"] is RiskLevel.SAFE and risk["list_dir"] is RiskLevel.SAFE
    assert risk["git_status"] is RiskLevel.SAFE and risk["git_diff"] is RiskLevel.SAFE
    assert risk["git_log"] is RiskLevel.SAFE
    assert risk["create_file"] is RiskLevel.LOW and risk["move_path"] is RiskLevel.LOW
    assert risk["render_design"] is RiskLevel.LOW          # writes a picture in the workspace, like create_file
    assert risk["run_command"] is RiskLevel.MEDIUM and risk["git_commit"] is RiskLevel.MEDIUM
    assert risk["delete_path"] is RiskLevel.HIGH and risk["git_push"] is RiskLevel.HIGH
    looking_only = {"close_browser", "find_on_screen", "screenshot", "list_windows"}
    assert risk["open_app"] is RiskLevel.MEDIUM and risk["focus_window"] is RiskLevel.LOW
    for name in (EXPECTED["browser"] | EXPECTED["computer"]) - looking_only - {"open_app", "focus_window"}:
        assert risk[name] is RiskLevel.HIGH, name
    for name in looking_only:
        assert risk[name] is RiskLevel.SAFE, name


async def test_high_risk_tools_need_approval_under_default_policy(tmp_path):
    ctx = ToolContext(workspace=str(tmp_path), approver=auto_deny)   # default max MEDIUM
    res = await default_registry().execute(ToolCall("click", {}), ctx)
    assert not res.ok and res.error.startswith("permission denied")
