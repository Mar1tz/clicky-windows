"""FreelancerExtension: the pack, as the platform sees it.

A back office for one person. It keeps its money in the Accounting pack's
ledger (manifest `requires: ["accounting"]`) but does not need that pack
switched on: a freelancer gets one business folder, not a practice full of
clients, and none of the accountant's eight agents.

Settings (ExtensionRegistry keeps them in extensions.json):
    folder          the business folder (default <workspace>/Freelance)
    name, preset, currency, rate, terms_days, set_aside_pct, email
                    used when the folder is first set up
    weekly_admin    run the weekly admin every Friday at 16:00
"""
from __future__ import annotations

import os
from typing import Any, Optional

from agents.core import Task, TaskKind
from agents.extensions import DashboardModule, ExtensionManifest, IAgentExtension, IntentRule, PermissionProfile
from extensions.accounting.permissions import LEVEL_RISK, ActionLevel

from . import agents_def
from .business import Business, BusinessCache
from .tools import REGISTRY, build_tools

HERE = os.path.dirname(os.path.abspath(__file__))
EXT = agents_def.EXT

_VERB = r"\b(?:please\s+)?(?:do|run|start|prepare|make|build|get|sort\s+out|chase|write|draft|send|put\s+together)\b"


def _rule(obj: str, workflow: str, reply: str, *, verb: str = _VERB) -> IntentRule:
    return IntentRule(pattern=verb + r"[^.?!]{0,60}?" + obj, template_id=agents_def.TEMPLATE.id,
                      workflow_id=workflow, reply=reply, extension_id=EXT)


INTENT_RULES = [
    _rule(r"\b(?:weekly\s+admin|my\s+admin|the\s+admin)\b", "weekly_admin",
          "On it: where things stand, unbilled hours, and reminders drafted for anyone late."),
    _rule(r"\b(?:my\s+late\s+payers|late\s+payers|my\s+clients?\s+who\s+(?:owe|haven't\s+paid))\b", "weekly_admin",
          "On it: a reminder for everyone who is late, waiting in your Outbox for you to send."),
    _rule(r"\byear[- ]?end(?:\s+pack)?\b", "year_end",
          "On it: the year-end pack for your accountant, as files in Year-end."),
    _rule(r"\b\d+(?:\.\d+)?\s*(?:hours?|hrs?|h)\b", "", "Logging it.", verb=r"\b(?:please\s+)?(?:log|record|add|track)\b"),
    _rule(r"\b(?:quote|estimate)\s+(?:for|to)\b", "", "I'll write the quote for you to check.",
          verb=r"\b(?:please\s+)?(?:write|draft|make|create|prepare|do)\b"),
    _rule(r"\bfor\s+(?:my|the)\s+(?:hours|time)\b", "", "I'll draft the invoice from your hours.",
          verb=r"\b(?:please\s+)?(?:invoice|bill)\b"),
]


class FreelancerExtension(IAgentExtension):
    manifest = ExtensionManifest.load(HERE)

    def __init__(self) -> None:
        self.businesses = BusinessCache()
        self._settings: dict[str, Any] = {}

    # -- parts -----------------------------------------------------------------------------------------
    def settings_defaults(self) -> dict[str, Any]:
        return {"folder": "", "name": "", "preset": "generic", "currency": "", "rate": 0, "terms_days": 14,
                "set_aside_pct": 25.0, "email": "", "weekly_admin": False}

    def get_agent_templates(self):
        return list(agents_def.TEMPLATES)

    def get_tools(self, ctx):
        self._settings = ctx.settings
        return build_tools(self.businesses)

    def get_workers(self, ctx):
        return [agents_def.FreelanceWorker([d.name for d in REGISTRY])]

    def get_skills(self):
        return list(agents_def.SKILLS)

    def get_workflows(self):
        return list(agents_def.WORKFLOWS)

    def get_permissions(self) -> PermissionProfile:
        return PermissionProfile(
            levels=dict(LEVEL_RISK), tool_levels={d.name: d.level.value for d in REGISTRY},
            sensitive=[d.name for d in REGISTRY if d.level is ActionLevel.SENSITIVE],
            always_ask=[d.name for d in REGISTRY if d.level.always_ask],
            notes="READ never asks; DRAFT runs unasked; POST (issue an invoice, record a payment, post receipts) "
                  "asks every time. Nothing is ever sent: emails wait in Outbox.")

    def get_dashboard_modules(self):
        def factory(manager, extension, parent=None):
            from .dashboard import FreelancerDashboard
            return FreelancerDashboard(manager, extension, parent)
        return [DashboardModule("freelancer.dashboard", "Freelancer", factory, order=20)]

    def get_intent_rules(self):
        return list(INTENT_RULES)

    def get_context_lines(self, task: Task) -> list[str]:
        try:
            b = self.businesses.get(task.workspace)
        except Exception:  # noqa: BLE001 - not the business folder
            return ["Freelancer: this task is not in the business folder."]
        return [f"Business: {b.name} · {b.currency} · rate {b.money(b.rate)}/h · terms {b.terms_days} days · "
                f"set aside {b.set_aside_pct:g}% of every payment"]

    # -- the business folder ------------------------------------------------------------------------------
    def business_root(self, ctx) -> str:
        return (ctx.settings.get("folder") or "").strip() or os.path.join(ctx.workspace_root, "Freelance")

    def ensure_business(self, ctx) -> Business:
        root = self.business_root(ctx)
        if not Business.is_business(root):
            s = ctx.settings
            Business.create(root, s.get("name") or "My business", preset=s.get("preset") or "generic",
                            currency=s.get("currency") or "", rate=s.get("rate") or 0,
                            terms_days=int(s.get("terms_days") or 14),
                            set_aside_pct=float(s.get("set_aside_pct", 25.0)), email=s.get("email") or "")
        return self.businesses.get(root)

    def workspace_for(self, meta: dict[str, Any], ctx) -> str:
        return self.ensure_business(ctx).root

    # -- tasks ----------------------------------------------------------------------------------------------
    def task_for(self, workflow_id: str, root: str, extra: Optional[dict[str, Any]] = None):
        wf = agents_def.WORKFLOW_BY_ID[workflow_id]
        t = agents_def.TEMPLATE
        task = Task(title=wf.name, goal=wf.goal, workspace=root, kind=TaskKind.GENERAL, worker_type=t.worker_type,
                    metadata={"extension": EXT, "template": t.id, "workflow": wf.id, **(extra or {})})
        return task, t.spec(wf.goal, workspace=root, name=t.name)

    def scheduled_jobs(self, ctx) -> list[Any]:
        if not ctx.settings.get("weekly_admin"):
            return []
        from agents.manager.scheduler import ScheduledJob
        root = self.ensure_business(ctx).root
        wf = agents_def.WORKFLOW_BY_ID["weekly_admin"]
        return [ScheduledJob(id="freelancer:weekly_admin", name="Weekly admin", trigger=wf.trigger,
                             make_task=lambda extra: self.task_for("weekly_admin", root, extra), owner=EXT)]

    # -- lifecycle ----------------------------------------------------------------------------------------------
    def on_enable(self, ctx) -> None:
        self._settings = ctx.settings

    def on_configure(self, ctx, settings: dict[str, Any]) -> None:
        self._settings = settings

    def on_disable(self, ctx) -> None:
        self.businesses.drop()
