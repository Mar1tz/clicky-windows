"""Browser tools - a real browser, behind our own specs.

Playwright drives a Chromium the user can see or not (`TORA_BROWSER_HEADLESS`,
default on), while the agent loop, the approval gate and the event log
stay ours. docs/agents/FRAMEWORK_RESEARCH.md explains why this is the one
framework we adopt and why it is wrapped rather than exposed.

Terms that keep it safe:

  * **Optional.** Playwright is not a required dependency. When it is
    missing, every tool here fails with an installable message, exactly
    as the Phase 1 stubs did, and the rest of the workforce is unaffected.
  * **HIGH risk, every one.** A browser reaches the whole internet with
    whatever it is given; nothing here auto-approves under the default
    ceiling.
  * **Password fields are never auto-approved**, even if the user has
    said "always allow fill_field": typing into one asks a human through
    `ctx.approver` regardless of policy. `always_allow` is for repetition
    of the same risk, not for escalation into credentials.
  * **One session per task**, closed when the task reaches a terminal
    state, so a crashed agent does not leave a Chromium behind.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

from agents.core import ApprovalRequest, EventKind, RiskLevel, ToolCall, ToolContext, ToolResult

from .base import Tool, make_spec

log = logging.getLogger("agents.tools.browser")

CATEGORY = "browser"
NOT_INSTALLED = ("browser tools need Playwright: run "
                 "`pip install playwright` then `playwright install chromium`")
MAX_TEXT = 6000            # chars of page text returned to the model
NAV_TIMEOUT_MS = 30_000

_sessions: dict[str, "BrowserSession"] = {}


def playwright_available() -> bool:
    try:
        import playwright.async_api  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 - ImportError, or a broken install
        return False


def headless() -> bool:
    return os.environ.get("TORA_BROWSER_HEADLESS", "1").strip().lower() not in ("0", "false", "no")


class BrowserSession:
    """One Chromium for one task, launched on first use."""

    def __init__(self, key: str):
        self.key = key
        self._pw = None
        self._browser = None
        self.page = None

    async def ensure(self):
        if self.page is not None:
            return self.page
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=headless())
        self.page = await self._browser.new_page()
        self.page.set_default_timeout(NAV_TIMEOUT_MS)
        return self.page

    async def close(self) -> None:
        for closer in (self._browser, self._pw):
            if closer is None:
                continue
            try:
                await (closer.close() if closer is self._browser else closer.stop())
            except Exception:  # noqa: BLE001 - closing is best effort
                log.debug("browser close failed", exc_info=True)
        self._browser = self._pw = self.page = None


def _key(ctx: ToolContext) -> str:
    return ctx.task_id or "default"


async def get_session(ctx: ToolContext) -> BrowserSession:
    """The task's browser, creating it and arranging its cleanup."""
    key = _key(ctx)
    session = _sessions.get(key)
    if session is None:
        session = BrowserSession(key)
        _sessions[key] = session
        if ctx.events is not None and ctx.task_id:
            _close_when_done(ctx, key)
    return session


def _close_when_done(ctx: ToolContext, key: str) -> None:
    """Close the session when its task ends. The bus publishes on the
    runtime loop, so scheduling the close there is safe."""
    unsub_box: dict[str, Any] = {}

    def _on_status(ev) -> None:
        status = str(ev.data.get("status", ""))
        if status not in ("completed", "failed", "cancelled"):
            return
        session = _sessions.pop(key, None)
        off = unsub_box.get("off")
        if off:
            off()
        if session is not None:
            try:
                asyncio.get_running_loop().create_task(session.close())
            except RuntimeError:
                pass                  # no loop: process is going down anyway

    unsub_box["off"] = ctx.events.subscribe(_on_status, kinds=[EventKind.TASK_STATUS],
                                            task_id=ctx.task_id)


async def close_all() -> None:
    for key in list(_sessions):
        session = _sessions.pop(key)
        await session.close()


