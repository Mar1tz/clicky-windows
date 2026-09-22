"""DRAFT tools: bills, invoices, journals, payment batches, reminders, requests, exceptions."""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta
from typing import Any

from ..books import LedgerError, split_tax, to_major, to_minor
from ..pdfgen import write_pdf
from .bank import account_code, account_names
from .common import (
    AMOUNT, CONFIDENCE, DATE, PATH, ActionLevel, F, Out, accounting_tool, as_of, lines_table, money,
    parse_date, policy, rel,
)

D = ActionLevel.DRAFT

ITEMS = {"type": "array", "description": "Line items.", "items": {"type": "object", "properties": {
    "description": {"type": "string"}, "quantity": {"type": "number"},
    "unit_price": {"type": "number", "description": "Price EXCLUDING tax."},
    "account": {"type": "string"}, "tax_rate": {"type": "number"}}}}
PO_TOLERANCE = 0.05


def _norm_ref(ref: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (ref or "").lower())


async def duplicate_bill(s, supplier: str, reference: str, total: float, when: str) -> list[str]:
    """Existing bills or drafts this invoice would duplicate."""
    hits = []
    sup = supplier.lower().strip()
    for b in await s.provider.invoices("purchase", "all"):
        if b.status == "void" or b.party.lower().strip() != sup:
            continue
        if reference and _norm_ref(b.reference) == _norm_ref(reference):
            hits.append(f"{b.number} (their ref {b.reference}, {b.date})")
        elif abs(b.total - total) < 0.005 and abs((date.fromisoformat(b.date) - date.fromisoformat(when)).days) <= 7:
            hits.append(f"{b.number} (same amount, {b.date})")
    for d in s.drafts.list(status="draft,review,approved", kind="bill"):
        p = d["payload"]
        if p.get("supplier", "").lower().strip() == sup and reference and \
                _norm_ref(p.get("reference", "")) == _norm_ref(reference):
            hits.append(f"draft {d['id']}")
    return hits


def _items_from(args: dict[str, Any], rate: float) -> list[dict[str, Any]]:
    items = [dict(i) for i in (args.get("items") or []) if isinstance(i, dict)]
    if not items and args.get("total") is not None:
        net, _tax = split_tax(to_minor(args["total"]), rate)
        items = [{"description": args.get("description") or "Per invoice", "quantity": 1,
                  "unit_price": to_major(net), "account": args.get("account") or ""}]
    if not items:
        raise ValueError("give items (description, quantity, unit_price excl. tax) or a total")
    return items


def _totals(items: list[dict[str, Any]], rate: float) -> tuple[float, float, float]:
    net = sum(to_minor(float(i.get("quantity") or 1) * float(i.get("unit_price") or 0)) for i in items)
    tax = sum(round(to_minor(float(i.get("quantity") or 1) * float(i.get("unit_price") or 0))
                    * float(i.get("tax_rate") if i.get("tax_rate") is not None else rate) / 100) for i in items)
    return to_major(net), to_major(tax), to_major(net + tax)


@accounting_tool("create_draft_bill", "Draft a supplier bill. Checks for a duplicate, an unknown supplier and "
                 "the purchase order first; applies the confidence policy.", D,
                 {"supplier": {"type": "string"}, "date": DATE, "reference": {"type": "string",
                  "description": "The SUPPLIER's invoice number."}, "items": ITEMS, "total": AMOUNT,
                  "description": {"type": "string"}, "account": {"type": "string"},
                  "po_number": {"type": "string"}, "document": PATH, "due_days": {"type": "integer"},
                  "confidence": CONFIDENCE, "reason": {"type": "string"}},
                 ["supplier", "date", "reference", "confidence"])
