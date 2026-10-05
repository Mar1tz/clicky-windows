"""The Marketing pack: tools, the marketing agent, its checklists.

Every tool is READ or DRAFT: the pack keeps the brand kit, the campaigns and
every post, ad and email - with their pictures - and never posts, sends or
pays for anything. Publishing becomes a POST tool once a platform connection
exists (catalogue gate 2); until then the export is the hand-over.

Pictures are designed by the agent as HTML and rendered by the core
render_design machinery (agents/tools/design_tools.py) at the size each
platform wants; make_image keeps them with their piece and shows the agent
what it made.

READ   marketing_overview, brand_kit, list_campaigns, campaign_details, check_campaign
DRAFT  set_brand_kit, create_campaign, save_campaign_plan, add_post, add_ad, add_email, update_piece,
       make_image, export_campaign
"""
from __future__ import annotations

import os
import re
from typing import Any

from agents.core import RiskLevel
from agents.extensions import AgentTemplate, ExtensionManifest, Schedule, Skill, Trigger, Workflow
from agents.tools.design_tools import PAGE_MM, SIZES, RenderError, render_design
from extensions.packkit import Level, Out, PackExtension, ToolBook, finding, rule, step

from .desk import AD_RULES, PLATFORMS, STATUSES, Desk

EXT = "marketing"
HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = ToolBook(EXT, noun="your marketing drafts")
CAMPAIGN = {"type": "string", "description": "The campaign: its name, id, or 'latest'."}
DATE = {"type": "string", "description": "YYYY-MM-DD."}
TIME = {"type": "string", "description": "HH:MM, 24-hour."}
TEXTS = {"type": "array", "items": {"type": "string"}}


def _found(problems: list[dict[str, str]], entity: str = "") -> list[dict[str, Any]]:
    return [finding(p["title"], action=p["action"], entity=entity, severity=p["severity"]) for p in problems]


def _saved(p: dict[str, Any]) -> dict[str, Any]:
    """A piece's result: the piece, and what was recorded (for the verifier)."""
    return {"piece": p, "recorded": f"piece #{p['id']} ({p['kind']}, {p['platform']}, {p['day'] or 'no date'})"}


def _piece_line(p: dict[str, Any]) -> str:
    where = PLATFORMS[p["platform"]].name if p["platform"] in PLATFORMS else (
        AD_RULES[p["platform"]]["name"] if p["platform"] in AD_RULES else "Email")
    words = p["data"].get("subject") if p["kind"] == "email" else (p["text"] or "").replace("\n", " ")
    when = f"{p['day']} {p['time']}".strip() or "no date"
    return (f"#{p['id']} {where} {p['kind']}, {when}: {words[:70]}" + ("…" if len(words or "") > 70 else "")
            + (" [picture]" if p["image"] else "") + f" - {p['status']}"
            + (f" ({len(p['problems'])} to fix)" if p["problems"] else ""))


# -- READ -------------------------------------------------------------------------------------------------------

@TOOLS.tool("marketing_overview", "The marketing desk at a glance: the brand, live campaigns, posts in the next "
            "week, what needs fixing.", Level.READ)
def marketing_overview(d: Desk, a: dict[str, Any]) -> Out:
    o = d.overview()
    n = o["counts"]
    text = (f"{o['brand']['name']}: {len(o['campaigns'])} campaign(s), {len(o['live'])} live. {n['post']} post(s), "
            f"{n['story']} story(ies), {n['ad']} ad(s), {n['email']} email(s), {o['images']} picture(s).")
    if o["upcoming"]:
        text += "\nNext 7 days:\n" + "\n".join(_piece_line(p) for p in o["upcoming"][:12])
    findings = []
    if not o["brand"].get("voice") or not o["brand"].get("colours"):
        findings.append(finding("The brand kit is not filled in", action="set the voice, audience and colours "
                                "so every post matches"))
    if o["problems"]:
        findings.append(finding(f"{o['problems']} problem(s) in drafted pieces", action="run check_campaign"))
    return Out(text, {"overview": {**o, "upcoming": [p["id"] for p in o["upcoming"]]}}, findings)


