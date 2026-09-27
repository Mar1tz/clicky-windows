"""The shared kit for industry packs: levels, tools, a store, a worker, a pack.

Accounting was built first, by hand. Every pack after it needs the same
machinery: a tool that declares its action level, a folder with a small
database, a worker that turns a failed check into a note and carries on,
intent rules, a workflow's scripted steps, a dashboard of a few numbers and
lists. A pack should write only what belongs to its trade. This is the rest.

This is a module, not a package, so pack discovery (which imports every
sub-package of `extensions`) never mistakes it for a pack. Nothing here
knows any trade.

    class MyStore(Store):  SETTINGS_FILE, DB, SCHEMA, FOLDERS  + the trade's methods
    TOOLS = ToolBook("mypack", noun="your records")
    @TOOLS.tool("name", "what it does", Level.DRAFT, {...props}, ["required"])
    def name(store, args) -> Out: ...
    class MyPack(PackExtension):  STORE, TOOLS, TEMPLATES, WORKFLOWS, SKILLS, INTENT_RULES, ...
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Optional

from agents.core import AgentContext, RiskLevel, Step, StepStatus, Task, TaskKind, ToolCall, ToolContext, ToolResult
from agents.extensions import (
    AgentTemplate, DashboardModule, IAgentExtension, IntentRule, PermissionProfile, Schedule, Skill, Workflow,
)
from agents.tools.base import Tool, make_spec
from agents.workers.base import Worker, collect_outcomes, describe_call


# -- levels ---------------------------------------------------------------------------------------------------

class Level(str, Enum):
    """The four action levels, as the Accounting pack defined them (docs/agents/EXTENSIONS.md 6)."""
    READ = "read"              # look                                  never asks
    DRAFT = "draft"            # prepare something nobody has accepted runs unasked
    POST = "post"              # change a record the person relies on  asks every time
    SENSITIVE = "sensitive"    # money leaves, a filing, a send        asks, never granted, typed word

    @property
    def risk(self) -> RiskLevel:
        return {Level.READ: RiskLevel.SAFE, Level.DRAFT: RiskLevel.LOW,
                Level.POST: RiskLevel.HIGH, Level.SENSITIVE: RiskLevel.HIGH}[self]

    @property
    def always_ask(self) -> bool:
        return self in (Level.POST, Level.SENSITIVE)

    @property
    def sensitive(self) -> bool:
        return self is Level.SENSITIVE


LEVEL_RISK = {lvl.value: lvl.risk for lvl in Level}


# -- tools ----------------------------------------------------------------------------------------------------

@dataclass
class Out:
    """What a tool returns: text for the model, data for the verifier and UI,
    and findings - what the person must look at, lifted onto the task."""
    text: str
    data: dict[str, Any] = field(default_factory=dict)
    findings: list[dict[str, Any]] = field(default_factory=list)


def finding(title: str, detail: str = "", *, action: str = "", entity: str = "",
            severity: str = "review") -> dict[str, Any]:
    return {"title": title, "detail": detail, "severity": severity, "entity": entity, "action": action}


@dataclass
class ToolDef:
    name: str
    description: str
    level: Level
    properties: dict[str, Any]
    required: list[str]
    fn: Callable[[Any, dict[str, Any]], Out]
    confirm_word: str = ""


# What a tool may raise and still be a clean "no, because...": bad input, a
# missing record, a file that is not there. Anything else is a bug and raises.
EXPECTED = (ValueError, KeyError, LookupError, OSError, TypeError, sqlite3.Error)


class ToolBook:
    """A pack's tool definitions. `category` is the ToolSpec category (tool
    names cannot carry a namespace); `noun` finishes the approval sentence."""

    def __init__(self, category: str, *, noun: str = "your records") -> None:
        self.category = category
        self.noun = noun
        self.defs: list[ToolDef] = []

    def tool(self, name: str, description: str, level: Level, properties: Optional[dict[str, Any]] = None,
             required: Optional[list[str]] = None, *, confirm_word: str = ""):
        def wrap(fn):
            if any(d.name == name for d in self.defs):
                raise ValueError(f"tool {name!r} declared twice")
            self.defs.append(ToolDef(name, description, level, dict(properties or {}), list(required or []), fn,
                                     confirm_word or ("CONFIRM" if level.sensitive else "")))
            return fn
        return wrap

    def names(self) -> list[str]:
        return [d.name for d in self.defs]

    def levels(self) -> dict[str, str]:
        return {d.name: d.level.value for d in self.defs}

    def build(self, stores: "StoreCache") -> list["PackTool"]:
        return [PackTool(d, stores, self) for d in self.defs]

    def permissions(self, notes: str = "") -> PermissionProfile:
        return PermissionProfile(levels=dict(LEVEL_RISK), tool_levels=self.levels(),
                                 sensitive=[d.name for d in self.defs if d.level.sensitive],
                                 always_ask=[d.name for d in self.defs if d.level.always_ask], notes=notes)


class PackTool(Tool):
    """A ToolDef bound to the pack's open stores. Its risk, always_ask and
    sensitive flags come from the level, so the permission a tool needs is a
    fact about the tool and not something a prompt can talk around."""

    def __init__(self, d: ToolDef, stores: "StoreCache", book: ToolBook) -> None:
        self.d = d
        self.stores = stores
        self.level = d.level
        prefix = {Level.READ: "[READ] ", Level.DRAFT: "[DRAFT] ", Level.POST: "[POST - needs approval] ",
                  Level.SENSITIVE: "[SENSITIVE - the person must confirm] "}[d.level]
        self.spec = make_spec(d.name, prefix + d.description, risk=d.level.risk, category=book.category,
                              properties=d.properties, required=d.required)
        self.always_ask = d.level.always_ask
        self.sensitive = d.level.sensitive
        self.soft_fail = not d.level.always_ask      # a READ/DRAFT failure is information
        self.confirm_word = d.confirm_word
        self.ask_reason = {Level.POST: f"{d.name} changes {book.noun} - approve it first",
                           Level.SENSITIVE: f"{d.name} cannot be undone - type {d.confirm_word} to confirm",
                           }.get(d.level, "")

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            store = self.stores.get(ctx.workspace)
        except LookupError as exc:
            return self.fail(str(exc))
        try:
            with store.lock:
                out = self.d.fn(store, args)
        except EXPECTED as exc:
            msg = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
            return self.fail(str(msg))
        text = out.text
        if out.findings:
            text += "\n\nNeeds you:\n" + "\n".join(f"  - {f['title']}" for f in out.findings)
        return self.ok(text, **out.data, findings=[{**f, "source": self.name} for f in out.findings])


# -- the store -------------------------------------------------------------------------------------------------

class NotSetUp(LookupError):
    pass


class Store:
    """A pack's folder: a JSON settings file, one SQLite database, and the
    sub-folders people open. Subclasses set the class attributes and add
    the trade's methods. Every write goes through `write()` (one commit)."""

    SETTINGS_FILE = "pack.json"
    DB = "pack.sqlite"
    SCHEMA = ""
    FOLDERS: tuple[str, ...] = ()

    def __init__(self, root: str) -> None:
        path = os.path.join(root, self.SETTINGS_FILE)
        if not os.path.isfile(path):
            raise NotSetUp(f"{root} has not been set up (no {self.SETTINGS_FILE})")
        self.root = root
        with open(path, encoding="utf-8") as fh:
            self.cfg: dict[str, Any] = json.load(fh)
        self.db = sqlite3.connect(os.path.join(root, self.DB), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(self.SCHEMA)
        self.lock = threading.RLock()

    @classmethod
    def create(cls, root: str, **cfg: Any) -> "Store":
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, cls.SETTINGS_FILE), "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
        for name in cls.FOLDERS:
            os.makedirs(os.path.join(root, name), exist_ok=True)
        return cls(root)

    @classmethod
    def is_set_up(cls, root: str) -> bool:
        return os.path.isfile(os.path.join(root, cls.SETTINGS_FILE))

    def close(self) -> None:
        self.db.close()

    def folder(self, name: str) -> str:
        path = os.path.join(self.root, name)
        os.makedirs(path, exist_ok=True)
        return path

    def rows(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute(sql, tuple(params))]

    def one(self, sql: str, params: Iterable[Any] = ()) -> Optional[dict[str, Any]]:
        r = self.db.execute(sql, tuple(params)).fetchone()
        return dict(r) if r is not None else None

    def write(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self.lock, self.db:
            return int(self.db.execute(sql, tuple(params)).lastrowid or 0)


class StoreCache:
    """Tools are handed only ctx.workspace; this turns it into the open store,
    one connection per folder for the life of the pack."""

    def __init__(self, cls: type[Store]) -> None:
        self.cls = cls
        self._open: dict[str, Store] = {}
        self._lock = threading.RLock()

    def get(self, path: str) -> Store:
        key = os.path.normcase(os.path.abspath(path or "."))
        with self._lock:
            s = self._open.get(key)
            if s is None:
                s = self._open[key] = self.cls(path)
            return s

    def drop(self) -> None:
        with self._lock:
            for s in self._open.values():
                s.close()
            self._open.clear()


# -- plans, rules, the worker ------------------------------------------------------------------------------------

def step(title: str, tool: str = "", args: Optional[dict[str, Any]] = None, description: str = "") -> Step:
    """A checklist step: scripted when it names a tool, left to the model when not."""
    return Step(title=title, description=description or title,
                tool_calls=[ToolCall(tool, dict(args or {}))] if tool else [])


VERB = (r"\b(?:please\s+)?(?:run|start|prepare|make|build|get|sort\s+out|chase|write|draft|put\s+together|"
        r"check|review|plan|create|add|log|record|update)\b")


def rule(ext: str, template: str, obj: str, *, workflow: str = "", reply: str = "", verb: str = VERB,
         extract: Optional[Callable[[str], dict[str, str]]] = None) -> IntentRule:
    """A request this pack takes: a verb, then within 60 characters the object.
    Both are needed, so a passing mention never starts an agent."""
    return IntentRule(pattern=verb + r"[^.?!]{0,60}?" + obj, template_id=template, workflow_id=workflow,
                      reply=reply, extension_id=ext, extract=extract)


class PackWorker(Worker):
    """One worker per pack. A scripted check that fails becomes a note for the
    person and the checklist carries on; a POST or SENSITIVE tool that fails
    fails its step."""

    max_iterations = 14

    def __init__(self, worker_type: str, tool_names: list[str], role: str, mission: str) -> None:
        self.worker_type = worker_type
        self.role = role
        self.mission = mission
        self.default_tools = list(dict.fromkeys([*tool_names, "read_file", "list_dir"]))

    def system_prompt(self, ctx: AgentContext) -> str:
        return (f"You are {ctx.agent.name}, {self.role}. Mission: {self.mission} You work only inside the folder "
                f"{ctx.workspace}. Use this pack's tools for every record and every change; they enforce drafts "
                "and approvals. When the step is done, reply with a two-line summary (what was done; what needs "
                "the person) and no tool call.")

    async def _run_scripted(self, step_: Step, ctx: AgentContext) -> None:
        tctx = ctx.tool_context()
        firsts = []
        for call in list(step_.tool_calls):
            await ctx.control.checkpoint()
            ctx.progress(describe_call(call))
            result = await ctx.tools.execute(call, tctx)
            step_.results.append(result)
            collect_outcomes(ctx, result)
            if not result.ok:
                try:
                    soft = bool(getattr(ctx.tools.get(call.tool_name), "soft_fail", False))
                except Exception:  # noqa: BLE001 - unknown tool: not soft
                    soft = False
                if not soft:
                    step_.status = StepStatus.FAILED
                    step_.error = result.error or f"{call.tool_name} failed"
                    return
                collect_outcomes(ctx, ToolResult(call_id=call.id, tool_name=call.tool_name, ok=True, data={
                    "findings": [finding(f"{step_.title}: could not be done", (result.error or "")[:300],
                                         entity=step_.title, action="do this one by hand, or give Tora what it needs")]}))
                firsts.append(f"could not: {(result.error or '')[:80]}")
                continue
            firsts.append((result.output or "").strip().splitlines()[0][:140] if result.output else call.tool_name)
        step_.status = StepStatus.DONE
        step_.summary = "; ".join(firsts)


# -- the pack ----------------------------------------------------------------------------------------------------

class PackExtension(IAgentExtension):
    """A pack built from parts. Subclasses set:

        manifest      ExtensionManifest.load(HERE)
        STORE         the Store subclass
        TOOLS         the ToolBook
        TEMPLATES     [AgentTemplate] - worker_type must be the manifest id
        WORKFLOWS     [Workflow]      - extension_id is filled in
        SKILLS        [Skill]
        INTENT_RULES  [IntentRule]
        FOLDER        default folder name under the workspace ("Channel")
        DEFAULTS      settings used when the folder is first set up
        ROLE          "the producer of a video channel" - for the worker prompt
        ACTIONS       [(button label, workflow id)] on the dashboard
        EXAMPLES      [(request, workflow id or "")] this pack must win with every pack on

    and implement snapshot(store) for the dashboard and context_line(store)
    for prompts. Settings `auto_<workflow id>` = True schedule that workflow.
    """

    STORE: type[Store] = Store
    TOOLS: ToolBook
    TEMPLATES: list[AgentTemplate] = []
    WORKFLOWS: list[Workflow] = []
    SKILLS: list[Skill] = []
    INTENT_RULES: list[IntentRule] = []
    FOLDER = "Pack"
    DEFAULTS: dict[str, Any] = {}
    ROLE = "an assistant"
    ACTIONS: list[tuple[str, str]] = []
    NOTES = ""
    EXAMPLES: list[tuple[str, str]] = []     # (request, workflow id or "") - must route here with every pack on

    def __init__(self) -> None:
        self.stores = StoreCache(self.STORE)

    # -- parts ---------------------------------------------------------------------------------------------
    def settings_defaults(self) -> dict[str, Any]:
        return {"folder": "", **self.DEFAULTS}

    def get_agent_templates(self):
        return list(self.TEMPLATES)

    def get_tools(self, ctx):
        return self.TOOLS.build(self.stores)

    def get_workers(self, ctx):
        mission = self.TEMPLATES[0].mission if self.TEMPLATES else ""
        return [PackWorker(self.id, self.TOOLS.names(), self.ROLE, mission)]

    def get_skills(self):
        return list(self.SKILLS)

    def get_workflows(self):
        for wf in self.WORKFLOWS:
            wf.extension_id = self.id
        return list(self.WORKFLOWS)

    def get_permissions(self) -> PermissionProfile:
        return self.TOOLS.permissions(self.NOTES)

    def get_intent_rules(self):
        return list(self.INTENT_RULES)

    def get_dashboard_modules(self):
        def factory(manager, extension, parent=None):
            from extensions.packkit_ui import PackDashboard
            return PackDashboard(manager, extension, parent)
        return [DashboardModule(f"{self.id}.dashboard", self.manifest.name, factory, order=50)]

    def get_context_lines(self, task: Task) -> list[str]:
        try:
            store = self.stores.get(task.workspace)
        except LookupError:
            return [f"{self.manifest.name}: this task is not in the pack's folder."]
        return [self.context_line(store)]

    # -- what a pack implements -----------------------------------------------------------------------------
    def context_line(self, store: Store) -> str:
        return f"{self.manifest.name}: {store.root}"

    def snapshot(self, store: Store) -> dict[str, Any]:
        """{"title", "meta", "numbers": [(caption, text)], "lists": [(caption, [(text, path or "")])]}"""
        return {"title": self.manifest.name, "meta": store.root, "numbers": [], "lists": []}

    # -- the folder -----------------------------------------------------------------------------------------
    def root(self, ctx) -> str:
        return (ctx.settings.get("folder") or "").strip() or os.path.join(ctx.workspace_root, self.FOLDER)

    def ensure_store(self, ctx) -> Store:
        root = self.root(ctx)
        if not self.STORE.is_set_up(root):
            self.STORE.create(root, **{k: ctx.settings.get(k, v) for k, v in self.DEFAULTS.items()})
        return self.stores.get(root)

    def workspace_for(self, meta: dict[str, Any], ctx) -> str:
        return self.ensure_store(ctx).root

    # -- tasks ------------------------------------------------------------------------------------------------
    def workflow(self, workflow_id: str) -> Workflow:
        for wf in self.WORKFLOWS:
            if wf.id == workflow_id:
                return wf
        raise KeyError(f"no workflow {workflow_id!r} in {self.id}")

    def task_for(self, workflow_id: str, root: str, extra: Optional[dict[str, Any]] = None):
        wf = self.workflow(workflow_id)
        t = next(t for t in self.TEMPLATES if t.id == wf.template_id)
        task = Task(title=wf.name, goal=wf.goal, workspace=root, kind=TaskKind.GENERAL, worker_type=t.worker_type,
                    metadata={"extension": self.id, "template": t.id, "workflow": wf.id, **(extra or {})})
        return task, t.spec(wf.goal, workspace=root, name=t.name)

    def scheduled_jobs(self, ctx) -> list[Any]:
        wanted = [wf for wf in self.WORKFLOWS if isinstance(wf.trigger, Schedule) and ctx.settings.get(f"auto_{wf.id}")]
        if not wanted:
            return []
        from agents.manager.scheduler import ScheduledJob
        root = self.ensure_store(ctx).root
        return [ScheduledJob(id=f"{self.id}:{wf.id}", name=wf.name, trigger=wf.trigger, owner=self.id,
                             make_task=lambda extra, w=wf.id: self.task_for(w, root, extra)) for wf in wanted]

    def on_disable(self, ctx) -> None:
        self.stores.drop()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:60] or "untitled"
