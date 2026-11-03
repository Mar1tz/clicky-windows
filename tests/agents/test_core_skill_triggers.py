"""Skill triggers (after OpenHands' microagents): a skill's know-how joins a
job the moment one of its words comes up - in the goal, or in something the
job reads - once, as know-how, never as a new job.
"""
from __future__ import annotations

import asyncio

from agents.core import (
    AgentContext, AgentSpec, EventBus, ProviderCapabilities, ProviderResponse, RunControl, Step, Task, ToolCall,
)
from agents.skills.library import SkillLibrary, parse
from agents.tools import default_registry
from agents.workers.file import FileWorker

DELPHI = """---
name: Build StyleHub
when: building the StyleHub PAT installer
triggers: delphi, dproj
---

Build with msbuild /p:Config=Release; never commit the .dcu files.
"""


def test_triggers_are_read_written_and_matched_as_whole_words():
    s = parse(DELPHI, "stylehub")
    assert s.triggers == ["delphi", "dproj"]
    assert parse(s.to_text(), "stylehub").triggers == ["delphi", "dproj"]
    assert s.triggered_by("fix the Delphi build") == "delphi"
    assert s.triggered_by("open StyleHub.dproj") == "dproj"
    assert s.triggered_by("delphinium pictures") == ""
    assert "Know-how from the user's skill 'Build StyleHub' ('delphi' came up)" in s.know_how("delphi")


def _library(tmp_path):
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "stylehub.md").write_text(DELPHI, encoding="utf-8")
    return SkillLibrary(starter=tmp_path / "skills", user=tmp_path / "mine")


def test_a_goal_that_says_the_word_brings_the_know_how(tmp_path):
    from agents.bootstrap import build_agent_manager
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    mgr.skills = _library(tmp_path)
    worker = mgr.workers.get("coding")
    task = Task(title="t", goal="fix the delphi build errors", workspace=str(tmp_path))
    extras = mgr._extras_for(task, worker, mgr._default_spec(task))
    assert any("Build StyleHub" in s and "msbuild" in s for s in extras["skills"])
    other = Task(title="t", goal="tidy my downloads", workspace=str(tmp_path))
    assert not mgr._extras_for(other, worker, mgr._default_spec(other))["skills"]


class _Reads:
    """Reads a file, then is done - recording what it was told each time."""
    name = "reads"

    def __init__(self):
        self.calls = 0
        self.seen = []

    async def complete(self, messages, tools=None, **kw):
        self.calls += 1
        self.seen.append([m.content for m in messages])
        if self.calls == 1:
            return ProviderResponse(text="", tool_calls=[ToolCall("read_file", {"path": "README.md"})])
        return ProviderResponse(text="done")

    def capabilities(self):
        return ProviderCapabilities(local=True, cost_tier=0)


def test_a_word_first_seen_in_what_the_job_reads_brings_it_in_once(tmp_path):
    (tmp_path / "README.md").write_text("This project is built with Delphi 12.", encoding="utf-8")
    lib = _library(tmp_path)
    provider = _Reads()
    step = Step("Look at the project")
    task = Task(title="t", goal="summarise the project", workspace=str(tmp_path), steps=[step])
    ctx = AgentContext(task=task, agent=AgentSpec(name="A", goal="g"), tools=default_registry(),
                       provider=provider, events=EventBus(), control=RunControl(),
                       extras={"triggers": [s for s in lib.enabled() if s.triggers]})
    asyncio.run(FileWorker().execute_step(step, ctx))
    assert not any("Know-how" in m for m in provider.seen[0])
    assert sum("Know-how from the user's skill 'Build StyleHub' ('delphi' came up)" in m
               for m in provider.seen[1]) == 1
