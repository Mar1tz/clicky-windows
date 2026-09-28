# Built packs

*Every pack in `extensions/`, one row each. The catalogue of ideas and the
build order are in the "HeyTora Extensions — the catalogue" doc. How a
pack is put together is in [EXTENSIONS.md](EXTENSIONS.md) and the kit in
[CREATOR.md](CREATOR.md#the-kit).*

Each pack is tested offline in `tests/agents/test_<id>_pack.py`, and all of
them in `tests/agents/test_e2e_every_pack.py`. That second file checks that
each pack:

* plugs in and comes out cleanly;
* shares no tool name with another pack, and still wins its own example requests with every pack on;
* gives every tool a level that matches its approval flags;
* has a worker for every agent it offers;
* routes its activation phrase to itself;
* draws its dashboard on an empty folder.

| Pack (id) | For | Riskiest action (level) | Scripted workflows | Say | Doc |
|---|---|---|---|---|---|
| Accounting (`accounting`) | An accountant's clients: bookkeeping, payables, receivables, month-end, tax, audit | Release payments, submit a return (SENSITIVE) | 9, month-end included | "prepare month-end for ABC" | [ACCOUNTING.md](ACCOUNTING.md) |
| Freelancer (`freelancer`) | One person's back office on Accounting's ledger | Issue an invoice, record a payment, post receipts (POST) | Weekly admin, year-end pack | "run my weekly admin" | [FREELANCER.md](FREELANCER.md) |
| Video creator (`creator`) | A channel: scripts, shot lists, chapters, upload details, sponsor deals, stats | none above DRAFT | Weekly channel review, prepare an upload | "run my weekly channel review" | [CREATOR.md](CREATOR.md) |
| Students (`students`) | Revision plan, SM-2 flashcards, APA/Harvard references, deadlines | none above DRAFT | Today's study, build the revision plan | "make me a revision plan" | [STUDENTS.md](STUDENTS.md) |
| Sales CRM (`sales`) | Contacts, a pipeline with a weighted forecast, follow-ups, quiet deals, email drafts | none above DRAFT | Morning pipeline, weekly forecast | "check my pipeline" | below |
| Online store (`store`) | Stock cover and reorders, purchase orders, margins, unshipped orders, returns, listings | none above DRAFT | Morning orders, weekly restock | "check my shop" | below |
| Trades (`trades`) | Quotes from a price list, a diary with no double bookings, parts lists, job invoices, certificate renewals | Issue an invoice, record a payment (POST) | Evening office, parts for the week | "do my paperwork" | below |
| Payroll (`payroll`) | A small employer's pay run: gross pay, imported deductions, reviewer checks, payslips, bank file, journal, leave | Finalise a pay run (POST) | Pay run check (the 20th) | "check the pay run" | below |
| Tax practice (`taxpractice`) | A practitioner's clients: deadline calendar, document checklists, escalating chasers, workpapers, assessment checks | none above DRAFT | Weekly deadlines | "check the tax deadlines" | below |
| Household finance (`household`) | Statements, categories, budgets, subscriptions and bills, price rises, warranties, a monthly report | none above DRAFT | Where the money went (the 1st) | "show me where the money went" | below |
| Lettings (`lettings`) | Rent roll and arrears, escalating letters, repairs and contractors, certificates, renewals, deposits | Settle a deposit (POST) | Morning lettings | "check the rent roll" | below |
| Software developers (`developer`) | Change review (secrets, leftovers, tests, size), test failures, dependencies, TODOs, issue triage, release notes | none above DRAFT | Review before pushing, prepare a release | "review my changes" | below |
| Podcast production (`podcast`) | Transcripts (length, speaking time), clips, chapters for podcast apps, show notes, guests and releases, calendar | none above DRAFT | The week's show | "check this week's podcast" | below |

## Sales CRM

* **Stage odds:** lead 10%, qualified 25%, proposal 50%, negotiation 75%.
  They can be changed with the `odds` setting.
* **Forecast:** best case and weighted value for the deals expected to
  close in a month, plus what has been won that month. Deals with no date
  are flagged, because the forecast cannot count them.
* **Quiet deals:** an open deal nobody has touched in `stale_days`
  (default 14) and with no follow-up planned. The biggest come first.
* **Follow-ups:** logging the next call or email on a deal closes that
  deal's earlier follow-ups.
* **Won and lost:** a lost deal needs its reason. The win rate and the
  average number of days to win come from the closed deals.
* **Import:** contacts come in from HubSpot, Pipedrive or Outlook CSV
  exports. A first and last name are joined, a bad email is dropped, and
  a second import updates people rather than duplicating them.
* **Email:** follow-up emails are `.eml` drafts in `Outbox`, marked
  `X-Unsent`. Syncing with a CRM's API waits for connected accounts
  (catalogue gate 2).

## Online store

* **Sales speed:** units a day per SKU over the last 30 days
  (`window_days`), from imported orders.
* **Reorder:** a product is reordered when its days of stock fall below
  lead time plus safety (14 + 7 days by default, or the product's own
  lead time). It orders enough for lead time plus `cover_days` (30), less
  what is on hand. Products that sell but have none left are flagged.
* **Purchase orders:** one CSV per supplier, with costs. Missing costs and
  missing suppliers are flagged. Nothing is ordered.
* **Margins:** products under `margin_floor` (30%) are flagged, and so are
  products with no cost.
* **Orders:** paid but not shipped within `ship_days` (2), and unpaid for 3+
  days.
* **Returns:** a return cannot exceed what was ordered, and can go back
  into stock. A product with 10+ sold that comes back 10% of the time or
  more is flagged, with the reasons.
* **Listings:** checked against Shopify's search-listing counters (title
  70, description 320).
