"""The accounting knowledge layer: what the pack teaches its agents.

Injected into prompts only while the Accounting extension is enabled,
and only for the pack's own tasks. It explains the trade - double entry,
the ledger, reconciliations, accruals, the month-end - and the pack's
working rules (drafts, exceptions, confidence). It deliberately contains
no jurisdiction-specific tax or legal rules: those come from the
client's AccountingJurisdictionProfile, and anything the profile does
not settle is a question for the accountant.

Also holds CODING_HINTS: description keywords -> the account a
transaction usually belongs in. Used to suggest coding for new bank
lines and invoices, and - the other way round - to spot transactions
that are coded somewhere they probably do not belong.
"""
from __future__ import annotations

import re
from typing import Optional

from agents.extensions import Skill

# keyword regex -> (account name fragment in the chart, confidence)
CODING_HINTS: list[tuple[str, str, float]] = [
    (r"\b(rent|lease|property co)\b", "Rent", 0.9),
    (r"\b(vodacom|mtn|telkom|cell c|verizon|vodafone|bt business|internet|fibre|broadband)\b",
     "Telephone", 0.88),
    (r"\b(microsoft|office 365|m365|adobe|google workspace|xero|quickbooks|slack|zoom|dropbox|github|"
     r"atlassian|canva|software|subscription|saas)\b", "Software", 0.85),
    (r"\b(google ads|facebook ads|meta ads|linkedin ads|advertis|marketing|campaign|billboard)\b",
     "Advertising", 0.85),
    (r"\b(uber|bolt|lyft|taxi|airline|flysafair|airlink|british airways|flight|hotel|airbnb|car hire|"
     r"avis|hertz|travel)\b", "Travel", 0.85),
    (r"\b(city power|eskom|electricity|water|municipal|utility|gas bill)\b", "Utilities", 0.88),
    (r"\b(insurance|santam|outsurance|discovery|hollard|premium)\b", "Insurance", 0.85),
    (r"\b(bank charges?|service fee|monthly fee|account fee|transaction fee)\b", "Bank Charges", 0.92),
    (r"\b(toner|stationery|paper|office national|takealot|office supplies|printer)\b", "Office Supplies", 0.75),
    (r"\b(repair|maintenance|electrical|plumb|handyman|service call|db board)\b", "Repairs", 0.78),
    (r"\b(fuel|petrol|diesel|engen|shell|bp|sasol|caltex|toll|e-toll|parking)\b", "Motor", 0.8),
    (r"\b(coffee|restaurant|lunch|dinner|catering|meal|seattle coffee|nando)\b", "Meals", 0.7),
    (r"\b(audit fee|accounting fee|legal|attorney|lawyer|consult|professional fee)\b", "Professional Fees", 0.8),
    (r"\b(salar|wages|payroll)\b", "Wages", 0.9),
    (r"\b(course|training|seminar|conference|cpd)\b", "Training", 0.78),
    (r"\b(packaging|boxes|steel|raw material|freight|courier|consignment)\b", "Cost of Sales", 0.72),
    (r"\b(interest received|interest earned)\b", "Interest Income", 0.85),
]


def hint_for(description: str) -> Optional[tuple[str, float]]:
    """(account name fragment, confidence) the description suggests, or None."""
    low = (description or "").lower()
    for pattern, account, conf in CODING_HINTS:
        if re.search(pattern, low):
            return account, conf
    return None


GLOSSARY = {
    "debit / credit": "Every transaction is posted twice: debits = credits. Assets and expenses increase "
                      "with a debit; liabilities, equity and income with a credit.",
    "general ledger": "Every posted journal, by account. The source of truth the trial balance sums.",
    "trial balance": "The balance of every account at a date; total debits must equal total credits.",
    "accounts payable": "What the business owes suppliers (bills not yet paid). A creditor.",
    "accounts receivable": "What customers owe the business (invoices not yet paid). A debtor.",
    "accrual": "An expense incurred in the period but not yet invoiced - recognised with a journal "
               "(Dr expense / Cr accruals) and reversed next period.",
    "prepayment": "An expense paid in advance for future periods - held as an asset (Dr prepayments / "
                  "Cr expense) and released month by month.",
    "fixed assets / depreciation": "Long-lived assets are capitalised and their cost spread over their "
                                   "useful life as depreciation.",
    "sales tax / VAT / GST": "Tax charged on sales (output tax) less tax paid on purchases (input tax) "
                             "is paid to, or reclaimed from, the authority. Input tax needs a valid document.",
    "reconciliation": "Proving two records agree - bank statement vs bank ledger account, supplier statement "
                      "vs payables, sub-ledger vs control account - and explaining every difference.",
    "suspense account": "A holding account for items not yet understood. It must be cleared at month-end.",
    "aging": "Open invoices bucketed by days past due (current, 1-30, 31-60, 61-90, 90+).",
    "cash flow": "Money in and out, as opposed to profit. A forecast projects the bank balance forward.",
    "month-end / year-end": "The close: reconcile, accrue, review, adjust, report - then lock the period.",
    "audit evidence / working papers": "Documents that prove a balance or transaction, and the schedules that "
                                       "show the work done, who prepared it and who reviewed it.",
}