async def create_draft_bill(s, args, ctx) -> Out:
    supplier = args["supplier"].strip()
    when = parse_date(args["date"])
    rate = s.ws.profile().tax_rate
    items = _items_from(args, rate)
    net, tax, total = _totals(items, rate)
    findings, notes = [], []
    dups = await duplicate_bill(s, supplier, args["reference"], total, when)
    if dups:
        return Out(f"NOT drafted: {supplier} {args['reference']} {money(s, total)} duplicates {', '.join(dups)}",
                   data={"route": "duplicate", "duplicates": dups},
                   findings=[F(f"Duplicate supplier invoice {supplier} {args['reference']}",
                               f"{money(s, total)} dated {when} matches {', '.join(dups)}", severity="high",
                               entity=f"{supplier} {args['reference']}", action="do not pay twice; confirm with supplier")])
    route = policy(s).route(args["confidence"])
    contacts = {c["name"].lower(): c for c in await s.provider.contacts("supplier")}
    known = contacts.get(supplier.lower())
    if known is None:
        notes.append("new supplier")
        findings.append(F(f"New supplier {supplier}", "Verify the supplier and its bank details before any payment.",
                          entity=supplier, action="verify supplier details"))
        route = "review" if route == "draft" else route
    default = (known or {}).get("default_account") or ""
    for it in items:
        code = await account_code(s, it.get("account") or args.get("account") or default)
        if code is None:
            raise ValueError(f"no account for item {it.get('description')!r}: give an account")
        it["account"] = code
    po = (args.get("po_number") or "").strip()
    if po:
        pos = {p["number"]: p for p in await s.provider.purchase_orders()}
        if po not in pos:
            findings.append(F(f"{supplier} {args['reference']} quotes {po}, which does not exist",
                              entity=po, action="confirm the order"))
            route = "review" if route == "draft" else route
        elif pos[po]["total"] and abs(net - pos[po]["total"]) / pos[po]["total"] > PO_TOLERANCE:
            findings.append(F(f"{supplier} {args['reference']} is {abs(net - pos[po]['total']) / pos[po]['total']:.0%} "
                              f"{'over' if net > pos[po]['total'] else 'under'} {po}",
                              f"Invoice net {money(s, net)} vs PO {money(s, pos[po]['total'])}", entity=po,
                              action="approve the variance or query the supplier"))
            route = "review" if route == "draft" else route
    if route == "ask":
        findings.append(F(f"Coding needed for {supplier} {args['reference']} {money(s, total)}",
                          f"Confidence {float(args['confidence']):.0%}. {args.get('reason') or ''}",
                          entity=f"{supplier} {args['reference']}"))
        return Out(f"Not drafted (confidence too low): {supplier} {args['reference']}", data={"route": "ask"},
                   findings=findings)
    doc = args.get("document") or ""
    d = s.drafts.add("bill", f"Bill {supplier} {args['reference']} {money(s, total)}",
                     {"supplier": supplier, "date": when, "reference": args["reference"], "items": items,
                      "po_number": po, "document": doc, "due_days": int(args.get("due_days") or 30),
                      "net": net, "tax": tax, "total": total},
                     confidence=float(args["confidence"]), status="draft" if route == "draft" else "review",
                     reason="; ".join(filter(None, [args.get("reason") or "", *notes])),
                     source_document=doc, task_id=ctx.task_id or "")
    return Out(f"Drafted {d['id']} ({d['status']}): bill {supplier} {args['reference']} net {money(s, net)} + tax "
               f"{money(s, tax)} = {money(s, total)}", data={"draft": d, "route": route}, findings=findings)


@accounting_tool("create_draft_invoice", "Draft a customer invoice.", D,
                 {"customer": {"type": "string"}, "date": DATE, "items": ITEMS, "due_days": {"type": "integer"},
                  "reference": {"type": "string"}, "confidence": CONFIDENCE},
                 ["customer", "items"])
async def create_draft_invoice(s, args, ctx) -> Out:
    rate = s.ws.profile().tax_rate
    items = _items_from(args, rate)
    net, tax, total = _totals(items, rate)
    when = parse_date(args.get("date") or date.today().isoformat())
    customer = args["customer"].strip()
    known = {c["name"].lower() for c in await s.provider.contacts("customer")}
    findings = [] if customer.lower() in known else [F(f"New customer {customer}", "Check credit terms and details.",
                                                       entity=customer)]
    d = s.drafts.add("invoice", f"Invoice {customer} {money(s, total)}",
                     {"customer": customer, "date": when, "items": items, "due_days": int(args.get("due_days") or 30),
                      "reference": args.get("reference") or "", "net": net, "tax": tax, "total": total},
                     confidence=float(args.get("confidence") or 0.99),
                     status="review" if findings else "draft", task_id=ctx.task_id or "")
    return Out(f"Drafted {d['id']}: invoice to {customer} {money(s, total)} (net {money(s, net)}, tax {money(s, tax)})",
               data={"draft": d}, findings=findings)


