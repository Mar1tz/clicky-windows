"""What Tora's voice and agents call: one tool, a few actions.

One tool for the live voice rather than eleven: every declaration is read
on every turn, and the model picks better from one clear "read the
editor" than from a shelf of near-twins. The packet it gets first is small
on purpose - 19 Oct, a 4,000-character tool result cost the voice 5-8 s
before its first word - and says what else can be asked for; the agents,
which read rather than speak, get a bigger one.
"""
from __future__ import annotations

from typing import Any

from . import current

ACTIONS = ("overview", "problems", "last_command", "failures", "tail", "search_logs", "streams",
           "file", "symbols", "references", "search_code")
VOICE_BUDGET = 1800
VOICE_CHARS = 2500
AGENT_BUDGET = 8000

DESCRIPTION = (
    "Read the user's code editor directly (VS Code, Cursor, Windsurf - through HeyTora DevLink): far "
    "more accurate than a screenshot, and it sees what is off screen. Use it FIRST for any question "
    "about their code, project, errors, build, tests or terminal - 'what's wrong', 'why did the build "
    "fail', 'where was I', 'what does this function do', 'what did that command print'. "
    "overview: where they are and what is broken (start here). problems: errors and warnings. "
    "last_command / failures / tail / search_logs / streams: terminals, tasks and their output. "
    "file / symbols / references / search_code: the code itself. Use look_at_screen only when this "
    "says no editor is connected. Read-only: it never changes anything."
)

PARAMETERS = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "question": {"type": "string", "description": "Their question, in their words (for overview)."},
        "query": {"type": "string", "description": "A symbol's name, or words to find in the code or the logs."},
        "path": {"type": "string", "description": "A file, relative to the project."},
        "line": {"type": "integer", "description": "A line in that file (1 = the first)."},
        "lines": {"type": "integer", "description": "How many lines of output (tail, last_command)."},
        "stream": {"type": "string", "description": "A terminal, task or debug session, by name (tail)."},
        "since_minutes": {"type": "number", "description": "How far back (failures, search_logs)."},
    },
    "required": ["action"],
}


def voice_declaration() -> dict:
    return {"name": "code_context", "description": DESCRIPTION, "parameters": PARAMETERS}


async def answer(args: dict[str, Any], *, budget: int = VOICE_BUDGET, cap: int = VOICE_CHARS) -> dict:
    """The action asked for, from the running hub - or why it cannot be."""
    hub = current()
    if hub is None:
        return {"error": "DevLink is off (HeyTora Settings → Code editors), so the editor cannot be read. "
                         "Use look_at_screen instead."}
    action = str(args.get("action") or "overview")
    query = str(args.get("query") or "").strip()
    path = str(args.get("path") or "").strip()
    line = _int(args.get("line"))
    lines = max(5, min(_int(args.get("lines")) or 40, 200))
    since = float(args.get("since_minutes") or 0) or None
    if action == "overview":
        work = hub.overview(str(args.get("question") or ""), budget)
    elif action == "problems":
        work = hub.problems(path)
    elif action == "last_command":
        work = hub.last_command(str(args.get("stream") or ""), lines)
    elif action == "failures":
        work = hub.failures(since or 60)
    elif action == "tail":
        work = hub.tail(str(args.get("stream") or ""), lines)
    elif action == "search_logs":
        if not query:
            return {"error": "search_logs needs a query"}
        work = hub.search_logs(query, since or 0)
    elif action == "streams":
        work = hub.streams()
    elif action == "file":
        if not path:
            return {"error": "file needs a path"}
        start = max(1, line - 20) if line else 1
        work = hub.get_file(path, start, start + (60 if line else 200))
    elif action == "symbols":
        if not query:
            return {"error": "symbols needs a query"}
        work = hub.symbols(query)
    elif action == "references":
        if not query and not path:
            return {"error": "references needs a symbol (query) or a path and line"}
        work = hub.references(query, path, max(0, line - 1), 0)
    elif action == "search_code":
        if not query:
            return {"error": "search_code needs a query"}
        work = hub.search_code(query)
    else:
        return {"error": f"no action {action!r} - one of {', '.join(ACTIONS)}"}
    out = await hub.run(work)
    if out.get("text") and len(out["text"]) > cap:
        out["text"] = out["text"][:cap] + "\n… (more: ask with a narrower action)"
    return out


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
