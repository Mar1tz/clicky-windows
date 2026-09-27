# Freelancer pack

*The second industry pack, after Accounting. Code: `extensions/freelancer/`.
Tests: `tests/agents/test_freelancer_pack.py` (offline, no model).*

A back office for one person: hours, quotes, invoices, late payers,
receipts, a tax set-aside on every payment, and a year-end pack for the
accountant.

## How it is built

* **Money lives in the Accounting pack's ledger** (`extensions/accounting/books.py`),
  used as a library. The manifest says `requires: ["accounting"]`; the
  Accounting pack does not have to be switched on.
* **One business folder**, `<workspace>/Freelance` by default:
  `freelancer.json`, `books.sqlite` (the ledger), `work.sqlite` (time,
  quotes, drafts, set-asides, outbox), and `Invoices`, `Quotes`, `Receipts`,
  `Outbox`, `Year-end`.
* **Nothing is sent.** An issued invoice or a reminder is an `.eml` in
  `Outbox` (marked `X-Unsent`, so Outlook opens it as a draft). The
  freelancer opens it and presses Send.
* **One agent, the Freelance Admin Agent**, on the Accounting worker.

## Levels

| Level | Tools |
|---|---|
| READ | `freelance_overview`, `list_invoices`, `list_unbilled_time`, `list_quotes`, `set_aside_report` |
| DRAFT | `log_time`, `create_quote`, `accept_quote`, `draft_invoice`, `draft_invoice_from_time`, `discard_invoice_draft`, `draft_reminder`, `chase_late_payers`, `add_expense`, `prepare_year_end_pack` |
| POST (asks every time) | `issue_invoice`, `record_payment`, `post_expenses` |

Nothing is SENSITIVE, because nothing moves money or sends mail.

## Workflows

* **Weekly admin**: the overview, unbilled hours, a reminder drafted for
  every late invoice, and the tax set aside. Reminders are friendly up to
  14 days late, firm up to 45, then a final notice, which is also flagged.
  It runs on Fridays at 16:00 if the `weekly_admin` setting is on.
* **Year-end pack**: `profit_and_loss.csv`, `invoices.csv`, `expenses.csv`,
  `set_aside.csv` and `summary.pdf` for the tax year. The tax year comes
  from the books' fiscal start (UK preset: 6 April).
* **Issue invoice**: started from the dashboard's Issue button. It asks
  for approval.

## Try it

Turn it on in **Extensions**, then say "run my weekly admin", "log 3 hours
for Acme", "invoice Acme for my hours" or "prepare my year-end pack".
Open the dashboard from the Extensions card.

The set-aside percentage (default 25%) is a savings habit, not a tax
calculation. Tora never says what is owed.
