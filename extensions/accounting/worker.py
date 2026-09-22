"""AccountingWorker: one worker class, eight roles.

The role (template) is on the task's metadata; the worker turns it into
the mission line of the system prompt. The pack's knowledge layer is
appended by the base worker from ctx.extras["skills"], and the client
context line arrives in the opening prompt - so this class only adds
what is specific to running an accounting checklist:

  * a scripted check that fails (the books cannot answer it - a CSV
    provider has no tax summary, a period has no payroll) becomes an
    exception for the accountant and the checklist carries on. A
    month-end that stops at step 6 of 19 because one check had no data
    is worse than one that finishes and says "could not check X".
  * POST and SENSITIVE tools are never soft: if one fails, the step fails.
"""
from __future__ import annotations

from agents.core import AgentContext, Step, StepStatus, ToolResult
from agents.workers.base import Worker, collect_outcomes, describe_call

from .roles import BY_ID


class AccountingWorker(Worker):
    worker_type = "accounting"
    max_iterations = 20

    def __init__(self, tool_names: list[str]) -> None:
        self.default_tools = list(dict.fromkeys([*tool_names, "read_file", "list_dir"]))

    def system_prompt(self, ctx: AgentContext) -> str:
        role = BY_ID.get((ctx.task.metadata or {}).get("template", ""))
        mission = role.mission if role else "Help the accountant with this client's books."
        handles = f" You handle: {', '.join(role.handles)}." if role else ""
        return (f"You are {ctx.agent.name}, part of a digital accounting team working for an accountant. "
                f"Mission: {mission}{handles}\n"
                f"You work only inside the client folder {ctx.workspace}. Use the accounting tools for every "
                "number and every change; they enforce drafts, confidence routing and approvals. When the step "
                "is done, reply with a two-line summary (what was checked; what needs review) and no tool call.")

    async def _run_scripted(self, step: Step, ctx: AgentContext) -> None:
        tctx = ctx.tool_context()
        firsts = []
        for call in list(step.tool_calls):
            await ctx.control.checkpoint()
            ctx.progress(describe_call(call))
            result = await ctx.tools.execute(call, tctx)
            step.results.append(result)
            collect_outcomes(ctx, result)
            if not result.ok:
                try:
                    soft = bool(getattr(ctx.tools.get(call.tool_name), "soft_fail", False))
                except Exception:  # noqa: BLE001 - unknown tool: not soft
                    soft = False
                if not soft:
                    step.status = StepStatus.FAILED
                    step.error = result.error or f"{call.tool_name} failed"
                    return
                note = ToolResult(call_id=call.id, tool_name=call.tool_name, ok=True, data={"findings": [{
                    "title": f"{step.title}: could not be checked",
                    "detail": (result.error or "")[:300], "severity": "review", "entity": step.title,
                    "action": "check this area by hand, or give the agent the data it needs"}]})
                collect_outcomes(ctx, note)
                firsts.append(f"could not check: {(result.error or '')[:80]}")
                continue
            firsts.append((result.output or "").strip().splitlines()[0][:140] if result.output else call.tool_name)
        step.status = StepStatus.DONE
        step.summary = "; ".join(firsts)
