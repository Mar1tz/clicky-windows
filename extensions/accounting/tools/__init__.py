"""The accounting tool set (the "AccountingTools" namespace: ToolSpec.category == "accounting").

Importing this package registers every tool definition; build_tools()
binds them to a session cache. Levels, at a glance:

READ       accounting_context, get_trial_balance, get_general_ledger, get_bank_transactions,
           get_customer_invoices, get_supplier_invoices, find_overdue_invoices, get_financial_statements,
           list_drafts, list_exceptions, suggest_reconciliation, detect_duplicate_invoice,
           detect_duplicate_payment, find_unusual_journal, find_misclassification, match_po_invoice,
           supplier_statement_reconcile, review_suspense, check_control_accounts,
           review_accruals_prepayments, payroll_review, find_missing_document, check_tax,
           monitor_deadlines, summarise_exceptions, sample_transactions, find_invoice_attachments,
           classify_document, extract_invoice_data, extract_receipt_data, reconcile_inbox_statements
DRAFT      import_bank_statement, import_inbox_statements, categorize_transaction, match_transaction,
           draft_from_suggestions, create_draft_bill, create_draft_invoice, create_draft_journal,
           create_draft_payment_batch, draft_payment_reminder, prepare_collection_run,
           generate_customer_statement, request_document, record_exception, resolve_exception,
           reject_draft, rename_document, file_document, process_invoice_document, process_inbox,
           generate_variance_report, generate_cashflow_forecast, create_reconciliation_schedule,
           generate_workpaper, prepare_management_pack, prepare_tax_pack, prepare_audit_pack
POST       post_drafts
SENSITIVE  release_payment_batch, submit_tax_return
"""
from . import bank, checks, documents, invoices, ledger, posting, reports  # noqa: F401 - registers the tools
from .common import REGISTRY, AccountingTool, build_tools

TOOL_LEVELS = {d.name: d.level.value for d in REGISTRY}

__all__ = ["REGISTRY", "AccountingTool", "build_tools", "TOOL_LEVELS"]
