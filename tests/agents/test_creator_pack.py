"""The Video creator pack on a fresh channel folder: no credentials, no network.

Script to shot list to chapters to upload details that pass YouTube's
rules; sponsor deals and their dates; a YouTube Studio export read; the
weekly review run as a workflow with no model.
"""
from __future__ import annotations

import csv
import os
from datetime import date, timedelta

import pytest

from agents.bootstrap import build_agent_manager
from agents.core import PermissionPolicy, RiskLevel, TaskStatus, ToolCall, ToolContext
from extensions.creator.studio import check_chapters, parse_script

SCRIPT = """Ever wondered why your logo looks cheap? It is almost always the spacing.

## The spacing rule
{a}
[B-roll: side by side of two logos]

## Quick fix
Nudge.

## Colour
{b}
[B-roll: palette swatches]
[Shot: close-up of the screen]

## Wrap up
{c}
"""


def _script() -> str:
    return SCRIPT.format(a=" ".join(["space"] * 150), b=" ".join(["colour"] * 75), c=" ".join(["bye"] * 30))


@pytest.fixture
def mgr(tmp_path):
    m = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    m.extensions.enable("creator")
    m.extensions.configure("creator", {"name": "Pixel & Pine", "wpm": 150})
    m.start()
    yield m
    m.stop()


def _studio(mgr):
    return mgr.extensions.get("creator").ensure_store(mgr.extensions._context("creator"))


def _run(mgr, tool: str, **args):
    ctx = ToolContext(workspace=_studio(mgr).root, policy=PermissionPolicy(max_auto_risk=RiskLevel.LOW))
    return mgr.runtime.call(mgr.tools.execute(ToolCall(tool, args), ctx), timeout=60)


def test_nothing_in_the_pack_can_publish_or_send(mgr):
    ext = mgr.extensions.get("creator")
    assert set(ext.TOOLS.levels().values()) == {"read", "draft"}


def test_a_script_becomes_a_timed_shot_list_and_chapters(mgr):
    assert _run(mgr, "add_video_idea", title="Why your logo looks cheap").ok
    saved = _run(mgr, "save_script", video="why your logo", text=_script())
    assert saved.ok, saved.error
    v = saved.data["video"]
    assert v["words"] == 13 + 150 + 1 + 75 + 30 and v["stage"] == "scripting"
    assert v["minutes"] == 1.8                                        # 269 words at 150 a minute

    shots = _run(mgr, "build_shot_list", video=str(v["id"])).data["shot_list"]
    assert (shots["a_roll"], shots["b_roll"]) == (5, 3)
    second = shots["shots"][1]
    assert (second["section"], second["type"], second["seconds"]) == ("The spacing rule", "A-roll", 60)
    with open(shots["path"], encoding="utf-8") as fh:
        assert len(list(csv.DictReader(fh))) == 8

    ch = _run(mgr, "chapters_from_script", video=str(v["id"]))
    chapters = ch.data["chapters"]["chapters"]
    # The 5.2 s hook is too short to be a chapter: 0:00 takes the spacing
    # rule's name. "Quick fix" (0.4 s) never gets one; it stays in the one before.
    assert [c["at"] for c in chapters] == ["0:00", "1:06", "1:36"]
    assert [c["title"] for c in chapters] == ["The spacing rule", "Colour", "Wrap up"]
    assert ch.data["findings"] == []


def test_chapters_follow_youtubes_rules():
    ok = [{"at": "0:00", "title": "a"}, {"at": "0:30", "title": "b"}, {"at": "1:10", "title": "c"}]
    assert check_chapters(ok) == []
    bad = [{"at": "0:05", "title": "a"}, {"at": "0:09", "title": "b"}]
    problems = check_chapters(bad)
    assert any("0:00" in p for p in problems) and any("at least 3" in p for p in problems)
    assert any("'b' starts 4 s" in p for p in problems)
    assert parse_script("[B-roll: sky]\nHello there")[0]["heading"] == "Intro"


def test_upload_details_are_checked_before_the_creator_pastes_them(mgr):
    _run(mgr, "add_video_idea", title="Logo spacing")
    good = _run(mgr, "write_upload_details", video="logo spacing", title="Why your logo looks cheap",
                description="The one spacing rule.", tags=["logo design", "branding"],
                chapters=[{"at": "0:00", "title": "Intro"}, {"at": "0:40", "title": "Spacing"},
                          {"at": "1:30", "title": "Colour"}])
    assert good.ok and good.data["findings"] == []
    with open(good.data["upload"]["path"], encoding="utf-8") as fh:
        text = fh.read()
    assert text.startswith("TITLE\nWhy your logo looks cheap") and "0:40 Spacing" in text and "logo design, branding" in text

    bad = _run(mgr, "write_upload_details", video="logo spacing", title="x" * 120, description="use <b>bold</b>",
               tags=["t" * 60] * 9)
    titles = [f["title"] for f in bad.data["findings"]]
    assert bad.ok and len(titles) == 3
    assert any("120 characters" in t for t in titles) and any("< or >" in t for t in titles)
    assert any("tags come to" in t for t in titles)


