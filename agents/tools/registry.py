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

# A yes for a screen or money action is good for this long after the ask.
APPROVAL_FRESH_S = 600.0
RETRY_PAUSE_S = 0.5

# EN-02 / OR-14: every failure carries one of these in data["error_class"],
# so a worker, a recovery pass or a report can decide without reading prose.
ERROR_CLASSES = ("unknown_tool", "invalid_input", "permission", "timeout", "crashed", "failed")


SCOPED_CATEGORIES = ("file", "spreadsheet", "design", "terminal", "git")
PATH_ARGS = ("path", "paths", "source", "destination", "output", "other_path", "cwd")


def _outside_workspace(spec, args: dict, ctx) -> str:
    """The first path argument of a folder-bound tool that leaves the job's
    folders, or ""."""
    if spec.category not in SCOPED_CATEGORIES:
        return ""
    for key in PATH_ARGS:
        value = args.get(key)
        for path in (value if isinstance(value, list) else [value]):
            if isinstance(path, str) and path and not ctx.is_inside_workspace(path):
                return path
    return ""


def error_class(result) -> str:
    """The stable class of a failed result (its own, else from the registry's words)."""
    given = (result.data or {}).get("error_class")
    if given:
        return given
    error = (result.error or "").lower()
    for prefix, kind in (("unknown tool", "unknown_tool"), ("invalid arguments", "invalid_input"),
                         ("permission denied", "permission"), ("timed out", "timeout")):
        if error.startswith(prefix):
            return kind
    return "failed"


