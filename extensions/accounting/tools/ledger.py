"""READ tools: look at the books. Never change anything, never ask."""
from __future__ import annotations

from datetime import date
from typing import Any

from .common import (
    AMOUNT, DATE, PERIOD, ActionLevel, F, Out, accounting_tool, as_of, lines_table, money,
    parse_date, period_range,
)

R = ActionLevel.READ


@accounting_tool("accounting_context", "The client, period, provider, currency, tax setup, month-end "
                 "status, open drafts and open exceptions. Call this first in any accounting task.", R)
async def accounting_context(s, args, ctx) -> Out:
    info = await s.provider.company_info()
    prof = s.ws.profile()
    drafts = s.drafts.counts()
    open_x = s.drafts.findings(status="open", limit=500)
    lines = [
        s.ws.context_line(s.provider.display_name),
        f"Jurisdiction: {prof.country} ({prof.code}) - {prof.tax_label} {prof.tax_rate:g}% standard, "
        f"fiscal year starts {prof.fiscal_year_start}. {prof.verify}",
        f"Period {s.ws.period}: {s.ws.period_start} to {s.ws.period_end}. Judge balances as of {s.ws.period_end}.",
        f"Provider: {s.provider.display_name} (tier: {s.provider.tier}); can post: "
        f"{', '.join(m for m in ('post_journal', 'post_bill', 'post_invoice', 'post_bank_line') if s.provider.supports(m)) or 'nothing (export only)'}",
        f"Drafts: {drafts or 'none'}; open exceptions: {len(open_x)}",
        f"Confidence policy: {s.ws.confidence_policy().describe()}",
    ]
    if info.get("set_up") is False:
        lines.append("The books are empty - import a bank statement or connect the accounting system.")
    return Out("\n".join(lines), data={"client": s.ws.client, "period": s.ws.period, "company": info,
                                       "open_exceptions": len(open_x), "drafts": drafts})


@accounting_tool("get_trial_balance", "Trial balance as of a date (default: end of the current period).",
                 R, {"as_of": DATE})
async def get_trial_balance(s, args, ctx) -> Out:
    tb = await s.provider.trial_balance(as_of(s, args))
    rows = [{"code": r["code"], "name": r["name"], "debit": money(s, r["debit"]) if r["debit"] else "",
             "credit": money(s, r["credit"]) if r["credit"] else ""} for r in tb["rows"]]
    text = (f"Trial balance as of {tb.get('as_of')}: debits {money(s, tb['total_debit'])}, credits "
            f"{money(s, tb['total_credit'])} - {'BALANCED' if tb['balanced'] else 'DOES NOT BALANCE'}\n"
            + lines_table(rows, ["code", "name", "debit", "credit"], 80))
    findings = [] if tb["balanced"] else [F("Trial balance does not balance",
                                            f"Debits {tb['total_debit']} vs credits {tb['total_credit']}",
                                            severity="high", entity="Trial balance")]
    return Out(text, data={"trial_balance": tb}, findings=findings, checks=1)


@accounting_tool("get_general_ledger", "General ledger lines for a date range and optionally one account.",
                 R, {"start": DATE, "end": DATE, "period": PERIOD,
                     "account": {"type": "string", "description": "Account code or name."},
                     "limit": {"type": "integer", "description": "Max journals (default 200)."}})
async def get_general_ledger(s, args, ctx) -> Out:
    start, end = period_range(s, args)
    gl = await s.provider.general_ledger(start, end, args.get("account") or None, int(args.get("limit") or 200))
    rows = [{"journal": l.journal, "date": l.date, "account": f"{l.account} {l.account_name}",
             "description": l.description[:40], "debit": l.debit or "", "credit": l.credit or ""} for l in gl]
    return Out(f"General ledger {start} to {end}: {len(gl)} line(s)\n"
               + lines_table(rows, ["journal", "date", "account", "description", "debit", "credit"], 60),
               data={"lines": [l.to_dict() for l in gl[:500]]})


@accounting_tool("get_bank_transactions", "Bank statement lines, with which are reconciled and a suggested "
                 "account for the rest.", R,
                 {"start": DATE, "end": DATE, "period": PERIOD,
                  "unreconciled_only": {"type": "boolean"},
                  "account": {"type": "string", "description": "Bank account code (default all)."}})
