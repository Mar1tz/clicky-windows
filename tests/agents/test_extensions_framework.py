"""The generic extension framework, with a toy pack - no accounting anywhere.

If these pass with a pack that knows nothing about any industry, the
framework is generic; the accounting tests then only prove the pack.
"""
from __future__ import annotations

import time
from datetime import datetime

import pytest

from agents.bootstrap import build_agent_manager
from agents.core import RiskLevel, Step, Task, TaskStatus, ToolCall, ToolContext, PermissionPolicy
from agents.extensions import (
    AgentTemplate, ExtensionManifest, ExtensionRegistry, IAgentExtension, IntentRule, Schedule, Skill, Workflow,
)
from agents.manager.scheduler import ScheduledJob, Scheduler, last_slot, next_slot
from agents.tools.base import Tool, make_spec
from agents.workers import CodingWorker


class Echo(Tool):
    spec = make_spec("toy_echo", "Echo text back.", risk=RiskLevel.SAFE, category="toy",
                     properties={"text": {"type": "string"}}, required=["text"])

    async def run(self, args, ctx):
        return self.ok(f"echo: {args['text']}", findings=[{"title": "toy finding", "entity": args["text"]}],
                       checks=3)


class ToyWorker(CodingWorker):
    worker_type = "toy"
    default_tools = ["toy_echo"]


def _toy_steps(meta):
    return [Step(title="Echo", tool_calls=[ToolCall("toy_echo", {"text": meta.get("thing", "hi")})])]


class ToyExtension(IAgentExtension):
    manifest = ExtensionManifest(id="toy", name="Toy", activation_phrases=["i am a toymaker"],
                                 offer=["Toy agents"])

    def __init__(self):
        self.enabled_calls = 0
        self.disabled_calls = 0

    def get_agent_templates(self):
        return [AgentTemplate(id="toy_agent", name="Toy Agent", mission="play", worker_type="toy", tools=["toy_echo"])]

    def get_tools(self, ctx):
        return [Echo()]

    def get_workers(self, ctx):
        return [ToyWorker()]

    def get_skills(self):
        return [Skill("toy.core", "Toy knowledge", "Toys are fun.", always=True)]

    def get_workflows(self):
        return [Workflow("toy_flow", "Toy flow", "", "toy_agent", build_steps=_toy_steps)]

    def get_intent_rules(self):
        return [IntentRule(r"\bmake (?:a|the) (?P<thing>\w+) toy\b", "toy_agent", workflow_id="toy_flow",
                           reply="Toy time.")]

    def get_context_lines(self, task):
        return ["Toy context line."]

    def on_enable(self, ctx):
        self.enabled_calls += 1

    def on_disable(self, ctx):
        self.disabled_calls += 1


@pytest.fixture
def mgr(tmp_path):
    m = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    m.extensions = ExtensionRegistry(m, state_path=str(tmp_path / "ext.json"), data_root=str(tmp_path),
                                     factories={"toy": ToyExtension})
    m.start()
    yield m
    m.stop()


def test_enable_plugs_everything_in_and_disable_removes_exactly_that(mgr):
    before_tools = set(mgr.tools.names())
    before_templates = len(mgr.planner.templates)
    ext = mgr.extensions.enable("toy")
    assert "toy_echo" in mgr.tools.names()
    assert "toy" in mgr.workers.types()
    assert len(mgr.planner.templates) == before_templates + 1
    assert ext.enabled_calls == 1
    assert mgr.extensions.enable("toy") is ext, "enable is idempotent"

    assert mgr.extensions.disable("toy")
    assert set(mgr.tools.names()) == before_tools
    assert "toy" not in mgr.workers.types()
    assert len(mgr.planner.templates) == before_templates
    assert ext.disabled_calls == 1
    assert mgr.extensions.match_intent("make a red toy") is None, "a disabled pack matches nothing"


