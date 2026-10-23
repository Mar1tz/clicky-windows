"""DevLink's one local file: grants, the audit log, the timeline, the logs.

SQLite in the data folder (stdlib, already in the bundle). Log lines are
indexed with FTS5 so "ECONNREFUSED in the last ten minutes" is one query;
where a Python's SQLite has no FTS5, search falls back to LIKE.

Everything here stays on the machine. Two limits keep it small: each
stream is a ring buffer (RING_LINES lines or RING_BYTES, whichever comes
first - the oldest tenth goes when either is passed), and anything older
than RETENTION_DAYS is purged on start and every hour after.

Only the hub's own thread touches the connection - the gateway, the tools
and the settings page all reach it through the hub's loop.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable, Optional

RING_LINES = 10_000
RING_BYTES = 5_000_000
RETENTION_DAYS = 7

_SCHEMA = """
create table if not exists settings (key text primary key, value text);
create table if not exists workspaces (
    workspace_id text primary key, name text, folders text, last_seen real);
create table if not exists grants (
    workspace_id text, capability text, scope text, granted_at real,
    primary key (workspace_id, capability));
create table if not exists audit (
    id integer primary key, ts real, kind text, workspace_id text, tool text,
    args text, size integer, text text);
create table if not exists timeline (
    id integer primary key, ts real, workspace_id text, kind text, summary text, data text);
create table if not exists streams (
    stream_id text primary key, workspace_id text, kind text, name text, command text, cwd text,
    started real, ended real, exit_code integer, lines integer default 0, bytes integer default 0);
create table if not exists lines (
    id integer primary key, stream_id text, ts real, level text, text text, command_id text);
create index if not exists lines_by_stream on lines (stream_id, id);
create index if not exists timeline_by_ts on timeline (workspace_id, ts);
create index if not exists audit_by_ts on audit (ts);
"""

_FTS = """
create virtual table if not exists lines_fts using fts5(text, content='lines', content_rowid='id');
create trigger if not exists lines_ai after insert on lines begin
    insert into lines_fts (rowid, text) values (new.id, new.text); end;
create trigger if not exists lines_ad after delete on lines begin
    insert into lines_fts (lines_fts, rowid, text) values ('delete', old.id, old.text); end;
