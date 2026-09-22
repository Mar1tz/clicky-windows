"""Accounting workflows: checklists the planner turns into steps.

Every mechanical check is a Step with a scripted tool call, so a month-end
always runs every check, in order, with no model involved - which is also
what lets the whole workflow run (and be tested) offline on the demo books.
Steps that need judgement (drafting adjustments, writing commentary) are
left open for the worker's model, and say exactly what is allowed.

    Invoice processing  new document in the Inbox -> extract -> duplicate -> supplier -> PO -> coding
                        -> draft bill -> file original -> flag only the uncertain
    Debtors run         every weekday morning -> aged debtors -> check receipts first -> reminders
                        drafted -> big/late accounts escalated -> nothing sent
    Month-end           checklist -> bank -> controls -> debtors/creditors -> suspense -> accruals ->
                        payroll -> journals -> tax -> variances -> draft journals -> pack -> exceptions
"""
from __future__ import annotations

from typing import Any, Callable

from agents.core import Step, ToolCall
from agents.extensions import FolderWatch, Schedule, Trigger, Workflow


def _s(title: str, tool: str = "", args: dict[str, Any] | None = None, description: str = "") -> Step:
    return Step(title=title, description=description or title,
                tool_calls=[ToolCall(tool, dict(args or {}))] if tool else [])


def _p(meta: dict[str, Any]) -> dict[str, Any]:
    return {"period": meta["period"]} if meta.get("period") else {}


def invoice_processing(meta: dict[str, Any]) -> list[Step]:
    files = [f for f in meta.get("files") or [] if f.lower().endswith((".pdf", ".eml", ".jpg", ".jpeg", ".png",
                                                                        ".docx", ".txt"))]
    steps = [_s("Load client and period", "accounting_context"),
             _s("Import any bank statements", "import_inbox_statements"),
             _s("Process invoices in the Inbox", "process_inbox", {"files": files} if files else {}),
             _s("Reconcile supplier statements", "reconcile_inbox_statements"),
             _s("Review uncertain invoices", description=(
                 "Look at the invoices this run could not draft with confidence (list_exceptions, list_drafts "
                 "status review). Where the document makes the coding clear, draft it with create_draft_bill at "
                 "your honest confidence. Otherwise leave the exception for the accountant. Never post.")),
             _s("Exception summary", "summarise_exceptions", {"title": "Invoice processing"})]
    return steps


def bookkeeping(meta: dict[str, Any]) -> list[Step]:
    return [_s("Load client and period", "accounting_context"),
            _s("Import bank statements from the Inbox", "import_inbox_statements"),
            _s("Draft matches and codings for bank lines", "draft_from_suggestions"),
            _s("Process invoices and receipts", "process_inbox"),
            _s("Find transactions without documents", "find_missing_document", _p(meta)),
            _s("Exception summary", "summarise_exceptions", {"title": "Bookkeeping"})]


def debtors_run(meta: dict[str, Any]) -> list[Step]:
    return [_s("Load client and period", "accounting_context"),
            _s("Check for receipts not yet allocated", "suggest_reconciliation"),
            _s("Classify overdue customers, draft reminders, escalate", "prepare_collection_run"),
            _s("Exception summary", "summarise_exceptions", {"title": "Debtors run"})]


def ap_run(meta: dict[str, Any]) -> list[Step]:
    return [_s("Load client and period", "accounting_context"),
            _s("Process supplier invoices in the Inbox", "process_inbox"),
            _s("Duplicate invoices", "detect_duplicate_invoice"),
            _s("Duplicate and unexplained payments", "detect_duplicate_payment", _p(meta)),
            _s("Purchase-order matching", "match_po_invoice"),
            _s("Supplier statement reconciliations", "reconcile_inbox_statements"),
            _s("Draft the payment batch", "create_draft_payment_batch"),
            _s("Exception summary", "summarise_exceptions", {"title": "Accounts payable run"})]


