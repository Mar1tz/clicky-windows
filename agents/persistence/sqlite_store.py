"""SQLite-backed TaskStore: the durable history behind the Active Agents UI.

Why SQLite: it ships with Python, needs no server, survives app restarts
and copes with the write pattern we have (a few hundred small rows per
task, appended from whichever thread happens to publish an event). The
rows keep the full JSON of each model so the schema never has to chase
new dataclass fields; the extra typed columns exist only for filtering
and ordering.

Threading: the EventBus runs subscribers on the publisher's thread, so
`append_event` may be called from the agent runtime, the GUI thread or a
tool's worker thread at the same time. One connection opened with
check_same_thread=False and guarded by a Lock is simpler and faster than
a connection per thread for this volume. WAL mode keeps readers (the UI
refreshing a list) from blocking writers.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any, Optional

from agents.core import (
    AgentState, Event, EventBus, Plan, Task, TaskStore, Unsubscribe, now,
)

_SCHEMA = """
create table if not exists tasks (
    id          text primary key,
    status      text not null,
    kind        text not null,
    title       text not null,
    created_at  real not null,
    updated_at  real not null,
    json        text not null
);
create index if not exists tasks_status_created on tasks (status, created_at desc);

create table if not exists plans (
    id       text primary key,
    task_id  text not null,
    json     text not null
);
create index if not exists plans_task on plans (task_id);

create table if not exists events (
    id        text primary key,
    task_id   text,
    agent_id  text,
    kind      text not null,
    level     text not null,
    ts        real not null,
    json      text not null
);
create index if not exists events_task_ts on events (task_id, ts);

create table if not exists agents (
    id    text primary key,
    json  text not null
);
"""


def default_db_path() -> str:
    """%LOCALAPPDATA%/Tora/agents.db (falls back to ~/.tora on non-Windows)."""
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".tora")
    return os.path.join(base, "Tora", "agents.db")


class SQLiteTaskStore(TaskStore):
    """TaskStore on one SQLite file (or ":memory:" for tests)."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or default_db_path()
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            # WAL is a no-op for :memory: (returns "memory"); harmless.
            self._conn.execute("pragma journal_mode=wal")
            self._conn.execute("pragma synchronous=normal")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # -- bus -----------------------------------------------------------------

    def attach(self, bus: EventBus) -> Unsubscribe:
        """Persist every event the bus publishes. Returns the unsubscribe."""
        return bus.subscribe(self.append_event)

    # -- tasks ---------------------------------------------------------------

    def save_task(self, task: Task) -> None:
        task.updated_at = now()
        with self._lock:
            self._conn.execute(
                "insert or replace into tasks (id, status, kind, title, created_at, updated_at, json) "
                "values (?, ?, ?, ?, ?, ?, ?)",
                (task.id, task.status.value, task.kind.value, task.title,
                 task.created_at, task.updated_at, _dumps(task.to_dict())),
            )
            self._conn.commit()

    def get_task(self, task_id: str) -> Optional[Task]:
        with self._lock:
            row = self._conn.execute("select json from tasks where id = ?", (task_id,)).fetchone()
        return Task.from_dict(json.loads(row["json"])) if row else None

    def list_tasks(self, *, status: Optional[str] = None, limit: int = 100) -> list[Task]:
        sql = "select json from tasks"
        args: list[Any] = []
        if status is not None:
            sql += " where status = ?"
            args.append(getattr(status, "value", status))   # accept TaskStatus or str
        sql += " order by created_at desc limit ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [Task.from_dict(json.loads(r["json"])) for r in rows]

    def delete_task(self, task_id: str) -> None:
        """Removes the task and everything hanging off it (plans, events)."""
        with self._lock:
            self._conn.execute("delete from events where task_id = ?", (task_id,))
            self._conn.execute("delete from plans where task_id = ?", (task_id,))
            self._conn.execute("delete from tasks where id = ?", (task_id,))
            self._conn.commit()

    # -- plans ---------------------------------------------------------------

    def save_plan(self, plan: Plan) -> None:
        with self._lock:
            self._conn.execute(
                "insert or replace into plans (id, task_id, json) values (?, ?, ?)",
                (plan.id, plan.task_id, _dumps(plan.to_dict())),
            )
            self._conn.commit()

    def get_plan(self, plan_id: str) -> Optional[Plan]:
        with self._lock:
            row = self._conn.execute("select json from plans where id = ?", (plan_id,)).fetchone()
        return Plan.from_dict(json.loads(row["json"])) if row else None

    def plans_for_task(self, task_id: str) -> list[Plan]:
        """Every plan saved for a task, oldest first (replans append)."""
        with self._lock:
            rows = self._conn.execute(
                "select json from plans where task_id = ? order by rowid", (task_id,)).fetchall()
        return [Plan.from_dict(json.loads(r["json"])) for r in rows]

    # -- events --------------------------------------------------------------

    def append_event(self, event: Event) -> None:
        # "or ignore": the same event is delivered twice when a store is
        # attached to two buses. Never raise here - the bus would swallow it
        # but log a traceback per event.
        try:
            with self._lock:
                self._conn.execute(
                    "insert or ignore into events (id, task_id, agent_id, kind, level, ts, json) "
                    "values (?, ?, ?, ?, ?, ?, ?)",
                    (event.id, event.task_id, event.agent_id, event.kind.value, event.level,
                     event.timestamp, _dumps(event.to_dict())),
                )
                self._conn.commit()
        except sqlite3.Error:
            pass

    def events(self, task_id: str, *, limit: int = 1000,
               since: Optional[float] = None) -> list[Event]:
        sql = "select json from events where task_id = ?"
        args: list[Any] = [task_id]
        if since is not None:
            sql += " and ts > ?"
            args.append(since)
        sql += " order by ts asc, rowid asc limit ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [Event.from_dict(json.loads(r["json"])) for r in rows]

    # -- agents --------------------------------------------------------------

    def save_agent(self, state: AgentState) -> None:
        with self._lock:
            self._conn.execute(
                "insert or replace into agents (id, json) values (?, ?)",
                (state.id, _dumps(state.to_dict())),
            )
            self._conn.commit()

    def list_agents(self) -> list[AgentState]:
        with self._lock:
            rows = self._conn.execute("select json from agents order by rowid").fetchall()
        return [AgentState.from_dict(json.loads(r["json"])) for r in rows]

    def delete_agent(self, agent_id: str) -> None:
        with self._lock:
            self._conn.execute("delete from agents where id = ?", (agent_id,))
            self._conn.commit()

    # -- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    def __enter__(self) -> "SQLiteTaskStore":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


def _dumps(obj: Any) -> str:
    # default=str: a stray Path/datetime in `metadata` must not lose the row.
    return json.dumps(obj, ensure_ascii=False, default=str)
