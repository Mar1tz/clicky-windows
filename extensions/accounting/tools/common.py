"""The shared machinery of every accounting tool.

A tool is declared with `@accounting_tool(name, description, level, props)`
over an `async def fn(s, args, ctx) -> Out`. The decorator turns it into
a registry Tool whose risk, always_ask and sensitive flags come from the
ACTION LEVEL - so the permission a tool needs is a fact about the tool,
visible in one line, and not something a prompt can talk around.

`Out` carries the text the model reads, data for the verifier and UI,
and two things the platform lifts onto the task: `findings` (what a
person must look at - also persisted as the client's open exceptions)
and `checks` (how many checks ran, for "27 checks completed").
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from agents.core import ToolContext, ToolResult
from agents.tools.base import Tool, make_spec

from ..books import LedgerError, fmt, parse_date, to_major, to_minor
from ..permissions import CONFIRM_WORDS, ActionLevel, ConfidencePolicy
from ..providers import ProviderUnsupported
from ..session import ClientSession, NotAClient, SessionCache
from ..tables import TableError

CATEGORY = "accounting"

DATE = {"type": "string", "description": "Date, YYYY-MM-DD (DD/MM/YYYY also accepted)."}
PERIOD = {"type": "string", "description": "Accounting period YYYY-MM. Default: the client's current period."}
AMOUNT = {"type": "number", "description": "Amount in the client's currency."}
CONFIDENCE = {"type": "number", "description": "Your confidence 0-1 that this is right. Drives automation: "
                                                "high drafts, medium drafts + flags, low refuses and asks."}
PATH = {"type": "string", "description": "File path, relative to the client folder or absolute inside it."}


@dataclass
class Out:
    text: str
    data: dict[str, Any] = field(default_factory=dict)
    findings: list[dict[str, Any]] = field(default_factory=list)
    checks: int = 0
    ok: bool = True
    error: str = ""


def F(title: str, detail: str = "", *, severity: str = "review", entity: str = "",
      action: str = "") -> dict[str, Any]:
    """A finding: something the accountant must look at."""
    return {"title": title, "detail": detail, "severity": severity, "entity": entity, "action": action}


Fn = Callable[[ClientSession, dict[str, Any], ToolContext], Awaitable[Out]]


@dataclass
class ToolDef:
    name: str
    description: str
    level: ActionLevel
    properties: dict[str, dict[str, Any]]
    required: list[str]
    fn: Fn


REGISTRY: list[ToolDef] = []


def accounting_tool(name: str, description: str, level: ActionLevel,
                    properties: Optional[dict[str, dict[str, Any]]] = None,
                    required: Optional[list[str]] = None):
    def wrap(fn: Fn) -> Fn:
        REGISTRY.append(ToolDef(name, description, level, dict(properties or {}), list(required or []), fn))
        return fn
    return wrap


class AccountingTool(Tool):
    """A ToolDef bound to the pack's session cache and settings."""

    def __init__(self, d: ToolDef, sessions: SessionCache,
                 settings: Callable[[], dict[str, Any]]) -> None:
        self.d = d
        self.sessions = sessions
        self.settings = settings
        self.level = d.level
        prefix = {ActionLevel.READ: "[READ] ", ActionLevel.DRAFT: "[DRAFT] ",
                  ActionLevel.POST: "[POST - needs approval] ",
                  ActionLevel.SENSITIVE: "[SENSITIVE - accountant must confirm] "}[d.level]
        self.spec = make_spec(d.name, prefix + d.description, risk=d.level.risk, category=CATEGORY,
                              properties=d.properties, required=d.required)
        self.always_ask = d.level.always_ask
        self.sensitive = d.level.sensitive
        # A READ or DRAFT tool failing is information (bad input the agent
        # corrects, a check the books cannot answer); POST and SENSITIVE
        # failures always count against the step.
        self.soft_fail = not d.level.always_ask
        self.confirm_word = CONFIRM_WORDS.get(d.name, "CONFIRM") if d.level.sensitive else ""
        self.ask_reason = {
            ActionLevel.POST: f"{d.name} changes the books - an accountant must approve it",
            ActionLevel.SENSITIVE: f"{d.name} moves money or files with an authority - type "
                                   f"{self.confirm_word} to confirm",
        }.get(d.level, "")

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            s = self.sessions.get(ctx.workspace)
        except NotAClient as exc:
            return self.fail(str(exc))
        except (ProviderUnsupported, LedgerError, ValueError) as exc:
            return self.fail(f"could not open the client's books: {exc}")
        try:
            out = await self.d.fn(s, args, ctx)
        except (LedgerError, ProviderUnsupported, TableError, KeyError, ValueError, OSError) as exc:
            msg = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
            return self.fail(str(msg))
        findings = []
        for f in out.findings:
            f = {**f, "source": f.get("source") or self.name}
            try:
                row, _new = s.drafts.add_finding(f, task_id=ctx.task_id or "")
                f["id"] = row["id"]
            except Exception:  # noqa: BLE001 - persisting an exception must never fail the check
                pass
            findings.append(f)
        data = {**out.data, "findings": findings, "checks": out.checks}
        text = out.text
        if findings:
            text += "\n\nNeeds review:\n" + "\n".join(f"  - {f['title']}" for f in findings[:25])
        if not out.ok:
            return self.fail(out.error or text, output=text, **data)
        return self.ok(text, **data)


