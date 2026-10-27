"""The person's own browser tab, through HeyTora's extension (CI-04, CI-05).

The browser agent's other tools drive a Chromium of HeyTora's own, which is
signed in to nothing. These work in the tab the person is looking at, in
the browser they use, with the sign-ins they already have - read it, click
in it, type in it - without moving their mouse. Reading is MEDIUM (it is
their logged-in page); clicking and typing always ask first, one action at
a time; a password, one-time-code or card field is never filled - the
extension refuses it as well (connectors/browser/background.js).

BR-03 (after BeatBrowser's TaskSpaces): a job may open tabs of its own,
in the background, in their browser (tab_open). It then works in those -
never in another tab of theirs; without one, in the tab they are looking
at, as before. Switching to a job's tab is taking it back: the job pauses
until they say carry on. When the job ends its tabs close; theirs never.

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


def _where(ctx: ToolContext) -> dict:
    """The job's own tab when it has one (BR-03), else the tab they are looking at."""
    from integrations.browser_bridge import current
    bridge = current()
    job = ctx.task_id or ""
    tab = bridge.job_tab(job) if bridge is not None and job else None
    return {"tabId": tab, "job": job}


_closers: set = set()


def _close_when_done(ctx: ToolContext) -> None:
    """The job's tabs close when it ends - only its own (BR-03)."""
    if ctx.events is None or not ctx.task_id or ctx.task_id in _closers:
        return
    from agents.core import EventKind, TaskStatus
    job = ctx.task_id
    _closers.add(job)

    def on_status(ev) -> None:
        if ev.task_id != job:
            return
        try:
            if not TaskStatus(ev.data.get("status", "")).is_terminal:
                return
        except ValueError:
            return
        from integrations.browser_bridge import current
        bridge = current()
        if bridge is not None and bridge.connected:
            try:
                bridge.request({"type": "close_job", "job": job}, 5.0)
            except Exception:  # noqa: BLE001 - a tab left open is theirs to close
                pass
        _closers.discard(job)
        unsub()
    unsub = ctx.events.subscribe(on_status, kinds=[EventKind.TASK_STATUS])


class TabOpen(Tool):
    spec = make_spec(
        "tab_open",
        "Open a page in a background tab of their own browser for this job; the tab_ tools then use it.",
        risk=RiskLevel.HIGH, category=CATEGORY, properties={"url": {"type": "string"}}, required=["url"])

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        url = str(args.get("url") or "").strip()
        if not url.lower().startswith(("https://", "http://")):
            return self.fail("only http(s) pages open in a tab")
        out = await _ask({"type": "open", "url": url, "job": ctx.task_id or ""})
        if not out.get("ok"):
            return self.fail(out.get("error", "the tab did not open"))
        from integrations.browser_bridge import current
        bridge = current()
        if bridge is not None:
            bridge.spaces[int(out["tabId"])] = {"job": ctx.task_id or "", "url": url, "alive": True}
        _close_when_done(ctx)
        return self.ok(f"opened {url} in a background tab of their browser - tab_read it next", tabId=out["tabId"])


class TabRead(Tool):
    spec = make_spec(
        "tab_read",
        "Read the tab the person has open in their own browser (signed in as them): its title, text, and "
        "its fields, buttons and links, each with a ref for tab_click / tab_type. Read again after acting.",
        risk=RiskLevel.MEDIUM, category=CATEGORY, replay_safe=True, properties={})

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        out = await _ask({"type": "read", **_where(ctx)})
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
        out = await _ask({"type": "click", **_where(ctx), "ref": args["ref"]})
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
        out = await _ask({"type": "type", **_where(ctx), "ref": args["ref"], "text": args["text"]})
        if not out.get("ok"):
            return self.fail(out.get("error", "the text did not go in"))
        return self.ok(f"typed into {args.get('what') or args['ref']}", unchanged=False)


def tools() -> list[Tool]:
    return [TabOpen(), TabRead(), TabClick(), TabType()]
