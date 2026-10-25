"""Memory with provenance; forget means gone (EN-13, EN-14).

Every memory row says where it came from, when, for whom, until when, how
sure, and whether the person confirmed it; an expired one is never offered
again; a database from before gains the columns. Forgetting one removes it
and its search index entry, and nothing of it is left in the file.
"""
from __future__ import annotations

import sqlite3
import time

from agents.core import MemoryEntry
from agents.memory import SQLiteMemoryStore

SECRET = "zanzibar-quartz-7781"


def test_provenance_round_trips_and_expiry_is_kept(tmp_path):
    store = SQLiteMemoryStore(str(tmp_path / "m.db"))
    store.add(MemoryEntry(scope="user", kind="preference", content="prefers metric units",
                          source="user", confidence=1.0, confirmed=True))
    store.add(MemoryEntry(scope="user", kind="preference", content="likes the old layout",
                          source="job:abc", confidence=0.6, expires_at=time.time() - 1))
    got = store.query("user")
    assert [e.content for e in got] == ["prefers metric units"], "an expired memory is not offered"
    e = got[0]
    assert (e.source, e.confidence, e.confirmed) == ("user", 1.0, True) and e.updated_at
    store.close()


def test_a_database_from_before_gains_the_columns(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("create table entries (id text primary key, scope text not null, kind text not null, "
                 "content text not null, tags text not null default '[]', data text not null default '{}', "
                 "created_at real not null)")
    conn.execute("insert into entries values ('1', 'user', 'context', 'old fact', '[]', '{}', 1.0)")
    conn.commit()
    conn.close()
    store = SQLiteMemoryStore(str(path))
    [old] = store.query("user")
    assert old.content == "old fact" and old.source == "" and old.confidence == 1.0 and not old.confirmed
    store.close()


def test_forget_means_gone(tmp_path):
    path = tmp_path / "m.db"
    store = SQLiteMemoryStore(str(path))
    entry = MemoryEntry(scope="user", kind="context", content=f"the alarm code is {SECRET}")
    store.add(entry)
    store.add(MemoryEntry(scope="user", kind="context", content="likes tea"))
    assert store.query("user", text=SECRET.split("-")[0])
    assert store.delete(entry.id) and not store.delete(entry.id)
    assert not store.query("user", text=SECRET.split("-")[0]), "search finds nothing of it"
    assert [e.content for e in store.query("user")] == ["likes tea"]
    store.close()
    raw = path.read_bytes() + (path.with_name("m.db-wal").read_bytes()
                               if path.with_name("m.db-wal").exists() else b"")
    assert SECRET.encode() not in raw, "and nothing of it is left in the file"


def test_the_voices_facts_carry_provenance_and_can_be_forgotten(tmp_path, monkeypatch):
    import numpy as np

    from ai import gemini_memory as gm
    monkeypatch.setattr(gm, "_db_path", lambda: tmp_path / "memory.db")
    conn = gm._connect()
    vec = np.zeros(gm.DIMS, dtype=np.float32).tobytes()
    conn.execute("INSERT INTO passages (kind, source, text, vec, created_at, confidence) "
                 "VALUES ('fact', 'live', ?, ?, ?, 0.8)", (f"code {SECRET}", vec, time.time()))
    conn.execute("INSERT INTO passages (kind, source, text, vec, created_at, expires_at) "
                 "VALUES ('fact', 'live', 'old news', ?, ?, ?)", (vec, time.time(), time.time() - 5))
    conn.commit()
    conn.close()
    facts = gm.facts()
    mine = next(f for f in facts if SECRET in f["text"])
    assert mine["source"] == "live" and mine["confidence"] == 0.8 and mine["confirmed"] is False
    assert gm.confirm_fact(mine["id"]) and next(f for f in gm.facts() if f["id"] == mine["id"])["confirmed"]
    assert [r[3] for r in gm._rows(gm._connect(), kinds=("fact",))] == [f"code {SECRET}"], \
        "an expired fact is never recalled"
    assert gm.forget_fact(mine["id"])
    assert SECRET.encode() not in (tmp_path / "memory.db").read_bytes()
