"""The Podcast production pack on a fresh folder: no credentials, no network.

Transcripts in three formats (length, speaking time), clips around a word,
chapters checked and written as Podcasting 2.0 JSON, show notes, a publish
that waits for its checklist (signed release included), the calendar.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from agents.bootstrap import build_agent_manager
from agents.core import PermissionPolicy, RiskLevel, TaskStatus, ToolCall, ToolContext
from extensions.podcast.studio import parse_transcript

SRT = """1
00:00:00,000 --> 00:00:30,000
Host: Welcome to the show. Today we talk about sourdough.

2
00:00:30,000 --> 00:01:30,000
Ana: I started baking in 2020. The secret is a slow ferment.

3
00:01:30,000 --> 00:02:00,000
Host: Why does a slow ferment matter?

4
00:02:00,000 --> 00:03:00,000
Ana: Flavour. The long ferment builds flavour and makes it easier to digest.
"""


@pytest.fixture
def mgr(tmp_path):
    m = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    m.extensions.enable("podcast")
    m.extensions.configure("podcast", {"show": "Crumb", "release_weekday": 1})
    m.start()
    yield m
    m.stop()


def _p(mgr):
    return mgr.extensions.get("podcast").ensure_store(mgr.extensions._context("podcast"))


def _run(mgr, tool: str, **args):
    ctx = ToolContext(workspace=_p(mgr).root, policy=PermissionPolicy(max_auto_risk=RiskLevel.LOW))
    return mgr.runtime.call(mgr.tools.execute(ToolCall(tool, args), ctx), timeout=60)


@pytest.fixture
def ep(mgr, tmp_path):
    _run(mgr, "add_guest", name="Ana Ruiz", email="ana@example.test", topic="Sourdough")
    assert _run(mgr, "plan_episode", title="Slow bread", guest="Ana Ruiz", planned="2026-10-06").ok
    (tmp_path / "ep1.srt").write_text(SRT, encoding="utf-8")
    t = _run(mgr, "attach_transcript", episode="1", path=str(tmp_path / "ep1.srt"))
    assert t.ok, t.error
    return t.data["transcript"]


def test_nothing_is_uploaded_or_published_by_a_tool_level(mgr):
    assert set(mgr.extensions.get("podcast").TOOLS.levels().values()) == {"read", "draft"}


def test_a_transcript_gives_length_and_speaking_time(ep, mgr):
    assert (ep["duration"], ep["cues"]) == ("00:03:00", 4)
    assert ep["speakers"] == {"Ana": {"seconds": 120.0, "share": 67}, "Host": {"seconds": 60.0, "share": 33}}
    assert _run(mgr, "speaker_times", episode="Slow bread").output.endswith("Ana 67%, Host 33%")


def test_webvtt_and_plain_timestamps_are_read_too():
    vtt = parse_transcript("WEBVTT\n\n00:00:00.000 --> 00:00:05.000\n<v Sam>Hello there\n\n"
                           "00:00:05.000 --> 00:00:15.000\n<v Jo>Hi Sam</v>\n")
    assert [(c["speaker"], c["end"] - c["start"], c["text"]) for c in vtt] == [("Sam", 5.0, "Hello there"),
                                                                                 ("Jo", 10.0, "Hi Sam")]
    plain = parse_transcript("[00:00] Host: hi there\n[00:10] Ana: hello, lovely to be here\n")
    assert [(c["speaker"], c["start"], c["end"]) for c in plain][0] == ("Host", 0.0, 10.0)


def test_clips_around_a_word_and_never_overlapping(ep, mgr):
    clips = _run(mgr, "find_clips", episode="1", keywords=["ferment"], top=2).data["clips"]
    assert [(c["start"], c["end"]) for c in clips] == [("00:00:30", "00:01:30"), ("00:01:30", "00:02:00")]
    assert all(20 <= c["seconds"] <= 60 for c in clips)


def test_chapters_are_checked_and_written_for_podcast_apps(ep, mgr):
    good = _run(mgr, "set_episode_chapters", episode="1", chapters=[
        {"at": "00:00", "title": "Welcome"}, {"at": "01:30", "title": "Why slow"}, {"at": "2:00", "title": "Flavour"}])
    assert good.data["findings"] == []
    data = json.load(open(good.data["chapters"]["json"], encoding="utf-8"))
    assert data == {"version": "1.2.0", "chapters": [{"startTime": 0.0, "title": "Welcome"},
                                                     {"startTime": 90.0, "title": "Why slow"},
                                                     {"startTime": 120.0, "title": "Flavour"}]}
    bad = _run(mgr, "set_episode_chapters", episode="1", chapters=[
        {"at": "00:10", "title": "Late start"}, {"at": "00:05", "title": "Backwards"}, {"at": "05:00", "title": "Gone"}])
    assert [f["title"] for f in bad.data["findings"]] == [
        "Episode 1 chapters: the first chapter must start at 00:00:00",
        "Episode 1 chapters: 'Backwards' starts before or with the one before it",
        "Episode 1 chapters: 'Gone' starts at 00:05:00, after the episode ends (00:03:00)"]


def test_publishing_waits_for_the_checklist_and_the_signed_release(ep, mgr, tmp_path):
    refused = _run(mgr, "set_episode_status", episode="1", status="published")
    assert "no final audio file" in refused.error and "Ana Ruiz has not signed a release" in refused.error
    audio = tmp_path / "ep1.mp3"
    audio.write_bytes(b"ID3fake")
    _run(mgr, "set_episode_status", episode="1", status="edited", audio=str(audio))
    _run(mgr, "set_episode_chapters", episode="1", chapters=[{"at": "0", "title": "Welcome"},
                                                             {"at": "90", "title": "Why slow"}])
    notes = _run(mgr, "write_show_notes", episode="1", summary="Ana Ruiz on why slow bread tastes better.",
                 links=["https://example.test/recipe"]).data["notes"]
    text = open(notes["path"], encoding="utf-8").read()
    assert "**Guest:** Ana Ruiz - Sourdough" in text and "- 00:01:30 Why slow" in text
    assert _run(mgr, "episode_checklist", episode="1").data["checklist"]["problems"] == ["Ana Ruiz has not signed a release"]
    _run(mgr, "update_guest", name="Ana", status="recorded", release_signed=True)
    assert _run(mgr, "set_episode_status", episode="1", status="published").data["episode"]["status"] == "published"


def test_the_calendar_shows_the_empty_weeks(ep, mgr):
    c = _p(mgr).calendar(4, "2026-09-28")
    assert [s["date"] for s in c["slots"]] == ["2026-09-29", "2026-10-06", "2026-10-13", "2026-10-20"]
    assert c["slots"][1]["episode"] == (1, "Slow bread") and len(c["gaps"]) == 3


def test_the_weekly_check_flags_an_episode_not_ready(mgr):
    _run(mgr, "plan_episode", title="Rye", planned=(date.today() + timedelta(days=3)).isoformat())
    task = mgr.wait(mgr.submit("check this week's podcast").id, 120)
    assert task.status is TaskStatus.COMPLETED, task.error
    assert any(f["title"].startswith("Episode 1 (Rye) is due") for f in task.metadata.get("findings", []))
