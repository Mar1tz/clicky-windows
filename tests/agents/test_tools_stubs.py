"""default_registry() contents and the Phase-1 browser / computer stubs."""
import pytest

from agents.core import PermissionPolicy, RiskLevel, ToolCall, ToolContext, auto_deny
from agents.tools import Registry, all_tools, default_registry

EXPECTED = {
    "file": {"create_folder", "create_file", "write_file", "edit_file", "read_file", "list_dir",
             "move_path", "delete_path"},
    "terminal": {"run_command", "install_package", "execute_script"},
    "git": {"git_init", "git_status", "git_diff", "git_log", "git_add", "git_commit",
            "git_branch", "git_push"},
    "browser": {"open_page", "extract_information"},
    "computer": {"move_cursor", "click", "type_text"},
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
    assert risk["run_command"] is RiskLevel.MEDIUM and risk["git_commit"] is RiskLevel.MEDIUM
    assert risk["delete_path"] is RiskLevel.HIGH and risk["git_push"] is RiskLevel.HIGH
    for name in EXPECTED["browser"] | EXPECTED["computer"]:
        assert risk[name] is RiskLevel.HIGH, name


@pytest.mark.parametrize("name,args", [
    ("open_page", {"url": "https://example.com"}),
    ("extract_information", {"query": "the title"}),
    ("move_cursor", {"x": 10, "y": 20}),
    ("click", {}),
    ("type_text", {"text": "hello"}),
])
async def test_stubs_report_not_available(tmp_path, name, args):
    ctx = ToolContext(workspace=str(tmp_path), policy=PermissionPolicy(max_auto_risk=RiskLevel.HIGH))
    res = await default_registry().execute(ToolCall(name, args), ctx)
    assert not res.ok and res.error == "not available in Phase 1" and res.data["phase"] == 1


async def test_stubs_need_approval_under_default_policy(tmp_path):
    ctx = ToolContext(workspace=str(tmp_path), approver=auto_deny)   # default max MEDIUM
    res = await default_registry().execute(ToolCall("click", {}), ctx)
    assert not res.ok and res.error.startswith("permission denied")
