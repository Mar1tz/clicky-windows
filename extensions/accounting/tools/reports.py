"""Reports and packs: variance, cash-flow forecast, workpapers, reconciliation schedules,
management / tax / audit packs, audit samples.

Everything is written into the client folder (Reports/, Workpapers/,
Audit/) as .xlsx - with live formulas where a reviewer would expect them
- or as CSV files when openpyxl is not installed. These are DRAFT-level:
they create files, never change the books. A tax pack also records a
draft tax return, which only submit_tax_return (SENSITIVE) can file.
"""
from __future__ import annotations

import os
import random
import re
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from ..books import to_major, to_minor
from ..tables import TableError, write_table, write_workbook
from .checks import _journals, _types
from .common import (
    DATE, PERIOD, ActionLevel, F, Out, accounting_tool, as_of, lines_table, money, pct_change, period_range, rel,
)

R, D = ActionLevel.READ, ActionLevel.DRAFT
FIRST_ROW = 4           # write_workbook with a title: title row 1, headers row 3, data from row 4


def save_book(s, path: str, sheets: list[tuple[str, list[str], list[list[Any]]]], *, money_cols=(),
              title: str = "") -> str:
    """xlsx when possible; otherwise one CSV per sheet in a folder of the same name."""
    try:
        return write_workbook(path, sheets, money_columns=money_cols, title=title)
    except TableError:
        folder = os.path.splitext(path)[0]
        os.makedirs(folder, exist_ok=True)
        for name, headers, rows in sheets:
            write_table(os.path.join(folder, f"{re.sub(r'[^A-Za-z0-9 _-]', '', name)}.csv"),
                        [dict(zip(headers, r)) for r in rows], columns=headers)
        return folder


async def _pl_by_account(s, start: str, end: str) -> dict[str, dict[str, Any]]:
    pl = await s.provider.profit_and_loss(start, end)
    out = {}
    for kind in ("income", "expenses"):
        for r in pl[kind]:
            out[r["code"]] = {"name": r["name"], "type": "income" if kind == "income" else "expense",
                              "amount": r["amount"]}
    return out


def _materiality(s, revenue: float) -> float:
    return s.ws.materiality or max(5000.0, round(abs(revenue) * 0.01, 2))


