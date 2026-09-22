"""LocalBooksProvider and MockAccountingProvider: IAccountingProvider over books.py.

LocalBooksProvider is a real set of books in the client's folder
(`.tora/books.sqlite`) for practices and sole traders who have no cloud
ledger - or who want the agents to prepare everything locally and then
export it. MockAccountingProvider is the same engine seeded with a
deterministic demo company, so every workflow can be exercised end to
end with no credentials; it is the provider the tests run against.

Both are "file" tier: nothing leaves the machine. Where a real system
would move money or file a return, the local provider records the fact
in the books and writes the file a bank or portal would need, and says
plainly that it has not paid or filed anything.
"""
from __future__ import annotations

import asyncio
import csv
import json
import os
import threading
from datetime import date, datetime
from typing import Any, Optional

from ..books import BANK, Ledger, LedgerError, fmt, parse_date, to_major, to_minor
from .base import (
    READ_METHODS, SENSITIVE_METHODS, WRITE_METHODS, Account, BankTransaction,
    IAccountingProvider, InvoiceRecord, LedgerLine, ProviderUnsupported,
)


class BooksProvider(IAccountingProvider):
    name = "local"
    display_name = "Local books"
    tier = "file"
    capabilities = frozenset(READ_METHODS + WRITE_METHODS + ("release_payment_batch",))

    def __init__(self, path: str, *, exports_dir: str = "") -> None:
        self.path = path
        self.exports_dir = exports_dir or os.path.join(os.path.dirname(os.path.dirname(path)), "Exports")
        self._ledger: Optional[Ledger] = None
        self._lock = threading.RLock()

    # One sqlite connection, used from whichever thread the tool runs on:
    # every call goes through _run, which serialises and hops to a thread.
    @property
    def ledger(self) -> Ledger:
        if self._ledger is None:
            self._ledger = Ledger(self.path)
        return self._ledger

    async def _run(self, fn, *args, **kwargs):
        def call():
            with self._lock:
                return fn(*args, **kwargs)
        return await asyncio.to_thread(call)

    def close(self) -> None:
        if self._ledger is not None:
            try:
                self._ledger.close()
            finally:
                self._ledger = None

    # -- read ------------------------------------------------------------------------
    async def company_info(self) -> dict[str, Any]:
        def get():
            L = self.ledger
            if not L.is_set_up:
                return {"set_up": False}
            return {"set_up": True, **L.company().to_dict(), **L.summary()}
        return await self._run(get)

    async def accounts(self) -> list[Account]:
        rows = await self._run(self.ledger.accounts)
        return [Account(r["code"], r["name"], r["type"], to_major(r["balance"]), r["taxable"]) for r in rows]

    async def trial_balance(self, as_of: Optional[str] = None) -> dict[str, Any]:
        return await self._run(self.ledger.trial_balance, as_of=as_of)

    async def general_ledger(self, start=None, end=None, account=None, limit: int = 500) -> list[LedgerLine]:
        def get():
            out = []
            for j in self.ledger.transactions(start=start, end=end, account=account, limit=limit):
                for l in j["lines"]:
                    if account and l["account"] != self.ledger.resolve_account(account):
                        continue
                    out.append(LedgerLine(f"J{j['id']}", j["date"], j["description"], l["account"],
                                          l["name"], l["debit"], l["credit"], j["reference"],
                                          j["source"], j["author"], j["created_at"], j["attachments"]))
            return out
        return await self._run(get)

    async def bank_accounts(self) -> list[Account]:
        rows = await self._run(self.ledger.accounts)
        return [Account(r["code"], r["name"], r["type"], to_major(r["balance"]))
                for r in rows if r["code"] in (BANK, "1010") or "bank" in r["name"].lower()
                or "credit card" in r["name"].lower()]

    async def bank_transactions(self, account=None, start=None, end=None,
                                unreconciled_only: bool = False) -> list[BankTransaction]:
        def get():
            L = self.ledger
            sql, params = "SELECT * FROM bank_lines WHERE 1=1", []
            if account:
                sql += " AND account = ?"; params.append(L.resolve_account(account))
            if start:
                sql += " AND date >= ?"; params.append(parse_date(start))
            if end:
                sql += " AND date <= ?"; params.append(parse_date(end))
            if unreconciled_only:
                sql += " AND journal_id IS NULL"
            rows = L.db.execute(sql + " ORDER BY date, id", params).fetchall()
            out = []
            for r in rows:
                sug = L.suggest(r["description"]) if r["journal_id"] is None else ""
                out.append(BankTransaction(f"bl-{r['id']}", r["account"], r["date"], r["description"],
                                           to_major(r["amount"]), r["journal_id"] is not None,
                                           f"J{r['journal_id']}" if r["journal_id"] else "", sug))
            return out
        return await self._run(get)

    async def invoices(self, kind: str = "sales", status: str = "all") -> list[InvoiceRecord]:
        doc_kind = "invoice" if kind in ("sales", "invoice", "customer") else "bill"
        def get():
            docs = self.ledger.documents(doc_kind, status="all" if status in ("all", "overdue") else status,
                                         limit=10000)
            return [InvoiceRecord(d["number"], "sales" if doc_kind == "invoice" else "purchase",
                                  d["party"], d["date"], d["due"], d["subtotal"], d["tax"], d["total"],
                                  d["paid"], d["status"], d.get("reference", ""), d.get("po_number", ""),
                                  d["file_path"], d["items"]) for d in docs]
        return await self._run(get)

    async def contacts(self, kind: str = "") -> list[dict[str, Any]]:
        return await self._run(self.ledger.contacts, kind)

    async def purchase_orders(self, supplier: str = "") -> list[dict[str, Any]]:
        return await self._run(self.ledger.purchase_orders, supplier=supplier)

    async def payroll(self, period: str) -> list[dict[str, Any]]:
        return await self._run(self.ledger.payroll, period)

    async def payroll_periods(self) -> list[str]:
        return await self._run(self.ledger.payroll_periods)

    async def balance(self, account: str, *, start=None, as_of=None) -> float:
        return to_major(await self._run(self.ledger.balance, account, start=start, as_of=as_of))

    async def profit_and_loss(self, start: str, end: str) -> dict[str, Any]:
        return await self._run(self.ledger.profit_and_loss, start=start, end=end)

    async def balance_sheet(self, as_of: str) -> dict[str, Any]:
        return await self._run(self.ledger.balance_sheet, as_of=as_of)

    async def tax_summary(self, start: str, end: str) -> dict[str, Any]:
        return await self._run(self.ledger.tax_summary, start=start, end=end)

    # -- write -----------------------------------------------------------------------------
    async def import_bank_statement(self, rows, *, account: str = "", source_file: str = "") -> dict[str, Any]:
        rep = await self._run(self.ledger.import_statement, rows, account=account or BANK,
                              source_file=source_file, auto_match=False, auto_categorise=False)
        return rep.__dict__

    async def post_journal(self, date, description, lines, *, reference="", attachments=None) -> str:
        def do():
            jid = self.ledger.post(date, description, lines, reference=reference, source="agent")
            for p in attachments or []:
                self.ledger.attach(jid, p)
            return f"J{jid}"
        return await self._run(do)

    async def post_bill(self, supplier, date, items, *, reference="", po_number="", document="",
                        due_days: int = 30) -> str:
        d = await self._run(self.ledger.create_document, "bill", supplier, items, when=date,
                            due_days=due_days, reference=reference, po_number=po_number,
                            file_path=document)
        return d["number"]

    async def post_invoice(self, customer, date, items, *, due_days: int = 30, reference="") -> str:
        d = await self._run(self.ledger.create_document, "invoice", customer, items, when=date,
                            due_days=due_days, reference=reference)
        return d["number"]

    async def post_bank_line(self, line_id, account, *, tax_rate=None, attachments=None) -> str:
        def do():
            jid = self.ledger.categorise(_line_no(line_id), account, tax_rate=tax_rate)
            for p in attachments or []:
                self.ledger.attach(jid, p)
            return f"J{jid}"
        return await self._run(do)

    async def apply_bank_match(self, line_id: str, target: str) -> str:
        def do():
            L = self.ledger
            n = _line_no(line_id)
            bl = L.db.execute("SELECT * FROM bank_lines WHERE id = ?", (n,)).fetchone()
            if bl is None:
                raise LedgerError(f"no bank line {line_id}")
            t = target.strip()
            if t.upper().startswith("J") and t[1:].isdigit():
                L.match(n, int(t[1:]))
                return t
            doc = L.document(t)
            paid = L.pay_document(doc["number"], to_major(abs(bl["amount"])), when=bl["date"],
                                  account=bl["account"])
            L.match(n, paid["payment_journal_id"])
            return f"J{paid['payment_journal_id']}"
        return await self._run(do)

    async def record_payment(self, number, amount=None, *, date=None, account="") -> str:
        d = await self._run(self.ledger.pay_document, number, amount, when=date, account=account or BANK)
        return f"J{d['payment_journal_id']}"

    async def attach_document(self, target: str, path: str) -> None:
        def do():
            t = target.strip()
            if t.upper().startswith("J") and t[1:].isdigit():
                self.ledger.attach(int(t[1:]), path)
                return
            d = self.ledger.document(t)
            if d["journal_id"]:
                self.ledger.attach(d["journal_id"], path)
            self.ledger.set_document_file(t, path)
        await self._run(do)

    async def upsert_contact(self, name: str, kind: str, **fields: Any) -> dict[str, Any]:
        return await self._run(self.ledger.upsert_contact, name, kind, **fields)

    # -- sensitive ----------------------------------------------------------------------------
    async def release_payment_batch(self, payments, *, reference: str) -> str:
        """Local books have no bank: record the payments and write the bulk
        payment file the bank's portal would import. Nothing is paid."""
        def do():
            os.makedirs(self.exports_dir, exist_ok=True)
            path = os.path.join(self.exports_dir, f"payment_batch_{reference}.csv")
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["bill", "supplier", "amount", "bank_account", "reference"])
                for p in payments:
                    self.ledger.pay_document(p["number"], p["amount"], when=date.today().isoformat())
                    w.writerow([p["number"], p.get("supplier", ""), f"{float(p['amount']):.2f}",
                                p.get("bank_account", ""), reference])
            return path
        return await self._run(do)

    async def submit_tax_return(self, summary, *, reference: str) -> str:
        raise ProviderUnsupported(
            f"{self.display_name} has no connection to a tax authority. The return pack is ready: "
            "submit it on the authority's portal yourself.")


