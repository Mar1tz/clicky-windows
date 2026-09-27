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