MONTH_END_CHECKS: list[tuple[str, str, bool]] = [
    # (title, tool, takes period)
    ("Bank: suggest reconciliation of open lines", "suggest_reconciliation", False),
    ("Bank reconciliation schedule", "create_reconciliation_schedule", False),
    ("Control accounts: debtors, creditors, bank", "check_control_accounts", False),
    ("Debtors review", "find_overdue_invoices", False),
    ("Creditors: duplicate invoices", "detect_duplicate_invoice", False),
    ("Creditors: duplicate and unexplained payments", "detect_duplicate_payment", True),
    ("Purchase orders", "match_po_invoice", False),
    ("Suspense and clearing accounts", "review_suspense", False),
    ("Accruals and prepayments", "review_accruals_prepayments", True),
    ("Payroll to general ledger", "payroll_review", True),
    ("Journal testing", "find_unusual_journal", True),
    ("Coding review", "find_misclassification", True),
    ("Missing documents", "find_missing_document", True),
    ("Tax control", "check_tax", True),
    ("Variance analysis", "generate_variance_report", True),
]


def month_end(meta: dict[str, Any]) -> list[Step]:
    steps = [_s("Load client and period", "accounting_context")]
    for title, tool, takes_period in MONTH_END_CHECKS:
        steps.append(_s(title, tool, _p(meta) if takes_period else {}))
    steps.append(_s("Draft adjusting journals", description=(
        "From the exceptions raised in this close (list_exceptions): draft journals with create_draft_journal "
        "for the accruals, prepayments and reclassifications the checks proposed, where the evidence makes the "
        "amount clear. Narrate each clearly and set reverse_on for accruals. Do NOT post anything. If nothing is "
        "clear enough, draft nothing and say so.")))
    steps.append(_s("Management pack", "prepare_management_pack", _p(meta)))
    steps.append(_s("Exception summary", "summarise_exceptions", {"title": "Month-End Complete"}))
    return steps


def tax_prep(meta: dict[str, Any]) -> list[Step]:
    return [_s("Load client and period", "accounting_context"),
            _s("Tax review", "check_tax", _p(meta)),
            _s("Missing support", "find_missing_document", _p(meta)),
            _s("Coding review", "find_misclassification", _p(meta)),
            _s("Deadlines", "monitor_deadlines"),
            _s("Tax pack and draft return", "prepare_tax_pack", _p(meta)),
            _s("Tax workpaper", "generate_workpaper", {"area": "tax", **_p(meta)}),
            _s("Exception summary", "summarise_exceptions", {"title": "Tax preparation"})]


def audit_prep(meta: dict[str, Any]) -> list[Step]:
    return [_s("Load client and period", "accounting_context"),
            _s("Trial balance", "get_trial_balance"),
            _s("Audit pack: requests, journal testing, sample", "prepare_audit_pack", _p(meta)),
            _s("Bank reconciliation", "create_reconciliation_schedule"),
            _s("Exception summary", "summarise_exceptions", {"title": "Audit preparation"})]


def payroll_run(meta: dict[str, Any]) -> list[Step]:
    return [_s("Load client and period", "accounting_context"),
            _s("Payroll review", "payroll_review", _p(meta)),
            _s("Payroll workpaper", "generate_workpaper", {"area": "payroll", **_p(meta)}),
            _s("Exception summary", "summarise_exceptions", {"title": "Payroll review"})]


def management_reporting(meta: dict[str, Any]) -> list[Step]:
    return [_s("Load client and period", "accounting_context"),
            _s("Variance analysis", "generate_variance_report", _p(meta)),
            _s("Cash-flow forecast", "generate_cashflow_forecast"),
            _s("Management pack", "prepare_management_pack", _p(meta)),
            _s("Write the variance commentary", description=(
                "Open Reports/<period> Management commentary.md (read_file) and, for each material movement, "
                "add one plain-English sentence explaining it from the ledger detail (get_general_ledger for "
                "that account). Numbers only from tools. Keep it under a page. Save with edit_file.")),
            _s("Exception summary", "summarise_exceptions", {"title": "Management reporting"})]