@TOOLS.tool("brand_kit", "The brand kit: name, tagline, voice, audience, colours, fonts, logo, website, hashtags. "
            "Every post, picture and email should match it.", Level.READ)
def brand_kit(d: Desk, a: dict[str, Any]) -> Out:
    b = d.brand()
    lines = [f"{k}: {', '.join(v) if isinstance(v, list) else v}" for k, v in b.items()
             if v not in ("", None, []) and k != "logo_path"]
    if b.get("logo_path"):
        lines.append(f"logo file (for pictures): {b['logo_path']}")
    missing = [k for k in ("voice", "audience", "colours") if not b.get(k)]
    findings = [finding(f"The brand kit has no {', '.join(missing)}",
                        action="ask the person, then save it with set_brand_kit")] if missing else []
    return Out("Brand kit:\n" + "\n".join(lines), {"brand": b}, findings)


@TOOLS.tool("list_campaigns", "Campaigns, newest first, optionally one status.", Level.READ,
            {"status": {"type": "string", "enum": ["planning", "ready", "running", "done"]}})
def list_campaigns(d: Desk, a: dict[str, Any]) -> Out:
    rows = d.campaigns(a.get("status", ""))
    lines = [f"#{c['id']} {c['name']} ({c['start']} to {c['end']}) - {c['status']}; channels: "
             f"{', '.join(c['channels']) or 'none yet'}" for c in rows]
    return Out(f"{len(rows)} campaign(s)" + (":\n" + "\n".join(lines) if lines else "."), {"campaigns": rows})


@TOOLS.tool("campaign_details", "One campaign: its brief, its plan file and every post, ad and email in it.",
            Level.READ, {"campaign": CAMPAIGN})
def campaign_details(d: Desk, a: dict[str, Any]) -> Out:
    c = d.campaign(a.get("campaign", "latest"))
    pieces = d.pieces(c["id"])
    text = (f"{c['name']} (#{c['id']}), {c['start']} to {c['end']}, {c['status']}. Goal: {c['goal'] or '-'}. "
            f"Audience: {c['audience'] or '-'}. Offer: {c['offer'] or '-'}. Channels: "
            f"{', '.join(c['channels']) or '-'}." + (f" Budget: {c['budget']:g}." if c["budget"] else "")
            + (f"\nPlan: {c['plan']}" if c["plan"] else "\nNo plan yet.")
            + (f"\n{len(pieces)} piece(s):\n" + "\n".join(_piece_line(p) for p in pieces) if pieces else ""))
    return Out(text, {"campaign": c, "pieces": pieces})


@TOOLS.tool("check_campaign", "Check a campaign before it goes out: every platform's limits, pictures missing, "
            "dates outside the campaign, two posts in one slot, channels with nothing in them.", Level.READ,
            {"campaign": CAMPAIGN})
def check_campaign(d: Desk, a: dict[str, Any]) -> Out:
    r = d.check(a.get("campaign", "latest"))
    c = r["campaign"]
    if not r["issues"]:
        return Out(f"{c['name']}: {r['pieces']} piece(s), nothing to fix.", {"check": r})
    text = (f"{c['name']}: {r['pieces']} piece(s), {r['fix']} thing(s) to fix and "
            f"{len(r['issues']) - r['fix']} to look at:\n" + "\n".join(f"- {i['title']} ({i['action']})"
                                                                     for i in r["issues"]))
    return Out(text, {"check": r}, _found(r["issues"], c["name"]))


# -- DRAFT ------------------------------------------------------------------------------------------------------

