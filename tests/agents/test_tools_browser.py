"""M19: real browser tools against a local HTML file.

No network: pages are `file://` URLs written into tmp_path. The suite
skips cleanly when Playwright or its Chromium is missing, and one test
proves that absence degrades to the same failure the stubs gave.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

from agents.core import (
    ApprovalRequest, EventBus, EventKind, PermissionPolicy, RiskLevel, ToolCall,
    ToolContext, auto_deny,
)
from agents.tools import default_registry
from agents.tools import browser_tools

pytestmark = pytest.mark.skipif(not browser_tools.playwright_available(),
                                reason="playwright not installed")

PAGE = """<!doctype html><html><head><title>Test Shop</title></head><body>
<h1 id="h">Welcome to Test Shop</h1>
<p class="price">Price: $42</p>
<a href="about.html" id="about">About us</a>
<form id="f">
  <input id="name" name="name" type="text" placeholder="Your name">
  <button type="button" id="go" onclick="document.getElementById('out').innerText='clicked:'+document.getElementById('name').value">Go</button>
</form>
<div id="out"></div>
</body></html>"""

LOGIN = """<!doctype html><html><head><title>Login</title></head><body>
<form><input id="u" type="text"><input id="p" type="password"></form>
</body></html>"""

ABOUT = "<!doctype html><html><head><title>About</title></head><body><p>We sell things.</p></body></html>"


def _url(path) -> str:
    return "file:///" + str(path).replace("\\", "/")


@pytest.fixture
def site(tmp_path):
    (tmp_path / "index.html").write_text(PAGE, encoding="utf-8")
    (tmp_path / "about.html").write_text(ABOUT, encoding="utf-8")
    (tmp_path / "login.html").write_text(LOGIN, encoding="utf-8")
    return tmp_path


@pytest.fixture
def run(tmp_path):
    """Execute tool calls through the registry with HIGH auto-approve, on
    one event loop so the browser session persists between calls."""
    reg = default_registry()
    bus = EventBus()
    ctx = ToolContext(workspace=str(tmp_path), task_id="t-browser", events=bus,
                      policy=PermissionPolicy(max_auto_risk=RiskLevel.HIGH))
    loop = asyncio.new_event_loop()

    def _run(name, **args):
        return loop.run_until_complete(reg.execute(ToolCall(name, args), ctx))

    _run.ctx = ctx
    _run.bus = bus
    _run.loop = loop
    yield _run
    loop.run_until_complete(browser_tools.close_all())
    loop.close()


# -- reading -----------------------------------------------------------------

def test_open_page_returns_title_and_text(site, run):
    r = run("open_page", url=_url(site / "index.html"))
    assert r.ok, r.error
    assert r.data["title"] == "Test Shop"
    assert "Welcome to Test Shop" in r.output
    assert "Price: $42" in r.data["text"]


def test_open_page_refuses_other_schemes(site, run):
    r = run("open_page", url="javascript:alert(1)")
    assert not r.ok and "refusing" in r.error


def test_extract_whole_page_includes_links(site, run):
    run("open_page", url=_url(site / "index.html"))
    r = run("extract_information", query="the price")
    assert r.ok
    assert "$42" in r.output
    assert any(l["text"] == "About us" for l in r.data["links"])


def test_extract_by_selector(site, run):
    run("open_page", url=_url(site / "index.html"))
    r = run("extract_information", query="price", selector=".price")
    assert r.ok and r.output.strip() == "Price: $42"
    assert r.data["matches"] == 1


def test_extract_missing_selector_fails_cleanly(site, run):
    run("open_page", url=_url(site / "index.html"))
    r = run("extract_information", query="x", selector="#does-not-exist")
    assert not r.ok and "nothing matches" in r.error


# -- acting ------------------------------------------------------------------

def test_click_by_text_navigates(site, run):
    run("open_page", url=_url(site / "index.html"))
    r = run("click_element", text="About us")
    assert r.ok, r.error
    assert r.data["title"] == "About"
    assert "We sell things" in r.data["text"]


def test_fill_then_click_by_selector(site, run):
    run("open_page", url=_url(site / "index.html"))
    assert run("fill_field", selector="#name", value="Tora").ok
    r = run("click_element", selector="#go")
    assert r.ok
    assert "clicked:Tora" in r.data["text"]


def test_click_nothing_fails_cleanly(site, run):
    run("open_page", url=_url(site / "index.html"))
    r = run("click_element", text="No Such Button")
    assert not r.ok and "nothing to click" in r.error


def test_screenshot_lands_in_the_workspace(site, run, tmp_path):
    run("open_page", url=_url(site / "index.html"))
    r = run("screenshot_page", path="shots/home.png")
    assert r.ok, r.error
    assert os.path.isfile(r.data["path"])
    assert os.path.getsize(r.data["path"]) > 500


def test_screenshot_cannot_escape_the_workspace(site, run):
    run("open_page", url=_url(site / "index.html"))
    r = run("screenshot_page", path="../../escape.png")
    assert not r.ok and "outside" in r.error.lower()


# -- the password rule -------------------------------------------------------

def test_password_page_asks_even_when_always_allowed(site, run):
    """'Always allow fill_field' must not extend to credentials."""
    asked: list[ApprovalRequest] = []

    async def approver(req):
        asked.append(req)
        return False

    run.ctx.policy.allow_always("fill_field")
    run.ctx.approver = approver
    run("open_page", url=_url(site / "login.html"))
    r = run("fill_field", selector="#p", value="hunter2")
    assert not r.ok and "password" in r.error
    assert len(asked) == 1
    assert asked[0].call.arguments["value"] == "•••", "the secret must not reach the dialog"


def test_password_page_proceeds_when_a_human_approves(site, run):
    async def approver(req):
        return True

    run.ctx.approver = approver
    run("open_page", url=_url(site / "login.html"))
    r = run("fill_field", selector="#u", value="alice")
    assert r.ok
    assert "alice" in r.output


# -- lifecycle ---------------------------------------------------------------

def test_session_closes_when_the_task_ends(site, run):
    run("open_page", url=_url(site / "index.html"))
    assert "t-browser" in browser_tools._sessions

    # The orchestrator publishes TASK_STATUS on the runtime loop - the same
    # loop the browser lives on. Emitting from any other loop would
    # schedule the close in the wrong place, which is exactly what this
    # test used to do.
    async def finish():
        run.bus.emit(EventKind.TASK_STATUS, "completed", task_id="t-browser",
                     data={"status": "completed"})
        await asyncio.sleep(0.3)

    run.loop.run_until_complete(finish())
    assert "t-browser" not in browser_tools._sessions


def test_close_browser_tool(site, run):
    run("open_page", url=_url(site / "index.html"))
    r = run("close_browser")
    assert r.ok and r.data["closed"] is True
    assert run("close_browser").data["closed"] is False


# -- absence -----------------------------------------------------------------

def test_missing_playwright_fails_like_the_stubs(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_tools, "playwright_available", lambda: False)
    ctx = ToolContext(workspace=str(tmp_path),
                      policy=PermissionPolicy(max_auto_risk=RiskLevel.HIGH))
    r = asyncio.run(default_registry().execute(
        ToolCall("open_page", {"url": "https://example.com"}), ctx))
    assert not r.ok
    assert "pip install playwright" in r.error
    assert r.data.get("installed") is False


def test_browser_tools_are_high_risk_and_denied_by_default(tmp_path):
    ctx = ToolContext(workspace=str(tmp_path), approver=auto_deny)   # default ceiling: MEDIUM
    r = asyncio.run(default_registry().execute(
        ToolCall("open_page", {"url": "https://example.com"}), ctx))
    assert not r.ok and "denied" in r.error.lower()


# -- the gate: an agent, through the orchestrator ----------------------------

def test_agent_opens_a_local_page_and_reports_a_value(site, tmp_path):
    """No LLM: a scripted plan drives the browser worker end to end and
    the verifier accepts it. The model-driven version differs only in who
    writes the tool calls."""
    from agents.bootstrap import build_agent_manager
    from agents.core import Plan, Planner, Step, Task, TaskKind, TaskStatus

    class BrowsePlan(Planner):
        async def plan(self, ctx):
            return Plan(task_id=ctx.task.id, steps=[
                Step("Open the shop page",
                     tool_calls=[ToolCall("open_page", {"url": _url(site / "index.html")})]),
                Step("Read the price",
                     tool_calls=[ToolCall("extract_information",
                                          {"query": "price", "selector": ".price"})]),
                Step("Close the browser", tool_calls=[ToolCall("close_browser", {})]),
            ])

        async def replan(self, ctx, plan, failure):
            return plan

    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True,
                              max_auto_risk=RiskLevel.HIGH)
    mgr.planner = BrowsePlan()
    mgr.orchestrator.planner = BrowsePlan()
    mgr.start()
    try:
        task = mgr.wait(mgr.submit_task(Task(
            title="Check the price", goal="Find the price on the shop page",
            workspace=str(tmp_path), kind=TaskKind.BROWSER, worker_type="browser")).id, 90)
        assert task.status is TaskStatus.COMPLETED, task.error
        read = task.steps[1]
        assert read.results and read.results[0].ok
        assert "$42" in read.results[0].output
        assert "t-" not in browser_tools._sessions or task.id not in browser_tools._sessions
    finally:
        mgr.stop()


def test_a_field_already_holding_the_value_is_not_typed_again(site, run):
    """OR-13: look before acting - a repeated step must not double anything."""
    run("open_page", url=_url(site / "index.html"))
    first = run("fill_field", selector="#name", value="Tora")
    assert first.ok and not first.data.get("unchanged")
    again = run("fill_field", selector="#name", value="Tora")
    assert again.ok and again.data.get("unchanged") is True and "already holds" in again.output


# -- BR-01: the page as numbered controls ----------------------------------------

FORM = """<!doctype html><html><head><title>Contact</title></head><body>
<label for="em">Email</label><input id="em" type="email">
<input id="nm" type="text" placeholder="Your name" value="Sam">
<button type="button" id="send" onclick="document.getElementById('out').innerText='sent:'+document.getElementById('em').value">Send</button>
<input type="hidden" name="secret" value="x">
<div id="out"></div></body></html>"""


def _number(text, label):
    import re
    m = re.search(r"\[(\d+)\] \w+ \"" + re.escape(label), text)
    assert m, text
    return int(m.group(1))


def test_a_page_reads_as_numbered_controls_and_is_used_by_number(tmp_path, run):
    (tmp_path / "form.html").write_text(FORM, encoding="utf-8")
    r = run("open_page", url=_url(tmp_path / "form.html"))
    assert r.ok and "Controls (use their numbers" in r.output
    assert '"Your name" = "Sam"' in r.output and "hidden" not in r.output.split("Controls")[1]
    run.ctx.extras["personal"] = {"email": "sam@example.com"}
    email, send = _number(r.output, "Email"), _number(r.output, "Send")
    filled = run("fill_field", index=email, value="{{user.email}}")
    assert filled.ok and "{{user.email}}" in filled.output and "sam@example.com" not in filled.output
    clicked = run("click_element", index=send)
    assert clicked.ok
    out = run("extract_information", selector="#out")
    assert out.ok and out.output == "sent:sam@example.com"


def test_an_unknown_detail_is_asked_for_not_typed(tmp_path, run):
    (tmp_path / "form.html").write_text(FORM, encoding="utf-8")
    r = run("open_page", url=_url(tmp_path / "form.html"))
    miss = run("fill_field", index=_number(r.output, "Email"), value="{{user.phone}}")
    assert not miss.ok and "does not know their phone" in miss.error


def test_several_steps_in_one_go(tmp_path, run):
    (tmp_path / "form.html").write_text(FORM, encoding="utf-8")
    r = run("open_page", url=_url(tmp_path / "form.html"))
    done = run("page_actions", actions=[{"do": "fill", "index": _number(r.output, "Email"), "value": "a@b.co"},
                                         {"do": "click", "index": _number(r.output, "Send")}])
    assert done.ok and done.data["done"] == [f"fill [{_number(r.output, 'Email')}]",
                                             f"click [{_number(r.output, 'Send')}]"]
    assert run("extract_information", selector="#out").output == "sent:a@b.co"


# -- BR-02: observe, act, extract; the action cache -------------------------------

SHOP = """<!doctype html><html><head><title>Invoices</title></head><body>
<table><tr><th>Month</th><th>Total</th></tr><tr><td>September</td><td>$120</td></tr>
<tr><td>October</td><td>$95</td></tr></table>
<p>Invoices are kept for two years.</p>
<button type="button" onclick="document.getElementById('out').innerText='downloaded'">Download invoice</button>
<button type="button">Download receipt</button>
<input id="q" type="text" placeholder="Search invoices">
<div id="out"></div></body></html>"""


@pytest.fixture
def fresh_cache():
    from agents.tools import browser_verbs
    browser_verbs.use_cache(browser_verbs.ActionCache())
    yield browser_verbs
    browser_verbs.use_cache(None)


def test_observe_ranks_what_fits_and_extract_reads_tables(tmp_path, run, fresh_cache):
    (tmp_path / "shop.html").write_text(SHOP, encoding="utf-8")
    run("open_page", url=_url(tmp_path / "shop.html"))
    seen = run("browser_observe", instruction="the download invoice button")
    assert seen.ok and seen.output.splitlines()[0].endswith('"Download invoice" - click it')
    data = run("browser_extract", instruction="invoice totals")
    assert data.ok and data.data["tables"][0] == [{"Month": "September", "Total": "$120"},
                                                   {"Month": "October", "Total": "$95"}]
    assert "Invoices are kept for two years." in data.data["passages"]


def test_act_does_the_clear_one_asks_when_unsure_and_remembers(tmp_path, run, fresh_cache):
    (tmp_path / "shop.html").write_text(SHOP, encoding="utf-8")
    run("open_page", url=_url(tmp_path / "shop.html"))
    unsure = run("browser_act", instruction="download")
    assert not unsure.ok and "call again with index" in unsure.error and len(unsure.data["candidates"]) == 2
    pick = unsure.data["candidates"][0]
    done = run("browser_act", instruction="download", index=pick)
    assert done.ok and done.data["how"] == "named"
    assert run("extract_information", selector="#out").output == "downloaded"
    run("open_page", url=_url(tmp_path / "shop.html"))
    again = run("browser_act", instruction="download")
    assert again.ok and again.data["how"] == "remembered", "next time: no choosing"
    typed = run("browser_act", instruction="the search box", value="October")
    assert typed.ok and typed.data["how"] == "matched"
