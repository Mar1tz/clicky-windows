"""The Tax practice pack: tools, the practice assistant, its checklists.

Every tool is READ or DRAFT: nothing is filed or sent. Chasers are .eml
drafts; filing is done by the practitioner in the authority's portal and
recorded here with the tax filed, so the assessment can be checked.

READ   practice_overview, tax_calendar, return_status
DRAFT  add_tax_return, record_documents, chase_documents, build_workpaper, set_return_stage, record_assessment
"""
from __future__ import annotations

import os
from typing import Any

from agents.core import RiskLevel
from agents.extensions import AgentTemplate, ExtensionManifest, Schedule, Skill, Workflow
from extensions.packkit import Level, Out, PackExtension, ToolBook, finding, rule, step

from .practice import STAGES, Practice

EXT = "taxpractice"
HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = ToolBook(EXT, noun="the practice's records")
RETURN = {"type": "string", "description": "The client's name (or the return's id)."}
KIND = {"type": "string", "description": "Return type, e.g. individual, company, vat."}


def _line(r: dict[str, Any]) -> str:
    return (f"#{r['id']} {r['client']} {r['kind']} {r['year']}: {r['stage']}, due {r['due']}"
            + (f", missing {len(r['missing'])}" if r["missing"] else ""))


@TOOLS.tool("practice_overview", "Every open return: late, due soon with documents missing, by stage, and filed "
            "returns waiting for an assessment.", Level.READ, {"as_of": {"type": "string"}})
def practice_overview(p: Practice, a: dict[str, Any]) -> Out:
    o = p.overview(a.get("as_of", ""))
    text = (f"{len(o['open'])} open return(s): {len(o['late'])} past the deadline, {len(o['urgent'])} due within "
            f"{int(p.num('urgent_days', 30))} days with documents missing; {len(o['to_assess'])} filed and waiting "
            "for an assessment.")
    findings = [finding(f"{r['client']}: {r['kind']} {r['year']} was due {r['due']} ({-r['days_left']} days ago)",
                        action="file it, or record why it is late", entity=r["client"], severity="high") for r in o["late"]]
    findings += [finding(f"{r['client']}: {r['kind']} {r['year']} is due in {r['days_left']} days and "
                         f"{len(r['missing'])} document(s) are missing", ", ".join(r["missing"]),
                         action="call the client", entity=r["client"]) for r in o["urgent"]]
    return Out(text, {"overview": o}, findings)


@TOOLS.tool("tax_calendar", "Open returns in deadline order.", Level.READ)
def tax_calendar(p: Practice, a: dict[str, Any]) -> Out:
    rows = p.returns()
    return Out(f"{len(rows)} open return(s)" + (":\n" + "\n".join(_line(r) for r in rows) if rows else "."),
               {"returns": rows})


@TOOLS.tool("return_status", "One client's return: stage, deadline, documents in and missing.", Level.READ,
            {"client": RETURN, "kind": KIND, "year": {"type": "string"}}, ["client"])
def return_status(p: Practice, a: dict[str, Any]) -> Out:
    r = p.ret(a["client"], a.get("kind", ""), a.get("year", ""))
    got = [d["name"] for d in r["documents"] if d["received"]]
    return Out(_line(r) + f". In: {', '.join(got) or 'nothing yet'}. Missing: {', '.join(r['missing']) or 'nothing'}.",
               {"return": r})


@TOOLS.tool("add_tax_return", "Open a return for a client (created if new) with its deadline and the documents "
            "it needs (the practice's checklist for that type unless given).", Level.DRAFT,
            {"client": {"type": "string"}, "kind": KIND, "year": {"type": "string"},
             "period_end": {"type": "string"}, "due": {"type": "string"}, "email": {"type": "string"},
             "documents": {"type": "array", "items": {"type": "string"}}}, ["client", "kind", "year", "period_end"])
def add_tax_return(p: Practice, a: dict[str, Any]) -> Out:
    r = p.add_return(a["client"], a["kind"], a["year"], a["period_end"], due=a.get("due", ""), email=a.get("email", ""),
                     documents=a.get("documents"))
    return Out(f"Opened {_line(r)}. Needs: {', '.join(r['missing']) or 'nothing listed'}.", {"return": r})


@TOOLS.tool("record_documents", "Mark documents received for a client's return.", Level.DRAFT,
            {"client": RETURN, "kind": KIND, "year": {"type": "string"},
             "documents": {"type": "array", "items": {"type": "string"}}, "note": {"type": "string"}},
            ["client", "documents"])
