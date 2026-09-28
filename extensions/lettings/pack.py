"""The Lettings pack: tools, the lettings assistant, its checklists.

Settling a deposit is POST: it fixes who gets what and ends the tenancy,
so the landlord approves it. Everything else is READ or DRAFT - letters
and works orders are .eml drafts, and no legal notice is ever drafted.

READ   lettings_overview, rent_roll, tenancy_balance, open_repairs, compliance_due, tenancy_renewals
DRAFT  add_tenancy, record_rent, import_rent_payments, draft_arrears_letters, add_contractor, report_repair,
       send_works_order, complete_repair, set_property_certificate, propose_deposit_return
POST   settle_deposit
"""
from __future__ import annotations

import os
from typing import Any

from agents.core import RiskLevel
from agents.extensions import AgentTemplate, ExtensionManifest, Schedule, Skill, Workflow
from extensions.packkit import Level, Out, PackExtension, ToolBook, finding, rule, step

from .lettings import PRIORITIES, Lettings

EXT = "lettings"
HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = ToolBook(EXT, noun="the tenancy and its deposit")
TENANCY = {"type": "string", "description": "Tenancy reference, tenant's name or the address."}


@TOOLS.tool("lettings_overview", "The portfolio: rent a month, arrears, repairs late or unassigned, certificates due, "
            "tenancies ending.", Level.READ, {"as_of": {"type": "string"}})
def lettings_overview(s: Lettings, a: dict[str, Any]) -> Out:
    o = s.overview(a.get("as_of", ""))
    r = o["roll"]
    text = (f"{len(r['tenancies'])} tenancies, {s.money(r['rent_a_month'])} a month; arrears {s.money(r['arrears'])} "
            f"on {len(r['in_arrears'])}. Repairs: {len(o['repairs'])} open, {len(o['late_repairs'])} past target, "
            f"{len(o['unassigned'])} with no contractor. {len(o['compliance'])} certificate(s) due within 30 days; "
            f"{len(o['renewals'])} tenancy(ies) ending soon.")
    findings = [finding(f"{b['address']}: {s.money(b['arrears'])} arrears, {b['days_late']} days", entity=b["ref"],
                        action="draft the arrears letters") for b in r["in_arrears"]]
    findings += [finding(f"{x['address']}: repair #{x['id']} ({x['priority']}) is {x['days_over']} day(s) past target",
                         x["issue"], action="chase the contractor", entity=x["address"]) for x in o["late_repairs"]]
    findings += [finding(f"{c['address']}: {c['kind']} " + ("expired" if c["days_left"] < 0 else "expires") +
                         f" {c['expires']}", action="book the renewal", entity=c["address"],
                         severity="high" if c["days_left"] < 0 else "review") for c in o["compliance"]]
    findings += [finding(f"{t['address']}: tenancy {t['ref']} ends {t['end']}", action="ask the landlord and tenants "
                         "about renewing", entity=t["ref"]) for t in o["renewals"]]
    return Out(text, {"overview": o}, findings)


@TOOLS.tool("rent_roll", "Every tenancy: rent due to date, paid, arrears and how late.", Level.READ,
            {"as_of": {"type": "string"}})
def rent_roll(s: Lettings, a: dict[str, Any]) -> Out:
    r = s.rent_roll(a.get("as_of", ""))
    lines = [f"{b['ref']} {b['address']}: paid {s.money(b['paid'])} of {s.money(b['due'])}"
             + (f", arrears {s.money(b['arrears'])} ({b['days_late']} days)" if b["arrears"] else "") for b in r["tenancies"]]
    return Out(f"Rent roll, {s.money(r['rent_a_month'])} a month, arrears {s.money(r['arrears'])}:\n" + "\n".join(lines),
               {"roll": r})


@TOOLS.tool("tenancy_balance", "One tenancy's rent account.", Level.READ,
            {"tenancy": TENANCY, "as_of": {"type": "string"}}, ["tenancy"])
def tenancy_balance(s: Lettings, a: dict[str, Any]) -> Out:
    b = s.balance(a["tenancy"], a.get("as_of", ""))
    return Out(f"{b['ref']} ({b['tenants']}): {b['months_due']} month(s) due, {s.money(b['due'])}; paid "
               f"{s.money(b['paid'])}; " + (f"arrears {s.money(b['arrears'])} since {b['oldest_unpaid']}."
                                            if b["arrears"] else f"up to date (credit {s.money(b['credit'])})."),
               {"balance": b})


