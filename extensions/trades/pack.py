"""The Trades pack: tools, the office assistant, its checklists.

Issuing an invoice and recording a payment change the business's records
and ask first (POST), as in the Freelancer pack. Everything else is READ
or DRAFT: nothing is sent, ordered or charged - quotes, invoices and
renewal reminders are PDFs and .eml drafts the tradesperson sends.

READ   trades_overview, day_sheet, free_slots, overdue_invoices, renewals_due
DRAFT  set_material, import_price_list, price_job, create_job_quote, set_quote_status, book_job, finish_job,
       parts_lists, draft_job_invoice, add_certificate, draft_renewal_reminder
POST   issue_job_invoice, record_job_payment
"""
from __future__ import annotations

import os
from typing import Any

from agents.core import RiskLevel
from agents.extensions import AgentTemplate, ExtensionManifest, Schedule, Skill, Workflow
from extensions.packkit import Level, Out, PackExtension, ToolBook, finding, rule, step

from .office import Office

EXT = "trades"
HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = ToolBook(EXT, noun="your invoices")
DATE = {"type": "string", "description": "YYYY-MM-DD."}
PARTS = {"type": "array", "description": "Parts from the price list: [{code, qty}].",
         "items": {"type": "object", "properties": {"code": {"type": "string"}, "qty": {"type": "number"}}}}


def _job_line(j: dict[str, Any]) -> str:
    return f"{j['start'][11:16]} #{j['id']} {j['customer']}: {j['title']} ({j['hours']:g} h)" + (
        f", {j['address']}" if j["address"] else "")


@TOOLS.tool("trades_overview", "Today and tomorrow's jobs, quotes waiting, jobs to invoice, invoices overdue, "
            "certificates coming due.", Level.READ, {"as_of": DATE})
def trades_overview(o: Office, a: dict[str, Any]) -> Out:
    v = o.overview(a.get("as_of", ""))
    text = (f"{v['as_of']}: {len(v['today'])} job(s) today, {len(v['tomorrow'])} tomorrow. {len(v['quotes_waiting'])} "
            f"quote(s) waiting, {len(v['to_invoice'])} finished job(s) not invoiced, {len(v['overdue'])} invoice(s) "
            f"overdue, {len(v['renewals'])} certificate(s) due within 30 days.")
    findings = [finding(f"Job #{j['id']} for {j['customer']} is done but not invoiced", action="draft the invoice",
                        entity=j["customer"]) for j in v["to_invoice"]]
    findings += [finding(f"{i['number']} ({i['customer']}) is {i['days_late']} days overdue, "
                         f"{o.money(i['outstanding'])}", action="chase it", entity=i["customer"]) for i in v["overdue"]]
    return Out(text, {"overview": v}, findings)


@TOOLS.tool("day_sheet", "The jobs booked on a day, in order, with addresses.", Level.READ, {"day": DATE})
def day_sheet(o: Office, a: dict[str, Any]) -> Out:
    jobs = o.day(a.get("day", ""))
    return Out(f"{len(jobs)} job(s)" + (":\n" + "\n".join(_job_line(j) for j in jobs) if jobs else "."), {"jobs": jobs})


@TOOLS.tool("free_slots", "Free time in a working day (at least the hours asked).", Level.READ,
            {"day": DATE, "hours": {"type": "number"}})
def free_slots(o: Office, a: dict[str, Any]) -> Out:
    slots = o.free_slots(a.get("day", ""), float(a.get("hours") or 1))
    return Out("Free: " + (", ".join(f"{s['from']}-{s['to']}" for s in slots) if slots else "nothing that long."),
               {"slots": slots})


@TOOLS.tool("overdue_invoices", "Issued invoices past their due date.", Level.READ, {"as_of": DATE})
def overdue_invoices(o: Office, a: dict[str, Any]) -> Out:
    rows = o.overdue(a.get("as_of", ""))
    return Out(f"{len(rows)} overdue" + (": " + "; ".join(f"{i['number']} {i['customer']} {o.money(i['outstanding'])}, "
                                                           f"{i['days_late']} days" for i in rows) if rows else "."),
               {"overdue": rows})


@TOOLS.tool("renewals_due", "Certificates (gas safety, electrical checks, servicing) due within the days given.",
            Level.READ, {"days": {"type": "integer"}, "as_of": DATE})
def renewals_due(o: Office, a: dict[str, Any]) -> Out:
    rows = o.renewals(a.get("as_of", ""), int(a.get("days") or 30))
    findings = [finding(f"{c['customer']}: {c['kind']} due {c['due']}" + (" (overdue)" if c["days_left"] < 0 else ""),
                        action="draft the renewal reminder", entity=c["customer"]) for c in rows]
    return Out(f"{len(rows)} certificate(s) due.", {"renewals": rows}, findings)


