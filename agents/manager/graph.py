"""Multi-agent projects: one request, several agents, a declared DAG.

"Build me a complete website" becomes a *project*: a parent task whose
children are real tasks with their own agents - Backend, Frontend,
Tests - and whose edges say who waits for whom. The graph is declared
and validated before anything runs (LangGraph's good idea, borrowed
without the dependency), so a cycle or a dangling reference is a
planning error, not a deadlock discovered an hour in.

Coordination is through shared project memory, not messages: every child
runs in the same workspace, so the memory scope `project:<workspace>` is
what the second agent reads when it plans. The Backend agent's completion
note ("Express API on :3001, routes in src/server.js") is the Frontend
agent's context. No agent talks to another directly; it is cheaper, it
is inspectable in the timeline, and it survives restarts.

Two decomposers: `TemplateDecomposer` recognises the obvious shapes with
no model call, and `ProviderDecomposer` asks the model for a JSON graph,
validates it, and falls back to the template on any problem.
"""
from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from agents.core import AgentContext, ProviderMessage, TaskKind, new_id

log = logging.getLogger("agents.graph")

MAX_NODES = 8


class GraphError(ValueError):
    """The declared graph cannot run: a cycle, or an unknown dependency."""


@dataclass
class TaskNode:
    """One agent's share of a project."""
    key: str                                   # short id used in depends_on
    title: str
    goal: str
    worker_type: str = "coding"
    kind: TaskKind = TaskKind.CODING
    depends_on: list[str] = field(default_factory=list)
    agent_name: str = ""                       # "Backend Agent"; default from kind

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "title": self.title, "goal": self.goal,
                "worker_type": self.worker_type, "kind": self.kind.value,
                "depends_on": list(self.depends_on), "agent_name": self.agent_name}


@dataclass
class TaskGraph:
    nodes: list[TaskNode]
    rationale: str = ""

    def __post_init__(self) -> None:
        self.validate()

    # -- validation ----------------------------------------------------------

    def validate(self) -> None:
        keys = [n.key for n in self.nodes]
        if not keys:
            raise GraphError("a project needs at least one node")
        if len(set(keys)) != len(keys):
            raise GraphError("duplicate node keys")
        known = set(keys)
        for n in self.nodes:
            for dep in n.depends_on:
                if dep not in known:
                    raise GraphError(f"{n.key!r} depends on unknown node {dep!r}")
                if dep == n.key:
                    raise GraphError(f"{n.key!r} depends on itself")
        self.order()                                   # raises on a cycle

    def order(self) -> list[TaskNode]:
        """Topological order - a cycle raises GraphError."""
        by_key = {n.key: n for n in self.nodes}
        state: dict[str, int] = {}                     # 0 unseen, 1 visiting, 2 done
        out: list[TaskNode] = []

        def visit(key: str, trail: list[str]) -> None:
            mark = state.get(key, 0)
            if mark == 2:
                return
            if mark == 1:
                raise GraphError("cycle: " + " -> ".join([*trail, key]))
            state[key] = 1
            for dep in by_key[key].depends_on:
                visit(dep, [*trail, key])
            state[key] = 2
            out.append(by_key[key])

        for n in self.nodes:
            visit(n.key, [])
        return out

    # -- scheduling ----------------------------------------------------------

    def ready(self, done: Iterable[str], started: Iterable[str]) -> list[TaskNode]:
        """Nodes whose every dependency is done and which have not started."""
        done_set, started_set = set(done), set(started)
        return [n for n in self.nodes
                if n.key not in started_set and all(d in done_set for d in n.depends_on)]

    def dependants_of(self, key: str) -> list[TaskNode]:
        return [n for n in self.nodes if key in n.depends_on]

    @property
    def roots(self) -> list[TaskNode]:
        return [n for n in self.nodes if not n.depends_on]

    def to_dict(self) -> dict[str, Any]:
        return {"rationale": self.rationale, "nodes": [n.to_dict() for n in self.nodes]}


# -- decomposers ---------------------------------------------------------------

class Decomposer(ABC):
    """Turns a big goal into a TaskGraph, or None when it is a single job."""

    @abstractmethod
    async def decompose(self, ctx: AgentContext) -> Optional[TaskGraph]: ...


_WHOLE_APP = re.compile(
    r"\b(complete|full|full[- ]stack|entire|whole|end[- ]to[- ]end)\b.*"
    r"\b(website|web ?site|web ?app|application|app|platform|product|saas|crm)\b",
    re.I)
_FRONT_AND_BACK = re.compile(r"\b(front[- ]?end|ui)\b.*\b(back[- ]?end|api|server)\b|"
                             r"\b(back[- ]?end|api|server)\b.*\b(front[- ]?end|ui)\b", re.I)
_WITH_TESTS = re.compile(r"\b(tests?|testing|test suite)\b", re.I)


