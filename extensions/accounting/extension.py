"""AccountingExtension: the pack, as the platform sees it.

Everything the Accounting pack contributes goes through the generic
IAgentExtension getters - templates, tools, the worker, skills,
workflows, permissions, integrations, dashboard module, document types,
validation rules, intent rules and scheduled jobs. Nothing in the core
knows about accounting; delete this folder and the app still runs.

Settings (ExtensionRegistry keeps them in extensions.json):
    clients_root          where client folders live (default <workspace>/Accounting Clients)
    active_client         the client a request without a name refers to
    default_jurisdiction  preset for new clients
    confidence_auto/flag  the default confidence policy
    agents                configured agents: [{id, template, client, schedule, enabled}]
"""
from __future__ import annotations

import calendar
import logging
import os
import re
from datetime import date
from typing import Any, Optional

from agents.core import EventKind, RiskLevel, Task, TaskKind
from agents.extensions import (
    DashboardModule, DocumentType, ExtensionManifest, FolderWatch, IAgentExtension, IntentRule,
    PermissionProfile, Schedule, Trigger,
)

from . import demo, knowledge, roles, workflows
from .permissions import LEVEL_RISK, ActionLevel
from .providers import INTEGRATIONS
from .session import SessionCache
from .workspace import ClientWorkspace, find_client, list_clients

log = logging.getLogger("extensions.accounting")

HERE = os.path.dirname(os.path.abspath(__file__))
DEMO_CLIENT = "ABC Manufacturing (Demo)"

# Read by tools for the default confidence policy (clients may override).
_CURRENT: dict[str, Any] = {}


def current_settings() -> dict[str, Any]:
    return dict(_CURRENT)


# -- understanding requests ---------------------------------------------------------------------

_VERB = (r"\b(?:please\s+)?(?:prepare|run|do|start|close|process|reconcile|chase|review|check|draft|build|make|"
         r"generate|create|produce|pay|file|categori[sz]e|code|import|clear|find|handle|sort\s+out|work\s+on|"
         r"update|go\s+through|get)\b")


_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
_NOT_CLIENTS = {"this client", "the client", "them", "it", "me", "us", "the month", "this month", "last month",
                "month-end", "month end", "the period", "the year", "this period", "the books", "him", "her"}