@TOOLS.tool("set_material", "Add or change a part on the price list (cost price, before markup).", Level.DRAFT,
            {"code": {"type": "string"}, "name": {"type": "string"}, "cost": {"type": "number"},
             "unit": {"type": "string"}, "supplier": {"type": "string"}}, ["code", "name", "cost"])
def set_material(o: Office, a: dict[str, Any]) -> Out:
    m = o.set_material(a["code"], a["name"], a["cost"], unit=a.get("unit") or "each", supplier=a.get("supplier", ""))
    return Out(f"{m['code']} {m['name']}: {o.money(m['cost'])} per {m['unit']}.", {"material": m})


@TOOLS.tool("import_price_list", "Import a supplier's price list (CSV: code, name/description, cost/price, unit).",
            Level.DRAFT, {"path": {"type": "string"}, "supplier": {"type": "string"}}, ["path"])
def import_price_list(o: Office, a: dict[str, Any]) -> Out:
    path = a["path"] if os.path.isabs(a["path"]) else os.path.join(o.root, a["path"])
    r = o.import_price_list(path, a.get("supplier", ""))
    return Out(f"{r['added']} part(s) on the price list, {r['skipped']} row(s) skipped.", {"import": r})


@TOOLS.tool("price_job", "Price a job without saving it: parts plus markup, labour, call-out, VAT.", Level.DRAFT,
            {"parts": PARTS, "labour_hours": {"type": "number"}}, ["labour_hours"])
def price_job(o: Office, a: dict[str, Any]) -> Out:
    p = o.price(a.get("parts") or [], a["labour_hours"])
    return Out(f"{len(p['lines'])} line(s): {o.money(p['subtotal'])} + VAT {o.money(p['vat'])} = {o.money(p['total'])} "
               f"(parts cost you {o.money(p['materials_cost'])}).", {"price": p})


@TOOLS.tool("create_job_quote", "Write a priced quote as a PDF. Nothing is sent.", Level.DRAFT,
            {"customer": {"type": "string"}, "job": {"type": "string"}, "parts": PARTS,
             "labour_hours": {"type": "number"}, "address": {"type": "string"}, "email": {"type": "string"},
             "callout": {"type": "number"}}, ["customer", "job", "labour_hours"])
def create_job_quote(o: Office, a: dict[str, Any]) -> Out:
    q = o.create_quote(a["customer"], a["job"], a.get("parts") or [], a["labour_hours"], address=a.get("address", ""),
                       email=a.get("email", ""), callout=a.get("callout"))
    return Out(f"Quote {q['number']} for {q['customer']}: {o.money(q['total'])} incl. VAT. File: {q['file']}",
               {"quote": q})


@TOOLS.tool("set_quote_status", "Mark a quote sent, accepted or declined.", Level.DRAFT,
            {"number": {"type": "string"}, "status": {"type": "string", "enum": ["sent", "accepted", "declined"]}},
            ["number", "status"])
def set_quote_status(o: Office, a: dict[str, Any]) -> Out:
    q = o.set_quote(a["number"], a["status"])
    return Out(f"{q['number']} is {q['status']}.", {"quote": q})


@TOOLS.tool("book_job", "Book a job into the diary (from a quote, or a customer and job). Refuses a double "
            "booking.", Level.DRAFT,
            {"quote": {"type": "string"}, "customer": {"type": "string"}, "title": {"type": "string"},
             "start": {"type": "string", "description": "YYYY-MM-DD HH:MM"}, "hours": {"type": "number"},
             "address": {"type": "string"}, "email": {"type": "string"}, "notes": {"type": "string"}}, ["start"])
def book_job(o: Office, a: dict[str, Any]) -> Out:
    j = o.book(quote=a.get("quote", ""), customer=a.get("customer", ""), title=a.get("title", ""), start=a["start"],
               hours=a.get("hours"), address=a.get("address", ""), email=a.get("email", ""), notes=a.get("notes", ""))
    return Out(f"Booked {j['start']}: " + _job_line(j), {"job": j})


@TOOLS.tool("finish_job", "Mark a job done, with notes on what was done.", Level.DRAFT,
            {"job_id": {"type": "integer"}, "notes": {"type": "string"}}, ["job_id"])
def finish_job(o: Office, a: dict[str, Any]) -> Out:
    j = o.finish(a["job_id"], a.get("notes", ""))
    return Out(f"Job #{j['id']} for {j['customer']} is done.", {"job": j})


@TOOLS.tool("parts_lists", "What to pick up or order for the jobs booked in the coming days, per supplier, as "
            "CSV files. Nothing is ordered.", Level.DRAFT, {"start": DATE, "days": {"type": "integer"}})