def record_documents(p: Practice, a: dict[str, Any]) -> Out:
    r = p.receive(p.ret(a["client"], a.get("kind", ""), a.get("year", ""))["id"], a["documents"], note=a.get("note", ""))
    findings = [finding(f"Not on {r['client']}'s checklist: {', '.join(r['unknown'])}",
                        action="check the name, or add it to the checklist")] if r["unknown"] else []
    return Out(f"{r['client']}: {len(r['missing'])} document(s) still missing; stage {r['stage']}.", {"return": r},
               findings)


@TOOLS.tool("chase_documents", "Draft a chaser for every client still missing documents (a week apart, friendly, "
            "then firm, then final). Not sent.", Level.DRAFT, {"as_of": {"type": "string"}})
def chase_documents(p: Practice, a: dict[str, Any]) -> Out:
    c = p.chase(a.get("as_of", ""))
    findings = [finding(f"{x['client']}: three chasers sent for the {x['return']} return and still missing documents",
                        action="phone them", entity=x["client"], severity="high") for x in c["exhausted"]]
    text = (f"{len(c['drafted'])} chaser(s) drafted" + (": " + "; ".join(f"{x['client']} ({x['tone']})" for x in c["drafted"])
                                                        if c["drafted"] else "")
            + (f". Chased this week already: {', '.join(c['waiting'])}." if c["waiting"] else "."))
    return Out(text, {"chase": c}, findings)


@TOOLS.tool("build_workpaper", "Total a client's transactions (CSV: date, description, amount, category) by "
            "category into a workpaper, list what has no category, and check it ties out.", Level.DRAFT,
            {"client": RETURN, "kind": KIND, "year": {"type": "string"}, "path": {"type": "string"}},
            ["client", "path"])
def build_workpaper(p: Practice, a: dict[str, Any]) -> Out:
    path = a["path"] if os.path.isabs(a["path"]) else os.path.join(p.root, a["path"])
    w = p.workpaper(p.ret(a["client"], a.get("kind", ""), a.get("year", ""))["id"], path)
    findings = [finding(f"{w['client']}: {len(w['uncategorised'])} transaction(s) need a category",
                        "; ".join(f"{x['date']} {x['description']} {x['amount']:.2f}" for x in w["uncategorised"][:10]),
                        action="categorise them before the return is prepared", entity=w["client"])] \
        if w["uncategorised"] else []
    return Out(f"Workpaper for {w['client']} ({w['transactions']} transactions, total {w['total']:.2f}"
               + (", ties out" if w["ties_out"] else ", DOES NOT tie out") + "):\n"
               + "\n".join(f"  {k}: {v:,.2f}" for k, v in sorted(w["totals"].items())) + f"\nFile: {w['path']}",
               {"workpaper": w}, findings)


@TOOLS.tool("set_return_stage", "Move a return along; 'filed' needs the tax on the return as filed.", Level.DRAFT,
            {"client": RETURN, "kind": KIND, "year": {"type": "string"},
             "stage": {"type": "string", "enum": [s for s in STAGES if s != "assessed"]},
             "filed_tax": {"type": "number"}}, ["client", "stage"])
def set_return_stage(p: Practice, a: dict[str, Any]) -> Out:
    r = p.set_stage(p.ret(a["client"], a.get("kind", ""), a.get("year", ""))["id"], a["stage"],
                    filed_tax=a.get("filed_tax"))
    return Out(f"{_line(r)}.", {"return": r})


@TOOLS.tool("record_assessment", "Record the tax the authority assessed and compare it with the tax filed.",
            Level.DRAFT, {"client": RETURN, "kind": KIND, "year": {"type": "string"}, "assessed_tax": {"type": "number"}},
            ["client", "assessed_tax"])
def record_assessment(p: Practice, a: dict[str, Any]) -> Out:
    r = p.assessment(p.ret(a["client"], a.get("kind", ""), a.get("year", ""))["id"], a["assessed_tax"])
    findings = [] if r["agrees"] else [finding(
        f"{r['client']}: assessed {r['assessed_tax']:,.2f} but filed {r['filed_tax']:,.2f} "
        f"({r['difference']:+,.2f})", action="find out why before the client pays", entity=r["client"], severity="high")]
    return Out(f"{r['client']} {r['kind']} {r['year']}: assessment " + ("agrees" if r["agrees"] else "differs") + ".",
               {"return": r}, findings)