@TOOLS.tool("set_brand_kit", "Save the brand kit. Only what is given changes.", Level.DRAFT,
            {"name": {"type": "string"}, "tagline": {"type": "string"},
             "voice": {"type": "string", "description": "How the brand sounds, e.g. 'warm, witty, never pushy'."},
             "audience": {"type": "string", "description": "Who it talks to."},
             "colours": {**TEXTS, "description": "Hex colours, main first: ['#14213D', '#FCA311']."},
             "fonts": {**TEXTS, "description": "Font names, headings first."},
             "logo": {"type": "string", "description": "The logo file, inside the marketing folder (Brand/)."},
             "website": {"type": "string"}, "hashtags": TEXTS, "currency": {"type": "string"}})
def set_brand_kit(d: Desk, a: dict[str, Any]) -> Out:
    b = d.set_brand(**a)
    return Out(f"Brand kit for {b['name']} saved.", {"brand": b})


@TOOLS.tool("create_campaign", "Start a campaign from a brief. Channels: instagram, facebook, linkedin, x, "
            "threads, tiktok, pinterest, email, google ads, meta ads, linkedin ads. Dates default to the next two "
            "weeks.", Level.DRAFT,
            {"name": {"type": "string"}, "goal": {"type": "string"}, "audience": {"type": "string"},
             "offer": {"type": "string"}, "channels": TEXTS, "start": DATE, "end": DATE,
             "budget": {"type": "number"}}, ["name"])
def create_campaign(d: Desk, a: dict[str, Any]) -> Out:
    c = d.add_campaign(a["name"], goal=a.get("goal", ""), audience=a.get("audience", ""), offer=a.get("offer", ""),
                       channels=a.get("channels") or [], start=a.get("start", ""), end=a.get("end", ""),
                       budget=a.get("budget") or 0)
    return Out(f"Campaign #{c['id']} '{c['name']}' set up, {c['start']} to {c['end']}, channels: "
               f"{', '.join(c['channels']) or 'none yet'}. Folder: {c['folder']}", {"campaign": c})


@TOOLS.tool("save_campaign_plan", "Save the campaign plan (Markdown): the goal, who it is for, the one message, "
            "the offer, each channel and why, the budget split, the timeline, and how success is measured.",
            Level.DRAFT, {"campaign": CAMPAIGN, "plan": {"type": "string"}}, ["plan"])
def save_campaign_plan(d: Desk, a: dict[str, Any]) -> Out:
    c = d.save_plan(a.get("campaign", "latest"), a["plan"])
    return Out(f"Plan for '{c['name']}' saved ({c['words']} words): {c['plan']}",
               {"campaign": c, "path": c["plan"]})


@TOOLS.tool("add_post", "Draft a social post (or a story) for one platform, dated. It is checked against the "
            "platform's limits and saved even if something needs fixing.", Level.DRAFT,
            {"campaign": CAMPAIGN, "platform": {"type": "string", "enum": list(PLATFORMS)},
             "text": {"type": "string"}, "date": DATE, "time": TIME, "hashtags": TEXTS,
             "link": {"type": "string"}, "cta": {"type": "string", "description": "The call to action."},
             "kind": {"type": "string", "enum": ["post", "story"]}}, ["platform", "text"])
def add_post(d: Desk, a: dict[str, Any]) -> Out:
    p = d.add_post(a.get("campaign", "latest"), a["platform"], a["text"], day=a.get("date", ""),
                   time=a.get("time", ""), hashtags=a.get("hashtags") or [], link=a.get("link", ""),
                   kind=a.get("kind") or "post", cta=a.get("cta", ""))
    plat = PLATFORMS[p["platform"]]
    text = f"Drafted {_piece_line(p)}."
    if plat.fold and len(p["text"]) > plat.fold:
        text += f" On {plat.name} about the first {plat.fold} characters show before 'more' - lead with the hook."
    return Out(text, _saved(p), _found(p["problems"], f"#{p['id']}"))


