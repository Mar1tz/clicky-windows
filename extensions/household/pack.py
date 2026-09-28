"""The Household finance pack: tools, the money assistant, its checklists.

Every tool is READ or DRAFT. Paying a bill or cancelling a subscription
is the household's to do; this pack finds them, adds them up and says
what they cost a year.

READ   household_overview, month_budget, regular_payments, uncategorised_payments, warranties_expiring
DRAFT  import_bank_statement_csv, teach_category, set_budget, add_warranty, where_the_money_went
"""
from __future__ import annotations

import os
from typing import Any

from agents.core import RiskLevel
from agents.extensions import AgentTemplate, ExtensionManifest, Schedule, Skill, Workflow
from extensions.packkit import Level, Out, PackExtension, ToolBook, finding, rule, step

from .money import Household

EXT = "household"
HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = ToolBook(EXT, noun="your household records")
MONTH = {"type": "string", "description": "YYYY-MM. Default this month."}


@TOOLS.tool("household_overview", "This month so far, bills due this week, price rises, warranties running out, "
            "payments with no category.", Level.READ, {"as_of": {"type": "string"}})
def household_overview(h: Household, a: dict[str, Any]) -> Out:
    o = h.overview(a.get("as_of", ""))
    m = o["month"]
    text = (f"{m['month']}: in {h.money(m['income'])}, out {h.money(m['spent'])}. {len(o['due_soon'])} regular "
            f"payment(s) due this week; {len(o['regular'])} in all, {h.money(sum(s['yearly'] for s in o['regular']))} "
            f"a year. {o['uncategorised']} payment(s) without a category.")
    findings = [finding(f"{x['category']}: {h.money(x['spent'])} spent against a {h.money(x['budget'])} budget",
                        action="see what pushed it over", entity=x["category"]) for x in m["over"]]
    findings += [finding(f"{s['description']} went up by {h.money(s['price_rise'])} to {h.money(s['amount'])} a month",
                         action="check it is still worth it", entity=s["payee"]) for s in o["price_rises"]]
    findings += [finding(f"The warranty on {w['item']} runs out on {w['expires']}", action="check it works before then",
                         entity=w["item"]) for w in o["warranties"]]
    return Out(text, {"overview": o}, findings)


@TOOLS.tool("month_budget", "A month's spending by category against the budget.", Level.READ, {"month": MONTH})
def month_budget(h: Household, a: dict[str, Any]) -> Out:
    m = h.month(a.get("month", ""))
    lines = [f"{x['category']}: {h.money(x['spent'])}" + (f" of {h.money(x['budget'])}" if x["budget"] is not None else "")
             + (" - OVER" if x["over"] else "") for x in sorted(m["lines"], key=lambda x: -x["spent"])]
    return Out(f"{m['month']}: in {h.money(m['income'])}, out {h.money(m['spent'])}.\n" + "\n".join(lines), {"month": m})


@TOOLS.tool("regular_payments", "Payments that recur every month (subscriptions and bills): what they cost a year, "
            "when the next is due, any price rise.", Level.READ, {"as_of": {"type": "string"}})
def regular_payments(h: Household, a: dict[str, Any]) -> Out:
    rows = h.recurring(a.get("as_of", ""))
    lines = [f"{r['description']}: {h.money(r['amount'])}/month ({h.money(r['yearly'])}/year), next {r['next']}"
             + (f", up {h.money(r['price_rise'])}" if r["price_rise"] else "") for r in rows]
    return Out(f"{len(rows)} regular payment(s), {h.money(sum(r['yearly'] for r in rows))} a year"
               + (":\n" + "\n".join(lines) if lines else "."), {"regular": rows})


@TOOLS.tool("uncategorised_payments", "Recent payments the pack could not put in a category.", Level.READ,
            {"limit": {"type": "integer"}})
def uncategorised_payments(h: Household, a: dict[str, Any]) -> Out:
    rows = h.uncategorised(int(a.get("limit") or 30))
    return Out(f"{len(rows)} without a category" + (":\n" + "\n".join(f"{r['date']} {r['description']} {r['amount']:,.2f}"
                                                                        for r in rows) if rows else "."), {"payments": rows})