@TOOLS.tool("open_repairs", "Repairs not yet done, with their target date.", Level.READ, {"as_of": {"type": "string"}})
def open_repairs(s: Lettings, a: dict[str, Any]) -> Out:
    rows = s.open_repairs(a.get("as_of", ""))
    return Out(f"{len(rows)} open repair(s)" + (":\n" + "\n".join(
        f"#{r['id']} {r['address']}: {r['issue']} ({r['priority']}, by {r['target']}"
        + (f", {r['contractor']}" if r["contractor"] else ", no contractor") + ")" for r in rows) if rows else "."),
        {"repairs": rows})


@TOOLS.tool("compliance_due", "Property certificates (gas safety, electrical, energy) expiring within the days "
            "given, or expired.", Level.READ, {"days": {"type": "integer"}, "as_of": {"type": "string"}})
def compliance_due(s: Lettings, a: dict[str, Any]) -> Out:
    rows = s.compliance(a.get("as_of", ""), int(a.get("days") or 30))
    return Out(f"{len(rows)} certificate(s) due: " + ("; ".join(f"{c['address']} {c['kind']} {c['expires']}" for c in rows)
                                                      or "none."), {"compliance": rows})


@TOOLS.tool("tenancy_renewals", "Tenancies ending within the renewal warning (default 60 days).", Level.READ,
            {"as_of": {"type": "string"}})
def tenancy_renewals(s: Lettings, a: dict[str, Any]) -> Out:
    rows = s.renewals(a.get("as_of", ""))
    return Out(f"{len(rows)} ending: " + ("; ".join(f"{t['ref']} {t['address']} on {t['end']}" for t in rows) or "none."),
               {"renewals": rows})


@TOOLS.tool("add_tenancy", "Set up a tenancy (and its property, if new).", Level.DRAFT,
            {"ref": {"type": "string"}, "address": {"type": "string"}, "tenants": {"type": "string"},
             "rent": {"type": "number", "description": "Monthly rent."}, "due_day": {"type": "integer"},
             "start": {"type": "string"}, "end": {"type": "string"}, "deposit": {"type": "number"},
             "email": {"type": "string"}, "landlord": {"type": "string"}, "landlord_email": {"type": "string"}},
            ["ref", "address", "tenants", "rent", "due_day", "start"])
def add_tenancy(s: Lettings, a: dict[str, Any]) -> Out:
    t = s.let(a["ref"], a["address"], a["tenants"], a["rent"], a["due_day"], a["start"], end=a.get("end", ""),
              deposit=a.get("deposit", 0), email=a.get("email", ""), landlord=a.get("landlord", ""),
              landlord_email=a.get("landlord_email", ""))
    return Out(f"Tenancy {t['ref']}: {t['tenants']} at {t['address']}, {s.money(t['rent'])} due on day {t['due_day']}.",
               {"tenancy": t})


@TOOLS.tool("record_rent", "Record a rent payment.", Level.DRAFT,
            {"tenancy": TENANCY, "amount": {"type": "number"}, "date": {"type": "string"}, "note": {"type": "string"}},
            ["tenancy", "amount"])
def record_rent(s: Lettings, a: dict[str, Any]) -> Out:
    b = s.record_payment(a["tenancy"], a["amount"], a.get("date", ""), a.get("note", ""))
    return Out(f"{b['ref']}: " + (f"arrears now {s.money(b['arrears'])}." if b["arrears"] else "up to date."),
               {"balance": b})


@TOOLS.tool("import_rent_payments", "Match money in from a bank CSV to tenancies (by reference, else surname).",
            Level.DRAFT, {"path": {"type": "string"}}, ["path"])
def import_rent_payments(s: Lettings, a: dict[str, Any]) -> Out:
    r = s.import_rent(a["path"] if os.path.isabs(a["path"]) else os.path.join(s.root, a["path"]))
    findings = [finding(f"Money in not matched to a tenancy: {u['date']} {u['description']} {u['amount']:,.2f}",
                        action="say whose it is, then record_rent") for u in r["unmatched"]]
    return Out(f"{r['matched']} payment(s) matched, {r['already_there']} already recorded, {len(r['unmatched'])} "
               "not matched.", {"import": r}, findings)


@TOOLS.tool("draft_arrears_letters", "Draft the next arrears letter for each tenancy behind: reminder, second, "
            "final. No legal notices. Not sent.", Level.DRAFT, {"as_of": {"type": "string"}})
def draft_arrears_letters(s: Lettings, a: dict[str, Any]) -> Out:
    r = s.arrears_letters(a.get("as_of", ""))
    findings = [finding(f"{b['address']}: {b['days_late']} days in arrears after the final letter",
                        "Any formal notice depends on the law where the property is - take advice before serving one.",
                        action="call the tenant; talk to the landlord", entity=b["ref"], severity="high")
                for b in r["final_stage"]]
    return Out(f"{len(r['drafted'])} letter(s) drafted" + (": " + "; ".join(f"{d['ref']} (step {d['level']})"
                                                                            for d in r["drafted"]) if r["drafted"] else "."),
               {"letters": r}, findings)


