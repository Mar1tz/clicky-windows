"""The freelancer's agent: one role, its checklists and what it knows.

Two workflows are scripted end to end, so they run (and are tested)
offline with no model: the weekly admin a freelancer puts off, and the
year-end pack. Anything else - "log 3 hours for Acme", "quote Bloom for
a logo" - is planned by the model with the same tool belt.

The worker is the Accounting pack's: a scripted check that cannot run
becomes a note for the person and the checklist carries on, and a POST
tool that fails always fails its step.
"""
from __future__ import annotations

from typing import Any

from agents.core import AgentContext, RiskLevel, Step, ToolCall
from agents.extensions import AgentTemplate, Schedule, Skill, Trigger, Workflow
from extensions.accounting.worker import AccountingWorker

from .tools import REGISTRY

EXT = "freelancer"
WORKER = "freelancer"

TEMPLATE = AgentTemplate(
    id="freelance_admin", name="Freelance Admin Agent", worker_type=WORKER,
    mission="Keep a one-person business paid and in order: hours billed, quotes out, late payers chased, receipts "
            "in the books, tax put aside, and a year-end pack the accountant can use.",
    handles=["time tracking", "quotes", "invoices", "late payers", "receipts", "tax set-aside", "year-end pack"],
    tools=[d.name for d in REGISTRY] + ["read_file", "list_dir"],
    max_auto_risk=RiskLevel.LOW,
    triggers=[Schedule(every="weekly", at="16:00", weekday=4)],
    memory_schema={"client": "rate, payment habits, who to email"},
    default_workflow="weekly_admin",
    artifacts=["invoice drafts", "reminder emails in Outbox", "year-end pack"],
    setup_questions=["Business name", "Currency", "Hourly rate", "Payment terms", "Share of income to set aside"],
    extension_id=EXT)

TEMPLATES = [TEMPLATE]
BY_ID = {t.id: t for t in TEMPLATES}


def _s(title: str, tool: str = "", args: dict[str, Any] | None = None, description: str = "") -> Step:
    return Step(title=title, description=description or title,
                tool_calls=[ToolCall(tool, dict(args or {}))] if tool else [])


def weekly_admin(meta: dict[str, Any]) -> list[Step]:
    return [_s("Where the business stands", "freelance_overview"),
            _s("Hours not yet billed", "list_unbilled_time"),
            _s("Reminders for late payers", "chase_late_payers"),
            _s("Tax put aside this year", "set_aside_report")]


def year_end(meta: dict[str, Any]) -> list[Step]:
    args = {"year": int(meta["year"])} if str(meta.get("year") or "").isdigit() else {}
    return [_s("Where the business stands", "freelance_overview"),
            _s("Build the year-end pack", "prepare_year_end_pack", args)]


def issue_run(meta: dict[str, Any]) -> list[Step]:
    return [_s(f"Issue invoice draft #{meta.get('draft_id', '')}", "issue_invoice",
               {"draft_id": int(meta.get("draft_id") or 0)})]


def _wf(id_: str, name: str, desc: str, trigger: Trigger, build, goal: str) -> Workflow:
    return Workflow(id=id_, name=name, description=desc, template_id=TEMPLATE.id, trigger=trigger,
                    build_steps=build, goal=goal, extension_id=EXT)


WORKFLOWS = [
    _wf("weekly_admin", "Weekly admin", "Friday afternoon: where things stand, unbilled hours, reminders drafted "
        "for late payers, tax set aside.", Schedule(every="weekly", at="16:00", weekday=4), weekly_admin,
        "Do the weekly admin"),
    _wf("year_end", "Year-end pack", "Income, expenses, invoices and set-aside as files for the accountant.",
        Trigger(), year_end, "Prepare the year-end pack"),
    # Started from the dashboard's Issue button; the tool asks for approval.
    _wf("issue_invoice", "Issue invoice", "Put one invoice draft on the books.", Trigger(), issue_run,
        "Issue the invoice"),
]
WORKFLOW_BY_ID = {w.id: w for w in WORKFLOWS}

SKILLS = [
    Skill("freelance.rules", "How this pack works", always=True, text=(
        "You are the back office of a one-person business. Drafts first: log_time, quotes, invoice drafts, "
        "reminders and receipts are drafts. Only issue_invoice, record_payment and post_expenses change the "
        "books, and each asks the freelancer. You never send email: invoices and reminders are written to the "
        "Outbox folder as .eml files for the freelancer to open and send. Money is in the business's currency.")),
    Skill("freelance.tax", "Setting tax aside", always=True, text=(
        "The set-aside percentage is the freelancer's own choice, applied to every payment received. It is a "
        "savings habit, not a tax calculation: never tell them what they owe; that is their accountant's job. "
        "The year-end pack gives the accountant income, expenses, invoices and the set-aside as files.")),
]


class FreelanceWorker(AccountingWorker):
    worker_type = WORKER
    max_iterations = 14

    def system_prompt(self, ctx: AgentContext) -> str:
        return (f"You are {ctx.agent.name}, the back office of a freelancer. Mission: {TEMPLATE.mission} "
                f"You work only inside the business folder {ctx.workspace}. Use the freelancer tools for every "
                "number and every change. When the step is done, reply with a two-line summary (what was done; "
                "what needs the freelancer) and no tool call.")
