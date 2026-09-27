"""The Video creator pack: tools, the producer agent, its checklists.

Every tool is READ or DRAFT: the pack keeps the channel's own notes, files
and numbers, and never uploads, publishes or emails a brand. Publishing
becomes a POST tool when a platform connection exists (catalogue gate 2).

READ   studio_overview, list_videos, list_sponsor_deals, check_sponsor_deals, channel_stats
DRAFT  add_video_idea, save_script, build_shot_list, chapters_from_script, write_upload_details,
       set_video_stage, add_sponsor_deal, set_deal_status, import_channel_stats
"""
from __future__ import annotations

import os
import re
from typing import Any

from agents.core import RiskLevel
from agents.extensions import AgentTemplate, ExtensionManifest, Schedule, Skill, Trigger, Workflow
from extensions.packkit import Level, Out, PackExtension, ToolBook, finding, rule, step

from .studio import DEAL_STATES, STAGES, Studio

EXT = "creator"
HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = ToolBook(EXT, noun="your channel's records")
VIDEO = {"type": "string", "description": "The video: its title, slug or id."}
DATE = {"type": "string", "description": "YYYY-MM-DD."}


# -- READ -------------------------------------------------------------------------------------------------------

@TOOLS.tool("studio_overview", "The channel at a glance: the pipeline by stage, what slipped, what is next, "
            "sponsor deals due, the latest numbers.", Level.READ, {"as_of": DATE})
def studio_overview(s: Studio, a: dict[str, Any]) -> Out:
    o = s.overview(a.get("as_of", ""))
    st = o["stages"]
    text = (f"{o['channel']} on {o['platform']}, {o['as_of']}: {st['idea']} idea(s), {o['in_production']} in "
            f"production, {st['ready']} ready, {st['published']} published.")
    if o["next"]:
        text += f" Next planned: {o['next']['title']} on {o['next']['planned']}."
    if o["stats"]["videos"]:
        text += f" Latest numbers: {o['stats']['views']:,} views across {o['stats']['videos']} videos."
    findings = [finding(f"'{v['title']}' was planned for {v['planned']} and is still at {v['stage']}",
                        action="set a new date, or drop it", entity=v["title"]) for v in o["slipped"]]
    findings += [finding(f"'{t}' is ready but has no upload details", action="write the title, description and tags",
                         entity=t) for t in o["ready_without_upload"]]
    return Out(text, {"overview": o}, findings)


@TOOLS.tool("list_videos", "Videos, optionally at one stage.", Level.READ,
            {"stage": {"type": "string", "enum": list(STAGES)}})
def list_videos(s: Studio, a: dict[str, Any]) -> Out:
    rows = s.videos(a.get("stage", ""))
    lines = [f"#{v['id']} {v['title']} - {v['stage']}" + (f", ~{v['minutes']:g} min" if v["words"] else "")
             + (f", planned {v['planned']}" if v["planned"] else "") for v in rows]
    return Out(f"{len(rows)} video(s)" + (":\n" + "\n".join(lines) if lines else "."), {"videos": rows})


@TOOLS.tool("list_sponsor_deals", "Sponsor deals: open, or one status.", Level.READ,
            {"status": {"type": "string", "enum": ["open", *DEAL_STATES]}})
def list_sponsor_deals(s: Studio, a: dict[str, Any]) -> Out:
    rows = s.deals(a.get("status", ""))
    lines = [f"#{d['id']} {d['brand']}: {d['deliverable']} - {d['status']}" + (f", due {d['due']}" if d["due"] else "")
             + (f", fee {d['fee']:g}" if d["fee"] else "") for d in rows]
    return Out(f"{len(rows)} deal(s)" + (":\n" + "\n".join(lines) if lines else "."), {"deals": rows})


@TOOLS.tool("check_sponsor_deals", "Agreed sponsor deals that are late or due within a week.", Level.READ, {"as_of": DATE})
def check_sponsor_deals(s: Studio, a: dict[str, Any]) -> Out:
    due = s.deals_due(a.get("as_of", ""))
    findings = [finding(f"{d['brand']}: '{d['deliverable']}' is {d['days_late']} day(s) late",
                        action="deliver it, or tell the brand the new date", entity=d["brand"]) for d in due["late"]]
    soon = [f"{d['brand']} ({d['deliverable']}) in {d['days_left']} day(s)" for d in due["soon"]]
    text = (f"{len(due['late'])} late, {len(due['soon'])} due this week"
            + (": " + "; ".join(soon) if soon else "") + ".")
    return Out(text, {"deals_due": due}, findings)


