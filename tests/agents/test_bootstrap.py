"""agents.bootstrap: one call must produce a working agent workforce.

This is the M6 gate - if these pass, the whole Phase 1 framework is wired
together correctly and the app can adopt it with a single import.
"""
from __future__ import annotations

import os

from agents.bootstrap import build_agent_manager, data_dir, default_workspace_root, describe_plan
from agents.core import RiskLevel, TaskStatus

REQUEST = "Create a new folder called MyApp and build the foundation of a React application inside it."


def test_build_agent_manager_wires_every_package(tmp_path):
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True)
    try:
        assert mgr.tools.names(), "no tools registered"
        for expected in ("create_folder", "create_file", "read_file", "run_command", "git_init"):
            assert expected in mgr.tools.names(), expected
        assert mgr.router.providers(), "no provider registered"
        assert mgr.workers.get("coding") is not None
        assert mgr.store is not None and mgr.memory is not None
        assert mgr.classifier is not None
        assert mgr.workspace_root == os.path.abspath(str(tmp_path))
    finally:
        mgr.stop()


def test_cold_start_builds_a_react_foundation(tmp_path):
    """The headline scenario, from one function call and no network."""
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True).start()
    try:
        task = mgr.submit(REQUEST)
        assert task is not None, "classifier did not recognise an autonomous task"
        task = mgr.wait(task.id, 60)
        assert task.status is TaskStatus.COMPLETED, task.error

        app = tmp_path / "MyApp"
        for f in ("package.json", "index.html", "vite.config.js", ".gitignore", "README.md"):
            assert (app / f).is_file(), f
        assert (app / "src" / "App.jsx").is_file()
        assert (app / "src" / "main.jsx").is_file()
        assert not (app / "node_modules").exists(), "npm install must be off by default"
    finally:
        mgr.stop()


def test_describe_plan_lists_the_steps(tmp_path):
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True).start()
    try:
        task = mgr.wait(mgr.submit(REQUEST).id, 60)
        text = describe_plan(task)
        assert "Understood" in text and "I will:" in text
        assert "1. Create project folder" in text
    finally:
        mgr.stop()


def test_folder_name_case_is_preserved(tmp_path):
    """A folder the user calls 'Notes' is created as Notes, not notes."""
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True).start()
    try:
        task = mgr.wait(mgr.submit("Create a folder called Notes").id, 30)
        assert task.status is TaskStatus.COMPLETED, task.error
        assert "Notes" in os.listdir(tmp_path)
    finally:
        mgr.stop()


def test_chat_is_never_submitted_as_a_task(tmp_path):
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True).start()
    try:
        for chat in ("What is a React component?", "is the project set up correctly?",
                     "how do I create a folder?", "explain the folder structure"):
            assert mgr.submit(chat) is None, chat
        assert mgr.list_tasks() == []
    finally:
        mgr.stop()


def test_policy_is_honoured(tmp_path):
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True,
                              max_auto_risk=RiskLevel.SAFE)
    try:
        assert mgr.default_policy.max_auto_risk is RiskLevel.SAFE
    finally:
        mgr.stop()


def test_paths_are_created_outside_the_app_folder():
    """Agents must never default to building inside Tora's own source tree."""
    ws = default_workspace_root()
    assert os.path.isdir(ws)
    assert "clicky-windows" not in ws.lower()
    assert os.path.isdir(data_dir())