@TOOLS.tool("add_ad", "Draft an ad. google-search: 3-15 headlines (30 characters each) and 2-4 descriptions (90 "
            "each), and the link. meta: primary text (125 shows), headline (40), description (30). linkedin: "
            "primary text (150 shows), headline (70). Checked and saved.", Level.DRAFT,
            {"campaign": CAMPAIGN, "platform": {"type": "string", "enum": list(AD_RULES)},
             "headlines": TEXTS, "descriptions": TEXTS, "primary_text": {"type": "string"},
             "headline": {"type": "string"}, "description": {"type": "string"}, "link": {"type": "string"},
             "cta": {"type": "string"}, "date": DATE}, ["platform"])
def add_ad(d: Desk, a: dict[str, Any]) -> Out:
    p = d.add_ad(a.get("campaign", "latest"), a["platform"], headlines=a.get("headlines"),
                 descriptions=a.get("descriptions"), primary_text=a.get("primary_text", ""),
                 headline=a.get("headline", ""), description=a.get("description", ""), link=a.get("link", ""),
                 cta=a.get("cta", ""), day=a.get("date", ""))
    return Out(f"Drafted {_piece_line(p)}.", _saved(p), _found(p["problems"], f"#{p['id']}"))


@TOOLS.tool("add_email", "Draft a marketing email: subject (60 characters show), preview text, the body (plain "
            "paragraphs; end with an unsubscribe line and the postal address), a link and its button text.",
            Level.DRAFT, {"campaign": CAMPAIGN, "subject": {"type": "string"}, "preview": {"type": "string"},
                          "body": {"type": "string"}, "date": DATE, "time": TIME, "audience": {"type": "string"},
                          "link": {"type": "string"}, "cta": {"type": "string"}}, ["subject", "body"])
def add_email(d: Desk, a: dict[str, Any]) -> Out:
    p = d.add_email(a.get("campaign", "latest"), a["subject"], a["body"], preview=a.get("preview", ""),
                    day=a.get("date", ""), time=a.get("time", ""), audience=a.get("audience", ""),
                    link=a.get("link", ""), cta=a.get("cta", ""))
    return Out(f"Drafted {_piece_line(p)}.", _saved(p), _found(p["problems"], f"#{p['id']}"))


@TOOLS.tool("update_piece", "Change a drafted post, ad or email - its words, date, hashtags, link - or move it "
            "along: draft, approved, scheduled, posted. Checked again.", Level.DRAFT,
            {"piece_id": {"type": "integer"}, "text": {"type": "string"}, "date": DATE, "time": TIME,
             "hashtags": TEXTS, "link": {"type": "string"}, "status": {"type": "string", "enum": list(STATUSES)},
             "subject": {"type": "string"}, "preview": {"type": "string"}, "headlines": TEXTS,
             "descriptions": TEXTS, "primary_text": {"type": "string"}, "headline": {"type": "string"},
             "description": {"type": "string"}, "cta": {"type": "string"}}, ["piece_id"])
def update_piece(d: Desk, a: dict[str, Any]) -> Out:
    fields = {k: v for k, v in a.items() if k != "piece_id"}
    p = d.update_piece(a["piece_id"], **fields)
    return Out(f"Updated {_piece_line(p)}.", _saved(p), _found(p["problems"], f"#{p['id']}"))


@TOOLS.tool("make_image", "Design the picture for a post, story, ad or email (piece_id) - or a free-standing one "
            "(name), like a poster or flyer - as ONE HTML page (inline CSS/SVG, no scripts), rendered at the right "
            "size for its platform. Use the brand's colours and fonts; put the logo in with <img src=\"{{logo}}\">. "
            "Big type, one message, strong contrast, nothing in the outer 5%. pdf=true also makes a print PDF "
            "(posters, flyers). Returns a preview: look at it and fix what is wrong.",
            Level.DRAFT, {"piece_id": {"type": "integer"}, "name": {"type": "string"}, "campaign": CAMPAIGN,
                          "html": {"type": "string"},
                          "size": {"type": "string", "enum": list(SIZES), "description": "Only to change the "
                                   "platform's own size; a poster: a4-poster, a3-poster, a5-flyer."},
                          "pdf": {"type": "boolean"}}, ["html"])
