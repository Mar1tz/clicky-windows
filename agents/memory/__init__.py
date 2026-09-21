"""Agent memory (worker 3): SQLiteMemoryStore plus the AgentMemory façade.

The façade exists so the runtime and workers never build MemoryEntry
objects by hand - they say `memory.remember_error(ws, err, fix)` and the
scope/kind/tag conventions stay in one place. `context_for(workspace)`
is the block a worker prepends to its system prompt.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from agents.core import MemoryEntry, MemoryStore, Task

from .store import SQLiteMemoryStore, _fit, has_fts5

__all__ = ["AgentMemory", "SQLiteMemoryStore", "has_fts5"]

USER_SCOPE = "user"


class AgentMemory:
    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    # -- write ---------------------------------------------------------------

    def remember_task(self, task: Task) -> MemoryEntry:
        """Record a finished (or failed) task under its workspace."""
        status = task.status.value
        text = task.title or task.goal
        if task.result:
            text = f"{text} — {task.result}"
        entry = MemoryEntry(
            scope=self.store.project_scope(task.workspace), kind="task", content=text,
            tags=["task", status, task.kind.value],
            data={"task_id": task.id, "status": status, "goal": task.goal,
                  "error": task.error, "steps": len(task.steps)},
        )
        self.store.add(entry)
        return entry

    def remember_decision(self, workspace: str, text: str, **data: Any) -> MemoryEntry:
        entry = MemoryEntry(scope=self.store.project_scope(workspace), kind="decision",
                            content=text, tags=["decision"], data=dict(data))
        self.store.add(entry)
        return entry

    def remember_error(self, workspace: str, error: str,
                       solution: Optional[str] = None) -> MemoryEntry:
        data: dict[str, Any] = {}
        tags = ["error"]
        if solution:
            data["solution"] = solution
            tags.append("solved")
        entry = MemoryEntry(scope=self.store.project_scope(workspace), kind="error",
                            content=error, tags=tags, data=data)
        self.store.add(entry)
        return entry

    def remember_context(self, workspace: str, key: str, value: Any) -> MemoryEntry:
        """A project fact keyed for overwrite-by-recency: framework, stack,
        package manager, entry point..."""
        entry = MemoryEntry(scope=self.store.project_scope(workspace), kind="context",
                            content=f"{key}: {value}", tags=["context", key],
                            data={"key": key, "value": value})
        self.store.add(entry)
        return entry

    def remember_preference(self, text: str, **data: Any) -> MemoryEntry:
        entry = MemoryEntry(scope=USER_SCOPE, kind="preference", content=text,
                            tags=["preference"], data=dict(data))
        self.store.add(entry)
        return entry

    # -- read ----------------------------------------------------------------

    def context_for(self, workspace: str, *, max_chars: int = 2000) -> str:
        """Project summary plus user preferences, budgeted to max_chars."""
        prefs = self.store.query(USER_SCOPE, kind="preference", limit=10)
        pref_block = ""
        if prefs:
            pref_block = _fit(["User preferences:", *(f"- {p.content}" for p in prefs)], max_chars // 4)
        budget = max_chars - (len(pref_block) + 2 if pref_block else 0)
        summary = self.store.project_summary(workspace, max_chars=max(0, budget))
        parts = [p for p in (summary, pref_block) if p]
        return "\n\n".join(parts)

    def preferences(self) -> list[MemoryEntry]:
        return self.store.query(USER_SCOPE, kind="preference", limit=50)

    def errors_for(self, workspace: str, *, text: Optional[str] = None) -> list[MemoryEntry]:
        return self.store.query(self.store.project_scope(workspace), kind="error", text=text)

    def forget_project(self, workspace: str) -> None:
        self.store.clear(self.store.project_scope(workspace))

    @staticmethod
    def workspace_name(workspace: str) -> str:
        return os.path.basename(os.path.normpath(workspace))
