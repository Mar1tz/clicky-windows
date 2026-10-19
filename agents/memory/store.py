"""SQLite MemoryStore: what an agent remembers between tasks.

Memory here is not a vector database. It is a handful of short, tagged
text entries per project ("uses Vite + React", "npm install failed
behind proxy -> set HTTPS_PROXY", "user prefers TypeScript") that get
folded into the next prompt as a compact block. Recall is by scope,
kind, tag and a plain text search - FTS5 when the bundled sqlite has it
(CPython's Windows build does), LIKE otherwise. The choice is made once
per process and both paths return the same rows for simple queries, so
tests behave the same on either.

The same .db file as SQLiteTaskStore is fine: the tables do not overlap
and each store holds its own connection.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from typing import Any, Optional

from agents.core import MemoryEntry, MemoryStore

_SCHEMA = """
create table if not exists entries (
    id          text primary key,
    scope       text not null,
    kind        text not null,
    content     text not null,
    tags        text not null default '[]',
    data        text not null default '{}',
    created_at  real not null
);
create index if not exists entries_scope_kind_created on entries (scope, kind, created_at desc);
"""

_FTS_SCHEMA = """
create virtual table if not exists entries_fts using fts5(id unindexed, content, tags);
"""

_has_fts5: Optional[bool] = None


def has_fts5() -> bool:
    """Detect FTS5 once per process (a virtual-table create on :memory:)."""
    global _has_fts5
    if _has_fts5 is None:
        try:
            probe = sqlite3.connect(":memory:")
            probe.execute("create virtual table t using fts5(x)")
            probe.close()
            _has_fts5 = True
        except sqlite3.Error:
            _has_fts5 = False
    return _has_fts5


_WORD = re.compile(r"[\w\-]+", re.UNICODE)


def _terms(text: str) -> list[str]:
    return [t for t in _WORD.findall(text.lower()) if t]


def _fold_wal(conn: sqlite3.Connection) -> None:
    """Fold the write-ahead log into the database and empty it (AG-09).
    SQLite only checkpoints as pages pile up, so a store written a little at
    a time kept its log: agent_memory.db was 4 KB beside a 1 MB log that had
    not been folded in since 11 Oct. At open and at close."""
    try:
        conn.execute("pragma wal_checkpoint(TRUNCATE)")
    except sqlite3.Error:
        pass                                   # busy, or :memory: - next time


class SQLiteMemoryStore(MemoryStore):
    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self.fts = has_fts5()
        with self._lock:
            self._conn.execute("pragma journal_mode=wal")
            self._conn.executescript(_SCHEMA)
            if self.fts:
                self._conn.executescript(_FTS_SCHEMA)
            self._conn.commit()
            _fold_wal(self._conn)

    # -- MemoryStore ---------------------------------------------------------

    def add(self, entry: MemoryEntry) -> None:
        tags = json.dumps(entry.tags, ensure_ascii=False)
        with self._lock:
            self._conn.execute(
                "insert or replace into entries (id, scope, kind, content, tags, data, created_at) "
                "values (?, ?, ?, ?, ?, ?, ?)",
                (entry.id, entry.scope, entry.kind, entry.content, tags,
                 json.dumps(entry.data, ensure_ascii=False, default=str), entry.created_at),
            )
            if self.fts:
                self._conn.execute("delete from entries_fts where id = ?", (entry.id,))
                self._conn.execute(
                    "insert into entries_fts (id, content, tags) values (?, ?, ?)",
                    (entry.id, entry.content, " ".join(entry.tags)),
                )
            self._conn.commit()

    def query(self, scope: str, *, kind: Optional[str] = None,
              tags: Optional[list[str]] = None, text: Optional[str] = None,
              limit: int = 50) -> list[MemoryEntry]:
        """Newest first. `tags` matches entries carrying ANY of them; `text`
        matches every word of it (prefix match under FTS, substring under
        LIKE)."""
        sql = "select * from entries where scope = ?"
        args: list[Any] = [scope]
        if kind is not None:
            sql += " and kind = ?"
            args.append(kind)
        terms = _terms(text) if text else []
        if terms:
            if self.fts:
                sql += " and id in (select id from entries_fts where entries_fts match ?)"
                args.append(" ".join(f'"{t}"*' for t in terms))
            else:
                for t in terms:
                    sql += " and (lower(content) like ? or lower(tags) like ?)"
                    args += [f"%{t}%", f"%{t}%"]
        sql += " order by created_at desc, rowid desc"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        wanted = set(tags or [])
        out: list[MemoryEntry] = []
        for r in rows:
            e = _row_to_entry(r)
            if wanted and not wanted.intersection(e.tags):
                continue
            out.append(e)
            if len(out) >= limit:
                break
        return out

    def project_summary(self, workspace: str, *, max_chars: int = 2000) -> str:
        """Compact prompt block: context facts, finished tasks, latest
        decisions, recent errors (with the fix when known)."""
        scope = self.project_scope(workspace)
        lines: list[str] = []

        # Context facts: newest value per key ("framework: React + Vite").
        seen: set[str] = set()
        ctx_lines = []
        for e in self.query(scope, kind="context", limit=50):
            key = str(e.data.get("key") or "").strip()
            if key in seen:
                continue
            seen.add(key)
            ctx_lines.append(f"- {key}: {e.data.get('value', e.content)}" if key else f"- {e.content}")
        if ctx_lines:
            lines += ["Project context:", *ctx_lines]

        done = [e for e in self.query(scope, kind="task", limit=20)
                if e.data.get("status", "completed") == "completed"]
        if done:
            lines += ["Done:", *(f"- {e.content}" for e in done[:8])]

        current = self.query(scope, kind="decision", limit=5) + self.query(scope, kind="current", limit=5)
        current.sort(key=lambda e: e.created_at, reverse=True)
        if current:
            lines += ["Current / decisions:", *(f"- {e.content}" for e in current[:5])]

        errors = self.query(scope, kind="error", limit=5)
        if errors:
            err_lines = []
            for e in errors:
                fix = e.data.get("solution")
                err_lines.append(f"- {e.content} -> fixed by: {fix}" if fix else f"- {e.content}")
            lines += ["Recent errors:", *err_lines]

        return _fit(lines, max_chars)

    def clear(self, scope: str) -> None:
        with self._lock:
            if self.fts:
                self._conn.execute(
                    "delete from entries_fts where id in (select id from entries where scope = ?)",
                    (scope,))
            self._conn.execute("delete from entries where scope = ?", (scope,))
            self._conn.commit()

    # -- extras --------------------------------------------------------------

    def count(self, scope: Optional[str] = None) -> int:
        with self._lock:
            if scope is None:
                return self._conn.execute("select count(*) from entries").fetchone()[0]
            return self._conn.execute(
                "select count(*) from entries where scope = ?", (scope,)).fetchone()[0]

    def close(self) -> None:
        with self._lock:
            _fold_wal(self._conn)
            try:
                self._conn.close()
            except sqlite3.Error:
                pass


def _row_to_entry(r: sqlite3.Row) -> MemoryEntry:
    return MemoryEntry(
        id=r["id"], scope=r["scope"], kind=r["kind"], content=r["content"],
        tags=json.loads(r["tags"] or "[]"), data=json.loads(r["data"] or "{}"),
        created_at=r["created_at"],
    )


def _fit(lines: list[str], max_chars: int) -> str:
    """Join whole lines while they fit; hard-cut with an ellipsis as the
    last resort (a single over-long line)."""
    out: list[str] = []
    used = 0
    for line in lines:
        cost = len(line) + (1 if out else 0)
        if used + cost > max_chars:
            break
        out.append(line)
        used += cost
    text = "\n".join(out)
    if not out and lines and max_chars > 1:
        text = lines[0][: max_chars - 1] + "…"
    return text
