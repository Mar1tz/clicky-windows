"""Planners: TemplatePlanner (deterministic) and ProviderPlanner (LLM).

TemplatePlanner is the Phase 1 default. It never touches the provider:
a recognised goal becomes a fully scripted Plan, anything else becomes a
single "Work on: <goal>" step with empty tool_calls, which tells the
worker to run its ReAct loop.

ProviderPlanner asks the provider for a JSON outline and validates it
hard - a plan is only accepted if it parses, has 1..max_steps steps and
every step has a non-empty title. Any other outcome (network error,
prose instead of JSON, empty list) silently falls back to the template
planner so a flaky model can never leave a task without a plan.

replan() is shared and deliberately conservative: the failed step is
marked SKIPPED and a "Fix: <failure>" step (no tool_calls - the LLM
worker handles it) is inserted right after it. Nothing else moves, so
the UI keeps its numbering and the runtime's step index stays valid.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from agents.core import (
    AgentContext, Plan, Planner, ProviderMessage, Step, StepStatus, ToolCall,
)

from .templates import (
    DEFAULT_TEMPLATES, ProjectTemplate, TemplateSpec, match_template,
)

log = logging.getLogger("agents.planning")


# -- shared helpers ----------------------------------------------------------

def fallback_plan(ctx: AgentContext, rationale: str = "") -> Plan:
    """One LLM-driven step for goals no template understands."""
    goal = (ctx.task.goal or ctx.task.title or "").strip()
    step = Step(title=f"Work on: {goal}"[:120],
                description=goal or "Complete the task described by the user.")
    return Plan(task_id=ctx.task.id, steps=[step],
                rationale=rationale or "No template matched; the worker decides the actions.")


def insert_fix_step(plan: Plan, failure: str) -> Plan:
    """Mark the failed step SKIPPED and insert a Fix step right after it.
    Mutates and returns `plan` (task.steps usually aliases plan.steps)."""
    failed = _find_failed(plan.steps)
    if failed is None:
        # Nothing marked failed: put the fix before the first pending step.
        idx = next((i for i, s in enumerate(plan.steps) if s.status is StepStatus.PENDING),
                   len(plan.steps))
        plan.steps.insert(idx, _fix_step(None, failure))
        return plan
    idx = plan.steps.index(failed)
    failed.status = StepStatus.SKIPPED
    plan.steps.insert(idx + 1, _fix_step(failed, failure))
    return plan


def _find_failed(steps: list[Step]) -> Optional[Step]:
    for status in (StepStatus.FAILED, StepStatus.RUNNING):
        for s in steps:
            if s.status is status:
                return s
    return None


def _fix_step(failed: Optional[Step], failure: str) -> Step:
    failure = (failure or "unknown error").strip()
    title = f"Fix: {failure}"[:120]
    if failed is None:
        return Step(title=title, description=f"A previous step failed: {failure}. Diagnose and fix it, then continue.")
    attempted = "; ".join(_describe_call(c) for c in failed.tool_calls) or "(no scripted tool calls)"
    desc = (f"Step '{failed.title}' failed: {failure}. It attempted: {attempted}. "
            "Diagnose the cause, achieve the same outcome another way, then continue.")
    return Step(title=title, description=desc)


def _describe_call(call: ToolCall) -> str:
    args = call.arguments or {}
    key = next((k for k in ("path", "command", "packages", "cwd") if k in args), None)
    return f"{call.tool_name}({key}={args[key]!r})" if key else call.tool_name


# -- TemplatePlanner ---------------------------------------------------------

class TemplatePlanner(Planner):
    def __init__(self, templates: Optional[list[ProjectTemplate]] = None) -> None:
        self.templates = list(templates) if templates is not None else list(DEFAULT_TEMPLATES)

    def match(self, ctx: AgentContext) -> Optional[ProjectTemplate]:
        return match_template(TemplateSpec.from_context(ctx), self.templates)

    async def plan(self, ctx: AgentContext) -> Plan:
        spec = TemplateSpec.from_context(ctx)
        template = match_template(spec, self.templates)
        if template is None:
            return fallback_plan(ctx)
        where = f"{spec.root}/" if spec.root else "the workspace"
        return Plan(task_id=ctx.task.id, steps=template.steps(spec),
                    rationale=f"Matched template '{template.name}'; scaffolding into {where}.")

    async def replan(self, ctx: AgentContext, plan: Plan, failure: str) -> Plan:
        return insert_fix_step(plan, failure)


# -- ProviderPlanner ---------------------------------------------------------

_SYSTEM = (
    "You are a planning assistant for an autonomous desktop agent. Break the user's goal "
    "into a short ordered list of concrete steps another agent will execute with tools. "
    "Reply with ONLY a JSON object of the form "
    '{"rationale": "<one sentence>", "steps": [{"title": "<imperative, <= 80 chars>", '
    '"description": "<what to do and how to know it is done>"}]} '
    "with between 1 and {max_steps} steps. No markdown, no commentary."
)

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


class ProviderPlanner(Planner):
    def __init__(self, fallback: Optional[Planner] = None, *, prefer_templates: bool = False,
                 max_steps: int = 12) -> None:
        self.fallback = fallback or TemplatePlanner()
        self.prefer_templates = prefer_templates
        self.max_steps = max_steps

    async def plan(self, ctx: AgentContext) -> Plan:
        if self.prefer_templates and isinstance(self.fallback, TemplatePlanner) and self.fallback.match(ctx):
            return await self.fallback.plan(ctx)
        try:
            resp = await ctx.provider.complete(
                [ProviderMessage(role="user", content=self._prompt(ctx))],
                tools=None, system=_SYSTEM.replace("{max_steps}", str(self.max_steps)),
                temperature=0.1, max_tokens=1500,
            )
            data = parse_json_object(resp.text)
            steps = self._validate(data)
            rationale = str(data.get("rationale") or "").strip() or "Plan proposed by the provider."
            return Plan(task_id=ctx.task.id, steps=steps, rationale=rationale)
        except Exception as exc:  # noqa: BLE001 - any failure means "use the template"
            log.warning("ProviderPlanner fell back to %s: %s", type(self.fallback).__name__, exc)
            return await self.fallback.plan(ctx)

    async def replan(self, ctx: AgentContext, plan: Plan, failure: str) -> Plan:
        return insert_fix_step(plan, failure)

    def _prompt(self, ctx: AgentContext) -> str:
        parts = [f"Goal: {ctx.task.goal}", f"Workspace: {ctx.workspace}"]
        sub = (ctx.task.metadata or {}).get("subdir")
        if sub:
            parts.append(f"Create new files under the sub-folder: {sub}")
        try:
            names = ctx.tools.names()
        except Exception:  # noqa: BLE001 - a stub registry in tests may not implement it
            names = []
        if names:
            parts.append("Available tools: " + ", ".join(sorted(names)))
        if ctx.memory is not None:
            try:
                summary = ctx.memory.project_summary(ctx.workspace, max_chars=800)
            except Exception:  # noqa: BLE001
                summary = ""
            if summary:
                parts.append("Known about this project:\n" + summary)
        return "\n".join(parts)

    def _validate(self, data: Any) -> list[Step]:
        if not isinstance(data, dict):
            raise ValueError("plan is not a JSON object")
        raw = data.get("steps")
        if not isinstance(raw, list) or not raw:
            raise ValueError("plan has no steps")
        if len(raw) > self.max_steps:
            raise ValueError(f"plan has {len(raw)} steps (max {self.max_steps})")
        steps: list[Step] = []
        for i, item in enumerate(raw):
            if isinstance(item, str):
                item = {"title": item}
            if not isinstance(item, dict):
                raise ValueError(f"step {i} is not an object")
            title = str(item.get("title") or "").strip()
            if not title:
                raise ValueError(f"step {i} has no title")
            steps.append(Step(title=title[:120], description=str(item.get("description") or "").strip()))
        return steps


def parse_json_object(text: str) -> Any:
    """Tolerate fenced or prose-wrapped JSON: try the whole text, then a
    ```json``` block, then the outermost {...}."""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty response")
    candidates = [text]
    m = _FENCE.search(text)
    if m:
        candidates.append(m.group(1).strip())
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    last: Exception = ValueError("no JSON object found")
    for c in candidates:
        try:
            return json.loads(c)
        except json.JSONDecodeError as exc:
            last = exc
    raise ValueError(f"could not parse JSON plan: {last}")
