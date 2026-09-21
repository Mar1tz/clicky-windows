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

from agents.core import PermissionPolicy, RiskLevel, TaskKind
from agents.manager import AgentManager

log = logging.getLogger("agents.bootstrap")

APP_DIR_NAME = "Tora"

# The settings page stores a name; the policy needs the enum.
RISK_BY_NAME = {r.value: r for r in RiskLevel}


def data_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def default_workspace_root() -> str:
    """Where agents build things unless told otherwise.

    The user's setting wins; otherwise ~/Documents/Tora Projects. Kept out
    of the app's own folder either way, so a runaway task can never touch
    Tora's code.
    """
    chosen = ""
    try:
        from config import cfg
        chosen = (cfg.agent_workspace or "").strip()
    except Exception:       # config is optional for library use
        pass
    if chosen:
        path = os.path.abspath(os.path.expanduser(chosen))
    else:
        docs = os.path.join(os.path.expanduser("~"), "Documents")
        base = docs if os.path.isdir(docs) else os.path.expanduser("~")
        path = os.path.join(base, "Tora Projects")
    os.makedirs(path, exist_ok=True)
    return path


def _configured(name: str, fallback):
    """One agent setting from config.py, tolerating config being absent."""
    try:
        from config import cfg
        return getattr(cfg, name, fallback)
    except Exception:
        return fallback


def has_live_provider(router) -> bool:
    """True when something other than the mock is registered.

    The whole system runs on the deterministic planner/verifier without a
    provider, so this decides between "free and predictable" and "can
    think about goals nobody wrote a template for".
    """
    from agents.providers import MockProvider
    try:
        return any(not isinstance(p, MockProvider) for p in router.providers())
    except Exception:  # noqa: BLE001 - a stub router in tests
        return False


def build_agent_manager(
    *,
    workspace_root: Optional[str] = None,
    db_path: Optional[str] = None,
    in_memory: bool = False,
    max_auto_risk: Optional[RiskLevel] = None,
    require_plan_approval: Optional[bool] = None,
    max_concurrent: Optional[int] = None,
    offline: bool = False,
) -> AgentManager:
    """Build the workforce. Anything left as None comes from the user's
    settings in config.py, so the settings page is not decorative.

    `offline=True` forces the MockProvider and refuses the network — for
    tests, and for proving the whole runtime works with no credentials.
    """
    from agents.memory import SQLiteMemoryStore
    from agents.persistence import SQLiteTaskStore
    from agents.planning import ProviderPlanner, TemplatePlanner
    from agents.providers import MockProvider, build_router
    from agents.tools import default_registry
    from agents.understanding import HeuristicIntentClassifier, TieredIntentClassifier
    from agents.verification import CompositeVerifier, RuleVerifier
    from agents.workers import default_workers

    if in_memory:
        task_db = mem_db = ":memory:"
    else:
        task_db = db_path or os.path.join(data_dir(), "agents.db")
        mem_db = os.path.join(os.path.dirname(task_db), "agent_memory.db")

    if max_auto_risk is None:
        max_auto_risk = RISK_BY_NAME.get(
            str(_configured("agent_auto_risk", "medium")).lower(), RiskLevel.MEDIUM)
    if require_plan_approval is None:
        require_plan_approval = bool(_configured("agent_plan_approval", False))
    if max_concurrent is None:
        max_concurrent = int(_configured("agent_max_concurrent", 3))

    # Let build_router decide: it registers the configured gateway above
    # the mock catch-all. Passing a fallback here used to suppress the
    # gateway entirely, so the app ran on the mock while looking wired up.
    router = build_router(fallback=MockProvider() if offline else None,
                          gateway=False if offline else None)

    # With a real provider connected, the model plans and judges; without
    # one, the deterministic pair still runs the whole system end to end.
    # ProviderPlanner keeps the templates as its fallback AND prefers them
    # when one matches, so "a React app" stays instant and free while
    # "build me a CRM" gets thought about.
    live = has_live_provider(router)
    planner = ProviderPlanner(fallback=TemplatePlanner(), prefer_templates=True) if live \
        else TemplatePlanner()
    verifier = CompositeVerifier(rules=RuleVerifier()) if live else RuleVerifier()
    # Tiered, not provider-only: the classifier runs on EVERY voice turn,
    # and a provider call costs seconds. Rules answer the clear cases
    # instantly; the model is consulted only where they are unsure.
    classifier = TieredIntentClassifier(router.pick(TaskKind.FAST)) if live \
        else HeuristicIntentClassifier()

    return AgentManager(
        tools=default_registry(),
        router=router,
        workers=default_workers(),
        planner=planner,
        verifier=verifier,
        workspace_root=workspace_root or default_workspace_root(),
        store=SQLiteTaskStore(task_db),
        memory=SQLiteMemoryStore(mem_db),
        classifier=classifier,
        default_policy=PermissionPolicy(max_auto_risk=max_auto_risk),
        require_plan_approval=require_plan_approval,
        max_concurrent=max_concurrent,
    )


def describe_plan(task) -> str:
    """The 'Understood. I will: 1. … Starting now.' acknowledgement."""
    reply = task.metadata.get("reply") or f"Understood. Starting: {task.title}."
    if task.steps:
        lines = [f"{i + 1}. {s.title}" for i, s in enumerate(task.steps[:8])]
        more = f" (+{len(task.steps) - 8} more)" if len(task.steps) > 8 else ""
        return f"{reply}\nI will:\n" + "\n".join(lines) + more
    return reply