async def make_image(d: Desk, a: dict[str, Any]) -> Out:
    with d.lock:
        target, size, piece = d.image_target(a.get("piece_id"), a.get("name", ""), a.get("campaign", "latest"),
                                             a.get("size", ""))
        logo = d.brand().get("logo_path", "")
    if size not in SIZES:
        raise ValueError(f"unknown size {size!r}")
    w, h, label = SIZES[size]
    html = a["html"]
    note = ""
    if "{{logo}}" in html:
        if logo:
            html = html.replace("{{logo}}", "file:///" + logo.replace("\\", "/"))
        else:
            # Left out rather than refused: a refusal made the agent write the
            # whole design out again (on the first live run).
            html = re.sub(r"<img\b[^>]*\{\{logo\}\}[^>]*>", "", html, flags=re.I).replace("{{logo}}", "")
            note = " There is no logo in the brand kit, so none was put in."
    root = os.path.normcase(os.path.abspath(d.root))

    def inside(path: str) -> bool:
        full = os.path.normcase(os.path.abspath(path))
        return os.path.commonpath([full, root]) == root

    try:
        out = await render_design(html, target, w, h, scale=2 if size in PAGE_MM else 1, inside=inside)
        pdf = (await render_design(html, os.path.splitext(target)[0] + ".pdf", w, h, mm=PAGE_MM.get(size),
                                   inside=inside))["path"] if a.get("pdf") else ""
    except RenderError as exc:
        raise ValueError(str(exc)) from None
    if piece is not None:
        with d.lock:
            d.set_image(piece["id"], out["path"])
    what = f"#{piece['id']}" if piece else os.path.basename(target)
    return Out(f"Picture for {what}: {out['path']} ({out['width']}x{out['height']}, {label})"
               + (f", and the print PDF {pdf}" if pdf else "") + "." + note + " The preview is attached - check it.",
               {"path": out["path"], "pdf": pdf, "paths": [out["path"], *([pdf] if pdf else [])],
                "source": out["source"], "image_b64": out["preview_b64"], "image_mime": "image/jpeg"})


@TOOLS.tool("export_campaign", "Put the campaign together to hand over: the plan, every picture, a posting "
            "schedule (CSV), the same for Hootsuite's bulk upload, the ads, each email as HTML and text, and a "
            "calendar workbook. Nothing is posted.", Level.DRAFT, {"campaign": CAMPAIGN})
def export_campaign(d: Desk, a: dict[str, Any]) -> Out:
    r = d.export(a.get("campaign", "latest"))
    text = (f"'{r['campaign']['name']}' exported to {r['folder']}: {r['posts']} post(s), {r['images']} picture(s), "
            f"{r['ads']} ad(s), {r['emails']} email(s). Files: {', '.join(r['files'])}.")
    if r["issues"]:
        text += f" Still open: {len(r['issues'])} - see check_campaign."
    return Out(text, {"export": {k: v for k, v in r.items() if k != "issues"}, "path": r["folder"]},
               _found([i for i in r["issues"] if i["severity"] == "fix"], r["campaign"]["name"]))


# -- the agent and its checklists ---------------------------------------------------------------------------------

TEMPLATE = AgentTemplate(
    id="marketer", name="Marketing Agent", worker_type=EXT,
    mission="Plan and prepare marketing that is on brand and ready to go: a campaign plan, posts for each platform "
            "within its rules, a picture for every post, ads and emails - checked, and exported as a kit the "
            "person posts or schedules themselves.",
    handles=["campaign plans", "social media posts", "post pictures and posters", "Google, Meta and LinkedIn ads",
             "marketing emails", "the brand kit", "campaign kits to schedule"],
    tools=TOOLS.names() + ["read_file", "list_dir", "render_design"], max_auto_risk=RiskLevel.LOW,
    memory_schema={"voice": "how the brand sounds, lines that worked", "results": "what performed"},
    default_workflow="plan_campaign",
    artifacts=["campaign plans", "posts", "pictures", "ads", "emails", "campaign kits"],
    setup_questions=["Brand or business name", "Who your customers are", "Brand colours (hex)", "How the brand sounds"],
    extension_id=EXT)

