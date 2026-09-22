"""Checks: READ tools that look for what is wrong and report it as exceptions.

Each check is mechanical and deterministic - the same books give the same
findings - so a month-end run is repeatable and testable, and the model's
judgement is spent on what to DO about the findings, not on finding them.
"""
from __future__ import annotations

import os
import re
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Optional

from ..books import LedgerError, parse_date, to_minor
from ..knowledge import hint_for
from ..tables import read_table
from .bank import account_code, account_names
from .common import (
    DATE, PATH, PERIOD, ActionLevel, F, Out, accounting_tool, as_of, lines_table, money, pct_change,
    period_range, rel, resolve_in_client,
)
from .invoices import _norm_ref

R = ActionLevel.READ


async def _journals(s, start: str, end: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for l in await s.provider.general_ledger(start, end, None, 5000):
        j = out.setdefault(l.journal, {"journal": l.journal, "date": l.date, "description": l.description,
                                       "source": l.source, "author": l.author, "created_at": l.created_at,
                                       "attachments": l.attachments, "reference": l.reference, "lines": []})
        j["lines"].append(l)
    return out


async def _types(s) -> dict[str, str]:
    return {a.code: a.type for a in await s.provider.accounts()}


def _bank_codes(s) -> set[str]:
    return set(s.ws.bank_accounts or ["1000"])


def _supplier_in(suppliers: list[dict[str, Any]], text: str) -> str:
    low = text.lower()
    for c in suppliers:
        first = c["name"].lower().split()[0]
        if c["name"].lower() in low or (len(first) >= 4 and first in low):
            return c["name"]
    return ""


# -- duplicates ---------------------------------------------------------------------------------

@accounting_tool("detect_duplicate_invoice", "Find supplier (or customer) invoices entered more than once: same "
                 "party and reference, or same party and amount within 7 days.", R,
                 {"kind": {"type": "string", "description": "supplier (default) | customer"}})
async def detect_duplicate_invoice(s, args, ctx) -> Out:
    kind = "sales" if (args.get("kind") or "supplier").startswith("cust") else "purchase"
    recs = [r for r in await s.provider.invoices(kind, "all") if r.status != "void"]
    by_ref: dict[tuple[str, str], list] = defaultdict(list)
    for r in recs:
        if r.reference:
            by_ref[(r.party.lower(), _norm_ref(r.reference))].append(r)
    findings, dups = [], []
    for (_party, _ref), group in by_ref.items():
        if len(group) > 1:
            nums = ", ".join(f"{g.number} ({g.date})" for g in group)
            dups.append({"party": group[0].party, "reference": group[0].reference, "entries": nums})
            findings.append(F(f"Duplicate invoice: {group[0].party} {group[0].reference} entered {len(group)} times",
                              f"{nums}; {money(s, group[0].total)} each", severity="high",
                              entity=f"{group[0].party} {group[0].reference}",
                              action="void the duplicate before it is paid"))
    seen = {id(x) for g in by_ref.values() if len(g) > 1 for x in g}
    by_amount: dict[tuple[str, int], list] = defaultdict(list)
    for r in recs:
        if id(r) not in seen:
            by_amount[(r.party.lower(), to_minor(r.total))].append(r)
    for group in by_amount.values():
        group.sort(key=lambda r: r.date)
        for a, b in zip(group, group[1:]):
            if (date.fromisoformat(b.date) - date.fromisoformat(a.date)).days <= 7:
                dups.append({"party": a.party, "reference": f"{a.number}/{b.number}", "entries": f"{a.date}, {b.date}"})
                findings.append(F(f"Possible duplicate: {a.party} {money(s, a.total)} on {a.date} and {b.date}",
                                  f"{a.number} and {b.number} - different references, same amount",
                                  entity=f"{a.number}/{b.number}"))
    return Out(f"{len(recs)} {'customer' if kind == 'sales' else 'supplier'} invoice(s) checked; "
               f"{len(dups)} duplicate group(s)\n" + lines_table(dups, ["party", "reference", "entries"]),
               data={"duplicates": dups}, findings=findings, checks=len(recs))


@accounting_tool("detect_duplicate_payment", "Find payments to the same supplier for the same amount within a "
                 "week, and supplier payments that no invoice explains.", R,
                 {"period": PERIOD, "start": DATE, "end": DATE})
async def detect_duplicate_payment(s, args, ctx) -> Out:
    start, end = period_range(s, args)
    lo = (date.fromisoformat(start) - timedelta(days=7)).isoformat()
    journals = await _journals(s, lo, end)
    banks = _bank_codes(s)
    suppliers = await s.provider.contacts("supplier")
    payments = []
    for j in journals.values():
        out = sum(l.credit for l in j["lines"] if l.account in banks)
        if out <= 0:
            continue
        party = _supplier_in(suppliers, j["description"])
        if not party:
            continue
        to_ap = any(l.account == "2000" and l.debit for l in j["lines"])
        payments.append({"journal": j["journal"], "date": j["date"], "party": party, "amount": round(out, 2),
                         "allocated": j["source"] == "payment", "to_ap": to_ap, "description": j["description"]})
    payments.sort(key=lambda p: p["date"])
    findings, dup_journals = [], set()
    for i, a in enumerate(payments):
        for b in payments[i + 1:]:
            if b["party"] == a["party"] and abs(b["amount"] - a["amount"]) < 0.005 and \
                    (date.fromisoformat(b["date"]) - date.fromisoformat(a["date"])).days <= 7 and b["date"] >= start:
                dup_journals.update({a["journal"], b["journal"]})
                findings.append(F(f"Payment of {money(s, a['amount'])} to {a['party']} looks like a duplicate",
                                  f"Paid {a['date']} ({a['journal']}) and again {b['date']} ({b['journal']})",
                                  severity="high", entity=f"{a['party']} {b['journal']}",
                                  action="ask the supplier to refund or credit the second payment"))
    bills = await s.provider.invoices("purchase", "all")
    for p in payments:
        if p["date"] < start or p["allocated"] or not p["to_ap"] or p["journal"] in dup_journals:
            continue
        explained = [b for b in bills if b.party == p["party"] and abs(b.total - p["amount"]) < 0.005]
        if not explained:
            findings.append(F(f"Supplier payment {money(s, p['amount'])} to {p['party']} has no matching invoice",
                              f"{p['date']} {p['description']} ({p['journal']}) - posted to payables with no bill",
                              severity="high", entity=f"{p['party']} {p['journal']}",
                              action="get the invoice (check the supplier statement) or recover the payment"))
    return Out(f"{len(payments)} supplier payment(s) checked {start} to {end}",
               data={"payments": payments}, findings=findings, checks=len(payments))


# -- journals and coding ---------------------------------------------------------------------------

_SUSPECT_WORDS = re.compile(r"\b(adjust|adjustment|correction|per md|per ceo|plug|balancing|misc|sundry|"
                            r"write[- ]?off|reverse|round)\b", re.I)


@accounting_tool("find_unusual_journal", "Journal testing: weekend or after-hours postings, round amounts, manual "
                 "entries to revenue or cash, unusual users, 'adjustment'-type narrations, outsized amounts.", R,
                 {"period": PERIOD, "start": DATE, "end": DATE})
async def find_unusual_journal(s, args, ctx) -> Out:
    start, end = period_range(s, args)
    journals = await _journals(s, start, end)
    types = await _types(s)
    authors = defaultdict(int)
    sizes = []
    for j in journals.values():
        authors[j["author"] or "?"] += 1
        sizes.append(sum(l.debit for l in j["lines"]))
    median = statistics.median(sizes) if sizes else 0
    total_j = max(1, len(journals))
    flagged, findings = [], []
    for j in journals.values():
        reasons = []
        amount = sum(l.debit for l in j["lines"])
        d = date.fromisoformat(j["date"])
        manual = j["source"] in ("manual", "")
        if d.weekday() >= 5:
            reasons.append("posted on a weekend")
        try:
            created = datetime.fromisoformat(j["created_at"]) if j["created_at"] else None
        except ValueError:
            created = None
        if created and (created.hour < 6 or created.hour >= 21):
            reasons.append(f"entered at {created:%H:%M}")
        if manual and amount >= 10000 and to_minor(amount) % 100000 == 0:
            reasons.append("round amount")
        if manual and any(types.get(l.account) == "income" for l in j["lines"]):
            reasons.append("manual entry to revenue")
        if manual and any(l.account in _bank_codes(s) for l in j["lines"]):
            reasons.append("manual entry to cash")
        if _SUSPECT_WORDS.search(j["description"]):
            reasons.append(f"narration '{j['description'][:30]}'")
        if authors[j["author"] or "?"] / total_j < 0.05 and (j["author"] or "") not in ("", "system", "agent"):
            reasons.append(f"posted by {j['author']} (rare user)")
        if median and amount > 5 * median and manual:
            reasons.append("far larger than usual")
        if len(reasons) >= 2 and manual:
            flagged.append({"journal": j["journal"], "date": j["date"], "amount": money(s, amount),
                            "description": j["description"][:40], "why": "; ".join(reasons)})
            findings.append(F(f"Unusual journal {j['journal']}: {j['description'][:40]} {money(s, amount)}",
                              "; ".join(reasons), severity="high" if len(reasons) >= 3 else "review",
                              entity=j["journal"], action="get support and approval for this entry"))
    return Out(f"{len(journals)} journal(s) tested {start} to {end}; {len(flagged)} unusual\n"
               + lines_table(flagged, ["journal", "date", "amount", "description", "why"]),
               data={"flagged": flagged}, findings=findings, checks=len(journals))


@accounting_tool("find_misclassification", "Spot transactions coded to an account their description does not "
                 "fit (Microsoft 365 in repairs, an Uber ride in advertising).", R,
                 {"period": PERIOD, "start": DATE, "end": DATE})
async def find_misclassification(s, args, ctx) -> Out:
    start, end = period_range(s, args)
    types = await _types(s)
    names = await account_names(s)
    lines = [l for l in await s.provider.general_ledger(start, end, None, 5000)
             if types.get(l.account) in ("expense", "income") and (l.debit or l.credit)]
    findings, rows = [], []
    for l in lines:
        hint = hint_for(l.description)
        if not hint or hint[1] < 0.8:
            continue
        expected = await account_code(s, hint[0])
        if expected is None or expected == l.account:
            continue
        rows.append({"journal": l.journal, "date": l.date, "description": l.description[:40],
                     "coded": names.get(l.account, l.account), "expected": names.get(expected, expected)})
        findings.append(F(f"{l.description[:40]} coded to {names.get(l.account, l.account)}",
                          f"{l.journal} {l.date} {money(s, l.debit or l.credit)} - usually "
                          f"{names.get(expected, expected)}", entity=l.journal,
                          action=f"reclass to {expected} {names.get(expected, '')} with a draft journal"))
    return Out(f"{len(lines)} income/expense line(s) checked; {len(rows)} look misclassified\n"
               + lines_table(rows, ["journal", "date", "description", "coded", "expected"]),
               data={"misclassified": rows}, findings=findings, checks=len(lines))


# -- purchasing ----------------------------------------------------------------------------------------

@accounting_tool("match_po_invoice", "Three-way check of supplier invoices against purchase orders: missing POs, "
                 "unknown POs, and invoice amounts outside tolerance of the PO.", R,
                 {"tolerance_pct": {"type": "number", "description": "Default 5."},
                  "po_required_over": {"type": "number", "description": "Net amount above which a PO is expected "
                                                                        "(default 10000)."}})
async def match_po_invoice(s, args, ctx) -> Out:
    tol = float(args.get("tolerance_pct") or 5) / 100
    need_over = float(args.get("po_required_over") or 10000)
    pos = {p["number"]: p for p in await s.provider.purchase_orders()}
    bills = [b for b in await s.provider.invoices("purchase", "all") if b.status != "void"]
    rows, findings = [], []
    seen: set[tuple[str, str]] = set()
    for b in bills:
        # A duplicate entry of the same supplier invoice is reported by
        # detect_duplicate_invoice; flagging its PO variance twice is noise.
        key = (b.party.lower(), _norm_ref(b.reference))
        if b.reference and key in seen:
            continue
        seen.add(key)
        if b.po_number:
            po = pos.get(b.po_number)
            if po is None:
                rows.append({"bill": b.number, "po": b.po_number, "result": "PO not found"})
                findings.append(F(f"{b.party} {b.reference or b.number} quotes unknown {b.po_number}",
                                  entity=b.number, action="confirm the order"))
                continue
            var = (b.subtotal - po["total"]) / po["total"] if po["total"] else 0
            result = "matched" if abs(var) <= tol else f"{var:+.0%} vs PO"
            rows.append({"bill": b.number, "po": b.po_number, "result": result})
            if abs(var) > tol:
                findings.append(F(f"{b.party} {b.reference or b.number} is {abs(var):.0%} "
                                  f"{'over' if var > 0 else 'under'} {b.po_number}",
                                  f"Invoice net {money(s, b.subtotal)} vs PO {money(s, po['total'])}",
                                  entity=f"{b.number} {b.po_number}", action="approve the variance or query it"))
        elif b.subtotal > need_over:
            rows.append({"bill": b.number, "po": "-", "result": "no PO"})
            findings.append(F(f"{b.party} {b.reference or b.number} {money(s, b.total)} has no purchase order",
                              entity=b.number, action="get the order approval", severity="info"))
    return Out(f"{len(bills)} bill(s) checked against {len(pos)} PO(s)\n" + lines_table(rows, ["bill", "po", "result"]),
               data={"results": rows}, findings=findings, checks=len(bills))


_STMT_LINE = re.compile(r"(?P<date>\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{4}-\d{2}-\d{2})\s+(?P<ref>[A-Z0-9][A-Z0-9/-]*)\s+"
                        r"(?P<desc>.*?)\s+(?P<amt>[\d,]+\.\d{2})(?:\s+(?P<amt2>[\d,]+\.\d{2}))?\s*$")


def _statement_lines(path: str) -> list[dict[str, Any]]:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".csv", ".xlsx", ".xlsm"):
        out = []
        for r in read_table(path):
            low = {str(k).lower(): v for k, v in r.items()}
            ref = str(low.get("reference") or low.get("ref") or low.get("invoice") or "")
            dr = low.get("debit") or low.get("invoice amount") or ""
            cr = low.get("credit") or low.get("payment") or ""
            try:
                out.append({"date": parse_date(low.get("date")), "ref": ref, "desc": str(low.get("description") or ""),
                            "debit": float(to_minor(dr) / 100) if dr else 0.0,
                            "credit": float(to_minor(cr) / 100) if cr else 0.0})
            except LedgerError:
                continue
        return out
    from .documents import text_of
    out = []
    for line in text_of(path).splitlines():
        m = _STMT_LINE.search(line.strip())
        if not m:
            continue
        try:
            when = parse_date(m.group("date"))
        except LedgerError:
            continue
        amt = to_minor(m.group("amt")) / 100
        desc = m.group("desc").lower()
        is_payment = "payment" in desc or "receipt" in desc or m.group("ref").upper() in ("PMT", "PAY")
        out.append({"date": when, "ref": m.group("ref"), "desc": m.group("desc"),
                    "debit": 0.0 if is_payment else amt, "credit": amt if is_payment else 0.0})
    return out


