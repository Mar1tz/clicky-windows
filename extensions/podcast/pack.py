"""The Podcast production pack: tools, the producer, its checklists.

Every tool is READ or DRAFT: nothing is uploaded or published. Marking an
episode published is refused until its checklist passes - audio, notes,
valid chapters and a signed guest release.

READ   podcast_overview, episode_checklist, release_calendar, speaker_times
DRAFT  plan_episode, add_guest, update_guest, attach_transcript, find_clips, set_episode_chapters,
       write_show_notes, set_episode_status
"""
from __future__ import annotations

import os
from typing import Any

from agents.core import RiskLevel
from agents.extensions import AgentTemplate, ExtensionManifest, Schedule, Skill, Workflow
from extensions.packkit import Level, Out, PackExtension, ToolBook, finding, rule, step

from .studio import GUEST_STATUSES, STATUSES, Podcast, stamp

EXT = "podcast"
HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = ToolBook(EXT, noun="the show's records")
EPISODE = {"type": "string", "description": "Episode number or title."}


@TOOLS.tool("podcast_overview", "The show: open episodes, this week's releases and whether they are ready, guests "
            "without a signed release, gaps in the calendar.", Level.READ, {"as_of": {"type": "string"}})
def podcast_overview(p: Podcast, a: dict[str, Any]) -> Out:
    o = p.overview(a.get("as_of", ""))
    text = (f"{p.show}: {len(o['open'])} episode(s) in production, {len(o['this_week'])} due out this week, "
            f"{len(o['calendar']['gaps'])} empty release slot(s) in the next 4 weeks.")
    findings = [finding(f"Episode {c['episode']} ({c['title']}) is due {c['planned']} and not ready: "
                        + "; ".join(c["problems"]), action="finish it or move the date", entity=str(c["episode"]))
                for c in o["this_week"] if c["problems"]]
    findings += [finding(f"{g['name']} was recorded but has not signed a release", action="send the release form",
                         entity=g["name"]) for g in o["unsigned"]]
    return Out(text, {"overview": o}, findings)


@TOOLS.tool("episode_checklist", "Whether an episode can be published: audio, notes, chapters, guest release.",
            Level.READ, {"episode": EPISODE}, ["episode"])
def episode_checklist(p: Podcast, a: dict[str, Any]) -> Out:
    c = p.checklist(a["episode"])
    return Out(f"Episode {c['episode']}: " + ("ready." if c["ready"] else "not ready - " + "; ".join(c["problems"])),
               {"checklist": c})


@TOOLS.tool("release_calendar", "The release slots for the coming weeks, what fills them, and the gaps.", Level.READ,
            {"weeks": {"type": "integer"}})
def release_calendar(p: Podcast, a: dict[str, Any]) -> Out:
    c = p.calendar(int(a.get("weeks") or 8))
    return Out("\n".join(f"{s['date']}: " + (f"#{s['episode'][0]} {s['episode'][1]}" if s["episode"] else "(empty)")
                         for s in c["slots"]), {"calendar": c})


@TOOLS.tool("speaker_times", "Who spoke for how long in an episode (from its transcript).", Level.READ,
            {"episode": EPISODE}, ["episode"])
def speaker_times(p: Podcast, a: dict[str, Any]) -> Out:
    e = p.episode(a["episode"])
    if not e["speakers"]:
        return Out(f"Episode {e['number']} has no transcript yet.", {"speakers": {}})
    return Out(f"Episode {e['number']} ({stamp(e['duration'])}): " + ", ".join(
        f"{k} {v['share']}%" for k, v in e["speakers"].items()), {"speakers": e["speakers"]})


@TOOLS.tool("plan_episode", "Plan an episode: working title, release date, guest.", Level.DRAFT,
            {"title": {"type": "string"}, "planned": {"type": "string"}, "guest": {"type": "string"},
             "number": {"type": "integer"}}, ["title"])
def plan_episode(p: Podcast, a: dict[str, Any]) -> Out:
    e = p.plan(a["title"], planned=a.get("planned", ""), guest=a.get("guest", ""), number=a.get("number"))
    return Out(f"Episode {e['number']}: {e['title']}" + (f", out {e['planned']}" if e["planned"] else "")
               + (f", with {e['guest']['name']}" if e["guest"] else "") + ".", {"episode": {k: e[k] for k in
                                                                                        ("number", "title", "planned", "status")}})