_PICTURES = ("Design each picture with make_image (piece_id of the post): ONE HTML page in the brand's colours and "
             "fonts, the post's one message in big type, the logo if there is one. Look at the preview that comes "
             "back; if anything is cut off, crowded or hard to read, make it again.")


def plan_campaign(meta: dict[str, Any]) -> list:
    return [
        step("The brand", "brand_kit"),
        step("Set up the campaign", description=(
            "From the request (the task goal), create the campaign with create_campaign: a short name, the goal, "
            "the audience, the offer, the channels (only those that fit - if none were named, pick two or three "
            "social platforms and email), the dates (default: the next two weeks) and the budget if one was "
            "given. If the brand kit was empty, save what the request says about the brand with set_brand_kit.")),
        step("Write the plan", description=(
            "Write the campaign plan in Markdown and save it with save_campaign_plan: the goal and how it is "
            "measured, who it is for, the one message, the offer, each channel and why, the budget split, and the "
            "timeline week by week. Short and concrete.")),
        step("Draft the social posts", description=(
            "Draft the posts with add_post for each social channel of the campaign (campaign 'latest'), spread "
            "over its dates at sensible times - about two or three a week per channel, at most 12 in all. Each in "
            "the brand's voice, written for that platform, hook first, one call to action, a few relevant "
            "hashtags. Fix anything the tool reports.")),
        step("Make the pictures", description=_PICTURES + " Every post that needs one; stories too."),
        step("Ads and emails", description=(
            "Only for the campaign's ad and email channels: add_ad (Google search: 10-15 headlines and 4 "
            "descriptions within the limits; Meta or LinkedIn: primary text, headline, a picture with "
            "make_image) and add_email (subject, preview, body ending in an unsubscribe line and the postal "
            "address, a link and its button). If the campaign has neither, say so and finish.")),
        step("Check everything", "check_campaign", {"campaign": "latest"}),
        step("Fix what the check found", description=(
            "Fix every 'fix' the check reported with update_piece or make_image. If nothing needs fixing, "
            "finish.")),
        step("Export the kit", "export_campaign", {"campaign": "latest"}),
    ]


def make_design(meta: dict[str, Any]) -> list:
    """One picture asked for on its own - a poster, a flyer, a banner. Not a
    campaign: on the first live run "design a poster" was planned as a
    campaign with a Facebook post, and redrawn eight times."""
    return [
        step("The brand", "brand_kit"),
        step("Design it", description=(
            "Design what the request (the task goal) asks for with make_image - a short name for it, no "
            "piece_id; the size that fits (a4-poster for a poster unless another is asked, a5-flyer for a flyer, "
            "a social size for a post); pdf=true when it is for print. Use every fact the request gives, exactly "
            "(dates with the right year and weekday - see today's date), and invent none. Look at the preview "
            "and redo it only for a real problem: something cut off or crowding an edge, overlapping, too small "
            "to read, a wrong word or date. At most two more tries. Then finish, saying where the files are.")),
    ]


def social_posts(meta: dict[str, Any]) -> list:
    return [
        step("The brand and what is planned", "marketing_overview"),
        step("Set up the week", description=(
            "Create a campaign with create_campaign named for these posts (e.g. 'Posts - week of <date>'), with "
            "the platforms the request names (if none: the brand's usual ones, else Instagram and LinkedIn) and "
            "the dates of the week asked for (default: the coming seven days).")),
        step("Draft the posts", description=(
            "Draft the posts with add_post (campaign 'latest'): what the request asks for, else three or four a "
            "platform, each dated, in the brand's voice, hook first, one call to action. Fix what the tool "
            "reports.")),
        step("Make the pictures", description=_PICTURES),
        step("Check everything", "check_campaign", {"campaign": "latest"}),
        step("Export the kit", "export_campaign", {"campaign": "latest"}),
    ]


