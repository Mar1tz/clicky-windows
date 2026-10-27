"""Facts that know when they were true (MEM-01, after Graphiti).

The notes in store.py are short tagged lines: a stale "Lee's email is
old@..." wins as often as the fresh one. Graphiti's answer, rebuilt on
SQLite (no graph server to ship): every fact is a relationship between an
entity and a value, with when it became true (valid_at) and when it
stopped (invalid_at), and the episode it was learned from.

* A new value for a one-value fact (an email, an address, where someone
  works) ends the old one - invalid_at - rather than deleting it: "what
  was Lee's email last month" still has an answer, and nothing is lost to
  a mistake. Facts that can hold many values (likes, projects) just add.
* recall(query) finds facts by their words (FTS5) and walks one hop: the
  entities the query names bring all their current facts with them.
* Point in time: at=t gives what was true then.
* forget(name) removes the entity, its facts and the episodes they came
  from - gone, not marked (EN-14).
* Groups: "user", "project:<folder>", "job:<id>".

Facts reach here from rules (agents/memory/extract.py) - no model call.
"""
from __future__ import annotations

import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Optional

from agents.core import new_id

from .store import has_fts5

# One value at a time: a new one ends the old.
SINGLE = {"email", "phone", "address", "lives_in", "works_at", "birthday", "role", "timezone", "manager",
          "company", "job_title", "website"}

_SCHEMA = """
create table if not exists episodes (id text primary key, source text, content text, occurred_at real,
    ingested_at real, group_id text);
create table if not exists entities (id text primary key, name text, kind text, summary text default '');
create unique index if not exists entities_name on entities(lower(name), kind);
create table if not exists facts (id text primary key, subject_id text, predicate text, value text, text text,
    valid_at real, invalid_at real, episode_id text, group_id text, source text default '');
create index if not exists facts_subject on facts(subject_id, predicate);
"""


@dataclass
class Fact:
    id: str
    subject: str
    predicate: str
    value: str
    text: str
    valid_at: float
    invalid_at: Optional[float]
    group_id: str
    source: str = ""

    def line(self) -> str:
        since = time.strftime("%d %b %Y", time.localtime(self.valid_at))
        until = (" until " + time.strftime("%d %b %Y", time.localtime(self.invalid_at))) if self.invalid_at else ""
        return f"{self.text} (since {since}{until})"