class TemplateDecomposer(Decomposer):
    """The shapes people actually say, with no model call.

    "Build me a complete website" / "a full-stack app" / "frontend and
    backend" -> Backend + Frontend in parallel, Tests after both. Anything
    else is one job and returns None - decomposition is for goals that
    are plainly several people's work, not a default.
    """

    async def decompose(self, ctx: AgentContext) -> Optional[TaskGraph]:
        goal = ctx.task.goal or ""
        if not (_WHOLE_APP.search(goal) or _FRONT_AND_BACK.search(goal)):
            return None
        subject = self._subject(goal)
        nodes = [
            TaskNode("backend", f"Backend for {subject}",
                     f"Build the backend for: {goal}. Create the server, data model and API "
                     f"under a backend/ folder. Write a short README in backend/ describing the "
                     f"endpoints so the frontend agent can use them.",
                     agent_name="Backend Agent"),
            TaskNode("frontend", f"Frontend for {subject}",
                     f"Build the frontend for: {goal}. Create the UI under a frontend/ folder. "
                     f"Read backend/README.md if it exists and call those endpoints.",
                     agent_name="Frontend Agent"),
            TaskNode("tests", f"Tests for {subject}",
                     f"Write tests for the project: {goal}. Cover the backend API and the "
                     f"frontend's main flows, under a tests/ folder, and make them runnable.",
                     depends_on=["backend", "frontend"], agent_name="Testing Agent"),
        ]
        return TaskGraph(nodes, rationale="Backend and frontend build in parallel; tests "
                                          "wait for both.")

    @staticmethod
    def _subject(goal: str) -> str:
        m = re.search(r"\b(website|web ?app|application|app|platform|crm|saas|product)\b",
                      goal, re.I)
        return m.group(0).lower() if m else "the project"


class ProviderDecomposer(Decomposer):
    """Asks the model whether, and how, to split the goal."""

    _SYSTEM = (
        "You plan work for a small team of autonomous agents that share one "
        "workspace. Decide whether the goal is one agent's job or several. "
        "Reply with ONLY a JSON object: {\"split\": bool, \"rationale\": str, "
        "\"nodes\": [{\"key\": str, \"title\": str, \"goal\": str, "
        "\"worker_type\": \"coding|research|browser|file|terminal\", "
        "\"agent_name\": str, \"depends_on\": [keys]}]}. Split only when the "
        "parts are genuinely separable (backend vs frontend, research vs "
        "build). Keys are short lowercase words. Dependencies must form a DAG; "
        "parts that can run at once should not depend on each other. Each "
        "node's goal must be self-contained and say which folder it owns. "
        f"At most {MAX_NODES} nodes. No prose, no code fence."
    )

    def __init__(self, fallback: Optional[Decomposer] = None, *, timeout: float = 30.0):
        self.fallback = fallback or TemplateDecomposer()
        self.timeout = timeout

    async def decompose(self, ctx: AgentContext) -> Optional[TaskGraph]:
        import asyncio
        try:
            resp = await asyncio.wait_for(ctx.provider.complete(
                [ProviderMessage("user", f"Goal: {ctx.task.goal}\nWorkspace: {ctx.workspace}")],
                tools=None, system=self._SYSTEM, temperature=0.1, max_tokens=1200,
            ), timeout=self.timeout)
            data = _parse(resp.text)
            if not data.get("split"):
                return None
            return self._to_graph(data)
        except Exception as exc:  # noqa: BLE001
            log.warning("ProviderDecomposer fell back (%s)", type(exc).__name__)
            return await self.fallback.decompose(ctx)

    @staticmethod
    def _to_graph(data: dict[str, Any]) -> TaskGraph:
        raw = data.get("nodes")
        if not isinstance(raw, list) or not raw:
            raise GraphError("no nodes")
        if len(raw) > MAX_NODES:
            raise GraphError(f"{len(raw)} nodes (max {MAX_NODES})")
        nodes = []
        for i, item in enumerate(raw):
            if not isinstance(item, dict) or not item.get("goal"):
                raise GraphError(f"node {i} has no goal")
            wt = str(item.get("worker_type") or "coding")
            kind = {"research": TaskKind.RESEARCH, "browser": TaskKind.BROWSER,
                    "file": TaskKind.FILE, "terminal": TaskKind.TERMINAL}.get(wt, TaskKind.CODING)
            nodes.append(TaskNode(
                key=str(item.get("key") or f"n{i}").strip().lower()[:24],
                title=str(item.get("title") or item["goal"])[:80],
                goal=str(item["goal"]),
                worker_type=wt if wt in ("coding", "research", "browser", "file", "terminal") else "coding",
                kind=kind,
                depends_on=[str(d).strip().lower()[:24] for d in (item.get("depends_on") or [])],
                agent_name=str(item.get("agent_name") or ""),
            ))
        return TaskGraph(nodes, rationale=str(data.get("rationale") or ""))


def _parse(text: str) -> dict[str, Any]:
    from agents.planning.planner import parse_json_object
    data = parse_json_object(text)
    if not isinstance(data, dict):
        raise GraphError("decomposer did not return a JSON object")
    return data
