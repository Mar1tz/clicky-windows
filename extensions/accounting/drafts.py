"""DraftStore: the line between "prepared" and "in the books".

Every accounting agent works in drafts. A draft journal, bill, invoice,
bank categorisation, payment batch or tax return lives here - in the
client's `.tora/drafts.sqlite`, NOT in the provider's books - with the
confidence it was made at and why. Only `post_drafts` (POST level,
always asks) moves a draft into the books, and only
`release_payment_batch` / `submit_tax_return` (SENSITIVE) act on money
or a filing.

That boundary is the pack's; it is the same whether the books are the
demo, a local ledger or (Phase 2) Xero, which is what lets one set of
agents work against any of them.

The store also keeps the client's open exceptions (findings that outlive
the task that raised them, for the dashboard) and which inbox documents
have already been processed, so a folder watch never double-books.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any, Optional

DRAFT_KINDS = ("journal", "bill", "invoice", "bank_line", "match", "payment_batch", "tax_return",
               "reminder", "document_request")
STATUSES = ("draft", "review", "approved", "posted", "rejected", "released", "submitted")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    seq INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, status TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0, title TEXT NOT NULL, payload TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '', source_document TEXT NOT NULL DEFAULT '',
    posted_ref TEXT NOT NULL DEFAULT '', task_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS findings (
    seq INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, key TEXT NOT NULL, title TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '', severity TEXT NOT NULL DEFAULT 'review',
    entity TEXT NOT NULL DEFAULT '', action TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open', note TEXT NOT NULL DEFAULT '', task_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, resolved_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS findings_key ON findings(key, status);
CREATE TABLE IF NOT EXISTS processed (
    digest TEXT PRIMARY KEY, path TEXT NOT NULL, result TEXT NOT NULL, draft_id TEXT NOT NULL DEFAULT '',
    processed_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class DraftStore:
    def __init__(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        self._lock = threading.RLock()

    def close(self) -> None:
        self.db.close()

    # -- drafts ----------------------------------------------------------------------
    def add(self, kind: str, title: str, payload: dict[str, Any], *, confidence: float = 1.0,
            status: str = "draft", reason: str = "", source_document: str = "",
            task_id: str = "") -> dict[str, Any]:
        if kind not in DRAFT_KINDS:
            raise ValueError(f"unknown draft kind {kind!r}")
        if status not in STATUSES:
            raise ValueError(f"unknown draft status {status!r}")
        with self._lock, self.db:
            seq = (self.db.execute("SELECT COALESCE(MAX(seq), 0) FROM drafts").fetchone()[0] or 0) + 1
            did = f"D-{seq:04d}"
            self.db.execute(
                "INSERT INTO drafts(seq, id, kind, status, confidence, title, payload, reason, "
                "source_document, task_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (seq, did, kind, status, float(confidence), title, json.dumps(payload, default=str),
                 reason, source_document, task_id, _now(), _now()))
        return self.get(did)

    def get(self, draft_id: str) -> dict[str, Any]:
        r = self.db.execute("SELECT * FROM drafts WHERE id = ?", (draft_id.strip().upper(),)).fetchone()
        if r is None:
            raise KeyError(f"no draft {draft_id!r}")
        return _draft(r)

    def list(self, *, status: str = "", kind: str = "", limit: int = 200) -> list[dict[str, Any]]:
        sql, params, where = "SELECT * FROM drafts", [], []
        if status:
            statuses = [s.strip() for s in status.split(",") if s.strip()]
            where.append(f"status IN ({','.join('?' * len(statuses))})"); params += statuses
        if kind:
            where.append("kind = ?"); params.append(kind)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY seq DESC LIMIT ?"
        params.append(max(1, int(limit)))
        return [_draft(r) for r in self.db.execute(sql, params).fetchall()]

    def set_status(self, draft_id: str, status: str, *, posted_ref: str = "", reason: str = "") -> dict[str, Any]:
        if status not in STATUSES:
            raise ValueError(f"unknown draft status {status!r}")
        with self._lock, self.db:
            self.db.execute("UPDATE drafts SET status = ?, posted_ref = COALESCE(NULLIF(?, ''), posted_ref), "
                            "reason = COALESCE(NULLIF(?, ''), reason), updated_at = ? WHERE id = ?",
                            (status, posted_ref, reason, _now(), draft_id.strip().upper()))
        return self.get(draft_id)

    def counts(self) -> dict[str, int]:
        return {r["status"]: r["n"] for r in self.db.execute(
            "SELECT status, count(*) n FROM drafts GROUP BY status")}

    # -- findings (the client's open exceptions) ---------------------------------------------
    def add_finding(self, finding: dict[str, Any], *, task_id: str = "") -> tuple[dict[str, Any], bool]:
        """(row, is_new). An open finding with the same title+entity is not
        duplicated - a month-end re-run must not double the review list."""
        key = f"{finding.get('title', '').strip().lower()}|{finding.get('entity', '').strip().lower()}"
        with self._lock, self.db:
            r = self.db.execute("SELECT * FROM findings WHERE key = ? AND status = 'open'", (key,)).fetchone()
            if r is not None:
                return _finding(r), False
            seq = (self.db.execute("SELECT COALESCE(MAX(seq), 0) FROM findings").fetchone()[0] or 0) + 1
            fid = f"X-{seq:04d}"
            self.db.execute(
                "INSERT INTO findings(seq, id, key, title, detail, severity, entity, action, source, task_id, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (seq, fid, key, finding.get("title", ""), finding.get("detail", ""),
                 finding.get("severity", "review"), finding.get("entity", ""), finding.get("action", ""),
                 finding.get("source", ""), task_id, _now()))
        return self.finding(fid), True

    def finding(self, finding_id: str) -> dict[str, Any]:
        r = self.db.execute("SELECT * FROM findings WHERE id = ?", (finding_id.strip().upper(),)).fetchone()
        if r is None:
            raise KeyError(f"no exception {finding_id!r}")
        return _finding(r)

    def findings(self, *, status: str = "open", limit: int = 200) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM findings", []
        if status and status != "all":
            sql += " WHERE status = ?"; params.append(status)
        order = "CASE severity WHEN 'high' THEN 0 WHEN 'review' THEN 1 ELSE 2 END, seq"
        return [_finding(r) for r in self.db.execute(f"{sql} ORDER BY {order} LIMIT ?",
                                                     params + [max(1, int(limit))]).fetchall()]

    def resolve_finding(self, finding_id: str, note: str = "") -> dict[str, Any]:
        with self._lock, self.db:
            self.db.execute("UPDATE findings SET status = 'resolved', note = ?, resolved_at = ? WHERE id = ?",
                            (note, _now(), finding_id.strip().upper()))
        return self.finding(finding_id)

    # -- processed documents ---------------------------------------------------------------------
    @staticmethod
    def digest(path: str) -> str:
        h = hashlib.sha1()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    def is_processed(self, path: str) -> Optional[dict[str, Any]]:
        try:
            d = self.digest(path)
        except OSError:
            return None
        r = self.db.execute("SELECT * FROM processed WHERE digest = ?", (d,)).fetchone()
        return dict(r) if r else None

    def mark_processed(self, path: str, result: str, draft_id: str = "") -> None:
        with self._lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO processed(digest, path, result, draft_id, processed_at) "
                            "VALUES (?, ?, ?, ?, ?)", (self.digest(path), path, result, draft_id, _now()))


def _draft(r: sqlite3.Row) -> dict[str, Any]:
    return {"id": r["id"], "kind": r["kind"], "status": r["status"], "confidence": r["confidence"],
            "title": r["title"], "payload": json.loads(r["payload"]), "reason": r["reason"],
            "source_document": r["source_document"], "posted_ref": r["posted_ref"],
            "task_id": r["task_id"], "created_at": r["created_at"], "updated_at": r["updated_at"]}


def _finding(r: sqlite3.Row) -> dict[str, Any]:
    return {"id": r["id"], "title": r["title"], "detail": r["detail"], "severity": r["severity"],
            "entity": r["entity"], "action": r["action"], "source": r["source"], "status": r["status"],
            "note": r["note"], "created_at": r["created_at"], "resolved_at": r["resolved_at"]}
