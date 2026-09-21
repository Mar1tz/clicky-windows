"""CodingWorker — scaffolds and edits code projects, and makes them pass.

The generalist of the set: it needs the file tools to lay out a project,
the terminal tools to install and run, and git to initialise a repo. Most
"build me an app" tasks land here.

What makes it an engineer rather than a scaffolder is the test-fix loop.
The model writes code, calls `run_tests`, reads a precise failure report
(test name, assertion, file:line), fixes the code, and runs again — the
ordinary ReAct loop already allows that. This class adds the discipline
around it: every red run is an *attempt*, announced in the timeline;
after `max_fix_attempts` red runs in one step the worker stops and
fails the step with the last report, rather than burning the iteration
budget on the same mistake. A green run resets the count, so a step that
fixes one bug and uncovers a second gets a fresh three.
"""
from __future__ import annotations

from typing import Optional

from agents.core import AgentContext, EventKind, Step, ToolCall, ToolResult

from .base import Worker


class CodingWorker(Worker):
    worker_type = "coding"
    default_tools = [
        "create_folder", "create_file", "write_file", "edit_file", "read_file",
        "list_dir", "move_path", "delete_path",
        "run_command", "install_package", "execute_script", "run_tests",
        "git_init", "git_status", "git_diff", "git_log", "git_add",
        "git_commit", "git_branch",
    ]
    max_iterations = 20              # a fix loop needs room: test, read, edit, test…
    max_fix_attempts = 3

    def __init__(self) -> None:
        self._red_runs: dict[str, int] = {}          # step id -> consecutive red runs
        self._last_report: dict[str, str] = {}

    def system_prompt(self, ctx: AgentContext) -> str:
        return (
            f"You are {ctx.agent.name}, an autonomous software engineer working "
            f"inside {ctx.workspace}. Build exactly what the goal asks for and no "
            "more. Prefer small, verifiable steps: create files, then run the "
            "project to check it works. Use real, runnable code — never a "
            "placeholder or a TODO you leave unfilled. Every path you touch stays "
            "inside the workspace. Never claim an action you did not perform with "
            "a tool.\n\n"
            "Tests are how you know you are done. When tests exist, call run_tests. "
            "If it reports failures: read the failing test and the code it "
            "exercises with read_file, fix the CODE with edit_file (change the test "
            "only if the test itself is wrong), then run_tests again. Do not "
            "guess at a fix without reading the failure. You get "
            f"{self.max_fix_attempts} red runs per step before the step is failed.\n\n"
            "When the step is complete, reply with a one-line summary."
        )

    async def on_tool_result(self, step: Step, call: ToolCall, result: ToolResult,
                             ctx: AgentContext) -> Optional[str]:
        if call.tool_name != "run_tests":
            return None
        if result.ok:
            if self._red_runs.get(step.id):
                ctx.log(f"Tests green after {self._red_runs[step.id]} fix attempt(s)")
            self._red_runs.pop(step.id, None)
            self._last_report.pop(step.id, None)
            return None

        n = self._red_runs.get(step.id, 0) + 1
        self._red_runs[step.id] = n
        failed = (result.data or {}).get("failed")
        names = ", ".join(f["name"].rsplit("::", 1)[-1]
                          for f in (result.data or {}).get("failures", [])[:3])
        self._last_report[step.id] = result.output or result.error
        ctx.events.emit(
            EventKind.LOG,
            f"Test attempt {n}/{self.max_fix_attempts}: {failed if failed is not None else 'some'} "
            f"failed" + (f" — {names}" if names else ""),
            task_id=ctx.task.id, agent_id=ctx.agent.id, level="warn",
            data={"attempt": n, "max": self.max_fix_attempts, "failed": failed},
        )
        if n >= self.max_fix_attempts:
            report = self._last_report.pop(step.id, "")
            self._red_runs.pop(step.id, None)
            return (f"tests still failing after {n} attempts. Last report:\n{report}"[:1500])
        return None
