"""TemplatePlanner / ProviderPlanner: scripted plans, fallback, replan."""
import json
import os
from typing import Any, Optional

from agents.core import (
    AgentContext, AgentProvider, AgentSpec, EventBus, Plan, ProviderCapabilities,
    ProviderResponse, RunControl, Step, StepStatus, Task, ToolCall, ToolRegistry,
    ToolResult,
)
from agents.planning import (
    ProviderPlanner, TemplatePlanner, TemplateSpec, insert_fix_step, match_template,
    parse_json_object,
)
from agents.planning.templates import npm_name, py_package

REACT_GOAL = ("Create a new folder called MyApp and build the foundation of a "
              "React application inside it.")


class FakeRegistry(ToolRegistry):
    def register(self, tool) -> None: ...
    def get(self, name): raise KeyError(name)
    def names(self) -> list[str]: return ["create_folder", "create_file", "list_dir", "run_command"]
    def specs(self, names=None): return []
    async def execute(self, call: ToolCall, ctx) -> ToolResult:
        return ToolResult.success(call, "ok")


class FakeProvider(AgentProvider):
    name = "fake"

    def __init__(self, text: str = "", raise_exc: Optional[Exception] = None) -> None:
        self.text, self.raise_exc, self.calls = text, raise_exc, 0
        self.last_system = ""
        self.last_user = ""

    async def complete(self, messages, tools=None, *, system="", model=None,
                       temperature=0.2, max_tokens=4096) -> ProviderResponse:
        self.calls += 1
        self.last_system = system
        self.last_user = chr(10).join(m.content or "" for m in messages if m.role == "user")
        if self.raise_exc:
            raise self.raise_exc
        return ProviderResponse(text=self.text)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities()


def _ctx(goal: str, workspace: str = "C:/ws", metadata: Optional[dict[str, Any]] = None,
         provider: Optional[AgentProvider] = None) -> AgentContext:
    task = Task(title="t", goal=goal, workspace=workspace, metadata=metadata or {})
    return AgentContext(task=task, agent=AgentSpec(name="Builder", goal=goal),
                        tools=FakeRegistry(), provider=provider or FakeProvider(),
                        events=EventBus(), control=RunControl())


def _calls(plan: Plan) -> list[ToolCall]:
    return [c for s in plan.steps for c in s.tool_calls]


# -- TemplatePlanner ---------------------------------------------------------

async def test_react_plan_under_subdir_skips_install_by_default():
    plan = await TemplatePlanner().plan(_ctx(REACT_GOAL, metadata={"subdir": "MyApp"}))
    assert plan.task_id and "react" in plan.rationale.lower()
    titles = [s.title for s in plan.steps]
    assert titles[0] == "Create project folder" and titles[-1] == "Verify project structure"
    assert "Install dependencies" not in titles
    calls = _calls(plan)
    assert calls[0].tool_name == "create_folder" and calls[0].arguments == {"path": "MyApp"}
    created = {c.arguments["path"]: c.arguments["content"] for c in calls if c.tool_name == "create_file"}
    assert set(created) == {
        "MyApp/package.json", "MyApp/index.html", "MyApp/vite.config.js", "MyApp/src/main.jsx",
        "MyApp/src/App.jsx", "MyApp/src/App.css", "MyApp/.gitignore", "MyApp/README.md",
    }
    pkg = json.loads(created["MyApp/package.json"])
    assert pkg["name"] == "myapp" and pkg["scripts"]["dev"] == "vite"
    assert "react" in pkg["dependencies"] and "vite" in pkg["devDependencies"]
    assert "/src/main.jsx" in created["MyApp/index.html"]
    assert "createRoot" in created["MyApp/src/main.jsx"] and "export default App" in created["MyApp/src/App.jsx"]
    assert all(content.strip() for content in created.values())
    assert calls[-1].tool_name == "list_dir" and calls[-1].arguments["path"] == "MyApp"
    assert all(s.status is StepStatus.PENDING and not s.results for s in plan.steps)


