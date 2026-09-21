"""Phase 1 acceptance: the React-foundation request runs end to end with
the REAL tools / workers / planner / verifier / store and a MockProvider.
No network, no Node. Skips while a worker package is still missing.

See docs/agents/ARCHITECTURE.md §7.
"""
from __future__ import annotations

import pytest

tools_pkg = pytest.importorskip("agents.tools")
providers_pkg = pytest.importorskip("agents.providers")
workers_pkg = pytest.importorskip("agents.workers")
understanding_pkg = pytest.importorskip("agents.understanding")
planning_pkg = pytest.importorskip("agents.planning")
verification_pkg = pytest.importorskip("agents.verification")
persistence_pkg = pytest.importorskip("agents.persistence")
memory_pkg = pytest.importorskip("agents.memory")

from agents.core import EventKind, TaskStatus  # noqa: E402
from agents.manager import AgentManager  # noqa: E402

REQUEST = "Create a new folder called MyApp and build the foundation of a React application inside it."


def _build(tmp_path) -> AgentManager:
    from agents.memory import AgentMemory, SQLiteMemoryStore
    from agents.persistence import SQLiteTaskStore
    from agents.planning import TemplatePlanner
    from agents.providers import MockProvider, build_router
    from agents.tools import default_registry
    from agents.understanding import HeuristicIntentClassifier
    from agents.verification import RuleVerifier
    from agents.workers import default_workers

    store = SQLiteTaskStore(":memory:")
    memory_store = SQLiteMemoryStore(":memory:")
    mgr = AgentManager(
        tools=default_registry(),
        router=build_router(fallback=MockProvider()),
        workers=default_workers(),
        planner=TemplatePlanner(),
        verifier=RuleVerifier(),
        workspace_root=str(tmp_path),
        store=store,
        memory=memory_store,
        classifier=HeuristicIntentClassifier(),
    )
    mgr._memory_facade = AgentMemory(memory_store)  # kept alive for the assertion below
    return mgr.start()


def test_react_foundation_end_to_end(tmp_path):
    mgr = _build(tmp_path)
    try:
        kinds: list[EventKind] = []
        mgr.events.subscribe(lambda e: kinds.append(e.kind))

        task = mgr.submit(REQUEST)
        assert task is not None, "classifier did not recognise an autonomous task"
        assert task.metadata.get("subdir") == "MyApp"
        assert task.metadata.get("reply"), "user-facing acknowledgement missing"

        task = mgr.wait(task.id, 60)
        assert task.status is TaskStatus.COMPLETED, f"{task.error}\n{[ (s.title, s.status.value, s.error) for s in task.steps ]}"

        app = tmp_path / "MyApp"
        assert (app / "package.json").is_file()
        assert (app / "src" / "App.jsx").is_file()
        assert (app / "src" / "main.jsx").is_file()
        assert (app / "index.html").is_file()
        assert not (app / "node_modules").exists(), "npm install must be skipped by default"

        for k in (EventKind.TASK_CREATED, EventKind.PLAN_CREATED, EventKind.STEP_STARTED,
                  EventKind.STEP_DONE, EventKind.TOOL_CALL, EventKind.TOOL_RESULT,
                  EventKind.VERIFICATION, EventKind.TASK_PROGRESS, EventKind.AGENT_STATUS):
            assert k in kinds, f"missing event {k}"

        # durable history
        stored = mgr.store.get_task(task.id)
        assert stored is not None and stored.status is TaskStatus.COMPLETED
        evs = mgr.store.events(task.id)
        assert any(e.kind is EventKind.PLAN_CREATED for e in evs)
        assert any(e.kind is EventKind.STEP_DONE for e in evs)

        # memory learned about the project
        summary = mgr.memory.project_summary(str(tmp_path))
        assert "MyApp" in summary or "completed" in summary.lower()

        # the agent card the UI would render
        agents = mgr.list_agents()
        assert agents and agents[0].spec.name == "Application Builder"
        assert agents[0].progress == 1.0
    finally:
        mgr.stop()


def test_chat_question_is_not_a_task(tmp_path):
    mgr = _build(tmp_path)
    try:
        assert mgr.submit("What is a React component?") is None
    finally:
        mgr.stop()
