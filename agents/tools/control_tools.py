"""Desktop control tools: ask the app, don't click at it.

    list_app_actions()                     what this machine can be asked to do
    app_action("spotify", "next")          the app's own way (media key here)
    ui_control("Notepad", "press", "Save") a control, invoked in the background
    open_link("spotify:playlist:…")        any registered link handler

The ladder is in `agents.control.model.Tier`: app API, deep link, system
key, background UI - and only under those, the existing `click` /
`type_text` tools, which move the user's real mouse and always ask.

Asking is per ACTION, not per tool: reading what is playing never asks,
skipping a track asks once and can be allowed for the rest of the job
("always allow spotify.next"), and anything that sends something to
another person is SENSITIVE and asks every time. That is what
`asks_for_call` and `grant_key` below are for.
"""
from __future__ import annotations

from typing import Any

from agents.control import Risk, control
from agents.control import uia as _uia
from agents.core import RiskLevel, ToolCall, ToolContext, ToolResult

from .base import Tool, make_spec
from .computer_tools import kill_switch

CATEGORY = "desktop"

_WINDOW = {"type": "string", "description": "Part of the window's title, e.g. 'Notepad' or 'Chrome'."}


class ListAppActions(Tool):
    spec = make_spec(
        "list_app_actions",
        "What this computer can be asked to do, app by app: Spotify, VS Code, browsers, Windows "
        "itself. Call this FIRST when a request names an app - it shows the exact action names, "
        "their arguments, and how quietly each one works.",
        risk=RiskLevel.SAFE, category=CATEGORY,
        properties={"app": {"type": "string", "description": "Only this app (spotify, vscode, windows…)."},
                    "query": {"type": "string", "description": "Only actions matching this word."}},
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        app, query = str(args.get("app") or ""), str(args.get("query") or "")
        c = control()
        actions = c.actions(app=app, query=query)
        if not actions:
            return self.ok(c.suggestions(app), apps=[a["app"] for a in c.apps()])
        lines = []
        for a in actions:
            params = ", ".join(f"{p.name}{'*' if p.required else ''}" for p in a.params)
            lines.append(f"{a.key}({params}) [{a.tier.label}, {a.risk.name.lower()}] - {a.description}")
        header = ("Apps I can drive directly: "
                  + ", ".join(f"{x['app']}" for x in c.apps()) + "\n") if not app else ""
        return self.ok(header + "\n".join(lines), actions=[a.describe() for a in actions])


class AppAction(Tool):
    spec = make_spec(
        "app_action",
        "Do something in an app its own way - a media key, the app's CLI, or a deep link - so the "
        "window need not be open, focused, or even visible, and the mouse never moves. Use "
        "list_app_actions to see what exists. Prefer this over click/type_text.",
        risk=RiskLevel.HIGH, category=CATEGORY,
        properties={"app": {"type": "string", "description": "spotify, vscode, cursor, edge, windows…"},
                    "action": {"type": "string", "description": "Action name, e.g. next, open_project, chat."},
                    "arguments": {"type": "object", "description": "The action's arguments, as an object."}},
        required=["app", "action"],
    )
    always_ask = True            # overridden per call by asks_for_call below

    # -- per-action asking ------------------------------------------------------------
    def _action(self, call: ToolCall):
        args = call.arguments or {}
        return control().find(str(args.get("app", "")), str(args.get("action", "")))

    def asks_for_call(self, call: ToolCall) -> bool:
        """Looking never interrupts; anything that acts does."""
        found = self._action(call)
        return True if found is None else found.risk.needs_confirmation

    def grant_key(self, call: ToolCall) -> str:
        """What "always allow" would mean here: this action, not every action."""
        found = self._action(call)
        return f"app_action:{found.key}" if found else ""

    def ask_reason_for(self, call: ToolCall) -> str:
        found = self._action(call)
        if found is None:
            return "app_action needs a human: the action is unknown"
        how = "without touching your screen" if not found.tier.disturbs_the_user else "ON YOUR SCREEN"
        return f"{found.key} - {found.description} ({found.tier.label}, {how})"

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if kill_switch.tripped:
            return self.fail("desktop control is stopped (Esc). It resets when a new task starts.")
        app, action = str(args.get("app", "")), str(args.get("action", ""))
        arguments = args.get("arguments") or {}
        if not isinstance(arguments, dict):
            return self.fail("arguments must be an object, e.g. {\"project\": \"my-app\"}")
        out = await control().run(app, action, arguments)
        data = {"app": out.app, "action": out.action, "tier": out.tier.label if out.tier else "",
                "attempts": out.attempts, **out.data}
        if not out.ok:
            return self.fail(out.message, **data)
        return self.ok(out.message, **data)


class UIControl(Tool):
    spec = make_spec(
        "ui_control",
        "Work a window's own controls through Windows UI Automation: list what a window offers, "
        "press a button, toggle a checkbox, pick an item, or read a field - all without focusing "
        "the window or moving the mouse. Use when an app has no action of its own. Chromium and "
        "Electron apps (VS Code, Spotify, Slack, Discord) often expose little this way - prefer "
        "their app_action entries.",
        risk=RiskLevel.HIGH, category=CATEGORY,
        properties={"window": _WINDOW,
                    "do": {"type": "string", "description": "list | press | toggle | select | expand | "
                                                            "collapse | read | set_text",
                           "enum": ["list", "press", "toggle", "select", "expand", "collapse", "read",
                                    "set_text"]},
                    "control": {"type": "string", "description": "The control's name, as `list` shows it."},
                    "value": {"type": "string", "description": "For set_text."},
                    "query": {"type": "string", "description": "For list: only names containing this."}},
        required=["window", "do"],
    )
    always_ask = True

    _PASSIVE = {"list", "read"}

    def asks_for_call(self, call: ToolCall) -> bool:
        return str((call.arguments or {}).get("do", "")).lower() not in self._PASSIVE

    def grant_key(self, call: ToolCall) -> str:
        args = call.arguments or {}
        return f"ui_control:{str(args.get('do', '')).lower()}"

    def ask_reason_for(self, call: ToolCall) -> str:
        args = call.arguments or {}
        return (f"ui_control would {args.get('do')} {args.get('control', '')!r} in "
                f"{args.get('window', '')!r} - in the background, without moving your mouse")

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if kill_switch.tripped:
            return self.fail("desktop control is stopped (Esc). It resets when a new task starts.")
        window = str(args.get("window", ""))
        do = str(args.get("do", "")).lower()
        control_name = str(args.get("control") or "")
        try:
            if do == "list":
                found = await _to_thread(_uia.controls, window, limit=40, query=str(args.get("query") or ""))
                if not found:
                    return self.ok(f"{window!r} exposes no named controls to UI Automation - it is "
                                   "probably a Chromium/Electron app. Try its app_action, or the "
                                   "on-screen tools.", controls=[])
                rows = " | ".join(f"{c['name']}[{c['type']}:{'+'.join(c['actions'])}]" for c in found)
                return self.ok(f"{len(found)} controls in {window!r}: {rows}", controls=found)
            if not control_name:
                return self.fail("say which control (use do='list' first)")
            action = {"press": "invoke", "set_text": "set_value"}.get(do, do)
            message = await _to_thread(_uia.act, window, control_name, action,
                                       value=str(args.get("value") or ""))
            return self.ok(message, window=window, control=control_name, tier="background UI")
        except LookupError as exc:
            return self.fail(str(exc))
        except (RuntimeError, ValueError) as exc:
            return self.fail(str(exc))


class OpenLink(Tool):
    spec = make_spec(
        "open_link",
        "Open any link this machine knows how to handle - a web address, or an app's own link "
        "(spotify:playlist:…, vscode://file/…, ms-settings:bluetooth, mailto:…). The app's handler "
        "takes it; nothing is typed or clicked.",
        risk=RiskLevel.HIGH, category=CATEGORY,
        properties={"uri": {"type": "string", "description": "The link."}},
        required=["uri"],
    )
    always_ask = True

    def grant_key(self, call: ToolCall) -> str:
        uri = str((call.arguments or {}).get("uri", ""))
        return f"open_link:{uri.split(':', 1)[0].lower()}" if ":" in uri else "open_link"

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if kill_switch.tripped:
            return self.fail("desktop control is stopped (Esc).")
        from agents.control.discovery import open_uri, scan
        uri = str(args.get("uri", "")).strip()
        if not uri or ":" not in uri:
            return self.fail("give a full link, like spotify:playlist:… or https://…")
        scheme = uri.split(":", 1)[0].lower()
        machine = scan()
        if scheme not in ("http", "https", "file", "mailto") and not machine.has_scheme(scheme):
            known = ", ".join(sorted(machine.schemes)[:20])
            return self.fail(f"nothing on this machine handles {scheme}: links. Known: {known}…")
        try:
            await _to_thread(open_uri, uri)
        except Exception as exc:  # noqa: BLE001
            return self.fail(f"could not open {uri}: {exc}")
        return self.ok(f"opened {uri}", uri=uri, tier="deep link")


async def _to_thread(fn, *args, **kwargs):
    import asyncio
    return await asyncio.to_thread(fn, *args, **kwargs)


def tools() -> list[Tool]:
    return [ListAppActions(), AppAction(), UIControl(), OpenLink()]