class LocalBooksProvider(BooksProvider):
    name = "local"
    display_name = "Local books (this computer)"


class MockAccountingProvider(BooksProvider):
    """Deterministic demo books: ABC Manufacturing, June-August 2026, with
    the exceptions a month-end should find already planted. Seeded once
    into the client folder so a restart keeps whatever the agents did."""
    name = "mock"
    display_name = "Demo books (mock data)"
    capabilities = frozenset(READ_METHODS + WRITE_METHODS + SENSITIVE_METHODS)

    def __init__(self, path: str, *, exports_dir: str = "", documents_dir: str = "",
                 seed: bool = True) -> None:
        super().__init__(path, exports_dir=exports_dir)
        self.documents_dir = documents_dir
        if seed and not Ledger.exists(path):
            from ..demo import seed_demo
            with Ledger(path) as L:
                seed_demo(L, documents_dir=documents_dir)

    async def release_payment_batch(self, payments, *, reference: str) -> str:
        path = await super().release_payment_batch(payments, reference=reference)
        return f"MOCK-PAY-{reference} ({path})"

    async def submit_tax_return(self, summary, *, reference: str) -> str:
        log = os.path.join(os.path.dirname(self.path), "mock_submissions.json")
        items = []
        if os.path.isfile(log):
            with open(log, encoding="utf-8") as fh:
                items = json.load(fh)
        ref = f"MOCK-SUB-{len(items) + 1:04d}"
        items.append({"reference": ref, "draft": reference, "at": datetime.now().isoformat(timespec="seconds"),
                      "summary": summary})
        with open(log, "w", encoding="utf-8") as fh:
            json.dump(items, fh, indent=2, default=str)
        return ref


def _line_no(line_id: Any) -> int:
    text = str(line_id).strip().lower()
    if text.startswith("bl-"):
        text = text[3:]
    if not text.isdigit():
        raise LedgerError(f"bank line ids look like 'bl-12', not {line_id!r}")
    return int(text)