def build_tools(sessions: SessionCache, settings: Callable[[], dict[str, Any]]) -> list[AccountingTool]:
    from . import bank, checks, documents, invoices, ledger, posting, reports  # noqa: F401 - registers
    seen, out = set(), []
    for d in REGISTRY:
        if d.name in seen:
            continue
        seen.add(d.name)
        out.append(AccountingTool(d, sessions, settings))
    return out


# -- helpers every tool uses ---------------------------------------------------------------

def money(s: ClientSession, amount: float) -> str:
    return s.ws.profile().money(amount)


def period_range(s: ClientSession, args: dict[str, Any]) -> tuple[str, str]:
    if args.get("start") or args.get("end"):
        start = parse_date(args["start"]) if args.get("start") else s.ws.period_start
        end = parse_date(args["end"]) if args.get("end") else s.ws.period_end
        return start, end
    return s.ws.period_bounds(args.get("period") or "")


def as_of(s: ClientSession, args: dict[str, Any]) -> str:
    return parse_date(args["as_of"]) if args.get("as_of") else s.ws.period_end


def policy(s: ClientSession, tool: Optional[AccountingTool] = None) -> ConfidencePolicy:
    defaults = {}
    try:
        from .. import extension as ext_mod
        defaults = ext_mod.current_settings()
    except Exception:  # noqa: BLE001
        pass
    return s.ws.confidence_policy(defaults)


def rel(s: ClientSession, path: str) -> str:
    try:
        return os.path.relpath(path, s.ws.root)
    except ValueError:
        return path


def resolve_in_client(s: ClientSession, path: str) -> str:
    p = path if os.path.isabs(path) else os.path.join(s.ws.root, path)
    p = os.path.normpath(os.path.abspath(p))
    root = os.path.normpath(os.path.abspath(s.ws.root))
    try:
        inside = os.path.commonpath([root, p]) == root
    except ValueError:
        inside = False
    if not inside:
        raise ValueError(f"{path} is outside the client folder")
    return p


def lines_table(rows: list[dict[str, Any]], cols: list[str], limit: int = 40) -> str:
    if not rows:
        return "(none)"
    out = [" | ".join(cols)]
    for r in rows[:limit]:
        out.append(" | ".join(str(r.get(c, "")) for c in cols))
    if len(rows) > limit:
        out.append(f"... and {len(rows) - limit} more")
    return "\n".join(out)


def pct_change(new: float, old: float) -> Optional[float]:
    if not old:
        return None
    return round((new - old) / abs(old) * 100, 1)


def minor(v: Any) -> int:
    return to_minor(v) if v not in (None, "") else 0


__all__ = ["Out", "F", "accounting_tool", "AccountingTool", "build_tools", "money", "period_range", "as_of",
           "policy", "rel", "resolve_in_client", "lines_table", "pct_change", "minor", "fmt", "to_major",
           "to_minor", "parse_date", "DATE", "PERIOD", "AMOUNT", "CONFIDENCE", "PATH", "ActionLevel", "REGISTRY"]