async def test_react_plan_without_subdir_uses_workspace_root():
    plan = await TemplatePlanner().plan(_ctx("build a react app", workspace="C:/ws/Shop"))
    calls = _calls(plan)
    assert calls[0].arguments == {"path": "."}
    paths = [c.arguments["path"] for c in calls if c.tool_name == "create_file"]
    assert "package.json" in paths and "src/App.jsx" in paths
    assert not any(p.startswith("/") or p.startswith(".") for p in paths if p != ".gitignore")
    assert json.loads(calls[1].arguments["content"])["name"] == "shop"


async def test_react_plan_install_step_when_requested():
    plan = await TemplatePlanner().plan(_ctx(REACT_GOAL, metadata={"subdir": "MyApp", "install": True}))
    titles = [s.title for s in plan.steps]
    assert titles[-2] == "Install dependencies" and titles[-1] == "Verify project structure"
    call = plan.steps[-2].tool_calls[0]
    assert call.tool_name == "run_command"
    assert call.arguments == {"command": "npm install", "cwd": "MyApp"}


async def test_python_project_plan():
    plan = await TemplatePlanner().plan(_ctx("set up a python project called data-tools",
                                             metadata={"subdir": "data-tools"}))
    assert "python" in plan.rationale
    paths = [c.arguments["path"] for c in _calls(plan) if c.tool_name == "create_file"]
    assert paths == ["data-tools/pyproject.toml", "data-tools/src/data_tools/__init__.py",
                     "data-tools/tests/test_smoke.py", "data-tools/README.md", "data-tools/.gitignore"]
    init = next(c for c in _calls(plan) if c.arguments.get("path", "").endswith("__init__.py"))
    assert "def hello" in init.arguments["content"]
    test = next(c for c in _calls(plan) if c.arguments.get("path", "").endswith("test_smoke.py"))
    assert "from data_tools import hello" in test.arguments["content"]


async def test_folder_plan_from_goal_or_subdir():
    plan = await TemplatePlanner().plan(_ctx("make a folder called Notes on the desktop"))
    assert len(plan.steps) == 1
    assert plan.steps[0].tool_calls[0].tool_name == "create_folder"
    assert plan.steps[0].tool_calls[0].arguments == {"path": "Notes"}

    plan = await TemplatePlanner().plan(_ctx("create a directory", metadata={"subdir": "Inbox/2026"}))
    assert plan.steps[0].tool_calls[0].arguments == {"path": "Inbox/2026"}


async def test_fallback_plan_has_no_tool_calls():
    goal = "write a haiku about the sea and save it"
    plan = await TemplatePlanner().plan(_ctx(goal))
    assert len(plan.steps) == 1
    step = plan.steps[0]
    assert step.title == f"Work on: {goal}" and step.tool_calls == []
    assert step.description == goal
    # "folder" without a name is not enough for the folder template either
    plan = await TemplatePlanner().plan(_ctx("tidy up the downloads folder"))
    assert plan.steps[0].tool_calls == []


def test_template_matching_and_name_helpers():
    assert match_template(TemplateSpec(goal="react site")).name == "react-vite"
    assert match_template(TemplateSpec(goal="a python package")).name == "python-project"
    assert match_template(TemplateSpec(goal="python", root="")) is None       # no qualifier
    assert match_template(TemplateSpec(goal="new folder", root="X")).name == "folder"
    assert match_template(TemplateSpec(goal="new folder")) is None
    assert npm_name("My App!") == "my-app" and npm_name("") == "my-app"
    assert py_package("data-tools") == "data_tools" and py_package("3d") == "_3d" and py_package("") == "app"
    spec = TemplateSpec.from_context(_ctx("x", metadata={"subdir": "..\\evil"}))
    assert spec.root == ""                                        # traversal never becomes a root