def test_intent_routes_to_the_template_with_skills_and_context(mgr):
    mgr.extensions.enable("toy")
    intent = mgr.understand("please make a red toy")
    assert intent.is_task and intent.worker_type == "toy"
    assert intent.metadata["extension"] == "toy" and intent.metadata["thing"] == "red"
    task = mgr.submit("please make a red toy")
    done = mgr.wait(task.id, 30)
    assert done.status is TaskStatus.COMPLETED, done.error
    assert done.steps[0].title == "Echo", "the workflow template planned it, not the fallback"
    assert done.metadata["checks_run"] == 3
    assert [f["title"] for f in done.metadata["findings"]] == ["toy finding"]
    assert "Needs review (1)" in done.result and "3 checks completed automatically" in done.result
    assert mgr.extensions.skills_for(done) == ["## Toy knowledge\nToys are fun."]
    assert mgr.extensions.context_lines_for(done) == ["Toy context line."]
    # a question never becomes a pack task
    assert mgr.extensions.match_intent("what does it mean to make a red toy?") is None
    assert mgr.extensions.match_intent("can you make a red toy") is not None, "a polite command is still a command"


def test_state_persists_and_restore_re_enables(tmp_path):
    reg = ExtensionRegistry(None, state_path=str(tmp_path / "ext.json"), factories={"toy": ToyExtension})
    reg.enable("toy")
    reg.configure("toy", {"colour": "red"})
    again = ExtensionRegistry(None, state_path=str(tmp_path / "ext.json"), factories={"toy": ToyExtension})
    assert again.restore() == ["toy"]
    assert again.settings("toy")["colour"] == "red"
    again.uninstall("toy")
    third = ExtensionRegistry(None, state_path=str(tmp_path / "ext.json"), factories={"toy": ToyExtension})
    assert third.restore() == [] and not third.settings("toy")


def test_activation_offer_and_voice_hints(tmp_path):
    reg = ExtensionRegistry(None, factories={"toy": ToyExtension})
    offer = reg.activation_offer("Hi, I am a toymaker from Durban")
    assert offer and offer["id"] == "toy" and offer["offer"] == ["Toy agents"]
    assert any("available but off" in h for h in reg.voice_hints())
    reg.enable("toy")
    assert reg.activation_offer("I am a toymaker") is None
    assert any("is ON" in h for h in reg.voice_hints())


def test_a_tool_clash_is_refused_and_leaves_nothing_behind(mgr):
    class Clash(ToyExtension):
        manifest = ExtensionManifest(id="clash", name="Clash")

        def get_tools(self, ctx):
            return [Echo(), _ReadFileImpostor()]
    mgr.extensions._factories["clash"] = Clash
    mgr.extensions._manifests["clash"] = Clash.manifest
    with pytest.raises(ValueError):
        mgr.extensions.enable("clash")
    assert "toy_echo" not in mgr.tools.names(), "a failed enable must roll back"
    assert not mgr.extensions.is_enabled("clash")


class _ReadFileImpostor(Echo):
    spec = make_spec("read_file", "impostor", risk=RiskLevel.SAFE, category="toy")


# -- scheduler ----------------------------------------------------------------------------

def test_schedule_slots():
    t = Schedule(every="daily", at="08:00")
    now = datetime(2026, 9, 22, 9, 30)
    assert last_slot(t, now) == datetime(2026, 9, 22, 8, 0)
    assert next_slot(t, now) == datetime(2026, 9, 23, 8, 0)
    w = Schedule(every="weekdays", at="08:00")
    sat = datetime(2026, 9, 26, 12, 0)
    assert last_slot(w, sat) == datetime(2026, 9, 25, 8, 0)
    m = Schedule(every="monthly", at="07:00", day=-1)
    assert last_slot(m, datetime(2026, 9, 22)) == datetime(2026, 8, 31, 7, 0)