@accounting_tool("supplier_statement_reconcile", "Reconcile a supplier's statement (PDF or CSV) to our payables: "
                 "invoices on the statement we do not have, bills we have that they do not, amount differences.", R,
                 {"supplier": {"type": "string"}, "path": PATH}, ["supplier", "path"])
async def supplier_statement_reconcile(s, args, ctx) -> Out:
    path = resolve_in_client(s, args["path"])
    stmt = _statement_lines(path)
    if not stmt:
        return Out("", ok=False, error=f"could not read any statement lines from {rel(s, path)}")
    sup = args["supplier"].strip().lower()
    ours = [b for b in await s.provider.invoices("purchase", "all") if sup in b.party.lower() and b.status != "void"]
    ours_by_ref = {_norm_ref(b.reference): b for b in ours if b.reference}
    theirs = [l for l in stmt if l["debit"]]
    findings, rows = [], []
    for l in theirs:
        b = ours_by_ref.get(_norm_ref(l["ref"]))
        if b is None:
            rows.append({"ref": l["ref"], "date": l["date"], "theirs": money(s, l["debit"]), "ours": "MISSING"})
            findings.append(F(f"{args['supplier']} invoice {l['ref']} {money(s, l['debit'])} is not in our books",
                              f"On their statement dated {l['date']}. It may explain an unallocated payment.",
                              severity="high", entity=f"{args['supplier']} {l['ref']}",
                              action="request a copy invoice and draft the bill"))
        elif abs(b.total - l["debit"]) >= 0.005:
            rows.append({"ref": l["ref"], "date": l["date"], "theirs": money(s, l["debit"]), "ours": money(s, b.total)})
            findings.append(F(f"{args['supplier']} {l['ref']}: statement {money(s, l['debit'])} vs ours {money(s, b.total)}",
                              entity=f"{args['supplier']} {l['ref']}", action="query the difference"))
        else:
            rows.append({"ref": l["ref"], "date": l["date"], "theirs": money(s, l["debit"]), "ours": "agrees"})
    their_refs = {_norm_ref(l["ref"]) for l in theirs}
    for b in ours:
        if b.reference and _norm_ref(b.reference) not in their_refs and b.date <= max(l["date"] for l in stmt):
            rows.append({"ref": b.reference, "date": b.date, "theirs": "NOT ON STATEMENT", "ours": money(s, b.total)})
            findings.append(F(f"Our bill {b.number} ({b.reference}) is not on {args['supplier']}'s statement",
                              entity=b.number, action="check it is theirs and not a duplicate"))
    stmt_balance = round(sum(l["debit"] - l["credit"] for l in stmt), 2)
    return Out(f"{args['supplier']} statement {rel(s, path)}: {len(theirs)} invoice(s), balance per statement "
               f"{money(s, stmt_balance)}\n" + lines_table(rows, ["ref", "date", "theirs", "ours"]),
               data={"lines": rows, "statement_balance": stmt_balance, "path": path},
               findings=findings, checks=len(theirs) + len(ours))


