# Industry extensions — design

*Status: Phase 1 built (M24 framework, M25 Accounting). Accounting is the
first pack and the blueprint for every later one (Legal, Sales, HR, …).
What it does for an accountant: [ACCOUNTING.md](ACCOUNTING.md).*

The rule that shapes everything below: **core stays generic.** Nothing under
`agents/` may know what an invoice is. An extension is a folder that can be
deleted, and the app still runs.

---

## 1. What the current architecture already gives us

Inspection of `agents/` (M0–M23) shows the seams an extension needs are
mostly there — they were built for the built-in worker types and just
need to be opened up:

| need                           | existing seam                                              | change needed in core |
|--------------------------------|------------------------------------------------------------|-----------------------|
| domain tools                   | `Registry.register(tool)`; `ToolSpec.category`             | `Registry.unregister`, ownership tag per tool (for clean unload) |
| specialised agent roles        | `WorkerRegistry.register(worker)`; `worker_type` string    | `unregister`; `Worker.system_prompt` appends `ctx.extras["knowledge"]` |
| agent templates                | `AgentSpec` (name, goal, worker_type, tools, permissions)  | none — a template is an `AgentSpec` factory plus metadata |
| approval / risk                | `RiskLevel` + `PermissionPolicy` + tool `always_ask` floor + per-task grants | none for levels (see §6); one flag: `ungrantable` for SENSITIVE |
| deterministic workflows        | `TemplatePlanner(templates=[ProjectTemplate…])`, `ProviderPlanner(fallback=…)` | planner accepts extension templates at runtime |
| validation                     | `RuleVerifier(rules={tool_name: rule})`                    | rules added/removed at runtime |
| intent ("reconcile the bank")  | `HeuristicIntentClassifier` (module-level regexes)         | generic `IntentRule` list the classifier consults first |
| domain knowledge in prompts    | `ctx.extras["context_lines"]` (added in field-fix 3)       | generic `ContextProvider` hook: extension contributes lines per task |
| memory schema                  | `MemoryStore` scopes + `MemoryEntry.kind/tags`            | none |
| background jobs                | — (nothing schedules tasks today)                          | **new** generic `Scheduler` with `Schedule` / `FolderWatch` triggers |
| exceptions the user must see   | `Task.metadata`, timeline `describe()`                     | generic `Finding` record + "Needs attention" surfacing in panel |
| UI modules                     | `ActiveAgentsPanel`, `AgentSettingsDialog`, tray           | generic dashboard-module slot + Extensions dialog |
| voice activation               | `_TECHNICAL_RULES` DELEGATION, `manage_agents` tool        | extension-supplied hint lines; `manage_agents(action="enable_extension")` |
| settings                       | `config.py` `agent_*` + `.env`                             | `TORA_EXTENSIONS_ENABLED`; per-extension JSON in `%LOCALAPPDATA%\Tora\extensions\` |

Everything in the right-hand column is *generic*: it would be needed by a
Legal pack in exactly the same shape.

---

## 2. Layout (as built)

```
agents/extensions/                 # the FRAMEWORK (core, generic)
    __init__.py                    # public API
    base.py                        # IAgentExtension, ExtensionManifest (+ JSON loader/validator)
    hooks.py                       # AgentTemplate, Skill, Workflow, Trigger/Schedule/FolderWatch, IntentRule,
                                   #   PermissionProfile, IntegrationSpec, DocumentType, DashboardModule
    registry.py                    # ExtensionRegistry: discover / install / enable / configure / disable /
                                   #   uninstall / restore; WorkflowPlanTemplate adapter
agents/manager/scheduler.py        # generic background trigger runner (schedules + folder watches)
agents/tools/spreadsheet_tools.py  # core spreadsheet tools (any agent)
agents/ui/extensions.py            # Extensions switchboard + dashboard frame (a tab per pack module)

extensions/                        # INDUSTRY PACKS - removable folders
    accounting/
        manifest.json  __init__.py (create_extension)  extension.py (AccountingExtension)
        knowledge.py               # skills (knowledge layer) + coding hints
        jurisdiction.py            # AccountingJurisdictionProfile + presets (generic, ZA, GB, US, EU, AU)
        permissions.py             # ActionLevel READ/DRAFT/POST/SENSITIVE, ConfidencePolicy
        workspace.py               # ClientWorkspace (accounting.json + folders)
        session.py                 # per-client provider + draft store cache
        drafts.py                  # DraftStore: drafts, open exceptions, processed documents
        books.py                   # the double-entry ledger engine behind local/demo books
        demo.py  pdfgen.py  tables.py
        providers/                 # base.py (IAccountingProvider), books_provider.py (Local + Mock),
                                   #   csv_excel.py; Xero/QuickBooks/Sage declared, Phase 2
        tools/                     # common.py (@accounting_tool, levels), ledger.py, bank.py, invoices.py,
                                   #   posting.py, checks.py, documents.py, reports.py
        roles.py                   # the 8 AgentTemplates
        worker.py                  # AccountingWorker (one class, eight roles)
        workflows.py               # 9 workflows + the accountant-initiated post/release/submit runs
        validation.py              # RuleVerifier rules
        ui/dashboard.py  ui/wizard.py
