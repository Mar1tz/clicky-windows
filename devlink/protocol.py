"""The connector protocol: what an app's connector may say, and to whom.

Every connector (the VS Code extension first) speaks JSON-RPC 2.0 to the
local gateway, one message per line. A message is either an event the
connector pushes ("the active file changed", "a command ended with exit
code 1"), a request one side makes of the other ("the text around the
cursor", "grant read_code for this workspace") or its response. The design
(docs/devlink/DEVLINK.md) keeps the connector a translator: it holds no AI
logic, so everything that decides - permissions, redaction, what the model
sees - lives on HeyTora's side, in one place.

Each event is normalised to one shape before anything else looks at it:
{source, workspace_id, type, ts, payload, sensitivity}. An event type the
gateway does not know is dropped, not stored: a connector of a newer
version cannot slip new kinds of data past the policy by naming them.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

PROTOCOL_VERSION = 1
MAX_LINE_BYTES = 1_000_000          # one message; a bigger one closes the connection

# ── Capabilities: what a workspace may let HeyTora do ──────────────────────
#
# Read-only in this phase. The write capabilities are named so the settings
# and the connector can show them as "not yet", and so a grant for one is
# refused rather than silently stored.
READ_CODE = "read_code"              # files, the editor, problems, symbols, git
READ_TERMINALS = "read_terminals"    # terminal commands and their output, tasks
READ_DEBUG = "read_debug"            # debug sessions, their output and stops
READ_CAPABILITIES = (READ_CODE, READ_TERMINALS, READ_DEBUG)
LATER_CAPABILITIES = ("edit_files", "run_tasks", "run_commands", "monitor")

CAPABILITY_WORDS = {
    READ_CODE: ("Code and problems", "Open files, the cursor, errors and warnings, symbols, git."),
    READ_TERMINALS: ("Terminals and tasks", "Commands you run, their output and exit codes."),
    READ_DEBUG: ("Debugging", "Debug sessions, their output and where they stopped."),
}

# How long a grant lasts: one answer, until HeyTora restarts, or for good.
ONCE, SESSION, ALWAYS = "once", "session", "always"
SCOPES = (ONCE, SESSION, ALWAYS)

# Risk levels for actions (the design's four). Every action is "read" in
# this phase; the others are here for the schema the connectors register.
RISK_READ, RISK_LOCAL_WRITE, RISK_EXECUTE, RISK_EXTERNAL = "read", "local-write", "execute", "external"

# ── Events: type -> the capability it needs (None: carries no content) ────
EVENTS: dict[str, Optional[str]] = {
    "window.focused": None,
    "workspace.folders": None,
    "editor.active": READ_CODE,
    "editor.selection": READ_CODE,
    "editor.visible": READ_CODE,
    "tabs.changed": READ_CODE,
    "document.changed": READ_CODE,
    "document.saved": READ_CODE,
    "diagnostics.changed": READ_CODE,
    "git.changed": READ_CODE,
    "terminal.started": READ_TERMINALS,
    "terminal.output": READ_TERMINALS,
    "terminal.ended": READ_TERMINALS,
    "task.started": READ_TERMINALS,
    "task.ended": READ_TERMINALS,
    "debug.started": READ_DEBUG,
    "debug.output": READ_DEBUG,
    "debug.stopped": READ_DEBUG,
    "debug.ended": READ_DEBUG,
}

# What HeyTora may ask a connector for, and the capability each needs.
REQUESTS: dict[str, str] = {
    "editor.window": READ_CODE,
    "file.read": READ_CODE,
    "symbols.workspace": READ_CODE,
    "symbols.document": READ_CODE,
    "definition": READ_CODE,
    "references": READ_CODE,
    "diagnostics.all": READ_CODE,
}

# Events whose payload is source text or program output: stored only after
# the redactor has been over it, and never kept past the retention.
SENSITIVE_EVENTS = {"editor.selection", "terminal.output", "debug.output"}


@dataclass
class Event:
    """One thing that happened in a connected app, in the one shape."""
    source: str                     # "vscode"
    workspace_id: str
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    sensitivity: str = "normal"     # "normal" | "content": holds code or output

    @property
    def capability(self) -> Optional[str]:
        return EVENTS.get(self.type)


def normalise(source: str, workspace_id: str, params: Any) -> Optional[Event]:
    """An event notification's params as an Event - None when it is not one
    the gateway knows, or its payload is not an object."""
    if not isinstance(params, dict):
        return None
    kind, payload = params.get("type"), params.get("payload", {})
    if kind not in EVENTS or not isinstance(payload, dict):
        return None
    ts = params.get("ts")
    return Event(source=source, workspace_id=workspace_id, type=kind, payload=payload,
                 ts=float(ts) / 1000.0 if isinstance(ts, (int, float)) and ts > 1e11 else time.time(),
                 sensitivity="content" if kind in SENSITIVE_EVENTS else "normal")


# ── JSON-RPC 2.0, one message per line ─────────────────────────────────────

def encode(message: dict) -> bytes:
    return (json.dumps(message, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def request(id_: int, method: str, params: Optional[dict] = None) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}


def notify(method: str, params: Optional[dict] = None) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params or {}}


def result(id_: Any, value: Any) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": value}


def error(id_: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


# JSON-RPC's own codes, and ours above -32000.
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602
NOT_AUTHORISED, NOT_GRANTED, PAUSED = -32001, -32002, -32003