# -- balances --------------------------------------------------------------------------------------------

@accounting_tool("review_suspense", "Suspense, clearing and unallocated accounts must be zero at period end.", R,
                 {"as_of": DATE})
async def review_suspense(s, args, ctx) -> Out:
    cutoff = as_of(s, args)
    accts = [a for a in await s.provider.accounts()
             if re.search(r"suspense|clearing|unallocated|unidentified", a.name, re.I)]
    findings, rows = [], []
    for a in accts:
        bal = await s.provider.balance(a.code, as_of=cutoff)
        rows.append({"account": f"{a.code} {a.name}", "balance": money(s, bal)})
        if abs(bal) >= 0.005:
            items = [l for l in await s.provider.general_ledger(None, cutoff, a.code, 200)]
            detail = "; ".join(f"{l.date} {l.description[:30]} {money(s, l.debit - l.credit)}" for l in items[:5])
            findings.append(F(f"{a.name} holds {money(s, abs(bal))} at {cutoff}", detail, entity=a.code,
                              action="identify each item and clear it with a draft journal"))
    return Out(f"{len(accts)} suspense/clearing account(s) at {cutoff}\n" + lines_table(rows, ["account", "balance"]),
               data={"accounts": rows}, findings=findings, checks=len(accts))


@accounting_tool("check_control_accounts", "Receivables and payables control accounts vs the open invoices and "
                 "bills behind them, plus unreconciled bank lines.", R, {"as_of": DATE})