WORKFLOWS = [
    Workflow(id="plan_campaign", name="Plan a campaign", description="From a brief: the plan, posts for each "
             "platform with their pictures, ads and emails - checked and exported as a kit.",
             template_id=TEMPLATE.id, trigger=Trigger(), build_steps=plan_campaign, goal="Plan a marketing campaign"),
    Workflow(id="make_design", name="Design a picture", description="A poster, flyer, banner or post picture "
             "in the brand's colours - and a print PDF when it is for print.", template_id=TEMPLATE.id,
             trigger=Trigger(), build_steps=make_design, goal="Design a picture"),
    Workflow(id="social_posts", name="This week's posts", description="A week of posts for each platform with "
             "their pictures, checked and exported.", template_id=TEMPLATE.id,
             trigger=Schedule(every="weekly", at="09:00", weekday=0), build_steps=social_posts,
             goal="Draft this week's social media posts"),
]

SKILLS = [Skill("marketing.rules", "How this pack works", always=True, text=(
    "You are the marketer for one brand. Read the brand kit and keep to it: its voice, its audience, its colours "
    "and fonts in every picture. Everything you make is a draft in the marketing folder; you never post, send, "
    "boost or buy anything - the person does that, or imports the export into their scheduler. Write for each "
    "platform: Instagram leads with the hook (about 125 characters show), links there cannot be clicked ('link "
    "in bio'); LinkedIn is plainer and about 210 characters show; X is 280 characters with each link counting "
    "23. One message and one call to action per piece. Never invent prices, dates, discounts, reviews or "
    "claims the person did not give - leave a [placeholder] instead. Marketing emails end with an unsubscribe "
    "line and the sender's postal address.")),
    Skill("marketing.pictures", "Designing pictures", always=True, text=(
        "A picture is one HTML page at the platform's exact size. Use flexbox or grid; size type in px for "
        "screens (a 1080px post: headline 90-140px, body 36-48px). Keep text away from the outer 5% (apps crop "
        "and overlay it). Strong contrast: light text on the brand's dark colour, or the reverse. At most two "
        "fonts and about 12 words. Shapes, gradients and SVG icons are fine; photos only if the person gave "
        "you the file. When the brand kit has a logo, it goes in as <img src=\"{{logo}}\">. Keep the HTML "
        "compact - under about "
        "10 KB; an illustration only where it carries the message - every picture is written out in full and "
        "that takes time. After make_image, look at the preview and redo it only for a real problem: cut off, "
        "crowding an edge, overlapping, too small to read on a phone, a wrong word or date. At most two more "
        "tries per picture."))]


INTENT_RULES = [
    rule(EXT, TEMPLATE.id, r"\b(?:marketing|ad|advertising|social\s+media|launch|promo(?:tional)?|sales?)\s+"
         r"campaign\b", workflow="plan_campaign",
         reply="On it: the plan, posts for each platform with their pictures, ads and emails - checked, and "
               "exported for you to schedule.",
         verb=r"\b(?:please\s+)?(?:plan|create|make|build|run|start|prepare|put\s+together|design|set\s+up|"
              r"launch|do)\b"),
    rule(EXT, TEMPLATE.id, r"\b(?:social\s+media\s+posts?|(?:instagram|facebook|linkedin|tiktok|threads)\s+posts?|"
         r"posts?\s+for\s+(?:instagram|facebook|linkedin|tiktok|threads|x|twitter|social\s+media))\b",
         workflow="social_posts", reply="Drafting the posts and their pictures.",
         verb=r"\b(?:please\s+)?(?:write|create|make|draft|plan|prepare|do)\b"),
    rule(EXT, TEMPLATE.id, r"\b(?:ad\s+copy|google\s+ads?|facebook\s+ads?|meta\s+ads?|instagram\s+ads?|"
         r"linkedin\s+ads?|search\s+ads?)\b", reply="Writing the ads, checked against each platform's limits.",
         verb=r"\b(?:please\s+)?(?:write|create|make|draft|prepare|do)\b"),
    rule(EXT, TEMPLATE.id, r"\b(?:email\s+campaign|marketing\s+email|newsletter)\b",
         reply="Drafting the email, checked before it goes anywhere.",
         verb=r"\b(?:please\s+)?(?:write|create|make|draft|plan|prepare|do)\b"),
    rule(EXT, TEMPLATE.id, r"\bbrand\s+kit\b", reply="Setting up the brand kit.",
         verb=r"\b(?:please\s+)?(?:set\s+up|update|save|create|make|build|change)\b"),
    rule(EXT, TEMPLATE.id, r"\b(?:poster|flyer|banner|social\s+media\s+(?:graphic|image|picture)|"
         r"(?:instagram|facebook|linkedin)\s+(?:graphic|image|picture))\b", workflow="make_design",
         reply="Designing it in your brand's colours.",
         verb=r"\b(?:please\s+)?(?:make|design|create|draw|do|build)\b"),
]


