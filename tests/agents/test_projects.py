"""M21: multi-agent projects.

The gate: one request produces >=3 agents, two of which run at the same
time, and the dependent one starts only after its dependencies complete.
A slow worker makes the overlap observable.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from agents.bootstrap import build_agent_manager
from agents.core import (
    AgentContext, AgentSpec, EventBus, EventKind, ProviderCapabilities, ProviderResponse,
    RunControl, StepStatus, Task, TaskKind, TaskStatus, ToolRegistry,
)
from agents.manager import (
    GraphError, ProviderDecomposer, TaskGraph, TaskNode, TemplateDecomposer,
)


# -- graph -------------------------------------------------------------------

def test_graph_orders_and_schedules():
    g = TaskGraph([TaskNode("a", "A", "g"), TaskNode("b", "B", "g"),
                   TaskNode("c", "C", "g", depends_on=["a", "b"])])
    assert [n.key for n in g.order()] == ["a", "b", "c"]
    assert [n.key for n in g.roots] == ["a", "b"]
    assert [n.key for n in g.ready(done=[], started=[])] == ["a", "b"]
    assert g.ready(done=["a"], started=["a", "b"]) == []
    assert [n.key for n in g.ready(done=["a", "b"], started=["a", "b"])] == ["c"]
    assert [n.key for n in g.dependants_of("a")] == ["c"]


@pytest.mark.parametrize("nodes,msg", [
    ([TaskNode("a", "A", "g", depends_on=["a"])], "itself"),
    ([TaskNode("a", "A", "g", depends_on=["b"]), TaskNode("b", "B", "g", depends_on=["a"])], "cycle"),
    ([TaskNode("a", "A", "g", depends_on=["zz"])], "unknown"),
    ([TaskNode("a", "A", "g"), TaskNode("a", "A2", "g")], "duplicate"),
    ([], "at least one"),
])
def test_graph_rejects_bad_shapes_before_anything_runs(nodes, msg):
    with pytest.raises(GraphError) as e:
        TaskGraph(nodes)
    assert msg in str(e.value)


# -- decomposers -------------------------------------------------------------

class _NoTools(ToolRegistry):
    def register(self, t): ...
    def get(self, n): raise KeyError(n)
    def names(self): return []
    def specs(self, names=None): return []
    async def execute(self, c, x): raise NotImplementedError


class _Say:
    name = "say"

    def __init__(self, text): self.text = text

    async def complete(self, *a, **k): return ProviderResponse(text=self.text)

    def capabilities(self): return ProviderCapabilities()


def _ctx(goal, provider=None):
    t = Task(title="t", goal=goal, workspace=".")
    return AgentContext(task=t, agent=AgentSpec(name="A", goal=goal), tools=_NoTools(),
                        provider=provider or _Say(""), events=EventBus(), control=RunControl())


@pytest.mark.parametrize("goal", [
    "Build me a complete website for my bakery",
    "Create a full-stack app with a frontend and backend",
    "make a CRM with a backend API and a frontend UI",
])
def test_template_splits_whole_app_goals(goal):
    g = asyncio.run(TemplateDecomposer().decompose(_ctx(goal)))
    assert g is not None
    keys = [n.key for n in g.nodes]
    assert keys == ["backend", "frontend", "tests"]
    assert g.nodes[2].depends_on == ["backend", "frontend"]
    assert g.nodes[0].agent_name == "Backend Agent"
    assert "backend/" in g.nodes[0].goal and "frontend/" in g.nodes[1].goal


@pytest.mark.parametrize("goal", [
    "Create a folder called MyApp",
    "Write a haiku to haiku.txt",
    "Build the foundation of a React application",
])
def test_template_leaves_single_jobs_alone(goal):
    assert asyncio.run(TemplateDecomposer().decompose(_ctx(goal))) is None


def test_provider_decomposer_parses_and_validates():
    reply = ('{"split": true, "rationale": "two parts", "nodes": ['
             '{"key": "api", "title": "API", "goal": "build api in api/", "worker_type": "coding"},'
             '{"key": "ui", "title": "UI", "goal": "build ui in ui/", "depends_on": ["api"], '
             '"agent_name": "UI Agent"}]}')
    g = asyncio.run(ProviderDecomposer().decompose(_ctx("build it", _Say(reply))))
    assert [n.key for n in g.nodes] == ["api", "ui"]
    assert g.nodes[1].depends_on == ["api"] and g.nodes[1].agent_name == "UI Agent"
    assert g.rationale == "two parts"


def test_provider_decomposer_no_split_means_one_task():
    reply = '{"split": false, "rationale": "single job", "nodes": []}'
    assert asyncio.run(ProviderDecomposer().decompose(_ctx("small", _Say(reply)))) is None


def test_provider_decomposer_falls_back_on_bad_graph():
    """A cyclic graph from the model must not become a deadlocked project."""
    reply = ('{"split": true, "nodes": [{"key": "a", "goal": "x", "depends_on": ["b"]},'
             '{"key": "b", "goal": "y", "depends_on": ["a"]}]}')
    g = asyncio.run(ProviderDecomposer().decompose(
        _ctx("Build me a complete website", _Say(reply))))
    assert g is not None and [n.key for n in g.nodes] == ["backend", "frontend", "tests"]


def test_provider_decomposer_falls_back_on_garbage():
    g = asyncio.run(ProviderDecomposer().decompose(_ctx("Build me a complete website",
                                                        _Say("I dunno"))))
    assert g is not None and len(g.nodes) == 3


# -- the gate: a real project through the manager ----------------------------

class _SlowFolderPlanner:
    """Each child: one create_folder step, padded so runs overlap."""

    def __init__(self, delay: float = 0.4): self.delay = delay

    async def plan(self, ctx):
        from agents.core import Plan, Step, ToolCall
        await asyncio.sleep(self.delay)
        key = ctx.task.metadata.get("node", "x")
        return Plan(task_id=ctx.task.id, steps=[
            Step(f"make {key}", tool_calls=[ToolCall("create_folder", {"path": key})])])

    async def replan(self, ctx, plan, failure): return plan


def _project_manager(tmp_path, **kw):
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True, **kw)
    mgr.planner = mgr.orchestrator.planner = _SlowFolderPlanner()
    mgr.decomposer = TemplateDecomposer()
    return mgr.start()


def test_one_request_becomes_a_project_with_three_agents(tmp_path):
    mgr = _project_manager(tmp_path)
    try:
        windows: dict[str, list[float]] = {}          # child id -> [start, end]

        def on_status(ev):
            st = ev.data.get("status")
            if st == "running":
                windows.setdefault(ev.task_id, [time.monotonic(), None])
            elif st in ("completed", "failed", "cancelled") and ev.task_id in windows:
                windows[ev.task_id][1] = time.monotonic()

        mgr.events.subscribe(on_status, kinds=[EventKind.TASK_STATUS])

        parent = mgr.submit("Build me a complete website for my bakery")
        assert parent is not None and parent.metadata.get("project")
        parent = mgr.wait(parent.id, 60)
        assert parent.status is TaskStatus.COMPLETED, parent.error

        children = parent.metadata["children"]
        assert set(children) == {"backend", "frontend", "tests"}
        names = {mgr.get_agent(mgr.get_task(cid).agent_id).spec.name for cid in children.values()}
        assert names == {"Backend Agent", "Frontend Agent", "Testing Agent"}
        assert len(mgr.list_agents()) == 4                  # + Project Lead

        # backend and frontend overlapped; tests started after both ended
        b, f, t = (windows[children[k]] for k in ("backend", "frontend", "tests"))
        assert b[0] < f[1] and f[0] < b[1], "backend and frontend did not run at the same time"
        assert t[0] >= max(b[1], f[1]) - 0.05, "tests started before its dependencies finished"

        for key in ("backend", "frontend", "tests"):
            assert (tmp_path / key).is_dir()
        assert [s.status for s in parent.steps] == [StepStatus.DONE] * 3
        assert parent.progress == 1.0
    finally:
        mgr.stop()


def test_project_respects_the_concurrency_limit(tmp_path):
    mgr = _project_manager(tmp_path, max_concurrent=1)
    try:
        active_peaks = []

        def on_status(ev):
            active_peaks.append(len(mgr._active))

        mgr.events.subscribe(on_status, kinds=[EventKind.TASK_STATUS])
        parent = mgr.wait(mgr.submit("Build me a complete website").id, 60)
        assert parent.status is TaskStatus.COMPLETED, parent.error
        assert max(active_peaks) <= 1
    finally:
        mgr.stop()


def test_failed_child_fails_the_project_and_skips_dependants(tmp_path):
    class _FailBackend(_SlowFolderPlanner):
        async def plan(self, ctx):
            from agents.core import Plan, Step, ToolCall
            if ctx.task.metadata.get("node") == "backend":
                return Plan(task_id=ctx.task.id,
                            steps=[Step("boom", tool_calls=[ToolCall("no_such_tool", {})])])
            return await super().plan(ctx)

    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True,
                              max_replans=0) if False else \
        build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    mgr.planner = mgr.orchestrator.planner = _FailBackend()
    mgr.orchestrator.max_replans = 0
    mgr.decomposer = TemplateDecomposer()
    mgr.start()
    try:
        parent = mgr.wait(mgr.submit("Build me a complete website").id, 60)
        assert parent.status is TaskStatus.FAILED
        assert "backend" in parent.error
        by_title = {s.title: s for s in parent.steps}
        assert by_title["Backend for website"].status is StepStatus.FAILED
        assert by_title["Frontend for website"].status is StepStatus.DONE
        assert by_title["Tests for website"].status is StepStatus.SKIPPED
        assert "blocked" in by_title["Tests for website"].error
        assert not (tmp_path / "tests").exists()
    finally:
        mgr.stop()


def test_children_share_project_memory(tmp_path):
    """What one agent finished is written where the next agent's planner
    reads - the coordination channel."""
    mgr = _project_manager(tmp_path)
    try:
        parent = mgr.wait(mgr.submit("Build me a complete website").id, 60)
        assert parent.status is TaskStatus.COMPLETED
        from agents.core import MemoryStore
        entries = mgr.memory.query(MemoryStore.project_scope(str(tmp_path)), kind="context")
        texts = " ".join(e.content for e in entries)
        assert "Backend for website is done" in texts
        assert "Frontend for website is done" in texts
    finally:
        mgr.stop()


def test_cancelling_the_project_cancels_its_children(tmp_path):
    mgr = _project_manager(tmp_path)
    mgr.planner = mgr.orchestrator.planner = _SlowFolderPlanner(delay=3.0)
    try:
        parent = mgr.submit("Build me a complete website")
        time.sleep(0.3)
        assert mgr.cancel(parent.id)
        parent = mgr.wait(parent.id, 30)
        assert parent.status is TaskStatus.CANCELLED
        for cid in parent.metadata["children"].values():
            assert mgr.wait(cid, 30).status is TaskStatus.CANCELLED
    finally:
        mgr.stop()


def test_a_child_is_never_decomposed_again(tmp_path):
    """Recursion guard: a child whose goal still says 'complete website'
    must run as one task, not spawn another project."""
    mgr = _project_manager(tmp_path)
    try:
        parent = mgr.wait(mgr.submit("Build me a complete website").id, 60)
        assert parent.status is TaskStatus.COMPLETED
        assert len(mgr.list_tasks()) == 4                   # parent + 3, no grandchildren
    finally:
        mgr.stop()


def test_a_node_gets_its_inputs_and_its_failure_policy(tmp_path):
    """OR-08: a node's result is the next node's input; "retry" runs a
    failed node once more; "continue" lets what depends on it go on."""
    from agents.core import Plan, Step, ToolCall
    tries: dict[str, int] = {}
    goals: dict[str, str] = {}

    class _Planner(_SlowFolderPlanner):
        async def plan(self, ctx):
            key = ctx.task.metadata.get("node")
            tries[key] = tries.get(key, 0) + 1
            goals[key] = ctx.task.goal
            if (key == "fetch" and tries[key] == 1) or key == "extras":
                return Plan(task_id=ctx.task.id, steps=[Step("boom", tool_calls=[ToolCall("no_such_tool", {})])])
            return Plan(task_id=ctx.task.id, steps=[
                Step(f"make {key}", tool_calls=[ToolCall("create_folder", {"path": key})])])

    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    mgr.planner = mgr.orchestrator.planner = _Planner(delay=0.05)
    mgr.orchestrator.max_replans = 0
    mgr.start()
    try:
        graph = TaskGraph([
            TaskNode("fetch", "Fetch prices", "find the prices", on_fail="retry", done_when="prices/ exists"),
            TaskNode("extras", "Find extras", "nice to have", on_fail="continue"),
            TaskNode("report", "Write report", "write it up", depends_on=["fetch", "extras"]),
        ])
        task = Task(title="Prices", goal="prices report", workspace=str(tmp_path))
        parent = mgr.wait(mgr.submit_project(task, graph).id, 60)
        assert parent.status is TaskStatus.COMPLETED, parent.error
        assert tries == {"fetch": 2, "extras": 1, "report": 1}
        assert "Done when: prices/ exists" in goals["fetch"]
        assert "From 'Fetch prices'" in goals["report"], "the result before it is its input"
        assert "✗ Find extras" in parent.result and "✓ Write report" in parent.result
    finally:
        mgr.stop()


def test_the_model_may_plan_computer_nodes_with_proofs():
    data = {"split": True, "nodes": [
        {"key": "find", "goal": "find the cheapest flight", "worker_type": "browser", "done_when": "a price"},
        {"key": "note", "goal": "put it in Notepad", "worker_type": "computer", "depends_on": ["find"],
         "on_fail": "explode"}]}
    g = ProviderDecomposer._to_graph(data)
    assert g.nodes[1].kind is TaskKind.COMPUTER and g.nodes[1].on_fail == "stop"
    assert g.nodes[0].done_when == "a price" and g.to_dict()["nodes"][1]["on_fail"] == "stop"