async def check_control_accounts(s, args, ctx) -> Out:
    cutoff = as_of(s, args)
    rows, findings = [], []
    for code, kind, label in (("1100", "sales", "Receivables"), ("2000", "purchase", "Payables")):
        try:
            control = await s.provider.balance(code, as_of=cutoff)
        except Exception:  # noqa: BLE001 - a file provider may not know the control account
            continue
        detail = round(sum(i.outstanding for i in await s.provider.invoices(kind, "all")
                           if i.date <= cutoff and i.status == "open"), 2)
        diff = round(control - detail, 2)
        rows.append({"control": label, "ledger": money(s, control), "open items": money(s, detail),
                     "difference": money(s, diff)})
        if abs(diff) >= 0.005:
            findings.append(F(f"{label} control differs from open {'invoices' if kind == 'sales' else 'bills'} by "
                              f"{money(s, diff)}", f"Ledger {money(s, control)} vs items {money(s, detail)} at {cutoff}",
                              severity="high", entity=code,
                              action="find the entries posted to the control account without a document"))
    unrec = await s.provider.bank_transactions(None, None, cutoff, unreconciled_only=True)
    if unrec:
        findings.append(F(f"{len(unrec)} bank entr{'y remains' if len(unrec) == 1 else 'ies remain'} unreconciled",
                          "; ".join(f"{l.date} {l.description[:30]} {money(s, l.amount)}" for l in unrec[:6]),
                          entity="bank", action="match, categorise or query each line"))
    rows.append({"control": "Bank", "ledger": "", "open items": f"{len(unrec)} unreconciled line(s)", "difference": ""})
    return Out(f"Control accounts at {cutoff}\n" + lines_table(rows, ["control", "ledger", "open items", "difference"]),
               data={"controls": rows, "unreconciled": len(unrec)}, findings=findings, checks=3)