async def test_replan_marks_failed_step_skipped_and_inserts_fix():
    ctx = _ctx(REACT_GOAL, metadata={"subdir": "MyApp"})
    plan = await TemplatePlanner().plan(ctx)
    n = len(plan.steps)
    plan.steps[0].status = StepStatus.DONE
    failed = plan.steps[1]
    failed.status = StepStatus.FAILED
    failed.error = "permission denied"
    ctx.task.steps = plan.steps                                   # runtime aliases the list

    out = await TemplatePlanner().replan(ctx, plan, "permission denied")
    assert out is plan and len(plan.steps) == n + 1
    assert failed.status is StepStatus.SKIPPED
    fix = plan.steps[2]
    assert fix.title == "Fix: permission denied" and fix.tool_calls == []
    assert failed.title in fix.description and "create_file" in fix.description
    assert fix.status is StepStatus.PENDING
    assert [s.title for s in plan.steps[3:]] == [s.title for s in ctx.task.steps[3:]]
    assert ctx.task.step_index == 2                               # runtime resumes at the fix step


def test_insert_fix_step_without_failed_marker_goes_before_first_pending():
    plan = Plan(task_id="t", steps=[Step("a", status=StepStatus.DONE), Step("b"), Step("c")])
    insert_fix_step(plan, "boom")
    assert [s.title for s in plan.steps] == ["a", "Fix: boom", "b", "c"]


# -- ProviderPlanner ---------------------------------------------------------

async def test_provider_plan_parses_fenced_json():
    prov = FakeProvider('Sure!\n```json\n{"rationale": "two steps", "steps": ['
                        '{"title": "Read the file", "description": "open it"}, '
                        '{"title": "Summarise"}]}\n```')
    plan = await ProviderPlanner().plan(_ctx("summarise notes.txt", provider=prov))
    assert prov.calls == 1 and "JSON" in prov.last_system
    assert plan.rationale == "two steps"
    assert [s.title for s in plan.steps] == ["Read the file", "Summarise"]
    assert plan.steps[0].description == "open it" and all(not s.tool_calls for s in plan.steps)


async def test_provider_plan_falls_back_on_bad_output_or_error():
    for prov in (FakeProvider("I cannot help with that."),
                 FakeProvider('{"steps": []}'),
                 FakeProvider('{"steps": [{"description": "no title"}]}'),
                 FakeProvider(raise_exc=RuntimeError("network down"))):
        plan = await ProviderPlanner().plan(_ctx(REACT_GOAL, metadata={"subdir": "MyApp"}, provider=prov))
        assert prov.calls == 1
        assert plan.steps[0].tool_calls[0].tool_name == "create_folder"   # template took over


async def test_provider_plan_prefers_templates_when_asked():
    prov = FakeProvider('{"steps": [{"title": "x"}]}')
    planner = ProviderPlanner(prefer_templates=True)
    plan = await planner.plan(_ctx(REACT_GOAL, metadata={"subdir": "MyApp"}, provider=prov))
    assert prov.calls == 0 and _calls(plan)
    plan = await planner.plan(_ctx("write a poem", provider=prov))
    assert prov.calls == 1 and plan.steps[0].title == "x"


def test_parse_json_object_variants():
    assert parse_json_object('{"a": 1}') == {"a": 1}
    assert parse_json_object('text before {"a": [1, 2]} text after') == {"a": [1, 2]}
    assert parse_json_object('```\n{"a": {"b": 2}}\n```') == {"a": {"b": 2}}
    for bad in ("", "nope", "{not json}"):
        try:
            parse_json_object(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {bad!r}")


async def test_provider_planner_is_told_what_is_installed_and_to_open_apps_first():
    """Field: asked to open Devin (installed), the planner planned 'open
    Devin in the browser' because nothing told it Devin was on the
    machine. The manager now hands it the Start Menu, and a rule."""
    prov = FakeProvider('{"steps": [{"title": "Open Devin"}]}')
    ctx = _ctx("open devin", provider=prov)
    ctx.extras["tool_names"] = ["open_app", "list_windows", "click"]
    ctx.extras["context_lines"] = ["Installed applications (open any with open_app): Devin, Notepad"]
    await ProviderPlanner().plan(ctx)
    assert "Installed applications" in prov.last_user and "Devin, Notepad" in prov.last_user
    assert "FIRST step is open_app" in prov.last_user