@TOOLS.tool("channel_stats", "The latest imported channel numbers: top videos, click-through, the ones people "
            "see but do not click.", Level.READ, {"top": {"type": "integer"}})
def channel_stats(s: Studio, a: dict[str, Any]) -> Out:
    st = s.stats(int(a.get("top") or 5))
    if not st["videos"]:
        return Out("No channel numbers yet: export the Content table from YouTube Studio and import it.",
                   {"stats": st})
    top = "; ".join(f"{r['title']} {r['views']:,}" for r in st["top"])
    text = (f"{st['views']:,} views, {st['watch_hours']:g} watch hours, {st['subscribers']:+,} subscribers across "
            f"{st['videos']} videos (imported {st['imported'][:10]}). Top: {top}.")
    if st["avg_ctr"] is not None:
        text += f" Average click-through {st['avg_ctr']:g}%."
    findings = [finding(f"'{r['title']}' is seen but rarely clicked ({r['ctr']:g}% of {r['impressions']:,})",
                        action="try a new thumbnail or title", entity=r["title"]) for r in st["low_ctr"]]
    return Out(text, {"stats": st}, findings)


# -- DRAFT ------------------------------------------------------------------------------------------------------

@TOOLS.tool("add_video_idea", "Save a video idea with a working title.", Level.DRAFT,
            {"title": {"type": "string"}, "idea": {"type": "string"}, "planned": DATE}, ["title"])
def add_video_idea(s: Studio, a: dict[str, Any]) -> Out:
    v = s.add_idea(a["title"], a.get("idea", ""), a.get("planned", ""))
    return Out(f"Idea #{v['id']} saved: {v['title']}" + (f", planned {v['planned']}." if v["planned"] else "."),
               {"video": v})


@TOOLS.tool("save_script", "Save a video's script (Markdown: '## Section' headings, [B-roll: ...] lines). Returns "
            "its length at the creator's speaking pace.", Level.DRAFT,
            {"video": VIDEO, "text": {"type": "string"}}, ["video", "text"])
def save_script(s: Studio, a: dict[str, Any]) -> Out:
    v = s.save_script(a["video"], a["text"])
    return Out(f"Script for '{v['title']}' saved: {v['words']} words, about {v['minutes']:g} min at {s.wpm} words "
               f"a minute. File: {v['script']}", {"video": v})


@TOOLS.tool("build_shot_list", "A shot list from the script: A-roll per paragraph with timings, B-roll from "
            "the markers. Written as CSV.", Level.DRAFT, {"video": VIDEO}, ["video"])
def build_shot_list(s: Studio, a: dict[str, Any]) -> Out:
    r = s.shot_list(a["video"])
    return Out(f"Shot list for '{r['video']}': {r['a_roll']} A-roll, {r['b_roll']} B-roll. File: {r['path']}",
               {"shot_list": r})


@TOOLS.tool("chapters_from_script", "Chapter timestamps from the script's headings, checked against YouTube's "
            "rules.", Level.DRAFT, {"video": VIDEO}, ["video"])
def chapters_from_script(s: Studio, a: dict[str, Any]) -> Out:
    r = s.chapters(a["video"])
    text = f"Chapters for '{r['video']}' (about {r['length']} long):\n" + "\n".join(
        f"{c['at']} {c['title']}" for c in r["chapters"])
    findings = [finding(f"Chapters for '{r['video']}': {p}", action="add a section or merge short ones")
                for p in r["problems"]]
    return Out(text, {"chapters": r}, findings)


@TOOLS.tool("write_upload_details", "Save the title, description, tags and chapters, ready to paste into YouTube "
            "Studio, and check them against YouTube's limits. Nothing is uploaded.", Level.DRAFT,
            {"video": VIDEO, "title": {"type": "string"}, "description": {"type": "string"},
             "tags": {"type": "array", "items": {"type": "string"}},
             "chapters": {"type": "array", "items": {"type": "object", "properties": {
                 "at": {"type": "string"}, "title": {"type": "string"}}}}},
            ["video", "title", "description"])
def write_upload_details(s: Studio, a: dict[str, Any]) -> Out:
    r = s.write_upload(a["video"], a["title"], a["description"], a.get("tags") or [], a.get("chapters"))
    findings = [finding(f"Upload details for '{r['video']}': {p}", action="fix it before pasting")
                for p in r["problems"]]
    return Out(f"Upload details for '{r['video']}' saved ({r['title_length']}-character title, "
               f"{r['tags_length']} characters of tags). File: {r['path']}", {"upload": r}, findings)