@accounting_tool("review_accruals_prepayments", "Suggest accruals (regular costs with no bill this period) and "
                 "prepayments (annual or unusually large costs expensed in one month), with amounts.", R,
                 {"period": PERIOD})
async def review_accruals_prepayments(s, args, ctx) -> Out:
    period = args.get("period") or s.ws.period
    start, end = s.ws.period_bounds(period)
    months = [period]
    y, m = int(period[:4]), int(period[5:7])
    for _ in range(2):
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        months.insert(0, f"{y}-{m:02d}")
    types = await _types(s)
    names = await account_names(s)
    first_start = s.ws.period_bounds(months[0])[0]
    # Keyed by VENDOR, not account: a cost coded to a different account this
    # month (a misclassification) is still a cost that arrived - not a
    # missing one to accrue.
    seen: dict[str, dict[str, Any]] = {}
    for l in await s.provider.general_ledger(first_start, end, None, 5000):
        if types.get(l.account) != "expense" or not l.debit:
            continue
        vendor = re.split(r"\s+-\s+|\s{2,}", l.description.upper())[0].strip()[:30]
        v = seen.setdefault(vendor, {"months": defaultdict(float), "account": l.account, "text": ""})
        v["months"][l.date[:7]] += l.debit
        if l.date[:7] == period:
            v["account"], v["text"] = l.account, l.description
        elif not v["text"]:
            v["account"] = l.account
    suggestions, findings = [], []
    for vendor, v in seen.items():
        by_month = v["months"]
        acct = v["account"]
        prior = [by_month.get(mm, 0.0) for mm in months[:-1]]
        current = by_month.get(period, 0.0)
        if all(prior) and not current and not re.search(r"SALAR|WAGE", vendor):
            amount = round(sum(prior) / len(prior), 2)
            suggestions.append({"type": "accrual", "account": acct, "vendor": vendor, "amount": amount})
            findings.append(F(f"No {vendor.title()} cost in {period} - accrue about {money(s, amount)}?",
                              f"{names.get(acct, acct)} had {', '.join(money(s, p) for p in prior)} in the prior months",
                              entity=f"{acct} {vendor}", action=f"draft an accrual Dr {acct} / Cr 2400 Accruals, "
                                                               "reversing next period"))
        nonzero = [p for p in prior if p]
        avg_prior = sum(nonzero) / len(nonzero) if nonzero else 0
        annual = re.search(r"ANNUAL|YEARLY|12 MONTHS|PREMIUM|RENEWAL", v["text"].upper())
        if current >= 1000 and (annual or (avg_prior and current > 3 * avg_prior and current > 10000)):
            monthly = round(current / 12, 2)
            suggestions.append({"type": "prepayment", "account": acct, "vendor": vendor, "amount": current,
                                "monthly": monthly})
            findings.append(F(f"{vendor.title()} {money(s, current)} expensed in {period} looks like a prepayment",
                              f"Hold 11/12 ({money(s, current - monthly)}) as a prepayment and release "
                              f"{money(s, monthly)} a month", entity=f"{acct} {vendor}",
                              action="draft Dr 1200 Prepayments / Cr expense"))
    return Out(f"Accruals/prepayments review for {period}: {len(suggestions)} suggestion(s)\n"
               + lines_table(suggestions, ["type", "account", "vendor", "amount"]),
               data={"suggestions": suggestions}, findings=findings, checks=len(seen))


