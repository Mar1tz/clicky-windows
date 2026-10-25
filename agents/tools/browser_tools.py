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
  * **Signed in once, on this PC** (SK-06, docs/agents/SKILLS.md). Every
    task's tab opens in one shared Chromium with a profile of its own in
    the data folder - never the user's own browser profile - so a site
    the user signed in to stays signed in for the next job. Signing in is
    the user's job, in a window they can see (ask_to_sign_in); an agent
    never types a password. Without a profile (or with it locked) a task
    gets a fresh browser, as before.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from typing import Any, Optional
from urllib.parse import urlparse

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


SITES_FILE = "signed_in.json"          # in the profile folder: the sites the user signed in to
SIGN_IN_WAIT_S = 600                   # how long a sign-in window may stay open


def profile_dir() -> str:
    """HeyTora's own browser profile, or "" for a fresh browser every time
    (TORA_BROWSER_PROFILE="" - and when there is no data folder)."""
    override = os.environ.get("TORA_BROWSER_PROFILE")
    if override is not None:
        return override.strip()
    try:
        from config import data_dir
        return str(data_dir() / "browser")
    except Exception:  # noqa: BLE001 - config is optional for the agent package
        return ""


class _Shared:
    """The one Chromium every task's tab opens in (a persistent context), on
    the agent loop. Visible only while the user signs in."""
    pw = None
    context = None
    visible = False
    loop = None
    starting = None                    # a future while it launches


async def shared_context(*, visible: bool = False):
    """The shared, signed-in browser - or None (no profile, or it could not
    start: locked by another HeyTora, say). Never raises."""
    path = profile_dir()
    if not path:
        return None
    loop = asyncio.get_running_loop()
    if _Shared.loop is not loop:                 # a new loop: the old browser is gone with it
        _Shared.pw = _Shared.context = _Shared.starting = None
        _Shared.loop = loop
    if _Shared.starting is not None:
        await asyncio.shield(_Shared.starting)
    if _Shared.context is not None and _Shared.visible == visible:
        return _Shared.context
    await close_shared()
    _Shared.starting = loop.create_future()
    try:
        from playwright.async_api import async_playwright
        _Shared.pw = await async_playwright().start()
        os.makedirs(path, exist_ok=True)
        _Shared.context = await _Shared.pw.chromium.launch_persistent_context(
            path, headless=headless() and not visible)
        _Shared.visible = visible
        for page in list(_Shared.context.pages):     # the blank tab it opens with
            if visible:
                break
            await page.close()
    except Exception as exc:  # noqa: BLE001 - a fresh browser is the fallback
        log.warning("the shared browser did not start (%s); using a fresh one", type(exc).__name__)
        await close_shared()
    finally:
        done, _Shared.starting = _Shared.starting, None
        if done is not None and not done.done():
            done.set_result(None)
    return _Shared.context


async def close_shared() -> None:
    for closer in (_Shared.context, _Shared.pw):
        if closer is None:
            continue
        try:
            await (closer.close() if closer is _Shared.context else closer.stop())
        except Exception:  # noqa: BLE001 - closing is best effort
            log.debug("shared browser close failed", exc_info=True)
    _Shared.context = _Shared.pw = None
    _Shared.visible = False


class BrowserSession:
    """One task's tab in the shared browser - or, without one, its own
    Chromium - opened on first use."""

    def __init__(self, key: str):
        self.key = key
        self._pw = None
        self._browser = None
        self.page = None
        self.shared = False

    async def ensure(self):
        if self.page is not None and not self.page.is_closed():
            return self.page
        context = await shared_context()
        if context is not None:
            self.page, self.shared = await context.new_page(), True
        else:
            from playwright.async_api import async_playwright
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=headless())
            self.page, self.shared = await self._browser.new_page(), False
        self.page.set_default_timeout(NAV_TIMEOUT_MS)
        return self.page

    async def close(self) -> None:
        if self.shared:
            try:
                if self.page is not None and not self.page.is_closed():
                    await self.page.close()
            except Exception:  # noqa: BLE001
                log.debug("tab close failed", exc_info=True)
            self.page = None
            if not _sessions:                       # the last task's tab: let Chromium go
                await close_shared()
            return
        for closer in (self._browser, self._pw):
            if closer is None:
                continue
            try:
                await (closer.close() if closer is self._browser else closer.stop())
            except Exception:  # noqa: BLE001 - closing is best effort
                log.debug("browser close failed", exc_info=True)
        self._browser = self._pw = self.page = None


# -- the sites the user signed in to (Settings > Data & privacy) -----------------------

