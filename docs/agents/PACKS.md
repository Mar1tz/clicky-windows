# Built packs

*Every pack in `extensions/`, one row each. The catalogue of ideas and the
build order are in the "HeyTora Extensions — the catalogue" doc. How a
pack is put together is in [EXTENSIONS.md](EXTENSIONS.md) and the kit in
[CREATOR.md](CREATOR.md#the-kit).*

Each pack is tested offline in `tests/agents/test_<id>_pack.py`, and all of
them in `tests/agents/test_e2e_every_pack.py`. That second file checks that
each pack:

* plugs in and comes out cleanly;
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
