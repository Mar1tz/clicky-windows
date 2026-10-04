"""
Knowledge Journal + Spaced Repetition.

Every Q&A Tora has gets logged to a SQLite db at:
    %LOCALAPPDATA%\\HeyTora\\journal.db

Voice queries that surface this:
    "what did I learn today"            → today's entries
    "what did I learn this week"        → past 7 days
    "show me my journal"                → all-time
    "quiz me on what I learned"         → spaced-repetition pull

Spaced repetition uses the SM-2 lite algorithm:
    intervals (days):  1, 3, 7, 14, 30
    each entry has `next_review_at` and `streak` columns
    "correct" answer  → streak +1, push next_review out
    "wrong"  answer   → reset streak to 0, due tomorrow
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


_INTERVALS_DAYS = (1, 3, 7, 14, 30, 60, 120)


def _db_path() -> Path:
    from config import data_dir
    return data_dir() / "journal.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at      REAL NOT NULL,
            app_key         TEXT,
            window_title    TEXT,
            question        TEXT NOT NULL,
            answer          TEXT NOT NULL,
            provider        TEXT,
            model           TEXT,
            streak          INTEGER DEFAULT 0,
            next_review_at  REAL,
            tags            TEXT
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_entries_created "
        "ON entries (created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_entries_due "
        "ON entries (next_review_at) WHERE next_review_at IS NOT NULL"
    )
    # The replayable plan behind a walkthrough entry. A table of its own so
    # an existing journal needs no migration.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS walkthroughs (
            entry_id    INTEGER PRIMARY KEY,
            plan_json   TEXT NOT NULL,
            seconds     REAL
        )
    """)
    return conn


# ─── Logging ──────────────────────────────────────────────────────────────────

def log_qa(
    question: str,
    answer: str,
    *,
    app_key: str = "",
    window_title: str = "",
    provider: str = "",
    model: str = "",
    tags: str = "",
) -> int:
    if not question.strip() or not answer.strip():
        return -1
    now = time.time()
    # First review tomorrow by default
    next_review = now + _INTERVALS_DAYS[0] * 86400
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO entries (created_at, app_key, window_title, question, "
            "answer, provider, model, streak, next_review_at, tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
            (now, app_key, window_title, question.strip(), answer.strip(),
             provider, model, next_review, tags),
        )
        return cur.lastrowid


WALKTHROUGH_TAG = "walkthrough"


def log_walkthrough(
    goal: str,
    steps: list[str],
    *,
    plan: Optional[dict] = None,
    app_key: str = "",
    window_title: str = "",
    seconds: float = 0.0,
) -> int:
    """A finished walkthrough, kept like anything else Tora taught.

    The goal becomes the question ("How do I export the video?") and the
    numbered steps the answer, so it turns up in "what did I learn today"
    and comes back for review like any other entry. The plan itself - the
    planner's JSON, with the targets that were actually used - is stored
    beside it, so the walkthrough can be replayed without planning again.
    """
    goal = " ".join(str(goal or "").split()).rstrip(".?!")
    steps = [s.strip() for s in steps if s and s.strip()]
    if not goal or not steps:
        return -1
    question = goal if goal.lower().startswith("how ") else (
        f"How do I {goal[0].lower()}{goal[1:]}")
    answer = "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
    entry_id = log_qa(f"{question[0].upper()}{question[1:]}?", answer,
                      app_key=app_key, window_title=window_title,
                      tags=WALKTHROUGH_TAG)
    if entry_id > 0 and plan:
        with _connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO walkthroughs (entry_id, plan_json, seconds) "
                "VALUES (?, ?, ?)", (entry_id, json.dumps(plan), float(seconds)))
    return entry_id