@accounting_tool("create_draft_journal", "Draft a journal (accrual, prepayment release, correction, reclass). "
                 "Must balance. Give a clear narration.", D,
                 {"date": DATE, "description": {"type": "string"},
                  "lines": {"type": "array", "items": {"type": "object", "properties": {
                      "account": {"type": "string"}, "debit": {"type": "number"}, "credit": {"type": "number"},
                      "memo": {"type": "string"}}}},
                  "reverse_on": {"type": "string", "description": "Date to auto-reverse (accruals)."},
                  "confidence": CONFIDENCE, "reason": {"type": "string"}},
                 ["date", "description", "lines", "confidence"])
async def create_draft_journal(s, args, ctx) -> Out:
    lines = []
    for l in args["lines"]:
        code = await account_code(s, str(l.get("account", "")))
        if code is None:
            raise ValueError(f"no account matches {l.get('account')!r}")
        dr, cr = to_minor(l.get("debit") or 0), to_minor(l.get("credit") or 0)
        if dr < 0 or cr < 0 or (dr and cr):
            raise ValueError("each line is a positive debit OR a positive credit")
        lines.append({"account": code, "debit": to_major(dr), "credit": to_major(cr), "memo": l.get("memo") or ""})
    dr_total = sum(to_minor(l["debit"]) for l in lines)
    cr_total = sum(to_minor(l["credit"]) for l in lines)
    if dr_total != cr_total or dr_total == 0:
        raise ValueError(f"journal does not balance: debits {to_major(dr_total)} vs credits {to_major(cr_total)}")
    route = policy(s).route(args["confidence"])
    if route == "ask":
        return Out("Not drafted (confidence too low)", data={"route": "ask"},
                   findings=[F(f"Proposed journal needs a decision: {args['description'][:60]}",
                               args.get("reason") or "", entity=args["description"][:40])])
    names = await account_names(s)
    d = s.drafts.add("journal", f"Journal: {args['description'][:70]} ({money(s, to_major(dr_total))})",
                     {"date": parse_date(args["date"]), "description": args["description"], "lines": lines,
                      "reverse_on": parse_date(args["reverse_on"]) if args.get("reverse_on") else ""},
                     confidence=float(args["confidence"]), status="draft" if route == "draft" else "review",
                     reason=args.get("reason") or "", task_id=ctx.task_id or "")
    detail = "\n".join(f"  {l['account']} {names.get(l['account'], '')}: Dr {l['debit'] or ''} Cr {l['credit'] or ''}"
                       for l in lines)
    findings = [] if route == "draft" else [F(f"Review draft journal {d['id']}: {args['description'][:50]}",
                                              args.get("reason") or "", entity=d["id"])]
    return Out(f"Drafted {d['id']} ({route}): {args['description']}\n{detail}", data={"draft": d}, findings=findings)


@accounting_tool("create_draft_payment_batch", "Draft a supplier payment batch from open bills due by a date. "
                 "Duplicates, unknown suppliers and recently changed bank details are held back and flagged. "
                 "Always goes to review; releasing it is a separate SENSITIVE step for the accountant.", D,
                 {"due_by": DATE, "bills": {"type": "array", "items": {"type": "string"},
                                            "description": "Only these bill numbers."},
                  "bank_change_days": {"type": "integer", "description": "Flag bank detail changes within N days (30)."}})
