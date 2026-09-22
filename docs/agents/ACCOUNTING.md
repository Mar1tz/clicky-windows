# Accounting — the first industry extension

Accounting turns Tora's agent workforce into a digital accounting team:
eight specialist agents with the tools, workflows, knowledge and
permissions of the trade. The agents **prepare**. The accountant
**decides**. Anything that changes the books, moves money or files a
return goes past a person first.

It's an extension, not a rewrite: switch it off (tray → Extensions…) and
the app is exactly what it was. Your clients' data stays where it is.

---

## The safety model in one table

| level         | what                                                        | asks?                                                         |
|---------------|-------------------------------------------------------------|---------------------------------------------------------------|
| **READ**      | trial balance, ledger, bank lines, invoices, every check    | never                                                         |
| **DRAFT**     | draft bills/invoices/journals, bank codings, reminders, packs, reports | no: runs inside the **confidence policy**           |
| **POST**      | `post_drafts`: anything into the books                      | **every time**; you can allow it for the rest of one job      |
| **SENSITIVE** | `release_payment_batch`, `submit_tax_return`                | **every time**, you **type** `RELEASE` / `SUBMIT`; never by voice, never granted for a whole job |

**Confidence policy** (per client, default 95% / 70%):
≥ 95% → drafted. 70–95% → drafted **and** flagged for review. < 70% →
nothing drafted; you're asked.

**Exceptions, not activity.** Every run ends with *"N checks completed
automatically. Needs review: 1. … 2. …"*. The full list stays on the
client's dashboard until you resolve it.

---

## Try it in two minutes (no accounting system, no credentials)

1. Tray → **Extensions…** → **Enable** on Accounting.
2. Tray → **Dashboard…** → **Demo client**. This creates *ABC Manufacturing (Demo)*:
   three months of books with problems planted in them.
3. Click **Month-end close**. It takes about two seconds. Then open the **Needs attention** tab.

You should see what a good senior would find: a R84,200 supplier payment
with no invoice, a supplier invoice entered twice, a payment made twice,
three VAT claims with no tax invoice behind them, advertising up ~50%, an
Uber ride coded to advertising, Microsoft 365 coded to repairs, no August
electricity (accrue it), a full-year insurance premium expensed in one
month (prepay it), R3,150 sitting in suspense, two bank lines nobody can
explain, a R50,000 "Adjustment – per MD" posted by *admin* at 22:47 on a
Sunday, a customer 69 days overdue, a 35% pay rise, two employees paid
into the same bank account, and wages R6,500 short of the payroll register.

Then try **Process inbox**. Five documents are waiting. One clean invoice
becomes a draft bill. One duplicate is caught. An unknown supplier is
drafted and flagged. A receipt and a supplier statement are routed to
their own workflows, and the statement explains the R84,200.

By voice or in the Agents panel's text box, once it's enabled:
*"prepare month-end for ABC"*, *"chase overdue invoices"*,
*"process the invoices in my inbox"*, *"prepare the VAT pack"*,
*"prepare the audit pack"*, *"review payroll"*, *"build the management pack"*.

Start from zero: say *"I'm an accountant"*. Tora offers the extension and
turns it on when you agree.

---

## The eight agents

| agent                       | handles                                                                 | default trigger              | you get back |
|-----------------------------|-------------------------------------------------------------------------|------------------------------|--------------|
| **Bookkeeping**             | incoming invoices, receipts, bank coding and matching, supporting documents | new files in Inbox; weekdays 07:30 | draft bills, draft codings, filed documents, gaps |
| **Accounts Payable**        | supplier invoices, PO matching, supplier statements, duplicates, payment batches | new files in Inbox; weekly | draft bills, a draft payment batch (held items explained), statement recs |
| **Accounts Receivable**     | overdue invoices, statements, collections, aging, reminder drafts       | weekdays 08:00               | reminders in Outbox (not sent), escalations, customer statements |
| **Month-End**               | the full close checklist, adjustments, pack                             | monthly (day 3)              | bank rec, workpapers, draft journals, management pack, exception summary |
| **Tax Preparation**         | tax review, missing support, deadlines, tax pack, draft return          | monthly (day 10)             | tax pack, draft return (filing is SENSITIVE) |
| **Audit Assistant**         | request list, evidence, samples, journal testing, audit folder          | on request                   | Audit/<period>/ with PBC list, sample, journal testing |
| **Payroll Review**          | variances, joiners and leavers, shared bank accounts, payroll-to-GL     | monthly (day 24)             | payroll review + workpaper |
| **Management Reporting**    | monthly pack, KPIs, variances, 13-week cash forecast, commentary        | monthly (day 5)              | pack workbook + commentary |