@TOOLS.tool("add_contractor", "Add a contractor and their trade.", Level.DRAFT,
            {"name": {"type": "string"}, "trade": {"type": "string"}, "email": {"type": "string"},
             "phone": {"type": "string"}}, ["name", "trade"])
def add_contractor(s: Lettings, a: dict[str, Any]) -> Out:
    c = s.add_contractor(a["name"], a["trade"], email=a.get("email", ""), phone=a.get("phone", ""))
    return Out(f"{c['name']} ({c['trade']}) added.", {"contractor": c})


@TOOLS.tool("report_repair", "Log a repair a tenant reported, with its priority.", Level.DRAFT,
            {"address": {"type": "string"}, "issue": {"type": "string"},
             "priority": {"type": "string", "enum": list(PRIORITIES)}}, ["address", "issue"])
def report_repair(s: Lettings, a: dict[str, Any]) -> Out:
    r = s.report_repair(a["address"], a["issue"], a.get("priority") or "normal")
    return Out(f"Repair #{r['id']} at {r['address']} ({r['priority']}), to be done by {r['target']}.", {"repair": r})


@TOOLS.tool("send_works_order", "Give a repair to a contractor (by name, or the first of a trade) and draft the "
            "works order email. Not sent.", Level.DRAFT,
            {"repair_id": {"type": "integer"}, "contractor": {"type": "string"}, "trade": {"type": "string"}},
            ["repair_id"])
def send_works_order(s: Lettings, a: dict[str, Any]) -> Out:
    r = s.assign(a["repair_id"], a.get("contractor", ""), a.get("trade", ""))
    return Out(f"Repair #{r['id']} given to {r['contractor']}; works order drafted: {r['works_order']}", {"repair": r})


@TOOLS.tool("complete_repair", "Mark a repair done, with its cost.", Level.DRAFT,
            {"repair_id": {"type": "integer"}, "cost": {"type": "number"}}, ["repair_id"])
def complete_repair(s: Lettings, a: dict[str, Any]) -> Out:
    r = s.complete(a["repair_id"], a.get("cost"))
    return Out(f"Repair #{r['id']} done.", {"repair": r})


@TOOLS.tool("set_property_certificate", "Record a property certificate and when it expires.", Level.DRAFT,
            {"address": {"type": "string"}, "kind": {"type": "string"}, "expires": {"type": "string"}},
            ["address", "kind", "expires"])
def set_property_certificate(s: Lettings, a: dict[str, Any]) -> Out:
    c = s.set_certificate(a["address"], a["kind"], a["expires"])
    return Out(f"{c['address']}: {c['kind']} valid until {c['expires']}.", {"certificate": c})


@TOOLS.tool("propose_deposit_return", "Propose how a deposit is split at the end of a tenancy: each deduction with "
            "its reason. Nothing is paid.", Level.DRAFT,
            {"tenancy": TENANCY, "deductions": {"type": "array", "items": {"type": "object", "properties": {
                "reason": {"type": "string"}, "amount": {"type": "number"}}}}}, ["tenancy"])
def propose_deposit_return(s: Lettings, a: dict[str, Any]) -> Out:
    d = s.propose_deposit(a["tenancy"], a.get("deductions") or [])
    return Out(f"{d['ref']}: {s.money(d['to_tenant'])} back to the tenants, {s.money(d['to_landlord'])} to the landlord "
               "- proposed, waiting for the landlord.", {"deposit": d})


@TOOLS.tool("settle_deposit", "Fix the agreed deposit split and end the tenancy. Asks first.", Level.POST,
            {"tenancy": TENANCY}, ["tenancy"])
def settle_deposit(s: Lettings, a: dict[str, Any]) -> Out:
    d = s.settle_deposit(a["tenancy"])
    return Out(f"{d['ref']}'s deposit is settled: {s.money(d['to_tenant'])} to the tenants, {s.money(d['to_landlord'])} "
               "to the landlord. Pay it through the deposit scheme.", {"deposit": d})


TEMPLATE = AgentTemplate(
    id="lettings_assistant", name="Lettings Assistant", worker_type=EXT,
    mission="Keep a rental portfolio in order: rent collected and arrears chased early and fairly, repairs with a "
            "contractor and done on time, certificates never lapsed, renewals raised in time, deposits settled fairly.",
    handles=["rent roll", "arrears", "repairs", "certificates", "renewals", "deposits"],
    tools=TOOLS.names() + ["read_file", "list_dir"], max_auto_risk=RiskLevel.LOW,
    triggers=[Schedule(every="weekdays", at="09:00")], memory_schema={"tenancy": "contact history, payment habits"},
    default_workflow="morning_lettings", artifacts=["arrears letters", "works orders"],
    setup_questions=["Agent name", "Currency", "Repair targets", "Renewal warning"], extension_id=EXT)