* **Imports:**
  * Shopify product export, with variants joined to their product's title;
  * WooCommerce product export;
  * Shopify order export, one row per line item. Importing the same
    export twice counts nothing twice.

## Trades

* **Pricing:** parts cost plus `markup_pct` (20), labour at `labour_rate`
  (60/h), a `callout` fee, extras, then `vat_pct`. Prices come only from
  the price list, which can be imported from a supplier's CSV. A part name
  that matches two items is asked about.
* **Diary:** a booking from a quote takes the quote's hours. A clash is
  refused and names the job in the way. Free slots are worked out within
  `day_start`-`day_end` (08:00-17:00). Booking a quote accepts it; a
  declined quote cannot be booked.
* **Parts lists:** one CSV per supplier for the jobs booked in the next 7
  days, with which job each part is for.
* **Invoices:** built from the job's quote with the hours actually worked
  and any extras, one per job. Issuing (POST) numbers it, sets the due date
  from `terms_days`, and writes the PDF and an `.eml` draft. Payments
  (POST) may be partial.
* **Certificates:** each has a number of months it lasts; the due date
  handles month ends and leap days. Those due within 30 days, or overdue,
  are flagged, and the reminder is an `.eml` draft.
* **Needs:** `requires: ["accounting"]` for its PDF writer.

## Payroll

* **Tax and social security:** never worked out, because they are each
  country's law. They come from the payroll software's export
  (`import_deductions`: tax / income tax / PAYE, NI / social security /
  UIF, pension, other) or are typed in.
* **Gross pay:** salary / 12, or (hours + overtime x
  `overtime_multiplier`) x rate. Timesheet rows for one person add up.
* **Pension:** `pension_pct` of gross, recomputed when gross changes
  unless a pension amount was typed in.
* **Checks:**
  * blocking: net pay below zero, no bank account, two people paid into
    one account, an hourly rate under `minimum_hourly`;
  * to look at: gross moved more than `variance_pct` (10%) since last
    month's final run, starters, leavers, hourly staff with no hours, no
    deductions imported, salaried starters or leavers part-way through a
    month (pro rata).
* **Finalising (POST):** refused while a blocking problem stands. It
  writes payslip PDFs, a bank payment CSV to upload, and a balanced
  journal CSV. A final run cannot change.
* **Leave:** earned month by month from the leave year's start (or the
  start date), less days taken.
* **With Accounting on too:** "payroll" also matches Accounting's
  payroll review, which is for accountants. The first pack switched on
  wins, so "pay run" is the Payroll pack's own phrase.

## Tax practice

* **No tax law:** each return's deadline is a date the practitioner gives,
  or their rule (`deadline_rules`: `{"company": {"months": 9, "days": 1}}`
  after the period end). With neither, the pack asks.
* **Checklists:** the documents each return type needs come from
  `checklists`, or the built-in process lists for individual, company and
  VAT returns. A document is found by part of its name. When all are in, a
  return waiting for documents moves to preparing.
* **Chasers:** `.eml` drafts listing what is missing, at most one every
  `chase_days` (7). They go friendly, then firm, then final (deadline,
  late-penalty risk, "call me"). After three, the client is flagged for a
  phone call.
* **Workpapers:** a client's transaction CSV totalled by its category
  column, or by keywords (`category_words`). Anything left over is listed.
  The total must tie out to the sum of the categories.