Create one for a client with **New agent…** on the dashboard, or say
*"create an accounts payable agent for this client"*. The wizard asks
for the accounting system, inbox, supplier folder, permission level,
approval threshold and schedule.

---

## What accountants can do with it

✅ works today on local books, the demo books or CSV/Excel exports ·
✅ᴹ works today but needs the agents' model (the connected gateway), because it's judgement, not a check ·
🟡 needs a Phase 2 connection (email, Xero/QuickBooks/Sage, bank feed)

### Daily bookkeeping
1. ✅ Drop a bank statement CSV/XLSX into `Inbox\Bank`. It's imported with duplicates skipped, then every line is matched or coded as a draft.
2. ✅ Code bank lines from the client's own rules first, then keywords (Uber → travel, Vodacom → telephone, Microsoft → software).
3. ✅ Learn a rule from an approved coding ("anything with TAKEALOT → Office Supplies").
4. ✅ Match receipts to open invoices by amount and reference.
5. ✅ Match supplier payments to open bills.
6. ✅ Flag every bank line nothing explains, with the question to ask the client.
7. ✅ Turn a folder of supplier invoice PDFs into draft bills: supplier, number, dates, net, VAT, total, PO, bank details.
8. ✅ Catch an invoice that's already in the books before it becomes a second bill.
9. ✅ Flag new suppliers, and show the bank details on their invoice, before anyone pays them.
10. ✅ Read receipts (PDF; photos via OCR) and suggest the account.
11. ✅ Rename documents to `2026-08-28 Acme Packaging AP-7790 R5,750.00.pdf`.
12. ✅ File documents into `Documents\Invoices\2026-08\` and the like.
13. ✅ Unpack invoice attachments from `.eml` files saved into the inbox.
14. ✅ List transactions without a supporting document; draft the request to the client.
15. ✅ Keep the dashboard's "Needs attention" list current as documents arrive (folder watch).
16. 🟡 Watch a Gmail/Outlook mailbox for invoices directly.
17. 🟡 Pull bank transactions from a bank feed instead of CSV files.

### Accounts payable
18. ✅ Three-way-style PO check: invoice vs purchase order, variances over tolerance flagged.
19. ✅ Flag invoices over a threshold that arrive with no PO.
20. ✅ Reconcile a supplier statement (PDF or CSV) to payables: invoices they billed that we don't have, bills of ours not on their statement.
21. ✅ Explain unallocated payments from the supplier's statement ("SS-2250 R84,200 is on Steelco's statement and not in our books").
22. ✅ Detect duplicate supplier invoices (same reference, or same amount within a week).
23. ✅ Detect duplicate payments and supplier payments with no invoice behind them.
24. ✅ Draft a payment batch of bills due by a date, **holding back** duplicates, unknown suppliers and suppliers whose bank details changed recently.
25. ✅ Release a reviewed batch: SENSITIVE. On local books this records the payments and writes a bank bulk-payment CSV. Nothing is paid.
26. ✅ Request copy invoices from suppliers (drafts in `Outbox`).
27. 🟡 Push approved bills into Xero/QuickBooks/Sage as bills awaiting payment.

### Accounts receivable and collections
28. ✅ Aged debtors at any date: current, 1–30, 31–60, 61–90, 90+.
29. ✅ Classify every overdue customer: gentle reminder, firm reminder, escalate.
30. ✅ Draft polite, specific reminders (invoice numbers, amounts, due dates) into `Outbox`, and never send them.
31. ✅ Escalate large or very late accounts to the accountant instead of emailing them.
32. ✅ Check receipts that aren't yet allocated *before* chasing anyone.
33. ✅ Produce customer statements as PDFs.
34. ✅ᴹ Draft customer invoices ("create an invoice for Karoo Foods for 20 tables at R3,000").
35. ✅ Morning debtors run on a schedule, with the results waiting on the dashboard.
36. 🟡 Send reminders when the client's policy allows it, and log the replies.

### Month-end and year-end
37. ✅ Run the full close checklist on a schedule or on request, 19 steps in order, none skipped.
38. ✅ Bank reconciliation schedule with formulas, and a difference if a statement balance is given.
39. ✅ Receivables and payables control accounts vs their sub-ledgers.
40. ✅ Clear suspense and clearing accounts.
41. ✅ Suggest accruals for regular costs with no bill this month, with amounts.
42. ✅ Suggest prepayments for annual or unusually large costs.
43. ✅ Payroll to general ledger reconciliation.
44. ✅ Journal testing: weekend and after-hours postings, round amounts, manual entries to revenue or cash, rare users, "adjustment" narrations.
45. ✅ Coding review for misclassifications.
46. ✅ Tax control review.
47. ✅ P&L variance analysis vs the prior month, above materiality.
48. ✅ᴹ Draft adjusting journals (accruals reverse automatically next period). Posting still asks.
49. ✅ Management pack at the end of the close.
50. ✅ Month-end progress on the dashboard (e.g. "Month-end 72%").
51. ✅ Working papers per area (bank, debtors, creditors, accruals, prepayments, tax, suspense, payroll, fixed assets) with lead schedule, detail, tickmarks and sign-off.
52. 🟡 Lock the period in the accounting system after sign-off.

### Tax (jurisdiction-configurable, never advice)
53. ✅ Output vs input tax for a period, tied to the control account.
54. ✅ Tax-bearing entries with no valid document ("3 VAT transactions missing support").
55. ✅ Entries at unusual rates.
56. ✅ Typical filing deadlines from the client's jurisdiction profile (ZA, GB, US, EU, AU or generic), always labelled *typical, confirm*.
57. ✅ Tax pack workbook (summary, output listing, input listing, missing support) and a **draft** return.
58. ✅ Submit the return: SENSITIVE (demo books record a mock reference; local books refuse and tell you to use the authority's portal).
59. 🟡 File through an authority's API where one exists.

### Audit support
60. ✅ PBC / request list showing what's already available.
61. ✅ Reproducible samples: random, high value or monetary-unit (MUS), with the seed recorded.
62. ✅ Evidence status for every sampled item.
63. ✅ Journal testing for the audit period.
64. ✅ Audit folder per period.
65. ✅ Bank reconciliation for the auditors.
66. 🟡 Upload the folder to the auditor's portal / SharePoint.

### Payroll review
67. ✅ Pay changes by employee over a threshold vs last month.
68. ✅ New and leaving employees.
69. ✅ Two employees paid into one bank account (ghost-employee signal).
70. ✅ Wages in the ledger vs the payroll register.
71. ✅ Payroll workpaper.

### Management reporting and advisory
72. ✅ KPI sheet: revenue, gross margin, net margin, debtor days, creditor days, current ratio.
73. ✅ P&L for the month, the prior month and year to date.
74. ✅ Balance sheet.
75. ✅ 13-week cash forecast from the bank balance, open invoices (doubtful ones excluded), open bills and recurring costs, with the low point flagged.
76. ✅ᴹ Variance commentary drafted in plain English; the Management Reporting agent writes it from the ledger detail.
77. ✅ A pack a director can read in five minutes (xlsx + commentary markdown).

### Spreadsheets (core tools, any agent)
78. ✅ Read any workbook or CSV.
79. ✅ Write cells into a client's workbook **without breaking its formulas or formatting** (formula cells are protected).
80. ✅ Clean exports: trim, de-duplicate, normalise dates and numbers, into a copy.
81. ✅ Reconcile two sheets on a key (ours vs theirs, ledger vs bank, month A vs B).
82. ✅ Audit a workbook's formulas: `#REF!`/`#DIV/0!`, links to other files, hard-coded numbers, a formula that breaks its column's pattern, numbers stored as text.
83. ✅ Compare two periods' figures with % change and flags.