@TOOLS.tool("add_guest", "Add a guest with their email and topic.", Level.DRAFT,
            {"name": {"type": "string"}, "email": {"type": "string"}, "topic": {"type": "string"}}, ["name"])
def add_guest(p: Podcast, a: dict[str, Any]) -> Out:
    g = p.guest(a["name"], email=a.get("email", ""), topic=a.get("topic", ""), create=True)
    return Out(f"Guest {g['name']} ({g['status']}).", {"guest": g})


@TOOLS.tool("update_guest", "Change a guest's status, or record their signed release.", Level.DRAFT,
            {"name": {"type": "string"}, "status": {"type": "string", "enum": list(GUEST_STATUSES)},
             "release_signed": {"type": "boolean"}}, ["name"])
def update_guest(p: Podcast, a: dict[str, Any]) -> Out:
    g = p.set_guest(a["name"], status=a.get("status", ""), release_signed=a.get("release_signed"))
    return Out(f"{g['name']}: {g['status']}, release {'signed' if g['release_signed'] else 'not signed'}.", {"guest": g})


@TOOLS.tool("attach_transcript", "Attach an episode's transcript (SRT, WebVTT or '[00:01:02] Name: text'): length "
            "and who spoke for how long.", Level.DRAFT, {"episode": EPISODE, "path": {"type": "string"}},
            ["episode", "path"])
def attach_transcript(p: Podcast, a: dict[str, Any]) -> Out:
    r = p.attach_transcript(a["episode"], a["path"] if os.path.isabs(a["path"]) else os.path.join(p.root, a["path"]))
    return Out(f"Episode {r['episode']}: {r['duration']}, {r['words']} words; " + ", ".join(
        f"{k} {v['share']}%" for k, v in r["speakers"].items()) + ".", {"transcript": r})


@TOOLS.tool("find_clips", "Candidate social clips (20-60 s by default) around the words given, or around questions.",
            Level.DRAFT, {"episode": EPISODE, "keywords": {"type": "array", "items": {"type": "string"}},
                          "top": {"type": "integer"}}, ["episode"])
def find_clips(p: Podcast, a: dict[str, Any]) -> Out:
    clips = p.clips(a["episode"], a.get("keywords") or [], top=int(a.get("top") or 3))
    return Out(f"{len(clips)} clip(s)" + (":\n" + "\n".join(f"{c['start']}-{c['end']} ({c['seconds']} s) {c['text'][:90]}"
                                                           for c in clips) if clips else "."), {"clips": clips})


@TOOLS.tool("set_episode_chapters", "Set an episode's chapters ([{at, title}]), check them, and write Podcasting 2.0 "
            "JSON chapters and a plain list.", Level.DRAFT,
            {"episode": EPISODE, "chapters": {"type": "array", "items": {"type": "object", "properties": {
                "at": {"type": "string"}, "title": {"type": "string"}}}}}, ["episode", "chapters"])
def set_episode_chapters(p: Podcast, a: dict[str, Any]) -> Out:
    r = p.set_chapters(a["episode"], a["chapters"])
    findings = [finding(f"Episode {r['episode']} chapters: {x}", action="fix the times") for x in r["problems"]]
    return Out(f"{len(r['chapters'])} chapter(s) for episode {r['episode']}. Files: {r['json']}", {"chapters": r}, findings)


@TOOLS.tool("write_show_notes", "Write the show notes: summary, guest, chapters, links.", Level.DRAFT,
            {"episode": EPISODE, "summary": {"type": "string"}, "links": {"type": "array", "items": {"type": "string"}}},
            ["episode", "summary"])
def write_show_notes(p: Podcast, a: dict[str, Any]) -> Out:
    r = p.show_notes(a["episode"], a["summary"], a.get("links"))
    return Out(f"Show notes for episode {r['episode']}: {r['path']}", {"notes": r})


@TOOLS.tool("set_episode_status", "Move an episode along; 'published' is refused until its checklist passes.",
            Level.DRAFT, {"episode": EPISODE, "status": {"type": "string", "enum": list(STATUSES)},
                          "audio": {"type": "string", "description": "The final audio file."}}, ["episode", "status"])
def set_episode_status(p: Podcast, a: dict[str, Any]) -> Out:
    e = p.set_status(a["episode"], a["status"], audio=a.get("audio", ""))
    return Out(f"Episode {e['number']} is {e['status']}.", {"episode": {k: e[k] for k in ("number", "title", "status")}})