async def create_draft_payment_batch(s, args, ctx) -> Out:
    due_by = parse_date(args["due_by"]) if args.get("due_by") else (date.fromisoformat(s.ws.period_end) +
                                                                   timedelta(days=14)).isoformat()
    only = set(args.get("bills") or [])
    bills = [b for b in await s.provider.invoices("purchase", "open") if b.outstanding > 0]
    contacts = {c["name"].lower(): c for c in await s.provider.contacts("supplier")}
    seen_refs: dict[tuple[str, str], str] = {}
    include, held, findings = [], [], []
    window = int(args.get("bank_change_days") or 30)
    for b in sorted(bills, key=lambda b: b.date):
        if only and b.number not in only:
            continue
        if not only and b.due > due_by:
            continue
        key = (b.party.lower(), _norm_ref(b.reference))
        c = contacts.get(b.party.lower())
        reason = ""
        if b.reference and key in seen_refs:
            reason = f"duplicate of {seen_refs[key]} (their ref {b.reference})"
        elif c is None:
            reason = "supplier not in contacts"
        elif c.get("bank_changed_at"):
            changed = date.fromisoformat(c["bank_changed_at"][:10])
            if (date.fromisoformat(s.ws.period_end) - changed).days <= window:
                reason = f"bank details changed on {changed}"
        elif not c.get("bank_account"):
            reason = "no bank details on file"
        seen_refs.setdefault(key, b.number)
        row = {"number": b.number, "supplier": b.party, "reference": b.reference, "due": b.due,
               "amount": b.outstanding, "bank_account": (c or {}).get("bank_account", "")}
        if reason:
            held.append({**row, "held": reason})
            findings.append(F(f"Payment held: {b.party} {b.number} {money(s, b.outstanding)} - {reason}",
                              severity="high" if "bank" in reason or "duplicate" in reason else "review",
                              entity=b.number, action="verify before paying"))
        else:
            include.append(row)
    total = round(sum(r["amount"] for r in include), 2)
    if not include and not held:
        return Out(f"No open bills due by {due_by}.", data={"route": "empty"})
    d = s.drafts.add("payment_batch", f"Payment batch {len(include)} bill(s) {money(s, total)} due by {due_by}",
                     {"due_by": due_by, "payments": include, "held": held, "total": total},
                     confidence=1.0, status="review", reason="payment batches always need review",
                     task_id=ctx.task_id or "")
    text = (f"Drafted {d['id']} for review: {len(include)} payment(s) totalling {money(s, total)}; "
            f"{len(held)} held back.\n"
            + lines_table([{**r, "amount": money(s, r["amount"])} for r in include],
                          ["number", "supplier", "reference", "due", "amount"])
            + ("\nHeld:\n" + lines_table([{**r, "amount": money(s, r["amount"])} for r in held],
                                         ["number", "supplier", "amount", "held"]) if held else ""))
    return Out(text, data={"draft": d, "payments": include, "held": held}, findings=findings, checks=len(bills))


_TONES = {
    "gentle": ("Friendly reminder: invoice {nums}",
               "Hi {name},\n\nI hope you're well. Our records show the following invoice(s) are now past due:\n\n"
               "{table}\n\nIf payment is already on its way, thank you and please ignore this note. Otherwise, "
               "we'd be grateful if you could settle the balance of {total} at your earliest convenience.\n\n"
               "Kind regards,\n{company}"),
    "firm": ("Overdue account: {total} outstanding",
             "Dear {name},\n\nThe following invoice(s) are more than 30 days overdue:\n\n{table}\n\n"
             "Please arrange payment of {total} within 7 days, or let us know if there is a query on any invoice "
             "so that we can resolve it.\n\nRegards,\n{company}"),
    "final": ("Final reminder before we review your account",
              "Dear {name},\n\nDespite earlier reminders, {total} remains unpaid on the invoice(s) below:\n\n{table}\n\n"
              "Please contact us within 7 days to arrange payment. If we do not hear from you we will need to review "
              "the credit terms on your account.\n\nRegards,\n{company}"),
}


@accounting_tool("draft_payment_reminder", "Write a payment reminder email as a draft in the client's Outbox "
                 "(not sent). Tone: gentle (1-30 days), firm (31-60), final (60+).", D,
                 {"customer": {"type": "string"}, "tone": {"type": "string", "description": "gentle | firm | final"},
                  "as_of": DATE}, ["customer"])