@TOOLS.tool("set_video_stage", "Move a video along: idea, scripting, filming, editing, ready, published.",
            Level.DRAFT, {"video": VIDEO, "stage": {"type": "string", "enum": list(STAGES)},
                          "url": {"type": "string"}, "date": DATE}, ["video", "stage"])
def set_video_stage(s: Studio, a: dict[str, Any]) -> Out:
    v = s.set_stage(a["video"], a["stage"], url=a.get("url", ""), when=a.get("date", ""))
    return Out(f"'{v['title']}' is now {v['stage']}.", {"video": v})


@TOOLS.tool("add_sponsor_deal", "Record a sponsor deal: the brand, what you owe them, the fee, when it is due.",
            Level.DRAFT, {"brand": {"type": "string"}, "deliverable": {"type": "string"}, "fee": {"type": "number"},
                          "due": DATE, "video": VIDEO, "status": {"type": "string", "enum": list(DEAL_STATES)},
                          "notes": {"type": "string"}}, ["brand", "deliverable"])
def add_sponsor_deal(s: Studio, a: dict[str, Any]) -> Out:
    d = s.add_deal(a["brand"], a["deliverable"], fee=a.get("fee", 0), due=a.get("due", ""), video=a.get("video"),
                   status=a.get("status") or "agreed", notes=a.get("notes", ""))
    return Out(f"Deal #{d['id']} with {d['brand']}: {d['deliverable']}, {d['status']}"
               + (f", due {d['due']}." if d["due"] else "."), {"deal": d})


@TOOLS.tool("set_deal_status", "Move a sponsor deal along: pitched, agreed, delivered, invoiced, paid, lost.",
            Level.DRAFT, {"deal_id": {"type": "integer"}, "status": {"type": "string", "enum": list(DEAL_STATES)}},
            ["deal_id", "status"])
def set_deal_status(s: Studio, a: dict[str, Any]) -> Out:
    d = s.set_deal(a["deal_id"], a["status"])
    return Out(f"Deal #{d['id']} with {d['brand']} is now {d['status']}.", {"deal": d})


@TOOLS.tool("import_channel_stats", "Import channel numbers from a YouTube Studio export (the Content table as "
            "CSV).", Level.DRAFT, {"path": {"type": "string"}}, ["path"])
def import_channel_stats(s: Studio, a: dict[str, Any]) -> Out:
    path = a["path"] if os.path.isabs(a["path"]) else os.path.join(s.root, a["path"])
    st = s.import_stats(path)
    return Out(f"Imported {st['videos']} videos, {st['views']:,} views.", {"stats": st})


# -- the agent and its checklists ---------------------------------------------------------------------------------

TEMPLATE = AgentTemplate(
    id="producer", name="Producer Agent", worker_type=EXT,
    mission="Keep a video channel moving: ideas scripted, shoots planned, uploads ready with details that pass "
            "YouTube's rules, sponsor deals delivered on time, and the numbers read every week.",
    handles=["video ideas", "scripts", "shot lists", "chapters", "titles and descriptions", "sponsor deals",
             "channel stats"],
    tools=TOOLS.names() + ["read_file", "list_dir"], max_auto_risk=RiskLevel.LOW,
    triggers=[Schedule(every="weekly", at="09:00", weekday=0)],
    memory_schema={"style": "the creator's voice, hooks that worked", "brand": "sponsors and what they asked for"},
    default_workflow="weekly_review", artifacts=["scripts", "shot lists", "upload details", "deal list"],
    setup_questions=["Channel name", "Platform", "Speaking pace (words a minute)"], extension_id=EXT)


def weekly_review(meta: dict[str, Any]) -> list:
    return [step("The pipeline", "studio_overview"), step("Sponsor deals due", "check_sponsor_deals"),
            step("What the numbers say", "channel_stats")]


def prep_upload(meta: dict[str, Any]) -> list:
    video = str(meta.get("video") or "")
    return [step("Chapters from the script", "chapters_from_script", {"video": video}),
            step("Write the upload details", description=(
                f"Write the title (at most 100 characters, the hook first), the description and up to 15 tags for "
                f"'{video}' from its script (read_file on the script path), and save them with write_upload_details, "
                "including the chapters from the step before. Do not change the chapters' timestamps."))]