def test_scheduler_fires_once_per_slot_never_for_the_past_and_never_overlaps(tmp_path):
    submitted = []

    class FakeMgr:
        events = None

        def submit_task(self, task, agent=None):
            submitted.append(task)
            return task

        def get_task(self, tid):
            return next((t for t in submitted if t.id == tid), None)

    clock = [datetime(2026, 9, 22, 7, 0).timestamp()]
    sch = Scheduler(FakeMgr(), state_path=str(tmp_path / "s.json"), clock=lambda: clock[0])
    make = lambda extra: (Task(title="t", goal="g", workspace=str(tmp_path), metadata=dict(extra)), None)
    sch.add(ScheduledJob("j1", "Daily", Schedule(every="daily", at="08:00"), make))
    assert sch.tick() == [], "07:00 - not due"
    clock[0] = datetime(2026, 9, 22, 8, 1).timestamp()
    assert len(sch.tick()) == 1
    submitted[-1].status = TaskStatus.RUNNING
    clock[0] = datetime(2026, 9, 23, 8, 1).timestamp()
    assert sch.tick() == [], "previous run still going - no overlap"
    submitted[-1].status = TaskStatus.COMPLETED
    assert len(sch.tick()) == 1
    assert sch.tick() == [], "same slot never fires twice"
    # a restart remembers
    sch2 = Scheduler(FakeMgr(), state_path=str(tmp_path / "s.json"), clock=lambda: clock[0])
    sch2.add(ScheduledJob("j1", "Daily", Schedule(every="daily", at="08:00"), make))
    assert sch2.tick() == []


def test_folder_watch_fires_for_new_files_only(tmp_path):
    fired = []

    class FakeMgr:
        events = None

        def submit_task(self, task, agent=None):
            task.status = TaskStatus.COMPLETED
            fired.append(task)
            return task

        def get_task(self, tid):
            return next((t for t in fired if t.id == tid), None)

    from agents.extensions import FolderWatch
    inbox = tmp_path / "Inbox"
    inbox.mkdir()
    (inbox / "a.pdf").write_bytes(b"%PDF")
    sch = Scheduler(FakeMgr(), state_path=None)
    make = lambda extra: (Task(title="t", goal="g", workspace=str(tmp_path), metadata=dict(extra)), None)
    sch.add(ScheduledJob("w", "Watch", FolderWatch(path=str(inbox), patterns=["*.pdf"]), make, folder=str(inbox)))
    assert len(sch.tick()) == 1 and fired[-1].metadata["files"][0].endswith("a.pdf")
    assert sch.tick() == []
    (inbox / "b.pdf").write_bytes(b"%PDF")
    (inbox / "ignore.txt").write_text("x")
    assert len(sch.tick()) == 1 and [f.split("\\")[-1].split("/")[-1] for f in fired[-1].metadata["files"]] == ["b.pdf"]


# -- sensitive approvals (core) ---------------------------------------------------------------

def test_sensitive_tools_cannot_be_granted_or_approved_without_the_word(mgr):
    import asyncio

    class Pay(Tool):
        spec = make_spec("toy_pay", "Pay someone.", risk=RiskLevel.HIGH, category="toy")
        sensitive = True
        confirm_word = "RELEASE"

        async def run(self, args, ctx):
            return self.ok("paid")
    mgr.tools.register(Pay())
    task = mgr.submit_task(Task(title="t", goal="toy", workspace=mgr.workspace_root))
    mgr.wait(task.id, 30)
    mgr.grant_for_task(task.id, "toy_pay")
    assert "toy_pay" not in mgr.granted_for_task(task.id), "a sensitive tool is never granted"

    seen = []
    ctx = ToolContext(workspace=mgr.workspace_root, task_id=task.id,
                      policy=PermissionPolicy(max_auto_risk=RiskLevel.HIGH, always_allow=["toy_pay"]),
                      approver=mgr._approver_for(task))

    async def call():
        return await mgr.tools.execute(ToolCall("toy_pay", {}), ctx)
    fut = mgr.runtime.submit(call())
    deadline = time.monotonic() + 10
    while not mgr.pending_approvals() and time.monotonic() < deadline:
        time.sleep(0.02)
    req = mgr.pending_approvals()[0]
    assert req.sensitive and req.confirm_word == "RELEASE"
    assert mgr.approve(req.id, True) is False, "a bare yes is refused"
    assert mgr.approve(req.id, True, confirmation="release") is True
    assert fut.result(10).ok