async def draft_payment_reminder(s, args, ctx) -> Out:
    cutoff = as_of(s, args)
    cust = args["customer"].strip().lower()
    recs = [r for r in await s.provider.invoices("sales", "open")
            if cust in r.party.lower() and r.outstanding > 0 and r.due < cutoff]
    if not recs:
        return Out(f"No overdue invoices for {args['customer']} at {cutoff} - no reminder drafted.",
                   data={"route": "nothing"})
    name = recs[0].party
    oldest = max((date.fromisoformat(cutoff) - date.fromisoformat(r.due)).days for r in recs)
    tone = (args.get("tone") or ("gentle" if oldest <= 30 else "firm" if oldest <= 60 else "final")).lower()
    subject, body = _TONES.get(tone, _TONES["gentle"])
    total = sum(r.outstanding for r in recs)
    table = "\n".join(f"  {r.number}  dated {r.date}  due {r.due}  {money(s, r.outstanding)}" for r in recs)
    contact = next((c for c in await s.provider.contacts("customer") if c["name"] == name), {})
    company = (await s.provider.company_info()).get("name") or s.ws.client
    text = (f"To: {contact.get('email', '(no email on file)')}\nSubject: "
            + subject.format(nums=", ".join(r.number for r in recs), total=money(s, total)) + "\n\n"
            + body.format(name=name, table=table, total=money(s, total), company=company))
    path = s.ws.path("Outbox", f"{date.today().isoformat()} Reminder {_safe(name)}.txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    d = s.drafts.add("reminder", f"Reminder ({tone}) to {name} for {money(s, total)}",
                     {"customer": name, "to": contact.get("email", ""), "tone": tone, "path": path,
                      "invoices": [r.number for r in recs], "total": total},
                     status="draft", task_id=ctx.task_id or "")
    return Out(f"Drafted {d['id']}: {tone} reminder to {name} ({money(s, total)}, oldest {oldest} days) at "
               f"{rel(s, path)}. Not sent.", data={"draft": d, "path": path})


@accounting_tool("generate_customer_statement", "Produce a customer statement PDF (open items and aging) in "
                 "Reports/Statements.", D, {"customer": {"type": "string"}, "as_of": DATE}, ["customer"])
async def generate_customer_statement(s, args, ctx) -> Out:
    cutoff = as_of(s, args)
    cust = args["customer"].strip().lower()
    recs = [r for r in await s.provider.invoices("sales", "all") if cust in r.party.lower()]
    if not recs:
        return Out("", ok=False, error=f"no invoices for {args['customer']}")
    name = recs[0].party
    company = (await s.provider.company_info()).get("name") or s.ws.client
    lines = [f"From: {company}", f"To: {name}", f"Statement date: {cutoff}", "",
             "Invoice      Date        Due         Total           Paid            Outstanding"]
    buckets = {"current": 0.0, "1-30": 0.0, "31-60": 0.0, "61-90": 0.0, "90+": 0.0}
    for r in sorted(recs, key=lambda r: r.date):
        if r.status == "void":
            continue
        lines.append(f"{r.number:<12} {r.date}  {r.due}  {money(s, r.total):>14}  {money(s, r.paid):>14}  "
                     f"{money(s, r.outstanding):>14}")
        if r.outstanding > 0:
            days = (date.fromisoformat(cutoff) - date.fromisoformat(r.due)).days
            b = "current" if days <= 0 else "1-30" if days <= 30 else "31-60" if days <= 60 else \
                "61-90" if days <= 90 else "90+"
            buckets[b] += r.outstanding
    total = sum(buckets.values())
    lines += ["", "Aging: " + "   ".join(f"{k}: {money(s, v)}" for k, v in buckets.items()),
              f"TOTAL DUE: {money(s, total)}"]
    path = write_pdf(s.ws.path("Reports", "Statements", f"{cutoff} Statement {_safe(name)}.pdf"), lines,
                     title="STATEMENT OF ACCOUNT")
    return Out(f"Statement for {name}: {money(s, total)} due. Saved {rel(s, path)}", data={"path": path, "total": total})


@accounting_tool("request_document", "Draft a request (email text in the Outbox) for a missing document - a copy "
                 "invoice, a receipt, a statement. Not sent.", D,
                 {"to": {"type": "string", "description": "Supplier, customer or person."},
                  "what": {"type": "string"}, "entity": {"type": "string"}, "reason": {"type": "string"}},
                 ["to", "what"])