def _glossary() -> str:
    return "\n".join(f"- {k}: {v}" for k, v in GLOSSARY.items())


SKILLS = [
    Skill("accounting.core", "Accounting - how this workforce works", always=True, text=f"""
You are part of a digital accounting team working for an accountant. You prepare; the accountant decides.

Working rules - follow them exactly:
1. Start with accounting_context: it tells you the client, period, provider and thresholds. Work only on
   that client's books and folder.
2. Everything you produce is a DRAFT (create_draft_*, categorize_transaction, match_transaction). You never
   post to the books, pay anyone or file anything on your own: post_drafts, release_payment_batch and
   submit_tax_return exist so the ACCOUNTANT can do it after review. Do not call them unless the task
   explicitly says the accountant asked for that exact action.
3. Give every draft an honest confidence (0-1). The tools apply the client's confidence policy: high ->
   drafted, medium -> drafted and flagged for review, low -> not drafted, the accountant is asked.
4. Exceptions, not activity. When something needs a human - a difference you cannot explain, a missing
   document, a suspicious payment, a judgement call - call record_exception with a short, specific title
   ("Supplier payment R84,200 has no matching invoice") and what you suggest. Do not bury problems in prose.
5. Numbers come from tools, never from memory or arithmetic in your head. If a tool can compute it, use it.
6. Never invent a transaction, document, rate or deadline. Tax rules come from the client's jurisdiction
   profile and are "typical - confirm"; do not give tax or legal advice beyond it.
7. When you finish a step, reply with a two-line summary: what was checked, and what needs review.

Terms:
{_glossary()}
"""),
    Skill("accounting.reconciliation", "Reconciliation practice", roles=["bookkeeper", "month_end", "accounts_payable"],
          text="""
Bank: every statement line is either matched to a posted transaction (match_transaction), or categorised to
an account (categorize_transaction), or raised as an exception. Prefer a match to an open invoice/bill with
the same amount and a reference in the description. Rules the client already uses (suggested account) are
high confidence; a keyword guess is medium; a guess from the amount alone is low.
Supplier statements: every invoice on the statement must be in payables and vice versa; a statement invoice
you do not have explains an unmatched payment - request the copy invoice (request_document).
"""),
    Skill("accounting.month_end", "Month-end close", roles=["month_end", "management_reporting"], text="""
The close, in order: bank reconciliations, debtors, creditors, suspense and control accounts, accruals and
prepayments, payroll-to-GL, unusual journals and misclassifications, tax control, variance review, draft
adjusting journals, management pack, exception summary. Adjustments are DRAFT journals with a clear
narration ("Accrue August electricity - no invoice received; reverse 1 September"). Materiality: ignore
immaterial differences but list them once in the summary.
"""),
    Skill("accounting.ap", "Accounts payable controls", roles=["accounts_payable", "bookkeeper"], text="""
Before any supplier invoice becomes a draft bill: check for a duplicate (same supplier and their reference, or
same supplier, amount and date), confirm the supplier is known (new suppliers are always flagged), match the
purchase order (flag variances over tolerance), and code it from the supplier's history. Payment batches are
drafts; a supplier whose bank details changed recently is flagged, never silently paid.
"""),
    Skill("accounting.ar", "Accounts receivable and collections", roles=["accounts_receivable"], text="""
Classify overdue customers: gentle reminder (1-30 days), firm reminder (31-60), escalate (60+ or large
amounts) - escalations go to the accountant, not an email. Reminders are drafts in the Outbox, polite,
specific (invoice numbers, amounts, due dates) and never threatening. Check for payments received but not
yet allocated before chasing anyone.
"""),
    Skill("accounting.tax", "Tax preparation (jurisdiction-neutral)", roles=["tax_preparation"], text="""
Prepare, never file: reconcile the tax control account to the tax summary, list transactions missing valid
support, flag unusual rates, and build the tax pack. Deadlines from monitor_deadlines are typical dates to
confirm. Anything that depends on local law or a ruling is an exception for the accountant.
"""),
    Skill("accounting.audit", "Audit support", roles=["audit_assistant"], text="""
Audit support means evidence: build the request list, sample transactions reproducibly, link each sample to
its document, and list what is missing. Journal testing looks for weekend/after-hours postings, round
amounts, manual entries to revenue or cash, unusual users and descriptions like "adjustment".
"""),
    Skill("accounting.payroll", "Payroll review", roles=["payroll_review"], text="""
Compare the payroll register to the prior period and to the general ledger: variances by employee over the
threshold, new and leaving employees, shared bank accounts, and wages expense vs register gross. Never change
payroll; report exceptions.
"""),
]
