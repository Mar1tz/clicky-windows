"""Every job leaves a trace (OB-01, after Langfuse's data model).

tora.log is silent about most job failures; agents.db has the steps but
not the time, the tokens or the money each part took. A trace does:

  trace       one per job: its goal, how it ended, how long, tokens, cost
  span        a step, a tool call, a wait - nested, timed, its input and
              output cut short and redacted
  generation  a model call: the model, tokens in and out, cost, latency
  score       a judgement: the verifier's pass or fail, later a thumbs-up

All of it is built by listening to the EventBus - no worker or tool code
changes - and kept on this PC in traces.db, nothing sent anywhere. Keys a
tool printed are blanked (logsafe.redact) before a row is written.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from typing import Any, Optional

from agents.core import Event, EventBus, EventKind, TaskStatus

from .prices import credits, price_usd

log = logging.getLogger("agents.telemetry")

MAX_TEXT = 500

_SCHEMA = """
create table if not exists traces (
    id text primary key, kind text, goal text, outcome text default '', started real, ended real,
    tokens integer default 0, cost_usd real default 0);
create table if not exists spans (
    id text primary key, trace_id text, parent_id text, name text, kind text, started real, ended real,
    status text default '', input text default '', output text default '');
create table if not exists generations (
    id integer primary key autoincrement, trace_id text, span_id text, model text, prompt_tokens integer,
    completion_tokens integer, cost_usd real, latency_ms integer, at real);
create table if not exists scores (
    id integer primary key autoincrement, trace_id text, name text, value real, source text,
    comment text default '', at real);
create table if not exists cases (
    id integer primary key autoincrement, dataset text, trace_id text default '', question text default '',
    window text default '', tools text default '[]', answer text default '', note text default '', at real);
create index if not exists spans_trace on spans(trace_id);
create index if not exists gens_trace on generations(trace_id);
"""


def _clip(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = text[:MAX_TEXT]
    try:
        from logsafe import redact
        return redact(text)
    except Exception:  # noqa: BLE001 - outside the app: cut, not redacted
        return text


class TraceStore:
    def __init__(self, path: str = ":memory:") -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)

    def _x(self, sql: str, args: tuple = ()) -> None:
        with self._lock:
            self._db.execute(sql, args)
            self._db.commit()

    def _q(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    # -- writing ---------------------------------------------------------------

    def open_trace(self, trace_id: str, kind: str, goal: str) -> None:
        self._x("insert or ignore into traces (id, kind, goal, started) values (?,?,?,?)",
                (trace_id, kind, _clip(goal), time.time()))

    def close_trace(self, trace_id: str, outcome: str) -> None:
        self._x("update traces set outcome=?, ended=? where id=?", (outcome, time.time(), trace_id))

    def open_span(self, span_id: str, trace_id: str, name: str, kind: str, *, parent_id: str = "",
                  input: Any = "") -> None:
        self._x("insert or replace into spans (id, trace_id, parent_id, name, kind, started, input) "
                "values (?,?,?,?,?,?,?)", (span_id, trace_id, parent_id, name, kind, time.time(), _clip(input)))

    def close_span(self, span_id: str, status: str, output: Any = "") -> None:
        self._x("update spans set ended=?, status=?, output=? where id=?",
                (time.time(), status, _clip(output), span_id))

    def generation(self, trace_id: str, model: str, prompt_tokens: int, completion_tokens: int, *,
                   latency_ms: int = 0, span_id: str = "") -> float:
        cost = price_usd(model, prompt_tokens, completion_tokens)
        self._x("insert into generations (trace_id, span_id, model, prompt_tokens, completion_tokens, cost_usd, "
                "latency_ms, at) values (?,?,?,?,?,?,?,?)",
                (trace_id, span_id, model, prompt_tokens, completion_tokens, cost, latency_ms, time.time()))
        self._x("update traces set tokens = tokens + ?, cost_usd = cost_usd + ? where id=?",
                (prompt_tokens + completion_tokens, cost, trace_id))
        return cost

    def voice_turn(self, route: str, outcome: str, phases: dict, over: list | None = None,
                   total_s: float = 0.0) -> str:
        """One spoken question as a trace: each phase (mic, first word, a
        look...) a span from the release. No words, only timings."""
        from agents.core import new_id
        tid, end = new_id(), time.time()
        start = end - max(total_s, 0.0)
        self._x("insert into traces (id, kind, goal, outcome, started, ended) values (?,?,?,?,?,?)",
                (tid, "voice", route, outcome, start, end))
        for phase, took in (phases or {}).items():
            self._x("insert into spans (id, trace_id, parent_id, name, kind, started, ended, status) "
                    "values (?,?,?,?,?,?,?,?)", (new_id(), tid, "", phase, "phase", start, start + float(took),
                                                 "over budget" if phase in (over or []) else "ok"))
        return tid

    # -- OB-02: what went wrong, kept as a test case ----------------------------

    def flag_case(self, dataset: str, *, question: str = "", window: str = "", tools: list | None = None,
                  answer: str = "", note: str = "", trace_id: str = "") -> int:
        """A turn or a job that went wrong, for the evals (tools/voice_eval.py
        --flagged) to try again on every change. Kept on this PC."""
        with self._lock:
            cur = self._db.execute(
                "insert into cases (dataset, trace_id, question, window, tools, answer, note, at) "
                "values (?,?,?,?,?,?,?,?)",
                (dataset, trace_id, _clip(question), _clip(window), json.dumps(list(tools or [])), _clip(answer),
                 _clip(note), time.time()))
            self._db.commit()
            return int(cur.lastrowid)

    def cases(self, dataset: str = "") -> list[dict]:
        rows = self._q("select * from cases" + (" where dataset=?" if dataset else "") + " order by at",
                       (dataset,) if dataset else ())
        for r in rows:
            r["tools"] = json.loads(r.get("tools") or "[]")
        return rows

    def score(self, trace_id: str, name: str, value: float, source: str, comment: str = "") -> None:
        self._x("insert into scores (trace_id, name, value, source, comment, at) values (?,?,?,?,?,?)",
                (trace_id, name, float(value), source, _clip(comment), time.time()))

    # -- reading ---------------------------------------------------------------

    def trace(self, trace_id: str) -> Optional[dict]:
        rows = self._q("select * from traces where id=?", (trace_id,))
        if not rows:
            return None
        t = rows[0]
        t["spans"] = self._q("select * from spans where trace_id=? order by started", (trace_id,))
        t["generations"] = self._q("select * from generations where trace_id=? order by at", (trace_id,))
        t["scores"] = self._q("select * from scores where trace_id=? order by at", (trace_id,))
        t["credits"] = credits(t.get("cost_usd") or 0.0)
        if t.get("ended") and t.get("started"):
            t["ms"] = int((t["ended"] - t["started"]) * 1000)
        return t

    def cost(self, trace_id: str) -> tuple[int, float]:
        rows = self._q("select tokens, cost_usd from traces where id=?", (trace_id,))
        return (int(rows[0]["tokens"] or 0), float(rows[0]["cost_usd"] or 0.0)) if rows else (0, 0.0)

    def day(self, since: float) -> dict:
        """A day's scorecard: jobs, how they ended, tokens, cost."""
        rows = self._q("select outcome, count(*) n, sum(tokens) tokens, sum(cost_usd) usd from traces "
                       "where started >= ? group by outcome", (since,))
        usd = sum(r["usd"] or 0 for r in rows)
        return {"jobs": sum(r["n"] for r in rows), "by_outcome": {r["outcome"] or "running": r["n"] for r in rows},
                "tokens": sum(r["tokens"] or 0 for r in rows), "cost_usd": round(usd, 4), "credits": credits(usd)}