@TOOLS.tool("warranties_expiring", "Warranties running out within the days given (default 60).", Level.READ,
            {"days": {"type": "integer"}})
def warranties_expiring(h: Household, a: dict[str, Any]) -> Out:
    rows = h.expiring(days=int(a.get("days") or 60))
    return Out(f"{len(rows)} warranty(ies) ending: " + ("; ".join(f"{w['item']} on {w['expires']}" for w in rows) or "none."),
               {"warranties": rows})


@TOOLS.tool("import_bank_statement_csv", "Import a bank statement CSV (any bank: one amount column, or money in / "
            "money out). Rows already imported are skipped.", Level.DRAFT,
            {"path": {"type": "string"}, "account": {"type": "string"}}, ["path"])
def import_bank_statement_csv(h: Household, a: dict[str, Any]) -> Out:
    path = a["path"] if os.path.isabs(a["path"]) else os.path.join(h.root, a["path"])
    r = h.import_statement(path, a.get("account", ""))
    return Out(f"{r['added']} payment(s) added, {r['already_there']} already there; {r['uncategorised']} without a "
               "category.", {"import": r})


@TOOLS.tool("teach_category", "Teach a category: payments whose description contains the words go there, now and "
            "from now on.", Level.DRAFT, {"words": {"type": "string"}, "category": {"type": "string"}},
            ["words", "category"])
def teach_category(h: Household, a: dict[str, Any]) -> Out:
    r = h.teach(a["words"], a["category"])
    return Out(f"'{r['pattern']}' is now {r['category']} ({r['recategorised']} payment(s)).", {"rule": r})


@TOOLS.tool("set_budget", "Set a monthly budget for a category.", Level.DRAFT,
            {"category": {"type": "string"}, "monthly": {"type": "number"}}, ["category", "monthly"])
def set_budget(h: Household, a: dict[str, Any]) -> Out:
    b = h.set_budget(a["category"], a["monthly"])
    return Out(f"{b['category']}: {h.money(b['monthly'])} a month.", {"budget": b})


@TOOLS.tool("add_warranty", "Record something bought with a warranty.", Level.DRAFT,
            {"item": {"type": "string"}, "bought": {"type": "string"}, "months": {"type": "integer"},
             "price": {"type": "number"}, "shop": {"type": "string"}, "receipt": {"type": "string"}},
            ["item", "bought", "months"])
def add_warranty(h: Household, a: dict[str, Any]) -> Out:
    w = h.add_warranty(a["item"], a["bought"], a["months"], price=a.get("price"), shop=a.get("shop", ""),
                       receipt=a.get("receipt", ""))
    return Out(f"{w['item']}: warranty until {w['expires']}.", {"warranty": w})


@TOOLS.tool("where_the_money_went", "Write the month's report: in, out, saved, by category against budget, the "
            "biggest payments, regular payments.", Level.DRAFT, {"month": MONTH})
def where_the_money_went(h: Household, a: dict[str, Any]) -> Out:
    r = h.report(a.get("month", ""))
    return Out(f"{r['month']}: in {h.money(r['income'])}, out {h.money(r['spent'])}, "
               f"{'saved' if r['saved'] >= 0 else 'short'} {h.money(abs(r['saved']))}. Report: {r['path']}", {"report": r})


TEMPLATE = AgentTemplate(
    id="money_assistant", name="Money Assistant", worker_type=EXT,
    mission="Keep a household's money clear: every payment in a category, spending against the budget, subscriptions "
            "and their price rises found, bills seen before they land, warranties used before they run out.",
    handles=["bank statements", "budgets", "subscriptions", "bills", "warranties", "monthly report"],
    tools=TOOLS.names() + ["read_file", "list_dir"], max_auto_risk=RiskLevel.LOW,
    triggers=[Schedule(every="monthly", at="09:00", day=1)], memory_schema={"household": "goals, what they are saving for"},
    default_workflow="month_end_money", artifacts=["monthly report"],
    setup_questions=["Currency", "Monthly budgets"], extension_id=EXT)