def parts_lists(o: Office, a: dict[str, Any]) -> Out:
    lists = o.parts_lists(a.get("start", ""), int(a.get("days") or 7))
    if not lists:
        return Out("No parts needed for the jobs booked.", {"parts": []})
    return Out("Parts lists:\n" + "\n".join(f"{p['supplier']}: {len(p['items'])} item(s) - {p['path']}" for p in lists),
               {"parts": lists})


@TOOLS.tool("draft_job_invoice", "Draft the invoice for a job from its quote, with the hours actually worked and "
            "any extras.", Level.DRAFT,
            {"job_id": {"type": "integer"}, "hours": {"type": "number"},
             "extras": {"type": "array", "items": {"type": "object", "properties": {
                 "description": {"type": "string"}, "amount": {"type": "number"}}}}}, ["job_id"])
def draft_job_invoice(o: Office, a: dict[str, Any]) -> Out:
    inv = o.draft_invoice(a["job_id"], hours=a.get("hours"), extras=a.get("extras"))
    return Out(f"Invoice draft #{inv['id']} for {inv['customer']}: {o.money(inv['total'])} incl. VAT.", {"invoice": inv})


@TOOLS.tool("issue_job_invoice", "Number an invoice draft, write its PDF and the email to send. Asks first.",
            Level.POST, {"invoice_id": {"type": "integer"}, "date": DATE}, ["invoice_id"])
def issue_job_invoice(o: Office, a: dict[str, Any]) -> Out:
    inv = o.issue(a["invoice_id"], a.get("date", ""))
    return Out(f"{inv['number']} to {inv['customer']}: {o.money(inv['total'])}, due {inv['due']}. Email ready in Outbox: "
               f"{inv['email']}", {"invoice": inv})


@TOOLS.tool("record_job_payment", "Record money received against an invoice. Asks first.", Level.POST,
            {"number": {"type": "string"}, "amount": {"type": "number"}}, ["number"])
def record_job_payment(o: Office, a: dict[str, Any]) -> Out:
    inv = o.pay(a["number"], a.get("amount"))
    return Out(f"{inv['number']}: {inv['status']}, {o.money(inv['outstanding'])} outstanding.", {"invoice": inv})


@TOOLS.tool("add_certificate", "Record a certificate or service issued, and how many months it lasts.", Level.DRAFT,
            {"kind": {"type": "string"}, "customer": {"type": "string"}, "issued": DATE,
             "valid_months": {"type": "integer"}, "address": {"type": "string"}, "email": {"type": "string"}},
            ["kind", "customer", "issued", "valid_months"])
def add_certificate(o: Office, a: dict[str, Any]) -> Out:
    c = o.add_certificate(a["kind"], a["customer"], a["issued"], a["valid_months"], address=a.get("address", ""),
                          email=a.get("email", ""))
    return Out(f"{c['kind']} for {c['customer']} recorded; due again {c['due']}.", {"certificate": c})


@TOOLS.tool("draft_renewal_reminder", "Write a renewal reminder to the customer as an email draft. Not sent.",
            Level.DRAFT, {"certificate_id": {"type": "integer"}}, ["certificate_id"])
def draft_renewal_reminder(o: Office, a: dict[str, Any]) -> Out:
    c = o.renewal_reminder(a["certificate_id"])
    return Out(f"Reminder to {c['customer']} in Outbox: {c['email_draft']}", {"reminder": c})


TEMPLATE = AgentTemplate(
    id="trade_office", name="Trade Office Assistant", worker_type=EXT,
    mission="Run a tradesperson's office: quotes priced right from the price list, a diary with no double bookings, "
            "parts ready for the week, every finished job invoiced, late payers and certificate renewals chased.",
    handles=["quotes", "bookings", "parts lists", "invoices", "certificate renewals"],
    tools=TOOLS.names() + ["read_file", "list_dir"], max_auto_risk=RiskLevel.LOW,
    triggers=[Schedule(every="weekdays", at="18:00")],
    memory_schema={"customer": "address, access notes, what was done before"},
    default_workflow="evening_office", artifacts=["quotes", "invoices", "parts lists", "reminders"],
    setup_questions=["Labour rate", "Markup on parts", "Call-out fee", "VAT", "Payment terms"], extension_id=EXT)

WORKFLOWS = [
    Workflow(id="evening_office", name="Evening office", description="Weekdays at six: tomorrow's jobs, what to "
             "invoice, who is late, renewals.", template_id=TEMPLATE.id, trigger=Schedule(every="weekdays", at="18:00"),
             build_steps=lambda meta: [step("Where things stand", "trades_overview"),
                                       step("Certificates coming due", "renewals_due")],
             goal="Do the evening office"),
    Workflow(id="weekly_parts", name="Parts for the week", description="Sunday: parts lists per supplier for the "
             "week's jobs.", template_id=TEMPLATE.id, trigger=Schedule(every="weekly", at="17:00", weekday=6),
             build_steps=lambda meta: [step("Parts lists", "parts_lists")], goal="Get the week's parts ready"),
]