def payload_hash(call) -> str:
    """The exact action an approval covers: the tool and its arguments."""
    import hashlib
    body = json.dumps({"tool": call.tool_name, "arguments": call.arguments or {}}, sort_keys=True, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()

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

    def unregister(self, name: str) -> bool:
        """Remove a tool, so nothing is left behind for a model to call."""
        return self._tools.pop(name, None) is not None

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
        tool = self._tools.get(call.tool_name)
        # OR-14 / OR-33: a passing failure (a timeout, a rate limit, a dropped
        # line) of a tool that may run again is tried again here, backing off;
        # a call that could change something is never repeated blindly.
        policy = tool.spec.retry_policy() if tool is not None else None
        tries = 1
        while (not result.ok and policy is not None and tries < policy.attempts
               and policy.retries(error_class(result), result.error or "")):
            await asyncio.sleep(RETRY_PAUSE_S * (2 ** (tries - 1)) * (policy.backoff_s / 0.5))
            if ctx.control is not None:
                await ctx.control.checkpoint()
            tries += 1
            result = await self._execute(call, ctx)
            result.data["retried"] = True
            result.data["attempts"] = tries
        if not result.ok:
            result.data.setdefault("error_class", error_class(result))
        if tool is not None:
            from . import plugins
            result = plugins.after(call, result, ctx)
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

        # EN-11: a path outside the job's folders is refused before anyone is
        # asked - the person's yes must never be all that stands between the
        # model and another project (the EN-01 scenario set found it asked).
        outside = _outside_workspace(spec, args, ctx)
        if outside:
            refused = ToolResult.failure(call, f"outside workspace: {outside}")
            refused.data["error_class"] = "permission"
            return refused

        # EN-32: plugins may refuse a call outright (never let one skip its
        # approval), and a tool may say this one call is dangerous - then the
        # person is asked whatever the policy, grant or take-over says.
        from . import plugins
        veto = plugins.before(call, ctx)
        if veto:
            refused = ToolResult.failure(call, f"blocked: {veto}")
            refused.data["error_class"] = "blocked"
            return refused
        danger = ""
        rate = getattr(tool, "danger_for_call", None)
        if callable(rate):
            try:
                danger = str(rate(call, ctx) or "")
            except Exception:  # noqa: BLE001 - a check that breaks asks, it never waves through
                danger = "could not be checked"

        # `always_ask` is a hard floor a tool can declare for itself: no
        # policy, ceiling or standing approval ever lets it run unasked.
        # Computer control uses it - moving the user's own mouse is not a
        # risk you get to pre-approve.
        always_ask = bool(getattr(tool, "always_ask", False))
        sensitive = bool(getattr(tool, "sensitive", False))
        # A tool may decide per call: reading what is playing does not need a
        # human, skipping a track does. Only ever used to ask MORE precisely -
        # a tool that says "no" for one call still cannot exceed the policy.
        asks = getattr(tool, "asks_for_call", None)
        if callable(asks) and always_ask:
            try:
                always_ask = bool(asks(call))
            except Exception:  # noqa: BLE001 - a broken hook asks, it never waves through
                always_ask = True
        grant_key = ""
        key_of = getattr(tool, "grant_key", None)
        if callable(key_of):
            try:
                grant_key = str(key_of(call) or "")
            except Exception:  # noqa: BLE001
                grant_key = ""
        granted = bool(grant_key) and grant_key in ctx.policy.always_allow
        if always_ask and not granted and getattr(ctx.policy, "control_until", 0.0) > time.time():
            # The person handed this job the controls ("take over"): no ask
            # per click while it lasts. A SENSITIVE tool still asks below.
            from agents.tools.computer_tools import kill_switch
            granted = not kill_switch.tripped
        if danger or (always_ask and not granted) or sensitive or not (ctx.policy.allows(spec) or granted):
            default_reason = f"{spec.name} always needs a human: it controls your mouse and keyboard"
            per_call = getattr(tool, "ask_reason_for", None)
            reason = ""
            if callable(per_call):
                try:
                    reason = str(per_call(call) or "")
                except Exception:  # noqa: BLE001
                    reason = ""
            reason = reason or ((getattr(tool, "ask_reason", "") or default_reason)
                                if (always_ask or sensitive) else _deny_reason(spec, ctx.policy))
            if danger:
                reason = f"Risky - this {danger}. {reason}"
            request = ApprovalRequest(task_id=ctx.task_id or "", agent_id=ctx.agent_id or "",
                                      call=call, reason=reason, sensitive=sensitive, grant_key=grant_key,
                                      confirm_word=(getattr(tool, "confirm_word", "") or "CONFIRM")
                                      if sensitive else "", payload_hash=payload_hash(call),
                                      dangerous=danger)
            try:
                approved = bool(await ctx.approver(request))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a broken approver means "no"
                log.exception("approver raised for %s", call.tool_name)
                approved = False
                reason = f"{reason} (approver error: {exc})"
            request.approved = approved
            request.decided_at = time.time()
            if not approved:
                return ToolResult.failure(call, f"permission denied: {reason}")
            # EN-08: a yes is for exactly what was shown, and only while it is
            # fresh. Changed since, or a screen or money action approved long
            # after it was asked: asked again, never run on the old yes.
            if payload_hash(call) != request.payload_hash:
                return ToolResult.failure(call, "permission denied: the action changed after it was approved - "
                                                "ask again with what will actually run")
            waited = request.decided_at - request.created_at
            if (always_ask or sensitive) and waited > APPROVAL_FRESH_S:
                return ToolResult.failure(call, f"permission denied: the approval came {waited / 60:.0f} min after "
                                                "it was asked and the screen may have changed - look again, then ask")

        if ctx.dry_run and spec.risk is not RiskLevel.SAFE:
            return ToolResult.success(call, "dry run", dry_run=True, arguments=args)

        started = time.perf_counter()
        try:
            if spec.timeout_s:
                result = await asyncio.wait_for(tool.run(args, ctx), spec.timeout_s)
            else:
                result = await tool.run(args, ctx)
        except asyncio.TimeoutError:
            result = ToolResult.failure(call, f"timed out after {spec.timeout_s:g}s")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - contain programmer errors too
            log.exception("tool %s raised", call.tool_name)
            result = ToolResult.failure(call, f"{type(exc).__name__}: {exc}")
            result.data["error_class"] = "crashed"
        if not isinstance(result, ToolResult):
            result = ToolResult.failure(
                call, f"tool returned {type(result).__name__}, expected ToolResult")
        result.duration_ms = int((time.perf_counter() - started) * 1000)
        cap = spec.max_output_chars
        if cap and isinstance(result.output, str) and len(result.output) > cap:
            # EN-03: a runaway result (a 10 MB log, a whole site) cannot sink
            # the model's context or the app: cut, and said to be cut.
            extra = len(result.output) - cap
            result.output = result.output[:cap] + f"\n[... {extra} more characters cut - ask for a narrower part]"
            result.data["truncated"] = True
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
