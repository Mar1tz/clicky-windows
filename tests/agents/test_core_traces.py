"""OB-01: every job leaves a trace - its steps and tool calls timed, each
model call with tokens and cost, the verifier's judgement - built from the
events alone, kept on this PC, keys blanked; a job can be capped in money.
"""
from __future__ import annotations

import pytest

from agents.bootstrap import build_agent_manager
from agents.core import EventBus, EventKind, Task, TaskStatus
from agents.manager.limits import LimitExceeded, MeteredProvider, TaskLimits
from agents.telemetry import Tracer, TraceStore, credits, price_usd

REQUEST = "Create a new folder called MyApp and build the foundation of a React application inside it."


def test_prices_follow_the_model_family():
    assert price_usd("gemini-3.8-flash", 1_000_000, 0) == 0.5
    assert price_usd("gemini-3.8-flash-lite", 0, 1_000_000) == 0.6
    assert price_usd("claude-sonnet-4.6", 1_000_000, 1_000_000) == 18.0
    assert price_usd("mystery-model-9", 1_000_000, 0) == 15.0, "unknown: priced high, never low"
    assert price_usd("mock", 10_000, 10_000) == 0.0 and credits(0.0123) == 1.23


def test_a_job_is_traced_from_its_events(tmp_path):
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True).start()
    try:
        task = mgr.wait(mgr.submit(REQUEST).id, 60)
        assert task.status is TaskStatus.COMPLETED
        t = mgr.tracer.store.trace(task.id)
        assert t["kind"] == "job" and t["outcome"] == "completed" and t["ms"] >= 0
        steps = [s for s in t["spans"] if s["kind"] == "step"]
        tools = [s for s in t["spans"] if s["kind"] == "tool"]
        assert len(steps) == len(task.steps) and all(s["status"] == "done" for s in steps)
        assert tools and all(s["parent_id"] in {st["id"] for st in steps} for s in tools)
        assert any(sc["name"] == "verified" and sc["value"] == 1.0 for sc in t["scores"])
        assert mgr.tracer.store.day(0)["by_outcome"]["completed"] >= 1
    finally:
        mgr.stop()


def test_each_model_call_is_priced_and_a_job_can_be_capped():
    bus = EventBus()
    store = TraceStore()
    Tracer(store, bus)
    task = Task(title="t", goal="g", workspace=".")
    bus.emit(EventKind.TASK_CREATED, "t", task_id=task.id, data={"task": task.to_dict()})
    metered = MeteredProvider(inner=type("P", (), {"name": "gemini"})(), task=task, events=bus,
                              limits=TaskLimits(max_cost_usd=0.001))
    metered.record(1500, model="gemini-3.8-flash", prompt=1000, completion=500, latency_ms=420)
    assert task.metadata["usage"]["cost_usd"] == pytest.approx(0.002)
    t = store.trace(task.id)
    [gen] = t["generations"]
    assert (gen["model"], gen["prompt_tokens"], gen["completion_tokens"], gen["latency_ms"]) == \
        ("gemini-3.8-flash", 1000, 500, 420)
    assert t["tokens"] == 1500 and t["cost_usd"] == pytest.approx(0.002) and t["credits"] == 0.2
    with pytest.raises(LimitExceeded) as over:
        metered.limits.check(task)
    assert over.value.which == "cost"


def test_keys_never_reach_a_trace():
    bus = EventBus()
    store = TraceStore()
    Tracer(store, bus)
    bus.emit(EventKind.TASK_CREATED, "t", task_id="j1", data={"task": {"goal": "g"}})
    bus.emit(EventKind.TOOL_CALL, "read", task_id="j1",
             data={"call": {"id": "c1", "tool_name": "read_file", "arguments": {"path": ".env"}}})
    bus.emit(EventKind.TOOL_RESULT, "ok", task_id="j1",
             data={"result": {"call_id": "c1", "ok": True, "output": "KEY=AIza" + "q" * 35}})
    [span] = store.trace("j1")["spans"]
    assert "AIza" + "q" * 35 not in span["output"] and span["status"] == "ok"


def test_a_spoken_question_is_a_voice_trace_of_timings_only():
    from ai import speed
    store = TraceStore()
    speed.on_turn = lambda rec: store.voice_turn(**rec)
    try:
        speed.begin("live")
        speed.mark("press")
        speed.mark("release")
        speed.mark("audio")
        speed.finish("answered")
    finally:
        speed.on_turn = None
    [t] = [r for r in store._q("select * from traces") if r["kind"] == "voice"]
    assert t["outcome"] == "answered" and t["goal"] == "live", "the route, never the words"
    names = {s["name"] for s in store._q("select * from spans where trace_id=?", (t["id"],))}
    assert "first word" in names


# -- OB-02: what went wrong, kept as a test case ------------------------------------

def test_a_corrected_answer_becomes_a_voice_case():
    import asyncio
    import time
    from types import SimpleNamespace
    import companion_manager as cm
    store = TraceStore()
    c = cm.CompanionManager.__new__(cm.CompanionManager)
    c._agents = SimpleNamespace(tracer=SimpleNamespace(store=store))

    async def point(args):
        return {"ok": True}
    asyncio.run(c._noted("point_at", point)({}))
    assert c._answer_tools == ["point_at"]
    c._last_turn = {"question": "where is export?", "answer": "Top right.", "window": "Report - Excel",
                    "tools": list(c._answer_tools), "at": time.monotonic()}
    assert c._flag_last_turn("they corrected it: no, the other one")
    [case] = store.cases("voice")
    assert (case["question"], case["window"], case["tools"]) == ("where is export?", "Report - Excel", ["point_at"])
    assert not c._flag_last_turn("again"), "one turn, one case"


def test_a_job_that_went_wrong_is_flagged_by_voice(tmp_path):
    import asyncio
    import companion_manager as cm
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True).start()
    try:
        task = mgr.wait(mgr.submit(REQUEST).id, 60)
        c = cm.CompanionManager.__new__(cm.CompanionManager)
        c._agents = mgr
        out = asyncio.run(c._tool_manage_agents({"action": "flag", "answer": "it made the wrong folder"}))
        assert out["ok"]
        [case] = mgr.tracer.store.cases("job")
        assert case["trace_id"] == task.id and case["note"] == "it made the wrong folder"
    finally:
        mgr.stop()


def test_the_voice_bench_reads_the_flagged_cases(tmp_path, monkeypatch):
    import sys
    from pathlib import Path
    import config
    # The bench sets TORA_ACCESS when imported; monkeypatch puts it back after.
    monkeypatch.delenv("TORA_ACCESS", raising=False)
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "tools"))
    monkeypatch.delitem(sys.modules, "voice_eval", raising=False)
    store = TraceStore(str(config.data_dir() / "traces.db"))
    store.flag_case("voice", question="what should I click?", window="Checkout - Chrome", tools=["point_at"])
    import importlib
    try:
        voice_eval = importlib.import_module("voice_eval")
    except Exception as exc:  # noqa: BLE001 - the bench needs the app's audio stack
        pytest.skip(f"voice_eval does not import here: {exc}")
    [case] = voice_eval.flagged_cases()
    assert case.question == "what should I click?" and "*" in case.expect
    assert voice_eval.check(case, "Click Pay.", "Click Pay.", ["look_at_screen"])["tool"]
