"""A small double-entry ledger in SQLite: the books behind LocalBooksProvider.

Why our own and not a spreadsheet: an agent that "keeps the books" in a
CSV will, sooner or later, write a number that does not balance, and
nobody will notice until year end. A ledger with the double-entry
invariant enforced at the insert (every journal's debits equal its
credits, to the penny) cannot drift. The model decides *what* a
transaction is; this module decides whether it is *allowed*.

Money is stored as integer minor units (pence, cents). Floats are for
display only, and every amount that comes in from the model or a bank
CSV goes through `to_minor()`, which rounds HALF_UP like a cashier does.

Conventions (the standard ones):
    asset, expense     — debit increases the balance
    liability, equity,
    income             — credit increases the balance

One file per set of books (`books.sqlite` in the task's workspace by
default). Bank statements are staged into `bank_lines`, matched to
journals or categorised into new ones, and the reconciliation report is
the difference between what the statement says and what the ledger
says — the number an accountant actually wants.

Also holds the side tables a small practice's books need and a ledger
alone does not: contacts (with bank details and when they changed),
purchase orders, payroll runs, and document attachments per journal.

Nothing here imports PyQt6 or anything outside the standard library.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Iterable, Optional

ACCOUNT_TYPES = ("asset", "liability", "equity", "income", "expense")
DEBIT_NORMAL = {"asset", "expense"}
DEFAULT_FILE = "books.sqlite"
TOLERANCE = 0            # minor units - the books balance exactly or not at all


class LedgerError(ValueError):
    """An expected failure the tool reports back to the model."""


# -- money ---------------------------------------------------------------------

_MONEY_JUNK = re.compile(r"[^\d.,\-()]")


def to_minor(value: Any) -> int:
    """'£1,234.50' / 1234.5 / '(12.00)' / '12.00 DR' / '1.234,56' -> minor units, HALF_UP."""
    if isinstance(value, bool):
        raise LedgerError(f"not an amount: {value!r}")
    if isinstance(value, int):
        return value * 100
    if isinstance(value, (float, Decimal)):
        return int(Decimal(str(value)).quantize(Decimal("0.01"), ROUND_HALF_UP) * 100)
    text = str(value).strip()
    if not text:
        raise LedgerError("empty amount")
    upper = text.upper()
    negative = (text.startswith("(") and text.endswith(")")) or upper.endswith("DR")
    cleaned = _MONEY_JUNK.sub("", text).replace("(", "").replace(")", "")
    if cleaned.endswith("-"):
        cleaned, negative = cleaned[:-1], True
    if cleaned.startswith("-"):
        cleaned, negative = cleaned[1:], True
    if "," in cleaned and "." in cleaned:
        # whichever comes last is the decimal separator
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".") if re.fullmatch(r"\d+,\d{1,2}", cleaned) else cleaned.replace(",", "")
    if not cleaned or cleaned.count(".") > 1:
        raise LedgerError(f"not an amount: {value!r}")
    try:
        amount = Decimal(cleaned).quantize(Decimal("0.01"), ROUND_HALF_UP)
    except InvalidOperation:
        raise LedgerError(f"not an amount: {value!r}") from None
    minor = int(amount * 100)
    return -minor if negative else minor


def to_major(minor: int) -> float:
    return round(minor / 100.0, 2)


def fmt(minor: int, currency: str = "") -> str:
    sign = "-" if minor < 0 else ""
    whole, frac = divmod(abs(minor), 100)
    return f"{sign}{currency}{whole:,}.{frac:02d}"


def split_tax(gross: int, rate_pct: float) -> tuple[int, int]:
    """(net, tax) for a tax-inclusive amount at `rate_pct`. net + tax == gross."""
    if not rate_pct:
        return gross, 0
    net = int((Decimal(gross) / (Decimal(1) + Decimal(str(rate_pct)) / 100))
              .quantize(Decimal("1"), ROUND_HALF_UP))
    return net, gross - net


def add_tax(net: int, rate_pct: float) -> int:
    if not rate_pct:
        return 0
    return int((Decimal(net) * Decimal(str(rate_pct)) / 100).quantize(Decimal("1"), ROUND_HALF_UP))


# -- dates ---------------------------------------------------------------------

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%m/%d/%Y", "%d %b %Y",
                 "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%Y/%m/%d", "%d/%m/%y", "%m/%d/%y",
                 "%Y%m%d", "%d-%b-%Y", "%d-%b-%y")


def parse_date(value: Any, *, day_first: bool = True) -> str:
    """Anything a bank or a model writes -> ISO 'YYYY-MM-DD'."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    if not text:
        raise LedgerError("empty date")
    if text.lower() == "today":
        return date.today().isoformat()
    text = re.sub(r"[T ]\d{1,2}:\d{2}.*$", "", text)           # drop a time part
    formats = list(_DATE_FORMATS)
    if not day_first:
        formats.remove("%m/%d/%Y"); formats.insert(1, "%m/%d/%Y")
        formats.remove("%m/%d/%y"); formats.insert(2, "%m/%d/%y")
    for f in formats:
        try:
            return datetime.strptime(text, f).date().isoformat()
        except ValueError:
            continue
    raise LedgerError(f"unreadable date: {value!r}")


def _iso(d: str) -> date:
    return date.fromisoformat(d)


# -- charts of accounts ------------------------------------------------------------

# (code, name, type, taxable-by-default)
_GENERIC_CHART = [
    ("1000", "Bank", "asset", 0),
    ("1010", "Cash", "asset", 0),
    ("1100", "Accounts Receivable", "asset", 0),
    ("1200", "Prepayments", "asset", 0),
    ("1500", "Equipment", "asset", 1),
    ("1600", "Accumulated Depreciation", "asset", 0),
    ("1900", "Suspense", "asset", 0),
    ("2000", "Accounts Payable", "liability", 0),
    ("2100", "Credit Card", "liability", 0),
    ("2200", "Sales Tax Control", "liability", 0),
    ("2300", "Loans", "liability", 0),
    ("2400", "Accruals", "liability", 0),
    ("2500", "Payroll Liabilities", "liability", 0),
    ("3000", "Owner's Capital", "equity", 0),
    ("3100", "Drawings", "equity", 0),
    ("3900", "Retained Earnings", "equity", 0),
    ("4000", "Sales", "income", 1),
    ("4100", "Other Income", "income", 0),
    ("4200", "Interest Income", "income", 0),
    ("5000", "Cost of Sales", "expense", 1),
    ("6000", "Advertising & Marketing", "expense", 1),
    ("6100", "Bank Charges", "expense", 0),
    ("6200", "Insurance", "expense", 0),
    ("6300", "Office Supplies", "expense", 1),
    ("6400", "Professional Fees", "expense", 1),
    ("6500", "Rent", "expense", 0),
    ("6600", "Software & Subscriptions", "expense", 1),
    ("6700", "Travel", "expense", 0),
    ("6800", "Utilities", "expense", 1),
    ("6900", "Telephone & Internet", "expense", 1),
    ("7000", "Wages & Salaries", "expense", 0),
    ("7100", "Meals & Entertainment", "expense", 0),
    ("7200", "Repairs & Maintenance", "expense", 1),
    ("7300", "Training", "expense", 0),
    ("7400", "Depreciation", "expense", 0),
    ("7500", "Motor Expenses", "expense", 1),
    ("7900", "Miscellaneous Expenses", "expense", 0),
]

