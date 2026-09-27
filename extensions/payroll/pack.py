"""The Payroll pack: tools, the payroll assistant, its checklists.

Finalising a run locks it and writes the payslips, the bank payment file
and the journal: POST, asked every time, and refused while a blocking
problem stands (negative net pay, a missing or shared bank account, pay
under the minimum rate). Nothing is paid or filed from here - the bank
file is uploaded by a person, the filing is made in the payroll software.

READ   payroll_overview, pay_run_summary, check_pay_run, leave_balance
DRAFT  add_employee, record_leaver, start_pay_run, import_timesheet, import_deductions, set_pay_line, record_leave
POST   finalise_pay_run
"""
from __future__ import annotations

import os
from typing import Any

from agents.core import RiskLevel
from agents.extensions import AgentTemplate, ExtensionManifest, Schedule, Skill, Workflow
from extensions.packkit import Level, Out, PackExtension, ToolBook, finding, rule, step

from .payroll import Payroll

EXT = "payroll"
HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = ToolBook(EXT, noun="the pay run")
PERIOD = {"type": "string", "description": "Pay period YYYY-MM."}


def _path(p: Payroll, path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(p.root, path)


@TOOLS.tool("payroll_overview", "This month's pay run: who is employed, whether the run is started or final, "
            "its problems, the filing date.", Level.READ, {"as_of": {"type": "string"}})
def payroll_overview(p: Payroll, a: dict[str, Any]) -> Out:
    o = p.overview(a.get("as_of", ""))
    run = o["run"]
    text = (f"{o['period']}: {o['employees']} employee(s); run " + (f"{run['status']}" if run else "not started")
            + (f"; filing due {o['filing_due']}" if o["filing_due"] else "") + ".")
    findings = [finding(x["text"], action="fix before finalising" if x["blocking"] else "check it", entity=x["employee"])
                for x in (o["check"] or {}).get("problems", [])]
    return Out(text, {"overview": o}, findings)


@TOOLS.tool("pay_run_summary", "A pay run line by line: hours, gross, deductions, net, and the totals.", Level.READ,
            {"period": PERIOD}, ["period"])
def pay_run_summary(p: Payroll, a: dict[str, Any]) -> Out:
    r = p.run(a["period"])
    lines = [f"{x['code']} {x['name']}: gross {p.money(x['gross'])}, net {p.money(x['net'])}" for x in r["lines"]]
    t = r["totals"]
    return Out(f"{r['period']} ({r['status']}): gross {p.money(t['gross'])}, net {p.money(t['net'])}\n" + "\n".join(lines),
               {"run": r})


@TOOLS.tool("check_pay_run", "Check a pay run the way a reviewer would: pay swings against last month, starters, "
            "leavers, shared bank accounts, missing hours or deductions, pay under the minimum.", Level.READ,
            {"period": PERIOD}, ["period"])
def check_pay_run(p: Payroll, a: dict[str, Any]) -> Out:
    c = p.check(a["period"])
    findings = [finding(x["text"], action="fix before finalising" if x["blocking"] else "check it",
                        entity=x["employee"], severity="high" if x["blocking"] else "review") for x in c["problems"]]
    return Out(f"{c['period']}: {len(c['problems'])} thing(s) to look at, {len(c['blocking'])} blocking"
               + (f" (compared with {c['previous']})." if c["previous"] else " (no earlier final run to compare)."),
               {"check": c}, findings)


@TOOLS.tool("leave_balance", "Holiday earned so far this leave year, taken and left.", Level.READ,
            {"employee": {"type": "string"}, "as_of": {"type": "string"}}, ["employee"])
def leave_balance(p: Payroll, a: dict[str, Any]) -> Out:
    b = p.leave_balance(a["employee"], a.get("as_of", ""))
    return Out(f"{b['employee']}: {b['earned']:g} day(s) earned since {b['year_start']}, {b['taken']:g} taken, "
               f"{b['left']:g} left.", {"leave": b})


@TOOLS.tool("add_employee", "Add or update an employee: hourly (rate an hour) or salary (a year).", Level.DRAFT,
            {"code": {"type": "string"}, "name": {"type": "string"}, "pay_type": {"type": "string",
                                                                                  "enum": ["hourly", "salary"]},
             "rate": {"type": "number"}, "bank": {"type": "string"}, "start": {"type": "string"},
             "leave_days": {"type": "number"}, "pension_pct": {"type": "number"}}, ["code", "name", "pay_type", "rate"])
def add_employee(p: Payroll, a: dict[str, Any]) -> Out:
    e = p.add_employee(a["code"], a["name"], a["pay_type"], a["rate"], bank=a.get("bank", ""), start=a.get("start", ""),
                       leave_days=a.get("leave_days"), pension_pct=a.get("pension_pct"))
    return Out(f"{e['code']} {e['name']}: {e['pay_type']} at {p.money(e['rate'])}.", {"employee": e})


@TOOLS.tool("record_leaver", "Record an employee's last day.", Level.DRAFT,
            {"employee": {"type": "string"}, "end": {"type": "string"}}, ["employee", "end"])
def record_leaver(p: Payroll, a: dict[str, Any]) -> Out:
    e = p.leaver(a["employee"], a["end"])
    return Out(f"{e['name']} leaves on {e['end']}.", {"employee": e})


@TOOLS.tool("start_pay_run", "Start a period's pay run with everyone employed in it.", Level.DRAFT,
            {"period": PERIOD}, ["period"])
def start_pay_run(p: Payroll, a: dict[str, Any]) -> Out:
    r = p.start(a["period"])
    return Out(f"{r['period']} run started: {len(r['lines'])} people, gross so far {p.money(r['totals']['gross'])}.",
               {"run": r})


@TOOLS.tool("import_timesheet", "Import hours for hourly staff (CSV: code or name, hours, overtime).", Level.DRAFT,
            {"period": PERIOD, "path": {"type": "string"}}, ["period", "path"])
def import_timesheet(p: Payroll, a: dict[str, Any]) -> Out:
    r = p.import_timesheet(a["period"], _path(p, a["path"]))
    findings = [finding(f"Timesheet names nobody on the payroll: {', '.join(r['unknown'])}",
                        action="add them, or fix the names")] if r["unknown"] else []
    return Out(f"Hours for {r['people']} people.", {"import": r}, findings)


@TOOLS.tool("import_deductions", "Import tax, social security, pension and other deductions from the payroll "
            "software's export (CSV by code or name).", Level.DRAFT,
            {"period": PERIOD, "path": {"type": "string"}}, ["period", "path"])
def import_deductions(p: Payroll, a: dict[str, Any]) -> Out:
    r = p.import_deductions(a["period"], _path(p, a["path"]))
    findings = [finding(f"Deductions for people not on the payroll: {', '.join(r['unknown'])}",
                        action="check the export")] if r["unknown"] else []
    return Out(f"Deductions for {r['updated']} people.", {"import": r}, findings)


@TOOLS.tool("set_pay_line", "Change one person's hours or deductions in a draft run.", Level.DRAFT,
            {"period": PERIOD, "employee": {"type": "string"}, "hours": {"type": "number"},
             "overtime": {"type": "number"}, "tax": {"type": "number"}, "social": {"type": "number"},
             "pension": {"type": "number"}, "other": {"type": "number"}}, ["period", "employee"])
def set_pay_line(p: Payroll, a: dict[str, Any]) -> Out:
    ln = p.set_line(a["period"], a["employee"], **{k: a.get(k) for k in ("hours", "overtime", "tax", "social",
                                                                         "pension", "other")})
    return Out(f"{ln['name']}: gross {p.money(ln['gross'])}, net {p.money(ln['net'])}.", {"line": ln})


@TOOLS.tool("record_leave", "Record holiday taken.", Level.DRAFT,
            {"employee": {"type": "string"}, "day": {"type": "string"}, "days": {"type": "number"},
             "note": {"type": "string"}}, ["employee", "day"])
def record_leave(p: Payroll, a: dict[str, Any]) -> Out:
    b = p.record_leave(a["employee"], a["day"], a.get("days", 1), a.get("note", ""))
    return Out(f"Recorded. {b['employee']} has {b['left']:g} day(s) left.", {"leave": b})


@TOOLS.tool("finalise_pay_run", "Lock a checked pay run and write payslips, the bank payment file and the journal. "
            "Asks first; refused while a blocking problem stands. Nobody is paid.", Level.POST,
            {"period": PERIOD}, ["period"])
def finalise_pay_run(p: Payroll, a: dict[str, Any]) -> Out:
    f = p.finalise(a["period"])
    return Out(f"{f['period']} is final: {len(f['payslips'])} payslip(s), net {p.money(f['totals']['net'])}. Upload the "
               f"bank file yourself: {f['bank_file']}. Journal: {f['journal']}", {"final": f})


TEMPLATE = AgentTemplate(
    id="payroll_assistant", name="Payroll Assistant", worker_type=EXT,
    mission="Get everyone paid the right amount on time: hours and salaries into gross pay, the payroll software's "
            "deductions in, every check a reviewer would make, and payslips, the bank file and the journal once the "
            "employer approves.",
    handles=["pay runs", "timesheets", "deductions", "payroll checks", "payslips", "leave"],
    tools=TOOLS.names() + ["read_file", "list_dir"], max_auto_risk=RiskLevel.LOW,
    triggers=[Schedule(every="monthly", at="09:00", day=20)],
    memory_schema={"employee": "pay history, changes agreed"}, default_workflow="pay_run_check",
    artifacts=["payslips", "bank payment file", "payroll journal"],
    setup_questions=["Currency", "Overtime multiplier", "Minimum hourly rate", "Leave days a year", "Filing day"],
    extension_id=EXT)

WORKFLOWS = [
    Workflow(id="pay_run_check", name="Pay run check", description="The 20th: this month's run and every check.",
             template_id=TEMPLATE.id, trigger=Schedule(every="monthly", at="09:00", day=20),
             build_steps=lambda meta: [step("This month's run", "payroll_overview")],
             goal="Check this month's pay run"),
]

SKILLS = [Skill("payroll.rules", "How this pack works", always=True, text=(
    "You prepare payroll; you never work out tax or social security - those are each country's law and come from "
    "the employer's payroll software (import_deductions) or are typed in by the employer. Never guess a deduction. "
    "Run order: start_pay_run, import_timesheet, import_deductions, check_pay_run, then the employer finalises. "
    "Nobody is paid from here and nothing is filed: the employer uploads the bank file and files in their payroll "
    "software. Pay details are private: never repeat one person's pay to another."))]

INTENT_RULES = [
    rule(EXT, TEMPLATE.id, r"\b(?:the\s+|this\s+month'?s\s+|my\s+)?(?:pay\s*run|payroll)\b", workflow="pay_run_check",
         reply="Checking this month's pay run.", verb=r"\b(?:please\s+)?(?:check|review|run|do|prepare|look\s+at)\b"),
    rule(EXT, TEMPLATE.id, r"\b(?:holiday|annual\s+leave|leave)\s+(?:balance|left)\b", reply="Working out the leave.",
         verb=r"\b(?:please\s+)?(?:check|show|get|work\s+out)\b"),
    rule(EXT, TEMPLATE.id, r"\b(?:new\s+)?(?:starter|employee|hire)\b", reply="Adding them to the payroll.",
         verb=r"\b(?:please\s+)?(?:add|set\s+up|record)\b"),
]


class PayrollExtension(PackExtension):
    manifest = ExtensionManifest.load(HERE)
    STORE = Payroll
    TOOLS = TOOLS
    TEMPLATES = [TEMPLATE]
    WORKFLOWS = WORKFLOWS
    SKILLS = SKILLS
    INTENT_RULES = INTENT_RULES
    FOLDER = "Payroll"
    DEFAULTS = {"employer": "", "currency": "GBP", "overtime_multiplier": 1.5, "minimum_hourly": 0, "leave_days": 28,
                "leave_year_start_month": 1, "pension_pct": 0, "variance_pct": 10, "filing_day": 0}
    ROLE = "the payroll assistant for a small employer"
    ACTIONS = [("Check this month", "pay_run_check")]
    NOTES = "Finalising a run asks first (POST) and is refused while a blocking problem stands; nothing is paid or filed."
    EXAMPLES = [("please check the pay run", "pay_run_check"), ("prepare this month's pay run", "pay_run_check"),
                ("check Sam's holiday balance", ""), ("add a new starter called Ana", "")]

    def context_line(self, p: Payroll) -> str:
        o = p.overview()
        return (f"Payroll: {o['employees']} employee(s) · {p.currency} · overtime x{p.num('overtime_multiplier', 1.5):g} · "
                f"{o['period']} run {(o['run'] or {}).get('status', 'not started')}")

    def snapshot(self, p: Payroll) -> dict[str, Any]:
        o = p.overview()
        probs = (o["check"] or {}).get("problems", [])
        return {"title": p.cfg.get("employer") or "Payroll", "meta": f"{p.currency} · {o['period']}",
                "numbers": [("Employees", o["employees"]), ("This month", (o["run"] or {}).get("status", "not started")),
                            ("To check", len(probs)), ("Blocking", sum(x["blocking"] for x in probs))],
                "lists": [("To check", [(x["text"], "") for x in probs]), ("", []), ("", [])]}
