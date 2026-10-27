"""OR-02: a request, typed - outcome, entities, references, constraints,
unknowns, risk class and wanted output - by rules, no model call.
"""
from __future__ import annotations

import pytest

from agents.core import Task, TaskKind
from agents.manager.agent_manager import AgentManager
from agents.understanding.typed import TypedIntent, type_request


def test_an_email_request_is_typed_whole():
    x = type_request("Email Lee the March invoice.pdf by Friday, don't cc anyone")
    assert (x.risk, x.output) == ("send", "message")
    assert x.entities == {"files": ["invoice.pdf"], "people": ["Lee"], "when": ["Friday"]}
    assert x.constraints == ["by Friday", "don't cc anyone"]
    assert x.unknowns == ["Lee's email address"] and x.consequential


@pytest.mark.parametrize("text,risk", [
    ("check my email and summarize it", "read"),
    ("add text to the image and save it as poster.png", "change"),
    ("delete the old logs in Downloads", "delete"),
    ("buy the cheapest HDMI cable under $20", "pay"),
    ("take over and type a shopping list in Notepad", "control"),
    ("what is on my calendar tomorrow", "read"),
    ("send this to sam@example.com", "send"),
])
def test_risk_classes(text, risk):
    assert type_request(text).risk == risk


def test_what_cannot_be_known_is_asked_only_where_it_matters():
    assert type_request("send this to sam@example.com").unknowns == ["what 'this' is"]
    assert type_request("send this to sam@example.com", resolved="report.pdf").unknowns == []
    assert type_request("buy a new kettle").unknowns == ["how much they are willing to pay"]
    assert type_request("tidy this folder").unknowns == [], "a change with 'this' is resolved elsewhere"
    assert type_request("email the team the notes").unknowns == ["who it goes to"]


def test_paths_quoted_names_and_links_are_entities():
    x = type_request('open "Q3 budget.xlsx" and C:\\Users\\me\\notes.txt, then go to github.com/foo')
    assert x.entities["files"] == ["Q3 budget.xlsx", "C:\\Users\\me\\notes.txt"]
    assert x.entities["urls"] == ["github.com/foo"]


def test_the_planner_is_told_the_constraints_as_rules():
    typed = type_request("Write a report.md on prices, only from official sites")
    task = Task(title="t", goal="g", workspace=".", kind=TaskKind.RESEARCH, metadata={"intent": typed.to_dict()})
    lines = AgentManager._context_lines_for(task)
    assert any("outcome" in l and "risk - change" in l for l in lines)
    assert any("each one is a rule" in l and "only from official sites" in l for l in lines)
    assert TypedIntent(**typed.to_dict()) == typed