def test_sponsor_deals_late_and_due_are_flagged(mgr):
    today = date.today()
    _run(mgr, "add_sponsor_deal", brand="Canva", deliverable="60-second integration", fee=1200,
         due=(today - timedelta(days=2)).isoformat())
    _run(mgr, "add_sponsor_deal", brand="Squarespace", deliverable="Pinned comment", due=(today + timedelta(days=3)).isoformat())
    _run(mgr, "add_sponsor_deal", brand="Notion", deliverable="Maybe", status="pitched",
         due=(today - timedelta(days=9)).isoformat())
    r = _run(mgr, "check_sponsor_deals")
    assert r.output.startswith("1 late, 1 due this week")
    assert [f["title"] for f in r.data["findings"]] == ["Canva: '60-second integration' is 2 day(s) late"]
    assert _run(mgr, "set_deal_status", deal_id=1, status="delivered").ok
    assert _run(mgr, "check_sponsor_deals").data["findings"] == []
    assert not _run(mgr, "set_deal_status", deal_id=1, status="done").ok


def test_a_youtube_studio_export_is_read(mgr, tmp_path):
    export = tmp_path / "Table data.csv"
    export.write_text(
        "Content,Video title,Video publish time,Views,Watch time (hours),Subscribers,Impressions,"
        "Impressions click-through rate (%)\n"
        "Total,,,\"12,400\",610.5,85,\"150,000\",4.1\n"
        "a1,Why your logo looks cheap,\"Sep 1, 2026\",\"9,000\",450.2,70,\"60,000\",9.0\n"
        "a2,Kerning in 60 seconds,\"Sep 8, 2026\",3000,140.3,14,\"80,000\",1.2\n"
        "a3,Studio tour,\"Sep 15, 2026\",400,20,1,\"10,000\",6.4\n", encoding="utf-8-sig")
    r = _run(mgr, "import_channel_stats", path=str(export))
    assert r.ok, r.error
    stats = _run(mgr, "channel_stats")
    st = stats.data["stats"]
    assert (st["videos"], st["views"], st["subscribers"]) == (3, 12400, 85)
    assert st["top"][0]["title"] == "Why your logo looks cheap"
    assert [f["title"] for f in stats.data["findings"]] == [
        "'Kerning in 60 seconds' is seen but rarely clicked (1.2% of 80,000)"]
    assert not _run(mgr, "import_channel_stats", path=str(tmp_path / "missing.csv")).ok


def test_the_weekly_review_runs_as_a_workflow(mgr):
    s = _studio(mgr)
    s.add_idea("Old plan", planned=(date.today() - timedelta(days=5)).isoformat())
    s.add_deal("Canva", "Integration", due=(date.today() - timedelta(days=1)).isoformat())
    task = mgr.wait(mgr.submit("run my weekly channel review").id, 120)
    assert task.status is TaskStatus.COMPLETED, task.error
    titles = {f["title"] for f in task.metadata.get("findings", [])}
    assert "Canva: 'Integration' is 1 day(s) late" in titles
    assert any("'Old plan' was planned" in t for t in titles)


def test_requests_route_to_the_producer(mgr):
    for text, workflow in (("run my weekly channel review", "weekly_review"),
                           ("please prep the upload details for Why your logo looks cheap", "prep_upload"),
                           ("add a video idea about kerning", ""), ("log a sponsor deal with Canva", "")):
        intent = mgr.extensions.match_intent(text)
        assert intent is not None and intent.metadata["template"] == "producer", text
        assert intent.metadata.get("workflow", "") == workflow, text
    assert mgr.extensions.match_intent(
        "please prep the upload details for Why your logo looks cheap").metadata["video"] == "Why your logo looks cheap"


def test_names_that_match_two_videos_are_asked_about(mgr):
    s = _studio(mgr)
    s.add_idea("Logo spacing")
    s.add_idea("Logo colour")
    assert s.add_idea("Logo spacing")["slug"] == "logo-spacing-2"
    r = _run(mgr, "save_script", video="Logo", text="Hello")
    assert not r.ok and "which one" in r.error
    assert not _run(mgr, "set_video_stage", video="Logo colour", stage="viral").ok
    assert _run(mgr, "set_video_stage", video="Logo colour", stage="published", url="https://youtu.be/x").data[
        "video"]["published"] == date.today().isoformat()
    assert os.path.isdir(os.path.join(s.root, "Scripts"))