"""


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("pragma journal_mode=wal")
        self.db.executescript(_SCHEMA)
        try:
            self.db.executescript(_FTS)
            self.fts = True
        except sqlite3.OperationalError:          # a SQLite built without FTS5
            self.fts = False
        self.purge()

    def close(self) -> None:
        self.db.close()

    # ── settings ───────────────────────────────────────────────────────────

    def setting(self, key: str, default: str = "") -> str:
        row = self.db.execute("select value from settings where key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.db.execute("insert or replace into settings values (?, ?)", (key, value))

    # ── workspaces and grants ──────────────────────────────────────────────

    def seen_workspace(self, workspace_id: str, name: str, folders: list[str]) -> None:
        self.db.execute("insert or replace into workspaces values (?, ?, ?, ?)",
                        (workspace_id, name, json.dumps(folders), time.time()))

    def workspaces(self) -> list[dict]:
        rows = self.db.execute("select * from workspaces order by last_seen desc").fetchall()
        return [{"workspace_id": r["workspace_id"], "name": r["name"], "folders": json.loads(r["folders"] or "[]"),
                 "last_seen": r["last_seen"]} for r in rows]

    def forget_workspace(self, workspace_id: str) -> None:
        self.db.execute("delete from workspaces where workspace_id=?", (workspace_id,))
        self.db.execute("delete from grants where workspace_id=?", (workspace_id,))

    def saved_grants(self) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        for r in self.db.execute("select workspace_id, capability, scope from grants"):
            out.setdefault(r["workspace_id"], {})[r["capability"]] = r["scope"]
        return out

    def save_grant(self, workspace_id: str, capability: str, scope: Optional[str]) -> None:
        if scope is None:
            self.db.execute("delete from grants where workspace_id=? and capability=?", (workspace_id, capability))
        else:
            self.db.execute("insert or replace into grants values (?, ?, ?, ?)",
                            (workspace_id, capability, scope, time.time()))

    # ── audit ──────────────────────────────────────────────────────────────

    def audit(self, kind: str, workspace_id: str, tool: str, args: dict, text: str) -> None:
        self.db.execute("insert into audit (ts, kind, workspace_id, tool, args, size, text) values (?,?,?,?,?,?,?)",
                        (time.time(), kind, workspace_id, tool, json.dumps(args, ensure_ascii=False)[:2000],
                         len(text.encode("utf-8")), text))

    def audit_log(self, limit: int = 50) -> list[dict]:
        rows = self.db.execute("select * from audit order by id desc limit ?", (limit,)).fetchall()
        return [dict(r) | {"args": json.loads(r["args"] or "{}")} for r in rows]

    # ── timeline ───────────────────────────────────────────────────────────

    def add_event(self, workspace_id: str, kind: str, summary: str, data: Optional[dict] = None,
                  ts: Optional[float] = None) -> None:
        self.db.execute("insert into timeline (ts, workspace_id, kind, summary, data) values (?,?,?,?,?)",
                        (ts or time.time(), workspace_id, kind, summary, json.dumps(data or {})))

    def timeline(self, workspace_id: Optional[str] = None, since: float = 0.0, limit: int = 30,
                 kinds: Optional[Iterable[str]] = None) -> list[dict]:
        sql, args = "select * from timeline where ts >= ?", [since]
        if workspace_id:
            sql += " and workspace_id = ?"
            args.append(workspace_id)
        if kinds:
            kinds = list(kinds)
            sql += f" and kind in ({','.join('?' * len(kinds))})"
            args += kinds
        rows = self.db.execute(sql + " order by id desc limit ?", (*args, limit)).fetchall()
        return [dict(r) | {"data": json.loads(r["data"] or "{}")} for r in rows]

    # ── streams and their lines ────────────────────────────────────────────

    def open_stream(self, stream_id: str, workspace_id: str, kind: str, name: str,
                    command: str = "", cwd: str = "") -> None:
        self.db.execute("insert or ignore into streams (stream_id, workspace_id, kind, name, command, cwd, started) "
                        "values (?,?,?,?,?,?,?)", (stream_id, workspace_id, kind, name, command, cwd, time.time()))
        if command:
            self.db.execute("update streams set command=?, cwd=?, ended=null, exit_code=null where stream_id=?",
                            (command, cwd, stream_id))

    def end_stream(self, stream_id: str, exit_code: Optional[int]) -> None:
        self.db.execute("update streams set ended=?, exit_code=? where stream_id=?",
                        (time.time(), exit_code, stream_id))

    def stream(self, stream_id: str) -> Optional[dict]:
        row = self.db.execute("select * from streams where stream_id=?", (stream_id,)).fetchone()
        return dict(row) if row else None

    def streams(self, workspace_id: Optional[str] = None) -> list[dict]:
        sql, args = "select * from streams", ()
        if workspace_id:
            sql, args = sql + " where workspace_id=?", (workspace_id,)
        return [dict(r) for r in self.db.execute(sql + " order by coalesce(ended, started) desc", args)]

    def add_lines(self, stream_id: str, rows: list[tuple[float, str, str, str]]) -> None:
        """(ts, level, text, command_id) rows, then the ring trimmed."""
        if not rows:
            return
        self.db.execute("begin")
        try:
            self.db.executemany("insert into lines (stream_id, ts, level, text, command_id) values (?,?,?,?,?)",
                                [(stream_id, ts, level, text, cid) for ts, level, text, cid in rows])
            size = sum(len(r[2]) for r in rows)
            self.db.execute("update streams set lines = lines + ?, bytes = bytes + ? where stream_id=?",
                            (len(rows), size, stream_id))
            self.db.execute("commit")
        except Exception:
            self.db.execute("rollback")
            raise
        st = self.stream(stream_id)
        if st and (st["lines"] > RING_LINES or st["bytes"] > RING_BYTES):
            self._trim(stream_id, st)

    def _trim(self, stream_id: str, st: dict) -> None:
        drop = max(1, st["lines"] // 10, st["lines"] - RING_LINES)
        ids = self.db.execute("select id, length(text) as n from lines where stream_id=? order by id limit ?",
                              (stream_id, drop)).fetchall()
        if not ids:
            return
        self.db.execute("delete from lines where stream_id=? and id <= ?", (stream_id, ids[-1]["id"]))
        self.db.execute("update streams set lines = max(0, lines - ?), bytes = max(0, bytes - ?) where stream_id=?",
                        (len(ids), sum(r["n"] for r in ids), stream_id))

    def tail(self, stream_id: str, n: int, command_id: Optional[str] = None) -> list[dict]:
        sql, args = "select * from lines where stream_id=?", [stream_id]
        if command_id:
            sql += " and command_id=?"
            args.append(command_id)
        rows = self.db.execute(sql + " order by id desc limit ?", (*args, n)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def search(self, query: str, since: float = 0.0, level: Optional[str] = None,
               workspace_id: Optional[str] = None, limit: int = 50) -> list[dict]:
        args: list[Any] = []
        if self.fts:
            sql = ("select lines.*, streams.name as stream_name from lines_fts join lines on lines.id = lines_fts.rowid "
                   "join streams using (stream_id) where lines_fts match ? and lines.ts >= ?")
            args += [_fts_query(query), since]
        else:
            sql = ("select lines.*, streams.name as stream_name from lines join streams using (stream_id) "
                   "where lines.text like ? and lines.ts >= ?")
            args += [f"%{query}%", since]
        if level:
            sql += " and lines.level = ?"
            args.append(level)
        if workspace_id:
            sql += " and streams.workspace_id = ?"
            args.append(workspace_id)
        try:
            rows = self.db.execute(sql + " order by lines.id desc limit ?", (*args, limit)).fetchall()
        except sqlite3.OperationalError:          # a query FTS cannot parse
            return []
        return [dict(r) for r in reversed(rows)]

    # ── retention ──────────────────────────────────────────────────────────

    def purge(self, now: Optional[float] = None) -> None:
        cutoff = (now or time.time()) - RETENTION_DAYS * 86400
        self.db.execute("delete from lines where ts < ?", (cutoff,))
        self.db.execute("delete from timeline where ts < ?", (cutoff,))
        self.db.execute("delete from audit where ts < ?", (cutoff,))
        self.db.execute("delete from streams where coalesce(ended, started) < ? "
                        "and stream_id not in (select distinct stream_id from lines)", (cutoff,))


def _fts_query(query: str) -> str:
    """The words of `query`, each quoted: FTS5's own syntax (AND, *, :)
    is not something a spoken question should be able to trip over."""
    words = [w.replace('"', "") for w in query.split() if w.strip('"')]
    return " ".join(f'"{w}"' for w in words) or '""'