async def get_bank_transactions(s, args, ctx) -> Out:
    start, end = period_range(s, args) if (args.get("period") or args.get("start") or args.get("end")) \
        else (None, None)
    lines = await s.provider.bank_transactions(args.get("account") or None, start, end,
                                               bool(args.get("unreconciled_only")))
    rows = [{"id": l.id, "date": l.date, "description": l.description[:40], "amount": money(s, l.amount),
             "status": "reconciled" if l.reconciled else ("suggest " + l.suggested_account
                                                          if l.suggested_account else "UNRECONCILED")}
            for l in lines]
    unrec = sum(1 for l in lines if not l.reconciled)
    return Out(f"{len(lines)} bank line(s), {unrec} unreconciled\n"
               + lines_table(rows, ["id", "date", "description", "amount", "status"], 80),
               data={"lines": [l.to_dict() for l in lines[:1000]], "unreconciled": unrec})


async def _invoices(s, kind: str, args: dict[str, Any]) -> list:
    status = (args.get("status") or "open").lower()
    recs = await s.provider.invoices(kind, "all" if status in ("overdue", "all") else status)
    party = (args.get("customer") or args.get("supplier") or "").lower()
    if party:
        recs = [r for r in recs if party in r.party.lower()]
    if status == "overdue":
        cutoff = as_of(s, args)
        recs = [r for r in recs if r.status == "open" and r.due < cutoff and r.outstanding > 0]
    return recs


@accounting_tool("get_customer_invoices", "Sales invoices (accounts receivable).", R,
                 {"status": {"type": "string", "description": "open | overdue | paid | all (default open)."},
                  "customer": {"type": "string"}, "as_of": DATE})
async def get_customer_invoices(s, args, ctx) -> Out:
    recs = await _invoices(s, "sales", args)
    rows = [{"number": r.number, "customer": r.party, "date": r.date, "due": r.due,
             "total": money(s, r.total), "outstanding": money(s, r.outstanding), "status": r.status} for r in recs]
    return Out(f"{len(recs)} customer invoice(s)\n"
               + lines_table(rows, ["number", "customer", "date", "due", "total", "outstanding", "status"]),
               data={"invoices": [r.to_dict() for r in recs]})


@accounting_tool("get_supplier_invoices", "Supplier invoices / bills (accounts payable).", R,
                 {"status": {"type": "string", "description": "open | overdue | paid | all (default open)."},
                  "supplier": {"type": "string"}, "as_of": DATE})
async def get_supplier_invoices(s, args, ctx) -> Out:
    recs = await _invoices(s, "purchase", args)
    rows = [{"number": r.number, "supplier": r.party, "their_ref": r.reference, "po": r.po_number,
             "date": r.date, "due": r.due, "total": money(s, r.total), "outstanding": money(s, r.outstanding)}
            for r in recs]
    return Out(f"{len(recs)} supplier invoice(s)\n"
               + lines_table(rows, ["number", "supplier", "their_ref", "po", "date", "due", "total", "outstanding"]),
               data={"invoices": [r.to_dict() for r in recs]})


@accounting_tool("find_overdue_invoices", "Aged debtors: overdue customer invoices bucketed by days overdue, "
                 "with the accounts that need escalating flagged.", R,
                 {"as_of": DATE, "escalate_days": {"type": "integer", "description": "Flag at or above (default 60)."},
                  "escalate_amount": AMOUNT})
async def find_overdue_invoices(s, args, ctx) -> Out:
    cutoff = date.fromisoformat(as_of(s, args))
    esc_days = int(args.get("escalate_days") or 60)
    esc_amt = float(args.get("escalate_amount") or 50000)
    recs = [r for r in await s.provider.invoices("sales", "all") if r.status == "open" and r.outstanding > 0]
    buckets = {"current": 0.0, "1-30": 0.0, "31-60": 0.0, "61-90": 0.0, "90+": 0.0}
    by_customer: dict[str, dict[str, Any]] = {}
    for r in recs:
        days = (cutoff - date.fromisoformat(r.due)).days
        b = "current" if days <= 0 else "1-30" if days <= 30 else "31-60" if days <= 60 else \
            "61-90" if days <= 90 else "90+"
        buckets[b] += r.outstanding
        c = by_customer.setdefault(r.party, {"customer": r.party, "outstanding": 0.0, "overdue": 0.0,
                                              "oldest_days": 0, "invoices": []})
        c["outstanding"] += r.outstanding
        if days > 0:
            c["overdue"] += r.outstanding
            c["oldest_days"] = max(c["oldest_days"], days)
            c["invoices"].append({"number": r.number, "due": r.due, "days_overdue": days,
                                  "outstanding": r.outstanding})
    overdue = [c for c in by_customer.values() if c["overdue"] > 0]
    overdue.sort(key=lambda c: (-c["oldest_days"], -c["overdue"]))
    findings = []
    for c in overdue:
        c["class"] = ("escalate" if c["oldest_days"] >= esc_days or c["overdue"] >= esc_amt
                      else "firm reminder" if c["oldest_days"] > 30 else "gentle reminder")
        if c["class"] == "escalate":
            findings.append(F(f"{c['customer']} owes {money(s, c['overdue'])}, {c['oldest_days']} days overdue",
                              "Escalate: phone call from the accountant or partner, consider credit hold.",
                              severity="high" if c["oldest_days"] >= esc_days else "review",
                              entity=c["customer"], action="call the customer; consider credit hold"))
    rows = [{"customer": c["customer"], "overdue": money(s, c["overdue"]), "oldest": f"{c['oldest_days']}d",
             "invoices": ", ".join(i["number"] for i in c["invoices"]), "action": c["class"]} for c in overdue]
    text = (f"Aged debtors at {cutoff}: " + ", ".join(f"{k} {money(s, v)}" for k, v in buckets.items())
            + f"\n{len(overdue)} customer(s) overdue\n"
            + lines_table(rows, ["customer", "overdue", "oldest", "invoices", "action"]))
    return Out(text, data={"buckets": buckets, "customers": overdue, "as_of": cutoff.isoformat()},
               findings=findings, checks=len(recs))


