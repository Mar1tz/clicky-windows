"""The Sales CRM pack: tools, the sales assistant, its checklists.

Every tool is READ or DRAFT. The pipeline is the salesperson's own
notebook; emails to prospects are .eml drafts they send themselves.
Syncing with HubSpot, Pipedrive or Salesforce waits for connected
accounts (catalogue gate 2); until then their CSV exports import here.

READ   pipeline_overview, list_deals, follow_ups_due, stale_deals, sales_forecast, find_contact
DRAFT  add_contact, import_contacts, add_deal, move_deal, log_activity, draft_follow_up_email
"""
from __future__ import annotations

import os
from typing import Any

from agents.core import RiskLevel
from agents.extensions import AgentTemplate, ExtensionManifest, Schedule, Skill, Workflow
from extensions.packkit import Level, Out, PackExtension, ToolBook, finding, rule, step

from .crm import CRM, KINDS, OPEN, STAGES

EXT = "sales"
HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = ToolBook(EXT, noun="your pipeline")
DATE = {"type": "string", "description": "YYYY-MM-DD."}
DEAL = {"type": "string", "description": "The deal: its name, company or id."}


def _deal_line(s: CRM, d: dict[str, Any]) -> str:
    return (f"#{d['id']} {d['title']}" + (f" ({d['company']})" if d["company"] else "") + f" - {d['stage']}, "
            f"{s.money(d['value'])}" + (f", closes {d['expected']}" if d["expected"] else ""))


@TOOLS.tool("pipeline_overview", "The pipeline at a glance: value by stage, weighted forecast, win rate, "
            "follow-ups due, deals going cold.", Level.READ, {"as_of": DATE})
def pipeline_overview(s: CRM, a: dict[str, Any]) -> Out:
    o = s.overview(a.get("as_of", ""))
    p, f = o["pipeline"], o["forecast"]
    stages = ", ".join(f"{st} {p['stages'][st]['count']}" for st in OPEN)
    text = (f"Open pipeline {s.money(p['open_value'])} ({stages}); weighted {s.money(p['weighted'])}. "
            f"This month: {s.money(f['weighted'])} weighted, {s.money(f['won'])} won. "
            f"{len(o['follow_ups'])} follow-up(s) due, {len(o['stale'])} deal(s) gone quiet."
            + (f" Win rate {p['win_rate']}%." if p["win_rate"] is not None else ""))
    findings = [finding(f"{d['title']}: no contact for {d['idle_days']} days ({s.money(d['value'])})",
                        action="call or email, or move it to lost", entity=d["title"]) for d in o["stale"][:5]]
    return Out(text, {"overview": o}, findings)


@TOOLS.tool("list_deals", "Deals: open, or one stage.", Level.READ,
            {"stage": {"type": "string", "enum": ["open", *STAGES]}})
def list_deals(s: CRM, a: dict[str, Any]) -> Out:
    rows = s.deals(a.get("stage") or "open")
    return Out(f"{len(rows)} deal(s)" + (":\n" + "\n".join(_deal_line(s, d) for d in rows) if rows else "."),
               {"deals": rows})


@TOOLS.tool("follow_ups_due", "Follow-ups due today or overdue.", Level.READ, {"as_of": DATE})
def follow_ups_due(s: CRM, a: dict[str, Any]) -> Out:
    rows = s.follow_ups(a.get("as_of", ""))
    lines = [f"{r['next_date']} {r['deal'] or r['contact']}: {r['note'] or r['kind']}"
             + (f" ({r['days_late']} day(s) late)" if r["days_late"] else "") for r in rows]
    return Out(f"{len(rows)} follow-up(s) due" + (":\n" + "\n".join(lines) if lines else "."), {"follow_ups": rows})


@TOOLS.tool("stale_deals", "Open deals nobody has touched for a while, with nothing planned.", Level.READ,
            {"as_of": DATE})
def stale_deals(s: CRM, a: dict[str, Any]) -> Out:
    rows = s.stale(a.get("as_of", ""))
    findings = [finding(f"{d['title']}: no contact for {d['idle_days']} days ({s.money(d['value'])})",
                        action="call or email, or move it to lost", entity=d["title"]) for d in rows]
    return Out(f"{len(rows)} deal(s) gone quiet.", {"stale": rows}, findings)


@TOOLS.tool("sales_forecast", "A month's forecast: best case, weighted by each stage's odds, and won so far.",
            Level.READ, {"month": {"type": "string", "description": "YYYY-MM. Default this month."}})
def sales_forecast(s: CRM, a: dict[str, Any]) -> Out:
    f = s.forecast(a.get("month", ""))
    text = (f"{f['month']}: {len(f['deals'])} deal(s) expected to close - best case {s.money(f['best_case'])}, "
            f"weighted {s.money(f['weighted'])}; won so far {s.money(f['won'])}.")
    findings = [finding(f"{f['undated']} open deal(s) have no expected close date",
                        action="give each a date so the forecast counts it")] if f["undated"] else []
    return Out(text, {"forecast": f}, findings)