WORKFLOWS = [
    Workflow(id="morning_lettings", name="Morning lettings", description="Weekdays: the portfolio, arrears letters "
             "drafted.", template_id=TEMPLATE.id, trigger=Schedule(every="weekdays", at="09:00"),
             build_steps=lambda meta: [step("The portfolio", "lettings_overview"),
                                       step("Arrears letters", "draft_arrears_letters")],
             goal="Check the lettings"),
]

SKILLS = [Skill("lettings.rules", "How this pack works", always=True, text=(
    "You help run rented homes. Rent figures come from the rent roll only. Arrears letters are firm and polite, and "
    "you never draft or threaten a legal notice - that depends on local law and is the landlord's decision with "
    "advice. Urgent repairs (no heat, no water, leaks, safety) are 'urgent'. Deposit deductions need a reason and "
    "evidence; the landlord approves the split. You never send, pay or serve anything."))]

INTENT_RULES = [
    rule(EXT, TEMPLATE.id, r"\b(?:the\s+|my\s+)?(?:lettings|rent\s+roll|portfolio|arrears)\b", workflow="morning_lettings",
         reply="Checking the portfolio and drafting any arrears letters.",
         verb=r"\b(?:please\s+)?(?:check|run|review|go\s+through|chase)\b"),
    rule(EXT, TEMPLATE.id, r"\b(?:a\s+)?(?:repair|leak|boiler\s+(?:is\s+)?(?:broken|not\s+working)|broken)\b",
         reply="Logging the repair.", verb=r"\b(?:please\s+)?(?:log|report|record|add)\b"),
    rule(EXT, TEMPLATE.id, r"\bdeposit\b", reply="Working out the deposit return.",
         verb=r"\b(?:please\s+)?(?:work\s+out|propose|settle|sort\s+out|return)\b"),
    rule(EXT, TEMPLATE.id, r"\b(?:new\s+)?tenancy\b", reply="Setting up the tenancy.",
         verb=r"\b(?:please\s+)?(?:add|set\s+up|create|record)\b"),
]


class LettingsExtension(PackExtension):
    manifest = ExtensionManifest.load(HERE)
    STORE = Lettings
    TOOLS = TOOLS
    TEMPLATES = [TEMPLATE]
    WORKFLOWS = WORKFLOWS
    SKILLS = SKILLS
    INTENT_RULES = INTENT_RULES
    FOLDER = "Lettings"
    DEFAULTS = {"agent": "", "currency": "GBP", "urgent_days": 1, "high_days": 7, "normal_days": 28, "renewal_days": 60,
                "reminder_days": 3, "second_days": 14, "final_days": 30}
    ROLE = "the lettings assistant for a landlord or letting agent"
    ACTIONS = [("Morning lettings", "morning_lettings")]
    NOTES = "Settling a deposit asks first (POST); letters and works orders are drafts; no legal notice is drafted."
    EXAMPLES = [("check the rent roll", "morning_lettings"), ("chase the arrears", "morning_lettings"),
                ("log a repair at 4 Kings Road: leak under the sink", ""), ("work out the deposit for flat 2", ""),
                ("set up a new tenancy at 9 Park Lane", "")]

    def context_line(self, s: Lettings) -> str:
        r = s.rent_roll()
        return f"Lettings: {len(r['tenancies'])} tenancies · {s.money(r['rent_a_month'])} a month · arrears {s.money(r['arrears'])}"

    def snapshot(self, s: Lettings) -> dict[str, Any]:
        o = s.overview()
        r = o["roll"]
        return {"title": s.cfg.get("agent") or "Lettings", "meta": f"{len(r['tenancies'])} tenancies · {s.currency}",
                "numbers": [("Rent a month", s.money(r["rent_a_month"])), ("Arrears", s.money(r["arrears"])),
                            ("Open repairs", len(o["repairs"])), ("Certificates due", len(o["compliance"]))],
                "lists": [("Arrears", [(f"{b['address']}: {b['arrears']:,.2f} · {b['days_late']} d", "") for b in r["in_arrears"]]),
                          ("Repairs", [(f"#{x['id']} {x['address']}: {x['issue']}", "") for x in o["repairs"]]),
                          ("Coming up", [(f"{c['expires']} {c['address']} {c['kind']}", "") for c in o["compliance"]]
                           + [(f"{t['end']} {t['ref']} ends", "") for t in o["renewals"]])]}