@accounting_tool("get_financial_statements", "Profit and loss for a period and the balance sheet at its end.",
                 R, {"period": PERIOD, "start": DATE, "end": DATE})
async def get_financial_statements(s, args, ctx) -> Out:
    start, end = period_range(s, args)
    if not s.provider.supports("profit_and_loss"):
        return Out("", ok=False, error=f"{s.provider.display_name} cannot produce statements; "
                                       "use get_trial_balance instead")
    pl = await s.provider.profit_and_loss(start, end)
    bs = await s.provider.balance_sheet(end)
    lines = [f"PROFIT AND LOSS {start} to {end}"]
    lines += [f"  {i['name']}: {money(s, i['amount'])}" for i in pl["income"]]
    lines.append(f"  Total income {money(s, pl['total_income'])}")
    lines += [f"  {e['name']}: {money(s, e['amount'])}" for e in pl["expenses"]]
    lines.append(f"  Total expenses {money(s, pl['total_expenses'])}")
    lines.append(f"  NET PROFIT {money(s, pl['net_profit'])}")
    lines.append(f"BALANCE SHEET at {end}: assets {money(s, bs['total_assets'])}, liabilities "
                 f"{money(s, bs['total_liabilities'])}, equity {money(s, bs['total_equity'])} - "
                 f"{'balanced' if bs['balanced'] else 'DOES NOT BALANCE'}")
    findings = [] if bs["balanced"] else [F("Balance sheet does not balance", severity="high",
                                            entity="Balance sheet")]
    return Out("\n".join(lines), data={"profit_and_loss": pl, "balance_sheet": bs}, findings=findings, checks=1)


@accounting_tool("list_drafts", "Drafts waiting for review or posting (journals, bills, invoices, bank "
                 "categorisations, payment batches, tax returns, reminders).", R,
                 {"status": {"type": "string", "description": "draft | review | posted | rejected | all "
                                                              "(comma-separated ok; default draft,review)."},
                  "kind": {"type": "string"}})
async def list_drafts(s, args, ctx) -> Out:
    status = args.get("status") or "draft,review"
    ds = s.drafts.list(status="" if status == "all" else status, kind=args.get("kind") or "")
    rows = [{"id": d["id"], "kind": d["kind"], "status": d["status"], "confidence": f"{d['confidence']:.0%}",
             "title": d["title"][:60]} for d in ds]
    return Out(f"{len(ds)} draft(s)\n" + lines_table(rows, ["id", "kind", "status", "confidence", "title"]),
               data={"drafts": ds})


@accounting_tool("list_exceptions", "The client's open exceptions - everything agents have flagged for the "
                 "accountant, most severe first.", R,
                 {"status": {"type": "string", "description": "open | resolved | all (default open)."}})
async def list_exceptions(s, args, ctx) -> Out:
    xs = s.drafts.findings(status=args.get("status") or "open")
    rows = [{"id": x["id"], "severity": x["severity"], "title": x["title"][:70], "entity": x["entity"]} for x in xs]
    return Out(f"{len(xs)} exception(s)\n" + lines_table(rows, ["id", "severity", "title", "entity"], 60),
               data={"exceptions": xs})