# -- payroll -------------------------------------------------------------------------------------------------

@accounting_tool("payroll_review", "Payroll register vs last period and vs the ledger: big changes per employee, "
                 "joiners and leavers, shared bank accounts, wages expense vs register.", R,
                 {"period": PERIOD, "variance_pct": {"type": "number", "description": "Default 20."}})
async def payroll_review(s, args, ctx) -> Out:
    period = args.get("period") or s.ws.period
    y, m = int(period[:4]), int(period[5:7])
    prior = f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"
    now_rows = await s.provider.payroll(period)
    if not now_rows:
        return Out(f"No payroll register for {period}.", data={"period": period})
    before = {r["employee_id"]: r for r in await s.provider.payroll(prior)}
    threshold = float(args.get("variance_pct") or 20)
    findings, rows = [], []
    for r in now_rows:
        p = before.get(r["employee_id"])
        change = pct_change(r["gross"], p["gross"]) if p else None
        rows.append({"employee": f"{r['employee_id']} {r['name']}", "gross": money(s, r["gross"]),
                     "prior": money(s, p["gross"]) if p else "new", "change": f"{change:+.0f}%" if change is not None else ""})
        if p is None:
            findings.append(F(f"New employee {r['name']} ({r['employee_id']}) in {period} payroll",
                              f"Gross {money(s, r['gross'])}", severity="info", entity=r["employee_id"],
                              action="confirm the appointment letter and details"))
        elif change is not None and abs(change) > threshold:
            findings.append(F(f"{r['name']} gross pay {change:+.0f}% vs {prior}",
                              f"{money(s, p['gross'])} -> {money(s, r['gross'])}", entity=r["employee_id"],
                              action="confirm the increase, overtime or bonus is approved"))
    for eid, p in before.items():
        if eid not in {r["employee_id"] for r in now_rows}:
            findings.append(F(f"{p['name']} ({eid}) was paid in {prior} but not {period}", severity="info",
                              entity=eid, action="confirm the termination"))
    by_bank = defaultdict(list)
    for r in now_rows:
        if r["bank_account"]:
            by_bank[r["bank_account"]].append(r["name"])
    for acct, people in by_bank.items():
        if len(people) > 1:
            findings.append(F(f"{' and '.join(people)} are paid into the same bank account",
                              f"Account {acct}", severity="high", entity=acct,
                              action="verify both employees exist and own the account"))
    gross = round(sum(r["gross"] for r in now_rows), 2)
    start, end = s.ws.period_bounds(period)
    wages_codes = [a.code for a in await s.provider.accounts() if re.search(r"wage|salar", a.name, re.I)]
    gl = 0.0
    for c in wages_codes:
        gl += await s.provider.balance(c, start=start, as_of=end)
    diff = round(gl - gross, 2)
    if abs(diff) >= 0.005:
        findings.append(F(f"Wages in the ledger differ from the payroll register by {money(s, diff)}",
                          f"Ledger {money(s, gl)} vs register gross {money(s, gross)} for {period}",
                          severity="high", entity=f"payroll {period}", action="find the missing or extra wages entry"))
    return Out(f"Payroll {period}: {len(now_rows)} employee(s), gross {money(s, gross)}, ledger {money(s, gl)}\n"
               + lines_table(rows, ["employee", "gross", "prior", "change"]),
               data={"employees": rows, "gross": gross, "ledger": gl}, findings=findings, checks=len(now_rows) + 2)


