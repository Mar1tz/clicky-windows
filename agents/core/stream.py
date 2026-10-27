"""A job's history as actions and observations (EN-30, after OpenHands).

OpenHands' contract (docs/BORROWED_LOGIC_PLAN.md): a worker emits actions,
the runtime answers with observations, and the pair is the job's whole
history. HeyTora already keeps that history - every step's ToolCalls and
ToolResults are saved to agents.db as they happen, and TOOL_CALL is
committed before a tool runs (EN-06) - but only as raw records. This
module reads them as a typed stream:

* an Action says what kind of thing was done (read, edit, run, browse,
  operate, ask, wait, use a service) with which tool and arguments;
* an Observation says what came back, ok or not, in one line.

The worker rebuilds its conversation from it when a step is resumed, so a
job a restart cut off carries on from what it did instead of starting
the step's thinking over; the stuck detector (EN-31, `stuck`) reads it to
see a loop.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Union

from .models import Step, Task, ToolCall, ToolResult

# What kind of thing a tool does, by its name's family. Unknown tools are
# "act": the stream never guesses that something only read.
_KINDS = (
    ("read", ("read_", "list_", "git_status", "git_diff", "git_log", "check_formulas", "tab_read",
              "find_on_screen", "screenshot", "extract_information", "devlink_")),
    ("edit", ("create_", "write_", "edit_", "move_path", "delete_path", "git_add", "git_commit", "clean_data",
              "render_design")),
    ("run", ("run_", "execute_script", "install_package", "git_push", "git_init", "git_branch")),
    ("browse", ("open_page", "open_link", "tab_", "click_element", "fill_field", "close_browser")),
    ("operate", ("click", "type_text", "key_press", "move_cursor", "open_app", "focus_window", "ui_control",
                 "app_action", "list_windows")),
    ("ask", ("ask_user", "ask_to_sign_in")),
    ("wait", ("wait_",)),
    ("use", ("mcp_",)),
)


def kind_of(tool_name: str) -> str:
    for kind, prefixes in _KINDS:
        if any(tool_name == p or tool_name.startswith(p) for p in prefixes):
            return kind
    return "act"


@dataclass
class Action:
    call_id: str
    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)
    kind: str = "act"

    @property
    def signature(self) -> str:
        """The same action, said the same way: tool + arguments, sorted."""
        return f"{self.tool}({json.dumps(self.arguments, sort_keys=True, default=str)[:400]})"


@dataclass
class Observation:
    call_id: str
    tool: str
    ok: bool
    text: str = ""                       # its first line, cut: what an outcome said

    @property
    def signature(self) -> str:
        return f"{'ok' if self.ok else 'error'}:{self.text[:200]}"


Event = Union[Action, Observation]


def _first_line(result: ToolResult) -> str:
    text = (result.output if result.ok else result.error) or ""
    return " ".join(text.strip().splitlines()[0].split())[:300] if text.strip() else ""


def pair(call: ToolCall, result: ToolResult) -> tuple[Action, Observation]:
    return (Action(call.id, call.tool_name, dict(call.arguments or {}), kind_of(call.tool_name)),
            Observation(call.id, call.tool_name, bool(result.ok), _first_line(result)))


def of_step(step: Step) -> list[Event]:
    """One step's stream: each answered call as an action then its observation.
    A call with no result yet (HeyTora stopped mid-call) is an action alone."""
    out: list[Event] = []
    results = {r.call_id: r for r in step.results}
    for i, call in enumerate(step.tool_calls):
        result = results.get(call.id) or (step.results[i] if i < len(step.results)
                                          and not step.results[i].call_id else None)
        if result is None:
            out.append(Action(call.id, call.tool_name, dict(call.arguments or {}), kind_of(call.tool_name)))
        else:
            out.extend(pair(call, result))
    return out


def of_task(task: Task) -> list[Event]:
    return [e for s in task.steps for e in of_step(s)]


def answered(stream: Iterable[Event]) -> list[tuple[Action, Observation]]:
    """The (action, observation) pairs, in order."""
    out, last = [], None
    for e in stream:
        if isinstance(e, Action):
            last = e
        elif last is not None and e.call_id == last.call_id:
            out.append((last, e))
            last = None
    return out


def stuck(pairs: list[tuple[Action, Observation]]) -> str:
    """EN-31, after OpenHands' stuck detector: why a step is going round in
    circles, or "". Cheaper than running into the token budget (limits.py):
    the same action with the same outcome three times; the same error three
    times; two actions taking turns for six."""
    if len(pairs) >= 3:
        last = pairs[-3:]
        if len({(a.signature, o.signature) for a, o in last}) == 1:
            return f"{last[-1][0].tool} ran three times with the same result"
        if all(not o.ok for _, o in last) and len({o.signature for _, o in last}) == 1:
            return f"the same error three times: {last[-1][1].text[:160]}"
    if len(pairs) >= 6:
        sig = [a.signature for a, _ in pairs[-6:]]
        if sig[0] == sig[2] == sig[4] and sig[1] == sig[3] == sig[5] and sig[0] != sig[1]:
            return f"going back and forth between {pairs[-1][0].tool} and {pairs[-2][0].tool}"
    return ""