WORKFLOWS = [
    Workflow(id="month_end_money", name="Where the money went", description="The 1st: last month's report, "
             "regular payments, anything over budget.", template_id=TEMPLATE.id,
             trigger=Schedule(every="monthly", at="09:00", day=1),
             build_steps=lambda meta: [step("This month so far", "household_overview"),
                                       step("Regular payments", "regular_payments"),
                                       step("The report", "where_the_money_went", {"month": meta.get("month", "")}
                                            if meta.get("month") else {})],
             goal="Report where the money went"),
]

SKILLS = [Skill("household.rules", "How this pack works", always=True, text=(
    "You help a household see its money. Numbers come from imported statements only. You never pay, cancel or move "
    "money: you show what a subscription costs a year and let them decide. When a payment has no category, ask "
    "which it is and teach_category so it sticks. Be plain and kind about overspending; no lectures."))]

INTENT_RULES = [
    rule(EXT, TEMPLATE.id, r"\b(?:where\s+(?:my|the|our)\s+money\s+went|money\s+report|monthly\s+spending)\b",
         workflow="month_end_money", reply="Working out where the money went.",
         verb=r"\b(?:please\s+)?(?:show|tell|work\s+out|check|do|make|write)\b"),
    rule(EXT, TEMPLATE.id, r"\b(?:my\s+|our\s+)?(?:subscriptions|direct\s+debits|regular\s+payments)\b",
         reply="Finding everything that goes out every month.",
         verb=r"\b(?:please\s+)?(?:find|list|show|check|review)\b"),
    rule(EXT, TEMPLATE.id, r"\bbudget\b", reply="Setting the budget.", verb=r"\b(?:please\s+)?(?:set|make|change)\b"),
    rule(EXT, TEMPLATE.id, r"\bwarranty\b", reply="Recording the warranty.",
         verb=r"\b(?:please\s+)?(?:add|record|log|save|note)\b"),
]


class HouseholdExtension(PackExtension):
    manifest = ExtensionManifest.load(HERE)
    STORE = Household
    TOOLS = TOOLS
    TEMPLATES = [TEMPLATE]
    WORKFLOWS = WORKFLOWS
    SKILLS = SKILLS
    INTENT_RULES = INTENT_RULES
    FOLDER = "Household"
    DEFAULTS = {"currency": "GBP", "category_words": {}}
    ROLE = "the money assistant for a household"
    ACTIONS = [("Where the money went", "month_end_money")]
    NOTES = "Every tool is READ or DRAFT: nothing is paid, cancelled or moved."
    EXAMPLES = [("show me where the money went", "month_end_money"), ("find my subscriptions", ""),
                ("set a budget for groceries", ""), ("add the warranty for the new fridge", "")]

    def context_line(self, h: Household) -> str:
        m = h.month()
        return f"Household: {h.currency} · this month in {h.money(m['income'])}, out {h.money(m['spent'])}"

    def snapshot(self, h: Household) -> dict[str, Any]:
        o = h.overview()
        m = o["month"]
        return {"title": "Household", "meta": f"{h.currency} · {m['month']}",
                "numbers": [("In", h.money(m["income"])), ("Out", h.money(m["spent"])),
                            ("Regular a year", h.money(sum(s["yearly"] for s in o["regular"]))),
                            ("Over budget", len(m["over"]))],
                "lists": [("Due this week", [(f"{s['next']} {s['description']} {s['amount']:,.2f}", "") for s in o["due_soon"]]),
                          ("Budget", [(f"{x['category']}: {x['spent']:,.2f}" + (f" / {x['budget']:,.2f}" if x["budget"]
                                                                               is not None else ""), "") for x in m["lines"]]),
                          ("Warranties ending", [(f"{w['expires']} {w['item']}", w["receipt"]) for w in o["warranties"]])]}
