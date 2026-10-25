"""
Memory and documents — gemini-embedding-001.

Not conversational at all. This turns text into vectors so Tora can find
the one paragraph that matters instead of re-reading everything.

Two things live in here:

  facts      small things worth remembering about the user — "always uses
             dark mode", "is learning Blender", "prefers metric". Written by
             the remember tool, read by recall.

  passages   attached documents, split into overlapping chunks. Dragging a
             40-page PDF onto the panel used to append the whole extracted
             text — up to 60,000 characters — to the system prompt of every
             single question for as long as it stayed attached. That is slow,
             expensive, and buries the question. Now the document is embedded
             once on attach and each question pulls back only the handful of
             passages that relate to it.

Stored in SQLite next to the journal, vectors as raw float32. 768 dimensions
rather than the 3072 default: indistinguishable for "which of these passages
is relevant" and a quarter of the disk.

EN-13 / EN-14 (the Massive Direction Plan): every row says where it came
from, when, for whom (scope), until when (expires_at), how sure (confidence)
and whether the person confirmed it; an expired row is never recalled.
Forgetting overwrites the row on disk (secure_delete) - nothing of it is
left to find.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

from ai import gemini

_log = logging.getLogger("tora.memory")

DIMS = 768
CHUNK_CHARS = 1400
CHUNK_OVERLAP = 200
# Embedding a batch of passages in one request; well inside the API's limit
# and enough that a long PDF is a handful of round trips rather than dozens.
BATCH = 50
MIN_SCORE = 0.45          # below this a "match" is noise


def _db_path() -> Path:
    from config import data_dir
    return data_dir() / "memory.db"


_PROVENANCE = (("scope", "TEXT NOT NULL DEFAULT 'user'"), ("updated_at", "REAL"), ("expires_at", "REAL"),
               ("confidence", "REAL NOT NULL DEFAULT 1.0"), ("confirmed", "INTEGER NOT NULL DEFAULT 0"))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.execute("PRAGMA secure_delete=ON")         # EN-14: forgotten means overwritten
    conn.execute("""
        CREATE TABLE IF NOT EXISTS passages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            kind       TEXT NOT NULL,      -- 'fact' | 'document'
            source     TEXT,               -- filename, app name, ''
            text       TEXT NOT NULL,
            vec        BLOB NOT NULL,
            created_at REAL NOT NULL
        )
    """)
    have = {row[1] for row in conn.execute("PRAGMA table_info(passages)")}
    for name, decl in _PROVENANCE:                  # EN-13: a database from before gains them
        if name not in have:
            conn.execute(f"ALTER TABLE passages ADD COLUMN {name} {decl}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_passages_kind ON passages (kind, source)"
    )
    conn.commit()
    return conn


@dataclass
class Hit:
    text: str
    kind: str
    source: str
    score: float


# ── Chunking ─────────────────────────────────────────────────────────────────

def chunk(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split on paragraph boundaries where possible, with a little overlap so
    a sentence spanning two chunks is still findable from either."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]

    out: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            # Prefer a paragraph break, then a sentence end, then a space.
            for sep in ("\n\n", "\n", ". ", " "):
                cut = text.rfind(sep, start + size // 2, end)
                if cut != -1:
                    end = cut + len(sep)
                    break
        piece = text[start:end].strip()
        if piece:
            out.append(piece)
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return out


# ── Writing ──────────────────────────────────────────────────────────────────

async def _embed(texts: list[str], task: str) -> list[np.ndarray]:
    vecs: list[np.ndarray] = []
    for i in range(0, len(texts), BATCH):
        raw = await gemini.embed(texts[i:i + BATCH], task=task, dims=DIMS)
        for v in raw:
            a = np.asarray(v, dtype=np.float32)
            n = float(np.linalg.norm(a))
            # Store unit vectors so recall is a dot product, not a division
            # per row.
            vecs.append(a / n if n else a)
    return vecs


async def index_document(source: str, text: str) -> int:
    """Embed a document and store its passages. Returns the passage count."""
    pieces = chunk(text)
    if not pieces:
        return 0
    try:
        vecs = await _embed(pieces, "RETRIEVAL_DOCUMENT")
    except Exception as e:
        _log.warning("indexing %s failed: %s", source, e)
        return 0

    now = time.time()
    conn = _connect()
    try:
        conn.execute("DELETE FROM passages WHERE kind='document' AND source=?",
                     (source,))
        conn.executemany(
            "INSERT INTO passages (kind, source, text, vec, created_at, scope, updated_at) "
            "VALUES ('document', ?, ?, ?, ?, 'document', ?)",
            [(source, p, v.tobytes(), now, now) for p, v in zip(pieces, vecs)],
        )
        conn.commit()
    finally:
        conn.close()
    _log.info("indexed %s into %d passages", source, len(pieces))
    return len(pieces)


async def remember(text: str, *, source: str = "") -> bool:
    """Store one small fact about the user."""
    text = (text or "").strip()
    if not text:
        return False
    try:
        vecs = await _embed([text], "RETRIEVAL_DOCUMENT")
    except Exception as e:
        _log.warning("remember failed: %s", e)
        return False
    conn = _connect()
    try:
        # Near-duplicates pile up fast when the same preference is mentioned
        # in three different sessions; replace rather than accumulate.
        existing = _rows(conn, kinds=("fact",))
        if existing:
            scores = np.vstack([r[4] for r in existing]) @ vecs[0]
            if float(scores.max()) > 0.93:
                stale = existing[int(scores.argmax())][0]
                conn.execute("DELETE FROM passages WHERE id=?", (stale,))
        said_by_them = source == "user"
        conn.execute(
            "INSERT INTO passages (kind, source, text, vec, created_at, scope, updated_at, "
            "confidence, confirmed) VALUES ('fact', ?, ?, ?, ?, 'user', ?, ?, ?)",
            (source, text, vecs[0].tobytes(), time.time(), time.time(),
             1.0 if said_by_them else 0.8, 1 if said_by_them else 0),
        )
        conn.commit()
    finally:
        conn.close()
    return True


def forget(source: str) -> None:
    """Drop everything indexed from one source — a detached document."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM passages WHERE source=?", (source,))
        conn.commit()
    finally:
        conn.close()