TEMPLATE = AgentTemplate(
    id="podcast_producer", name="Podcast Producer", worker_type=EXT,
    mission="Get every episode out on time and complete: guests booked and released, transcripts read, chapters "
            "that work in podcast apps, show notes, clips for social, and no gaps in the calendar.",
    handles=["episodes", "guests", "transcripts", "chapters", "show notes", "clips", "release calendar"],
    tools=TOOLS.names() + ["read_file", "list_dir"], max_auto_risk=RiskLevel.LOW,
    triggers=[Schedule(every="weekly", at="10:00", weekday=0)], memory_schema={"guest": "who they are, what they said"},
    default_workflow="weekly_show", artifacts=["show notes", "chapters", "clip list"],
    setup_questions=["Show name", "Release day", "Clip length"], extension_id=EXT)

WORKFLOWS = [
    Workflow(id="weekly_show", name="The week's show", description="Monday: what goes out this week and whether it is "
             "ready, releases unsigned, the calendar.", template_id=TEMPLATE.id,
             trigger=Schedule(every="weekly", at="10:00", weekday=0),
             build_steps=lambda meta: [step("The show", "podcast_overview"), step("The calendar", "release_calendar")],
             goal="Check the week's show"),
]

SKILLS = [Skill("podcast.rules", "How this pack works", always=True, text=(
    "You produce a podcast. Timings come from the transcript, never guesses. Chapters start at 00:00:00, go in "
    "order and stay inside the episode. Show notes: two or three plain sentences on what the listener gets, then "
    "the guest, chapters and links. Clips should stand alone without context. You never upload or publish; an "
    "episode is marked published only when its checklist passes, including the guest's signed release."))]

INTENT_RULES = [
    rule(EXT, TEMPLATE.id, r"\b(?:the\s+|this\s+week'?s\s+|my\s+)?(?:podcast|show|episodes)\b", workflow="weekly_show",
         reply="Checking what goes out this week and whether it is ready.",
         verb=r"\b(?:please\s+)?(?:check|run|review|plan)\b"),
    rule(EXT, TEMPLATE.id, r"\bshow\s+notes\b", reply="Writing the show notes."),
    rule(EXT, TEMPLATE.id, r"\bclips?\b", reply="Finding clips in the transcript.",
         verb=r"\b(?:please\s+)?(?:find|pick|get|cut)\b"),
    rule(EXT, TEMPLATE.id, r"\b(?:a\s+)?guest\b", reply="Adding the guest.", verb=r"\b(?:please\s+)?(?:add|book|invite)\b"),
]


class PodcastExtension(PackExtension):
    manifest = ExtensionManifest.load(HERE)
    STORE = Podcast
    TOOLS = TOOLS
    TEMPLATES = [TEMPLATE]
    WORKFLOWS = WORKFLOWS
    SKILLS = SKILLS
    INTENT_RULES = INTENT_RULES
    FOLDER = "Podcast"
    DEFAULTS = {"show": "My podcast", "release_weekday": 1, "clip_min": 20, "clip_max": 60}
    ROLE = "the producer of a podcast"
    ACTIONS = [("This week", "weekly_show")]
    NOTES = "Every tool is READ or DRAFT; nothing is uploaded or published."
    EXAMPLES = [("check this week's podcast", "weekly_show"), ("write the show notes for episode 12", ""),
                ("find clips in episode 12", ""), ("book a guest for November", "")]

    def context_line(self, p: Podcast) -> str:
        return f"Podcast: {p.show} · releases on weekday {p.cfg.get('release_weekday', 1)} · {len(p.episodes(open_only=True))} in production"

    def snapshot(self, p: Podcast) -> dict[str, Any]:
        o = p.overview()
        return {"title": p.show, "meta": f"{len(o['open'])} episodes in production",
                "numbers": [("In production", len(o["open"])), ("Out this week", len(o["this_week"])),
                            ("Unsigned releases", len(o["unsigned"])), ("Empty slots", len(o["calendar"]["gaps"]))],
                "lists": [("Episodes", [(f"#{e['number']} {e['title']} · {e['status']}", e["notes"]) for e in o["open"]]),
                          ("Calendar", [(f"{s['date']} " + (f"#{s['episode'][0]}" if s["episode"] else "(empty)"), "")
                                        for s in o["calendar"]["slots"]]),
                          ("Releases to sign", [(g["name"], "") for g in o["unsigned"]])]}
