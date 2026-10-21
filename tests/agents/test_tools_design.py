"""render_design: an agent makes a picture by designing it as HTML (5 Oct 2026).

"Allow agents to generate images" - with Claude, through OmniRoute. Claude
lays out rather than paints, so a picture is one HTML page rendered by the
Edge every Windows PC has, and the model is shown what came out.

The browser is a stand-in here (it draws a PNG of the size asked for, or
writes a PDF), so the tests never start Edge or touch the network.
"""
from __future__ import annotations

import base64
import io
import os
import re

import pytest

from agents.core import PermissionPolicy, RiskLevel, ToolCall, ToolContext
from agents.tools import default_registry, design_tools
from agents.tools.process import CommandOutput

PIL = pytest.importorskip("PIL.Image")


@pytest.fixture
def browser(monkeypatch):
    """A fake Edge: --screenshot draws a picture the size of --window-size
    times the scale (flat when the page says BLANK); --print-to-pdf writes a
    PDF. Records every command line."""
    calls = []

    async def fake_run(args, *, cwd, timeout=60, env=None):
        calls.append(list(args))
        flags = {a.split("=", 1)[0]: (a.split("=", 1)[1] if "=" in a else "") for a in args if a.startswith("--")}
        page = args[-1]
        html = open(page.replace("file:///", "").replace("/", os.sep), encoding="utf-8").read()
        if "--screenshot" in flags:
            w, h = (int(x) for x in flags["--window-size"].split(","))
            s = int(flags.get("--force-device-scale-factor") or 1)
            im = PIL.new("RGB", (w * s, h * s), "white")
            if "BLANK" not in html:
                im.paste((20, 33, 61), (0, 0, w * s // 2, h * s // 2))
            im.save(flags["--screenshot"])
        if "--print-to-pdf" in flags:
            with open(flags["--print-to-pdf"], "wb") as fh:
                fh.write(b"%PDF-1.4 stand-in")
        return CommandOutput(exit_code=0, command=" ".join(args), cwd=cwd)

    monkeypatch.setattr(design_tools, "run_process", fake_run)
    monkeypatch.setattr(design_tools, "find_browser", lambda: r"C:\Edge\msedge.exe")
    return calls


async def _render(tmp_path, **args):
    ctx = ToolContext(workspace=str(tmp_path), policy=PermissionPolicy(max_auto_risk=RiskLevel.LOW))
    return await default_registry().execute(ToolCall("render_design", args), ctx)


async def test_a_design_becomes_a_picture_of_the_platforms_size_and_the_model_sees_it(tmp_path, browser):
    r = await _render(tmp_path, html="<h1>Spring Sale</h1>", path="out/post.png", size="instagram-portrait")
    assert r.ok, r.error
    assert (r.data["width"], r.data["height"]) == (1080, 1350)
    with PIL.open(tmp_path / "out" / "post.png") as im:
        assert im.size == (1080, 1350)
    preview = PIL.open(io.BytesIO(base64.b64decode(r.data["image_b64"])))
    assert max(preview.size) <= design_tools.PREVIEW_MAX and r.data["image_mime"] == "image/jpeg", \
        "a small preview for the model, not the full picture"
    source = (tmp_path / "out" / "post.html").read_text(encoding="utf-8")
    assert "<h1>Spring Sale</h1>" in source and "width:1080px;height:1350px" in source, "the source is kept, sized"


async def test_edge_runs_headless_in_a_throwaway_profile_never_the_persons(tmp_path, browser):
    await _render(tmp_path, html="<p>x</p>", path="a.png", width=300, height=250)
    args = browser[0]
    assert "--headless=new" in args and any(a.startswith("--user-data-dir=") for a in args)
    profile = next(a for a in args if a.startswith("--user-data-dir=")).split("=", 1)[1]
    assert "tora-render-" in profile, "its own temporary profile: a running Edge is never handed the job"
    assert "--disable-extensions" in args and "--window-size=300,250" in args


async def test_print_sizes_are_one_sheet_of_real_paper(tmp_path, browser):
    r = await _render(tmp_path, html="<h1>Charity Ball</h1>", path="poster.pdf", size="a4-poster")
    assert r.ok, r.error
    assert (tmp_path / "poster.pdf").read_bytes().startswith(b"%PDF")
    page = (tmp_path / "poster.html").read_text(encoding="utf-8")
    assert "@page{size:210mm 297mm;margin:0}" in page and "width:210mm;height:297mm" in page
    png = await _render(tmp_path, html="<h1>Charity Ball</h1>", path="poster.png", size="a4-poster")
    assert (png.data["width"], png.data["height"]) == (794 * 2, 1123 * 2), "print PNGs at twice the pixels"


async def test_a_jpg_is_a_jpeg(tmp_path, browser):
    r = await _render(tmp_path, html="<p>x</p>", path="ad.jpg", size="display-300x250")
    assert r.ok
    with PIL.open(tmp_path / "ad.jpg") as im:
        assert im.format == "JPEG" and im.size == (300, 250)


async def test_a_blank_design_is_sent_back(tmp_path, browser):
    r = await _render(tmp_path, html="<p style='color:#fff'>BLANK</p>", path="b.png", width=400, height=300)
    assert not r.ok and "one flat colour" in r.error


@pytest.mark.parametrize("html, why", [
    ('<img src="C:/Windows/win.ini">', "outside the workspace"),
    ('<img src="file:///C:/Users/someone/secret.png">', "outside the workspace"),
    ('<div style="background:url(../../elsewhere.png)">', "outside the workspace"),
    ("<script>alert(1)</script><p>x</p>", "no scripts"),
    ("   ", "empty"),
])
async def test_what_a_design_may_not_do(tmp_path, browser, html, why):
    r = await _render(tmp_path, html=html, path="x.png", width=400, height=300)
    assert not r.ok and why in r.error
    assert not browser, "refused before Edge is started"


async def test_its_own_files_web_fonts_and_data_are_fine(tmp_path, browser):
    (tmp_path / "logo.png").write_bytes(b"png")
    html = ('<link href="https://fonts.googleapis.com/css2?family=Inter" rel="stylesheet">'
            '<img src="logo.png"><img src="data:image/png;base64,AAAA"><svg><use href="#a"/></svg>'
            f'<img src="file:///{(tmp_path / "logo.png").as_posix()}">')
    assert (await _render(tmp_path, html=html, path="ok.png", width=400, height=300)).ok


async def test_sizes_and_paths_are_checked(tmp_path, browser):
    assert "end in .png" in (await _render(tmp_path, html="<p>x</p>", path="x.gif", width=10, height=10)).error
    assert "outside workspace" in (await _render(tmp_path, html="<p>x</p>", path="../x.png", size="story")).error
    assert "16-4096" in (await _render(tmp_path, html="<p>x</p>", path="x.png", width=10, height=10)).error
    assert "give a size" in (await _render(tmp_path, html="<p>x</p>", path="x.png")).error


async def test_no_browser_is_a_clear_no(tmp_path, monkeypatch):
    monkeypatch.setattr(design_tools, "find_browser", lambda: None)
    r = await _render(tmp_path, html="<p>x</p>", path="x.png", size="x-post")
    assert not r.ok and "no Edge or Chrome" in r.error


def test_a_fragment_or_a_whole_page_gets_the_size_first():
    frag = design_tools.page_html("<h1>Hi</h1>", 100, 50)
    assert frag.startswith("<!doctype html>") and "width:100px;height:50px" in frag
    whole = design_tools.page_html("<html><head><style>h1{color:red}</style></head><body><h1>Hi</h1></body></html>",
                                   100, 50)
    assert whole.index("width:100px") < whole.index("h1{color:red}"), "the design's own CSS comes after, and wins"
    bare = design_tools.page_html("<html><body>x</body></html>", 100, 50)
    assert re.search(r"<html><head><meta charset=\"utf-8\"><style>", bare)


def test_the_browser_can_be_named(tmp_path, monkeypatch):
    exe = tmp_path / "edge.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv("TORA_RENDER_BROWSER", str(exe))
    assert design_tools.find_browser() == str(exe)


def test_the_file_agent_can_make_pictures():
    from agents.workers.file import FileWorker
    assert "render_design" in FileWorker.default_tools
    assert "render_design" in FileWorker().system_prompt(type("C", (), {
        "agent": type("A", (), {"name": "File Agent"})(), "workspace": "W"})())


async def test_a_browser_that_writes_after_it_returns_is_waited_for(tmp_path, monkeypatch):
    """Edge 155 (19 Oct): the launcher returns at once; the picture lands ~1 s later.
    And an older file at the same path is never taken for the new one."""
    import asyncio as _asyncio

    async def late_run(args, *, cwd, timeout=60, env=None):
        flags = {a.split("=", 1)[0]: (a.split("=", 1)[1] if "=" in a else "") for a in args if a.startswith("--")}
        target = flags.get("--screenshot") or flags.get("--print-to-pdf")

        async def later():
            await _asyncio.sleep(0.4)
            if target.endswith(".png"):
                PIL.new("RGB", (320, 200), (20, 33, 61)).save(target)
            else:
                open(target, "wb").write(b"%PDF-1.4 stand-in")
        _asyncio.ensure_future(later())
        return CommandOutput(exit_code=0, command=" ".join(args), cwd=cwd)

    monkeypatch.setattr(design_tools, "run_process", late_run)
    monkeypatch.setattr(design_tools, "find_browser", lambda: r"C:\Edge\msedge.exe")
    old = tmp_path / "poster.png"
    PIL.new("RGB", (10, 10), "white").save(old)                  # yesterday's
    (tmp_path / "p.html").write_text("<p>Sale</p>", encoding="utf-8")
    seen = await design_tools.render(str(tmp_path / "p.html"), str(old), 320, 200)
    assert seen["path"] == str(old) and PIL.open(old).size == (320, 200), "the new picture, not the old one"
    pdf = await design_tools.render(str(tmp_path / "p.html"), str(tmp_path / "flyer.pdf"), 320, 200, pdf=True)
    assert (tmp_path / "flyer.pdf").read_bytes().startswith(b"%PDF") and pdf["path"].endswith("flyer.pdf")