async def request_document(s, args, ctx) -> Out:
    who = args["to"].strip()
    contact = next((c for c in await s.provider.contacts() if c["name"].lower() == who.lower()), {})
    company = (await s.provider.company_info()).get("name") or s.ws.client
    body = (f"To: {contact.get('email', '(add email)')}\nSubject: Request: {args['what']}\n\n"
            f"Hello {who},\n\nCould you please send us {args['what']}"
            + (f" ({args['reason']})" if args.get("reason") else "") + "? We need it to complete our records.\n\n"
            f"Thank you,\n{company}")
    path = s.ws.path("Outbox", f"{date.today().isoformat()} Request {_safe(who)} {_safe(args['what'])[:40]}.txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    d = s.drafts.add("document_request", f"Request {args['what']} from {who}",
                     {"to": who, "email": contact.get("email", ""), "what": args["what"], "path": path,
                      "entity": args.get("entity") or ""}, task_id=ctx.task_id or "")
    return Out(f"Drafted {d['id']}: request to {who} for {args['what']} at {rel(s, path)}. Not sent.",
               data={"draft": d, "path": path})


@accounting_tool("record_exception", "Flag something for the accountant: a difference you cannot explain, a "
                 "missing document, a suspicious item, a judgement call. Short specific title.", D,
                 {"title": {"type": "string"}, "detail": {"type": "string"},
                  "severity": {"type": "string", "description": "info | review | high"},
                  "entity": {"type": "string"}, "action": {"type": "string"}}, ["title"])
async def record_exception(s, args, ctx) -> Out:
    sev = (args.get("severity") or "review").lower()
    f = F(args["title"].strip(), args.get("detail") or "", severity=sev if sev in ("info", "review", "high") else "review",
          entity=args.get("entity") or "", action=args.get("action") or "")
    return Out(f"Recorded for review: {f['title']}", findings=[f])


@accounting_tool("resolve_exception", "Mark an exception resolved, with a note.", D,
                 {"id": {"type": "string"}, "note": {"type": "string"}}, ["id"])
async def resolve_exception(s, args, ctx) -> Out:
    x = s.drafts.resolve_finding(args["id"], args.get("note") or "")
    return Out(f"Resolved {x['id']}: {x['title']}", data={"exception": x})


@accounting_tool("reject_draft", "Reject a draft (it will not be posted).", D,
                 {"draft_id": {"type": "string"}, "reason": {"type": "string"}}, ["draft_id"])
async def reject_draft(s, args, ctx) -> Out:
    d = s.drafts.get(args["draft_id"])
    if d["status"] in ("posted", "released", "submitted"):
        return Out("", ok=False, error=f"{d['id']} is already {d['status']}; reverse it with a journal instead")
    d = s.drafts.set_status(d["id"], "rejected", reason=args.get("reason") or "")
    return Out(f"Rejected {d['id']}: {d['title']}", data={"draft": d})


def _safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', " ", name).strip()[:60]


@accounting_tool("prepare_collection_run", "The morning debtors run: classify every overdue customer, draft "
                 "reminders (gentle/firm/final) for the routine ones, and escalate large or very late accounts to "
                 "the accountant instead of emailing them. Nothing is sent.", D,
                 {"as_of": DATE, "escalate_days": {"type": "integer"}, "escalate_amount": AMOUNT})
async def prepare_collection_run(s, args, ctx) -> Out:
    from .ledger import find_overdue_invoices
    aged = await find_overdue_invoices(s, args, ctx)
    drafted, escalated, findings = [], [], list(aged.findings)
    already = {d["payload"].get("customer") for d in s.drafts.list(status="draft,review", kind="reminder")}
    for c in aged.data["customers"]:
        if c["class"] == "escalate":
            escalated.append(c["customer"])
            continue
        if c["customer"] in already:
            continue
        out = await draft_payment_reminder(s, {"customer": c["customer"], "as_of": aged.data["as_of"],
                                               "tone": "gentle" if c["class"] == "gentle reminder" else "firm"}, ctx)
        if out.data.get("draft"):
            drafted.append(f"{c['customer']} ({out.data['draft']['id']})")
    return Out(f"Debtors run at {aged.data['as_of']}: {len(drafted)} reminder(s) drafted in the Outbox "
               f"({', '.join(drafted) or 'none'}); {len(escalated)} escalated to the accountant "
               f"({', '.join(escalated) or 'none'}). Nothing was sent.",
               data={"drafted": drafted, "escalated": escalated, "buckets": aged.data["buckets"]},
               findings=findings, checks=aged.checks)
