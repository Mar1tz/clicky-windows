"""What the jobs of 27 Oct got wrong, kept fixed.

* "tidy my Downloads folder": the file tools were fenced to the job's own
  folder, so every look at Downloads was "outside workspace" and the job
  flailed until it ran out of steps. A folder the goal names is where the
  job may work.
* "list my latest GitHub repository": it found the answer, but two wrong
  guesses (404s) through GitHub's read-only tools failed the step. A failed
  read is how an agent learns; and a job with GitHub's tools is told to ask
  GitHub whose account it is (get_me) instead of guessing.
"""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

from agents.core import (
    AgentContext, AgentSpec, EventBus, RunControl, Step, StepStatus, Task, ToolCall, ToolContext, ToolResult,
)
from agents.manager import agent_manager
from agents.tools import Registry, default_registry, mcp_tools
from agents.verification import RuleVerifier


def test_a_folder_the_goal_names_is_where_the_job_works(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True)
    spaced = tmp_path / "Tora Box"
    spaced.mkdir()
    monkeypatch.setattr(agent_manager.os.path, "expanduser", lambda p: str(home) if p == "~" else p)
    assert agent_manager.folders_named("tidy my downloads folder") == [str(home / "Downloads")]
    assert agent_manager.folders_named(f"sort {spaced / 'x.txt'} please") == [str(spaced)]
    assert agent_manager.folders_named(f"move {home / 'Downloads' / 'a.pdf'}") == [str(home / "Downloads")], \
        "a folder's name inside a path is not a second grant"
    assert agent_manager.folders_named("make me a website") == []


def test_a_tidy_job_can_read_its_downloads(tmp_path, monkeypatch):
    from agents.bootstrap import build_agent_manager
    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True)
    (home / "Downloads" / "setup.exe").write_bytes(b"x")
    monkeypatch.setattr(agent_manager.os.path, "expanduser", lambda p: str(home) if p == "~" else p)
    mgr = build_agent_manager(workspace_root=str(tmp_path / "Tora Projects"), in_memory=True, offline=True)
    task = Task(title="Tidy", goal="tidy my downloads folder", workspace=str(tmp_path / "Tora Projects"),
                worker_type="file")
    spec = mgr._default_spec(task)
    os.makedirs(task.workspace, exist_ok=True)
    ctx = ToolContext(workspace=task.workspace, policy=spec.permissions)
    out = asyncio.run(default_registry().execute(ToolCall("list_dir", {"path": str(home / "Downloads")}), ctx))
    assert out.ok and "setup.exe" in out.output
    elsewhere = asyncio.run(default_registry().execute(ToolCall("list_dir", {"path": str(home)}), ctx))
    assert not elsewhere.ok and "outside workspace" in elsewhere.error, "only the folder it names"
    lines = mgr._extras_for(task, mgr.workers.get("file"), spec)["context_lines"]
    assert any("Folders this job may work in" in l and "Downloads" in l for l in lines)


def _github(reg):
    server = SimpleNamespace(cfg=SimpleNamespace(name="github"), running=True, tools=[
        {"name": "list_branches", "description": "List branches", "annotations": {"readOnlyHint": True}},
        {"name": "get_me", "description": "Who am I", "annotations": {"readOnlyHint": True}}])
    return mcp_tools.attach_server(reg, server)


def test_a_wrong_guess_through_a_read_only_service_tool_does_not_fail_the_step(tmp_path):
    reg = Registry()
    _github(reg)
    bad, good = ToolCall("mcp_github_list_branches", {"repo": "Mar1tz/Mar1tz"}), ToolCall("mcp_github_get_me", {})
    step = Step("Find the latest repo", status=StepStatus.DONE, tool_calls=[bad, good], results=[
        ToolResult(call_id=bad.id, tool_name=bad.tool_name, ok=False, error="404 Not Found"),
        ToolResult(call_id=good.id, tool_name=good.tool_name, ok=True, output="Mar1tz")])
    ctx = AgentContext(task=Task(title="t", goal="g", workspace=str(tmp_path), steps=[step]),
                       agent=AgentSpec(name="A", goal="g"), tools=reg, provider=None, events=EventBus(),
                       control=RunControl())
    assert asyncio.run(RuleVerifier().verify_step(step, ctx)).ok


def test_a_job_with_githubs_tools_is_told_to_ask_whose_account_it_is():
    reg = Registry()
    names = _github(reg)
    [hint] = mcp_tools.hints_for(names, reg)
    assert "get_me" in hint and "Never guess a repository name" in hint
    assert mcp_tools.hints_for(["read_file"], reg) == []


def test_after_sleep_the_old_microphone_stream_is_let_go_untouched():
    """28 Oct: stopping the stream whose device went away in the sleep
    crashed HeyTora (an access violation inside PortAudio, past any except)."""
    from audio import ambient_listener as al
    touched = []
    stream = SimpleNamespace(stop=lambda: touched.append("stop"), close=lambda: touched.append("close"))
    listener = al.AmbientListener.__new__(al.AmbientListener)
    listener._stream, listener._running = stream, True
    listener.stop(abandon=True)
    assert touched == [] and listener._stream is None and stream in al._ABANDONED
    al._ABANDONED.remove(stream)
