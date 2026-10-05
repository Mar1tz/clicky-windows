"""The Marketing pack on a fresh folder: no credentials, no network, no model.

"Build a marketing campaign tool into the agents" (5 Oct 2026): a campaign
plan, posts for each platform within its rules with a picture for each, ads
and emails within theirs, the brand kit, and an export to schedule from -
drafts only, nothing posted. Pictures go through render_design; the browser
here is a stand-in that draws a PNG of the size asked for.
"""
from __future__ import annotations

import csv
import os
from datetime import date, timedelta

import pytest

from agents.bootstrap import build_agent_manager
from agents.core import PermissionPolicy, RiskLevel, ToolCall, ToolContext
from agents.tools import design_tools
from agents.tools.process import CommandOutput

PIL = pytest.importorskip("PIL.Image")
TODAY = date.today()


def _d(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


@pytest.fixture
def mgr(tmp_path):
    m = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    m.extensions.enable("marketing")
    m.extensions.configure("marketing", {"name": "Three Monkeys Café", "voice": "warm, cheeky, never pushy",
                                         "colours": ["#14213D", "#FCA311"], "fonts": ["Segoe UI"]})
    m.start()
    yield m
    m.stop()


@pytest.fixture
def browser(monkeypatch):
    seen = []

    async def fake_run(args, *, cwd, timeout=60, env=None):
        flags = {a.split("=", 1)[0]: (a.split("=", 1)[1] if "=" in a else "") for a in args if a.startswith("--")}
        seen.append(open(args[-1].replace("file:///", "").replace("/", os.sep), encoding="utf-8").read())
        if "--screenshot" in flags:
            w, h = (int(x) for x in flags["--window-size"].split(","))
            s = int(flags.get("--force-device-scale-factor") or 1)
            im = PIL.new("RGB", (w * s, h * s), "white")
            im.paste((20, 33, 61), (0, 0, w * s // 2, h * s // 2))
            im.save(flags["--screenshot"])
        if "--print-to-pdf" in flags:
            open(flags["--print-to-pdf"], "wb").write(b"%PDF-1.4")
        return CommandOutput(exit_code=0)

    monkeypatch.setattr(design_tools, "run_process", fake_run)
    monkeypatch.setattr(design_tools, "find_browser", lambda: "edge.exe")
    return seen


def _desk(mgr):
    return mgr.extensions.get("marketing").ensure_store(mgr.extensions._context("marketing"))


def _run(mgr, tool: str, **args):
    ctx = ToolContext(workspace=_desk(mgr).root, policy=PermissionPolicy(max_auto_risk=RiskLevel.LOW))
    return mgr.runtime.call(mgr.tools.execute(ToolCall(tool, args), ctx), timeout=60)


def _titles(result) -> str:
    return " | ".join(f["title"] for f in result.data.get("findings", []))


def _campaign(mgr, **extra):
    r = _run(mgr, "create_campaign", name="Spring Sale", goal="fill the café on weekday mornings",
             channels=["Instagram", "linkedin", "email", "google ads", "meta ads"], start=_d(0), end=_d(13), **extra)
    assert r.ok, r.error
    return r.data["campaign"]


def test_nothing_in_the_pack_can_post_send_or_pay(mgr):
    assert set(mgr.extensions.get("marketing").TOOLS.levels().values()) == {"read", "draft"}


def test_the_brand_kit_is_kept_and_checked(mgr):
    r = _run(mgr, "brand_kit")
    assert "Three Monkeys Café" in r.output and "#FCA311" in r.output
    assert _run(mgr, "set_brand_kit", tagline="Coffee worth getting up for", hashtags=["#threemonkeys"]).ok
    b = _run(mgr, "brand_kit").data["brand"]
    assert b["tagline"] == "Coffee worth getting up for" and b["hashtags"] == ["threemonkeys"]
    assert b["voice"] == "warm, cheeky, never pushy", "only what is given changes"
    bad = _run(mgr, "set_brand_kit", colours=["navy"])
    assert not bad.ok and "hex" in bad.error
    away = _run(mgr, "set_brand_kit", logo="C:/Windows/logo.png")
    assert not away.ok and "outside the marketing folder" in away.error


def test_a_campaign_from_a_brief(mgr):
    c = _campaign(mgr)
    assert c["channels"] == ["instagram", "linkedin", "email", "google-search-ads", "meta-ads"]
    assert os.path.isdir(os.path.join(c["folder"], "images"))
    later = _run(mgr, "create_campaign", name="Spring Sale")
    assert later.data["campaign"]["slug"] == "spring-sale-2", "a second of the same name gets its own folder"
    assert later.data["campaign"]["end"] == (TODAY + timedelta(days=13)).isoformat(), "two weeks by default"
    assert "fax" in _run(mgr, "create_campaign", name="Old school", channels=["fax"]).error
    plan = _run(mgr, "save_campaign_plan", campaign="Spring Sale", plan="# Spring Sale\n\nGoal: ...")
    assert plan.ok and open(plan.data["campaign"]["plan"], encoding="utf-8").read().startswith("# Spring Sale")


def test_each_platform_keeps_its_own_rules(mgr):
    _campaign(mgr)
    x = _run(mgr, "add_post", platform="x", text="a" * 281, date=_d(1), time="08:00")
    assert x.ok, "a draft that breaks a rule is still saved"
    assert "281 characters - the limit is 280" in _titles(x)
    link = "https://threemonkeys.example/a/very/long/path/that/goes/on/and/on/and/on/and/on/and/on/and/on"
    short = _run(mgr, "add_post", platform="x", text="b" * 250 + " " + link, date=_d(1), time="12:00")
    assert short.ok and not short.data["findings"], "on X a link counts as 23 characters"
    insta = _run(mgr, "add_post", platform="instagram", text="New menu " + link, date=_d(2),
                 hashtags=[f"tag{i}" for i in range(31)])
    assert "31 hashtags - at most 30" in _titles(insta) and "cannot be clicked" in _titles(insta)
    li = _run(mgr, "add_post", platform="linkedin", text="Hello " * 60, date=_d(3))
    assert "about the first 210 characters show" in li.output
    assert "myspace" in _run(mgr, "add_post", platform="myspace", text="hi").error
    from extensions.marketing.desk import platform_key
    assert platform_key("Twitter") == "x" and platform_key("IG") == "instagram"


def test_ads_keep_googles_and_metas_limits(mgr):
    _campaign(mgr)
    g = _run(mgr, "add_ad", platform="google-search", headlines=["Fresh coffee", "x" * 31],
             descriptions=["Good."], link="https://threemonkeys.example")
    t = _titles(g)
    assert "2 headlines - it needs at least 3" in t and "is 31 characters - the limit is 30" in t
    assert "1 descriptions - it needs at least 2" in t
    m = _run(mgr, "add_ad", platform="meta", primary_text="y" * 130, headline="Weekday mornings, sorted")
    assert "more is cut off - keep it to 125" in _titles(m)
    assert all(f["severity"] == "review" for f in m.data["findings"]), "Meta's are advice, not hard limits"


def test_an_email_needs_a_way_out(mgr):
    _campaign(mgr)
    e = _run(mgr, "add_email", subject="FREE COFFEE THIS WEEK", body="Come in!\n\nThe Three Monkeys")
    t = _titles(e)
    assert "all capitals" in t and "no unsubscribe line" in t and "no preview text" in t
    ok = _run(mgr, "add_email", subject="Your weekday mornings, sorted", preview="Half-price flat whites before 10",
              body="Hi there,\n\nCome in before 10.\n\nUnsubscribe: reply STOP. 1 High St, Town",
              link="https://threemonkeys.example", cta="See the menu")
    assert ok.ok and not ok.data["findings"]


def test_the_check_finds_what_would_go_wrong(mgr):
    _campaign(mgr)
    _run(mgr, "add_post", platform="instagram", text="Morning!", date=_d(1), time="08:00")
    _run(mgr, "add_post", platform="instagram", text="Again!", date=_d(1), time="08:00")
    _run(mgr, "add_post", platform="instagram", text="Too late", date=_d(30))
    r = _run(mgr, "check_campaign", campaign="spring")
    t = _titles(r)
    assert "needs a picture" in t and "outside the campaign" in t and "2 posts at" in t
    assert "LinkedIn is a channel of this campaign with no posts" in t
    assert "email is a channel of this campaign with no email" in t
    assert "Google search ads are a channel with no ad" in t and "no plan" in t


def test_a_post_gets_its_picture_at_its_platforms_size(mgr, browser):
    _campaign(mgr)
    p = _run(mgr, "add_post", platform="instagram", text="Morning!", date=_d(1)).data["piece"]
    r = _run(mgr, "make_image", piece_id=p["id"], html="<h1 style='font-size:120px'>Morning!</h1>")
    assert r.ok, r.error
    with PIL.open(r.data["path"]) as im:
        assert im.size == (1080, 1350), "Instagram's portrait post"
    assert r.data["image_b64"], "the agent is shown what it made"
    assert _run(mgr, "campaign_details").data["pieces"][0]["image"].endswith(".png")
    assert "needs a picture" not in _titles(_run(mgr, "check_campaign"))
    story = _run(mgr, "add_post", platform="instagram", kind="story", text="", date=_d(2)).data["piece"]
    s = _run(mgr, "make_image", piece_id=story["id"], html="<h1>Today</h1>")
    with PIL.open(s.data["path"]) as im:
        assert im.size == (1080, 1920)


def test_the_logo_goes_in_by_name_and_nothing_from_outside(mgr, browser):
    desk = _desk(mgr)
    _campaign(mgr)
    p = _run(mgr, "add_post", platform="linkedin", text="Hi", date=_d(1)).data["piece"]
    bare = _run(mgr, "make_image", piece_id=p["id"], html='<img class="logo" src="{{logo}}" alt=""><h1>Hi</h1>')
    assert bare.ok and "no logo in the brand kit" in bare.output, "left out, not refused: no second design"
    assert "<img" not in browser[-1] and "{{logo}}" not in browser[-1]
    with open(os.path.join(desk.folder("Brand"), "logo.png"), "wb") as fh:
        fh.write(b"png")
    assert _run(mgr, "set_brand_kit", logo="Brand/logo.png").ok
    assert _run(mgr, "make_image", piece_id=p["id"], html='<img src="{{logo}}"><h1>Hi</h1>').ok
    assert "Brand/logo.png" in browser[-1].replace("\\", "/") and "{{logo}}" not in browser[-1]
    sneaky = _run(mgr, "make_image", piece_id=p["id"], html='<img src="C:/Users/someone/private.jpg">')
    assert not sneaky.ok and "outside" in sneaky.error


def test_a_poster_with_no_campaign_and_its_print_pdf(mgr, browser):
    r = _run(mgr, "make_image", name="Charity Ball poster", size="a4-poster", pdf=True,
             html="<h1>Charity Ball</h1>")
    assert r.ok, r.error
    assert os.path.basename(os.path.dirname(r.data["path"])) == "Designs"
    assert r.data["pdf"].endswith(".pdf") and os.path.isfile(r.data["pdf"])
    assert r.data["paths"] == [r.data["path"], r.data["pdf"]], "both, so a checker sees the PDF too"


def test_the_checker_is_shown_every_file_a_step_made(tmp_path):
    from agents.core import ToolResult
    from agents.verification.provider_verifier import ProviderVerifier
    (tmp_path / "poster.png").write_bytes(b"png")
    (tmp_path / "poster.pdf").write_bytes(b"%PDF")
    res = ToolResult(call_id="1", tool_name="make_image", ok=True, data={
        "path": str(tmp_path / "poster.png"), "paths": [str(tmp_path / "poster.png"), str(tmp_path / "poster.pdf")]})
    seen = ProviderVerifier()._evidence([res], type("C", (), {"workspace": str(tmp_path)})())
    assert "poster.pdf" in seen and "poster.png" in seen


def test_the_kit_is_ready_to_schedule(mgr, browser):
    _campaign(mgr)
    _run(mgr, "save_campaign_plan", plan="# Spring Sale\n\nThe plan.")
    p = _run(mgr, "add_post", platform="instagram", text="Morning! <coffee> & cake", date=_d(1), time="08:07",
             hashtags=["coffee"]).data["piece"]
    _run(mgr, "make_image", piece_id=p["id"], html="<h1>Morning!</h1>")
    _run(mgr, "add_post", platform="linkedin", text="We're hiring baristas.", date=_d(2), time="12:30")
    _run(mgr, "add_ad", platform="google-search", headlines=["Fresh coffee", "Near you", "Open at 7"],
         descriptions=["Half-price before 10.", "Every weekday."], link="https://threemonkeys.example")
    _run(mgr, "add_email", subject="Mornings <sorted>", preview="Half-price before 10",
         body="Hi,\n\nCome in.\n\nUnsubscribe here. 1 High St", link="https://threemonkeys.example")
    r = _run(mgr, "export_campaign")
    assert r.ok, r.error
    out = r.data["export"]["folder"]
    files = set(os.listdir(out))
    assert {"plan.md", "schedule.csv", "hootsuite.csv", "ads.csv", "calendar.xlsx", "images"} <= files
    assert len(os.listdir(os.path.join(out, "images"))) == 1
    with open(os.path.join(out, "schedule.csv"), encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    assert [r_["platform"] for r_ in rows] == ["Instagram", "LinkedIn"]
    assert rows[0]["hashtags"] == "#coffee" and rows[0]["image"].startswith("images/")
    with open(os.path.join(out, "hootsuite.csv"), encoding="utf-8") as fh:
        first = next(csv.reader(fh))
    y, m, d = _d(1).split("-")
    assert first[0] == f"{d}/{m}/{y} 08:05", "Hootsuite's layout, on a five-minute mark"
    assert first[1].endswith("#coffee")
    email = next(f for f in files if f.startswith("email-") and f.endswith(".html"))
    page = open(os.path.join(out, email), encoding="utf-8").read()
    assert "Mornings &lt;sorted&gt;" in page and "Unsubscribe" in page and "<sorted>" not in page
    import openpyxl
    ws = openpyxl.load_workbook(os.path.join(out, "calendar.xlsx")).active
    assert ws.max_row == 3 + 4, "a title, a gap, the header, and one row per piece"


def test_the_dashboard_and_the_prompt_line(mgr, browser):
    _campaign(mgr)
    _run(mgr, "add_post", platform="instagram", text="Morning!", date=_d(1))
    ext = mgr.extensions.get("marketing")
    snap = ext.snapshot(_desk(mgr))
    assert snap["title"] == "Three Monkeys Café" and dict(snap["numbers"])["Posts"] == 1
    line = ext.context_line(_desk(mgr))
    assert "warm, cheeky" in line and "#14213D" in line and "Spring Sale" in line


def test_the_campaign_checklist_uses_tools_that_exist(mgr):
    ext = mgr.extensions.get("marketing")
    for wf in ext.get_workflows():
        for s in wf.steps({}):
            for call in s.tool_calls:
                assert call.tool_name in mgr.tools.names(), call.tool_name
    names = [s.title for s in ext.workflow("plan_campaign").steps({})]
    assert names[0] == "The brand" and names[-1] == "Export the kit"
    worker = ext.get_workers(None)[0]
    assert "render_design" in worker.default_tools and worker.max_iterations >= 30