class Tracer:
    """Turns the job events into traces. Only a subscriber: nothing else changes."""

    def __init__(self, store: TraceStore, events: EventBus) -> None:
        self.store = store
        self._step: dict[str, str] = {}             # task id -> its running step's span
        self._unsub = events.subscribe(self._on)

    def stop(self) -> None:
        self._unsub()

    def _on(self, ev: Event) -> None:
        try:
            self._handle(ev)
        except Exception:  # noqa: BLE001 - tracing must never break a job
            log.debug("trace event dropped", exc_info=True)

    def _handle(self, ev: Event) -> None:
        tid, data, kind = ev.task_id, ev.data or {}, ev.kind
        if not tid:
            return
        s = self.store
        if kind is EventKind.TASK_CREATED:
            task = data.get("task") or {}
            s.open_trace(tid, "project" if data.get("project") else "job", task.get("goal") or ev.message)
        elif kind is EventKind.STEP_STARTED:
            step = data.get("step") or {}
            sid = step.get("id") or f"{tid}:{data.get('index')}"
            self._step[tid] = sid
            s.open_span(sid, tid, step.get("title") or ev.message, "step")
        elif kind in (EventKind.STEP_DONE, EventKind.STEP_FAILED):
            step = data.get("step") or {}
            sid = step.get("id") or self._step.get(tid, "")
            s.close_span(sid, "done" if kind is EventKind.STEP_DONE else "failed",
                         step.get("summary") or step.get("error") or ev.message)
        elif kind is EventKind.TOOL_CALL:
            call = data.get("call") or {}
            s.open_span(call.get("id") or "", tid, call.get("tool_name") or ev.message, "tool",
                        parent_id=self._step.get(tid, ""), input=call.get("arguments") or {})
        elif kind is EventKind.TOOL_RESULT:
            res = data.get("result") or {}
            s.close_span(res.get("call_id") or "", "ok" if res.get("ok") else "failed",
                         res.get("output") or res.get("error") or "")
        elif kind is EventKind.WAITPOINT:
            wp = data.get("waitpoint") or {}
            if wp.get("status") == "waiting":
                s.open_span(wp.get("id") or "", tid, f"wait {wp.get('kind')}: {wp.get('prompt', '')}", "wait",
                            parent_id=self._step.get(tid, ""))
            else:
                s.close_span(wp.get("id") or "", wp.get("status") or "", wp.get("output") or "")
        elif kind is EventKind.USAGE:
            s.generation(tid, str(data.get("model") or ""), int(data.get("prompt_delta") or 0),
                         int(data.get("completion_delta") or 0) or
                         (0 if data.get("prompt_delta") else int(data.get("delta") or 0)),
                         latency_ms=int(data.get("latency_ms") or 0), span_id=self._step.get(tid, ""))
        elif kind is EventKind.VERIFICATION:
            result = data.get("result") or {}
            s.score(tid, "verified" if data.get("scope") == "task" else "step_verified",
                    1.0 if result.get("ok") else 0.0, "verifier", result.get("summary") or ev.message)
        elif kind is EventKind.TASK_STATUS:
            status = str(data.get("status") or "")
            try:
                ended = TaskStatus(status).is_terminal
            except ValueError:
                ended = False
            if ended:
                s.close_trace(tid, status)
                self._step.pop(tid, None)
