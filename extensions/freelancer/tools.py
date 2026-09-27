"""The freelancer tool set (ToolSpec.category == "freelancer").

Levels come from the Accounting pack's ActionLevel, so the rule reads the
same in both packs: READ never asks, DRAFT runs unasked, POST changes the
books and asks every time. Nothing here is SENSITIVE because nothing here
moves money or sends mail - an issued invoice or a reminder is an .eml in
Outbox that the freelancer sends themselves.

READ   freelance_overview, list_invoices, list_unbilled_time, list_quotes, set_aside_report
DRAFT  log_time, create_quote, accept_quote, draft_invoice, draft_invoice_from_time, discard_invoice_draft,
       draft_reminder, chase_late_payers, add_expense, prepare_year_end_pack
POST   issue_invoice, record_payment, post_expenses
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from agents.core import ToolContext, ToolResult
from agents.tools.base import Tool, make_spec
from extensions.accounting.books import LedgerError, to_minor
from extensions.accounting.permissions import ActionLevel

from .business import Business, BusinessCache, NotABusiness

CATEGORY = "freelancer"
FINAL_NOTICE_DAYS = 45            # past this a reminder is a final notice, and the freelancer hears about it

ITEMS = {"type": "array", "description": "Lines: [{description, quantity, unit_price}].",
         "items": {"type": "object", "properties": {"description": {"type": "string"},
                                                    "quantity": {"type": "number"},
                                                    "unit_price": {"type": "number"}}}}
DATE = {"type": "string", "description": "YYYY-MM-DD. Default today."}


@dataclass
class Out:
    text: str
    data: dict[str, Any] = field(default_factory=dict)
    findings: list[dict[str, Any]] = field(default_factory=list)


def finding(title: str, detail: str = "", *, action: str = "", entity: str = "") -> dict[str, Any]:
    return {"title": title, "detail": detail, "severity": "review", "entity": entity, "action": action}


Fn = Callable[[Business, dict[str, Any]], Out]


@dataclass
class ToolDef:
    name: str
    description: str
    level: ActionLevel
    properties: dict[str, Any]
    required: list[str]
    fn: Fn


REGISTRY: list[ToolDef] = []


def tool(name: str, description: str, level: ActionLevel, properties: Optional[dict[str, Any]] = None,
         required: Optional[list[str]] = None):
    def wrap(fn: Fn) -> Fn:
        REGISTRY.append(ToolDef(name, description, level, dict(properties or {}), list(required or []), fn))
        return fn
    return wrap


class FreelanceTool(Tool):
    def __init__(self, d: ToolDef, businesses: BusinessCache) -> None:
        self.d = d
        self.businesses = businesses
        self.level = d.level
        prefix = {ActionLevel.READ: "[READ] ", ActionLevel.DRAFT: "[DRAFT] ",
                  ActionLevel.POST: "[POST - needs approval] "}[d.level]
        self.spec = make_spec(d.name, prefix + d.description, risk=d.level.risk, category=CATEGORY,
                              properties=d.properties, required=d.required)
        self.always_ask = d.level.always_ask
        self.sensitive = d.level.sensitive
        self.soft_fail = not d.level.always_ask
        self.ask_reason = f"{d.name} changes your books - approve it first" if d.level.always_ask else ""

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            b = self.businesses.get(ctx.workspace)
        except NotABusiness as exc:
            return self.fail(str(exc))
        try:
            with b.lock:
                out = self.d.fn(b, args)
        except (LedgerError, ValueError, KeyError, OSError, TypeError) as exc:
            msg = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
            return self.fail(str(msg))
        text = out.text
        if out.findings:
            text += "\n\nNeeds you:\n" + "\n".join(f"  - {f['title']}" for f in out.findings)
        return self.ok(text, **out.data, findings=[{**f, "source": self.name} for f in out.findings])


def build_tools(businesses: BusinessCache) -> list[FreelanceTool]:
    return [FreelanceTool(d, businesses) for d in REGISTRY]


def _m(b: Business, major: Any) -> str:
    return b.money(to_minor(major))


# -- READ ---------------------------------------------------------------------------------------------

@tool("freelance_overview", "The business at a glance: income this month, unpaid and overdue invoices, "
      "unbilled hours, tax set aside, drafts waiting.", ActionLevel.READ, {"as_of": DATE})
def freelance_overview(b: Business, a: dict[str, Any]) -> Out:
    o = b.overview(a.get("as_of"))
    text = (f"{o['business']} ({o['currency']}), {o['as_of']}: income this month {_m(b, o['month_income'])}, "
            f"expenses {_m(b, o['month_expenses'])}. Unpaid {_m(b, o['unpaid'])} on {o['unpaid_count']} invoice(s), "
            f"{o['overdue_count']} overdue ({_m(b, o['overdue'])}). Unbilled {o['unbilled_hours']:g} h "
            f"({_m(b, o['unbilled_value'])}). Tax set aside this year {_m(b, o['set_aside'])} "
            f"at {o['set_aside_pct']:g}%. Waiting: {o['drafts']} invoice draft(s), {o['expense_drafts']} receipt(s), "
            f"{o['open_quotes']} open quote(s).")
    return Out(text, {"overview": o})


@tool("list_invoices", "Invoices by status: open, paid, overdue or all.", ActionLevel.READ,
      {"status": {"type": "string", "enum": ["open", "paid", "overdue", "all"]}})
def list_invoices(b: Business, a: dict[str, Any]) -> Out:
    status = a.get("status") or "open"
    rows = b.overdue() if status == "overdue" else b.invoices(status)
    lines = [f"{i['number']} {i['party']} {_m(b, i['total'])} due {i['due']} {i['status']}"
             + (f", {_m(b, i['outstanding'])} outstanding" if i["outstanding"] else "")
             + (f", {i['days_late']} days late" if i.get("days_late") else "") for i in rows]
    return Out(f"{len(rows)} {status} invoice(s)" + (":\n" + "\n".join(lines) if lines else "."), {"invoices": rows})


@tool("list_unbilled_time", "Hours logged but not yet on an invoice, optionally for one client.", ActionLevel.READ,
      {"client": {"type": "string"}})
def list_unbilled_time(b: Business, a: dict[str, Any]) -> Out:
    rows = b.unbilled(a.get("client", ""))
    by: dict[str, list[float]] = {}
    for e in rows:
        h, v = by.setdefault(e["client"], [0.0, 0.0])
        by[e["client"]] = [h + e["hours"], v + e["value"]]
    lines = [f"{c}: {h:g} h, {_m(b, v)}" for c, (h, v) in sorted(by.items())]
    return Out("Unbilled: " + ("; ".join(lines) if lines else "nothing."), {"entries": rows})


@tool("list_quotes", "Quotes by status: open, accepted or all.", ActionLevel.READ,
      {"status": {"type": "string", "enum": ["open", "accepted", "all"]}})
def list_quotes(b: Business, a: dict[str, Any]) -> Out:
    rows = b.quotes(a.get("status") or "open")
    lines = [f"{q['number']} {q['client']} {_m(b, q['total'])} valid until {q['valid_until']} {q['status']}"
             for q in rows]
    return Out(f"{len(rows)} quote(s)" + (":\n" + "\n".join(lines) if lines else "."), {"quotes": rows})


@tool("set_aside_report", "How much of this tax year's income has been put aside for tax. Tora does not work "
      "out what is owed; the percentage is the freelancer's own.", ActionLevel.READ, {"year": {"type": "integer"}})
def set_aside_report(b: Business, a: dict[str, Any]) -> Out:
    start, end = b.tax_year(int(a["year"]) if a.get("year") else None)
    r = b.set_asides(start, end)
    return Out(f"Tax year {start} to {end}: {_m(b, r['received'])} received, {_m(b, r['total'])} set aside "
               f"({len(r['rows'])} payment(s)).", {"set_aside": r})


# -- DRAFT --------------------------------------------------------------------------------------------

@tool("log_time", "Record hours worked for a client. Rate defaults to the business's hourly rate.", ActionLevel.DRAFT,
      {"client": {"type": "string"}, "hours": {"type": "number"}, "date": DATE, "note": {"type": "string"},
       "rate": {"type": "number", "description": "Hourly rate, if not the usual one."}}, ["client", "hours"])
def log_time(b: Business, a: dict[str, Any]) -> Out:
    e = b.log_time(a["client"], a["hours"], when=a.get("date"), note=a.get("note", ""), rate=a.get("rate"))
    return Out(f"Logged {e['hours']:g} h for {e['client']} on {e['date']} ({_m(b, e['value'])}).", {"entry": e})


@tool("create_quote", "Write a quote as a PDF in Quotes. Nothing is sent.", ActionLevel.DRAFT,
      {"client": {"type": "string"}, "items": ITEMS, "valid_days": {"type": "integer"}, "notes": {"type": "string"}},
      ["client", "items"])
def create_quote(b: Business, a: dict[str, Any]) -> Out:
    q = b.create_quote(a["client"], a["items"], valid_days=int(a.get("valid_days") or 30), notes=a.get("notes", ""))
    return Out(f"Quote {q['number']} for {q['client']}: {_m(b, q['total'])}, valid until {q['valid_until']}. "
               f"File: {q['file']}", {"quote": q})


@tool("accept_quote", "The client said yes: turn a quote into an invoice draft.", ActionLevel.DRAFT,
      {"number": {"type": "string"}}, ["number"])
def accept_quote(b: Business, a: dict[str, Any]) -> Out:
    d = b.accept_quote(a["number"])
    return Out(f"Invoice draft #{d['id']} for {d['client']} ({_m(b, d['total'])}) from {d['source']}. "
               "Issue it when ready.", {"draft": d})


@tool("draft_invoice", "Draft an invoice from lines. Drafts are not on the books until issued.", ActionLevel.DRAFT,
      {"client": {"type": "string"}, "items": ITEMS, "due_days": {"type": "integer"}, "notes": {"type": "string"}},
      ["client", "items"])
def draft_invoice(b: Business, a: dict[str, Any]) -> Out:
    d = b.draft_invoice(a["client"], a["items"], due_days=a.get("due_days"), notes=a.get("notes", ""))
    return Out(f"Invoice draft #{d['id']} for {d['client']}: {_m(b, d['total'])}.", {"draft": d})


@tool("draft_invoice_from_time", "Draft an invoice from a client's unbilled hours.", ActionLevel.DRAFT,
      {"client": {"type": "string"}}, ["client"])
def draft_invoice_from_time(b: Business, a: dict[str, Any]) -> Out:
    d = b.draft_from_time(a["client"])
    hours = sum(i["quantity"] for i in d["items"])
    return Out(f"Invoice draft #{d['id']} for {d['client']}: {hours:g} h, {_m(b, d['total'])}.", {"draft": d})


@tool("discard_invoice_draft", "Throw away an invoice draft; its hours become unbilled again.", ActionLevel.DRAFT,
      {"draft_id": {"type": "integer"}}, ["draft_id"])
def discard_invoice_draft(b: Business, a: dict[str, Any]) -> Out:
    b.discard_draft(int(a["draft_id"]))
    return Out(f"Draft #{a['draft_id']} discarded.", {"draft_id": int(a["draft_id"])})


@tool("draft_reminder", "Write a payment reminder for one overdue invoice as an email in Outbox. Not sent.",
      ActionLevel.DRAFT, {"number": {"type": "string"}, "as_of": DATE}, ["number"])
def draft_reminder(b: Business, a: dict[str, Any]) -> Out:
    r = b.draft_reminder(a["number"], as_of=a.get("as_of"))
    return Out(f"{r['tone'].title()} reminder for {r['invoice']} ({r['client']}, {r['days_late']} days late) "
               f"is in Outbox: {r['email']}", {"reminder": r})


@tool("chase_late_payers", "Write a reminder for every overdue invoice (at most one a day each). Not sent.",
      ActionLevel.DRAFT, {"as_of": DATE})
def chase_late_payers(b: Business, a: dict[str, Any]) -> Out:
    late = b.overdue(a.get("as_of"))
    done, skipped, findings = [], [], []
    for inv in late:
        if b.reminded_today(inv["number"]):
            skipped.append(inv["number"])
            continue
        r = b.draft_reminder(inv["number"], as_of=a.get("as_of"))
        done.append(r)
        if r["days_late"] > FINAL_NOTICE_DAYS:
            findings.append(finding(f"{r['client']}: {r['invoice']} is {r['days_late']} days late",
                                    f"{_m(b, r['outstanding'])} outstanding; the reminder is a final notice.",
                                    action="call them, or decide whether to send the final notice",
                                    entity=r["client"]))
    if not late:
        return Out("Nobody is late. No reminders needed.", {"reminders": []})
    text = (f"{len(done)} reminder(s) in Outbox for you to send: "
            + ", ".join(f"{r['invoice']} ({r['tone']})" for r in done)
            + (f". Already reminded today: {', '.join(skipped)}." if skipped else "."))
    return Out(text, {"reminders": done, "skipped": skipped}, findings)


@tool("add_expense", "Add a receipt or expense as a draft. Category is an expense account name or code; left out, "
      "a learnt rule or Miscellaneous is used.", ActionLevel.DRAFT,
      {"description": {"type": "string"}, "amount": {"type": "number"}, "category": {"type": "string"},
       "date": DATE, "receipt": {"type": "string", "description": "Path to the receipt file, if there is one."}},
      ["description", "amount"])
def add_expense(b: Business, a: dict[str, Any]) -> Out:
    e = b.add_expense(a["description"], a["amount"], category=a.get("category", ""), when=a.get("date"),
                      receipt=a.get("receipt", ""))
    return Out(f"Expense draft #{e['id']}: {e['description']} {_m(b, e['amount'])} as {e['category']}.",
               {"expense": e})


@tool("prepare_year_end_pack", "Build the year-end pack for the accountant: income, expenses, invoices, set-aside, "
      "a summary PDF - files in Year-end.", ActionLevel.DRAFT, {"year": {"type": "integer",
                                                                         "description": "Year the tax year starts."}})
def prepare_year_end_pack(b: Business, a: dict[str, Any]) -> Out:
    p = b.year_end_pack(int(a["year"]) if a.get("year") else None)
    findings = []
    if p["unpaid_invoices"]:
        findings.append(finding(f"{len(p['unpaid_invoices'])} invoice(s) from the year are still unpaid",
                                ", ".join(p["unpaid_invoices"]), action="chase them or tell your accountant"))
    if p["expenses_without_receipt"]:
        findings.append(finding(f"{len(p['expenses_without_receipt'])} expense(s) have no receipt",
                                action="find the receipts before the accountant asks"))
    return Out(f"Year-end pack for {p['start']} to {p['end']} is in {p['folder']}: net profit {_m(b, p['net_profit'])}, "
               f"set aside {_m(b, p['set_aside'])}.", {"pack": p}, findings)


# -- POST ---------------------------------------------------------------------------------------------

@tool("issue_invoice", "Put an invoice draft on the books, write its PDF, and put the email in Outbox for the "
      "freelancer to send.", ActionLevel.POST, {"draft_id": {"type": "integer"}, "date": DATE}, ["draft_id"])
def issue_invoice(b: Business, a: dict[str, Any]) -> Out:
    inv = b.issue(int(a["draft_id"]), when=a.get("date"))
    return Out(f"Issued {inv['number']} to {inv['party']} for {_m(b, inv['total'])}, due {inv['due']}. "
               f"The email is ready in Outbox: {inv['email']}", {"invoice": inv})


@tool("record_payment", "Record money received against an invoice, and put the set-aside share aside.",
      ActionLevel.POST, {"number": {"type": "string"}, "amount": {"type": "number",
                                                                  "description": "Default: all that is owed."},
                         "date": DATE}, ["number"])
def record_payment(b: Business, a: dict[str, Any]) -> Out:
    p = b.record_payment(a["number"], a.get("amount"), when=a.get("date"))
    return Out(f"{p['number']}: {_m(b, p['received'])} received, {p['status']}. Put {_m(b, p['set_aside'])} aside "
               f"for tax ({p['set_aside_pct']:g}%).", {"payment": p})


@tool("post_expenses", "Post expense drafts to the books (all of them, or the ids given).", ActionLevel.POST,
      {"ids": {"type": "array", "items": {"type": "integer"}}})
def post_expenses(b: Business, a: dict[str, Any]) -> Out:
    posted = b.post_expenses(a.get("ids"))
    total = sum(to_minor(e["amount"]) for e in posted)
    return Out(f"Posted {len(posted)} expense(s), {b.money(total)}.", {"posted": posted})


TOOL_LEVELS = {d.name: d.level.value for d in REGISTRY}