tests/agents/test_extensions_framework.py   test_accounting_pack.py   test_tools_spreadsheet.py
tests/agents/test_ui_extensions.py
```

`tora.spec` collects `extensions` and ships each `manifest.json`. Deleting
`extensions/accounting/` leaves a working app with no accounting anywhere.

---

## 3. The extension API (generic)

```python
class IAgentExtension(ABC):
    manifest: ExtensionManifest                     # id, name, version, capabilities, required_tools, optional_integrations

    def get_agent_templates(self) -> list[AgentTemplate]: ...
    def get_tools(self, ctx: ExtensionContext) -> list[BaseTool]: ...
    def get_skills(self) -> list[Skill]: ...        # named prompt fragments (knowledge layer), injected only when enabled
    def get_workflows(self) -> list[Workflow]: ...
    def get_permissions(self) -> PermissionProfile: ...   # default policy + ungrantable tools + confidence thresholds
    def get_integrations(self) -> list[IntegrationSpec]: ...   # what "Connect…" offers (mock, local, xero…)
    def get_dashboard_modules(self) -> list[DashboardModule]: ...
    def get_document_types(self) -> list[DocumentType]: ...
    def get_validation_rules(self) -> dict[str, Rule]: ...     # keyed by tool name, for RuleVerifier
    def get_intent_rules(self) -> list[IntentRule]: ...        # "reconcile the bank" → worker_type
    def get_context_provider(self) -> ContextProvider | None: ...   # lines for planner/worker prompts per task
    def activation_phrases(self) -> list[str]: ...  # "I am an accountant" → offer to enable

    # lifecycle
    def install(self, ctx) / configure(self, ctx, settings) / enable(self, ctx) / disable(self, ctx) / uninstall(self, ctx)
```

`ExtensionContext` hands the extension the things it may plug into — the
tool registry, worker registry, planner templates, verifier rules,
classifier rules, scheduler, memory, data dir, settings — and nothing
else. `ExtensionRegistry` records what each extension registered so
`disable()` removes exactly that (tools, workers, templates, rules,
schedules, dashboard modules) and nothing more.

**Naming.** Tool names cannot contain dots (OpenAI-compatible endpoints
reject them), so the "namespace" is `ToolSpec.category = "accounting"`
plus the registry's ownership map. Names follow your list
(`get_trial_balance`, `create_draft_bill`, …).

---

## 4. Provider abstraction — `IAccountingProvider`

A capability interface, asked for operations; agents never see Xero or
Sage. Every method is async, returns plain dataclasses, and declares
which **action level** it is:

```
READ   get_accounts, get_trial_balance, get_general_ledger, get_bank_transactions,
       get_customer_invoices, get_supplier_invoices, get_contacts, get_purchase_orders
DRAFT  create_draft_journal, create_draft_bill, create_draft_invoice, create_draft_payment_batch,
       categorize_transaction(confidence), match_transaction, attach_document