class _BrowserTool(Tool):
    """Common guard: Playwright present, page available, errors reported."""

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if not playwright_available():
            return self.fail(NOT_INSTALLED, installed=False)
        try:
            session = await get_session(ctx)
            page = await session.ensure()
            return await self.act(page, args, ctx)
        except Exception as exc:  # noqa: BLE001 - the model needs the message, not a crash
            return self.fail(f"{type(exc).__name__}: {str(exc)[:300]}")

    async def act(self, page, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raise NotImplementedError

    @staticmethod
    async def describe(page) -> dict[str, Any]:
        text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        text = " ".join(text.split())
        return {"url": page.url, "title": await page.title(),
                "text": text[:MAX_TEXT] + ("…" if len(text) > MAX_TEXT else "")}

    @staticmethod
    async def has_password_field(page) -> bool:
        try:
            return await page.locator("input[type='password']").count() > 0
        except Exception:  # noqa: BLE001
            return False


class OpenPage(_BrowserTool):
    spec = make_spec(
        "open_page", "Open a URL in the agent browser and return its title and visible text.",
        risk=RiskLevel.HIGH, category=CATEGORY,
        properties={"url": {"type": "string", "description": "http(s):// or file:// URL."}},
        required=["url"],
    )

    async def act(self, page, args, ctx):
        url = str(args.get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://", "file://")):
            return self.fail(f"refusing to open {url!r}: only http(s) and file URLs")
        await page.goto(url, wait_until="domcontentloaded")
        info = await self.describe(page)
        return self.ok(f"{info['title']} — {info['url']}\n{info['text']}", **info)


class ExtractInformation(_BrowserTool):
    spec = make_spec(
        "extract_information",
        "Read the current page: the visible text, or one element's text by CSS selector.",
        risk=RiskLevel.HIGH, category=CATEGORY,
        properties={
            "query": {"type": "string", "description": "What you are looking for (for your own notes)."},
            "selector": {"type": "string", "description": "CSS selector to read instead of the whole page."},
            "url": {"type": "string", "description": "Open this first. Default: current page."},
        },
    )

    async def act(self, page, args, ctx):
        url = str(args.get("url") or "").strip()
        if url:
            await page.goto(url, wait_until="domcontentloaded")
        selector = str(args.get("selector") or "").strip()
        if selector:
            loc = page.locator(selector)
            n = await loc.count()
            if n == 0:
                return self.fail(f"nothing matches selector {selector!r}")
            texts = [" ".join((await loc.nth(i).inner_text()).split()) for i in range(min(n, 20))]
            return self.ok("\n".join(texts), url=page.url, matches=n, texts=texts)
        info = await self.describe(page)
        links = await page.evaluate(
            "() => Array.from(document.querySelectorAll('a[href]')).slice(0, 40)"
            ".map(a => ({text: a.innerText.trim().slice(0, 80), href: a.href}))")
        return self.ok(info["text"], links=links, **info)


class ClickElement(_BrowserTool):
    spec = make_spec(
        "click_element", "Click an element, by CSS selector or by its visible text.",
        risk=RiskLevel.HIGH, category=CATEGORY,
        properties={
            "selector": {"type": "string", "description": "CSS selector."},
            "text": {"type": "string", "description": "Visible text of a button or link."},
        },
    )

    async def act(self, page, args, ctx):
        selector = str(args.get("selector") or "").strip()
        text = str(args.get("text") or "").strip()
        if not selector and not text:
            return self.fail("give a selector or the element's visible text")
        loc = page.locator(selector) if selector else page.get_by_text(text, exact=False)
        if await loc.count() == 0:
            return self.fail(f"nothing to click for {selector or text!r}")
        await loc.first.click()
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:  # noqa: BLE001 - not every click navigates
            pass
        info = await self.describe(page)
        return self.ok(f"clicked {selector or text!r}; now at {info['title']} — {info['url']}",
                       **info)


class FillField(_BrowserTool):
    spec = make_spec(
        "fill_field", "Type a value into an input, textarea or editable field.",
        risk=RiskLevel.HIGH, category=CATEGORY,
        properties={
            "selector": {"type": "string", "description": "CSS selector of the field."},
            "value": {"type": "string", "description": "Text to enter."},
            "submit": {"type": "boolean", "description": "Press Enter afterwards. Default false."},
        },
        required=["selector", "value"],
    )

    async def act(self, page, args, ctx):
        selector = str(args.get("selector") or "").strip()
        value = str(args.get("value") or "")
        loc = page.locator(selector)
        if await loc.count() == 0:
            return self.fail(f"no field matches {selector!r}")
        kind = (await loc.first.get_attribute("type") or "").lower()
        if kind == "password" or await self.has_password_field(page):
            # A standing "always allow fill_field" must not extend to
            # credentials. Ask a human every single time.
            req = ApprovalRequest(
                task_id=ctx.task_id or "", agent_id=ctx.agent_id or "",
                call=ToolCall("fill_field", {"selector": selector, "value": "•••"}),
                reason=("This page has a password field. Typing here could submit "
                        "credentials — approve only if you expect it."))
            if not await ctx.approver(req):
                return self.fail("denied: typing on a page with a password field")
        await loc.first.fill(value)
        if args.get("submit"):
            await loc.first.press("Enter")
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:  # noqa: BLE001
                pass
        shown = "•" * min(len(value), 8) if kind == "password" else value[:60]
        return self.ok(f"filled {selector!r} with {shown!r}", url=page.url, selector=selector)


class ScreenshotPage(_BrowserTool):
    spec = make_spec(
        "screenshot_page", "Save a PNG of the current page into the workspace.",
        risk=RiskLevel.HIGH, category=CATEGORY,
        properties={"path": {"type": "string", "description": "Where to save. Default screenshots/page.png"}},
    )

    async def act(self, page, args, ctx):
        rel = str(args.get("path") or "screenshots/page.png")
        target, err = self.guard(ctx, rel)
        if err:
            return err
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        await page.screenshot(path=target, full_page=True)
        return self.ok(f"saved screenshot to {target}", path=target)


class CloseBrowser(Tool):
    spec = make_spec(
        "close_browser", "Close the agent browser for this task.",
        risk=RiskLevel.SAFE, category=CATEGORY, properties={},
    )

    async def run(self, args, ctx):
        session = _sessions.pop(_key(ctx), None)
        if session is None:
            return self.ok("no browser was open", closed=False)
        await session.close()
        return self.ok("browser closed", closed=True)


def tools() -> list[Tool]:
    return [OpenPage(), ExtractInformation(), ClickElement(), FillField(),
            ScreenshotPage(), CloseBrowser()]