@TOOLS.tool("find_contact", "Look up a contact by name, company or email.", Level.READ,
            {"query": {"type": "string"}}, ["query"])
def find_contact(s: CRM, a: dict[str, Any]) -> Out:
    c = s.contact(a["query"])
    return Out(f"{c['name']}" + (f", {c['company']}" if c["company"] else "") + (f", {c['email']}" if c["email"] else "")
               + (f", {c['phone']}" if c["phone"] else "") + ".", {"contact": c})


@TOOLS.tool("add_contact", "Add a contact, or fill in details for one already there.", Level.DRAFT,
            {"name": {"type": "string"}, "company": {"type": "string"}, "email": {"type": "string"},
             "phone": {"type": "string"}, "notes": {"type": "string"}}, ["name"])
def add_contact(s: CRM, a: dict[str, Any]) -> Out:
    c = s.add_contact(a["name"], company=a.get("company", ""), email=a.get("email", ""), phone=a.get("phone", ""),
                      notes=a.get("notes", ""))
    return Out(f"Contact #{c['id']}: {c['name']}" + (f" ({c['company']})." if c["company"] else "."), {"contact": c})


@TOOLS.tool("import_contacts", "Import contacts from a CSV export (HubSpot, Pipedrive, Outlook, a spreadsheet).",
            Level.DRAFT, {"path": {"type": "string"}}, ["path"])
def import_contacts(s: CRM, a: dict[str, Any]) -> Out:
    path = a["path"] if os.path.isabs(a["path"]) else os.path.join(s.root, a["path"])
    r = s.import_contacts(path)
    return Out(f"{r['added']} added, {r['updated']} updated, {r['skipped']} row(s) without a name skipped.",
               {"import": r})


@TOOLS.tool("add_deal", "Add a deal to the pipeline.", Level.DRAFT,
            {"title": {"type": "string"}, "contact": {"type": "string"}, "company": {"type": "string"},
             "value": {"type": "number"}, "stage": {"type": "string", "enum": list(OPEN)}, "expected": DATE},
            ["title"])
def add_deal(s: CRM, a: dict[str, Any]) -> Out:
    d = s.add_deal(a["title"], contact=a.get("contact"), company=a.get("company", ""), value=a.get("value", 0),
                   stage=a.get("stage") or "lead", expected=a.get("expected", ""))
    return Out("Added " + _deal_line(s, d) + ".", {"deal": d})


@TOOLS.tool("move_deal", "Move a deal to another stage; won or lost closes it (lost needs the reason).",
            Level.DRAFT, {"deal": DEAL, "stage": {"type": "string", "enum": list(STAGES)},
                          "reason": {"type": "string"}}, ["deal", "stage"])
def move_deal(s: CRM, a: dict[str, Any]) -> Out:
    d = s.move(a["deal"], a["stage"], reason=a.get("reason", ""))
    return Out(f"{d['title']} is now {d['stage']}.", {"deal": d})


@TOOLS.tool("log_activity", "Record a call, email, meeting or note, and when to follow up next.", Level.DRAFT,
            {"deal": DEAL, "contact": {"type": "string"}, "kind": {"type": "string", "enum": list(KINDS)},
             "note": {"type": "string"}, "next_date": DATE}, ["kind"])
def log_activity(s: CRM, a: dict[str, Any]) -> Out:
    r = s.log(a.get("deal"), a["kind"], a.get("note", ""), contact=a.get("contact"), next_date=a.get("next_date", ""))
    return Out(f"Logged {r['kind']}" + (f"; follow up {r['next_date']}." if r["next_date"] else "."), {"activity": r})


@TOOLS.tool("draft_follow_up_email", "Write a follow-up email to a deal's contact as a draft in Outbox. Not sent.",
            Level.DRAFT, {"deal": DEAL, "subject": {"type": "string"}, "body": {"type": "string"}}, ["deal", "body"])
def draft_follow_up_email(s: CRM, a: dict[str, Any]) -> Out:
    r = s.draft_email(a["deal"], a.get("subject", ""), a["body"])
    findings = [finding(f"No email address for the contact on {r['deal']}", action="add it before sending")] \
        if r["no_email"] else []
    return Out(f"Draft to {r['to']} in Outbox: {r['path']}", {"email": r}, findings)


TEMPLATE = AgentTemplate(
    id="sales_assistant", name="Sales Assistant", worker_type=EXT,
    mission="Keep the pipeline honest and moving: every follow-up done on time, quiet deals caught before they "
            "die, a forecast weighted by how far each deal really is, and the next email drafted.",
    handles=["contacts", "deals", "follow-ups", "forecast", "follow-up emails"],
    tools=TOOLS.names() + ["read_file", "list_dir"], max_auto_risk=RiskLevel.LOW,
    triggers=[Schedule(every="weekdays", at="08:30")],
    memory_schema={"contact": "what they care about, how they like to be contacted"},
    default_workflow="morning_pipeline", artifacts=["follow-up drafts", "forecast"],
    setup_questions=["Currency", "Days before a deal is stale", "Stage odds"], extension_id=EXT)

