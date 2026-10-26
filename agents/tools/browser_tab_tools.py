"""The person's own browser tab, through HeyTora's extension (CI-04, CI-05).

The browser agent's other tools drive a Chromium of HeyTora's own, which is
signed in to nothing. These work in the tab the person is looking at, in
the browser they use, with the sign-ins they already have - read it, click
in it, type in it - without moving their mouse. Reading is MEDIUM (it is
their logged-in page); clicking and typing always ask first, one action at
a time; a password, one-time-code or card field is never filled - the
extension refuses it as well (connectors/browser/background.js).

The bridge lives in the app (integrations/browser_bridge.py); without it, or
with the extension not connected, each tool says how to set it up.
"""
from __future__ import annotations

from typing import Any

from agents.core import RiskLevel, ToolContext, ToolResult

from .base import Tool, make_spec

CATEGORY = "browser"
NOT_CONNECTED = ("HeyTora's browser extension is not connected - ask Tora to \"set up the browser "
                 "extension\", then try again.")


async def _ask(msg: dict, timeout: float = 15.0) -> dict:
    from integrations.browser_bridge import current
    bridge = current()
    if bridge is None or not bridge.connected:
        return {"ok": False, "error": NOT_CONNECTED}
    return await bridge.arequest(msg, timeout)


class TabRead(Tool):
    spec = make_spec(
        "tab_read",
        "Read the tab the person has open in their own browser (signed in as them): its title, text, and "
        "its fields, buttons and links, each with a ref for tab_click / tab_type. Read again after acting.",
        risk=RiskLevel.MEDIUM, category=CATEGORY, properties={})

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        out = await _ask({"type": "read", "tabId": None})
        if not out.get("ok"):
            return self.fail(out.get("error", "the tab could not be read"))
        fields = out.get("fields") or []
        lines = [f"{f['ref']}: {f['tag']}{'/' + f['type'] if f.get('type') else ''} "
                 f"{f.get('label', '')!r}" + (" [password/code/card - theirs to fill]" if f.get("secret") else "")
                 + (f" = {f['value']!r}" if f.get("value") else "") for f in fields[:150]]
        text = (out.get("text") or "")[:12000]
        return self.ok(f"Tab: {out.get('title', '')}\n\nFields:\n" + "\n".join(lines) + f"\n\nText:\n{text}",
                       title=out.get("title", ""), fields=len(fields))


class _Act(Tool):
    always_ask = True                       # every click and every typing, approved

    def ask_reason_for(self, call) -> str:
        a = call.arguments
        if call.tool_name == "tab_type":
            return f"Type {str(a.get('text', ''))[:120]!r} into {a.get('what') or a.get('ref')} in your browser tab"
        return f"Click {a.get('what') or a.get('ref')} in your browser tab"


class TabClick(_Act):
    spec = make_spec(
        "tab_click", "Click a button or link (by its ref from tab_read) in the person's browser tab. Asks them first.",
        risk=RiskLevel.HIGH, category=CATEGORY,
        properties={"ref": {"type": "string"}, "what": {"type": "string", "description": "Its label, for the approval."}},
        required=["ref"])

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        out = await _ask({"type": "click", "tabId": None, "ref": args["ref"]})
        return self.ok(f"clicked {args.get('what') or args['ref']}") if out.get("ok") else \
            self.fail(out.get("error", "the click did not happen"))


class TabType(_Act):
    spec = make_spec(
        "tab_type", "Type text into a field (by its ref from tab_read) in the person's browser tab. Asks them first. "
        "Never a password, one-time code or card number.",
        risk=RiskLevel.HIGH, category=CATEGORY,
        properties={"ref": {"type": "string"}, "text": {"type": "string"},
                    "what": {"type": "string", "description": "The field's label, for the approval."}},
        required=["ref", "text"])

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        out = await _ask({"type": "type", "tabId": None, "ref": args["ref"], "text": args["text"]})
        if not out.get("ok"):
            return self.fail(out.get("error", "the text did not go in"))
        return self.ok(f"typed into {args.get('what') or args['ref']}", unchanged=False)


def tools() -> list[Tool]:
    return [TabRead(), TabClick(), TabType()]
