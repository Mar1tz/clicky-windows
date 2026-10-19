"""M17: model-backed verification, and the rules/model split.

The gate is `test_empty_file_is_caught_by_the_model`: RuleVerifier passes
a file that exists, so an agent that writes an empty App.jsx completes
"successfully". The model reads the disk and fails it.
"""
from __future__ import annotations

import os

import pytest

from agents.core import (
    AgentContext, AgentProvider, AgentSpec, Check, ProviderCapabilities,
    ProviderResponse, RunControl, Step, StepStatus, Task, ToolCall, ToolResult,
    ToolRegistry, VerificationResult, Verifier, EventBus,
)
from agents.verification import CompositeVerifier, ProviderVerifier, RuleVerifier


# -- doubles -----------------------------------------------------------------

class Say(AgentProvider):
    """A provider that answers with whatever text it was given."""
    name = "say"

    def __init__(self, text: str = '{"ok": true, "reason": "fine"}', fail: bool = False):
        self.text = text
        self.fail = fail
        self.prompts: list[str] = []

    async def complete(self, messages, tools=None, *, system="", model=None,
                       temperature=0.2, max_tokens=4096):
        self.prompts.append(messages[-1].content)
        if self.fail:
            raise RuntimeError("provider is down")
        return ProviderResponse(text=self.text)

    def capabilities(self): return ProviderCapabilities(local=True, cost_tier=0)


class NoTools(ToolRegistry):
    def register(self, tool): ...
    def get(self, name): raise KeyError(name)
    def names(self): return []
    def specs(self, names=None): return []
    async def execute(self, call, ctx): raise NotImplementedError


def context(tmp_path, steps, provider=None) -> AgentContext:
    task = Task(title="t", goal="Write a haiku to haiku.txt", workspace=str(tmp_path),
                steps=steps)
    return AgentContext(task=task, agent=AgentSpec(name="A", goal="g"), tools=NoTools(),
                        provider=provider or Say(), events=EventBus(), control=RunControl())


def wrote(path: str, tool: str = "create_file", ok: bool = True) -> ToolResult:
    return ToolResult(call_id="c", tool_name=tool, ok=ok, output="done", data={"path": path})


def step_writing(path: str, title: str = "Create haiku.txt") -> Step:
    return Step(title, status=StepStatus.DONE,
                tool_calls=[ToolCall("create_file", {"path": path, "content": "..."})],
                results=[wrote(path)], summary=f"Created {path}")


# -- evidence ----------------------------------------------------------------

async def test_prompt_shows_real_file_contents_not_the_agents_claim(tmp_path):
    (tmp_path / "haiku.txt").write_text("Bug hides in the code", encoding="utf-8")
    say = Say()
    step = step_writing("haiku.txt")
    await ProviderVerifier().verify_step(step, context(tmp_path, [step], say))
    prompt = say.prompts[0]
    assert "haiku.txt" in prompt
    assert "Bug hides in the code" in prompt, "the model must see what is actually on disk"
    assert "bytes" in prompt


async def test_prompt_marks_an_empty_file(tmp_path):
    (tmp_path / "haiku.txt").write_text("", encoding="utf-8")
    say = Say()
    step = step_writing("haiku.txt")
    await ProviderVerifier().verify_step(step, context(tmp_path, [step], say))
    assert "(EMPTY FILE)" in say.prompts[0]


async def test_prompt_marks_a_missing_file(tmp_path):
    say = Say()
    step = step_writing("gone.txt")
    await ProviderVerifier().verify_step(step, context(tmp_path, [step], say))
    assert "(DOES NOT EXIST)" in say.prompts[0]