WORKFLOWS = [
    Workflow(id="morning_pipeline", name="Morning pipeline", description="Weekdays: the pipeline, follow-ups due, "
             "deals gone quiet.", template_id=TEMPLATE.id, trigger=Schedule(every="weekdays", at="08:30"),
             build_steps=lambda meta: [step("The pipeline", "pipeline_overview"),
                                       step("Follow-ups due", "follow_ups_due")],
             goal="Check the pipeline"),
    Workflow(id="weekly_forecast", name="Weekly forecast", description="Friday: this month's forecast and the "
             "deals with no date.", template_id=TEMPLATE.id, trigger=Schedule(every="weekly", at="15:00", weekday=4),
             build_steps=lambda meta: [step("This month's forecast", "sales_forecast"),
                                       step("Deals gone quiet", "stale_deals")],
             goal="Forecast the month"),
]

SKILLS = [Skill("sales.rules", "How this pack works", always=True, text=(
    "You keep a salesperson's pipeline. Log every call, email and meeting with log_activity and always set the "
    "next follow-up date - a deal with no next step goes quiet. Stages: lead, qualified, proposal, negotiation, "
    "won, lost; lost needs the reason. You never send email: draft_follow_up_email writes a draft the "
    "salesperson sends. Keep drafts short, specific to what was last discussed, with one clear next step."))]

INTENT_RULES = [
    rule(EXT, TEMPLATE.id, r"\b(?:my\s+)?pipeline\b", workflow="morning_pipeline",
         reply="Here's the pipeline: follow-ups due and anything gone quiet.",
         verb=r"\b(?:please\s+)?(?:check|show|run|review|go\s+through)\b"),
    rule(EXT, TEMPLATE.id, r"\b(?:sales\s+)?forecast\b", workflow="weekly_forecast",
         reply="Building this month's forecast, weighted by stage."),
    rule(EXT, TEMPLATE.id, r"\b(?:a\s+)?(?:call|meeting|chat)\s+with\b", reply="Logging it.",
         verb=r"\b(?:please\s+)?(?:log|record|note|add)\b"),
    rule(EXT, TEMPLATE.id, r"\bfollow[- ]up\s+(?:email|note|message)\b", reply="Drafting the follow-up."),
    rule(EXT, TEMPLATE.id, r"\b(?:new\s+)?(?:deal|lead|prospect)\b", reply="Adding it to the pipeline.",
         verb=r"\b(?:please\s+)?(?:add|log|create|record)\b"),
]


class SalesExtension(PackExtension):
    manifest = ExtensionManifest.load(HERE)
    STORE = CRM
    TOOLS = TOOLS
    TEMPLATES = [TEMPLATE]
    WORKFLOWS = WORKFLOWS
    SKILLS = SKILLS
    INTENT_RULES = INTENT_RULES
    FOLDER = "Sales"
    DEFAULTS = {"currency": "USD", "stale_days": 14, "email": ""}
    ROLE = "a sales assistant"
    ACTIONS = [("Pipeline", "morning_pipeline"), ("Forecast", "weekly_forecast")]
    NOTES = "Every tool is READ or DRAFT; follow-up emails are drafts in Outbox."
    EXAMPLES = [('check my pipeline', 'morning_pipeline'), ('run the sales forecast', 'weekly_forecast'), ('log a call with Priya', ''), ('add a new deal for Contoso', ''), ('draft a follow-up email to Priya', '')]

    def context_line(self, s: CRM) -> str:
        p = s.pipeline()
        return (f"Sales: {s.currency} · open pipeline {s.money(p['open_value'])}, weighted {s.money(p['weighted'])} · "
                f"stale after {s.stale_days} days")

    def snapshot(self, s: CRM) -> dict[str, Any]:
        o = s.overview()
        p = o["pipeline"]
        return {"title": "Sales", "meta": f"{s.currency} · stale after {s.stale_days} days",
                "numbers": [("Open pipeline", s.money(p["open_value"])), ("Weighted", s.money(p["weighted"])),
                            ("Follow-ups due", len(o["follow_ups"])), ("Gone quiet", len(o["stale"]))],
                "lists": [("Follow-ups due", [(f"{r['next_date']} {r['deal'] or r['contact']}: {r['note'] or r['kind']}", "")
                                              for r in o["follow_ups"]]),
                          ("Closing this week", [(_deal_line(s, d), "") for d in o["closing_soon"]]),
                          ("Gone quiet", [(f"{d['title']} · {d['idle_days']} days", "") for d in o["stale"]])]}