def signed_in_sites() -> list[dict[str, Any]]:
    """[{site, name, at}], newest first. Read from disk: no browser needed."""
    path = profile_dir()
    if not path:
        return []
    try:
        with open(os.path.join(path, SITES_FILE), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    rows = [{"site": k, "name": v.get("name") or k, "at": float(v.get("at") or 0)}
            for k, v in (data or {}).items() if isinstance(v, dict)]
    return sorted(rows, key=lambda r: -r["at"])


def _remember_site(url: str, name: str) -> None:
    path = profile_dir()
    host = (urlparse(url).hostname or url).lower().removeprefix("www.")
    if not path or not host:
        return
    file = os.path.join(path, SITES_FILE)
    try:
        with open(file, encoding="utf-8") as fh:
            data = json.load(fh) or {}
    except (OSError, ValueError):
        data = {}
    data[host] = {"name": name or host, "at": time.time()}
    os.makedirs(path, exist_ok=True)
    with open(file, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def forget_sign_ins() -> str:
    """Sign out everywhere: delete HeyTora's browser profile. "" when done,
    else why not (a job is using the browser right now)."""
    path = profile_dir()
    if not path or not os.path.isdir(path):
        return ""
    if _Shared.context is not None:
        return "A job is using the browser right now - try again when it has finished."
    try:
        shutil.rmtree(path)
    except OSError as e:
        return f"It could not all be deleted ({e.strerror or e}) - try again when no job is running."
    return ""


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
    await close_shared()


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
        text = f"{info['title']} — {info['url']}\n{info['text']}"
        if await self.has_password_field(page):
            info["sign_in"] = True
            text = ("This page asks to sign in. Call ask_to_sign_in with this URL: the user signs in "
                    "themselves, once, and this browser remembers it. Never type a password.\n\n" + text)
        return self.ok(text, **info)


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


async def _value_of(locator) -> "str | None":
    """What an input or textarea holds; None for a field that does not say
    (contenteditable), which is then neither skipped nor checked."""
    try:
        return await locator.input_value(timeout=2000)
    except Exception:  # noqa: BLE001
        return None


def _bare(text: str) -> str:
    """Letters and digits only: a phone field that adds brackets still took it."""
    return "".join(ch for ch in (text or "").lower() if ch.isalnum())


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
        # OR-13: look before acting, check after. Already holding the value,
        # it is not typed again (a repeated step must not double anything);
        # a field that did not take the value says so instead of "filled".
        before = await _value_of(loc.first)
        if before is not None and before == value and not args.get("submit"):
            return self.ok(f"{selector!r} already holds that - nothing typed", url=page.url, selector=selector,
                           unchanged=True)
        await loc.first.fill(value)
        after = await _value_of(loc.first)
        if after is not None and _bare(value) and _bare(value) not in _bare(after):
            return self.fail(f"{selector!r} did not take the value (it holds {after[:60]!r})", url=page.url,
                             selector=selector)
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


class AskToSignIn(Tool):
    """The user signs in, in a window they can see; the agent waits.

    Asked first (it opens a window on their screen), then the shared browser
    is shown at the site until they close it. From then on that site is
    signed in for every job, on this PC only - and listed in Settings, where
    it can be forgotten."""
    spec = make_spec(
        "ask_to_sign_in",
        "When a site needs the user to sign in: ask them, open the site in a window on their "
        "screen for them to sign in themselves, and wait until they close it. After that every "
        "job's browser is signed in to it. Then open the page again and carry on.",
        risk=RiskLevel.SAFE, category=CATEGORY,
        properties={"url": {"type": "string", "description": "The sign-in page or the site."},
                    "site": {"type": "string", "description": "Its name, e.g. 'Xero'."}},
        required=["url"],
    )

    async def run(self, args, ctx):
        if not playwright_available():
            return self.fail(NOT_INSTALLED, installed=False)
        url = str(args.get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            return self.fail("give the site's http(s) address")
        site = str(args.get("site") or "").strip() or (urlparse(url).hostname or url)
        if not profile_dir():
            return self.fail("this browser can't keep a sign-in on this PC; ask the user to do this step")
        if any(k != _key(ctx) for k in _sessions):
            return self.fail("another job is using the browser right now - try again when it has finished")
        req = ApprovalRequest(
            task_id=ctx.task_id or "", agent_id=ctx.agent_id or "",
            call=ToolCall("ask_to_sign_in", {"site": site}),
            reason=(f"{site} needs you to sign in. A browser window will open: sign in there, then "
                    "close the window. HeyTora's browser remembers it, on this PC only."))
        if ctx.approver is None or not await ctx.approver(req):
            return self.fail(f"the user didn't want to sign in to {site} now")
        mine = _sessions.pop(_key(ctx), None)
        if mine is not None:
            await mine.close()
        try:
            context = await shared_context(visible=True)
            if context is None:
                return self.fail("the browser could not open a window - ask the user to do this step")
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(url, wait_until="domcontentloaded")
            try:
                await page.bring_to_front()
            except Exception:  # noqa: BLE001
                pass
            deadline = time.monotonic() + SIGN_IN_WAIT_S
            while time.monotonic() < deadline:
                await asyncio.sleep(0.5)
                if ctx.control is not None and getattr(ctx.control, "cancelled", False):
                    break
                try:
                    if not context.pages:
                        break
                except Exception:  # noqa: BLE001 - the window was closed with the browser
                    break
            else:
                return self.fail(f"the sign-in window was open for {SIGN_IN_WAIT_S // 60} minutes; "
                                 "carry on without it, or ask again")
        finally:
            await close_shared()
        _remember_site(url, site)
        return self.ok(f"the user has signed in to {site} - open the page again and carry on",
                       site=site)


def tools() -> list[Tool]:
    return [OpenPage(), ExtractInformation(), ClickElement(), FillField(),
            ScreenshotPage(), CloseBrowser(), AskToSignIn()]
