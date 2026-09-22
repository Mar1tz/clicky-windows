"""Finance validation rules for the RuleVerifier.

The verifier checks what really happened, not what the agent said: a
draft the tool claims to have made must exist in the client's draft
store with a sane status; a pack must exist on disk and not be empty; a
posted draft must be marked posted with the provider's reference; a
released batch must be released. Checks and reads pass when they ran -
their findings are the output, not a failure.
"""
from __future__ import annotations

import os
from typing import Any, Callable

from agents.core import AgentContext, Check, ToolResult

from .session import SessionCache

Rule = Callable[[dict[str, Any], ToolResult, AgentContext], Check]

DRAFT_TOOLS = ("create_draft_bill", "create_draft_invoice", "create_draft_journal", "create_draft_payment_batch",
               "categorize_transaction", "match_transaction", "draft_payment_reminder", "request_document",
               "prepare_tax_pack", "process_invoice_document")
FILE_TOOLS = ("generate_variance_report", "generate_cashflow_forecast", "generate_workpaper",
              "create_reconciliation_schedule", "prepare_management_pack", "prepare_tax_pack", "prepare_audit_pack",
              "generate_customer_statement", "summarise_exceptions", "draft_payment_reminder", "request_document")


def build_rules(sessions: SessionCache) -> dict[str, Rule]:
    def draft_ok(args: dict[str, Any], res: ToolResult, ctx: AgentContext) -> Check:
        d = (res.data or {}).get("draft")
        if not d:
            return Check("", True, f"no draft needed ({(res.data or {}).get('route', 'n/a')})")
        s = sessions.get(ctx.task.workspace)
        try:
            got = s.drafts.get(d["id"])
        except KeyError:
            return Check("", False, f"draft {d['id']} is not in the draft store")
        return Check("", got["status"] in ("draft", "review", "approved", "posted", "rejected"),
                     f"{got['id']} {got['status']} ({got['confidence']:.0%})")

    def file_ok(args: dict[str, Any], res: ToolResult, ctx: AgentContext) -> Check:
        path = (res.data or {}).get("path")
        if not path:
            return Check("", True, "no file")
        if os.path.isdir(path):
            return Check("", bool(os.listdir(path)), path)
        ok = os.path.isfile(path) and os.path.getsize(path) > 0
        return Check("", ok, path if ok else f"missing or empty: {path}")

    def both(args: dict[str, Any], res: ToolResult, ctx: AgentContext) -> Check:
        a, b = draft_ok(args, res, ctx), file_ok(args, res, ctx)
        return Check("", a.passed and b.passed, f"{a.detail}; {b.detail}")

    def ran(args: dict[str, Any], res: ToolResult, ctx: AgentContext) -> Check:
        n = len((res.data or {}).get("findings") or [])
        return Check("", True, f"ran; {n} exception(s) raised" if n else "ran; nothing to flag")

    def posted(args: dict[str, Any], res: ToolResult, ctx: AgentContext) -> Check:
        s = sessions.get(ctx.task.workspace)
        bad = []
        for p in (res.data or {}).get("posted") or []:
            d = s.drafts.get(p["id"])
            if d["status"] != "posted" or not d["posted_ref"]:
                bad.append(d["id"])
        return Check("", not bad, "all posted with a reference" if not bad else f"not posted: {', '.join(bad)}")

    def released(args: dict[str, Any], res: ToolResult, ctx: AgentContext) -> Check:
        s = sessions.get(ctx.task.workspace)
        d = s.drafts.get(args.get("draft_id", ""))
        return Check("", d["status"] in ("released", "submitted") and bool(d["posted_ref"]),
                     f"{d['id']} {d['status']} {d['posted_ref']}")

    rules: dict[str, Rule] = {}
    from .tools.common import REGISTRY
    for t in REGISTRY:
        rules[t.name] = ran
    for name in FILE_TOOLS:
        rules[name] = file_ok
    for name in DRAFT_TOOLS:
        rules[name] = both if name in FILE_TOOLS else draft_ok
    rules["post_drafts"] = posted
    rules["release_payment_batch"] = released
    rules["submit_tax_return"] = released
    return rules