* **Filing:** a return is marked filed only when every document is in and
  the tax filed is recorded. An assessment that differs from the tax filed
  by more than `assessment_tolerance` (1.00) is flagged.
* **Weekly deadlines** (Mondays): shows returns past their deadline, and
  those due within `urgent_days` (30) with documents missing, then drafts
  the chasers.

## Household finance

* **Statements:** any bank's CSV. The pack reads either one amount column
  or money in / money out, and dates as `2026-09-05`, `05/09/2026` or
  `05 Sep 2026`. A statement dropped in twice is imported once, while two
  identical payments on one day stay two.
* **Categories:** whole words in the description (so "tfl" is not found
  inside "netflix"), then any rule the household teaches
  (`teach_category`), which also re-files past payments. Positive amounts
  are income unless they are a transfer.
* **Budget:** a month's spending by category against `set_budget`.
  Overruns are flagged.
* **Regular payments:** a payee paid three or more times about a month
  apart (a median gap of 26-35 days) is treated as regular, if it was paid
  in the last 45 days. The pack gives the yearly cost, the next date and
  any price rise, and flags those due within 7 days.
* **Warranties:** one ending within 60 days is flagged.
* **Report:** in, out, saved, the category table, the biggest payments and
  the regular payments, as Markdown in `Reports/`.

## Lettings

* **Rent:** falls due on the tenancy's day each month; a 31st falls on the
  month's last day. Payments pay off the oldest month first. Arrears are
  dated from the oldest unpaid month.
* **Bank matching:** money in is matched to a tenancy by its reference in
  the description, or else by a tenant's surname. Anything unmatched is
  listed, and the same bank line is never counted twice.
* **Arrears letters:** a reminder at 3 days late, a second at 14 and a
  final at 30, with each step drafted once per run of arrears. After the
  final letter the pack flags that any formal notice depends on local
  law. It never drafts one.
* **Repairs:** target days by priority (urgent 1, high 7, normal 28). A
  works order `.eml` goes to a named contractor or the first of the trade.
  Repairs past their target are flagged.
* **Certificates:** expired or expiring within 30 days are flagged.
  Tenancies ending within `renewal_days` (60) are flagged too.
* **Deposits:** each deduction needs a reason, and together they cannot
  exceed the deposit. Settling (POST) fixes the split and ends the
  tenancy. The money moves through the deposit scheme, not here.

## Software developers

* **Reads, never writes the repo:** `git log`, `diff` and `grep` are run
  locally. Release notes, reviews and triage reports go to the pack's own
  folder. Nothing is committed, pushed, tagged, merged or labelled. Tool
  names stay clear of the core's `git_*` tools.
* **Versions:** worked out from conventional commits since the last tag.
  A breaking change (`!` or `BREAKING CHANGE`) is a major bump (minor
  before 1.0), `feat` is minor, and `fix`/`perf` is patch.
* **Release notes:** breaking changes, features, fixes, performance and
  other changes. Docs, tests, CI, chore, style and build commits are left
  out.
* **Change review** (a range, or the uncommitted work) flags:
  * secrets: AWS and Google keys, private keys, GitHub tokens, hard-coded
    passwords;
  * debug leftovers outside tests;
  * source changed with no test changed;
  * changes over `big_change` (400) lines.
* **Checks:**
  * TODO/FIXME/HACK/XXX in tracked files;
  * unpinned or ranged Python requirements, packages listed twice, and
    npm `*`/`latest`;
  * JUnit XML failures grouped by cause.
* **Issue triage** (from `gh issue list --json …`): suggests labels for
  unlabelled issues, finds likely duplicates by title similarity (80%),
  and lists issues quiet for `stale_days` (60).

## Podcast production

* **Transcripts:** SRT, WebVTT (`<v Name>` voices) or `[00:01:02] Name:
  text` lines. The pack gives the episode's length, words, and each
  speaker's seconds and share.
* **Clips:** windows of `clip_min`-`clip_max` (20-60 s) around the words
  asked for, or around questions if no words are given. The best come
  first, and no two overlap.
* **Chapters:** must start at 00:00:00, go in order, have a title and
  stay inside the episode. They are written as Podcasting 2.0 JSON
  chapters (`version 1.2.0`, `startTime` in seconds) and as a plain list.
* **Show notes:** summary, guest, chapters and links.
* **Publishing:** marking an episode published is refused until it has
  final audio, notes and valid chapters, and its guest has signed a
  release.
* **Calendar:** release slots on `release_weekday` (Tuesday by default),
  what fills them, and the gaps. Episodes due within a week that are not
  ready are flagged, and so are recorded guests with no signed release.