def walkthrough_plan(entry_id: int) -> Optional[dict]:
    """The stored plan for a walkthrough entry, or None."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT plan_json FROM walkthroughs WHERE entry_id = ?", (entry_id,)
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return None


# ─── Query helpers ────────────────────────────────────────────────────────────

def entries_since(seconds_ago: float) -> list[dict]:
    cutoff = time.time() - seconds_ago
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM entries WHERE created_at >= ? ORDER BY created_at DESC",
            (cutoff,),
        ).fetchall()
        cols = [d[0] for d in conn.execute("PRAGMA table_info(entries)").fetchall()]
        cols = [r[1] for r in conn.execute("PRAGMA table_info(entries)").fetchall()]
    return [dict(zip(cols, r)) for r in rows]


def entries_today() -> list[dict]:
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return entries_since(time.time() - today_start.timestamp())


def entries_this_week() -> list[dict]:
    return entries_since(7 * 86400)


def entries_all() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM entries ORDER BY created_at DESC LIMIT 500"
        ).fetchall()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(entries)").fetchall()]
    return [dict(zip(cols, r)) for r in rows]


def recent(limit: int = 10) -> list[dict]:
    """The latest entries, newest first - the Journal page's list."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM entries ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(entries)").fetchall()]
    return [dict(zip(cols, r)) for r in rows]


def search(text: str, limit: int = 100) -> list[dict]:
    """Entries whose question or answer contains `text` (any case), newest
    first - Home's search box."""
    needle = f"%{text.strip()}%"
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM entries WHERE question LIKE ? OR answer LIKE ? "
            "ORDER BY created_at DESC LIMIT ?", (needle, needle, limit)
        ).fetchall()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(entries)").fetchall()]
    return [dict(zip(cols, r)) for r in rows]


def activity() -> list[tuple[float, str]]:
    """(created_at, tags) for every entry - all of them, for counting:
    Home's totals and streak. Two columns, so it stays cheap at any size."""
    with _connect() as conn:
        return conn.execute("SELECT created_at, COALESCE(tags, '') FROM entries").fetchall()


def due_for_review(limit: int = 5) -> list[dict]:
    """Spaced-repetition: pull entries whose `next_review_at` is in the past."""
    now = time.time()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM entries WHERE next_review_at IS NOT NULL AND "
            "next_review_at <= ? ORDER BY next_review_at ASC LIMIT ?",
            (now, limit),
        ).fetchall()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(entries)").fetchall()]
    return [dict(zip(cols, r)) for r in rows]


def due_count() -> int:
    """How many entries are due for review now."""
    with _connect() as conn:
        (n,) = conn.execute(
            "SELECT COUNT(*) FROM entries WHERE next_review_at IS NOT NULL AND "
            "next_review_at <= ?", (time.time(),),
        ).fetchone()
    return int(n)


def next_due_at() -> Optional[float]:
    """When the next entry comes back for review, if none is due yet."""
    with _connect() as conn:
        (at,) = conn.execute(
            "SELECT MIN(next_review_at) FROM entries WHERE next_review_at > ?",
            (time.time(),),
        ).fetchone()
    return float(at) if at is not None else None


def mark_reviewed(entry_id: int, correct: bool) -> None:
    """Update streak + next review interval."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT streak FROM entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if not row:
            return
        streak = row[0] or 0
        if correct:
            streak += 1
            interval_days = _INTERVALS_DAYS[min(streak, len(_INTERVALS_DAYS) - 1)]
        else:
            streak = 0
            interval_days = 1
        next_review = time.time() + interval_days * 86400
        conn.execute(
            "UPDATE entries SET streak = ?, next_review_at = ? WHERE id = ?",
            (streak, next_review, entry_id),
        )


# ─── Summarisers — used for "what did I learn today" voice replies ────────────

def summarise(entries: list[dict], header: str = "") -> str:
    if not entries:
        return f"{header}Nothing logged yet."
    lines = [header.strip()] if header else []
    for e in entries[:10]:
        when = datetime.fromtimestamp(e["created_at"]).strftime("%I:%M %p")
        q = e["question"][:80]
        if WALKTHROUGH_TAG in (e.get("tags") or ""):
            q += " (walkthrough)"
        lines.append(f"• {when} — {q}")
    if len(entries) > 10:
        lines.append(f"…and {len(entries) - 10} more.")
    return "\n".join(lines)
