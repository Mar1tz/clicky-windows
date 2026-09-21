"""Wire the agent system together for the running app.

One call, one AgentManager, all defaults:

    from agents.bootstrap import build_agent_manager
    mgr = build_agent_manager().start()

Phase 1 runs on the template planner, rule verifier and a MockProvider
fallback, so every autonomous task the app accepts is deterministic and
free. Phase 2 swaps the router's providers for real ones without touching
this function's callers.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from agents.core import PermissionPolicy, RiskLevel
from agents.manager import AgentManager

log = logging.getLogger("agents.bootstrap")

APP_DIR_NAME = "Tora"


def data_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def default_workspace_root() -> str:
    """Where agents build things unless told otherwise. Kept out of the
    app's own folder so a runaway task can never touch Tora's code."""
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    path = os.path.join(docs if os.path.isdir(docs) else os.path.expanduser("~"), "Tora Projects")
    os.makedirs(path, exist_ok=True)
    return path


def build_agent_manager(
    *,
    workspace_root: Optional[str] = None,
    db_path: Optional[str] = None,
    in_memory: bool = False,
    max_auto_risk: RiskLevel = RiskLevel.MEDIUM,
    require_plan_approval: bool = False,
) -> AgentManager:
    from agents.memory import SQLiteMemoryStore
    from agents.persistence import SQLiteTaskStore
    from agents.planning import TemplatePlanner
    from agents.providers import MockProvider, build_router
    from agents.tools import default_registry
    from agents.understanding import HeuristicIntentClassifier
    from agents.verification import RuleVerifier
    from agents.workers import default_workers

    if in_memory:
        task_db = mem_db = ":memory:"
    else:
        task_db = db_path or os.path.join(data_dir(), "agents.db")
        mem_db = os.path.join(os.path.dirname(task_db), "agent_memory.db")

    return AgentManager(
        tools=default_registry(),
        router=build_router(fallback=MockProvider()),
        workers=default_workers(),
        planner=TemplatePlanner(),
        verifier=RuleVerifier(),
        workspace_root=workspace_root or default_workspace_root(),
        store=SQLiteTaskStore(task_db),
        memory=SQLiteMemoryStore(mem_db),
        classifier=HeuristicIntentClassifier(),
        default_policy=PermissionPolicy(max_auto_risk=max_auto_risk),
        require_plan_approval=require_plan_approval,
    )


def describe_plan(task) -> str:
    """The 'Understood. I will: 1. … Starting now.' acknowledgement."""
    reply = task.metadata.get("reply") or f"Understood. Starting: {task.title}."
    if task.steps:
        lines = [f"{i + 1}. {s.title}" for i, s in enumerate(task.steps[:8])]
        more = f" (+{len(task.steps) - 8} more)" if len(task.steps) > 8 else ""
        return f"{reply}\nI will:\n" + "\n".join(lines) + more
    return reply
