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
from agents.manager import AgentManager, ProviderDecomposer, TemplateDecomposer

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


def _switched_on(name: str) -> bool:
    """One of config's saved switches; off when config is absent."""
    try:
        from config import cfg
        return bool(cfg.switch(name))
    except Exception:
        return False


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
    auto_approve: Optional[bool] = None,
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
    from agents.providers import AccountStore, MockProvider, build_router
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
    if auto_approve is None:
        auto_approve = _switched_on("auto_approve")
    from agents.manager.limits import TaskLimits
    limits = TaskLimits(wall_clock_s=float(_configured("agent_wall_clock_s", 1800)),
                        max_tokens=int(_configured("agent_max_tokens", 300000)))

    # Let build_router decide: it registers the configured gateway above
    # the mock catch-all. Passing a fallback here used to suppress the
    # gateway entirely, so the app ran on the mock while looking wired up.
    # Accounts the user connected in Settings outrank the bare environment.
    # Skipped for in-memory and offline runs: a test must neither read nor
    # write the real profile, and the environment alone is enough there.
    accounts = None if (offline or in_memory) else AccountStore(
        os.path.join(data_dir(), "agent_accounts.json"))
    router = build_router(accounts,
                          fallback=MockProvider() if offline else None,
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
    # Multi-agent projects: the template recognises "a complete website"
    # and the like with no model call; the model decides the rest.
    decomposer = ProviderDecomposer(fallback=TemplateDecomposer()) if live \
        else TemplateDecomposer()
    # Tiered, not provider-only: the classifier runs on EVERY voice turn,
    # and a provider call costs seconds. Rules answer the clear cases
    # instantly; the model is consulted only where they are unsure.
    classifier = TieredIntentClassifier(router.pick(TaskKind.FAST)) if live \
        else HeuristicIntentClassifier()

    mgr = AgentManager(
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
        decomposer=decomposer,
        limits=limits,
        auto_approve=auto_approve,
    )
    attach_extensions(mgr, in_memory=in_memory, base_dir=None if in_memory else os.path.dirname(task_db))
    return mgr


def attach_extensions(mgr: AgentManager, *, in_memory: bool = False,
                      base_dir: Optional[str] = None) -> AgentManager:
    """Give the manager its scheduler and industry-extension registry, and
    re-enable the packs that were on last session - before start(), so a
    recovered task from a pack finds its worker registered.

    In-memory (tests): no state files, and pack data under the workspace,
    never the user profile.
    """
    from agents.extensions import ExtensionRegistry
    from agents.manager.scheduler import Scheduler
    base = os.path.join(mgr.workspace_root, ".extensions") if in_memory else (base_dir or data_dir())
    mgr.scheduler = Scheduler(mgr, state_path=None if in_memory else os.path.join(base, "schedules.json"))
    mgr.extensions = ExtensionRegistry(mgr, state_path=None if in_memory else os.path.join(base, "extensions.json"),
                                       data_root=base)
    try:
        mgr.extensions.discover()
        if not in_memory:
            restored = mgr.extensions.restore()
            if restored:
                log.info("extensions enabled: %s", ", ".join(restored))
    except Exception:  # noqa: BLE001 - a broken pack must never stop the workforce
        log.exception("extension discovery failed")
    return mgr


def describe_plan(task) -> str:
    """The 'Understood. I will: 1. … Starting now.' acknowledgement."""
    reply = task.metadata.get("reply") or f"Understood. Starting: {task.title}."
    if task.steps:
        lines = [f"{i + 1}. {s.title}" for i, s in enumerate(task.steps[:8])]
        more = f" (+{len(task.steps) - 8} more)" if len(task.steps) > 8 else ""
        return f"{reply}\nI will:\n" + "\n".join(lines) + more
    return reply
