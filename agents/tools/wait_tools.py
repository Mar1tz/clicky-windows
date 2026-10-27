"""Waiting inside a job: until a time, for a file, for the person's answer (OR-32).

Each tool makes a waitpoint (agents/manager/waitpoints.py) and waits on
it; the job shows what it is waiting for, lets go of the screen and the
browser while it waits for a time or a file, and stops waiting when it is
cancelled. All three are SAFE and replay-safe: a wait run again after a
restart lands on the same deadline, and a question asked again is only
asked again.

A job gets them when its goal needs them (`for_goal`): "at 9", "in two
hours", "when the export appears". ask_user goes with every job, for the
one thing it cannot go on without.
"""
from __future__ import annotations

import datetime as dt
import re
import time
from typing import Any, Optional

from agents.core import RiskLevel, ToolContext, ToolResult, WaitKind, WaitStatus

from .base import Tool, make_spec

CATEGORY = "wait"
ANSWER_WAIT_MIN = 30
FILE_WAIT_MIN = 60

_TIME = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*$", re.I)


def when(text: str, now: Optional[dt.datetime] = None) -> Optional[dt.datetime]:
    """'09:00', '9pm', '21:30' -> the next time it comes; an ISO date-time as it is."""
    now = now or dt.datetime.now()
    text = (text or "").strip()
    try:
        at = dt.datetime.fromisoformat(text)
        return at if at.tzinfo is None else at.astimezone().replace(tzinfo=None)
    except ValueError:
        pass
    m = _TIME.match(text)
    if not m:
        return None
    hour, minute, half = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if half == "pm" and hour < 12:
        hour += 12
    if half == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return at if at > now else at + dt.timedelta(days=1)


def _hub(ctx: ToolContext):
    return (ctx.extras or {}).get("waitpoints")


class WaitUntil(Tool):
    spec = make_spec(
        "wait_until",
        "Wait before the next step: until a time of day ('09:00', '9pm') or for some minutes. "
        "The job lets go of the screen while it waits.",
        risk=RiskLevel.SAFE, category=CATEGORY, replay_safe=True,
        properties={"time": {"type": "string", "description": "When: '09:00', '9pm' or '2026-10-28T09:00'."},
                    "minutes": {"type": "number", "description": "Or how many minutes from now."}},
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        hub = _hub(ctx)
        if hub is None:
            return self.fail("waiting is not available here")
        if args.get("time"):
            at = when(str(args["time"]))
            if at is None:
                return self.fail(f"not a time: {args['time']!r} - use '09:00', '9pm' or an ISO date-time")
        elif args.get("minutes"):
            at = dt.datetime.now() + dt.timedelta(minutes=float(args["minutes"]))
        else:
            return self.fail("give a time or minutes")
        label = at.strftime("%H:%M") if at.date() == dt.date.today() else at.strftime("%a %d %b %H:%M")
        wp = hub.create(WaitKind.TIME, ctx.task_id or "", prompt=f"until {label}", until=at.timestamp(),
                        idempotency_key=f"{ctx.task_id}:time:{int(at.timestamp())}")
        await hub.wait(wp, ctx.task_id or "", ctx.control)
        return self.ok(f"waited until {label}", waitpoint=wp.id)


class WaitForFile(Tool):
    spec = make_spec(
        "wait_for_file",
        "Wait until a file exists (a download, an export, a build output) before the next step.",
        risk=RiskLevel.SAFE, category=CATEGORY, replay_safe=True,
        properties={"path": {"type": "string"},
                    "timeout_minutes": {"type": "number", "description": f"Give up after this long (default {FILE_WAIT_MIN})."}},
        required=["path"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        hub = _hub(ctx)
        if hub is None:
            return self.fail("waiting is not available here")
        path = ctx.resolve_path(str(args.get("path") or ""))
        minutes = float(args.get("timeout_minutes") or FILE_WAIT_MIN)
        wp = hub.create(WaitKind.EVENT, ctx.task_id or "", prompt=f"for {path}", until=time.time() + minutes * 60,
                        condition={"file": path}, idempotency_key=f"{ctx.task_id}:file:{path}")
        await hub.wait(wp, ctx.task_id or "", ctx.control)
        if wp.status is WaitStatus.COMPLETED:
            return self.ok(f"{path} is there", path=path, waitpoint=wp.id)
        return self.fail(f"{path} did not appear within {minutes:.0f} minutes", waitpoint=wp.id)


class AskUser(Tool):
    spec = make_spec(
        "ask_user",
        "Ask the person one short question and wait for the answer - only when the job cannot go on "
        "without it (which account, which month). Tora says it aloud.",
        risk=RiskLevel.SAFE, category=CATEGORY, replay_safe=True,
        properties={"question": {"type": "string"},
                    "timeout_minutes": {"type": "number", "description": f"Default {ANSWER_WAIT_MIN}."}},
        required=["question"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        hub = _hub(ctx)
        if hub is None:
            return self.fail("there is no one to ask here")
        question = " ".join(str(args.get("question") or "").split())[:300]
        if not question:
            return self.fail("no question given")
        minutes = float(args.get("timeout_minutes") or ANSWER_WAIT_MIN)
        wp = hub.create(WaitKind.ANSWER, ctx.task_id or "", prompt=question, until=time.time() + minutes * 60,
                        idempotency_key=f"{ctx.task_id}:ask:{question.lower()}")
        await hub.wait(wp, ctx.task_id or "", ctx.control)
        if wp.status is WaitStatus.COMPLETED:
            return self.ok(f"They answered: {wp.output}", answer=wp.output, waitpoint=wp.id)
        return self.fail(f"no answer within {minutes:.0f} minutes - go on without it or stop", waitpoint=wp.id)


_LATER = re.compile(r"\b(at \d{1,2}(:\d{2})?\s*(am|pm)?|in \w+ (minutes?|hours?)|tonight|this (evening|afternoon)|"
                    r"tomorrow|wait(ing)? (until|for|till)|later today)\b", re.I)
_FILE_EVENT = re.compile(r"\bwhen (the |my )?[\w .-]+ (appears|arrives|exists|is (saved|ready|downloaded|finished))\b|"
                         r"\bonce (it|the [\w .-]+) (is|has) (downloaded|finished|exported|saved)\b", re.I)


def for_goal(goal: str) -> list[str]:
    """The wait tools a job needs: ask_user always; the others when the goal waits."""
    names = ["ask_user"]
    if _LATER.search(goal or ""):
        names.append("wait_until")
    if _FILE_EVENT.search(goal or ""):
        names.append("wait_for_file")
    return names


def tools() -> list[Tool]:
    return [WaitUntil(), WaitForFile(), AskUser()]
