"""Every agent tool, as Gemini receives it, is one Gemini accepts (5 Oct 2026).

Gemini refuses the WHOLE request over one bad tool schema, so one tool
failed every step of a user's File Agent task ("Create Three Monkeys Ball
poster": write_workbook's rows were an array of arrays with no item type).
Sending all 262 tools (the packs' too, then) to Gemini's countTokens found
three more (install_package and git_add: a type that was a list;
focus_window: a parameter named "title", which the scrubber deleted as if it
were the schema keyword). Gemini then accepted all 262. These rules are
checked here offline for every tool, so a new tool cannot bring it back.
"""
from __future__ import annotations

import tempfile

import pytest


def _problems(schema, where="parameters") -> list[str]:
    out: list[str] = []
    if not isinstance(schema, dict):
        return out
    kind = schema.get("type")
    if isinstance(kind, list):
        out.append(f"{where}: type is a list {kind}")
    if kind == "array":
        items = schema.get("items")
        if not isinstance(items, dict) or not (items.get("type") or items.get("anyOf")):
            out.append(f"{where}: an array that does not say what it holds")
    props = schema.get("properties")
    if isinstance(props, dict):
        for name, sub in props.items():
            out += _problems(sub, f"{where}.{name}")
        for req in schema.get("required") or []:
            if req not in props:
                out.append(f"{where}: requires {req!r}, which it does not define")
    if isinstance(schema.get("items"), dict):
        out += _problems(schema["items"], f"{where}[]")
    for key in ("anyOf", "oneOf", "allOf"):
        for i, sub in enumerate(schema.get(key) or []):
            out += _problems(sub, f"{where}.{key}[{i}]")
    return out


@pytest.fixture(scope="module")
def every_tool():
    from agents.bootstrap import build_agent_manager
    mgr = build_agent_manager(workspace_root=tempfile.mkdtemp(), in_memory=True, offline=True)
    return mgr.tools.specs()


def test_every_tool_is_one_gemini_takes(every_tool):
    from agents.providers.gemini import GeminiAgentProvider
    assert len(every_tool) > 40, "every tool is in"
    bad = {s.name: p for s in every_tool
           if (p := _problems(GeminiAgentProvider.encode_tool(s)["parameters"]))}
    assert not bad, bad


@pytest.mark.parametrize("schema, check", [
    ({"type": "object", "properties": {"rows": {"type": "array", "items": {"type": "array"}}}},
     lambda s: s["properties"]["rows"]["items"]["items"] == {"type": "string"}),
    ({"type": "object", "properties": {"paths": {"type": ["array", "string"], "items": {"type": "string"}}}},
     lambda s: s["properties"]["paths"]["type"] == "array"),
    ({"type": "object", "properties": {"title": {"type": "string", "title": "Title"}}, "required": ["title"]},
     lambda s: s["properties"] == {"title": {"type": "string"}} and s["required"] == ["title"]),
    ({"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a", "gone"]},
     lambda s: s["required"] == ["a"]),
    ({"type": ["string", "null"]}, lambda s: s["type"] == "string"),
])
def test_the_scrubber(schema, check):
    from agents.providers.gemini import GeminiAgentProvider
    assert check(GeminiAgentProvider.scrub_schema(schema))


def test_a_number_that_came_as_text_is_written_as_a_number():
    from agents.tools.spreadsheet_tools import _number
    assert _number("1200") == 1200 and _number("-3.5") == -3.5
    for text in ("007", "1,200", "12abc", "=B7*2", ""):
        assert _number(text) == text, text
    assert _number(5) == 5


def test_every_kind_of_agent_work_prefers_claude_on_the_gateway():
    from agents.providers import GATEWAY_MODELS
    for kind, chain in GATEWAY_MODELS.items():
        assert chain and all("claude" in m for m in chain), (kind, chain)
