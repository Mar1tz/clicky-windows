"""ClientWorkspace: one client's folder, and everything an agent must know about it.

An accounting agent never works "in general": it works for a client, on
a period, against a set of books. That context lives in the client's
folder as `accounting.json` next to the folders the work lands in:

    <Clients root>/ABC Manufacturing/
        accounting.json      client, period, provider, jurisdiction, thresholds, month-end status
        Inbox/               documents waiting to be processed (Inbox/Bank for statements)
        Documents/           filed support: Invoices, Receipts, Statements, Bank, Other
        Reports/  Workpapers/  Audit/  Outbox/ (draft emails)  Exports/ (files for import)
        .tora/               the pack's own data: books, drafts, findings

The task workspace IS this folder, so a tool finds its client from
`ctx.workspace` alone - no global "current client" to go stale.
"""
from __future__ import annotations

import calendar
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Optional

from .jurisdiction import AccountingJurisdictionProfile, profile
from .permissions import ConfidencePolicy

CONFIG = "accounting.json"
FOLDERS = ("Inbox", "Inbox/Bank", "Documents/Invoices", "Documents/Receipts", "Documents/Statements",
           "Documents/Bank", "Documents/Other", "Reports", "Workpapers", "Audit", "Outbox", "Exports",
           ".tora")
PROVIDERS = ("mock", "local", "csv", "xero", "quickbooks", "sage")


@dataclass
class ClientWorkspace:
    root: str
    client: str
    provider: str = "local"
    provider_settings: dict[str, Any] = field(default_factory=dict)
    jurisdiction: str = "generic"
    jurisdiction_overrides: dict[str, Any] = field(default_factory=dict)
    period: str = ""                                   # YYYY-MM being worked on
    bank_accounts: list[str] = field(default_factory=lambda: ["1000"])
    confidence: dict[str, float] = field(default_factory=dict)
    deadlines: list[dict[str, Any]] = field(default_factory=list)      # the accountant's own
    month_end: dict[str, Any] = field(default_factory=dict)            # {period, done, total, status}
    materiality: float = 0.0                           # below this, variances are noise
    notes: str = ""
    created: str = ""

    # -- files ---------------------------------------------------------------------
    @classmethod
    def create(cls, clients_root: str, client: str, *, provider: str = "local",
               jurisdiction: str = "generic", period: str = "", **extra: Any) -> "ClientWorkspace":
        client = client.strip() or "New Client"
        root = os.path.join(clients_root, _safe_folder(client))
        if os.path.isfile(os.path.join(root, CONFIG)):
            return cls.load(root)
        ws = cls(root=root, client=client, provider=provider, jurisdiction=jurisdiction,
                 period=period or date.today().strftime("%Y-%m"),
                 created=datetime.now().isoformat(timespec="seconds"), **extra)
        ws.ensure_folders()
        ws.save()
        return ws

    @classmethod
    def load(cls, root: str) -> "ClientWorkspace":
        path = os.path.join(root, CONFIG)
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        data["root"] = os.path.abspath(root)
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})

    @staticmethod
    def is_client(root: str) -> bool:
        return os.path.isfile(os.path.join(root, CONFIG))

    def save(self) -> None:
        data = asdict(self)
        data.pop("root", None)
        os.makedirs(self.root, exist_ok=True)
        tmp = os.path.join(self.root, CONFIG + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, os.path.join(self.root, CONFIG))

    def ensure_folders(self) -> None:
        for f in FOLDERS:
            os.makedirs(os.path.join(self.root, f), exist_ok=True)

    def path(self, *parts: str) -> str:
        return os.path.join(self.root, *parts)

    @property
    def inbox(self) -> str:
        return self.path("Inbox")

    @property
    def documents(self) -> str:
        return self.path("Documents")

    @property
    def data(self) -> str:
        return self.path(".tora")

    # -- period -----------------------------------------------------------------------
    @property
    def period_start(self) -> str:
        y, m = self._ym()
        return date(y, m, 1).isoformat()

    @property
    def period_end(self) -> str:
        y, m = self._ym()
        return date(y, m, calendar.monthrange(y, m)[1]).isoformat()

    @property
    def prior_period(self) -> str:
        y, m = self._ym()
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        return f"{y}-{m:02d}"

    def period_bounds(self, period: str = "") -> tuple[str, str]:
        y, m = self._ym(period)
        return date(y, m, 1).isoformat(), date(y, m, calendar.monthrange(y, m)[1]).isoformat()

    def _ym(self, period: str = "") -> tuple[int, int]:
        text = (period or self.period or date.today().strftime("%Y-%m")).strip()
        m = re.fullmatch(r"(\d{4})-(\d{1,2})", text)
        if not m:
            raise ValueError(f"period must look like 2026-08, not {text!r}")
        return int(m.group(1)), int(m.group(2))

    @property
    def as_of(self) -> str:
        """The date the period is judged at: its last day (or today, if earlier)."""
        return min(self.period_end, date.today().isoformat()) if self.period_end > date.today().isoformat() \
            else self.period_end

    # -- policy -------------------------------------------------------------------------
    def profile(self) -> AccountingJurisdictionProfile:
        return profile(self.jurisdiction, self.jurisdiction_overrides or None)

    def confidence_policy(self, defaults: Optional[dict[str, Any]] = None) -> ConfidencePolicy:
        return ConfidencePolicy.from_settings({**(defaults or {}), **(self.confidence or {})})

    # -- month-end progress ------------------------------------------------------------------
    def set_month_end(self, period: str, done: list[str], total: int) -> None:
        self.month_end = {"period": period, "done": list(done), "total": int(total),
                          "status": round(len(done) / total, 2) if total else 0.0,
                          "updated": datetime.now().isoformat(timespec="seconds")}
        self.save()

    def context_line(self, provider_name: str = "") -> str:
        prof = self.profile()
        bits = [f"Client: {self.client}", f"Period: {self.period}", prof.currency,
                f"Provider: {provider_name or self.provider}"]
        me = self.month_end or {}
        if me.get("period") == self.period and me.get("total"):
            bits.append(f"Month-end {int(me.get('status', 0) * 100)}%")
        return " · ".join(bits)


