"""CSVExcelProvider: books as a folder of exports.

The "file import/export" integration tier. Every accounting system can
export a trial balance, a ledger, a bank statement and invoice lists,
and import journals and bills from a file. This provider reads the
exports from the client's data folder and, when a draft is posted,
appends it to an import file in Exports/ instead of changing anything -
the accountant imports that file into the real system.

Expected files in the data folder (any of .csv / .xlsx, names matched
loosely, columns detected by header):
    trial_balance     code, name, type?, debit, credit   (or balance)
    general_ledger    date, account, description, debit, credit, reference
    bank*             date, description, amount | money in/out, balance
    customer_invoices number, customer, date, due, total, paid|outstanding
    supplier_invoices number, supplier, date, due, total, paid, reference, po
    contacts, purchase_orders, payroll_<YYYY-MM>   (optional)
"""
from __future__ import annotations

import csv
import glob
import os
import re
from datetime import datetime
from typing import Any, Optional

from ..books import LedgerError, detect_columns, parse_date, to_major, to_minor
from ..tables import TableError, read_table
from .base import Account, BankTransaction, IAccountingProvider, InvoiceRecord, LedgerLine

TYPE_BY_CODE = [("1", "asset"), ("2", "liability"), ("3", "equity"), ("4", "income"),
                ("5", "expense"), ("6", "expense"), ("7", "expense"), ("8", "expense"), ("9", "expense")]


