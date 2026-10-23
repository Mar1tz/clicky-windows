"""The Log Hub: every allowed terminal, task and debug console, queryable.

"Why did the build fail?" should not need anyone to scroll a terminal. Each
stream's output arrives from the connector in chunks; here it becomes
clean lines (escape codes and progress-bar overwrites gone, secrets
redacted, a level on each), goes into the store's ring buffer and its
full-text index, and is read by rules that pull out what matters - a
failed test with its file and line, a compiler error, a port already in
use, a crash. Those become timeline events, so "what changed since it last
worked?" has an answer, and when a command ends its errors are summed up in
one line ("3 errors, all TS2322 in billing/") instead of 2,000.

Rules, not a model: they are instant, free, and the same every time. The
model gets the summary and can tail or search for the rest.
"""
from __future__ import annotations

import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from .redact import redact
from .store import Store

MAX_LINE = 2000
MAX_EXTRACTED = 50                 # events one command may add to the timeline

_ANSI = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-Z\\-_]")
# "TypeError:" and "npm ERR!" are errors too: no word boundary before the
# one or after the other, so the edges are drawn by hand.
_ERROR = re.compile(r"error\b|\bERR!|\bfatal\b|exception\b|\btraceback\b|\bfail(?:ed|ure)?\b|\bpanic\b", re.I)
_WARN = re.compile(r"warn(?:ing)?\b|\bdeprecat", re.I)

# (kind, pattern) - the named groups become the event's data.
_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("test.failed", re.compile(r"^FAILED (?P<file>[^\s:]+)::(?P<name>\S+)(?: - (?P<message>.*))?$")),
    ("test.failed", re.compile(r"^\s*FAIL\s+(?P<file>\S+\.(?:[jt]sx?|mjs|cjs))\b")),
    ("test.failed", re.compile(r"^\s*●\s+(?P<name>.+? › .+)$")),
    ("test.failed", re.compile(r"^\s*--- FAIL: (?P<name>\S+)")),
    ("build.error", re.compile(r"^(?P<file>[^\s(][^(]*?)\((?P<line>\d+),(?P<col>\d+)\): error (?P<code>TS\d+): "
                               r"(?P<message>.*)$")),
    ("build.error", re.compile(r"^(?P<file>\S+?):(?P<line>\d+):(?P<col>\d+) - error (?P<code>TS\d+): "
                               r"(?P<message>.*)$")),
    ("build.error", re.compile(r"^(?P<file>[^\s:][^:]*?):(?P<line>\d+):(?P<col>\d+): (?:fatal )?error: "
                               r"(?P<message>.*)$")),
    ("build.error", re.compile(r"^error(?:\[(?P<code>E\d+)\])?: (?P<message>.+)$")),
    ("port.in_use", re.compile(r"EADDRINUSE.*?:(?P<port>\d{2,5})|address already in use.*?(?P<port2>\d{2,5})|"
                               r"[Pp]ort (?P<port3>\d{2,5}) is (?:already )?in use")),
    ("error", re.compile(r"^npm ERR! (?P<message>.+)$")),
    ("error", re.compile(r"(?P<message>.*\bECONNREFUSED\b.*)")),
    ("server.ready", re.compile(r"(?i)\b(?:ready|listening|local:)\b.*?(?P<url>https?://\S+|:\d{2,5}\b)")),
]
_TRACE_AT = re.compile(r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+)(?:, in (?P<func>\S+))?')
_TRACE_END = re.compile(r"^(?P<type>[A-Za-z_][\w.]*(?:Error|Exception|Interrupt|Exit|Warning))(?:: (?P<message>.*))?$")
_RUST_AT = re.compile(r"^\s*--> (?P<file>[^:]+):(?P<line>\d+):(?P<col>\d+)")
_TEST_COMMAND = re.compile(r"\b(pytest|jest|vitest|mocha|go test|cargo test|npm (?:run )?test|pnpm (?:run )?test|"
                           r"yarn test|dotnet test|mvn test|gradle test|phpunit|rspec)\b")
_BUILD_COMMAND = re.compile(r"\b(tsc|build|make|cargo build|go build|webpack|vite build|next build|dotnet build|"
                            r"mvn (?:compile|package)|gradle build|lint|eslint|mypy|ruff)\b")
FAILURE_KINDS = ("test.failed", "build.error", "error", "port.in_use", "command.failed", "crash", "build.failed")


def clean(text: str) -> str:
    """Terminal output as plain text: escape sequences out, and a line a
    progress bar rewrote with \\r kept as its last version."""
    text = _ANSI.sub("", text).replace("\r\n", "\n")
    return "\n".join(part.rsplit("\r", 1)[-1] for part in text.split("\n"))