POST   post_journal(draft_id), approve_bill(draft_id), send_invoice(draft_id)
SENS.  release_payment_batch(id), submit_tax_return(id)
```

`capabilities()` says which a provider supports — `MockAccountingProvider`
supports all (canned data); `LocalBooksProvider` supports everything but
payments/tax submission; a Phase-2 `XeroProvider` maps each onto the API.

**Drafts are ours, not the provider's.** `create_draft_*` writes to the
extension's draft store (`accounting.sqlite` in the client workspace)
with a confidence score and status `draft | flagged | approved | posted |
rejected`. Only `post_*` touches the provider's books, and `post_*` is
the approval boundary. This is what makes Mock, Local and Xero behave
identically from the agent's point of view.

Integration priority (§6 of your spec) is a property of each
`IntegrationSpec`: `api > oauth > file > browser > uia > vision`. The
provider layer picks the highest available; the computer worker is a last
resort and never the default when an API exists.

---

## 5. Agent templates and the single worker

One `AccountingWorker` class; eight `AgentTemplate`s that differ in
mission, tool subset, permissions, triggers, memory scope, default
workflow and expected artifacts:

| template               | tools (subset)                                            | default trigger      | artifacts |
|------------------------|-----------------------------------------------------------|----------------------|-----------|
| Bookkeeper             | bank, documents, categorize, create_draft_bill/journal    | folder watch (inbox) | drafts + exceptions |
| AccountsPayable        | supplier invoices, PO match, duplicates, supplier statement reconcile, draft payment batch | folder watch | draft batch, exception list |
| AccountsReceivable     | overdue invoices, statements, reminder drafts, aging      | daily 08:00          | reminder drafts, aged debtors |
| MonthEnd               | reconciliations, suspense, accruals/prepayments, variances, draft journals, management pack | monthly, or "prepare month-end for X" | pack + exceptions |
| TaxPreparation         | transaction review, missing docs, tax pack, deadlines     | period end           | tax workpapers |
| AuditAssistant         | request lists, evidence folders, samples, anomaly review  | manual               | audit folder |
| PayrollReview          | variance checks, payroll-to-GL, duplicates                | pay run              | summary report |
| ManagementReporting    | packs, KPIs, variance commentary, cash-flow forecast      | monthly              | reporting pack |

A template is turned into a live agent by the existing
`AgentManager.create_agent(spec)`; the wizard fills in client workspace,
provider, permission level, confidence threshold and schedule.

---

## 6. Permission model

Accounting levels map onto the core risk model without changing it:

| level     | core                                        | behaviour |
|-----------|---------------------------------------------|-----------|
| READ      | `RiskLevel.SAFE`                            | never asks |
| DRAFT     | `RiskLevel.LOW`                             | auto within confidence policy |
| POST      | `RiskLevel.HIGH` + `always_ask`             | approval dialog every time; per-task grant allowed |
| SENSITIVE | `RiskLevel.HIGH` + `always_ask` + `ungrantable` | approval every time, no grants, dialog requires typed confirmation |

`ungrantable` is the one new (generic) flag: the manager's per-task
grants skip it. **Default rule: financially consequential actions require
explicit approval** — enforced by the tool's declared level, not by
prompt wording.

`ConfidencePolicy(auto=0.95, flag=0.70)` is configurable per agent;
`categorize_transaction(confidence=)` and `create_draft_bill(confidence=)`
route: ≥auto → draft; ≥flag → draft + `Finding`; below → refuse and
record a Finding "ask accountant". The worker is told the thresholds.

---

## 7. Workflows and background triggers (generic core + accounting definitions)

`agents/manager/scheduler.py` (core): a loop on the manager runtime that
fires `Trigger`s — `Schedule(every=daily|weekly|monthly, at, weekday, day)`,
`FolderWatch(path, patterns)`, `Manual`. Due → `manager.submit(goal,
agent=template.spec(...))`. Persisted in `schedules.json`; survives
restart via the existing `recover()`.

Accounting defines three `Workflow`s with explicit checklists (each item
becomes a `Step` in the deterministic planner, so the model cannot skip
a check): **InvoiceProcessing** (folder watch), **DebtorsMorning**
(daily), **MonthEnd** (monthly or on request). Steps that need judgement
(coding, variance commentary) run through the model; steps that are
mechanical (duplicate check, reconciliation math) are tool calls with
scripted arguments.

---

## 8. Knowledge, jurisdiction, workspace, exceptions

* **Knowledge layer** (`knowledge.py`): terminology and rules of the
  trade as `Skill` fragments — double-entry, TB, AP/AR, accruals,
  prepayments, fixed assets, depreciation, tax control, reconciliation,
  aging, cash flow, month/year-end, audit evidence, workpapers. Injected
  into the worker system prompt and planner only when the extension is
  enabled. **Never** jurisdiction-specific advice.
* **`AccountingJurisdictionProfile`**: country, currency, tax label,
  default rate, fiscal year start, date convention, reporting periods,
  document requirements, deadlines. Presets `generic / GB / US / EU / ZA`
  are labels and dates, not law; everything is editable.
* **`ClientWorkspace`** (`accounting.json` in the client folder): client,
  period, currency, provider, bank accounts, documents dir, deadlines,
  outstanding tasks, month-end status → one context line:
  `Client: ABC Manufacturing · Period: 2026-08 · ZAR · Provider: local books · Month-end 72%`.
* **Exceptions** (`Finding`, generic): every tool that hits doubt records
  one (severity, title, detail, entity, suggested action). The task's
  final summary is exception-driven ("27 checks done · Needs review: 4"),
  the panel shows "Needs attention: N", and the Accounting dashboard
  lists them.

---

## 9. UI (registered, not hardcoded)

* **Extensions dialog** (core): list of packs with Enable / Configure /
  Disable; "Configure" opens the pack's own settings widget.
* **Dashboard module slot** (core): the Active Agents panel gets a tab
  strip; each enabled extension may add tabs. Accounting adds one:
  *Accounting* — Active work, Month-end, Debtors, Creditors, Missing
  documents, Tax deadlines, Audit requests, Reports.
* **Setup wizard** (accounting): "Create an accounts payable agent for
  this client" → accounting system, inbox/folder, supplier folder,
  permission level, approval threshold, schedule → `create_agent`.
* **Voice**: `activation_phrases` ("I am an accountant", "I do the
  books") → Tora offers to enable; `manage_agents(action=
  "enable_extension", id="accounting")`; extension hint lines join the
  DELEGATION rules so "prepare month-end for ABC" is delegated.

---

## 10. Phase 1 (build now, mock-first)

1. Framework: `agents/extensions/*`, registry + lifecycle, manager
   wiring (`extensions=`), `Registry/WorkerRegistry.unregister`,
   `IntentRule`s, `ContextProvider`, `Finding`, `ungrantable`,
   `Scheduler`, Extensions dialog, dashboard slot, voice hooks.
2. Accounting: manifest, knowledge, jurisdiction, permissions +
   confidence, `IAccountingProvider` + Mock + LocalBooks (+ CSV read),
   the tool set (§2), 8 templates + worker, 3 workflows, validation,
   dashboard module, setup wizard, `ClientWorkspace`.
3. Tests (`tests/extensions/`): load/unload leaves registries clean;
   provider abstraction (same tests run against Mock and LocalBooks);
   permissions (POST asks, SENSITIVE ungrantable); confidence routing;
   duplicate invoices; reconciliation workflow; month-end workflow
   end-to-end on mock data; crash recovery / restart with schedules;
   missing documents; approval; sensitive-action blocking.
4. Docs: this file (final), `ACCOUNTING.md` (use-case catalogue + how to
   test), MILESTONES M24–M26, CLAUDE.md ownership (`extensions/accounting`
   → worker 3; `agents/extensions` → manager).

## 11. Phase 2

Xero / QuickBooks / Sage providers (OAuth via connected accounts +
`http_request`), email (IMAP first, Graph/Gmail after), OneDrive /
SharePoint / Drive, formula-preserving Excel (openpyxl is already
installed), bank feeds where available.

---

## 12. Open decisions (defaults I will take unless told otherwise)

1. **Packs live in top-level `extensions/`**, framework in
   `agents/extensions/`. (Alternative: everything under `agents/`.)
2. **Local books is a real provider in Phase 1**, not just mock — so an
   accountant without Xero can use it on day one. Costs ~1 day.
3. **SENSITIVE actions require a typed word** in the approval dialog
   ("RELEASE"). (Alternative: plain Approve button.)
4. Default jurisdiction preset: `generic`; the wizard asks.


---

## 13. As built (differences from the proposal)

* `IAgentExtension.get_tools/get_workers(ctx)` take the ExtensionContext;
  `get_context_lines(task)` is the ContextProvider; `scheduled_jobs(ctx)`
  returns the pack's jobs from its settings; an optional
  `workspace_for(meta, ctx)` lets a pack route a task to a folder (the
  accounting client).
* Tools can declare `sensitive` (+ `confirm_word`, `ask_reason`) and
  `soft_fail`. Registry: sensitive ⇒ always asks; manager: never
  granted per task, `approve()` refuses without the typed word;
  RuleVerifier: a `soft_fail` tool's failure is information, not a failed
  step. All four are generic.
* `Finding` + `EventKind.FINDING` in core; tools return
  `data["findings"]` / `data["checks"]`; the worker lifts them onto the
  task and the orchestrator's result text is exception-first.
* Intent rules are skipped for questions (`what/how/why…`), so "what is a
  month-end?" is always conversation.
* A pack never creates a client from a phrase in speech ("chase Karoo for
  payment" names a customer); it falls back to the active client and says so.
* Per-turn voice context lists only ENABLED packs; Tora asks
  `manage_agents list_extensions` when the user says what they do.
* Core spreadsheet tools (`read_workbook`, `write_workbook`, `clean_data`,
  `reconcile_sheets`, `check_formulas`, `compare_periods`) are generic —
  category `spreadsheet` — and used by the pack's templates.

## 14. Building the next pack (Legal, Sales, HR…)

1. `extensions/<id>/manifest.json`: id, name, capabilities, activation
   phrases, "offer" bullets.
2. `extensions/<id>/__init__.py`: `create_extension()` returning your
   `IAgentExtension` subclass.
3. Tools: plain `agents.tools.base.Tool` subclasses, `spec.category = "<id>"`,
   names that don't clash (the registry refuses a clash and rolls back).
   Mark money/filing/external-send actions `sensitive`, anything that changes
   a system of record `always_ask`.
4. One worker class (subclass `agents.workers.base.Worker`) + `AgentTemplate`s
   for the roles; skills for the knowledge layer; `Workflow`s with scripted
   steps for every mechanical check.
5. `IntentRule`s that need a verb *and* an object; an `extract` for names.
6. A dashboard module factory (Qt imports inside the factory).
7. Tests with a mock provider — the whole pack must run offline, the way
   the Accounting month-end does on its demo books.
