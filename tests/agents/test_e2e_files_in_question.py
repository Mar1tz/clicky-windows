"""The files the user means go to the agent as paths (ai/screen_files.py).

11 Oct 2026: the owner traced over a Word document in File Explorer and said
"turn this Word document into a PDF". The voice first started a walkthrough
(show_me_how, 77 s to plan) and then handed the agent "convert the selected
Word document" with its own workspace: it searched Tora Projects and failed
twice. The file was C:\\Users\\torbe\\Documents\\10 Mei 2026.docx. Now the
file traced over (or selected in Explorer, or open in Office) goes with the
job as its path, the job works in that folder, a conversion goes to the
agent that can run PowerShell, and a walkthrough is only for "how do I".
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai import screen_files, trace as tracing, trace_read


@pytest.fixture
def docs(tmp_path):
    folder = tmp_path / "Documents"
    folder.mkdir()
    for name in ("10 Mei 2026.docx", "Report.docx", "Report.pdf", "Budget.xlsx"):
        (folder / name).write_bytes(b"x")
    return folder


# -- a name on the screen to the file it is ---------------------------------------------------------------------

def test_a_name_with_its_extension_hidden_is_its_file(docs):
    assert screen_files.match(["10 Mei 2026"], [str(docs)]) == [str(docs / "10 Mei 2026.docx")]


def test_an_exact_name_is_that_file(docs):
    assert screen_files.match(["Report.pdf"], [str(docs)]) == [str(docs / "Report.pdf")]


def test_a_name_that_could_be_two_files_is_neither(docs):
    assert screen_files.match(["Report"], [str(docs)]) == [], "never a guess"
    assert screen_files.match(["Nothing here"], [str(docs)]) == []


def test_names_in_an_explorer_window_are_its_files(docs, monkeypatch):
    monkeypatch.setattr(screen_files, "explorer_window", lambda hwnd: (str(docs), []))
    assert screen_files.files_at(["10 Mei 2026", "Budget"], 1234) == [str(docs / "10 Mei 2026.docx"),
                                                                     str(docs / "Budget.xlsx")]


def test_names_on_the_desktop_are_its_files(docs, monkeypatch):
    monkeypatch.setattr(screen_files, "explorer_window", lambda hwnd: None)
    monkeypatch.setattr(screen_files, "_class_name", lambda hwnd: "WorkerW")
    monkeypatch.setattr(screen_files, "desktop_folders", lambda: [str(docs)])
    assert screen_files.files_at(["Budget"], 99) == [str(docs / "Budget.xlsx")]


def test_without_a_trace_the_selection_or_the_open_document(docs, monkeypatch):
    monkeypatch.setattr(screen_files, "explorer_window", lambda hwnd: (str(docs), [str(docs / "Budget.xlsx")]))
    assert screen_files.in_view(5) == [str(docs / "Budget.xlsx")]
    monkeypatch.setattr(screen_files, "explorer_window", lambda hwnd: None)
    monkeypatch.setattr(screen_files, "office_document", lambda hwnd: str(docs / "Report.docx"))
    assert screen_files.in_view(5) == [str(docs / "Report.docx")]


@pytest.mark.parametrize("goal, conversion", [
    ("turn this Word document into a PDF", True), ("convert it to pdf", True), ("export this as PDF", True),
    ("save it as a PDF", True), ("rename these files by date", False), ("build me a website", False),
])
def test_what_is_a_conversion(goal, conversion):
    assert screen_files.is_conversion(goal) is conversion


# -- traced over in Explorer: the voice hears the path ---------------------------------------------------------

def _fake_explorer_uia(monkeypatch):
    class Item:
        ControlTypeName, Name, IsPassword = "ListItemControl", "10 Mei 2026", False

        def GetPattern(self, pid):
            return None

        def GetParentControl(self):
            return None

        def GetTopLevelControl(self):
            return SimpleNamespace(Name="Documents - File Explorer")

    fake = SimpleNamespace(ControlFromPoint=lambda x, y: Item(), PatternId=SimpleNamespace(TextPattern=1),
                           TextUnit=SimpleNamespace(Word=2, Line=3, Paragraph=4),
                           InitializeUIAutomationInCurrentThread=lambda: None)
    monkeypatch.setitem(sys.modules, "uiautomation", fake)


def test_a_file_traced_over_is_heard_as_its_path(docs, monkeypatch):
    _fake_explorer_uia(monkeypatch)
    monkeypatch.setattr(screen_files, "root_window_at", lambda x, y: 77)
    monkeypatch.setattr(screen_files, "explorer_window", lambda hwnd: (str(docs), []) if hwnd == 77 else None)
    focus = tracing.Focus("sweep", (100, 100, 900, 220), 0.1, 1.0, [(300, 160), (500, 160)])
    (reading,) = trace_read.read([focus])
    assert reading.files == [str(docs / "10 Mei 2026.docx")]
    line = trace_read.voice_line([reading])
    assert str(docs / "10 Mei 2026.docx") in line and "delegate_task" in line


# -- the agent gets the file and works where it is ---------------------------------------------------------------

@pytest.fixture
def workforce(tmp_path):
    from agents.bootstrap import build_agent_manager
    m = build_agent_manager(workspace_root=str(tmp_path / "work"), in_memory=True, offline=True)
    m.start()
    yield m
    m.stop()


class _Voice:
    from companion_manager import CompanionManager as _CM
    _WORKER_FOR_KIND = _CM._WORKER_FOR_KIND
    _tool_delegate_task = _CM._tool_delegate_task
    _wait_for_plan = staticmethod(_CM._wait_for_plan)

    def __init__(self, manager, readings=()):
        self._agents = manager
        self._trace_readings = list(readings)


def test_the_traced_document_goes_to_the_agent_as_its_path(workforce, docs):
    path = str(docs / "10 Mei 2026.docx")
    reading = trace_read.Reading(tracing.Focus("sweep", (0, 0, 10, 10), 0, 1, []), files=[path])
    out = asyncio.run(_Voice(workforce, [reading])._tool_delegate_task(
        {"goal": "Convert the selected Word document to PDF format.", "kind": "file"}))
    assert out["ok"]
    task = workforce.get_task(out["task_id"])
    assert path in task.goal and task.metadata["files"] == [path]
    assert Path(task.workspace) == docs, "it works where the file is, not in Tora Projects"
    assert task.worker_type == "terminal", "converting needs PowerShell: the file agent cannot run anything"
    assert task.metadata.get("skill") == "convert-to-pdf"


def test_the_selection_in_explorer_goes_with_the_job(workforce, docs, monkeypatch):
    path = str(docs / "Budget.xlsx")
    monkeypatch.setattr(screen_files, "in_view", lambda hwnd=None: [path])
    out = asyncio.run(_Voice(workforce)._tool_delegate_task({"goal": "turn this spreadsheet into a PDF"}))
    task = workforce.get_task(out["task_id"])
    assert path in task.goal and Path(task.workspace) == docs


def test_a_job_about_no_file_is_as_before(workforce, monkeypatch):
    monkeypatch.setattr(screen_files, "in_view", lambda hwnd=None: pytest.fail("not asked"))
    out = asyncio.run(_Voice(workforce)._tool_delegate_task({"goal": "build me a website for my coffee shop"}))
    task = workforce.get_task(out["task_id"])
    assert "files" not in task.metadata and Path(task.workspace) == Path(workforce.workspace_root)


def test_the_starter_skill_knows_how_to_convert():
    from agents.skills.library import SkillLibrary
    skill = SkillLibrary().get("convert-to-pdf")
    assert skill is not None and "SaveAs2" in skill.body and "ExportAsFixedFormat" in skill.body
    assert "never guess" in skill.body.lower()


# -- doing it is the agent's; a walkthrough is only for "how do I" ---------------------------------------------

def test_the_voice_does_it_rather_than_teaching_it():
    import config
    from ai import gemini_realtime
    rules = " ".join(config._REALTIME_RULES.split())
    assert "DOING SOMETHING TO A FILE is a job for an agent" in rules
    assert "Never show_me_how for these" in rules
    walk = next(d for d in gemini_realtime.tool_declarations()[0]["functionDeclarations"]
                if d["name"] == "show_me_how")
    assert "Never when they ask you to DO it" in walk["description"]
