"""M7: the companion can hand work to the agent workforce.

Constructing a real CompanionManager would start a microphone, a hotkey
hook and a websocket, so these tests bind the delegation methods to a
stand-in object holding only what they touch (`_agents`). That is the
whole surface of the integration - if these pass, the wiring in
companion_manager.py is sound.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from agents.bootstrap import build_agent_manager
from agents.core import TaskStatus

REQUEST = "Create a new folder called MyApp and build the foundation of a React application inside it."


class _Stub:
    """Just enough companion for the delegation methods to run."""
    from companion_manager import CompanionManager as _CM

    _WORKER_FOR_KIND = _CM._WORKER_FOR_KIND
    _tool_delegate_task = _CM._tool_delegate_task
    _delegate_if_task = _CM._delegate_if_task
    # staticmethod, and it must stay one here too: pulling it off the class
    # yields a plain function, which would bind `self` as the first argument.
    _wait_for_plan = staticmethod(_CM._wait_for_plan)

    def __init__(self, manager):
        self._agents = manager


@pytest.fixture
def companion(tmp_path):
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True).start()
    try:
        yield _Stub(mgr), mgr, tmp_path
    finally:
        mgr.stop()


# -- wiring ------------------------------------------------------------------

def test_delegate_task_is_registered_as_a_live_tool():
    from ai.gemini_realtime import tool_declarations
    decls = {d["name"]: d for d in tool_declarations()[0]["functionDeclarations"]}
    assert "delegate_task" in decls
    params = decls["delegate_task"]["parameters"]
    assert params["required"] == ["goal"]
    assert set(params["properties"]) >= {"goal", "title", "workspace_hint", "kind"}


def test_prompt_tells_the_model_when_to_delegate():
    import config
    text = config.DEFAULT_SYSTEM_PROMPT + config._TECHNICAL_RULES
    assert "DELEGATION" in text and "delegate_task" in text


def test_companion_exposes_the_handler_and_the_signal():
    import inspect

    from companion_manager import CompanionManager
    handlers = inspect.getsource(CompanionManager._tool_handlers)
    assert '"delegate_task"' in handlers, "handler not registered in _tool_handlers"
    assert hasattr(CompanionManager, "sig_agent_event")
    assert isinstance(CompanionManager.agents, property)
    # the cascade route must run before the screenshot, not after
    turn = inspect.getsource(CompanionManager._cascade_turn)
    assert "_delegate_if_task" in turn
    assert turn.index("_delegate_if_task") < turn.index("# 2. Screen capture")


# -- the live-model route ----------------------------------------------------

def test_delegate_task_starts_an_agent_and_returns_the_plan(companion):
    stub, mgr, tmp_path = companion
    out = asyncio.run(stub._tool_delegate_task({
        "goal": REQUEST, "title": "Create React app MyApp", "workspace_hint": "MyApp",
    }))
    assert out["ok"] is True
    assert out["agent"] == "Application Builder"
    assert out["steps"], "the model must get real steps to read back"
    assert out["steps"][0] == "Create project folder"

    task = mgr.wait(out["task_id"], 60)
    assert task.status is TaskStatus.COMPLETED, task.error
    assert (tmp_path / "MyApp" / "package.json").is_file()


def test_delegate_task_strips_quotes_from_the_folder_hint(companion):
    stub, mgr, tmp_path = companion
    out = asyncio.run(stub._tool_delegate_task({"goal": "make a folder", "workspace_hint": "'Notes'"}))
    task = mgr.wait(out["task_id"], 30)
    assert task.status is TaskStatus.COMPLETED, task.error
    assert "Notes" in os.listdir(tmp_path)


def test_delegate_task_rejects_an_empty_goal(companion):
    stub, _mgr, _tmp = companion
    assert "error" in asyncio.run(stub._tool_delegate_task({"goal": "   "}))


def test_delegate_task_falls_back_on_an_unknown_kind(companion):
    stub, mgr, _tmp = companion
    out = asyncio.run(stub._tool_delegate_task({"goal": "build a react app", "kind": "nonsense"}))
    assert out["ok"] is True
    assert mgr.get_task(out["task_id"]).worker_type == "coding"


def test_delegation_reports_cleanly_when_the_workforce_is_off():
    stub = _Stub(None)
    out = asyncio.run(stub._tool_delegate_task({"goal": REQUEST}))
    assert "error" in out and "not available" in out["error"]


# -- the cascade (voice transcript) route ------------------------------------

def test_cascade_route_delegates_a_build_request(companion):
    stub, mgr, tmp_path = companion
    task = asyncio.run(stub._delegate_if_task(REQUEST))
    assert task is not None
    assert task.metadata["subdir"] == "MyApp"
    assert task.steps, "the plan should be ready for the spoken acknowledgement"

    from agents.bootstrap import describe_plan
    spoken = describe_plan(task)
    assert "Understood" in spoken and "1. Create project folder" in spoken

    task = mgr.wait(task.id, 60)
    assert task.status is TaskStatus.COMPLETED, task.error
    assert (tmp_path / "MyApp" / "src" / "App.jsx").is_file()


@pytest.mark.parametrize("chat", [
    "what is a React component?",
    "is the project set up correctly?",
    "how do I create a folder?",
    "explain the folder structure",
    "where is the save button",
])
def test_cascade_route_leaves_chat_alone(companion, chat):
    stub, mgr, _tmp = companion
    assert asyncio.run(stub._delegate_if_task(chat)) is None, chat
    assert mgr.list_tasks() == []


def test_cascade_route_is_a_no_op_without_the_workforce():
    assert asyncio.run(_Stub(None)._delegate_if_task(REQUEST)) is None


def test_cascade_route_survives_a_broken_manager():
    """A failure inside the agent system must never break a voice turn."""
    class Exploding:
        workspace_root = "."
        def submit(self, _text):
            raise RuntimeError("agent system is on fire")

    assert asyncio.run(_Stub(Exploding())._delegate_if_task(REQUEST)) is None