### Controls and fraud signals
84. ✅ Supplier bank-detail changes flagged at payment time.
85. ✅ Duplicate invoices and duplicate payments.
86. ✅ Payments to payables with no invoice.
87. ✅ Shared employee bank accounts.
88. ✅ Unusual journals (the classic override signals).
89. ✅ Control-account differences that point at entries posted without documents.
90. ✅ Every POST and SENSITIVE action goes through the approval dialog and is recorded in the timeline.

### Practice management (many clients)
91. ✅ One folder per client, with its own provider, jurisdiction, period, thresholds, deadlines and month-end status.
92. ✅ Say the client's name ("…for ABC"). It's matched to the right folder, and Tora never invents a client from a phrase.
93. ✅ Several agents for several clients at once (the orbs show each one).
94. ✅ Scheduled agents per client that survive a restart and never double-run.
95. ✅ Crash-safe: an interrupted month-end resumes where it stopped.
96. ✅ Dashboard per client: exceptions, drafts, debtors & creditors, deadlines, agents, reports.
97. 🟡 A practice-wide view across all clients (deadlines, open exceptions).
98. 🟡 Client portal / document requests sent from the Outbox.

### Asking questions (Tora answers directly)
99. ✅ "What's a prepayment?", "How do accruals reverse?", "Explain this variance". Questions stay in the conversation and never start a job.
100. ✅ "How is the month-end going?", "Is the pack ready?" Tora checks the running agents and answers.