SKILLS = [Skill("trades.rules", "How this pack works", always=True, text=(
    "You run a tradesperson's office. Price from the price list only (set_material to add a part); never invent "
    "a price. When a job description or photo comes in, list the parts and the hours, then price_job, then "
    "create_job_quote. Book jobs into free slots; the diary refuses clashes. Issuing an invoice and recording a "
    "payment ask the tradesperson first. You never send, order or charge anything: emails are drafts in Outbox."))]

INTENT_RULES = [
    rule(EXT, TEMPLATE.id, r"\b(?:evening\s+office|my\s+(?:paperwork|admin\s+for\s+the\s+jobs))\b",
         workflow="evening_office", reply="Doing the paperwork: tomorrow's jobs, invoices, late payers, renewals.",
         verb=r"\b(?:please\s+)?(?:do|run|sort\s+out|catch\s+up\s+on)\b"),
    rule(EXT, TEMPLATE.id, r"\bparts\s+(?:list|lists|for\s+the\s+week)\b", workflow="weekly_parts",
         reply="Building the parts lists for the week's jobs."),
    rule(EXT, TEMPLATE.id, r"\b(?:a\s+)?quote\s+for\s+(?:a|the|an)\s+(?:boiler|rewire|bathroom|kitchen|job|repair)",
         reply="Pricing it from your price list.", verb=r"\b(?:please\s+)?(?:price|quote|write|draft|do|make)\b"),
    rule(EXT, TEMPLATE.id, r"\b(?:job|visit|call-?out)\s+(?:in|for|on)\b", reply="Finding a slot in the diary.",
         verb=r"\b(?:please\s+)?(?:book|put|schedule|pencil)\b"),
    rule(EXT, TEMPLATE.id, r"\b(?:gas\s+safety|certificate|eicr|boiler\s+service)\b", reply="Checking the renewals.",
         verb=r"\b(?:please\s+)?(?:check|record|add|log|remind)\b"),
]


class TradesExtension(PackExtension):
    manifest = ExtensionManifest.load(HERE)
    STORE = Office
    TOOLS = TOOLS
    TEMPLATES = [TEMPLATE]
    WORKFLOWS = WORKFLOWS
    SKILLS = SKILLS
    INTENT_RULES = INTENT_RULES
    FOLDER = "Trade office"
    DEFAULTS = {"name": "My trade business", "currency": "GBP", "labour_rate": 60, "markup_pct": 20, "callout": 0,
                "vat_pct": 0, "terms_days": 14, "day_start": "08:00", "day_end": "17:00"}
    ROLE = "the office assistant for a tradesperson"
    ACTIONS = [("Evening office", "evening_office"), ("Parts for the week", "weekly_parts")]
    NOTES = "Issuing an invoice and recording a payment ask first (POST); nothing is sent, ordered or charged."
    EXAMPLES = [("do my paperwork", "evening_office"), ("please do the evening office", "evening_office"), ("please build the parts lists", "weekly_parts"),
                ("price a quote for a boiler swap in Hove", ""), ("book a job in for Mrs Patel on Friday", ""),
                ("record the gas safety certificate for 12 Elm Road", "")]

    def context_line(self, o: Office) -> str:
        return (f"Trade office: {o.name} · labour {o.money(o.num('labour_rate', 60))}/h · markup {o.num('markup_pct', 20):g}% · "
                f"VAT {o.num('vat_pct', 0):g}% · working day {o.cfg.get('day_start') or '08:00'}-{o.cfg.get('day_end') or '17:00'}")

    def snapshot(self, o: Office) -> dict[str, Any]:
        v = o.overview()
        return {"title": o.name, "meta": f"labour {o.money(o.num('labour_rate', 60))}/h · markup {o.num('markup_pct', 20):g}%",
                "numbers": [("Jobs today", len(v["today"])), ("To invoice", len(v["to_invoice"])),
                            ("Overdue", len(v["overdue"])), ("Renewals due", len(v["renewals"]))],
                "lists": [("Tomorrow", [(_job_line(j), "") for j in v["tomorrow"]]),
                          ("Overdue", [(f"{i['number']} {i['customer']} {o.money(i['outstanding'])}", i["file"])
                                       for i in v["overdue"]]),
                          ("Renewals", [(f"{c['due']} {c['customer']}: {c['kind']}", "") for c in v["renewals"]])]}