async def test_bulk_arguments_are_summarised_not_dumped(tmp_path):
    (tmp_path / "big.txt").write_text("x" * 50, encoding="utf-8")
    say = Say()
    step = Step("Write big", status=StepStatus.DONE,
                tool_calls=[ToolCall("create_file", {"path": "big.txt", "content": "y" * 5000})],
                results=[wrote("big.txt")])
    await ProviderVerifier().verify_step(step, context(tmp_path, [step], say))
    assert "content=5000 chars" in say.prompts[0]
    assert "y" * 200 not in say.prompts[0]


# -- verdicts ----------------------------------------------------------------

async def test_model_can_fail_a_step(tmp_path):
    say = Say('{"ok": false, "reason": "haiku.txt is empty"}')
    step = step_writing("haiku.txt")
    result = await ProviderVerifier().verify_step(step, context(tmp_path, [step], say))
    assert result.ok is False
    assert "empty" in result.summary


async def test_model_json_in_a_code_fence_still_parses(tmp_path):
    say = Say('```json\n{"ok": true, "reason": "all good"}\n```')
    step = step_writing("haiku.txt")
    result = await ProviderVerifier().verify_step(step, context(tmp_path, [step], say))
    assert result.ok and result.summary == "all good"


async def test_unparseable_verdict_raises(tmp_path):
    """The composite turns this into 'use the rules'; alone it must not
    quietly return a pass."""
    say = Say("I think it's probably fine?")
    step = step_writing("haiku.txt")
    with pytest.raises(Exception):
        await ProviderVerifier().verify_step(step, context(tmp_path, [step], say))


# -- the rules / model split -------------------------------------------------

async def test_failing_rules_short_circuit_without_a_model_call(tmp_path):
    """A missing file is not a judgement call - do not pay for one."""
    say = Say('{"ok": true, "reason": "looks fine to me"}')
    step = step_writing("never_written.txt")
    result = await CompositeVerifier().verify_step(step, context(tmp_path, [step], say))
    assert result.ok is False
    assert say.prompts == [], "the model was asked about a plainly missing file"


async def test_model_is_asked_when_rules_are_silent(tmp_path):
    """'no rule' means nobody checked - that is the gap worth buying."""
    say = Say('{"ok": false, "reason": "nothing actually happened"}')
    step = Step("Think about it", status=StepStatus.DONE,
                tool_calls=[ToolCall("mystery_tool", {})],
                results=[ToolResult(call_id="c", tool_name="mystery_tool", ok=True,
                                    output="hmm")])
    result = await CompositeVerifier().verify_step(step, context(tmp_path, [step], say))
    assert say.prompts, "rules were silent and the model was not asked"
    assert result.ok is False


async def test_model_not_asked_when_rules_actually_checked(tmp_path):
    (tmp_path / "haiku.txt").write_text("real content", encoding="utf-8")
    say = Say()
    step = step_writing("haiku.txt")
    result = await CompositeVerifier().verify_step(step, context(tmp_path, [step], say))
    assert result.ok is True
    assert say.prompts == [], "rules checked it; a model call would be waste"


async def test_model_on_every_step_is_opt_in(tmp_path):
    (tmp_path / "haiku.txt").write_text("real content", encoding="utf-8")
    say = Say('{"ok": true, "reason": "genuinely a haiku"}')
    step = step_writing("haiku.txt")
    result = await CompositeVerifier(model_on_every_step=True).verify_step(
        step, context(tmp_path, [step], say))
    assert say.prompts, "model_on_every_step should force the call"
    assert result.ok and "genuinely a haiku" in result.summary


async def test_a_broken_model_degrades_to_the_rules(tmp_path):
    """A verifier that rubber-stamps when broken is worse than none."""
    (tmp_path / "haiku.txt").write_text("content", encoding="utf-8")
    step = step_writing("haiku.txt")
    ctx = context(tmp_path, [step], Say(fail=True))
    result = await CompositeVerifier(model_on_every_step=True).verify_step(step, ctx)
    assert result.ok is True                      # the rules' verdict, not an invention
    assert any("create_file" in c.name or "haiku" in c.name for c in result.checks)