# -- documents and tax -----------------------------------------------------------------------------------------

@accounting_tool("find_missing_document", "Transactions without supporting documents: every tax-bearing entry, and "
                 "anything above a threshold.", R,
                 {"period": PERIOD, "start": DATE, "end": DATE,
                  "min_amount": {"type": "number", "description": "Also flag untaxed items above this (default 5000)."}})
async def find_missing_document(s, args, ctx) -> Out:
    start, end = period_range(s, args)
    journals = await _journals(s, start, end)
    tax_code = "2200"
    threshold = float(args.get("min_amount") or 5000)
    taxed, large = [], []
    for j in journals.values():
        # Our own sales invoices ARE the document; payroll, openings and
        # allocations are supported by their registers and bills.
        if j["source"] in ("payroll", "opening", "payment", "transfer", "agent", "invoice") or j["attachments"]:
            continue
        amount = sum(l.debit for l in j["lines"])
        has_tax = any(l.account == tax_code for l in j["lines"])
        row = {"journal": j["journal"], "date": j["date"], "description": j["description"][:40], "amount": money(s, amount)}
        if has_tax:
            taxed.append(row)
        elif amount >= threshold:
            large.append(row)
    label = s.ws.profile().tax_label
    findings = []
    if taxed:
        findings.append(F(f"{len(taxed)} {label} transaction{'s' if len(taxed) != 1 else ''} missing support",
                          "; ".join(f"{r['date']} {r['description']} {r['amount']}" for r in taxed),
                          entity=f"{label} support {start[:7]}",
                          action=f"obtain valid tax invoices or reverse the {label} claimed"))
    if large:
        findings.append(F(f"{len(large)} transaction(s) over {money(s, threshold)} without a document",
                          "; ".join(f"{r['date']} {r['description']} {r['amount']}" for r in large[:8]),
                          entity=f"support {start[:7]}", action="attach or request the documents"))
    return Out(f"{len(journals)} journal(s) checked {start} to {end}: {len(taxed)} tax-bearing and {len(large)} large "
               f"without support\n" + lines_table(taxed + large, ["journal", "date", "description", "amount"]),
               data={"taxed": taxed, "large": large}, findings=findings, checks=len(journals))


@accounting_tool("check_tax", "Sales tax / VAT review for a period: output vs input tax, the control account, "
                 "unusual rates and entries without support.", R, {"period": PERIOD, "start": DATE, "end": DATE})