TEMPLATE = AgentTemplate(
    id="practice_assistant", name="Practice Assistant", worker_type=EXT,
    mission="Keep a tax practice ahead of its deadlines: every client's documents chased in time, workpapers that "
            "tie out, nothing filed late, and every assessment checked against what was filed.",
    handles=["deadlines", "document chasing", "workpapers", "return stages", "assessment checks"],
    tools=TOOLS.names() + ["read_file", "list_dir"], max_auto_risk=RiskLevel.LOW,
    triggers=[Schedule(every="weekly", at="08:00", weekday=0)],
    memory_schema={"client": "how quickly they send things, who to call"}, default_workflow="weekly_deadlines",
    artifacts=["chasers", "workpapers", "calendar"],
    setup_questions=["Firm name", "Checklists per return type", "Deadline rules", "Days between chasers"],
    extension_id=EXT)

WORKFLOWS = [
    Workflow(id="weekly_deadlines", name="Weekly deadlines", description="Monday: the calendar, the late and the "
             "urgent, a chaser to everyone still missing documents.", template_id=TEMPLATE.id,
             trigger=Schedule(every="weekly", at="08:00", weekday=0),
             build_steps=lambda meta: [step("Where every return stands", "practice_overview"),
                                       step("Chase missing documents", "chase_documents")],
             goal="Run the weekly deadlines"),
]

SKILLS = [Skill("taxpractice.rules", "How this pack works", always=True, text=(
    "You support a tax practitioner; you never give tax advice or work out tax due - the practitioner does, in "
    "their own software. Deadlines and checklists are the practitioner's; never invent a filing date. Chasers "
    "are drafts the practitioner sends. When a return is filed, record the tax filed; when the assessment "
    "arrives, record it, and any difference is flagged for the practitioner."))]

INTENT_RULES = [
    rule(EXT, TEMPLATE.id, r"\b(?:tax\s+)?(?:deadlines|calendar)\b", workflow="weekly_deadlines",
         reply="Checking every return's deadline and chasing missing documents.",
         verb=r"\b(?:please\s+)?(?:check|run|review|go\s+through|show)\b"),
    rule(EXT, TEMPLATE.id, r"\b(?:clients?\s+for\s+(?:their\s+)?(?:documents|records|paperwork)|missing\s+documents)\b",
         workflow="weekly_deadlines", reply="Drafting chasers for everyone still missing documents.",
         verb=r"\b(?:please\s+)?(?:chase|remind|nudge)\b"),
    rule(EXT, TEMPLATE.id, r"\bworkpaper\b", reply="Building the workpaper."),
    rule(EXT, TEMPLATE.id, r"\b(?:tax\s+)?assessment\b", reply="Checking it against what was filed.",
         verb=r"\b(?:please\s+)?(?:check|record|log|compare)\b"),
]


class TaxPracticeExtension(PackExtension):
    manifest = ExtensionManifest.load(HERE)
    STORE = Practice
    TOOLS = TOOLS
    TEMPLATES = [TEMPLATE]
    WORKFLOWS = WORKFLOWS
    SKILLS = SKILLS
    INTENT_RULES = INTENT_RULES
    FOLDER = "Tax practice"
    DEFAULTS = {"firm": "", "chase_days": 7, "urgent_days": 30, "assessment_tolerance": 1.0, "checklists": {},
                "deadline_rules": {}}
    ROLE = "the assistant in a tax practice"
    ACTIONS = [("Weekly deadlines", "weekly_deadlines")]
    NOTES = "Every tool is READ or DRAFT: nothing is filed or sent from this pack."
    EXAMPLES = [("check the tax deadlines", "weekly_deadlines"), ("chase clients for their documents", "weekly_deadlines"),
                ("build the workpaper for Mrs Hale", ""), ("record the assessment for Mr Ode", "")]

    def context_line(self, p: Practice) -> str:
        o = p.overview()
        return (f"Tax practice: {p.firm} · {len(o['open'])} open return(s), {len(o['late'])} late · chasers every "
                f"{int(p.num('chase_days', 7))} days")

    def snapshot(self, p: Practice) -> dict[str, Any]:
        o = p.overview()
        return {"title": p.firm if p.cfg.get("firm") else "Tax practice", "meta": f"{len(o['open'])} open returns",
                "numbers": [("Late", len(o["late"])), ("Urgent", len(o["urgent"])),
                            ("Waiting docs", o["by_stage"]["waiting_docs"]), ("To assess", len(o["to_assess"]))],
                "lists": [("Next deadlines", [(_line(r), "") for r in o["open"][:15]]),
                          ("Missing documents", [(f"{r['client']}: {', '.join(r['missing'])}", "") for r in o["urgent"]]),
                          ("Filed, no assessment", [(f"{r['client']} {r['kind']} {r['year']}", "") for r in o["to_assess"]])]}