def clear_documents() -> None:
    conn = _connect()
    try:
        conn.execute("DELETE FROM passages WHERE kind='document'")
        conn.commit()
    finally:
        conn.close()


# ── Reading ──────────────────────────────────────────────────────────────────

def _rows(
    conn: sqlite3.Connection, kinds: Optional[Iterable[str]] = None
) -> list[tuple[int, str, str, str, np.ndarray]]:
    """Every stored passage as (id, kind, source, text, unit_vector).

    Rows whose vector does not match the current dimensionality are skipped
    rather than crashing the search: changing GEMINI_EMBED_MODEL or DIMS
    leaves older rows behind, and a stale row should be invisible, not fatal.
    """
    sql = "SELECT id, kind, source, text, vec FROM passages WHERE (expires_at IS NULL OR expires_at > ?)"
    args: tuple = (time.time(),)
    if kinds:
        kinds = tuple(kinds)
        sql += f" AND kind IN ({','.join('?' * len(kinds))})"
        args += kinds
    out = []
    for pid, kind, source, text, blob in conn.execute(sql, args):
        vec = np.frombuffer(blob, dtype=np.float32)
        if vec.size == DIMS:
            out.append((pid, kind, source or "", text, vec))
    return out


async def recall(
    query: str,
    *,
    k: int = 5,
    kinds: Optional[Iterable[str]] = None,
) -> list[Hit]:
    """The passages most related to `query`, best first."""
    query = (query or "").strip()
    if not query:
        return []

    conn = _connect()
    try:
        rows = _rows(conn)
    finally:
        conn.close()
    if kinds:
        wanted = set(kinds)
        rows = [r for r in rows if r[1] in wanted]
    if not rows:
        return []

    try:
        qv = (await _embed([query], "RETRIEVAL_QUERY"))[0]
    except Exception as e:
        _log.warning("recall failed: %s", e)
        return []

    mat = np.vstack([r[4] for r in rows])
    scores = mat @ qv
    order = np.argsort(-scores)[:k]
    return [
        Hit(text=rows[i][3], kind=rows[i][1], source=rows[i][2],
            score=float(scores[i]))
        for i in order
        if float(scores[i]) >= MIN_SCORE
    ]


def format_for_prompt(hits: list[Hit]) -> str:
    """Render recalled passages for the system prompt."""
    if not hits:
        return ""
    facts = [h for h in hits if h.kind == "fact"]
    docs = [h for h in hits if h.kind != "fact"]
    out = []
    if facts:
        out.append("\n\nWHAT YOU REMEMBER ABOUT THIS USER:\n"
                   + "\n".join(f"  • {h.text}" for h in facts))
    if docs:
        out.append("\n\nRELEVANT PASSAGES FROM THE USER'S ATTACHED DOCUMENTS:")
        for h in docs:
            out.append(f"\n[{h.source}]\n{h.text}")
        out.append(
            "\nAnswer from these passages when the user refers to 'this "
            "document', 'my notes' or a page number. Say so plainly if the "
            "passages do not cover what they asked."
        )
    return "".join(out)


def recent_facts(limit: int = 2) -> list[tuple[str, float]]:
    """The facts most recently learned about the user, newest first, as
    (text, created_at) - Insights' "What Tora knows"."""
    conn = _connect()
    try:
        return conn.execute(
            "SELECT text, created_at FROM passages WHERE kind='fact' "
            "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    finally:
        conn.close()


def facts(limit: int = 200) -> list[dict]:
    """EN-13: the facts kept about the person, with where each came from -
    what Settings lists to inspect, confirm or forget (EN-15)."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, text, source, created_at, updated_at, expires_at, confidence, confirmed "
            "FROM passages WHERE kind='fact' ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    finally:
        conn.close()
    keys = ("id", "text", "source", "created_at", "updated_at", "expires_at", "confidence", "confirmed")
    return [dict(zip(keys, r)) | {"confirmed": bool(r[7])} for r in rows]


def forget_fact(fact_id: int) -> bool:
    """EN-14: one fact gone - overwritten on disk, never recalled again."""
    conn = _connect()
    try:
        gone = conn.execute("DELETE FROM passages WHERE id=? AND kind='fact'", (int(fact_id),)).rowcount
        conn.commit()
    finally:
        conn.close()
    return bool(gone)


def confirm_fact(fact_id: int, confirmed: bool = True) -> bool:
    conn = _connect()
    try:
        done = conn.execute("UPDATE passages SET confirmed=?, confidence=?, updated_at=? WHERE id=? AND kind='fact'",
                            (1 if confirmed else 0, 1.0 if confirmed else 0.8, time.time(), int(fact_id))).rowcount
        conn.commit()
    finally:
        conn.close()
    return bool(done)


def stats() -> dict:
    conn = _connect()
    try:
        facts = conn.execute(
            "SELECT COUNT(*) FROM passages WHERE kind='fact'"
        ).fetchone()[0]
        docs = conn.execute(
            "SELECT COUNT(DISTINCT source) FROM passages WHERE kind='document'"
        ).fetchone()[0]
        passages = conn.execute(
            "SELECT COUNT(*) FROM passages WHERE kind='document'"
        ).fetchone()[0]
    finally:
        conn.close()
    return {"facts": facts, "documents": docs, "passages": passages}
