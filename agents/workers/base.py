"""Worker — runs one step of a plan by looping the provider against the tools.

A step reaches a worker in one of two shapes:

  * SCRIPTED — the planner already filled `step.tool_calls` (deterministic
    Phase-1 templates). The worker just runs them in order, no provider
    involved. This is what makes a whole task run with zero network.

  * OPEN — `step.tool_calls` is empty. The worker enters a ReAct loop: ask
    the provider what to do, run the tool calls it returns, feed the results
    back, repeat until the provider answers with prose (the step is done) or
    the iteration budget is spent.

The contract (see BaseWorker) is strict on two points that matter for the
runtime: `execute_step` sets the step's status and never raises except on
cancellation, and it checkpoints before every tool call so pause/cancel are
responsive mid-step.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from agents.core import (
    AgentContext, BaseWorker, ProviderMessage, Step, StepStatus, ToolContext,
    now,
)


class Worker(BaseWorker):
    """Concrete BaseWorker. Subclasses set worker_type / default_tools /
    system_prompt; the execution logic here is shared by all of them."""
    worker_type = "base"
    default_tools: list[str] = []
    max_iterations = 12

    async def execute_step(self, step: Step, ctx: AgentContext) -> Step:
        step.status = StepStatus.RUNNING
        step.started_at = now()
        try:
            if step.tool_calls and not step.results:
                await self._run_scripted(step, ctx)
            else:
                await self._run_react(step, ctx)
        except asyncio.CancelledError:
            # Cancellation is the one thing that propagates: the runtime is
            # tearing the task down and must see it.
            step.status = StepStatus.FAILED
            step.error = "cancelled"
            step.finished_at = now()
            raise
        except Exception as exc:             # noqa: BLE001 - contract: never raise
            step.status = StepStatus.FAILED
            step.error = f"{type(exc).__name__}: {exc}"
        step.finished_at = now()
        return step

    # -- scripted -------------------------------------------------------------

    async def _run_scripted(self, step: Step, ctx: AgentContext) -> None:
        tctx = ctx.tool_context()
        for call in list(step.tool_calls):
            await ctx.control.checkpoint()
            ctx.progress(f"{self.worker_type}: {call.tool_name}")
            result = await ctx.tools.execute(call, tctx)
            step.results.append(result)
            if not result.ok:
                step.status = StepStatus.FAILED
                step.error = result.error or f"{call.tool_name} failed"
                return
        step.status = StepStatus.DONE
        step.summary = self._scripted_summary(step)

    @staticmethod
    def _scripted_summary(step: Step) -> str:
        names = ", ".join(c.tool_name for c in step.tool_calls)
        return f"Ran {len(step.tool_calls)} step action(s): {names}"

    # -- ReAct ----------------------------------------------------------------

    async def _run_react(self, step: Step, ctx: AgentContext) -> None:
        system = self.system_prompt(ctx)
        messages = [ProviderMessage(role="user", content=self._opening_prompt(step, ctx))]
        tool_names = self.allowed_tool_names(ctx)
        specs = ctx.tools.specs(tool_names)
        tctx = ctx.tool_context()

        for _ in range(self.max_iterations):
            await ctx.control.checkpoint()
            resp = await ctx.provider.complete(messages, tools=specs, system=system)

            if not resp.tool_calls:
                step.summary = resp.text
                step.status = StepStatus.DONE
                return

            # The model wants to act. Record the assistant turn once, then run
            # each call and feed its result back as a tool turn.
            messages.append(ProviderMessage(
                role="assistant", content=resp.text, tool_calls=list(resp.tool_calls),
            ))
            for call in resp.tool_calls:
                await ctx.control.checkpoint()
                ctx.progress(f"{self.worker_type}: {call.tool_name}")
                result = await ctx.tools.execute(call, tctx)
                step.tool_calls.append(call)
                step.results.append(result)
                messages.append(ProviderMessage(
                    role="tool", tool_call_id=call.id,
                    content=result.output or result.error or "(no output)",
                ))

        step.status = StepStatus.FAILED
        step.error = f"max iterations ({self.max_iterations}) reached without completing"

    # -- prompt assembly ------------------------------------------------------

    def _opening_prompt(self, step: Step, ctx: AgentContext) -> str:
        parts = [f"Goal: {ctx.task.goal}", ""]
        parts.append(f"Current step: {step.title}")
        if step.description:
            parts.append(step.description)

        done = [s for s in ctx.task.steps if s.status is StepStatus.DONE and s.summary]
        if done:
            parts.append("")
            parts.append("Already done:")
            parts.extend(f"  - {s.title}: {s.summary}" for s in done)

        if ctx.memory is not None:
            try:
                summary = ctx.memory.project_summary(ctx.workspace)
            except Exception:
                summary = ""
            if summary:
                parts.append("")
                parts.append("Project context:")
                parts.append(summary)

        parts.append("")
        parts.append("Use the available tools to complete THIS step. When it is "
                     "done, reply with a short summary and no tool call.")
        return "\n".join(parts)

    # -- tool scoping ---------------------------------------------------------

    def allowed_tool_names(self, ctx: AgentContext) -> list[str]:
        """Which tools this step may use: the agent's explicit set, else this
        worker's defaults, else everything the registry knows."""
        if ctx.agent.tools:
            return list(ctx.agent.tools)
        if self.default_tools:
            return list(self.default_tools)
        return ctx.tools.names()