CHART_PRESETS: dict[str, dict[str, Any]] = {
    "generic": {"currency": "USD", "tax_label": "Sales Tax", "tax_rate": 0.0,
                "fiscal_year_start": "01-01", "renames": {}},
    "uk": {"currency": "GBP", "tax_label": "VAT", "tax_rate": 20.0,
           "fiscal_year_start": "04-06",
           "renames": {"2200": "VAT Control", "1100": "Trade Debtors",
                       "2000": "Trade Creditors", "7100": "Subsistence & Entertaining"}},
    "us": {"currency": "USD", "tax_label": "Sales Tax", "tax_rate": 0.0,
           "fiscal_year_start": "01-01", "renames": {}},
    "eu": {"currency": "EUR", "tax_label": "VAT", "tax_rate": 21.0,
           "fiscal_year_start": "01-01", "renames": {"2200": "VAT Control"}},
}

# Fixed roles some postings need. Codes, so renaming is harmless.
BANK = "1000"
CASH = "1010"
RECEIVABLE = "1100"
SUSPENSE = "1900"
PAYABLE = "2000"
TAX = "2200"
SALES = "4000"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS company (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT NOT NULL, currency TEXT NOT NULL, tax_label TEXT NOT NULL,
    tax_rate REAL NOT NULL, fiscal_year_start TEXT NOT NULL,
    next_invoice INTEGER NOT NULL DEFAULT 1, next_bill INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
    code TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    type TEXT NOT NULL, taxable INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY, date TEXT NOT NULL, description TEXT NOT NULL,
    reference TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'manual',
    tags TEXT NOT NULL DEFAULT '', needs_review INTEGER NOT NULL DEFAULT 0,
    author TEXT NOT NULL DEFAULT 'system', created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS journal_date ON journal(date);
CREATE TABLE IF NOT EXISTS lines (
    id INTEGER PRIMARY KEY, journal_id INTEGER NOT NULL REFERENCES journal(id) ON DELETE CASCADE,
    account TEXT NOT NULL REFERENCES accounts(code),
    debit INTEGER NOT NULL DEFAULT 0, credit INTEGER NOT NULL DEFAULT 0,
    memo TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS lines_account ON lines(account);
CREATE TABLE IF NOT EXISTS bank_lines (
    id INTEGER PRIMARY KEY, account TEXT NOT NULL REFERENCES accounts(code),
    date TEXT NOT NULL, description TEXT NOT NULL, amount INTEGER NOT NULL,
    balance INTEGER, fingerprint TEXT NOT NULL UNIQUE, source_file TEXT NOT NULL DEFAULT '',
    journal_id INTEGER REFERENCES journal(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY, pattern TEXT NOT NULL UNIQUE COLLATE NOCASE,
    account TEXT NOT NULL REFERENCES accounts(code), tax_rate REAL NOT NULL DEFAULT 0,
    hits INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, number TEXT NOT NULL UNIQUE,
    party TEXT NOT NULL, date TEXT NOT NULL, due TEXT NOT NULL,
    subtotal INTEGER NOT NULL, tax INTEGER NOT NULL, total INTEGER NOT NULL,
    paid INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'open',
    journal_id INTEGER REFERENCES journal(id), file_path TEXT NOT NULL DEFAULT '',
    items TEXT NOT NULL DEFAULT '[]', notes TEXT NOT NULL DEFAULT '',
    reference TEXT NOT NULL DEFAULT '', po_number TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY, journal_id INTEGER NOT NULL REFERENCES journal(id) ON DELETE CASCADE,
    path TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'support'
);
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, kind TEXT NOT NULL,
    email TEXT NOT NULL DEFAULT '', bank_account TEXT NOT NULL DEFAULT '',
    tax_id TEXT NOT NULL DEFAULT '', terms_days INTEGER NOT NULL DEFAULT 30,
    default_account TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
    bank_changed_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS purchase_orders (
    id INTEGER PRIMARY KEY, number TEXT NOT NULL UNIQUE, supplier TEXT NOT NULL,
    date TEXT NOT NULL, total INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'open',
    items TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS payroll (
    id INTEGER PRIMARY KEY, period TEXT NOT NULL, employee_id TEXT NOT NULL, name TEXT NOT NULL,
    gross INTEGER NOT NULL, tax INTEGER NOT NULL DEFAULT 0, net INTEGER NOT NULL,
    bank_account TEXT NOT NULL DEFAULT '', UNIQUE(period, employee_id)
);
"""


@dataclass
class Line:
    account: str
    debit: int = 0
    credit: int = 0
    memo: str = ""


@dataclass
class Company:
    name: str
    currency: str
    tax_label: str
    tax_rate: float
    fiscal_year_start: str
    next_invoice: int = 1
    next_bill: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "currency": self.currency, "tax_label": self.tax_label,
                "tax_rate": self.tax_rate, "fiscal_year_start": self.fiscal_year_start}


@dataclass
class ImportReport:
    imported: int = 0
    duplicates: int = 0
    skipped: int = 0
    first_date: str = ""
    last_date: str = ""
    columns: dict[str, str] = field(default_factory=dict)
    auto_matched: int = 0
    auto_categorised: int = 0
    unresolved: int = 0


# -- the ledger ------------------------------------------------------------------

class Ledger:
    """One set of books. Use as a context manager; every public method is
    a single transaction that either commits or raises LedgerError."""

    def __init__(self, path: str) -> None:
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        # Callers serialise access (the provider holds a lock), so one
        # connection may be used from the worker threads tools run on.
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(_SCHEMA)

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "Ledger":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    @staticmethod
    def exists(path: str) -> bool:
        if not os.path.isfile(path):
            return False
        try:
            with sqlite3.connect(path) as db:
                return db.execute("SELECT count(*) FROM company").fetchone()[0] == 1
        except sqlite3.Error:
            return False

    # -- company / chart ---------------------------------------------------------

    @property
    def is_set_up(self) -> bool:
        return self.db.execute("SELECT count(*) FROM company").fetchone()[0] == 1

    def setup(self, name: str, *, preset: str = "generic", currency: str = "",
              tax_rate: Optional[float] = None, fiscal_year_start: str = "",
              tax_label: str = "", renames: Optional[dict[str, str]] = None) -> Company:
        if self.is_set_up:
            raise LedgerError(f"books already set up for {self.company().name}; "
                              "use them, or start a new file")
        p = CHART_PRESETS.get((preset or "generic").lower())
        if p is None:
            raise LedgerError(f"unknown chart preset {preset!r}; one of {', '.join(CHART_PRESETS)}")
        rate = p["tax_rate"] if tax_rate is None else float(tax_rate)
        fy = fiscal_year_start or p["fiscal_year_start"]
        if not re.fullmatch(r"\d{2}-\d{2}", fy):
            raise LedgerError("fiscal_year_start must be 'MM-DD'")
        with self.db:
            self.db.execute(
                "INSERT INTO company(id, name, currency, tax_label, tax_rate, fiscal_year_start, "
                "created_at) VALUES (1, ?, ?, ?, ?, ?, ?)",
                (name.strip() or "My Business", (currency or p["currency"]).upper(),
                 tax_label or p["tax_label"], rate, fy, datetime.now().isoformat(timespec="seconds")))
            names = {**p["renames"], **(renames or {})}
            for code, aname, atype, taxable in _GENERIC_CHART:
                aname = names.get(code, aname)
                self.db.execute("INSERT INTO accounts(code, name, type, taxable) VALUES (?, ?, ?, ?)",
                                (code, aname, atype, taxable))
        return self.company()

    def company(self) -> Company:
        row = self.db.execute("SELECT * FROM company WHERE id = 1").fetchone()
        if row is None:
            raise LedgerError("no books here yet - call ledger_setup first")
        return Company(row["name"], row["currency"], row["tax_label"], row["tax_rate"],
                       row["fiscal_year_start"], row["next_invoice"], row["next_bill"])

    def accounts(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT a.code, a.name, a.type, a.taxable, a.archived, "
            "COALESCE(SUM(l.debit), 0) AS dr, COALESCE(SUM(l.credit), 0) AS cr "
            "FROM accounts a LEFT JOIN lines l ON l.account = a.code "
            "GROUP BY a.code ORDER BY a.code").fetchall()
        out = []
        for r in rows:
            if r["archived"] and not include_archived:
                continue
            out.append({"code": r["code"], "name": r["name"], "type": r["type"],
                        "taxable": bool(r["taxable"]), "balance": _balance(r["type"], r["dr"], r["cr"])})
        return out

    def add_account(self, code: str, name: str, type_: str, *, taxable: bool = False) -> dict[str, Any]:
        type_ = (type_ or "").lower().strip()
        if type_ not in ACCOUNT_TYPES:
            raise LedgerError(f"account type must be one of {', '.join(ACCOUNT_TYPES)}")
        code = str(code).strip()
        if not code or not name.strip():
            raise LedgerError("code and name are required")
        try:
            with self.db:
                self.db.execute("INSERT INTO accounts(code, name, type, taxable) VALUES (?, ?, ?, ?)",
                                (code, name.strip(), type_, int(taxable)))
        except sqlite3.IntegrityError:
            raise LedgerError(f"an account with code {code} or name {name!r} already exists") from None
        return {"code": code, "name": name.strip(), "type": type_}

    def resolve_account(self, ref: Any) -> str:
        """Code for a code, an exact name, or a unique name fragment."""
        text = str(ref or "").strip()
        if not text:
            raise LedgerError("account is required")
        row = self.db.execute("SELECT code FROM accounts WHERE code = ? OR name = ? COLLATE NOCASE",
                              (text, text)).fetchone()
        if row:
            return row["code"]
        rows = self.db.execute("SELECT code, name FROM accounts WHERE archived = 0 AND "
                               "name LIKE ? COLLATE NOCASE", (f"%{text}%",)).fetchall()
        if len(rows) == 1:
            return rows[0]["code"]
        if not rows:
            names = ", ".join(f"{a['code']} {a['name']}" for a in self.accounts()[:40])
            raise LedgerError(f"no account matches {text!r}. Accounts: {names}")
        raise LedgerError(f"{text!r} is ambiguous: " + ", ".join(f"{r['code']} {r['name']}" for r in rows))

    def account_type(self, code: str) -> str:
        row = self.db.execute("SELECT type FROM accounts WHERE code = ?", (code,)).fetchone()
        if row is None:
            raise LedgerError(f"no account {code}")
        return row["type"]

    def account_name(self, code: str) -> str:
        row = self.db.execute("SELECT name FROM accounts WHERE code = ?", (code,)).fetchone()
        return row["name"] if row else code

    # -- posting ---------------------------------------------------------------

    def post(self, when: Any, description: str, lines: Iterable[Line | dict[str, Any]], *,
             reference: str = "", source: str = "manual", tags: Iterable[str] = (),
             needs_review: bool = False, author: str = "system",
             created_at: str = "") -> int:
        """Post a balanced journal. Raises LedgerError if it does not balance."""
        when = parse_date(when)
        description = (description or "").strip()
        if not description:
            raise LedgerError("description is required")
        norm: list[Line] = []
        for raw in lines:
            ln = raw if isinstance(raw, Line) else Line(
                account=str(raw.get("account", "")), debit=_amt(raw.get("debit")),
                credit=_amt(raw.get("credit")), memo=str(raw.get("memo") or ""))
            if ln.debit < 0 or ln.credit < 0:
                raise LedgerError("debit and credit must be positive; swap the side instead")
            if ln.debit and ln.credit:
                raise LedgerError("a line is either a debit or a credit, not both")
            if not ln.debit and not ln.credit:
                continue
            ln.account = self.resolve_account(ln.account)
            norm.append(ln)
        if len(norm) < 2:
            raise LedgerError("a journal needs at least two lines (a debit and a credit)")
        dr, cr = sum(l.debit for l in norm), sum(l.credit for l in norm)
        if abs(dr - cr) > TOLERANCE:
            raise LedgerError(f"journal does not balance: debits {fmt(dr)} != credits {fmt(cr)}")
        if reference:
            dup = self.db.execute("SELECT id FROM journal WHERE reference = ? AND reference != ''",
                                  (reference,)).fetchone()
            if dup:
                raise LedgerError(f"reference {reference!r} already posted as journal #{dup['id']}")
        with self.db:
            cur = self.db.execute(
                "INSERT INTO journal(date, description, reference, source, tags, needs_review, "
                "author, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (when, description, reference or "", source, ",".join(t.strip() for t in tags if t),
                 int(needs_review), author or "system",
                 created_at or datetime.now().isoformat(timespec="seconds")))
            jid = int(cur.lastrowid)
            self.db.executemany(
                "INSERT INTO lines(journal_id, account, debit, credit, memo) VALUES (?, ?, ?, ?, ?)",
                [(jid, l.account, l.debit, l.credit, l.memo) for l in norm])
        return jid

    def expense(self, when: Any, description: str, gross: Any, category: Any, *,
                paid_from: Any = BANK, tax_rate: Optional[float] = None, reference: str = "",
                source: str = "manual", tags: Iterable[str] = (), needs_review: bool = False) -> int:
        """Money out: Dr category (net) + Dr tax control, Cr paid_from (gross)."""
        gross_m = _amt(gross)
        if gross_m <= 0:
            raise LedgerError("amount must be positive (for a refund, record income)")
        cat = self.resolve_account(category)
        rate = self._rate_for(cat, tax_rate)
        net, tax = split_tax(gross_m, rate)
        lines = [Line(cat, debit=net)]
        if tax:
            lines.append(Line(TAX, debit=tax, memo=f"{rate:g}%"))
        lines.append(Line(self.resolve_account(paid_from), credit=gross_m))
        return self.post(when, description, lines, reference=reference, source=source,
                         tags=tags, needs_review=needs_review)

    def income(self, when: Any, description: str, gross: Any, category: Any = SALES, *,
               received_in: Any = BANK, tax_rate: Optional[float] = None, reference: str = "",
               source: str = "manual", tags: Iterable[str] = (), needs_review: bool = False) -> int:
        """Money in: Dr received_in (gross), Cr category (net) + Cr tax control."""
        gross_m = _amt(gross)
        if gross_m <= 0:
            raise LedgerError("amount must be positive")
        cat = self.resolve_account(category)
        rate = self._rate_for(cat, tax_rate)
        net, tax = split_tax(gross_m, rate)
        lines = [Line(self.resolve_account(received_in), debit=gross_m), Line(cat, credit=net)]
        if tax:
            lines.append(Line(TAX, credit=tax, memo=f"{rate:g}%"))
        return self.post(when, description, lines, reference=reference, source=source,
                         tags=tags, needs_review=needs_review)

    def transfer(self, when: Any, description: str, amount: Any, from_account: Any,
                 to_account: Any, *, reference: str = "") -> int:
        amt = _amt(amount)
        if amt <= 0:
            raise LedgerError("amount must be positive")
        return self.post(when, description,
                         [Line(self.resolve_account(to_account), debit=amt),
                          Line(self.resolve_account(from_account), credit=amt)],
                         reference=reference, source="transfer")

    def void(self, journal_id: int, reason: str = "") -> None:
        row = self.db.execute("SELECT id FROM journal WHERE id = ?", (journal_id,)).fetchone()
        if row is None:
            raise LedgerError(f"no journal #{journal_id}")
        with self.db:
            self.db.execute("UPDATE bank_lines SET journal_id = NULL WHERE journal_id = ?", (journal_id,))
            self.db.execute("UPDATE documents SET journal_id = NULL WHERE journal_id = ?", (journal_id,))
            self.db.execute("DELETE FROM journal WHERE id = ?", (journal_id,))

    def _rate_for(self, code: str, tax_rate: Optional[float]) -> float:
        if tax_rate is not None:
            return float(tax_rate)
        row = self.db.execute("SELECT taxable FROM accounts WHERE code = ?", (code,)).fetchone()
        return self.company().tax_rate if row and row["taxable"] else 0.0

    # -- reading ---------------------------------------------------------------------

    def journal(self, journal_id: int) -> dict[str, Any]:
        j = self.db.execute("SELECT * FROM journal WHERE id = ?", (journal_id,)).fetchone()
        if j is None:
            raise LedgerError(f"no journal #{journal_id}")
        lines = self.db.execute("SELECT * FROM lines WHERE journal_id = ? ORDER BY id", (journal_id,)).fetchall()
        return {
            "id": j["id"], "date": j["date"], "description": j["description"],
            "reference": j["reference"], "source": j["source"], "needs_review": bool(j["needs_review"]),
            "author": j["author"], "created_at": j["created_at"],
            "attachments": self.attachments(journal_id),
            "tags": [t for t in j["tags"].split(",") if t],
            "lines": [{"account": l["account"], "name": self.account_name(l["account"]),
                       "debit": to_major(l["debit"]), "credit": to_major(l["credit"]), "memo": l["memo"]}
                      for l in lines],
        }

    def transactions(self, *, start: Any = None, end: Any = None, account: Any = None,
                     text: str = "", needs_review: Optional[bool] = None,
                     limit: int = 200) -> list[dict[str, Any]]:
        where, params = [], []
        if start:
            where.append("j.date >= ?"); params.append(parse_date(start))
        if end:
            where.append("j.date <= ?"); params.append(parse_date(end))
        if account:
            where.append("j.id IN (SELECT journal_id FROM lines WHERE account = ?)")
            params.append(self.resolve_account(account))
        if text:
            where.append("(j.description LIKE ? OR j.reference LIKE ? OR j.tags LIKE ?)")
            params += [f"%{text}%"] * 3
        if needs_review is not None:
            where.append("j.needs_review = ?"); params.append(int(needs_review))
        sql = "SELECT j.id FROM journal j" + (" WHERE " + " AND ".join(where) if where else "") \
              + " ORDER BY j.date DESC, j.id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        return [self.journal(r["id"]) for r in self.db.execute(sql, params).fetchall()]

    def balance(self, account: Any, *, as_of: Any = None, start: Any = None) -> int:
        code = self.resolve_account(account)
        sql = ("SELECT COALESCE(SUM(l.debit), 0) dr, COALESCE(SUM(l.credit), 0) cr FROM lines l "
               "JOIN journal j ON j.id = l.journal_id WHERE l.account = ?")
        params: list[Any] = [code]
        if start:
            sql += " AND j.date >= ?"; params.append(parse_date(start))
        if as_of:
            sql += " AND j.date <= ?"; params.append(parse_date(as_of))
        r = self.db.execute(sql, params).fetchone()
        return _balance(self.account_type(code), r["dr"], r["cr"])

    # -- reports ---------------------------------------------------------------------

    def trial_balance(self, *, as_of: Any = None) -> dict[str, Any]:
        rows = []
        total_dr = total_cr = 0
        for a in self.accounts():
            bal = self.balance(a["code"], as_of=as_of)
            if bal == 0:
                continue
            debit_side = (a["type"] in DEBIT_NORMAL) == (bal > 0)
            dr, cr = (abs(bal), 0) if debit_side else (0, abs(bal))
            total_dr += dr; total_cr += cr
            rows.append({"code": a["code"], "name": a["name"], "type": a["type"],
                         "debit": to_major(dr), "credit": to_major(cr)})
        return {"as_of": parse_date(as_of) if as_of else date.today().isoformat(), "rows": rows,
                "total_debit": to_major(total_dr), "total_credit": to_major(total_cr),
                "balanced": total_dr == total_cr}

    def profit_and_loss(self, *, start: Any = None, end: Any = None) -> dict[str, Any]:
        start = parse_date(start) if start else self.fiscal_year_start_for(date.today()).isoformat()
        end = parse_date(end) if end else date.today().isoformat()
        income, expenses = [], []
        for a in self.accounts():
            if a["type"] not in ("income", "expense"):
                continue
            bal = self.balance(a["code"], start=start, as_of=end)
            if bal == 0:
                continue
            (income if a["type"] == "income" else expenses).append(
                {"code": a["code"], "name": a["name"], "amount": to_major(bal)})
        ti = sum(to_minor(x["amount"]) for x in income)
        te = sum(to_minor(x["amount"]) for x in expenses)
        return {"start": start, "end": end, "income": income, "expenses": expenses,
                "total_income": to_major(ti), "total_expenses": to_major(te),
                "net_profit": to_major(ti - te)}

    def balance_sheet(self, *, as_of: Any = None) -> dict[str, Any]:
        as_of = parse_date(as_of) if as_of else date.today().isoformat()
        groups: dict[str, list[dict[str, Any]]] = {"asset": [], "liability": [], "equity": []}
        earnings = 0
        for a in self.accounts():
            bal = self.balance(a["code"], as_of=as_of)
            if a["type"] in groups:
                if bal:
                    groups[a["type"]].append({"code": a["code"], "name": a["name"], "amount": to_major(bal)})
            elif a["type"] == "income":
                earnings += bal
            else:
                earnings -= bal
        assets = sum(to_minor(x["amount"]) for x in groups["asset"])
        liabilities = sum(to_minor(x["amount"]) for x in groups["liability"])
        equity = sum(to_minor(x["amount"]) for x in groups["equity"])
        return {"as_of": as_of, "assets": groups["asset"], "liabilities": groups["liability"],
                "equity": groups["equity"] + [{"code": "", "name": "Current earnings", "amount": to_major(earnings)}],
                "total_assets": to_major(assets), "total_liabilities": to_major(liabilities),
                "total_equity": to_major(equity + earnings),
                "balanced": assets == liabilities + equity + earnings}

    def tax_summary(self, *, start: Any = None, end: Any = None) -> dict[str, Any]:
        """Output tax (on sales) vs input tax (on purchases) from the control
        account: credits are tax collected, debits are tax paid."""
        start = parse_date(start) if start else self.fiscal_year_start_for(date.today()).isoformat()
        end = parse_date(end) if end else date.today().isoformat()
        r = self.db.execute(
            "SELECT COALESCE(SUM(l.credit), 0) out_tax, COALESCE(SUM(l.debit), 0) in_tax "
            "FROM lines l JOIN journal j ON j.id = l.journal_id "
            "WHERE l.account = ? AND j.date BETWEEN ? AND ?", (TAX, start, end)).fetchone()
        sales = self.db.execute(
            "SELECT COALESCE(SUM(l.credit - l.debit), 0) FROM lines l JOIN journal j ON j.id = l.journal_id "
            "JOIN accounts a ON a.code = l.account WHERE a.type = 'income' AND j.date BETWEEN ? AND ?",
            (start, end)).fetchone()[0]
        purchases = self.db.execute(
            "SELECT COALESCE(SUM(l.debit - l.credit), 0) FROM lines l JOIN journal j ON j.id = l.journal_id "
            "JOIN accounts a ON a.code = l.account WHERE a.type = 'expense' AND j.date BETWEEN ? AND ?",
            (start, end)).fetchone()[0]
        c = self.company()
        return {"start": start, "end": end, "label": c.tax_label, "rate": c.tax_rate,
                "sales_net": to_major(sales), "purchases_net": to_major(purchases),
                "tax_on_sales": to_major(r["out_tax"]), "tax_on_purchases": to_major(r["in_tax"]),
                "net_due": to_major(r["out_tax"] - r["in_tax"])}

    def fiscal_year_start_for(self, on: date) -> date:
        mm, dd = (int(x) for x in self.company().fiscal_year_start.split("-"))
        start = date(on.year, mm, dd)
        return start if start <= on else date(on.year - 1, mm, dd)

    def summary(self) -> dict[str, Any]:
        """The one-paragraph state of the books, for prompts."""
        c = self.company()
        n_j = self.db.execute("SELECT count(*) FROM journal").fetchone()[0]
        last = self.db.execute("SELECT max(date) FROM journal").fetchone()[0]
        review = self.db.execute("SELECT count(*) FROM journal WHERE needs_review = 1").fetchone()[0]
        unrec = self.db.execute("SELECT count(*) FROM bank_lines WHERE journal_id IS NULL").fetchone()[0]
        open_inv = self.db.execute("SELECT count(*), COALESCE(SUM(total - paid), 0) FROM documents "
                                   "WHERE kind = 'invoice' AND status = 'open'").fetchone()
        open_bills = self.db.execute("SELECT count(*), COALESCE(SUM(total - paid), 0) FROM documents "
                                     "WHERE kind = 'bill' AND status = 'open'").fetchone()
        return {"company": c.name, "currency": c.currency, "tax_label": c.tax_label,
                "tax_rate": c.tax_rate, "fiscal_year_start": c.fiscal_year_start,
                "journals": n_j, "last_entry": last or "", "needs_review": review,
                "unreconciled_bank_lines": unrec, "open_invoices": open_inv[0],
                "receivable": to_major(open_inv[1]), "open_bills": open_bills[0],
                "payable": to_major(open_bills[1]), "bank_balance": to_major(self.balance(BANK))}

    # -- bank statements -----------------------------------------------------------------

    def import_statement(self, rows: list[dict[str, Any]], *, account: Any = BANK,
                         source_file: str = "", day_first: bool = True,
                         auto_match: bool = True, auto_categorise: bool = True) -> ImportReport:
        """Stage statement rows (already read from CSV/XLSX) into bank_lines,
        skipping exact duplicates, then (optionally) auto-match and
        auto-categorise. The accounting pack turns both off: under its
        permission model an import is a DRAFT action, and matching or
        categorising changes the books, which is POST."""
        code = self.resolve_account(account)
        if not rows:
            raise LedgerError("the statement has no rows")
        cols = detect_columns(list(rows[0].keys()), rows)
        report = ImportReport(columns=cols)
        if "date" not in cols or ("amount" not in cols and "debit" not in cols and "credit" not in cols):
            raise LedgerError(f"could not find date/amount columns in {list(rows[0].keys())}; "
                              "say which columns hold the date, description and amount")
        dates: list[str] = []
        with self.db:
            for raw in rows:
                try:
                    when = parse_date(raw.get(cols["date"]), day_first=day_first)
                    amount = _row_amount(raw, cols)
                except LedgerError:
                    report.skipped += 1
                    continue
                if amount == 0:
                    report.skipped += 1
                    continue
                desc = " ".join(str(raw.get(cols[k]) or "").strip()
                                for k in ("description", "reference") if k in cols).strip() or "(no description)"
                balance = None
                if "balance" in cols:
                    try:
                        balance = to_minor(raw.get(cols["balance"]))
                    except LedgerError:
                        balance = None
                fp = hashlib.sha1(f"{code}|{when}|{desc.lower()}|{amount}|{balance}".encode()).hexdigest()
                try:
                    self.db.execute(
                        "INSERT INTO bank_lines(account, date, description, amount, balance, fingerprint, "
                        "source_file) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (code, when, desc, amount, balance, fp, source_file))
                    report.imported += 1
                    dates.append(when)
                except sqlite3.IntegrityError:
                    report.duplicates += 1
        if dates:
            report.first_date, report.last_date = min(dates), max(dates)
        report.auto_matched = self.auto_match(code) if auto_match else 0
        report.auto_categorised = self.apply_rules(code) if auto_categorise else 0
        report.unresolved = self.db.execute(
            "SELECT count(*) FROM bank_lines WHERE account = ? AND journal_id IS NULL", (code,)).fetchone()[0]
        return report

    def unreconciled(self, account: Any = BANK, *, limit: int = 100) -> list[dict[str, Any]]:
        code = self.resolve_account(account)
        rows = self.db.execute(
            "SELECT * FROM bank_lines WHERE account = ? AND journal_id IS NULL ORDER BY date, id LIMIT ?",
            (code, max(1, int(limit)))).fetchall()
        return [{"id": r["id"], "date": r["date"], "description": r["description"],
                 "amount": to_major(r["amount"]),
                 "suggested": self.suggest(r["description"])} for r in rows]

    def suggest(self, description: str) -> str:
        """Account a learnt rule would give this description, or ''."""
        for r in self.db.execute("SELECT pattern, account FROM rules ORDER BY length(pattern) DESC"):
            if r["pattern"].lower() in description.lower():
                return r["account"]
        return ""

    def auto_match(self, account: str, *, window_days: int = 3) -> int:
        """Link bank lines to journals already posted on that account with
        the same amount within a few days (an invoice payment recorded
        before the statement arrived, a transfer posted from the other side)."""
        matched = 0
        used = {r[0] for r in self.db.execute("SELECT journal_id FROM bank_lines WHERE journal_id IS NOT NULL")}
        for bl in self.db.execute("SELECT * FROM bank_lines WHERE account = ? AND journal_id IS NULL",
                                  (account,)).fetchall():
            amount = bl["amount"]
            side = "debit" if amount > 0 else "credit"
            lo = (_iso(bl["date"]) - timedelta(days=window_days)).isoformat()
            hi = (_iso(bl["date"]) + timedelta(days=window_days)).isoformat()
            cands = self.db.execute(
                f"SELECT j.id FROM journal j JOIN lines l ON l.journal_id = j.id "
                f"WHERE l.account = ? AND l.{side} = ? AND j.date BETWEEN ? AND ? ORDER BY j.date",
                (account, abs(amount), lo, hi)).fetchall()
            for c in cands:
                if c["id"] not in used:
                    with self.db:
                        self.db.execute("UPDATE bank_lines SET journal_id = ? WHERE id = ?", (c["id"], bl["id"]))
                    used.add(c["id"])
                    matched += 1
                    break
        return matched

    def apply_rules(self, account: Any = BANK) -> int:
        """Categorise unmatched bank lines whose description contains a
        learnt pattern. Returns how many journals were posted."""
        code = self.resolve_account(account)
        rules = self.db.execute("SELECT * FROM rules ORDER BY length(pattern) DESC").fetchall()
        if not rules:
            return 0
        posted = 0
        for bl in self.db.execute("SELECT * FROM bank_lines WHERE account = ? AND journal_id IS NULL",
                                  (code,)).fetchall():
            for r in rules:
                if r["pattern"].lower() in bl["description"].lower():
                    self._categorise_line(bl, r["account"], r["tax_rate"], code)
                    with self.db:
                        self.db.execute("UPDATE rules SET hits = hits + 1 WHERE id = ?", (r["id"],))
                    posted += 1
                    break
        return posted

    def categorise(self, line_id: int, category: Any, *, tax_rate: Optional[float] = None,
                   learn_pattern: str = "", needs_review: bool = False) -> int:
        bl = self.db.execute("SELECT * FROM bank_lines WHERE id = ?", (line_id,)).fetchone()
        if bl is None:
            raise LedgerError(f"no bank line #{line_id}")
        if bl["journal_id"]:
            raise LedgerError(f"bank line #{line_id} is already reconciled to journal #{bl['journal_id']}")
        cat = self.resolve_account(category)
        jid = self._categorise_line(bl, cat, tax_rate, bl["account"], needs_review=needs_review)
        if learn_pattern.strip():
            self.learn(learn_pattern, cat, tax_rate=tax_rate)
        return jid

    def _categorise_line(self, bl: sqlite3.Row, cat: str, tax_rate: Optional[float],
                         bank: str, *, needs_review: bool = False) -> int:
        amount = bl["amount"]
        rate = self._rate_for(cat, tax_rate)
        if amount < 0:
            jid = self.expense(bl["date"], bl["description"], -amount, cat, paid_from=bank,
                               tax_rate=rate, source="bank", needs_review=needs_review)
        else:
            jid = self.income(bl["date"], bl["description"], amount, cat, received_in=bank,
                              tax_rate=rate, source="bank", needs_review=needs_review)
        with self.db:
            self.db.execute("UPDATE bank_lines SET journal_id = ? WHERE id = ?", (jid, bl["id"]))
        return jid

    def match(self, line_id: int, journal_id: int) -> None:
        bl = self.db.execute("SELECT * FROM bank_lines WHERE id = ?", (line_id,)).fetchone()
        if bl is None:
            raise LedgerError(f"no bank line #{line_id}")
        j = self.journal(journal_id)
        side = "debit" if bl["amount"] > 0 else "credit"
        if not any(l["account"] == bl["account"] and to_minor(l[side]) == abs(bl["amount"]) for l in j["lines"]):
            raise LedgerError(f"journal #{journal_id} has no {side} of {fmt(abs(bl['amount']))} on "
                              f"{self.account_name(bl['account'])}")
        with self.db:
            self.db.execute("UPDATE bank_lines SET journal_id = ? WHERE id = ?", (journal_id, line_id))

    def learn(self, pattern: str, category: Any, *, tax_rate: Optional[float] = None) -> dict[str, Any]:
        pattern = pattern.strip()
        if len(pattern) < 3:
            raise LedgerError("a rule pattern needs at least 3 characters")
        cat = self.resolve_account(category)
        rate = self._rate_for(cat, tax_rate)
        with self.db:
            self.db.execute("INSERT INTO rules(pattern, account, tax_rate) VALUES (?, ?, ?) "
                            "ON CONFLICT(pattern) DO UPDATE SET account = excluded.account, "
                            "tax_rate = excluded.tax_rate", (pattern, cat, rate))
        return {"pattern": pattern, "account": cat, "name": self.account_name(cat), "tax_rate": rate}

    def rules(self) -> list[dict[str, Any]]:
        return [{"pattern": r["pattern"], "account": r["account"], "name": self.account_name(r["account"]),
                 "tax_rate": r["tax_rate"], "hits": r["hits"]}
                for r in self.db.execute("SELECT * FROM rules ORDER BY hits DESC, pattern")]

    def reconcile(self, account: Any = BANK, *, statement_balance: Any = None,
                  as_of: Any = None) -> dict[str, Any]:
        code = self.resolve_account(account)
        as_of = parse_date(as_of) if as_of else date.today().isoformat()
        ledger_bal = self.balance(code, as_of=as_of)
        n_lines = self.db.execute("SELECT count(*) FROM bank_lines WHERE account = ? AND date <= ?",
                                  (code, as_of)).fetchone()[0]
        n_matched = self.db.execute("SELECT count(*) FROM bank_lines WHERE account = ? AND date <= ? "
                                    "AND journal_id IS NOT NULL", (code, as_of)).fetchone()[0]
        unmatched = [u for u in self.unreconciled(code, limit=500) if u["date"] <= as_of]
        unmatched_total = sum(to_minor(u["amount"]) for u in unmatched)
        out: dict[str, Any] = {
            "account": code, "name": self.account_name(code), "as_of": as_of,
            "ledger_balance": to_major(ledger_bal), "statement_lines": n_lines,
            "matched": n_matched, "unmatched": unmatched[:50], "unmatched_count": len(unmatched),
            "unmatched_total": to_major(unmatched_total),
        }
        if statement_balance is not None:
            stmt = _amt(statement_balance)
            out["statement_balance"] = to_major(stmt)
            out["difference"] = to_major(stmt - ledger_bal)
            out["explained_by_unmatched"] = (stmt - ledger_bal) == unmatched_total
            out["reconciled"] = stmt == ledger_bal and not unmatched
        else:
            out["reconciled"] = not unmatched
        return out

    # -- invoices and bills ------------------------------------------------------------

    def create_document(self, kind: str, party: str, items: list[dict[str, Any]], *,
                        when: Any = None, due_days: int = 30, number: str = "",
                        notes: str = "", tax_rate: Optional[float] = None,
                        category: Any = None, reference: str = "", po_number: str = "",
                        file_path: str = "") -> dict[str, Any]:
        """An invoice (kind='invoice', posts Dr receivable / Cr sales+tax) or a
        bill (kind='bill', posts Dr expense+tax / Cr payable). Items carry
        description, quantity, unit_price and optionally tax_rate/account."""
        kind = kind.lower().strip()
        if kind not in ("invoice", "bill"):
            raise LedgerError("kind must be 'invoice' or 'bill'")
        party = (party or "").strip()
        if not party:
            raise LedgerError("customer/vendor name is required")
        if not items:
            raise LedgerError("at least one line item is required")
        c = self.company()
        when = parse_date(when) if when else date.today().isoformat()
        due = (_iso(when) + timedelta(days=max(0, int(due_days)))).isoformat()
        default_rate = c.tax_rate if tax_rate is None else float(tax_rate)
        default_cat = self.resolve_account(category) if category else (SALES if kind == "invoice" else "7900")
        norm_items, subtotal, tax_total = [], 0, 0
        by_account: dict[str, int] = {}
        for it in items:
            desc = str(it.get("description") or "").strip()
            if not desc:
                raise LedgerError("every item needs a description")
            qty = Decimal(str(it.get("quantity", 1) or 1))
            unit = _amt(it.get("unit_price", it.get("amount", 0)))
            net = int((qty * unit).quantize(Decimal("1"), ROUND_HALF_UP))
            rate = float(it["tax_rate"]) if it.get("tax_rate") is not None else default_rate
            tax = add_tax(net, rate)
            acct = self.resolve_account(it["account"]) if it.get("account") else default_cat
            by_account[acct] = by_account.get(acct, 0) + net
            subtotal += net; tax_total += tax
            norm_items.append({"description": desc, "quantity": float(qty), "unit_price": to_major(unit),
                               "net": to_major(net), "tax_rate": rate, "tax": to_major(tax),
                               "total": to_major(net + tax), "account": acct})
        total = subtotal + tax_total
        if not number:
            seq = c.next_invoice if kind == "invoice" else c.next_bill
            number = f"{'INV' if kind == 'invoice' else 'BILL'}-{seq:04d}"
        if self.db.execute("SELECT 1 FROM documents WHERE number = ?", (number,)).fetchone():
            raise LedgerError(f"{number} already exists")
        if kind == "invoice":
            lines = [Line(RECEIVABLE, debit=total, memo=party)]
            lines += [Line(a, credit=n) for a, n in by_account.items()]
            if tax_total:
                lines.append(Line(TAX, credit=tax_total))
        else:
            lines = [Line(a, debit=n) for a, n in by_account.items()]
            if tax_total:
                lines.append(Line(TAX, debit=tax_total))
            lines.append(Line(PAYABLE, credit=total, memo=party))
        jid = self.post(when, f"{'Invoice' if kind == 'invoice' else 'Bill'} {number} - {party}", lines,
                        reference=number, source=kind, tags=[party])
        with self.db:
            self.db.execute(
                "INSERT INTO documents(kind, number, party, date, due, subtotal, tax, total, journal_id, "
                "items, notes, reference, po_number, file_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (kind, number, party, when, due, subtotal, tax_total, total, jid,
                 json.dumps(norm_items), notes or "", reference or "", po_number or "", file_path or ""))
            if file_path:
                self.db.execute("INSERT INTO attachments(journal_id, path, kind) VALUES (?, ?, ?)",
                                (jid, file_path, kind))
            col = "next_invoice" if kind == "invoice" else "next_bill"
            self.db.execute(f"UPDATE company SET {col} = {col} + 1 WHERE id = 1")
        return self.document(number)

    def document(self, number: str) -> dict[str, Any]:
        r = self.db.execute("SELECT * FROM documents WHERE number = ?", (number,)).fetchone()
        if r is None:
            raise LedgerError(f"no invoice or bill {number!r}")
        return {"kind": r["kind"], "number": r["number"], "party": r["party"], "date": r["date"],
                "due": r["due"], "subtotal": to_major(r["subtotal"]), "tax": to_major(r["tax"]),
                "total": to_major(r["total"]), "paid": to_major(r["paid"]),
                "outstanding": to_major(r["total"] - r["paid"]), "status": r["status"],
                "journal_id": r["journal_id"], "file_path": r["file_path"],
                "items": json.loads(r["items"]), "notes": r["notes"],
                "reference": r["reference"], "po_number": r["po_number"]}

    def set_document_file(self, number: str, path: str) -> None:
        with self.db:
            self.db.execute("UPDATE documents SET file_path = ? WHERE number = ?", (path, number))

    def pay_document(self, number: str, amount: Any = None, *, when: Any = None,
                     account: Any = BANK, reference: str = "") -> dict[str, Any]:
        """Receipt against an invoice (Dr bank / Cr receivable) or payment of
        a bill (Dr payable / Cr bank). Default amount: whatever is outstanding."""
        d = self.db.execute("SELECT * FROM documents WHERE number = ?", (number,)).fetchone()
        if d is None:
            raise LedgerError(f"no invoice or bill {number!r}")
        if d["status"] == "void":
            raise LedgerError(f"{number} is void")
        outstanding = d["total"] - d["paid"]
        amt = outstanding if amount is None else _amt(amount)
        if amt <= 0:
            raise LedgerError(f"{number} has nothing outstanding" if outstanding <= 0 else "amount must be positive")
        if amt > outstanding:
            raise LedgerError(f"{fmt(amt)} is more than the {fmt(outstanding)} outstanding on {number}")
        bank = self.resolve_account(account)
        when = parse_date(when) if when else date.today().isoformat()
        if d["kind"] == "invoice":
            lines = [Line(bank, debit=amt), Line(RECEIVABLE, credit=amt, memo=d["party"])]
            desc = f"Payment received {number} - {d['party']}"
        else:
            lines = [Line(PAYABLE, debit=amt, memo=d["party"]), Line(bank, credit=amt)]
            desc = f"Payment made {number} - {d['party']}"
        jid = self.post(when, desc, lines, reference=reference or f"{number}/pay/{d['paid'] + amt}",
                        source="payment")
        paid = d["paid"] + amt
        with self.db:
            self.db.execute("UPDATE documents SET paid = ?, status = ? WHERE number = ?",
                            (paid, "paid" if paid >= d["total"] else "open", number))
        out = self.document(number)
        out["payment_journal_id"] = jid
        return out

    def void_document(self, number: str) -> None:
        d = self.db.execute("SELECT * FROM documents WHERE number = ?", (number,)).fetchone()
        if d is None:
            raise LedgerError(f"no invoice or bill {number!r}")
        if d["paid"]:
            raise LedgerError(f"{number} has payments against it; refund those first")
        with self.db:
            if d["journal_id"]:
                self.db.execute("DELETE FROM journal WHERE id = ?", (d["journal_id"],))
            self.db.execute("UPDATE documents SET status = 'void', journal_id = NULL WHERE number = ?", (number,))

    def documents(self, kind: str = "invoice", *, status: str = "open", party: str = "",
                  limit: int = 100) -> list[dict[str, Any]]:
        where, params = ["kind = ?"], [kind]
        if status and status != "all":
            where.append("status = ?"); params.append(status)
        if party:
            where.append("party LIKE ?"); params.append(f"%{party}%")
        rows = self.db.execute(f"SELECT number FROM documents WHERE {' AND '.join(where)} "
                               f"ORDER BY date DESC, id DESC LIMIT ?", params + [max(1, int(limit))]).fetchall()
        return [self.document(r["number"]) for r in rows]

    def aged(self, kind: str = "invoice", *, as_of: Any = None) -> dict[str, Any]:
        """Aged receivables (kind='invoice') or payables (kind='bill')."""
        as_of_d = _iso(parse_date(as_of)) if as_of else date.today()
        buckets = {"current": 0, "1-30": 0, "31-60": 0, "61-90": 0, "90+": 0}
        by_party: dict[str, dict[str, Any]] = {}
        for d in self.documents(kind, status="open", limit=10000):
            out = to_minor(d["outstanding"])
            if out <= 0:
                continue
            overdue = (as_of_d - _iso(d["due"])).days
            b = ("current" if overdue <= 0 else "1-30" if overdue <= 30 else "31-60" if overdue <= 60
                 else "61-90" if overdue <= 90 else "90+")
            buckets[b] += out
            p = by_party.setdefault(d["party"], {"party": d["party"], "total": 0, "overdue": 0, "documents": []})
            p["total"] += out
            if overdue > 0:
                p["overdue"] += out
            p["documents"].append({"number": d["number"], "due": d["due"], "outstanding": d["outstanding"],
                                   "days_overdue": max(0, overdue)})
        parties = sorted(by_party.values(), key=lambda p: -p["total"])
        for p in parties:
            p["total"], p["overdue"] = to_major(p["total"]), to_major(p["overdue"])
        return {"kind": kind, "as_of": as_of_d.isoformat(),
                "buckets": {k: to_major(v) for k, v in buckets.items()},
                "total": to_major(sum(buckets.values())), "parties": parties}


    # -- attachments -------------------------------------------------------------------

    def attach(self, journal_id: int, path: str, kind: str = "support") -> None:
        self.journal(journal_id)                                   # raises if missing
        with self.db:
            self.db.execute("INSERT INTO attachments(journal_id, path, kind) VALUES (?, ?, ?)",
                            (journal_id, path, kind))

    def attachments(self, journal_id: int) -> list[str]:
        return [r["path"] for r in self.db.execute(
            "SELECT path FROM attachments WHERE journal_id = ? ORDER BY id", (journal_id,))]

    # -- contacts ------------------------------------------------------------------------

    def upsert_contact(self, name: str, kind: str, *, email: str = "", bank_account: str = "",
                       tax_id: str = "", terms_days: int = 30, default_account: str = "",
                       created_at: str = "", bank_changed_at: str = "") -> dict[str, Any]:
        kind = kind.lower()
        if kind not in ("customer", "supplier", "employee", "other"):
            raise LedgerError("contact kind must be customer, supplier, employee or other")
        now_s = created_at or datetime.now().isoformat(timespec="seconds")
        existing = self.db.execute("SELECT * FROM contacts WHERE name = ?", (name.strip(),)).fetchone()
        with self.db:
            if existing is None:
                self.db.execute(
                    "INSERT INTO contacts(name, kind, email, bank_account, tax_id, terms_days, "
                    "default_account, created_at, bank_changed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (name.strip(), kind, email, bank_account, tax_id, int(terms_days),
                     default_account, now_s, bank_changed_at))
            else:
                changed = bank_account and bank_account != existing["bank_account"]
                self.db.execute(
                    "UPDATE contacts SET email = COALESCE(NULLIF(?, ''), email), "
                    "bank_account = COALESCE(NULLIF(?, ''), bank_account), "
                    "tax_id = COALESCE(NULLIF(?, ''), tax_id), "
                    "default_account = COALESCE(NULLIF(?, ''), default_account), "
                    "bank_changed_at = ? WHERE id = ?",
                    (email, bank_account, tax_id, default_account,
                     (bank_changed_at or now_s) if changed else existing["bank_changed_at"], existing["id"]))
        return self.contact(name)

    def contact(self, name: str) -> dict[str, Any]:
        r = self.db.execute("SELECT * FROM contacts WHERE name = ?", (name.strip(),)).fetchone()
        if r is None:
            rows = self.db.execute("SELECT * FROM contacts WHERE name LIKE ?", (f"%{name.strip()}%",)).fetchall()
            if len(rows) != 1:
                raise LedgerError(f"no contact {name!r}")
            r = rows[0]
        return {k: r[k] for k in r.keys() if k != "id"}

    def contacts(self, kind: str = "") -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM contacts", []
        if kind:
            sql += " WHERE kind = ?"; params.append(kind)
        return [{k: r[k] for k in r.keys() if k != "id"} for r in self.db.execute(sql + " ORDER BY name", params)]

    # -- purchase orders -------------------------------------------------------------------

    def add_purchase_order(self, number: str, supplier: str, when: Any, total: Any,
                           items: Optional[list[dict[str, Any]]] = None, status: str = "open") -> None:
        with self.db:
            self.db.execute("INSERT INTO purchase_orders(number, supplier, date, total, status, items) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (number, supplier, parse_date(when), _amt(total), status, json.dumps(items or [])))

    def purchase_orders(self, *, supplier: str = "", status: str = "") -> list[dict[str, Any]]:
        sql, params, where = "SELECT * FROM purchase_orders", [], []
        if supplier:
            where.append("supplier LIKE ?"); params.append(f"%{supplier}%")
        if status:
            where.append("status = ?"); params.append(status)
        if where:
            sql += " WHERE " + " AND ".join(where)
        return [{"number": r["number"], "supplier": r["supplier"], "date": r["date"],
                 "total": to_major(r["total"]), "status": r["status"], "items": json.loads(r["items"])}
                for r in self.db.execute(sql + " ORDER BY date", params)]

    # -- payroll -------------------------------------------------------------------------------

    def add_payroll(self, period: str, employee_id: str, name: str, gross: Any, net: Any,
                    tax: Any = 0, bank_account: str = "") -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO payroll(period, employee_id, name, gross, tax, net, "
                            "bank_account) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (period, employee_id, name, _amt(gross), _amt(tax), _amt(net), bank_account))

    def payroll(self, period: str) -> list[dict[str, Any]]:
        return [{"employee_id": r["employee_id"], "name": r["name"], "gross": to_major(r["gross"]),
                 "tax": to_major(r["tax"]), "net": to_major(r["net"]), "bank_account": r["bank_account"]}
                for r in self.db.execute("SELECT * FROM payroll WHERE period = ? ORDER BY employee_id", (period,))]

    def payroll_periods(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT DISTINCT period FROM payroll ORDER BY period")]


# -- helpers ----------------------------------------------------------------------

def _amt(value: Any) -> int:
    if value is None or value == "":
        return 0
    return to_minor(value)


def _balance(type_: str, dr: int, cr: int) -> int:
    return (dr - cr) if type_ in DEBIT_NORMAL else (cr - dr)


_COL_HINTS: dict[str, tuple[str, ...]] = {
    "date": ("date", "transaction date", "posted", "value date", "booking date", "when"),
    "description": ("description", "narrative", "details", "memo", "payee", "name", "transaction",
                    "merchant", "particulars", "text", "counterparty"),
    "reference": ("reference", "ref", "transaction type", "type"),
    "amount": ("amount", "value", "sum", "transaction amount", "net"),
    "debit": ("debit", "paid out", "money out", "withdrawal", "withdrawals", "out", "spent", "dr"),
    "credit": ("credit", "paid in", "money in", "deposit", "deposits", "in", "received", "cr"),
    "balance": ("balance", "running balance", "closing balance"),
}


def detect_columns(headers: list[str], rows: list[dict[str, Any]]) -> dict[str, str]:
    """Map our roles onto whatever the bank called its columns."""
    found: dict[str, str] = {}
    low = {h: re.sub(r"[^a-z ]", "", str(h).lower()).strip() for h in headers}
    for role, hints in _COL_HINTS.items():
        for h in headers:
            name = low[h]
            if role in found:
                break
            if name in hints or any(name.startswith(x) or name.endswith(x) for x in hints if len(x) > 3):
                if h not in found.values():
                    found[role] = h
    # A single signed column beats a debit/credit pair when both are present
    # but the pair is empty (some exports include all three).
    if "amount" in found and ("debit" in found or "credit" in found):
        if all(not str(r.get(found["amount"]) or "").strip() for r in rows[:20]):
            found.pop("amount")
    if "date" not in found:                       # first column whose values parse as dates
        for h in headers:
            sample = [r.get(h) for r in rows[:5] if r.get(h)]
            if sample and all(_is_date(v) for v in sample):
                found["date"] = h
                break
    if "description" not in found:                # the longest text column
        best, best_len = "", 0
        for h in headers:
            if h in found.values():
                continue
            avg = sum(len(str(r.get(h) or "")) for r in rows[:20]) / max(1, min(20, len(rows)))
            if avg > best_len and not all(_is_number(r.get(h)) for r in rows[:5] if r.get(h)):
                best, best_len = h, avg
        if best:
            found["description"] = best
    return found


def _is_date(v: Any) -> bool:
    try:
        parse_date(v)
        return True
    except LedgerError:
        return False


def _is_number(v: Any) -> bool:
    try:
        to_minor(v)
        return True
    except LedgerError:
        return False


def _row_amount(raw: dict[str, Any], cols: dict[str, str]) -> int:
    if "amount" in cols and str(raw.get(cols["amount"]) or "").strip():
        return to_minor(raw.get(cols["amount"]))
    debit = credit = 0
    if "debit" in cols and str(raw.get(cols["debit"]) or "").strip():
        debit = abs(to_minor(raw.get(cols["debit"])))
    if "credit" in cols and str(raw.get(cols["credit"]) or "").strip():
        credit = abs(to_minor(raw.get(cols["credit"])))
    if not debit and not credit:
        raise LedgerError("no amount")
    return credit - debit


def read_csv_rows(text: str) -> list[dict[str, Any]]:
    """Statement CSV text -> rows keyed by header. Copes with preamble
    lines above the header and with either delimiter."""
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return []
    # Header = first line with 2+ cells whose cells are mostly words.
    start = 0
    for i, line in enumerate(lines[:15]):
        cells = re.split(r"[,;\t]", line)
        if len(cells) >= 2 and sum(1 for c in cells if re.search(r"[A-Za-z]", c)) >= max(2, len(cells) // 2):
            start = i
            break
    sample = "\n".join(lines[start:start + 5])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO("\n".join(lines[start:])), dialect=dialect)
    rows = []
    for r in reader:
        clean = {str(k).strip(): (v.strip() if isinstance(v, str) else v) for k, v in r.items() if k is not None}
        if any(str(v or "").strip() for v in clean.values()):
            rows.append(clean)
    return rows