async def test_final_task_check_always_asks_the_model(tmp_path):
    (tmp_path / "haiku.txt").write_text("content", encoding="utf-8")
    say = Say('{"ok": false, "reason": "the goal asked for three lines; there is one"}')
    step = step_writing("haiku.txt")
    result = await CompositeVerifier().verify_task(context(tmp_path, [step], say))
    assert say.prompts, "the verdict the user sees must get a model pass"
    assert result.ok is False
    assert "three lines" in result.summary


# -- the gate ----------------------------------------------------------------

async def test_empty_file_is_caught_by_the_model(tmp_path):
    """RuleVerifier passes a file that exists; an empty App.jsx would ship
    as COMPLETED. This is the case the model layer exists for."""
    (tmp_path / "App.jsx").write_text("", encoding="utf-8")
    step = Step("Create App.jsx", status=StepStatus.DONE,
                tool_calls=[ToolCall("create_file", {"path": "App.jsx", "content": ""})],
                results=[wrote("App.jsx")])

    rules_only = await RuleVerifier().verify_step(step, context(tmp_path, [step]))
    assert rules_only.ok is True, "precondition: rules alone accept an empty file"

    say = Say('{"ok": false, "reason": "App.jsx is empty"}')
    judged = await CompositeVerifier(model_on_every_step=True).verify_step(
        step, context(tmp_path, [step], say))
    assert judged.ok is False
    assert "empty" in judged.summary


# -- AG-03: the output on disk is what the judge sees -------------------------------------------------------------

async def test_a_pdf_written_into_a_busy_folder_is_shown_by_name_and_kind(tmp_path):
    """Two PDF jobs failed because the judge saw only "(directory, 32 entries)"."""
    import time as _time
    docs = tmp_path / "Documents"
    docs.mkdir()
    for i in range(32):
        (docs / f"old-{i}.txt").write_text("x", encoding="utf-8")
        os.utime(docs / f"old-{i}.txt", (_time.time() - 3600, _time.time() - 3600))
    (docs / "10 Mei 2026.docx").write_bytes(b"PK\x03\x04 a word document")
    os.utime(docs / "10 Mei 2026.docx", (_time.time() - 3600, _time.time() - 3600))
    pdf = docs / "10 Mei 2026.pdf"
    pdf.write_bytes(b"%PDF-1.7\n" + b"\x00" * 4000)
    command = (f'$w.Documents.Open("{docs / "10 Mei 2026.docx"}").SaveAs("{pdf}", 17)')
    step = Step("Convert to PDF", status=StepStatus.DONE,
                tool_calls=[ToolCall("run_command", {"command": command, "cwd": str(docs)})],
                results=[ToolResult(call_id="c", tool_name="run_command", ok=True, output="",
                                    data={"path": str(docs)})])
    say = Say()
    ctx = context(tmp_path, [step], say)
    ctx.task.started_at = _time.time() - 60
    await ProviderVerifier().verify_step(step, ctx)
    prompt = say.prompts[0]
    assert "10 Mei 2026.pdf  (4009 bytes, PDF document)" in prompt, "named in the command: found, sized, typed"
    assert "changed during this job: 10 Mei 2026.pdf (4009 bytes, PDF document)" in prompt, "the folder, by name"
    assert "old-3.txt" not in prompt and "%PDF" not in prompt, "only what changed; a PDF is never read as text"


async def test_an_output_the_command_named_but_never_wrote_says_so(tmp_path):
    missing = tmp_path / "report.pdf"
    step = Step("Export", status=StepStatus.DONE,
                tool_calls=[ToolCall("run_command", {"command": f'export --to "{missing}"'})],
                results=[ToolResult(call_id="c", tool_name="run_command", ok=True, output="")])
    say = Say()
    await ProviderVerifier().verify_step(step, context(tmp_path, [step], say))
    assert "report.pdf  (DOES NOT EXIST)" in say.prompts[0]