class MarketingExtension(PackExtension):
    manifest = ExtensionManifest.load(HERE)
    STORE = Desk
    TOOLS = TOOLS
    TEMPLATES = [TEMPLATE]
    WORKFLOWS = WORKFLOWS
    SKILLS = SKILLS
    INTENT_RULES = INTENT_RULES
    FOLDER = "Marketing"
    DEFAULTS = {"name": "My brand", "tagline": "", "voice": "", "audience": "", "colours": [], "fonts": [],
                "logo": "", "website": "", "hashtags": [], "currency": ""}
    ROLE = "the marketer for a brand"
    # Only what needs no brief: a campaign or a poster is asked for in words.
    ACTIONS = [("This week's posts", "social_posts")]
    NOTES = "Every tool is READ or DRAFT: nothing is posted, sent, boosted or bought from this pack."
    EXTRA_TOOLS = ["render_design"]
    MAX_ITERATIONS = 30
    EXAMPLES = [("plan a marketing campaign for our spring sale", "plan_campaign"),
                ("create a social media campaign for the new menu", "plan_campaign"),
                ("write this week's social media posts", "social_posts"),
                ("draft linkedin posts about our new hire", "social_posts"),
                ("write google ads for the spring sale", ""),
                ("draft a newsletter for our customers", ""),
                ("set up our brand kit", ""),
                ("design a poster for the charity ball", "make_design")]

    def context_line(self, d: Desk) -> str:
        b = d.brand()
        camps = d.campaigns()
        latest = f" · latest campaign '{camps[0]['name']}' ({camps[0]['start']} to {camps[0]['end']})" if camps else ""
        return (f"Brand: {b['name']}" + (f" - {b['tagline']}" if b.get("tagline") else "")
                + f" · voice: {b.get('voice') or 'not set'} · colours: {', '.join(b['colours']) or 'not set'}"
                + f" · fonts: {', '.join(b['fonts']) or 'not set'}"
                + (f" · logo: {b['logo']}" if b.get("logo") else "") + latest)

    def snapshot(self, d: Desk) -> dict[str, Any]:
        o = d.overview()
        b = o["brand"]
        n = o["counts"]
        return {"title": b["name"], "meta": (b.get("tagline") or "") + (" · " if b.get("tagline") else "")
                + (", ".join(b["colours"]) or "no brand colours yet"),
                "numbers": [("Campaigns", len(o["campaigns"])), ("Posts", n["post"] + n["story"]),
                            ("Pictures", o["images"]), ("To fix", o["problems"])],
                "lists": [("Campaigns", [(f"{c['name']} · {c['start']} to {c['end']} · {c['status']}",
                                          c["plan"] or c["folder"]) for c in o["campaigns"][:8]]),
                          ("Next 7 days", [(_piece_line(p), p["image_path"]) for p in o["upcoming"][:8]])]}
