"""Registry: the one door between a worker and the outside world.

Workers never call tool.run() themselves. Registry.execute() is the
single choke point, so pause/cancel, argument validation, the permission
policy, dry runs, exception containment, timing and the TOOL_CALL /
TOOL_RESULT events are enforced once and cannot be bypassed by a
forgetful worker. The order inside execute():

    checkpoint -> TOOL_CALL event -> validate -> policy / approver
    -> dry run -> tool.run() -> stamp ids + duration -> TOOL_RESULT event

Everything that can go wrong for expected reasons (unknown tool, bad
arguments, denial, tool raised) becomes a failed ToolResult - the LLM
reads it and reacts. Only asyncio.CancelledError propagates.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Iterable, Optional

from agents.core import (
    ApprovalRequest, BaseTool, EventKind, PermissionPolicy, RiskLevel, ToolCall,
    ToolContext, ToolRegistry, ToolResult, ToolSpec,
)

from .schema import validate

log = logging.getLogger("agents.tools")

# Arguments larger than this are replaced in EVENTS (never in the call
# itself) by their size. A create_file with a 40 KB payload is one tool
# call; it must not become 40 KB in the event log, the SQLite history,
# and every timeline that replays it.
EVENT_ARG_LIMIT = 2000


def _event_view_result(result) -> dict:
    """Same idea for results: a screenshot's base64 is ~100 KB and belongs
    to the worker, not to every timeline that replays the event."""
    view = result.to_dict()
    data = dict(view.get("data") or {})
    for k, v in list(data.items()):
        if isinstance(v, str) and len(v) > EVENT_ARG_LIMIT:
            data[k] = f"<{len(v)} chars>"
    view["data"] = data
    if isinstance(view.get("output"), str) and len(view["output"]) > EVENT_ARG_LIMIT * 4:
        view["output"] = view["output"][:EVENT_ARG_LIMIT * 4] + "…"
    return view


def _event_view(call) -> dict:
    view = call.to_dict()
    args = view.get("arguments") or {}
    view["arguments"] = {
        k: (f"<{len(v)} chars>" if isinstance(v, str) and len(v) > EVENT_ARG_LIMIT else v)
        for k, v in args.items()
    }
    return view

_PREVIEW_CHARS = 160


class Registry(ToolRegistry):
    def __init__(self, tools: Optional[Iterable[BaseTool]] = None) -> None:
        self._tools: dict[str, BaseTool] = {}
        for tool in tools or ():
            self.register(tool)

    # -- catalogue -------------------------------------------------------------

    def register(self, tool: BaseTool) -> None:
        """Register (or replace - last one wins, so tests can stub) a tool."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool:
        try:
            return self._tools[name]
        except KeyError:
            raise KeyError(f"unknown tool: {name!r}") from None

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self, names: Optional[list[str]] = None) -> list[ToolSpec]:
        """Specs for `names` (in that order, unknown names skipped) or all."""
        if names is None:
            return [t.spec for t in self._tools.values()]
        return [self._tools[n].spec for n in names if n in self._tools]

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    # -- execution -------------------------------------------------------------

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolResult:
        if ctx.control is not None:
            await ctx.control.checkpoint()

        self._emit(ctx, EventKind.TOOL_CALL, _describe(call), {"call": _event_view(call)})

        result = await self._execute(call, ctx)
        # Tools build results without knowing the call (see base.Tool);
        # stamp identity here so nothing downstream has to trust them.
        result.call_id = call.id
        result.tool_name = call.tool_name

        status = "ok" if result.ok else f"failed: {result.error}"
        self._emit(ctx, EventKind.TOOL_RESULT, f"{call.tool_name} {status}",
                   {"result": _event_view_result(result)}, level="info" if result.ok else "warn")
        return result

    async def _execute(self, call: ToolCall, ctx: ToolContext) -> ToolResult:
        tool = self._tools.get(call.tool_name)
        if tool is None:
            return ToolResult.failure(
                call, f"unknown tool: {call.tool_name!r}; available: {', '.join(self.names())}")
        spec = tool.spec
        args = dict(call.arguments or {})

        problems = validate(spec.parameters, args)
        if problems:
            return ToolResult.failure(call, "invalid arguments: " + "; ".join(problems))

        # `always_ask` is a hard floor a tool can declare for itself: no
        # policy, ceiling or standing approval ever lets it run unasked.
        # Computer control uses it - moving the user's own mouse is not a
        # risk you get to pre-approve.
        always_ask = bool(getattr(tool, "always_ask", False))
        if always_ask or not ctx.policy.allows(spec):
            reason = (f"{spec.name} always needs a human: it controls your mouse and keyboard"
                      if always_ask else _deny_reason(spec, ctx.policy))
            request = ApprovalRequest(task_id=ctx.task_id or "", agent_id=ctx.agent_id or "",
                                      call=call, reason=reason)
            try:
                approved = bool(await ctx.approver(request))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a broken approver means "no"
                log.exception("approver raised for %s", call.tool_name)
                approved = False
                reason = f"{reason} (approver error: {exc})"
            request.approved = approved
            if not approved:
                return ToolResult.failure(call, f"permission denied: {reason}")

        if ctx.dry_run and spec.risk is not RiskLevel.SAFE:
            return ToolResult.success(call, "dry run", dry_run=True, arguments=args)

        started = time.perf_counter()
        try:
            result = await tool.run(args, ctx)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - contain programmer errors too
            log.exception("tool %s raised", call.tool_name)
            result = ToolResult.failure(call, f"{type(exc).__name__}: {exc}")
        if not isinstance(result, ToolResult):
            result = ToolResult.failure(
                call, f"tool returned {type(result).__name__}, expected ToolResult")
        result.duration_ms = int((time.perf_counter() - started) * 1000)
        return result

    # -- events ----------------------------------------------------------------

    @staticmethod
    def _emit(ctx: ToolContext, kind: EventKind, message: str,
              data: dict[str, Any], level: str = "info") -> None:
        if ctx.events is None:
            return
        ctx.events.emit(kind, message, task_id=ctx.task_id, agent_id=ctx.agent_id,
                        data=data, level=level)


def _deny_reason(spec: ToolSpec, policy: PermissionPolicy) -> str:
    if spec.name in policy.denied_tools:
        return f"{spec.name} is in denied_tools"
    if policy.allowed_tools is not None and spec.name not in policy.allowed_tools:
        return f"{spec.name} is not in allowed_tools"
    return (f"{spec.name} is {spec.risk.value} risk; policy auto-approves up to "
            f"{policy.max_auto_risk.value}")


def _describe(call: ToolCall) -> str:
    try:
        preview = json.dumps(call.arguments, default=str, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        preview = repr(call.arguments)
    if len(preview) > _PREVIEW_CHARS:
        preview = preview[:_PREVIEW_CHARS] + "…"
    return f"{call.tool_name} {preview}"