def _safe_folder(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', " ", name).strip().rstrip(".")
    return re.sub(r"\s+", " ", cleaned)[:80] or "Client"


def list_clients(clients_root: str) -> list[ClientWorkspace]:
    if not os.path.isdir(clients_root):
        return []
    out = []
    for name in sorted(os.listdir(clients_root)):
        root = os.path.join(clients_root, name)
        if ClientWorkspace.is_client(root):
            try:
                out.append(ClientWorkspace.load(root))
            except (OSError, ValueError, TypeError):
                continue
    return out


def find_client(clients_root: str, name: str) -> Optional[ClientWorkspace]:
    """Exact, then prefix, then word overlap - "ABC" finds "ABC Manufacturing"."""
    want = _norm(name)
    if not want:
        return None
    clients = list_clients(clients_root)
    for c in clients:
        if _norm(c.client) == want:
            return c
    for c in clients:
        if _norm(c.client).startswith(want) or want.startswith(_norm(c.client)):
            return c
    words = set(want.split())
    best, best_score = None, 0
    for c in clients:
        score = len(words & set(_norm(c.client).split()))
        if score > best_score:
            best, best_score = c, score
    return best


def workspace_for_path(path: str) -> Optional[ClientWorkspace]:
    """The client whose folder contains `path` (walks up)."""
    cur = os.path.abspath(path)
    for _ in range(6):
        if ClientWorkspace.is_client(cur):
            return ClientWorkspace.load(cur)
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


_STOP = {"pty", "ltd", "limited", "inc", "llc", "cc", "the", "co", "company", "demo", "(demo)"}


def _norm(name: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split()
    return " ".join(w for w in words if w not in _STOP)