def level_of(line: str) -> str:
    return "error" if _ERROR.search(line) else "warn" if _WARN.search(line) else "info"


@dataclass
class _StreamState:
    partial: str = ""
    command_id: str = ""
    command: str = ""
    traceback_at: Optional[dict] = None
    rust_error: Optional[dict] = None
    extracted: list[dict] = field(default_factory=list)


class LogHub:
    def __init__(self, store: Store):
        self.store = store
        self._state: dict[str, _StreamState] = {}

    # ── in ─────────────────────────────────────────────────────────────────

    def command_started(self, ws: str, stream_id: str, kind: str, name: str, command: str = "",
                        cwd: str = "", command_id: str = "") -> None:
        command = redact(command)
        self.store.open_stream(stream_id, ws, kind, name, command, cwd)
        st = self._state.setdefault(stream_id, _StreamState())
        self._flush(ws, stream_id, st)
        st.command_id, st.command, st.extracted = command_id, command, []
        st.traceback_at = st.rust_error = None

    def output(self, ws: str, stream_id: str, kind: str, name: str, text: str,
               command_id: str = "") -> list[dict]:
        """A chunk of a stream's output: whole lines stored, a trailing
        partial line held until the next chunk. The events it raised."""
        self.store.open_stream(stream_id, ws, kind, name)
        st = self._state.setdefault(stream_id, _StreamState())
        if command_id:
            st.command_id = command_id
        data = st.partial + clean(text)
        lines = data.split("\n")
        st.partial = lines.pop()
        return self._store_lines(ws, stream_id, st, lines)

    def command_ended(self, ws: str, stream_id: str, exit_code: Optional[int], command: str = "",
                      command_id: str = "") -> Optional[dict]:
        """A command (or task, or debug session) finished: the rest of its
        output in, the stream closed, and one timeline event for how it went."""
        st = self._state.setdefault(stream_id, _StreamState())
        self._flush(ws, stream_id, st)
        if st.rust_error and len(st.extracted) < MAX_EXTRACTED:
            st.extracted.append(st.rust_error)
            self.store.add_event(ws, st.rust_error["kind"], st.rust_error["summary"], st.rust_error | {"stream": stream_id})
        st.rust_error = None
        self.store.open_stream(stream_id, ws, "terminal", stream_id.rsplit(":", 1)[-1])
        self.store.end_stream(stream_id, exit_code)
        command = redact(command) or st.command
        found = st.extracted
        event = None
        if exit_code not in (None, 0):
            summary = f"`{command[:120]}` failed (exit {exit_code})" if command else f"exit {exit_code}"
            if found:
                summary += " - " + summarise(found)
            kind = "build.failed" if (_BUILD_COMMAND.search(command) or _TEST_COMMAND.search(command)) else "command.failed"
            event = {"kind": kind, "summary": summary,
                     "data": {"command": command, "exit_code": exit_code, "stream": stream_id,
                              "command_id": command_id or st.command_id, "found": found[:10]}}
        elif command and (_TEST_COMMAND.search(command) or _BUILD_COMMAND.search(command)):
            event = {"kind": "build.passed", "summary": f"`{command[:120]}` passed",
                     "data": {"command": command, "stream": stream_id}}
        if event:
            self.store.add_event(ws, event["kind"], event["summary"], event["data"])
        st.extracted, st.command = [], ""
        return event

    def _flush(self, ws: str, stream_id: str, st: _StreamState) -> None:
        if st.partial:
            line, st.partial = st.partial, ""
            self._store_lines(ws, stream_id, st, [line])

    def _store_lines(self, ws: str, stream_id: str, st: _StreamState, lines: list[str]) -> list[dict]:
        rows, events, now = [], [], time.time()
        for raw in lines:
            line = redact(raw[:MAX_LINE])
            if not line.strip():
                continue
            rows.append((now, level_of(line), line, st.command_id))
            for ev in self._extract(st, line):
                if len(st.extracted) < MAX_EXTRACTED:
                    st.extracted.append(ev)
                    events.append(ev)
                    self.store.add_event(ws, ev["kind"], ev["summary"], ev | {"stream": stream_id})
        self.store.add_lines(stream_id, rows)
        return events

    def _extract(self, st: _StreamState, line: str) -> list[dict]:
        out = []
        at = _TRACE_AT.match(line)
        if at:
            st.traceback_at = at.groupdict()
            return out
        end = _TRACE_END.match(line.strip())
        if end and st.traceback_at:
            where = st.traceback_at
            st.traceback_at = None
            out.append(_event("crash", f"{end['type']}: {end['message'] or ''} at {where['file']}:{where['line']}",
                              type=end["type"], message=end["message"] or "", file=where["file"],
                              line=int(where["line"])))
            return out
        rust_at = _RUST_AT.match(line)
        if rust_at and st.rust_error:
            ev, st.rust_error = st.rust_error, None
            ev.update(file=rust_at["file"], line=int(rust_at["line"]))
            ev["summary"] += f" at {rust_at['file']}:{rust_at['line']}"
            out.append(ev)
            return out
        for kind, pattern in _RULES:
            m = pattern.search(line)
            if not m:
                continue
            data = {k: v for k, v in m.groupdict().items() if v}
            if kind == "port.in_use":
                port = data.get("port") or data.get("port2") or data.get("port3")
                out.append(_event(kind, f"port {port} already in use", port=int(port)))
            elif kind == "build.error" and line.startswith("error"):
                # Rust's location is on a later line (" --> src/x.rs:4:5"):
                # held until then; one with no location goes out as it is.
                if st.rust_error:
                    out.append(st.rust_error)
                st.rust_error = _event(kind, f"error {data.get('code', '')}: {data['message']}".replace("error : ", "error: "),
                                       **data)
            elif kind in ("test.failed", "build.error"):
                where = f" in {data['file']}" + (f":{data['line']}" if data.get("line") else "") if data.get("file") else ""
                what = data.get("name") or data.get("code") or ""
                message = data.get("message", "")
                out.append(_event(kind, " ".join(x for x in (what, message) if x)[:200] + where,
                                  **{k: int(v) if k in ("line", "col") else v for k, v in data.items()}))
            elif kind == "server.ready":
                out.append(_event(kind, f"server ready at {data['url']}", url=data["url"]))
            else:
                out.append(_event(kind, data.get("message", line)[:200], message=data.get("message", line)[:500]))
            break
        return out

    # ── out ────────────────────────────────────────────────────────────────

    def list_streams(self, ws: Optional[str] = None) -> list[dict]:
        return [{"stream": s["stream_id"], "kind": s["kind"], "name": s["name"], "command": s["command"],
                 "running": s["ended"] is None, "exit_code": s["exit_code"], "lines": s["lines"]}
                for s in self.store.streams(ws)]

    def tail(self, stream_id: str, n: int = 50, command_id: Optional[str] = None) -> list[str]:
        return [r["text"] for r in self.store.tail(stream_id, max(1, min(n, 500)), command_id)]

    def search(self, query: str, since_s: float = 0.0, level: Optional[str] = None,
               ws: Optional[str] = None, limit: int = 40) -> list[dict]:
        since = time.time() - since_s if since_s else 0.0
        return [{"stream": r["stream_name"], "level": r["level"], "text": r["text"], "ts": r["ts"]}
                for r in self.store.search(query, since, level, ws, limit)]

    def last_command(self, ws: str, stream_id: Optional[str] = None, lines: int = 40) -> Optional[dict]:
        streams = [s for s in self.store.streams(ws) if s["kind"] == "terminal" and s["command"]
                   and (stream_id is None or s["stream_id"] == stream_id)]
        if not streams:
            return None
        s = streams[0]
        return {"stream": s["stream_id"], "terminal": s["name"], "command": s["command"], "cwd": s["cwd"],
                "running": s["ended"] is None, "exit_code": s["exit_code"],
                "output": self.tail(s["stream_id"], lines)}

    def failures(self, ws: Optional[str], since_s: float = 3600.0, limit: int = 20) -> list[dict]:
        return [{"kind": e["kind"], "summary": e["summary"], "ts": e["ts"],
                 **{k: e["data"][k] for k in ("file", "line", "name", "code", "command", "exit_code")
                    if k in e["data"]}}
                for e in self.store.timeline(ws, time.time() - since_s, limit, FAILURE_KINDS)]


def _event(kind: str, summary: str, **data) -> dict:
    return {"kind": kind, "summary": summary.strip(), **data}


def summarise(found: list[dict]) -> str:
    """Many extracted errors as one line: "3 errors, all TS2322 in billing/"."""
    if not found:
        return ""
    n = len(found)
    noun = "failures" if all(f["kind"] == "test.failed" for f in found) else "errors"
    words = f"{n} {noun if n != 1 else noun[:-1]}"
    codes = Counter(f.get("code") for f in found if f.get("code"))
    if codes:
        code, count = codes.most_common(1)[0]
        words += f", {'all' if count == n else 'mostly'} {code}" if count >= max(2, n / 2) or n == 1 else ""
    dirs = Counter((f.get("file") or "").replace("\\", "/").rpartition("/")[0] for f in found if f.get("file"))
    if dirs:
        folder, count = dirs.most_common(1)[0]
        if folder and count == sum(dirs.values()):
            words += f" in {folder}/"
    first = found[0]
    if first.get("file"):
        words += f"; first: {first['file']}" + (f":{first['line']}" if first.get("line") else "")
    return words