---

## Where everything lives

```
Documents\Tora Projects\Accounting Clients\<Client>\
    accounting.json   client, period, provider, jurisdiction, thresholds, month-end status
    Inbox\            drop invoices, receipts, supplier statements, .eml files
    Inbox\Bank\       drop bank statement CSV/XLSX files
    Documents\        filed: Invoices\<month>\, Receipts\, Statements\, Bank\, Other\
    Reports\          variance, management pack + commentary, tax pack, statements, Exceptions.md
    Workpapers\       bank rec, area workpapers
    Audit\<period>\   audit pack
    Outbox\           reminder and request drafts (never sent)
    Exports\          import files for your accounting system, payment batch files
    .tora\            the books (local/demo), drafts, exceptions
```

## Connecting real books

| provider                            | tier   | status      | how it posts |
|-------------------------------------|--------|-------------|--------------|
| Demo books                          | file   | ✅          | into the demo ledger |
| Local books (a real double-entry ledger on this PC) | file | ✅ | into the local ledger |
| Spreadsheet / CSV exports           | file   | ✅          | writes `Exports\journals_for_import.csv` etc. to import |
| Xero · QuickBooks Online · Sage     | OAuth  | 🟡 Phase 2  | through their APIs |
| Sage Pastel (desktop) · custom ERP  | file / API | 🟡 Phase 2 | import files / adapters |

The order is fixed: API, then OAuth, then files, then browser automation,
then UI automation, then vision. The agents never click through an
accounting system that has an API.

## Honest limits (Phase 1)
- No bank connection and no tax-authority connection: "release" and "submit" record and export. They don't pay or file.
- Email is folder-based. Save attachments into the Inbox (an Outlook rule can do it).
- Extraction is pattern-based, and every field carries a confidence. Badly scanned documents come back as exceptions, never as guesses.
- Jurisdiction presets are typical values to start from. They aren't tax or legal advice. Edit them per client in `accounting.json`.
- `.xlsx` output needs `openpyxl` (in requirements.txt). Without it, packs are written as CSV folders.