class CSVExcelProvider(IAccountingProvider):
    name = "csv"
    display_name = "Spreadsheet / CSV exports"
    tier = "file"
    capabilities = frozenset({
        "company_info", "accounts", "trial_balance", "general_ledger", "bank_accounts",
        "bank_transactions", "invoices", "contacts", "purchase_orders", "payroll", "payroll_periods",
        "balance", "post_journal", "post_bill", "post_invoice",
    })

    def __init__(self, folder: str, *, exports_dir: str = "", company: str = "") -> None:
        self.folder = folder
        self.exports_dir = exports_dir or os.path.join(os.path.dirname(folder.rstrip("/\\")), "Exports")
        self.company = company

    # -- file discovery ---------------------------------------------------------------
    def _find(self, *stems: str) -> Optional[str]:
        for stem in stems:
            for ext in (".csv", ".xlsx", ".xlsm"):
                hits = sorted(glob.glob(os.path.join(self.folder, f"*{stem}*{ext}")))
                if hits:
                    return hits[0]
        return None

    def _rows(self, *stems: str) -> list[dict[str, Any]]:
        path = self._find(*stems)
        if not path:
            return []
        try:
            return read_table(path)
        except TableError as exc:
            raise LedgerError(str(exc)) from None

    # -- read ------------------------------------------------------------------------------
    async def company_info(self) -> dict[str, Any]:
        files = sorted(os.path.basename(p) for p in glob.glob(os.path.join(self.folder, "*"))
                       if p.lower().endswith((".csv", ".xlsx", ".xlsm")))
        return {"set_up": bool(files), "name": self.company, "files": files, "folder": self.folder}

    async def accounts(self) -> list[Account]:
        tb = await self.trial_balance()
        return [Account(r["code"], r["name"], r["type"], r.get("balance", 0.0)) for r in tb["rows"]]

    async def trial_balance(self, as_of: Optional[str] = None) -> dict[str, Any]:
        rows = self._rows("trial_balance", "trial balance", "tb")
        out, dr_total, cr_total = [], 0, 0
        for r in rows:
            low = {str(k).lower().strip(): v for k, v in r.items()}
            code = str(_first(low, "code", "account code", "account no", "number", "acc") or "").strip()
            name = str(_first(low, "name", "account name", "account", "description") or code).strip()
            if not code and not name:
                continue
            dr = _money(_first(low, "debit", "dr"))
            cr = _money(_first(low, "credit", "cr"))
            if not dr and not cr:
                bal = _money(_first(low, "balance", "closing balance", "amount"))
                dr, cr = (bal, 0) if bal >= 0 else (0, -bal)
            atype = str(_first(low, "type", "account type", "class") or _type_for(code)).lower()
            dr_total += dr; cr_total += cr
            balance = (dr - cr) if atype in ("asset", "expense") else (cr - dr)
            out.append({"code": code or name, "name": name, "type": atype, "debit": to_major(dr),
                        "credit": to_major(cr), "balance": to_major(balance)})
        return {"as_of": as_of or "", "rows": out, "total_debit": to_major(dr_total),
                "total_credit": to_major(cr_total), "balanced": dr_total == cr_total}

    async def general_ledger(self, start=None, end=None, account=None, limit: int = 500) -> list[LedgerLine]:
        out = []
        for i, r in enumerate(self._rows("general_ledger", "general ledger", "gl", "ledger")):
            low = {str(k).lower().strip(): v for k, v in r.items()}
            try:
                when = parse_date(_first(low, "date", "transaction date", "posted"))
            except LedgerError:
                continue
            if (start and when < parse_date(start)) or (end and when > parse_date(end)):
                continue
            acct = str(_first(low, "account", "account code", "code") or "").strip()
            if account and account.lower() not in acct.lower():
                continue
            out.append(LedgerLine(str(_first(low, "journal", "id", "entry") or f"L{i + 1}"), when,
                                  str(_first(low, "description", "details", "memo") or ""), acct,
                                  str(_first(low, "account name", "name") or acct),
                                  to_major(_money(_first(low, "debit", "dr"))),
                                  to_major(_money(_first(low, "credit", "cr"))),
                                  str(_first(low, "reference", "ref") or ""),
                                  str(_first(low, "source", "type") or ""),
                                  str(_first(low, "user", "author", "created by") or ""),
                                  str(_first(low, "created", "created at", "entered") or "")))
            if len(out) >= limit:
                break
        return out

    async def bank_accounts(self) -> list[Account]:
        return [a for a in await self.accounts() if "bank" in a.name.lower()] or \
               [Account("1000", "Bank", "asset")]

    async def bank_transactions(self, account=None, start=None, end=None,
                                unreconciled_only: bool = False) -> list[BankTransaction]:
        out = []
        for path in sorted(glob.glob(os.path.join(self.folder, "*bank*"))):
            if not path.lower().endswith((".csv", ".xlsx", ".xlsm")):
                continue
            rows = read_table(path)
            if not rows:
                continue
            cols = detect_columns(list(rows[0].keys()), rows)
            for i, r in enumerate(rows):
                try:
                    when = parse_date(r.get(cols.get("date", "")))
                except LedgerError:
                    continue
                if (start and when < parse_date(start)) or (end and when > parse_date(end)):
                    continue
                amount = _row_amount(r, cols)
                if amount is None:
                    continue
                out.append(BankTransaction(f"{os.path.basename(path)}#{i + 1}", account or "1000", when,
                                           str(r.get(cols.get("description", "")) or ""), to_major(amount)))
        return out

    async def invoices(self, kind: str = "sales", status: str = "all") -> list[InvoiceRecord]:
        stems = ("customer_invoices", "sales_invoices", "receivables", "debtors") if kind in ("sales", "customer") \
            else ("supplier_invoices", "purchase_invoices", "bills", "payables", "creditors")
        out = []
        for r in self._rows(*stems):
            low = {str(k).lower().strip(): v for k, v in r.items()}
            number = str(_first(low, "number", "invoice", "invoice number", "invoice no", "no") or "").strip()
            if not number:
                continue
            total = _money(_first(low, "total", "amount", "gross"))
            outstanding = _first(low, "outstanding", "amount due", "balance", "due amount")
            paid = total - _money(outstanding) if outstanding not in (None, "") else \
                _money(_first(low, "paid", "amount paid"))
            tax = _money(_first(low, "tax", "vat", "gst"))
            try:
                when = parse_date(_first(low, "date", "invoice date"))
                due = parse_date(_first(low, "due", "due date") or when)
            except LedgerError:
                continue
            st = "paid" if total and paid >= total else "open"
            if status not in ("all", "overdue") and st != status:
                continue
            out.append(InvoiceRecord(number, "sales" if kind in ("sales", "customer") else "purchase",
                                     str(_first(low, "customer", "supplier", "contact", "name", "party") or ""),
                                     when, due, to_major(total - tax), to_major(tax), to_major(total),
                                     to_major(paid), st, str(_first(low, "reference", "supplier ref", "ref") or ""),
                                     str(_first(low, "po", "po number", "order") or "")))
        return out

    async def contacts(self, kind: str = "") -> list[dict[str, Any]]:
        rows = self._rows("contacts", "suppliers", "customers")
        return [{str(k).lower(): v for k, v in r.items()} for r in rows
                if not kind or str(r.get("kind") or r.get("type") or kind).lower().startswith(kind[:4])]

    async def purchase_orders(self, supplier: str = "") -> list[dict[str, Any]]:
        out = []
        for r in self._rows("purchase_orders", "po"):
            low = {str(k).lower().strip(): v for k, v in r.items()}
            sup = str(_first(low, "supplier", "vendor") or "")
            if supplier and supplier.lower() not in sup.lower():
                continue
            out.append({"number": str(_first(low, "number", "po", "po number") or ""), "supplier": sup,
                        "date": str(_first(low, "date") or ""), "status": str(_first(low, "status") or "open"),
                        "total": to_major(_money(_first(low, "total", "amount")))})
        return out

    async def payroll(self, period: str) -> list[dict[str, Any]]:
        out = []
        for r in self._rows(f"payroll_{period}", f"payroll {period}"):
            low = {str(k).lower().strip(): v for k, v in r.items()}
            out.append({"employee_id": str(_first(low, "employee id", "id", "emp no") or ""),
                        "name": str(_first(low, "name", "employee") or ""),
                        "gross": to_major(_money(_first(low, "gross", "gross pay"))),
                        "tax": to_major(_money(_first(low, "tax", "paye"))),
                        "net": to_major(_money(_first(low, "net", "net pay"))),
                        "bank_account": str(_first(low, "bank account", "account") or "")})
        return out

    async def payroll_periods(self) -> list[str]:
        periods = set()
        for p in glob.glob(os.path.join(self.folder, "*payroll*")):
            m = re.search(r"(\d{4}-\d{2})", os.path.basename(p))
            if m:
                periods.add(m.group(1))
        return sorted(periods)

    async def balance(self, account: str, *, start=None, as_of=None) -> float:
        for r in (await self.trial_balance(as_of))["rows"]:
            if account in (r["code"], r["name"]):
                return r["balance"]
        return 0.0

    # -- "post": write import files ------------------------------------------------------------
    async def post_journal(self, date, description, lines, *, reference="", attachments=None) -> str:
        path = self._append("journals_for_import.csv", ["date", "reference", "description", "account",
                                                        "debit", "credit", "memo"],
                            [[parse_date(date), reference, description, l.get("account", ""),
                              l.get("debit") or "", l.get("credit") or "", l.get("memo", "")] for l in lines])
        return f"exported:{os.path.basename(path)}"

    async def post_bill(self, supplier, date, items, *, reference="", po_number="", document="",
                        due_days: int = 30) -> str:
        path = self._append("bills_for_import.csv", ["supplier", "date", "reference", "po", "description",
                                                     "quantity", "unit_price", "account", "tax_rate", "document"],
                            [[supplier, parse_date(date), reference, po_number, it.get("description", ""),
                              it.get("quantity", 1), it.get("unit_price", it.get("amount", "")),
                              it.get("account", ""), it.get("tax_rate", ""), document] for it in items])
        return f"exported:{os.path.basename(path)}"

    async def post_invoice(self, customer, date, items, *, due_days: int = 30, reference="") -> str:
        path = self._append("invoices_for_import.csv", ["customer", "date", "due_days", "reference",
                                                        "description", "quantity", "unit_price", "tax_rate"],
                            [[customer, parse_date(date), due_days, reference, it.get("description", ""),
                              it.get("quantity", 1), it.get("unit_price", ""), it.get("tax_rate", "")]
                             for it in items])
        return f"exported:{os.path.basename(path)}"

    def _append(self, name: str, header: list[str], rows: list[list[Any]]) -> str:
        os.makedirs(self.exports_dir, exist_ok=True)
        path = os.path.join(self.exports_dir, name)
        new = not os.path.isfile(path)
        with open(path, "a", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(header)
            w.writerows(rows)
        return path


def _first(row: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
    return None


def _money(v: Any) -> int:
    if v in (None, ""):
        return 0
    try:
        return to_minor(v)
    except LedgerError:
        return 0


def _type_for(code: str) -> str:
    for prefix, t in TYPE_BY_CODE:
        if code.startswith(prefix):
            return t
    return "expense"


def _row_amount(r: dict[str, Any], cols: dict[str, str]) -> Optional[int]:
    from ..books import _row_amount as ra
    try:
        return ra(r, cols)
    except LedgerError:
        return None