async def variance_rows(s, period: str, threshold: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    start, end = s.ws.period_bounds(period)
    y, m = int(period[:4]), int(period[5:7])
    prior = f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"
    pstart, pend = s.ws.period_bounds(prior)
    cur, prev = await _pl_by_account(s, start, end), await _pl_by_account(s, pstart, pend)
    revenue = sum(v["amount"] for v in cur.values() if v["type"] == "income")
    mat = _materiality(s, revenue)
    rows, findings = [], []
    for code in sorted(set(cur) | set(prev)):
        c, p = cur.get(code, {}), prev.get(code, {})
        name = c.get("name") or p.get("name") or code
        a, b = c.get("amount", 0.0), p.get("amount", 0.0)
        change = pct_change(a, b)
        diff = round(a - b, 2)
        rows.append({"code": code, "account": name, "current": a, "prior": b, "change": diff,
                     "pct": f"{change:+.0f}%" if change is not None else ("new" if a else "")})
        if abs(diff) >= mat and (change is None or abs(change) >= threshold):
            label = f"{change:+.0f}% MoM" if change is not None else "new this month"
            findings.append(F(f"{name} {label} ({money(s, b)} -> {money(s, a)})",
                              f"Change {money(s, diff)}; materiality {money(s, mat)}",
                              severity="review", entity=f"{code} {period}",
                              action="explain the movement in the commentary"))
    return rows, findings


@accounting_tool("generate_variance_report", "Profit and loss by account, this period vs the prior period, with "
                 "material movements flagged for commentary. Saves Reports/<period> Variance.xlsx.", D,
                 {"period": PERIOD, "threshold_pct": {"type": "number", "description": "Flag at or above (default 25)."}})
async def generate_variance_report(s, args, ctx) -> Out:
    period = args.get("period") or s.ws.period
    rows, findings = await variance_rows(s, period, float(args.get("threshold_pct") or 25))
    path = save_book(s, s.ws.path("Reports", f"{period} Variance.xlsx"),
                     [("Variance", ["Code", "Account", "Current", "Prior", "Change", "%"],
                       [[r["code"], r["account"], r["current"], r["prior"], f"=C{i}-D{i}", r["pct"]]
                        for i, r in enumerate(rows, FIRST_ROW)])],
                     money_cols=("Current", "Prior", "Change"), title=f"{s.ws.client} - variance {period}")
    text = lines_table([{**r, "current": money(s, r["current"]), "prior": money(s, r["prior"]),
                         "change": money(s, r["change"])} for r in rows],
                       ["account", "current", "prior", "change", "pct"])
    return Out(f"Variance {period} vs prior month - saved {rel(s, path)}\n{text}",
               data={"path": path, "rows": rows}, findings=findings, checks=len(rows))


async def forecast(s, weeks: int, start_d: date) -> dict[str, Any]:
    opening = sum([await s.provider.balance(b, as_of=start_d.isoformat()) for b in (s.ws.bank_accounts or ["1000"])])
    buckets = [{"week": i + 1, "from": (start_d + timedelta(days=7 * i + 1)).isoformat(), "in": 0.0, "out": 0.0,
                "detail": []} for i in range(weeks)]

    def put(when: date, amount: float, what: str) -> None:
        idx = max(0, (when - start_d).days - 1) // 7
        if 0 <= idx < weeks:
            buckets[idx]["in" if amount > 0 else "out"] += abs(amount)
            buckets[idx]["detail"].append(f"{what} {amount:+,.0f}")

    for inv in await s.provider.invoices("sales", "open"):
        if inv.outstanding <= 0:
            continue
        due = date.fromisoformat(inv.due)
        late = (start_d - due).days
        if late > 60:
            continue                                   # doubtful: do not count on it
        put(max(due, start_d + timedelta(days=1 + max(0, late // 2))), inv.outstanding, inv.number)
    for bill in await s.provider.invoices("purchase", "open"):
        if bill.outstanding > 0:
            put(max(date.fromisoformat(bill.due), start_d + timedelta(days=1)), -bill.outstanding, bill.number)
    # recurring costs and payroll: last 3 months' bank payments by description, on their usual day
    lo = (start_d - timedelta(days=92)).isoformat()
    journals = await _journals(s, lo, start_d.isoformat())
    banks = set(s.ws.bank_accounts or ["1000"])
    recurring: dict[str, list[tuple[date, float]]] = defaultdict(list)
    for j in journals.values():
        if j["source"] not in ("bank", "payroll"):
            continue
        out = sum(l.credit for l in j["lines"] if l.account in banks)
        if out <= 0 or any(l.account == "2000" for l in j["lines"]):
            continue
        key = re.split(r"\s+-\s+", j["description"].upper())[0][:25]
        recurring[key].append((date.fromisoformat(j["date"]), out))
    for key, hits in recurring.items():
        months = {d.strftime("%Y-%m") for d, _ in hits}
        if len(months) < 2:
            continue
        avg = sum(a for _, a in hits) / len(hits)
        day = sorted(d.day for d, _ in hits)[len(hits) // 2]
        d0 = start_d.replace(day=1)
        for k in range(0, weeks // 4 + 2):
            y, m = d0.year + (d0.month - 1 + k) // 12, (d0.month - 1 + k) % 12 + 1
            try:
                when = date(y, m, min(day, 28))
            except ValueError:
                continue
            if when > start_d:
                put(when, -round(avg, 2), key.title())
    bal, rows, low_point = opening, [], (opening, "")
    for b in buckets:
        bal = round(bal + b["in"] - b["out"], 2)
        rows.append({**b, "closing": bal})
        if bal < low_point[0]:
            low_point = (bal, b["from"])
    return {"opening": opening, "weeks": rows, "low_point": low_point}


@accounting_tool("generate_cashflow_forecast", "13-week cash-flow forecast from the bank balance, open invoices "
                 "(by due date, doubtful debts excluded), open bills and recurring costs. Saves "
                 "Reports/<period> Cash forecast.xlsx.", D,
                 {"weeks": {"type": "integer"}, "from_date": DATE,
                  "buffer": {"type": "number", "description": "Flag weeks below this balance (default 0)."}})
async def generate_cashflow_forecast(s, args, ctx) -> Out:
    start_d = date.fromisoformat(args["from_date"]) if args.get("from_date") else date.fromisoformat(s.ws.period_end)
    weeks = max(4, min(52, int(args.get("weeks") or 13)))
    fc = await forecast(s, weeks, start_d)
    buffer = float(args.get("buffer") or 0)
    findings = []
    for w in fc["weeks"]:
        if w["closing"] < buffer:
            findings.append(F(f"Cash forecast dips to {money(s, w['closing'])} in the week of {w['from']}",
                              "; ".join(w["detail"][:6]), severity="high" if w["closing"] < 0 else "review",
                              entity=f"cash {w['from']}", action="chase debtors, delay payments or arrange facilities"))
            break
    r0 = FIRST_ROW
    sheet = [[w["week"], w["from"], w["in"], w["out"],
              (f"={fc['opening']}+C{r0}-D{r0}" if i == 0 else f"=E{r0 + i - 1}+C{r0 + i}-D{r0 + i}"),
              "; ".join(w["detail"])[:250]] for i, w in enumerate(fc["weeks"])]
    path = save_book(s, s.ws.path("Reports", f"{s.ws.period} Cash forecast.xlsx"),
                     [("Forecast", ["Week", "From", "Receipts", "Payments", "Closing", "Detail"], sheet)],
                     money_cols=("Receipts", "Payments", "Closing"),
                     title=f"{s.ws.client} - {weeks}-week cash forecast from {start_d} (opening {fc['opening']:,.2f})")
    text = lines_table([{"week": w["week"], "from": w["from"], "in": money(s, w["in"]), "out": money(s, w["out"]),
                         "closing": money(s, w["closing"])} for w in fc["weeks"]], ["week", "from", "in", "out", "closing"])
    return Out(f"Opening {money(s, fc['opening'])}; lowest {money(s, fc['low_point'][0])}"
               f"{' in week of ' + fc['low_point'][1] if fc['low_point'][1] else ''}. Saved {rel(s, path)}\n{text}",
               data={"path": path, "forecast": fc}, findings=findings, checks=weeks)


@accounting_tool("create_reconciliation_schedule", "Bank reconciliation schedule at period end: balance per "
                 "ledger, statement lines not in the ledger, ledger items not on the statement, and the balance per "
                 "statement - with formulas. Saves Workpapers/<period> Bank rec.xlsx.", D,
                 {"account": {"type": "string"}, "as_of": DATE,
                  "statement_balance": {"type": "number", "description": "Closing balance per the bank statement."}})
async def create_reconciliation_schedule(s, args, ctx) -> Out:
    cutoff = as_of(s, args)
    acct = args.get("account") or (s.ws.bank_accounts or ["1000"])[0]
    ledger_bal = await s.provider.balance(acct, as_of=cutoff)
    lines = await s.provider.bank_transactions(acct, None, cutoff)
    unrec = [l for l in lines if not l.reconciled]
    matched = {l.journal_ref for l in lines if l.reconciled}
    gl = [l for l in await s.provider.general_ledger(None, cutoff, acct, 5000)
          if l.journal not in matched and l.source != "opening"]
    implied = round(ledger_bal + sum(l.amount for l in unrec) - sum(l.debit - l.credit for l in gl), 2)
    stmt = float(args["statement_balance"]) if args.get("statement_balance") is not None else implied
    rows = [["Balance per ledger", cutoff, ledger_bal]]
    rows += [[f"Add/(less): on statement, not in ledger - {l.description[:40]}", l.date, l.amount] for l in unrec]
    rows += [[f"Less/(add): in ledger, not on statement - {l.description[:40]}", l.date, -(l.debit - l.credit)] for l in gl]
    n = len(rows)
    rows.append(["Calculated balance per statement", cutoff, f"=SUM(C4:C{n + 3})"])
    rows.append(["Balance per statement (actual)", cutoff, stmt])
    rows.append(["Difference", "", f"=C{n + 5}-C{n + 4}"])
    path = save_book(s, s.ws.path("Workpapers", f"{s.ws.period} Bank rec {acct}.xlsx"),
                     [("Bank rec", ["Item", "Date", "Amount"], rows)], money_cols=("Amount",),
                     title=f"{s.ws.client} - bank reconciliation {acct} at {cutoff} - prepared by agent, reviewed by: ____")
    diff = round(stmt - implied, 2)
    findings = []
    if abs(diff) >= 0.005:
        findings.append(F(f"Bank reconciliation {acct} does not agree by {money(s, diff)}",
                          f"Statement {money(s, stmt)} vs calculated {money(s, implied)}", severity="high",
                          entity=f"bank rec {acct} {cutoff}", action="find the missing statement line or entry"))
    return Out(f"Bank rec {acct} at {cutoff}: ledger {money(s, ledger_bal)}, {len(unrec)} statement line(s) not in "
               f"the ledger, {len(gl)} ledger item(s) not on the statement, statement {money(s, stmt)}"
               f"{'' if args.get('statement_balance') is not None else ' (implied - give statement_balance to prove it)'}"
               f". Saved {rel(s, path)}", data={"path": path, "difference": diff}, findings=findings,
               checks=len(unrec) + len(gl) + 1)


_AREAS = ("bank", "debtors", "creditors", "accruals", "prepayments", "tax", "suspense", "payroll", "fixed_assets")


@accounting_tool("generate_workpaper", "A working paper for one area (bank, debtors, creditors, accruals, "
                 "prepayments, tax, suspense, payroll, fixed_assets): lead schedule, detail, tickmarks, sign-off. "
                 "Saves Workpapers/<period> <area>.xlsx.", D,
                 {"area": {"type": "string", "description": " | ".join(_AREAS)}, "period": PERIOD}, ["area"])
async def generate_workpaper(s, args, ctx) -> Out:
    area = args["area"].lower().replace(" ", "_")
    if area not in _AREAS:
        return Out("", ok=False, error=f"area must be one of {', '.join(_AREAS)}")
    period = args.get("period") or s.ws.period
    start, end = s.ws.period_bounds(period)
    code = {"bank": "1000", "debtors": "1100", "creditors": "2000", "accruals": "2400", "prepayments": "1200",
            "tax": "2200", "suspense": "1900", "payroll": "2500", "fixed_assets": "1500"}[area]
    bal = await s.provider.balance(code, as_of=end)
    detail: list[list[Any]] = []
    support = 0.0
    if area in ("debtors", "creditors"):
        kind = "sales" if area == "debtors" else "purchase"
        for i in await s.provider.invoices(kind, "open"):
            if i.outstanding > 0 and i.date <= end:
                detail.append([i.number, i.party, i.date, i.due, i.outstanding, "✓ agreed to invoice"])
                support += i.outstanding
    else:
        for l in await s.provider.general_ledger(None, end, code, 1000):
            detail.append([l.journal, l.description[:50], l.date, "", round(l.debit - l.credit, 2),
                           "✓ vouched" if l.attachments else "? no document"])
        support = bal if area not in ("bank",) else bal
    lead = [["Account", code], ["Balance per ledger", bal], ["Balance per supporting schedule", round(support, 2)],
            ["Difference", "=B5-B6"], ["", ""], ["Prepared by", "Tora agent"], ["Prepared on", date.today().isoformat()],
            ["Reviewed by", ""], ["Conclusion", "(reviewer)"]]
    legend = [["✓", "agreed / vouched to document"], ["?", "no document attached - see exceptions"],
              ["Σ", "cast and cross-cast"]]
    path = save_book(s, s.ws.path("Workpapers", f"{period} {area.replace('_', ' ').title()}.xlsx"),
                     [("Lead", ["Item", "Value"], lead),
                      ("Detail", ["Ref", "Description", "Date", "Due", "Amount", "Tick"], detail),
                      ("Tickmarks", ["Mark", "Meaning"], legend)],
                     money_cols=("Amount", "Value"), title=f"{s.ws.client} - {area} - {period}")
    diff = round(bal - support, 2)
    findings = []
    if area in ("debtors", "creditors") and abs(diff) >= 0.005:
        findings.append(F(f"{area.title()} workpaper: ledger differs from schedule by {money(s, diff)}",
                          entity=f"{area} {period}", action="investigate before sign-off", severity="high"))
    unsupported = sum(1 for d in detail if str(d[-1]).startswith("?"))
    return Out(f"{area.title()} workpaper {period}: ledger {money(s, bal)}, schedule {money(s, support)}, "
               f"{len(detail)} item(s), {unsupported} without a document. Saved {rel(s, path)}",
               data={"path": path, "difference": diff}, findings=findings, checks=len(detail) + 1)


def _kpis(pl: dict[str, Any], bs: dict[str, Any], debtors: float, creditors: float, days: int) -> list[list[Any]]:
    rev = pl["total_income"] or 0.0
    cos = sum(e["amount"] for e in pl["expenses"] if e["code"].startswith("5"))
    gp = rev - cos
    cur_assets = sum(a["amount"] for a in bs["assets"] if a["code"][:2] in ("10", "11", "12"))
    cur_liab = sum(l["amount"] for l in bs["liabilities"])
    return [["Revenue", rev], ["Gross profit", gp], ["Gross margin %", round(gp / rev * 100, 1) if rev else ""],
            ["Net profit", pl["net_profit"]], ["Net margin %", round(pl["net_profit"] / rev * 100, 1) if rev else ""],
            ["Debtor days", round(debtors / rev * days, 0) if rev else ""],
            ["Creditor days", round(creditors / cos * days, 0) if cos else ""],
            ["Current ratio", round(cur_assets / cur_liab, 2) if cur_liab else ""]]


@accounting_tool("prepare_management_pack", "Monthly management reporting pack: KPIs, P&L (month, prior, YTD), "
                 "balance sheet, aged debtors and creditors, 13-week cash forecast, variance commentary and open "
                 "exceptions. Saves Reports/<period> Management pack.xlsx and a commentary .md.", D, {"period": PERIOD})
async def prepare_management_pack(s, args, ctx) -> Out:
    period = args.get("period") or s.ws.period
    start, end = s.ws.period_bounds(period)
    ytd_start = s.provider.ledger.fiscal_year_start_for(date.fromisoformat(end)).isoformat() \
        if hasattr(s.provider, "ledger") else start
    y, m = int(period[:4]), int(period[5:7])
    prior = f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"
    pl = await s.provider.profit_and_loss(start, end)
    pl_prior = await s.provider.profit_and_loss(*s.ws.period_bounds(prior))
    pl_ytd = await s.provider.profit_and_loss(ytd_start, end)
    bs = await s.provider.balance_sheet(end)
    debtors = sum(i.outstanding for i in await s.provider.invoices("sales", "open"))
    creditors = sum(i.outstanding for i in await s.provider.invoices("purchase", "open"))
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    kpis = _kpis(pl, bs, debtors, creditors, days)
    var_rows, var_findings = await variance_rows(s, period, 25)
    prior_map = {r["code"]: r["amount"] for k in ("income", "expenses") for r in pl_prior[k]}
    ytd_map = {r["code"]: r["amount"] for k in ("income", "expenses") for r in pl_ytd[k]}
    pl_rows = []
    for k, label in (("income", "Income"), ("expenses", "Expenses")):
        for r in pl[k]:
            pl_rows.append([label, r["code"], r["name"], r["amount"], prior_map.get(r["code"], 0.0),
                            ytd_map.get(r["code"], 0.0)])
    pl_rows.append(["", "", "Net profit", pl["net_profit"], pl_prior["net_profit"], pl_ytd["net_profit"]])
    bs_rows = [[g, a["code"], a["name"], a["amount"]] for g, key in (("Assets", "assets"), ("Liabilities", "liabilities"),
                                                                     ("Equity", "equity")) for a in bs[key]]
    fc = await forecast(s, 13, date.fromisoformat(end))
    fc_rows = [[w["week"], w["from"], w["in"], w["out"], w["closing"]] for w in fc["weeks"]]
    aged = []
    for kind, label in (("sales", "Debtor"), ("purchase", "Creditor")):
        for i in await s.provider.invoices(kind, "open"):
            if i.outstanding > 0:
                aged.append([label, i.party, i.number, i.due, max(0, (date.fromisoformat(end) - date.fromisoformat(i.due)).days),
                             i.outstanding])
    xs = s.drafts.findings(status="open", limit=200)
    commentary = [f"# Management commentary - {s.ws.client} - {period}", "",
                  f"Revenue {money(s, pl['total_income'])} ({pct_change(pl['total_income'], pl_prior['total_income']) or 0:+.0f}% "
                  f"on {prior}); net profit {money(s, pl['net_profit'])}; cash {money(s, fc['opening'])} at {end}, "
                  f"forecast low {money(s, fc['low_point'][0])} over 13 weeks.", "", "## Material movements"]
    commentary += [f"- {f['title']}" for f in var_findings] or ["- None above materiality."]
    commentary += ["", f"## Open exceptions ({len(xs)})"] + [f"- {x['title']}" for x in xs[:20]]
    commentary += ["", "_Draft prepared by the Management Reporting agent - for the accountant's review._"]
    md = s.ws.path("Reports", f"{period} Management commentary.md")
    os.makedirs(os.path.dirname(md), exist_ok=True)
    with open(md, "w", encoding="utf-8") as fh:
        fh.write("\n".join(commentary) + "\n")
    path = save_book(s, s.ws.path("Reports", f"{period} Management pack.xlsx"), [
        ("KPIs", ["Measure", "Value"], kpis),
        ("P&L", ["Section", "Code", "Account", period, prior, "YTD"], pl_rows),
        ("Balance sheet", ["Group", "Code", "Account", end], bs_rows),
        ("Aged", ["Type", "Party", "Number", "Due", "Days overdue", "Outstanding"], aged),
        ("Cash forecast", ["Week", "From", "Receipts", "Payments", "Closing"], fc_rows),
        ("Variance", ["Code", "Account", "Current", "Prior", "Change", "%"],
         [[r["code"], r["account"], r["current"], r["prior"], r["change"], r["pct"]] for r in var_rows]),
        ("Exceptions", ["Severity", "Title", "Detail"], [[x["severity"], x["title"], x["detail"]] for x in xs]),
    ], money_cols=(period, prior, "YTD", end, "Outstanding", "Receipts", "Payments", "Closing", "Current", "Prior",
                   "Change", "Value"), title=f"{s.ws.client} - management pack {period}")
    return Out(f"Management pack {period} saved: {rel(s, path)} and {rel(s, md)}. Revenue {money(s, pl['total_income'])}, "
               f"net profit {money(s, pl['net_profit'])}, {len(var_findings)} material movement(s), {len(xs)} open "
               f"exception(s).", data={"path": path, "commentary": md, "kpis": kpis}, findings=var_findings,
               checks=len(pl_rows) + len(bs_rows))


@accounting_tool("prepare_tax_pack", "Sales tax / VAT pack for a period: summary, output and input listings, "
                 "entries missing support, control account - and a DRAFT return for the accountant. Saves "
                 "Reports/<period> Tax pack.xlsx.", D, {"period": PERIOD, "start": DATE, "end": DATE})
async def prepare_tax_pack(s, args, ctx) -> Out:
    start, end = period_range(s, args)
    prof = s.ws.profile()
    t = await s.provider.tax_summary(start, end)
    journals = await _journals(s, start, end)
    out_rows, in_rows, missing = [], [], []
    for j in journals.values():
        tax_lines = [l for l in j["lines"] if l.account == "2200"]
        if not tax_lines:
            continue
        net = sum(l.debit + l.credit for l in j["lines"] if l.account not in ("2200", "1000", "1100", "2000"))
        for l in tax_lines:
            row = [j["date"], j["journal"], j["description"][:60], round(net, 2), round(l.credit or l.debit, 2),
                   "yes" if j["attachments"] or j["source"] == "invoice" else "NO"]
            (out_rows if l.credit else in_rows).append(row)
            if row[-1] == "NO":
                missing.append(row)
    control = await s.provider.balance("2200", start=start, as_of=end)
    summary = [["Period", f"{start} to {end}"], [f"{prof.tax_label} on sales (output)", t["tax_on_sales"]],
               [f"{prof.tax_label} on purchases (input)", t["tax_on_purchases"]],
               ["Net payable / (refundable)", t["net_due"]], ["Movement on control account", control],
               ["Difference", round(t["net_due"] - control, 2)],
               ["Input tax without support", round(sum(r[4] for r in missing if r in in_rows), 2)],
               ["Basis", prof.verify]]
    deadlines = [d for d in prof.deadlines(date.fromisoformat(end), 90) if "vat" in d["name"].lower()
                 or "tax" in d["name"].lower() or "gst" in d["name"].lower() or "bas" in d["name"].lower()]
    if deadlines:
        summary.append(["Next deadline (typical)", f"{deadlines[0]['name']} {deadlines[0]['due']}"])
    path = save_book(s, s.ws.path("Reports", f"{start[:7]} Tax pack.xlsx"), [
        ("Summary", ["Item", "Value"], summary),
        ("Output tax", ["Date", "Journal", "Description", "Net", "Tax", "Support"], out_rows),
        ("Input tax", ["Date", "Journal", "Description", "Net", "Tax", "Support"], in_rows),
        ("Missing support", ["Date", "Journal", "Description", "Net", "Tax", "Support"], missing),
    ], money_cols=("Net", "Tax", "Value"), title=f"{s.ws.client} - {prof.tax_label} pack {start} to {end}")
    d = s.drafts.add("tax_return", f"{prof.tax_label} return {start} to {end}: net {money(s, t['net_due'])}",
                     {"start": start, "end": end, "summary": t, "pack": path, "jurisdiction": prof.code},
                     status="review", reason="prepared for the accountant; submission is SENSITIVE",
                     task_id=ctx.task_id or "")
    findings = []
    if abs(t["net_due"] - control) >= 0.005:
        findings.append(F(f"{prof.tax_label} control movement differs from the tax summary by "
                          f"{money(s, t['net_due'] - control)}", entity=f"{prof.tax_label} {start[:7]}", severity="high"))
    return Out(f"{prof.tax_label} pack {start} to {end}: output {money(s, t['tax_on_sales'])}, input "
               f"{money(s, t['tax_on_purchases'])}, net {money(s, t['net_due'])}; {len(missing)} entr(ies) without "
               f"support. Draft return {d['id']} awaits review. Saved {rel(s, path)}",
               data={"path": path, "draft": d, "summary": t}, findings=findings, checks=len(out_rows) + len(in_rows))


def _sample(population: list[dict[str, Any]], method: str, size: int, seed: int) -> list[dict[str, Any]]:
    if not population:
        return []
    size = min(size, len(population))
    if method == "high_value":
        return sorted(population, key=lambda r: -abs(r["amount"]))[:size]
    rng = random.Random(seed)
    if method == "mus":
        total = sum(abs(r["amount"]) for r in population) or 1
        interval = total / size
        start = rng.uniform(0, interval)
        picks, cum, target = [], 0.0, start
        for r in population:
            cum += abs(r["amount"])
            while cum >= target and len(picks) < size:
                if r not in picks:
                    picks.append(r)
                target += interval
        return picks
    return rng.sample(population, size)


@accounting_tool("sample_transactions", "Reproducible audit sample: random, high value, or monetary-unit (MUS), "
                 "from expenses, income or journals in a period - each with whether a document is attached.", R,
                 {"population": {"type": "string", "description": "expenses | income | journals"},
                  "method": {"type": "string", "description": "random | high_value | mus"},
                  "size": {"type": "integer"}, "seed": {"type": "integer"}, "period": PERIOD,
                  "start": DATE, "end": DATE})
async def sample_transactions(s, args, ctx) -> Out:
    start, end = period_range(s, args)
    pop_kind = (args.get("population") or "expenses").lower()
    types = await _types(s)
    journals = await _journals(s, start, end)
    pop = []
    for j in journals.values():
        amt = sum(l.debit for l in j["lines"] if pop_kind == "journals" or types.get(l.account) ==
                  ("expense" if pop_kind == "expenses" else "income") or
                  (pop_kind == "income" and types.get(l.account) == "income" and l.credit))
        if pop_kind == "income":
            amt = sum(l.credit for l in j["lines"] if types.get(l.account) == "income")
        if amt:
            pop.append({"journal": j["journal"], "date": j["date"], "description": j["description"][:50],
                        "amount": round(amt, 2), "evidence": "yes" if j["attachments"] or j["source"] == "invoice" else "NO"})
    picks = _sample(pop, (args.get("method") or "random").lower(), int(args.get("size") or 25), int(args.get("seed") or 42))
    missing = [p for p in picks if p["evidence"] == "NO"]
    findings = [F(f"{len(missing)} sampled item(s) have no evidence attached",
                  "; ".join(f"{p['journal']} {p['description']}" for p in missing[:8]), entity=f"sample {start[:7]}",
                  action="request the documents")] if missing else []
    return Out(f"Sampled {len(picks)} of {len(pop)} {pop_kind} ({args.get('method') or 'random'}, seed "
               f"{args.get('seed') or 42})\n" + lines_table([{**p, "amount": money(s, p["amount"])} for p in picks],
                                                            ["journal", "date", "description", "amount", "evidence"]),
               data={"sample": picks, "population": len(pop)}, findings=findings, checks=len(picks))


PBC = ["Trial balance at year/period end", "General ledger export", "Bank statements for every account",
       "Bank reconciliations", "Aged debtors listing", "Aged creditors listing", "Fixed asset register",
       "Payroll reports and payroll tax returns", "Sales tax / VAT returns and reconciliations",
       "Loan agreements and statements", "Lease agreements", "Board / shareholder minutes",
       "Supplier statements (major suppliers)", "Accruals and prepayments schedules", "Stock / inventory count"]


@accounting_tool("prepare_audit_pack", "Audit folder for a period: request list (PBC) with what is already "
                 "available, journal testing, a sample with evidence status and an evidence index. Saves under "
                 "Audit/<period>/.", D, {"period": PERIOD, "start": DATE, "end": DATE,
                                          "sample_size": {"type": "integer"}})
async def prepare_audit_pack(s, args, ctx) -> Out:
    start, end = period_range(s, args)
    folder = s.ws.path("Audit", f"{start[:7]}_{end[:7]}" if start[:7] != end[:7] else start[:7])
    os.makedirs(folder, exist_ok=True)
    have = {f.lower() for _d, _s, files in os.walk(s.ws.root) for f in files}
    statement_lines = await s.provider.bank_transactions(None, start, end)
    def available(item: str) -> str:
        key = item.lower()
        if "bank statement" in key:
            return "yes" if statement_lines else "NO"
        if "bank reconciliation" in key:
            return "yes" if any("bank rec" in f for f in have) else "NO - run create_reconciliation_schedule"
        if "trial balance" in key or "general ledger" in key or "aged" in key:
            return "yes - from the books"
        if "vat" in key or "sales tax" in key:
            return "yes" if any("tax pack" in f for f in have) else "NO - run prepare_tax_pack"
        if "accruals" in key:
            return "yes" if any("accruals" in f or "prepayments" in f for f in have) else "NO"
        if "supplier statements" in key:
            return "yes" if os.listdir(s.ws.path("Documents", "Statements")) else "NO"
        return "requested"
    pbc = [[i + 1, item, available(item), ""] for i, item in enumerate(PBC)]
    unusual = await s.provider.general_ledger(start, end, None, 5000)
    from .checks import find_unusual_journal
    jt = await find_unusual_journal(s, {"start": start, "end": end}, ctx)
    sample = await sample_transactions(s, {"start": start, "end": end, "method": "mus",
                                           "size": int(args.get("sample_size") or 25)}, ctx)
    evidence = [[p["journal"], p["date"], p["description"], p["amount"], p["evidence"]] for p in sample.data["sample"]]
    path = save_book(s, os.path.join(folder, "Audit pack.xlsx"), [
        ("Request list", ["#", "Item", "Available", "Notes"], pbc),
        ("Journal testing", ["Journal", "Date", "Amount", "Description", "Why"],
         [[f["journal"], f["date"], f["amount"], f["description"], f["why"]] for f in jt.data["flagged"]]),
        ("Sample", ["Journal", "Date", "Description", "Amount", "Evidence"], evidence),
    ], money_cols=("Amount",), title=f"{s.ws.client} - audit support {start} to {end}")
    missing = [r for r in pbc if str(r[2]).startswith("NO")]
    return Out(f"Audit pack saved to {rel(s, path)}: {len(PBC)} request items ({len(missing)} not yet available), "
               f"{len(jt.data['flagged'])} unusual journal(s), {len(evidence)} sampled item(s) "
               f"({sum(1 for e in evidence if e[-1] == 'NO')} without evidence). {len(unusual)} ledger lines in scope.",
               data={"path": path, "folder": folder}, findings=jt.findings + sample.findings,
               checks=len(PBC) + jt.checks + sample.checks)
