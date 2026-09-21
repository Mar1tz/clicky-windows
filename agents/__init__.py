"""Tora autonomous agent system.

Layout (each package has one owner — see CLAUDE.md):

    agents/core          contracts: models, events, interfaces   (manager)
    agents/manager       AgentManager, Orchestrator, runtime      (manager)
    agents/tools         ToolRegistry + tool implementations      (worker 1)
    agents/providers     AgentProvider ABC + Mock + Router        (worker 2)
    agents/workers       BaseWorker + Coding/File/Terminal/...    (worker 2)
    agents/understanding intent classification (chat vs task)     (worker 2)
    agents/memory        AgentMemory                              (worker 3)
    agents/planning      Planner                                  (worker 3)
    agents/verification  Verifier                                 (worker 3)
    agents/persistence   TaskStore / history (SQLite)             (worker 3)
    agents/ui            Qt: Active Agents panel, timeline        (worker 1, round 2)

Nothing in here imports PyQt6 except agents/ui. The runtime is plain
asyncio on a daemon thread, mirroring CompanionManager.
"""