async def check_tax(s, args, ctx) -> Out:
    start, end = period_range(s, args)
    prof = s.ws.profile()
    if not s.provider.supports("tax_summary"):
        return Out("", ok=False, error=f"{s.provider.display_name} cannot summarise tax")
    t = await s.provider.tax_summary(start, end)
    journals = await _journals(s, start, end)
    findings, odd = [], []
    for j in journals.values():
        tax = sum(l.debit + l.credit for l in j["lines"] if l.account == "2200")
        if not tax:
            continue
        net = sum(l.debit + l.credit for l in j["lines"] if l.account not in ("2200", *_bank_codes(s), "1100", "2000"))
        rate = round(tax / net * 100, 1) if net else 0
        if prof.tax_rate and abs(rate - prof.tax_rate) > 0.6:
            odd.append({"journal": j["journal"], "description": j["description"][:40], "rate": f"{rate}%"})
    if odd:
        findings.append(F(f"{len(odd)} entr{'y' if len(odd) == 1 else 'ies'} with an unusual {prof.tax_label} rate",
                          "; ".join(f"{o['journal']} {o['description']} {o['rate']}" for o in odd[:8]),
                          entity=f"{prof.tax_label} rates {start[:7]}", action="check the tax code on each"))
    text = (f"{prof.tax_label} {start} to {end} (standard {prof.tax_rate:g}%): on sales {money(s, t['tax_on_sales'])}, "
            f"on purchases {money(s, t['tax_on_purchases'])}, net {'payable' if t['net_due'] >= 0 else 'refundable'} "
            f"{money(s, abs(t['net_due']))}. Sales net {money(s, t['sales_net'])}, purchases net "
            f"{money(s, t['purchases_net'])}.\n{prof.verify}")
    return Out(text, data={"summary": t, "unusual_rates": odd}, findings=findings, checks=len(journals) + 1)


@accounting_tool("monitor_deadlines", "Upcoming filing and payment deadlines for the client (typical dates from "
                 "the jurisdiction profile, plus the accountant's own).", R,
                 {"days": {"type": "integer", "description": "Look ahead (default 60)."}, "from_date": DATE})
async def monitor_deadlines(s, args, ctx) -> Out:
    start = date.fromisoformat(parse_date(args["from_date"])) if args.get("from_date") else date.today()
    days = int(args.get("days") or 60)
    items = s.ws.profile().deadlines(start - timedelta(days=15), days + 15)
    for d in s.ws.deadlines:
        try:
            due = parse_date(d.get("due"))
        except LedgerError:
            continue
        items.append({"name": d.get("name", "Deadline"), "due": due, "period_end": d.get("period_end", ""),
                      "note": d.get("note", ""), "basis": "set by the accountant"})
    items.sort(key=lambda d: d["due"])
    findings = []
    for d in items:
        left = (date.fromisoformat(d["due"]) - start).days
        d["days_left"] = left
        if -15 <= left < 0:
            findings.append(F(f"{d['name']} was due {d['due']} ({-left} days ago)", d.get("note", ""), severity="high",
                              entity=f"{d['name']} {d['due']}", action="confirm it was filed"))
        elif 0 <= left <= 14:
            findings.append(F(f"{d['name']} due in {left} days ({d['due']})", f"{d.get('basis', '')}. {d.get('note', '')}",
                              entity=f"{d['name']} {d['due']}", action="prepare the pack"))
    upcoming = [d for d in items if d["days_left"] >= -15]
    return Out(f"Deadlines from {start} (next {days} days):\n"
               + lines_table(upcoming, ["name", "due", "days_left", "basis"]),
               data={"deadlines": upcoming}, findings=findings, checks=len(upcoming))


@accounting_tool("summarise_exceptions", "The exception-driven summary of the client's open issues: what was "
                 "checked and exactly what needs the accountant. Writes Reports/<period> Exceptions.md.", R,
                 {"title": {"type": "string", "description": "e.g. 'Month-End Complete'."}})
async def summarise_exceptions(s, args, ctx) -> Out:
    xs = s.drafts.findings(status="open", limit=500)
    drafts = s.drafts.list(status="draft,review")
    title = args.get("title") or f"{s.ws.client} - {s.ws.period}"
    lines = [f"# {title}", "", s.ws.context_line(s.provider.display_name), ""]
    if xs:
        lines.append(f"## Needs review ({len(xs)})")
        for i, x in enumerate(xs, 1):
            lines.append(f"{i}. **{x['title']}**" + (f" - {x['detail']}" if x["detail"] else "")
                         + (f"  \n   _Suggested:_ {x['action']}" if x["action"] else ""))
    else:
        lines.append("Nothing needs review.")
    if drafts:
        lines += ["", f"## Drafts waiting for approval ({len(drafts)})"]
        lines += [f"- {d['id']} [{d['status']}] {d['title']} ({d['confidence']:.0%})" for d in drafts]
    path = s.ws.path("Reports", f"{s.ws.period} Exceptions.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    short = "\n".join(f"{i}. {x['title']}" for i, x in enumerate(xs[:15], 1))
    return Out(f"{title}\nNeeds review ({len(xs)}):\n{short or 'nothing'}\n{len(drafts)} draft(s) await approval. "
               f"Saved {rel(s, path)}", data={"path": path, "exceptions": len(xs), "drafts": len(drafts)})
