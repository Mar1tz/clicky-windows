"""A freelancer's business: one folder, the books, and the work around them.

The money lives in the Accounting pack's double-entry Ledger, used here as
a library: an issued invoice, a payment and a receipt are real journal
entries, so the year-end pack the accountant receives balances. What a
ledger has no place for - hours not yet billed, quotes, invoice drafts,
receipts waiting to be posted, the tax put aside from each payment, the
emails waiting to be sent - lives beside it in work.sqlite.

    <root>/freelancer.json   name, chart preset, rate, terms, set-aside %
    <root>/books.sqlite      the Ledger
    <root>/work.sqlite       time, quotes, drafts, set-asides, outbox
    <root>/Invoices  Quotes  Receipts  Outbox  Year-end

Nothing here sends anything. An issued invoice or a reminder becomes an
.eml in Outbox that the freelancer opens in their mail app and sends.
"""
from __future__ import annotations

import csv
import json
import os
import sqlite3
import threading
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from typing import Any, Iterable, Optional

from extensions.accounting.books import Ledger, LedgerError, fmt, parse_date, to_major, to_minor
from extensions.accounting.pdfgen import write_pdf

SETTINGS_FILE = "freelancer.json"
FOLDERS = ("Invoices", "Quotes", "Receipts", "Outbox", "Year-end")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS time_entries (
    id INTEGER PRIMARY KEY, date TEXT NOT NULL, client TEXT NOT NULL COLLATE NOCASE,
    hours REAL NOT NULL, rate INTEGER NOT NULL, note TEXT NOT NULL DEFAULT '',
    draft_id INTEGER, invoice TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY, number TEXT NOT NULL UNIQUE, client TEXT NOT NULL, date TEXT NOT NULL,
    valid_until TEXT NOT NULL, items TEXT NOT NULL, total INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'open', file TEXT NOT NULL DEFAULT '', draft_id INTEGER
);
CREATE TABLE IF NOT EXISTS invoice_drafts (
    id INTEGER PRIMARY KEY, client TEXT NOT NULL, items TEXT NOT NULL, due_days INTEGER NOT NULL,
    notes TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft',
    number TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS expense_drafts (
    id INTEGER PRIMARY KEY, date TEXT NOT NULL, description TEXT NOT NULL, amount INTEGER NOT NULL,
    category TEXT NOT NULL, receipt TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'draft',
    journal_id INTEGER
);
CREATE TABLE IF NOT EXISTS set_asides (
    id INTEGER PRIMARY KEY, date TEXT NOT NULL, invoice TEXT NOT NULL, received INTEGER NOT NULL,
    pct REAL NOT NULL, amount INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY, created TEXT NOT NULL, kind TEXT NOT NULL, recipient TEXT NOT NULL,
    subject TEXT NOT NULL, path TEXT NOT NULL, invoice TEXT NOT NULL DEFAULT ''
);
"""


class NotABusiness(LookupError):
    pass


def _today() -> str:
    return date.today().isoformat()


def _iso(d: str) -> date:
    return date.fromisoformat(d)


def _items(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Line items as the Ledger takes them, checked before anything is stored."""
    out = []
    for it in items or []:
        desc = str(it.get("description") or "").strip()
        if not desc:
            raise ValueError("every line needs a description")
        qty = float(it.get("quantity", 1) or 1)
        unit = it.get("unit_price", it.get("amount"))
        if unit in (None, ""):
            raise ValueError(f"'{desc}' needs a unit_price")
        if qty <= 0 or to_minor(unit) <= 0:
            raise ValueError(f"'{desc}' needs a quantity and price above zero")
        out.append({"description": desc, "quantity": qty, "unit_price": to_major(to_minor(unit))})
    if not out:
        raise ValueError("at least one line is needed")
    return out


def _total(items: list[dict[str, Any]]) -> int:
    return sum(round(i["quantity"] * to_minor(i["unit_price"])) for i in items)


class Business:
    """One freelancer's books and work. Every public method commits or raises."""

    def __init__(self, root: str) -> None:
        path = os.path.join(root, SETTINGS_FILE)
        if not os.path.isfile(path):
            raise NotABusiness(f"{root} is not a freelancer folder (no {SETTINGS_FILE}); set the business up first")
        self.root = root
        with open(path, encoding="utf-8") as fh:
            self.cfg: dict[str, Any] = json.load(fh)
        self.ledger = Ledger(os.path.join(root, "books.sqlite"))
        self.db = sqlite3.connect(os.path.join(root, "work.sqlite"), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        self.lock = threading.RLock()

    @classmethod
    def create(cls, root: str, name: str, *, preset: str = "generic", currency: str = "", rate: Any = 0,
               terms_days: int = 14, set_aside_pct: float = 25.0, email: str = "") -> "Business":
        os.makedirs(root, exist_ok=True)
        cfg = {"name": name.strip() or "My business", "preset": preset, "rate": to_major(to_minor(rate or 0)),
               "terms_days": int(terms_days), "set_aside_pct": float(set_aside_pct), "email": email}
        with Ledger(os.path.join(root, "books.sqlite")) as books:
            if not books.is_set_up:
                books.setup(cfg["name"], preset=preset, currency=currency)
        with open(os.path.join(root, SETTINGS_FILE), "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
        for f in FOLDERS:
            os.makedirs(os.path.join(root, f), exist_ok=True)
        return cls(root)

    @staticmethod
    def is_business(root: str) -> bool:
        return os.path.isfile(os.path.join(root, SETTINGS_FILE))

    def close(self) -> None:
        try:
            self.ledger.close()
        finally:
            self.db.close()

    # -- settings ------------------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self.cfg.get("name", "My business")

    @property
    def currency(self) -> str:
        return self.ledger.company().currency

    @property
    def rate(self) -> int:
        return to_minor(self.cfg.get("rate") or 0)

    @property
    def terms_days(self) -> int:
        return int(self.cfg.get("terms_days") or 14)

    @property
    def set_aside_pct(self) -> float:
        return float(self.cfg.get("set_aside_pct", 25.0))

    def money(self, minor: int) -> str:
        return fmt(minor, self.currency)

    def folder(self, kind: str) -> str:
        path = os.path.join(self.root, kind)
        os.makedirs(path, exist_ok=True)
        return path

    def tax_year(self, year: Optional[int] = None) -> tuple[str, str]:
        """The tax year that starts in `year`, from the books' fiscal start (MM-DD)."""
        fy = self.ledger.company().fiscal_year_start
        m, d = int(fy[:2]), int(fy[3:])
        if year is None:
            today = date.today()
            year = today.year if (today.month, today.day) >= (m, d) else today.year - 1
        start = date(year, m, d)
        return start.isoformat(), (date(year + 1, m, d) - timedelta(days=1)).isoformat()

    # -- clients ---------------------------------------------------------------------------------

    def client_email(self, client: str) -> str:
        try:
            return self.ledger.contact(client).get("email") or ""
        except LedgerError:
            return ""

    def remember_client(self, client: str, email: str = "") -> None:
        self.ledger.upsert_contact(client, "customer", email=email, terms_days=self.terms_days)

    # -- time --------------------------------------------------------------------------------------

    def log_time(self, client: str, hours: float, *, when: Any = None, note: str = "",
                 rate: Any = None) -> dict[str, Any]:
        client = (client or "").strip()
        if not client:
            raise ValueError("which client was the work for?")
        hours = float(hours)
        if not 0 < hours <= 24:
            raise ValueError("hours must be more than 0 and at most 24 for one entry")
        r = to_minor(rate) if rate not in (None, "") else self.rate
        if r <= 0:
            raise ValueError("no hourly rate: give one, or set the business's default rate")
        when = parse_date(when) if when else _today()
        with self.lock, self.db:
            cur = self.db.execute("INSERT INTO time_entries(date, client, hours, rate, note) VALUES (?, ?, ?, ?, ?)",
                                  (when, client, hours, r, note.strip()))
        return self.time_entry(int(cur.lastrowid))

    def time_entry(self, entry_id: int) -> dict[str, Any]:
        r = self.db.execute("SELECT * FROM time_entries WHERE id = ?", (entry_id,)).fetchone()
        if r is None:
            raise KeyError(f"no time entry #{entry_id}")
        return {"id": r["id"], "date": r["date"], "client": r["client"], "hours": r["hours"],
                "rate": to_major(r["rate"]), "value": to_major(round(r["hours"] * r["rate"])), "note": r["note"],
                "draft_id": r["draft_id"], "invoice": r["invoice"]}

    def unbilled(self, client: str = "") -> list[dict[str, Any]]:
        sql, params = "SELECT id FROM time_entries WHERE draft_id IS NULL AND invoice = ''", []
        if client:
            sql += " AND client LIKE ?"; params.append(f"%{client.strip()}%")
        return [self.time_entry(r["id"]) for r in self.db.execute(sql + " ORDER BY date, id", params)]

    # -- quotes --------------------------------------------------------------------------------------

    def create_quote(self, client: str, items: list[dict[str, Any]], *, valid_days: int = 30,
                     notes: str = "") -> dict[str, Any]:
        client = (client or "").strip()
        if not client:
            raise ValueError("who is the quote for?")
        lines = _items(items)
        when = _today()
        with self.lock:
            seq = (self.db.execute("SELECT count(*) FROM quotes").fetchone()[0] or 0) + 1
            number = f"Q-{seq:04d}"
            valid = (_iso(when) + timedelta(days=max(1, int(valid_days)))).isoformat()
            path = os.path.join(self.folder("Quotes"), f"{number} {_safe(client)}.pdf")
            write_pdf(path, self._document_lines(client, lines, when, f"Valid until {valid}", notes),
                      title=f"{self.name} - Quote {number}")
            with self.db:
                self.db.execute("INSERT INTO quotes(number, client, date, valid_until, items, total, file) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                                (number, client, when, valid, json.dumps(lines), _total(lines), path))
        return self.quote(number)

    def quote(self, number: str) -> dict[str, Any]:
        r = self.db.execute("SELECT * FROM quotes WHERE number = ?", (number.strip().upper(),)).fetchone()
        if r is None:
            raise KeyError(f"no quote {number!r}")
        return {"number": r["number"], "client": r["client"], "date": r["date"], "valid_until": r["valid_until"],
                "items": json.loads(r["items"]), "total": to_major(r["total"]), "status": r["status"],
                "file": r["file"], "draft_id": r["draft_id"]}

    def quotes(self, status: str = "open") -> list[dict[str, Any]]:
        sql, params = "SELECT number FROM quotes", []
        if status and status != "all":
            sql += " WHERE status = ?"; params.append(status)
        return [self.quote(r["number"]) for r in self.db.execute(sql + " ORDER BY id DESC", params)]

    def accept_quote(self, number: str) -> dict[str, Any]:
        q = self.quote(number)
        if q["status"] != "open":
            raise ValueError(f"{q['number']} is {q['status']}, not open")
        draft = self.draft_invoice(q["client"], q["items"], source=q["number"],
                                   notes=f"As quoted in {q['number']}.")
        with self.lock, self.db:
            self.db.execute("UPDATE quotes SET status = 'accepted', draft_id = ? WHERE number = ?",
                            (draft["id"], q["number"]))
        return draft

    # -- invoice drafts --------------------------------------------------------------------------------

    def draft_invoice(self, client: str, items: list[dict[str, Any]], *, due_days: Optional[int] = None,
                      notes: str = "", source: str = "", time_ids: Iterable[int] = ()) -> dict[str, Any]:
        client = (client or "").strip()
        if not client:
            raise ValueError("who is the invoice for?")
        lines = _items(items)
        with self.lock, self.db:
            cur = self.db.execute(
                "INSERT INTO invoice_drafts(client, items, due_days, notes, created, source) VALUES (?, ?, ?, ?, ?, ?)",
                (client, json.dumps(lines), int(self.terms_days if due_days is None else due_days), notes.strip(),
                 datetime.now().isoformat(timespec="seconds"), source))
            draft_id = int(cur.lastrowid)
            for tid in time_ids:
                self.db.execute("UPDATE time_entries SET draft_id = ? WHERE id = ?", (draft_id, tid))
        return self.draft(draft_id)

    def draft_from_time(self, client: str) -> dict[str, Any]:
        entries = self.unbilled(client)
        if not entries:
            raise ValueError(f"no unbilled time for {client}")
        names = {e["client"].lower() for e in entries}
        if len(names) > 1:
            raise ValueError(f"'{client}' matches more than one client ({', '.join(sorted(names))}); be exact")
        items = [{"description": f"{e['date']} {e['note'] or 'Work'} ({e['hours']:g} h)",
                  "quantity": e["hours"], "unit_price": e["rate"]} for e in entries]
        return self.draft_invoice(entries[0]["client"], items, source="time", time_ids=[e["id"] for e in entries])

    def draft(self, draft_id: int) -> dict[str, Any]:
        r = self.db.execute("SELECT * FROM invoice_drafts WHERE id = ?", (int(draft_id),)).fetchone()
        if r is None:
            raise KeyError(f"no invoice draft #{draft_id}")
        items = json.loads(r["items"])
        return {"id": r["id"], "client": r["client"], "items": items, "total": to_major(_total(items)),
                "due_days": r["due_days"], "notes": r["notes"], "created": r["created"], "status": r["status"],
                "number": r["number"], "source": r["source"]}

    def drafts(self, status: str = "draft") -> list[dict[str, Any]]:
        return [self.draft(r["id"]) for r in self.db.execute(
            "SELECT id FROM invoice_drafts WHERE status = ? ORDER BY id", (status,))]

    def discard_draft(self, draft_id: int) -> None:
        d = self.draft(draft_id)
        if d["status"] != "draft":
            raise ValueError(f"draft #{draft_id} is {d['status']}")
        with self.lock, self.db:
            self.db.execute("UPDATE invoice_drafts SET status = 'discarded' WHERE id = ?", (draft_id,))
            self.db.execute("UPDATE time_entries SET draft_id = NULL WHERE draft_id = ?", (draft_id,))

    # -- issuing, payments -------------------------------------------------------------------------------

    def issue(self, draft_id: int, *, when: Any = None) -> dict[str, Any]:
        """Put a draft on the books as an invoice, write its PDF and the email
        that goes with it. The only step that makes an invoice real."""
        with self.lock:
            d = self.draft(draft_id)
            if d["status"] != "draft":
                raise ValueError(f"draft #{draft_id} is already {d['status']}"
                                 + (f" as {d['number']}" if d["number"] else ""))
            self.remember_client(d["client"])
            inv = self.ledger.create_document("invoice", d["client"], d["items"], when=when or _today(),
                                              due_days=d["due_days"], notes=d["notes"], reference=d["source"])
            number = inv["number"]
            pdf = os.path.join(self.folder("Invoices"), f"{number} {_safe(d['client'])}.pdf")
            write_pdf(pdf, self._document_lines(d["client"], d["items"], inv["date"], f"Due {inv['due']}",
                                                d["notes"], total=to_minor(inv["total"])),
                      title=f"{self.name} - Invoice {number}")
            self.ledger.set_document_file(number, pdf)
            with self.db:
                self.db.execute("UPDATE invoice_drafts SET status = 'issued', number = ? WHERE id = ?",
                                (number, draft_id))
                self.db.execute("UPDATE time_entries SET invoice = ? WHERE draft_id = ?", (number, draft_id))
            body = (f"Hello,\n\nPlease find attached invoice {number} for {self.money(to_minor(inv['total']))}, "
                    f"due on {inv['due']}.\n\nThank you,\n{self.name}\n")
            eml = self._outbox("invoice", d["client"], f"Invoice {number} from {self.name}", body, number, pdf)
        return {**self.ledger.document(number), "email": eml}

    def invoices(self, status: str = "open") -> list[dict[str, Any]]:
        return self.ledger.documents("invoice", status=status, limit=10000)

    def overdue(self, as_of: Any = None) -> list[dict[str, Any]]:
        today = _iso(parse_date(as_of)) if as_of else date.today()
        out = []
        for inv in self.invoices("open"):
            late = (today - _iso(inv["due"])).days
            if late > 0 and inv["outstanding"] > 0:
                out.append({**inv, "days_late": late})
        return sorted(out, key=lambda i: -i["days_late"])

    def record_payment(self, number: str, amount: Any = None, *, when: Any = None) -> dict[str, Any]:
        """Money in against an invoice, and the share of it to put aside for tax."""
        with self.lock:
            paid = self.ledger.pay_document(number.strip().upper(), amount, when=when)
            received = to_minor(paid["paid"]) - sum(r["received"] for r in self.db.execute(
                "SELECT received FROM set_asides WHERE invoice = ?", (paid["number"],)))
            aside = round(received * self.set_aside_pct / 100)
            with self.db:
                self.db.execute("INSERT INTO set_asides(date, invoice, received, pct, amount) VALUES (?, ?, ?, ?, ?)",
                                (parse_date(when) if when else _today(), paid["number"], received,
                                 self.set_aside_pct, aside))
        return {**paid, "received": to_major(received), "set_aside": to_major(aside),
                "set_aside_pct": self.set_aside_pct}

    def set_asides(self, start: str = "", end: str = "") -> dict[str, Any]:
        if not start:
            start, end = self.tax_year()
        rows = [dict(r) for r in self.db.execute(
            "SELECT * FROM set_asides WHERE date >= ? AND date <= ? ORDER BY date, id", (start, end))]
        return {"start": start, "end": end,
                "rows": [{**r, "received": to_major(r["received"]), "amount": to_major(r["amount"])} for r in rows],
                "received": to_major(sum(r["received"] for r in rows)),
                "total": to_major(sum(r["amount"] for r in rows))}

    # -- reminders --------------------------------------------------------------------------------------

    def draft_reminder(self, number: str, *, as_of: Any = None) -> dict[str, Any]:
        inv = next((i for i in self.overdue(as_of) if i["number"] == number.strip().upper()), None)
        if inv is None:
            doc = self.ledger.document(number.strip().upper())
            raise ValueError(f"{doc['number']} is not overdue (status {doc['status']}, due {doc['due']})")
        late, owed = inv["days_late"], self.money(to_minor(inv["outstanding"]))
        if late <= 14:
            tone, text = "friendly", (f"Just a gentle reminder that invoice {inv['number']} for {owed} was due on "
                                      f"{inv['due']}. If it is already on its way, thank you and please ignore this.")
        elif late <= 45:
            tone, text = "firm", (f"Invoice {inv['number']} for {owed} is now {late} days overdue (due {inv['due']}). "
                                  "Please let me know when I can expect payment.")
        else:
            tone, text = "final", (f"Invoice {inv['number']} for {owed} is {late} days overdue. Please pay it within "
                                   "seven days, or call me to agree a date.")
        body = f"Hello,\n\n{text}\n\nKind regards,\n{self.name}\n"
        eml = self._outbox("reminder", inv["party"], f"Reminder: invoice {inv['number']}", body, inv["number"],
                           inv.get("file_path") or "")
        return {"invoice": inv["number"], "client": inv["party"], "days_late": late, "tone": tone, "email": eml,
                "outstanding": inv["outstanding"]}

    def reminded_today(self, number: str) -> bool:
        return self.db.execute("SELECT 1 FROM outbox WHERE kind = 'reminder' AND invoice = ? AND created >= ?",
                               (number, _today())).fetchone() is not None

    def outbox(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM outbox ORDER BY id DESC")]

    def _outbox(self, kind: str, client: str, subject: str, body: str, invoice: str, attach: str) -> str:
        msg = EmailMessage()
        to = self.client_email(client)
        msg["To"] = to
        if self.cfg.get("email"):
            msg["From"] = self.cfg["email"]
        msg["Subject"] = subject
        msg["X-Unsent"] = "1"                   # Outlook opens it as a draft, ready to send
        msg.set_content(body)
        if attach and os.path.isfile(attach):
            with open(attach, "rb") as fh:
                msg.add_attachment(fh.read(), maintype="application", subtype="pdf",
                                   filename=os.path.basename(attach))
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(self.folder("Outbox"), f"{stamp} {kind} {invoice} {_safe(client)}.eml")
        with open(path, "wb") as fh:
            fh.write(bytes(msg))
        with self.db:
            self.db.execute("INSERT INTO outbox(created, kind, recipient, subject, path, invoice) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (datetime.now().isoformat(timespec="seconds"), kind, to or client, subject, path, invoice))
        return path

    # -- expenses ------------------------------------------------------------------------------------------

    def add_expense(self, description: str, amount: Any, *, category: str = "", when: Any = None,
                    receipt: str = "") -> dict[str, Any]:
        description = (description or "").strip()
        if not description:
            raise ValueError("what was the expense for?")
        minor = to_minor(amount)
        if minor <= 0:
            raise ValueError("the amount must be above zero")
        code = self.ledger.resolve_account(category) if category else (
            self.ledger.suggest(description) or "7900")
        if self.ledger.account_type(code) != "expense":
            raise ValueError(f"{self.ledger.account_name(code)} is not an expense account")
        with self.lock, self.db:
            cur = self.db.execute(
                "INSERT INTO expense_drafts(date, description, amount, category, receipt) VALUES (?, ?, ?, ?, ?)",
                (parse_date(when) if when else _today(), description, minor, code, receipt))
        return self.expense(int(cur.lastrowid))

    def expense(self, expense_id: int) -> dict[str, Any]:
        r = self.db.execute("SELECT * FROM expense_drafts WHERE id = ?", (int(expense_id),)).fetchone()
        if r is None:
            raise KeyError(f"no expense #{expense_id}")
        return {"id": r["id"], "date": r["date"], "description": r["description"], "amount": to_major(r["amount"]),
                "category": self.ledger.account_name(r["category"]), "code": r["category"], "receipt": r["receipt"],
                "status": r["status"], "journal_id": r["journal_id"]}

    def expenses(self, status: str = "draft") -> list[dict[str, Any]]:
        return [self.expense(r["id"]) for r in self.db.execute(
            "SELECT id FROM expense_drafts WHERE status = ? ORDER BY date, id", (status,))]

    def post_expenses(self, ids: Optional[Iterable[int]] = None) -> list[dict[str, Any]]:
        todo = [e for e in self.expenses("draft") if ids is None or e["id"] in set(int(i) for i in ids)]
        posted = []
        with self.lock:
            for e in todo:
                jid = self.ledger.expense(e["date"], e["description"], e["amount"], e["code"],
                                          reference=os.path.basename(e["receipt"]), source="receipt")
                if e["receipt"]:
                    self.ledger.attach(jid, e["receipt"], "receipt")
                with self.db:
                    self.db.execute("UPDATE expense_drafts SET status = 'posted', journal_id = ? WHERE id = ?",
                                    (jid, e["id"]))
                posted.append(self.expense(e["id"]))
        return posted

    # -- the whole picture ------------------------------------------------------------------------------------

    def overview(self, as_of: Any = None) -> dict[str, Any]:
        today = _iso(parse_date(as_of)) if as_of else date.today()
        month = self.ledger.profit_and_loss(start=today.replace(day=1).isoformat(), end=today.isoformat())
        open_inv, late = self.invoices("open"), self.overdue(today.isoformat())
        unbilled = self.unbilled()
        start, end = self.tax_year()
        return {"business": self.name, "currency": self.currency, "as_of": today.isoformat(),
                "month_income": month["total_income"], "month_expenses": month["total_expenses"],
                "unpaid": to_major(sum(to_minor(i["outstanding"]) for i in open_inv)), "unpaid_count": len(open_inv),
                "overdue": to_major(sum(to_minor(i["outstanding"]) for i in late)), "overdue_count": len(late),
                "unbilled_hours": round(sum(e["hours"] for e in unbilled), 2),
                "unbilled_value": to_major(sum(to_minor(e["value"]) for e in unbilled)),
                "set_aside": self.set_asides(start, end)["total"], "set_aside_pct": self.set_aside_pct,
                "tax_year": [start, end], "drafts": len(self.drafts()), "expense_drafts": len(self.expenses()),
                "open_quotes": len(self.quotes())}

    def year_end_pack(self, year: Optional[int] = None) -> dict[str, Any]:
        """Everything the accountant asks for, as files they can open."""
        start, end = self.tax_year(year)
        folder = self.folder(os.path.join("Year-end", f"{start[:4]}-{end[:4]}" if start[:4] != end[:4] else start[:4]))
        pnl = self.ledger.profit_and_loss(start=start, end=end)
        invoices = [i for i in self.invoices("all") if start <= i["date"] <= end and i["status"] != "void"]
        posted = [e for e in self.expenses("posted") if start <= e["date"] <= end]
        aside = self.set_asides(start, end)
        files = {
            "invoices.csv": _csv(folder, "invoices.csv", ["number", "date", "party", "subtotal", "tax", "total",
                                                          "paid", "outstanding", "status"], invoices),
            "expenses.csv": _csv(folder, "expenses.csv", ["date", "description", "category", "amount", "receipt"],
                                 posted),
            "set_aside.csv": _csv(folder, "set_aside.csv", ["date", "invoice", "received", "pct", "amount"],
                                  aside["rows"]),
            "profit_and_loss.csv": _csv(folder, "profit_and_loss.csv", ["section", "name", "amount"],
                                        [{"section": "income", **i} for i in pnl["income"]]
                                        + [{"section": "expense", **e} for e in pnl["expenses"]]),
        }
        missing = [e for e in posted if not e["receipt"]]
        unpaid = [i for i in invoices if i["outstanding"] > 0]
        cur = self.currency
        lines = [f"Tax year {start} to {end}", "",
                 f"Income            {fmt(to_minor(pnl['total_income']), cur)}",
                 f"Expenses          {fmt(to_minor(pnl['total_expenses']), cur)}",
                 f"Net profit        {fmt(to_minor(pnl['net_profit']), cur)}", "",
                 f"Invoices issued   {len(invoices)} ({len(unpaid)} still unpaid)",
                 f"Expenses posted   {len(posted)} ({len(missing)} without a receipt)",
                 f"Tax set aside     {fmt(to_minor(aside['total']), cur)} of {fmt(to_minor(aside['received']), cur)} "
                 f"received", "",
                 "Files: " + ", ".join(files), "",
                 "Prepared by Tora from your books. Your accountant decides what is owed."]
        files["summary.pdf"] = write_pdf(os.path.join(folder, "summary.pdf"), lines,
                                         title=f"{self.name} - year-end pack")
        return {"folder": folder, "start": start, "end": end, "files": files, "net_profit": pnl["net_profit"],
                "income": pnl["total_income"], "expenses": pnl["total_expenses"], "set_aside": aside["total"],
                "unpaid_invoices": [i["number"] for i in unpaid],
                "expenses_without_receipt": [e["id"] for e in missing]}

    # -- documents ---------------------------------------------------------------------------------------------

    def _document_lines(self, client: str, items: list[dict[str, Any]], when: str, due_line: str,
                        notes: str, *, total: Optional[int] = None) -> list[str]:
        lines = [f"To: {client}", f"Date: {when}", due_line, ""]
        for it in items:
            amount = round(it["quantity"] * to_minor(it["unit_price"]))
            lines.append(f"{it['description'][:60]:<60} {it['quantity']:>6g} x {it['unit_price']:>10.2f} "
                         f"{self.money(amount):>14}")
        lines += ["", f"{'Total':<60} {self.money(_total(items) if total is None else total):>33}"]
        if notes:
            lines += ["", notes]
        return lines


def _safe(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in " -_").strip()[:40] or "client"


def _csv(folder: str, name: str, cols: list[str], rows: list[dict[str, Any]]) -> str:
    path = os.path.join(folder, name)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path


class BusinessCache:
    """Tools are handed only ctx.workspace; this turns it into the open business."""

    def __init__(self) -> None:
        self._open: dict[str, Business] = {}
        self._lock = threading.RLock()

    def get(self, path: str) -> Business:
        key = os.path.normcase(os.path.abspath(path or "."))
        with self._lock:
            b = self._open.get(key)
            if b is None:
                b = self._open[key] = Business(path)
            return b

    def drop(self) -> None:
        with self._lock:
            for b in self._open.values():
                b.close()
            self._open.clear()
