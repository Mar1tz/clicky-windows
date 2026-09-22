"""The eight accounting agent templates.

Each defines mission, tools, permissions, triggers, memory schema,
default workflow and expected artifacts. They share one worker class
(AccountingWorker); what differs is the brief and the tool belt - a
payroll reviewer cannot draft a payment batch because the tool is not
on its belt, not because a prompt asked it nicely.

Permissions: every template runs with max_auto_risk LOW, i.e. READ and
DRAFT run unasked; POST and SENSITIVE tools always ask anyway (see
permissions.py). A template never lists a SENSITIVE tool: those exist
for the accountant to invoke through an agent they explicitly ask.
"""
from __future__ import annotations

from agents.core import RiskLevel
from agents.extensions import AgentTemplate, FolderWatch, Schedule, Trigger

WORKER = "accounting"

BASE = ["accounting_context", "list_drafts", "list_exceptions", "record_exception", "resolve_exception",
        "summarise_exceptions", "read_file", "list_dir"]
SHEETS = ["read_workbook", "check_formulas", "reconcile_sheets", "compare_periods"]

TEMPLATES: list[AgentTemplate] = [
    AgentTemplate(
        id="bookkeeper", name="Bookkeeping Agent", worker_type=WORKER,
        mission="Keep the client's books current: bank lines matched or coded, incoming invoices and receipts "
                "turned into draft bills, every transaction supported by a document.",
        handles=["incoming invoices", "receipts", "transaction classification", "draft bills",
                 "supporting documents", "reconciliations"],
        tools=BASE + ["import_bank_statement", "import_inbox_statements", "get_bank_transactions",
                      "suggest_reconciliation", "draft_from_suggestions", "categorize_transaction",
                      "match_transaction", "find_invoice_attachments", "classify_document", "extract_invoice_data",
                      "extract_receipt_data", "process_invoice_document", "process_inbox", "create_draft_bill",
                      "create_draft_journal", "file_document", "rename_document", "find_missing_document",
                      "request_document", "get_general_ledger"] + SHEETS,
        triggers=[FolderWatch(setting="inbox", patterns=["*.pdf", "*.eml", "*.jpg", "*.png", "*.csv", "*.xlsx"]),
                  Schedule(every="weekdays", at="07:30")],
        memory_schema={"coding_rule": "description pattern -> account, learnt from approved drafts",
                       "supplier_default": "supplier -> usual account"},
        default_workflow="bookkeeping", artifacts=["draft bills", "draft bank codings", "filed documents",
                                                   "exception list"],
        setup_questions=["Accounting system", "Email inbox or folder for invoices", "Bank statement folder",
                         "Approval threshold", "Schedule"]),
    AgentTemplate(
        id="accounts_payable", name="Accounts Payable Agent", worker_type=WORKER,
        mission="Get supplier invoices into the books correctly and pay the right suppliers the right amount once.",
        handles=["supplier invoices", "purchase-order matching", "supplier statement reconciliation",
                 "duplicate invoices", "draft payment batches"],
        tools=BASE + ["get_supplier_invoices", "find_invoice_attachments", "extract_invoice_data",
                      "process_invoice_document", "process_inbox", "detect_duplicate_invoice",
                      "detect_duplicate_payment", "match_po_invoice", "supplier_statement_reconcile",
                      "reconcile_inbox_statements", "create_draft_bill", "create_draft_payment_batch",
                      "request_document", "classify_document", "file_document"] + SHEETS,
        triggers=[FolderWatch(setting="inbox", patterns=["*.pdf", "*.eml"]), Schedule(every="weekly", at="09:00", weekday=3)],
        memory_schema={"supplier": "bank details history, usual account, PO habits"},
        default_workflow="ap_run", artifacts=["draft bills", "draft payment batch", "supplier statement recs"],
        setup_questions=["Accounting system", "Email inbox", "Supplier invoice folder", "Payment day",
                         "Approval threshold"]),
    AgentTemplate(
        id="accounts_receivable", name="Accounts Receivable Agent", worker_type=WORKER,
        mission="Get paid: know who owes what, chase politely and early, escalate the accounts that matter.",
        handles=["overdue invoices", "customer statements", "collections", "debtor aging",
                 "payment reminder drafts"],
        tools=BASE + ["get_customer_invoices", "find_overdue_invoices", "prepare_collection_run",
                      "draft_payment_reminder", "generate_customer_statement", "create_draft_invoice",
                      "get_bank_transactions", "suggest_reconciliation", "match_transaction"],
        triggers=[Schedule(every="weekdays", at="08:00")],
        memory_schema={"customer": "payment behaviour, promises to pay, contacts"},
        default_workflow="debtors_run", artifacts=["reminder drafts", "customer statements", "aged debtors"],
        setup_questions=["Accounting system", "Escalation threshold (days / amount)", "Reminder tone", "Schedule"]),
    AgentTemplate(
        id="month_end", name="Month-End Agent", worker_type=WORKER,
        mission="Close the month: run every check, draft the adjustments, build the pack, and hand the "
                "accountant only the exceptions.",
        handles=["bank reconciliation checks", "suspense accounts", "accrual/prepayment review", "P&L variances",
                 "balance sheet review", "management reporting"],
        tools=BASE + ["get_trial_balance", "get_financial_statements", "get_bank_transactions",
                      "suggest_reconciliation", "check_control_accounts", "find_overdue_invoices",
                      "get_supplier_invoices", "detect_duplicate_invoice", "detect_duplicate_payment",
                      "match_po_invoice", "review_suspense", "review_accruals_prepayments", "payroll_review",
                      "find_unusual_journal", "find_misclassification", "find_missing_document", "check_tax",
                      "generate_variance_report", "create_draft_journal", "create_reconciliation_schedule",
                      "generate_workpaper", "prepare_management_pack", "request_document"] + SHEETS,
        triggers=[Schedule(every="monthly", at="07:00", day=3)],
        memory_schema={"close": "last close date, recurring adjustments, known differences"},
        default_workflow="month_end",
        artifacts=["bank rec", "workpapers", "draft adjusting journals", "management pack", "exception summary"],
        setup_questions=["Accounting system", "Close day", "Materiality", "Approval threshold"]),
    AgentTemplate(
        id="tax_preparation", name="Tax Preparation Agent", worker_type=WORKER,
        mission="Prepare - never file - the client's sales tax / VAT: reconciled, supported and on time.",
        handles=["transaction review", "tax-support documents", "reconciliations", "missing documentation",
                 "draft tax workpapers", "deadline monitoring"],
        tools=BASE + ["check_tax", "find_missing_document", "find_misclassification", "prepare_tax_pack",
                      "monitor_deadlines", "get_general_ledger", "request_document", "generate_workpaper",
                      "create_draft_journal"],
        triggers=[Schedule(every="monthly", at="08:00", day=10)],
        memory_schema={"tax": "filing history, recurring adjustments, authority correspondence"},
        default_workflow="tax_prep", artifacts=["tax pack", "draft return", "missing-support list"],
        setup_questions=["Jurisdiction", "Filing frequency", "Accounting system"]),
    AgentTemplate(
        id="audit_assistant", name="Audit Assistant Agent", worker_type=WORKER,
        mission="Make the audit easy: request list, evidence, samples and journal testing ready before they ask.",
        handles=["audit request lists", "evidence collection", "missing support", "sample support",
                 "audit folders", "journal/anomaly review"],
        tools=BASE + ["prepare_audit_pack", "sample_transactions", "find_unusual_journal", "find_missing_document",
                      "generate_workpaper", "get_trial_balance", "get_general_ledger", "request_document",
                      "create_reconciliation_schedule"] + SHEETS,
        triggers=[Trigger()],
        memory_schema={"audit": "requests, auditors' queries, evidence locations"},
        default_workflow="audit_prep", artifacts=["audit folder", "PBC list", "sample with evidence", "journal testing"],
        setup_questions=["Audit period", "Auditor contact", "Sample size"]),
    AgentTemplate(
        id="payroll_review", name="Payroll Review Agent", worker_type=WORKER,
        mission="Catch payroll errors and fraud signals before the money goes: variances, ghosts, GL mismatches.",
        handles=["payroll variance checks", "payroll-to-GL reconciliation", "unusual changes",
                 "duplicate employee checks", "summary reports"],
        tools=BASE + ["payroll_review", "get_general_ledger", "create_draft_journal", "generate_workpaper"] + SHEETS,
        triggers=[Schedule(every="monthly", at="09:00", day=24)],
        memory_schema={"employee": "approved changes, known anomalies"},
        default_workflow="payroll_review", artifacts=["payroll review report", "payroll workpaper"],
        setup_questions=["Payroll system export folder", "Variance threshold", "Pay day"]),
    AgentTemplate(
        id="management_reporting", name="Management Reporting Agent", worker_type=WORKER,
        mission="Turn the books into a monthly pack a director can read in five minutes, with the numbers "
                "explained.",
        handles=["monthly reporting packs", "KPI reports", "P&L analysis", "balance-sheet analysis",
                 "cash-flow forecasts", "variance commentary"],
        tools=BASE + ["prepare_management_pack", "generate_variance_report", "generate_cashflow_forecast",
                      "get_financial_statements", "get_trial_balance", "find_overdue_invoices",
                      "check_control_accounts", "get_general_ledger", "edit_file", "write_file"] + SHEETS,
        triggers=[Schedule(every="monthly", at="08:00", day=5)],
        memory_schema={"reporting": "KPIs the client cares about, commentary style"},
        default_workflow="management_reporting",
        artifacts=["management pack", "variance commentary", "cash forecast"],
        setup_questions=["Accounting system", "KPIs", "Recipients", "Schedule"]),
]

for _t in TEMPLATES:
    _t.max_auto_risk = RiskLevel.LOW
    _t.extension_id = "accounting"

BY_ID = {t.id: t for t in TEMPLATES}

ROLE_PROMPTS = {t.id: t.mission for t in TEMPLATES}