WORKFLOWS = [
    Workflow(id="weekly_review", name="Weekly channel review", description="Monday: the pipeline, deals due, "
             "what the numbers say.", template_id=TEMPLATE.id, trigger=Schedule(every="weekly", at="09:00", weekday=0),
             build_steps=weekly_review, goal="Review the channel for the week"),
    Workflow(id="prep_upload", name="Prepare an upload", description="Chapters from the script, then the title, "
             "description and tags, checked.", template_id=TEMPLATE.id, trigger=Trigger(), build_steps=prep_upload,
             goal="Prepare the upload details"),
]

SKILLS = [Skill("creator.rules", "How this pack works", always=True, text=(
    "You are the producer for a video creator. Scripts use '## Section' headings (they become chapters) and "
    "'[B-roll: what]' lines (they become the shot list). Everything you save is a draft in the channel's folder; "
    "you never upload, publish or message a brand - the creator does that. Keep the creator's own voice: when "
    "they wrote a line, keep it. YouTube's rules: titles up to 100 characters, descriptions up to 5000, tags up "
    "to 500 characters in all, chapters start at 0:00, at least three, each at least 10 seconds."))]


def _video_named(text: str) -> dict[str, str]:
    m = re.search(r"\bfor\s+(?:the\s+|my\s+)?(?:video\s+)?[\"'“]?(?P<v>[^\"'”.?!]+?)[\"'”]?\s*$", text, re.I)
    return {"video": m.group("v").strip()} if m else {}


INTENT_RULES = [
    rule(EXT, TEMPLATE.id, r"\b(?:channel|weekly\s+channel|youtube)\s+review\b", workflow="weekly_review",
         reply="On it: the pipeline, sponsor deals due, and what the numbers say."),
    rule(EXT, TEMPLATE.id, r"\b(?:upload\s+details|title\s+and\s+description|video\s+description)\b",
         workflow="prep_upload", reply="On it: chapters from the script, then the title, description and tags.",
         verb=r"\b(?:please\s+)?(?:prep|prepare|write|draft|get|do|make)\b", extract=_video_named),
    rule(EXT, TEMPLATE.id, r"\b(?:video\s+idea|idea\s+for\s+a\s+video)\b", reply="Saving the idea.",
         verb=r"\b(?:please\s+)?(?:add|save|note|log|record)\b"),
    rule(EXT, TEMPLATE.id, r"\bshot\s+list\b", reply="Building the shot list from the script."),
    rule(EXT, TEMPLATE.id, r"\bsponsor(?:ship)?\s+deal\b", reply="Adding the deal.",
         verb=r"\b(?:please\s+)?(?:add|log|record|save|track)\b"),
]


class CreatorExtension(PackExtension):
    manifest = ExtensionManifest.load(HERE)
    STORE = Studio
    TOOLS = TOOLS
    TEMPLATES = [TEMPLATE]
    WORKFLOWS = WORKFLOWS
    SKILLS = SKILLS
    INTENT_RULES = INTENT_RULES
    FOLDER = "Channel"
    DEFAULTS = {"name": "My channel", "platform": "YouTube", "wpm": 150}
    ROLE = "the producer for a video creator"
    ACTIONS = [("Weekly review", "weekly_review")]
    NOTES = "Every tool is READ or DRAFT: nothing is uploaded, published or sent from this pack."
    EXAMPLES = [('run my weekly channel review', 'weekly_review'), ('please prep the upload details for Why your logo looks cheap', 'prep_upload'), ('add a video idea about kerning', ''), ('log a sponsor deal with Canva', '')]

    def context_line(self, s: Studio) -> str:
        o = s.overview()
        return (f"Channel: {s.name} on {o['platform']} · speaks {s.wpm} words a minute · {o['in_production']} in "
                f"production · {len(o['deals_late']) + len(o['deals_soon'])} sponsor deal(s) late or due this week")

    def snapshot(self, s: Studio) -> dict[str, Any]:
        o = s.overview()
        return {"title": s.name, "meta": f"{o['platform']} · {s.wpm} words a minute",
                "numbers": [("Ideas", o["stages"]["idea"]), ("In production", o["in_production"]),
                            ("Ready", o["stages"]["ready"]),
                            ("Deals due", len(o["deals_late"]) + len(o["deals_soon"]))],
                "lists": [("In production", [(f"{v['title']} - {v['stage']}", v["script"]) for v in s.videos()
                                             if v["stage"] not in ("idea", "published")]),
                          ("Sponsor deals", [(f"{d['brand']}: {d['deliverable']} · {d['status']}"
                                              + (f" · due {d['due']}" if d["due"] else ""), "")
                                             for d in s.deals("open")]),
                          ("Top videos", [(f"{r['title']} · {r['views']:,} views", "") for r in o["stats"]["top"]])]}