class FactStore:
    def __init__(self, path: str = ":memory:") -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self._fts = has_fts5()
        if self._fts:
            self._db.execute("create virtual table if not exists facts_fts using fts5(text, fact_id unindexed)")
        self._db.commit()

    # -- writing ------------------------------------------------------------------

    def episode(self, source: str, content: str, *, group: str = "user", at: Optional[float] = None) -> str:
        eid = new_id()
        now = time.time()
        with self._lock:
            self._db.execute("insert into episodes values (?,?,?,?,?,?)",
                             (eid, source, (content or "")[:2000], at or now, now, group))
            self._db.commit()
        return eid

    def _entity(self, name: str, kind: str) -> str:
        row = self._db.execute("select id from entities where lower(name)=lower(?) and kind=?",
                               (name, kind)).fetchone()
        if row:
            return row["id"]
        eid = new_id()
        self._db.execute("insert into entities (id, name, kind) values (?,?,?)", (eid, name, kind))
        return eid

    def learn(self, subject: str, predicate: str, value: str, text: str, *, kind: str = "person",
              episode: str = "", group: str = "user", at: Optional[float] = None, source: str = "") -> Optional[str]:
        """Add a fact; a different value for a one-value fact ends the old one.
        None when it was already known (same value, still true)."""
        at = at or time.time()
        predicate = predicate.lower()
        with self._lock:
            sid = self._entity(subject.strip(), kind)
            live = self._db.execute(
                "select id, value, valid_at from facts where subject_id=? and predicate=? and invalid_at is null "
                "and group_id=?", (sid, predicate, group)).fetchall()
            if any(r["value"].lower() == value.lower() for r in live):
                return None
            ended = None
            if predicate in SINGLE:
                # Arriving late (said before a value already kept): history, ended by the newer one.
                newer = [r["valid_at"] for r in live if r["valid_at"] > at]
                if newer:
                    ended = min(newer)
                else:
                    for r in live:
                        self._db.execute("update facts set invalid_at=? where id=?", (at, r["id"]))
            fid = new_id()
            self._db.execute("insert into facts values (?,?,?,?,?,?,?,?,?,?)",
                             (fid, sid, predicate, value, text, at, ended, episode, group, source))
            if self._fts:
                self._db.execute("insert into facts_fts (text, fact_id) values (?,?)", (text, fid))
            self._db.commit()
        return fid

    def forget(self, name: str) -> int:
        """Gone, not marked: the entity, its facts, their search rows and episodes."""
        with self._lock:
            ents = [r["id"] for r in self._db.execute("select id from entities where lower(name)=lower(?)", (name,))]
            if not ents:
                return 0
            marks = ",".join("?" * len(ents))
            facts = self._db.execute(f"select id, episode_id from facts where subject_id in ({marks})", ents).fetchall()
            for f in facts:
                if self._fts:
                    self._db.execute("delete from facts_fts where fact_id=?", (f["id"],))
                if f["episode_id"]:
                    self._db.execute("delete from episodes where id=?", (f["episode_id"],))
            self._db.execute(f"delete from facts where subject_id in ({marks})", ents)
            self._db.execute(f"delete from entities where id in ({marks})", ents)
            self._db.commit()
            return len(facts)

    # -- reading --------------------------------------------------------------------

    def _rows(self, where: str, args: tuple) -> list[Fact]:
        rows = self._db.execute(
            "select f.*, e.name subject from facts f join entities e on e.id=f.subject_id where " + where, args)
        return [Fact(r["id"], r["subject"], r["predicate"], r["value"], r["text"], r["valid_at"], r["invalid_at"],
                     r["group_id"], r["source"]) for r in rows]

    @staticmethod
    def _when(at: Optional[float]) -> tuple[str, tuple]:
        if at is None:
            return "f.invalid_at is null", ()
        return "f.valid_at <= ? and (f.invalid_at is null or f.invalid_at > ?)", (at, at)

    def current(self, subject: str, predicate: str, *, group: str = "user", at: Optional[float] = None) -> Optional[str]:
        when, args = self._when(at)
        with self._lock:
            rows = self._rows(f"lower(e.name)=lower(?) and f.predicate=? and f.group_id=? and {when} "
                              "order by f.valid_at desc", (subject, predicate.lower(), group, *args))
        return rows[0].value if rows else None

    def about(self, subject: str, *, group: str = "user", at: Optional[float] = None) -> list[Fact]:
        when, args = self._when(at)
        with self._lock:
            return self._rows(f"lower(e.name)=lower(?) and f.group_id=? and {when} order by f.valid_at",
                              (subject, group, *args))

    def recall(self, query: str, *, group: str = "", at: Optional[float] = None, limit: int = 8) -> list[Fact]:
        """MEM-02 (keyword half): facts whose words fit, then one hop - every
        current fact of an entity the query names. Only what was true at
        `at` (default: now); a stale value never outranks the current one."""
        words = [w for w in re.findall(r"[\w@.\-]+", (query or "").lower()) if len(w) > 2]
        if not words:
            return []
        when, args = self._when(at)
        grp, gargs = ("and f.group_id=?", (group,)) if group else ("", ())
        found: dict[str, Fact] = {}
        with self._lock:
            names = self._db.execute("select name from entities").fetchall()
            named = [r["name"] for r in names if r["name"].lower() in words or r["name"].lower() in query.lower()]
            if re.search(r"\b(my|me|mine|i)\b", query.lower()) and any(r["name"] == "me" for r in names):
                named.append("me")                                # "what's my address": their own facts
            for name in named:                                    # one hop: the entity's current facts
                for f in self._rows(f"lower(e.name)=lower(?) {grp} and {when}", (name, *gargs, *args)):
                    found.setdefault(f.id, f)
            if self._fts:
                match = " OR ".join('"' + w.replace('"', "") + '"' for w in words)
                ids = [r["fact_id"] for r in self._db.execute(
                    "select fact_id from facts_fts where facts_fts match ? order by rank limit 40", (match,))]
            else:
                ids = [r["id"] for r in self._db.execute(
                    "select id from facts where " + " or ".join("lower(text) like ?" for _ in words),
                    tuple(f"%{w}%" for w in words))]
            for fid in ids:
                for f in self._rows(f"f.id=? {grp} and {when}", (fid, *gargs, *args)):
                    found.setdefault(f.id, f)
        return sorted(found.values(), key=lambda f: -f.valid_at)[:limit]