def extract_client_period(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    t = text
    m = re.search(r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?\s+(\d{4})\b", t, re.I)
    if m:
        out["period"] = f"{m.group(2)}-{_MONTHS[m.group(1).lower()]:02d}"
        t = t[:m.start()] + t[m.end():]
    else:
        m = re.search(r"\b(20\d{2})-(0[1-9]|1[0-2])\b", t)
        if m:
            out["period"] = f"{m.group(1)}-{m.group(2)}"
            t = t[:m.start()] + t[m.end():]
        elif re.search(r"\blast month\b", t, re.I):
            d = date.today().replace(day=1)
            y, mm = (d.year - 1, 12) if d.month == 1 else (d.year, d.month - 1)
            out["period"] = f"{y}-{mm:02d}"
    m = re.search(r"\bfor\s+(?:the\s+)?(?:client\s+)?(?P<c>[^.?!,]+?)(?=\s+(?:for|in|by|as\s+of|on|please|now|today|"
                  r"this|last|and)\b|[.?!,]|\s*$)", t, re.I)
    if m:
        c = m.group("c").strip(" '\"")
        if c and c.lower() not in _NOT_CLIENTS and not re.match(r"^(the\s+)?(month|period|year|quarter)\b", c, re.I):
            out["client"] = c
    return out


def _rule(obj: str, template: str, workflow: str, reply: str, *, verb: str = _VERB,
          extract: bool = True) -> IntentRule:
    return IntentRule(pattern=verb + r"[^.?!]{0,80}?" + obj, template_id=template, workflow_id=workflow,
                      reply=reply, extension_id="accounting", extract=extract_client_period if extract else None)


INTENT_RULES = [
    _rule(r"(?:the\s+|this\s+)?(?:books|month|period)\b", "month_end", "month_end",
          "Understood. The Month-End Agent will run the close checklist and bring you only the exceptions.",
          verb=r"\b(?:please\s+)?close\b"),
    _rule(r"\b(?:month[- ]?end|close\s+(?:the\s+)?(?:books|month|period)|period[- ]end)\b", "month_end",
          "month_end", "Understood. The Month-End Agent will run the close checklist and bring you only the "
                       "exceptions."),
    _rule(r"\b(?:overdue\s+invoices|debtors|collections?|payment\s+reminders?|aged\s+receivables|"
          r"who\s+owes)\b", "accounts_receivable", "debtors_run",
          "Understood. The Accounts Receivable Agent will draft the reminders and escalate the big ones."),
    _rule(r"\b(?:supplier\s+invoices?|accounts\s+payable|payables|payment\s+(?:run|batch)|bills\s+to\s+pay|"
          r"creditors|supplier\s+statements?)\b", "accounts_payable", "ap_run",
          "Understood. The Accounts Payable Agent is on it - drafts only, nothing paid."),
    _rule(r"\b(?:an?\s+)?invoice\s+(?:to|for)\b", "accounts_receivable", "",
          "Understood. I'll draft the invoice for your review.",
          verb=r"\b(?:please\s+)?(?:create|draft|raise|make|prepare|issue|send)\b", extract=False),
    _rule(r"\b(?:the\s+|my\s+|new\s+|incoming\s+)?(?:invoices|inbox|bills|receipts)\b", "bookkeeper",
          "invoice_processing", "Understood. The Bookkeeping Agent will process the documents and draft the bills.",
          verb=r"\b(?:please\s+)?(?:process|handle|sort\s+out|go\s+through|do|work\s+on|deal\s+with|capture|"
               r"enter|file)\b"),
    _rule(r"\b(?:the\s+bank|bank\s+rec\w*|bank\s+statements?|transactions|bookkeeping|the\s+books)\b",
          "bookkeeper", "bookkeeping", "Understood. The Bookkeeping Agent will reconcile and code the bank."),
    _rule(r"\b(?:vat|gst|sales\s+tax|tax\s+(?:pack|return|prep\w*|workpapers?))\b", "tax_preparation",
          "tax_prep", "Understood. The Tax Preparation Agent will prepare the pack - it will not file anything."),
    _rule(r"\baudit\b", "audit_assistant", "audit_prep",
          "Understood. The Audit Assistant will build the request list, samples and evidence."),
    _rule(r"\bpayroll\b", "payroll_review", "payroll_review",
          "Understood. The Payroll Review Agent will check the register against last month and the ledger."),
    _rule(r"\b(?:management\s+(?:pack|reports?|accounts)|kpis?|variance\s+(?:report|analysis|commentary)|"
          r"cash\s*-?\s*flow\s+forecast|board\s+pack|reporting\s+pack)\b", "management_reporting",
          "management_reporting", "Understood. The Management Reporting Agent will build the pack."),
]


# -- the extension ------------------------------------------------------------------------------------

class AccountingExtension(IAgentExtension):
    manifest = ExtensionManifest.load(HERE)

    def __init__(self) -> None:
        self.sessions = SessionCache()
        self._unsub = None
        self._settings: dict[str, Any] = {}
        self._manager = None

    # -- parts ------------------------------------------------------------------------------------
    def settings_defaults(self) -> dict[str, Any]:
        return {"clients_root": "", "active_client": "", "default_jurisdiction": "generic",
                "confidence_auto": 0.95, "confidence_flag": 0.70, "agents": []}

    def get_agent_templates(self):
        return list(roles.TEMPLATES)

    def get_tools(self, ctx):
        from .tools import build_tools
        self._settings = ctx.settings
        return build_tools(self.sessions, lambda: self._settings)

    def get_workers(self, ctx):
        from .tools import REGISTRY
        from .worker import AccountingWorker
        return [AccountingWorker([d.name for d in REGISTRY])]

    def get_skills(self):
        return list(knowledge.SKILLS)

    def get_workflows(self):
        return list(workflows.WORKFLOWS)

    def get_permissions(self) -> PermissionProfile:
        from .tools import REGISTRY
        return PermissionProfile(
            levels=dict(LEVEL_RISK),
            tool_levels={d.name: d.level.value for d in REGISTRY},
            sensitive=[d.name for d in REGISTRY if d.level is ActionLevel.SENSITIVE],
            always_ask=[d.name for d in REGISTRY if d.level.always_ask],
            notes="READ never asks; DRAFT runs within the confidence policy; POST always asks; SENSITIVE asks "
                  "every time, cannot be granted for a task, and needs a typed confirm word.")

    def get_integrations(self):
        return list(INTEGRATIONS)

    def get_dashboard_modules(self):
        def factory(manager, extension, parent=None):
            from .ui.dashboard import AccountingDashboard
            return AccountingDashboard(manager, extension, parent)
        return [DashboardModule("accounting.dashboard", "Accounting", factory, order=10)]

    def get_document_types(self):
        from .tools.documents import DOCUMENT_TYPES
        return [DocumentType(k, k.replace("_", " ").title(), v["keywords"], folder=v["folder"])
                for k, v in DOCUMENT_TYPES.items()]

    def get_validation_rules(self):
        from .validation import build_rules
        return build_rules(self.sessions)

    def get_intent_rules(self):
        return list(INTENT_RULES)

    def get_context_lines(self, task: Task) -> list[str]:
        try:
            s = self.sessions.get(task.workspace)
        except Exception:  # noqa: BLE001 - not a client folder (yet)
            return ["Accounting: no client folder for this task - ask which client."]
        prof = s.ws.profile()
        lines = [s.ws.context_line(s.provider.display_name),
                 f"{prof.tax_label} standard rate {prof.tax_rate:g}% ({prof.country}); fiscal year starts "
                 f"{prof.fiscal_year_start}. {s.ws.confidence_policy(current_settings()).describe()}"]
        asked = (task.metadata or {}).get("client_requested")
        if asked:
            lines.append(f"The request named '{asked}', which is not a client folder - working on {s.ws.client}. "
                         "If that is wrong, stop and say so.")
        period = (task.metadata or {}).get("period")
        if period and period != s.ws.period:
            lines.append(f"This task is for period {period} (the client's current period is {s.ws.period}).")
        return lines

    # -- clients ----------------------------------------------------------------------------------------
    def clients_root(self, ctx) -> str:
        root = (ctx.settings.get("clients_root") or "").strip() or os.path.join(ctx.workspace_root,
                                                                               "Accounting Clients")
        os.makedirs(root, exist_ok=True)
        return root

    def create_client(self, ctx, name: str, *, provider: str = "local", jurisdiction: str = "",
                      period: str = "", **extra: Any) -> ClientWorkspace:
        ws = ClientWorkspace.create(self.clients_root(ctx), name, provider=provider,
                                    jurisdiction=jurisdiction or ctx.settings.get("default_jurisdiction") or "generic",
                                    period=period, **extra)
        self.sessions.get(ws.root)                    # opens (and for local books, sets up) the books
        return ws

    def create_demo_client(self, ctx) -> ClientWorkspace:
        root = self.clients_root(ctx)
        existing = find_client(root, DEMO_CLIENT)
        if existing and existing.client == DEMO_CLIENT:
            return existing
        ws = ClientWorkspace.create(root, DEMO_CLIENT, provider="mock", jurisdiction="ZA", period=demo.PERIOD,
                                    notes="Demo books with planted exceptions. Safe to experiment; delete the "
                                          "folder to reset.")
        demo.seed_inbox(ws.inbox)
        self.sessions.get(ws.root)                    # seeds the books and documents
        return ws

    def workspace_for(self, meta: dict[str, Any], ctx) -> str:
        root = self.clients_root(ctx)
        name = (meta.get("client") or "").strip()
        if name:
            ws = find_client(root, name)
            if ws is not None:
                meta["client"] = ws.client
                return ws.root
            # Never create a client from a phrase in speech: "chase Karoo for
            # payment" names a customer, not a client. Fall back and say so.
            meta["client_requested"] = name
            meta.pop("client", None)
        active = (ctx.settings.get("active_client") or "").strip()
        if active:
            ws = find_client(root, active)
            if ws is not None:
                meta["client"] = ws.client
                return ws.root
        clients = list_clients(root)
        ws = clients[0] if clients else self.create_demo_client(ctx)
        meta["client"] = ws.client
        return ws.root

    def clients(self, ctx) -> list[ClientWorkspace]:
        return list_clients(self.clients_root(ctx))

    # -- scheduled agents -----------------------------------------------------------------------------
    def scheduled_jobs(self, ctx) -> list[Any]:
        from agents.manager.scheduler import ScheduledJob
        jobs = []
        for a in ctx.settings.get("agents") or []:
            if not a.get("enabled", True):
                continue
            t = roles.BY_ID.get(a.get("template", ""))
            if t is None:
                continue
            ws = find_client(self.clients_root(ctx), a.get("client", ""))
            if ws is None:
                continue
            wf = workflows.BY_ID.get(a.get("workflow") or t.default_workflow)
            sched = a.get("schedule") or {}
            if sched.get("kind") == "manual":
                continue
            if sched.get("kind") == "folder" or (not sched and isinstance(wf.trigger, FolderWatch)):
                folder = (a.get("inbox") or "").strip() or ws.inbox
                patterns = list(getattr(wf.trigger, "patterns", None) or ["*.pdf", "*.eml", "*.jpg", "*.png"])
                trigger = FolderWatch(path=folder, patterns=patterns)
            elif sched.get("every") or isinstance(wf.trigger, Schedule):
                base = wf.trigger if isinstance(wf.trigger, Schedule) else Schedule()
                trigger = Schedule(every=sched.get("every", base.every), at=sched.get("at", base.at),
                                   weekday=int(sched.get("weekday", base.weekday)), day=int(sched.get("day", base.day)))
                folder = ""
            else:
                continue
            risk = RiskLevel.SAFE if a.get("level") == "read" else None
            jobs.append(ScheduledJob(id=f"accounting:{a.get('id') or t.id + ':' + ws.client}",
                                     name=f"{a.get('name') or t.name} - {ws.client}", trigger=trigger,
                                     make_task=self._job_task(t, wf, ws, risk=risk, name=a.get("name", "")),
                                     owner="accounting", folder=folder))
        return jobs

    def _job_task(self, t, wf, ws, *, risk: Optional[RiskLevel] = None, name: str = ""):
        def make(extra: dict[str, Any]):
            goal = f"{wf.goal} for {ws.client}"
            task = Task(title=f"{wf.name} - {ws.client}", goal=goal, workspace=ws.root, kind=TaskKind.GENERAL,
                        worker_type=t.worker_type,
                        metadata={"extension": "accounting", "template": t.id, "workflow": wf.id,
                                  "client": ws.client, **extra})
            return task, t.spec(goal, workspace=ws.root, name=f"{name or t.name} ({ws.client})",
                                max_auto_risk=risk)
        return make

    def task_for(self, template_id: str, client_root: str, *, workflow_id: str = "",
                 period: str = "", extra: Optional[dict[str, Any]] = None) -> tuple[Task, Any]:
        """A ready-to-submit task for 'Run now' in the dashboard or wizard
        (and for the accountant-initiated post / release / submit runs)."""
        t = roles.BY_ID[template_id]
        wf = workflows.BY_ID[workflow_id or t.default_workflow]
        ws = ClientWorkspace.load(client_root)
        meta = dict(extra or {})
        if period:
            meta["period"] = period
        return self._job_task(t, wf, ws)(meta)

    # -- lifecycle ------------------------------------------------------------------------------------------
    def on_enable(self, ctx) -> None:
        _CURRENT.clear()
        _CURRENT.update(ctx.settings)
        self._settings = ctx.settings
        self._manager = ctx.manager
        events = getattr(ctx.manager, "events", None)
        if events is not None:
            self._unsub = events.subscribe(self._on_event)

    def on_configure(self, ctx, settings: dict[str, Any]) -> None:
        _CURRENT.clear()
        _CURRENT.update(settings)
        self._settings = settings

    def on_disable(self, ctx) -> None:
        if self._unsub is not None:
            try:
                self._unsub()
            finally:
                self._unsub = None
        self.sessions.drop()
        _CURRENT.clear()

    def _on_event(self, ev) -> None:
        """Keep the client's month-end status current as the close runs."""
        if ev.kind not in (EventKind.STEP_DONE, EventKind.STEP_FAILED) or self._manager is None:
            return
        task = self._manager.get_task(ev.task_id or "")
        if task is None or (task.metadata or {}).get("workflow") != "month_end":
            return
        try:
            ws = ClientWorkspace.load(task.workspace)
            done = [s.title for s in task.steps if s.status.value == "done"]
            ws.set_month_end(task.metadata.get("period") or ws.period, done, len(task.steps))
        except Exception:  # noqa: BLE001 - progress is a nicety
            log.debug("month-end status update failed", exc_info=True)