def post_run(meta: dict[str, Any]) -> list[Step]:
    """The accountant chose drafts to post (dashboard / voice). One POST
    call, so one approval covers the batch they picked."""
    return [_s(f"Post {len(meta.get('draft_ids') or [])} draft(s) to the books", "post_drafts",
               {"draft_ids": list(meta.get("draft_ids") or [])})]


def release_run(meta: dict[str, Any]) -> list[Step]:
    return [_s("Release the payment batch", "release_payment_batch", {"draft_id": meta.get("draft_id", "")})]


def submit_run(meta: dict[str, Any]) -> list[Step]:
    return [_s("Submit the tax return", "submit_tax_return", {"draft_id": meta.get("draft_id", "")})]


def _wf(id_: str, name: str, desc: str, template: str, trigger: Trigger,
        build: Callable[[dict[str, Any]], list[Step]], goal: str) -> Workflow:
    return Workflow(id=id_, name=name, description=desc, template_id=template, trigger=trigger,
                    build_steps=build, goal=goal, extension_id="accounting")


WORKFLOWS: list[Workflow] = [
    _wf("invoice_processing", "Invoice processing", "New documents in the Inbox become draft bills; only the "
        "uncertain ones reach the accountant.", "bookkeeper",
        FolderWatch(setting="inbox", patterns=["*.pdf", "*.eml", "*.jpg", "*.png", "*.csv", "*.xlsx"]),
        invoice_processing, "Process the new documents in the Inbox"),
    _wf("bookkeeping", "Daily bookkeeping", "Statements imported, bank lines drafted, documents processed, "
        "gaps flagged.", "bookkeeper", Schedule(every="weekdays", at="07:30"), bookkeeping, "Do the bookkeeping"),
    _wf("debtors_run", "Debtors run", "Every morning: aged debtors, reminders drafted, big accounts escalated.",
        "accounts_receivable", Schedule(every="weekdays", at="08:00"), debtors_run, "Chase overdue invoices"),
    _wf("ap_run", "Accounts payable run", "Invoices, duplicates, POs, supplier statements, draft payment batch.",
        "accounts_payable", Schedule(every="weekly", at="09:00", weekday=3), ap_run, "Run accounts payable"),
    _wf("month_end", "Month-end close", "The full close checklist, draft adjustments, management pack, "
        "exceptions.", "month_end", Schedule(every="monthly", at="07:00", day=3), month_end, "Prepare month-end"),
    _wf("tax_prep", "Tax preparation", "Tax review, support, deadlines, tax pack and draft return.",
        "tax_preparation", Schedule(every="monthly", at="08:00", day=10), tax_prep, "Prepare the tax pack"),
    _wf("audit_prep", "Audit preparation", "Request list, journal testing, sample, evidence, bank rec.",
        "audit_assistant", Trigger(), audit_prep, "Prepare the audit pack"),
    _wf("payroll_review", "Payroll review", "Register vs prior month and ledger; ghosts and shared accounts.",
        "payroll_review", Schedule(every="monthly", at="09:00", day=24), payroll_run, "Review payroll"),
    _wf("management_reporting", "Management reporting", "Variances, cash forecast, pack and commentary.",
        "management_reporting", Schedule(every="monthly", at="08:00", day=5), management_reporting,
        "Prepare the management pack"),
    # Accountant-initiated only (never scheduled): the actions behind the approval dialogs.
    _wf("post_drafts", "Post drafts", "Post the drafts the accountant selected.", "bookkeeper", Trigger(),
        post_run, "Post the selected drafts"),
    _wf("release_batch", "Release payments", "Release a reviewed payment batch.", "accounts_payable", Trigger(),
        release_run, "Release the payment batch"),
    _wf("submit_return", "Submit tax return", "Submit a reviewed tax return.", "tax_preparation", Trigger(),
        submit_run, "Submit the tax return"),
]

BY_ID = {w.id: w for w in WORKFLOWS}
