"""The Students pack: tools, the study coach agent, its checklists.

One firm line, in the tools and in the prompt: Tora helps a student learn
and plan. It never writes work that is handed in for marks - no tool here
produces an essay, an assignment or an answer to submit, and a request for
one is not routed to this pack.

Every tool is READ or DRAFT; nothing is submitted.

READ   study_overview, list_deadlines, todays_study, cards_due
DRAFT  add_course_topics, add_deadline, finish_deadline, build_study_plan, finish_study_session, save_notes,
       add_flashcards, review_flashcard, export_flashcards, add_source, write_reference_list
"""
from __future__ import annotations

import os
from typing import Any

from agents.core import RiskLevel
from agents.extensions import AgentTemplate, ExtensionManifest, Schedule, Skill, Trigger, Workflow
from extensions.packkit import Level, Out, PackExtension, ToolBook, finding, rule, step

from .study import KINDS, SOURCE_TYPES, STYLES, StudyDesk

EXT = "students"
HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = ToolBook(EXT, noun="your study records")
DATE = {"type": "string", "description": "YYYY-MM-DD."}
COURSE = {"type": "string", "description": "The course or module name."}


# -- READ -------------------------------------------------------------------------------------------------------

@TOOLS.tool("study_overview", "Today at a glance: sessions planned, deadlines ahead, anything late or missed, "
            "flashcards due.", Level.READ, {"as_of": DATE})
def study_overview(s: StudyDesk, a: dict[str, Any]) -> Out:
    o = s.overview(a.get("as_of", ""))
    nxt = "; ".join(f"{d['course']} {d['title']} in {d['days_left']} day(s)" for d in o["next"][:3]) or "none"
    text = (f"{o['as_of']}: {len(o['today'])} study session(s) today, {o['cards_due']} flashcard(s) due. "
            f"Next deadlines: {nxt}.")
    findings = [finding(f"{d['course']}: '{d['title']}' was due {d['due']}", action="finish it, or mark it done",
                        entity=d["course"]) for d in o["late"]]
    if len(o["missed"]) >= 3:
        findings.append(finding(f"{len(o['missed'])} planned study sessions were missed",
                                action="rebuild the plan so the rest still fits"))
    return Out(text, {"overview": o}, findings)


@TOOLS.tool("list_deadlines", "Deadlines ahead (exams, assignments, tests), soonest first.", Level.READ,
            {"include_done": {"type": "boolean"}, "as_of": DATE})
def list_deadlines(s: StudyDesk, a: dict[str, Any]) -> Out:
    rows = s.deadlines(as_of=a.get("as_of", ""), include_done=bool(a.get("include_done")))
    lines = [f"#{d['id']} {d['due']} {d['course']}: {d['title']} ({d['kind']}"
             + (f", {d['weight']:g}%" if d["weight"] is not None else "") + f") - {d['days_left']} day(s)"
             for d in rows]
    return Out(f"{len(rows)} deadline(s)" + (":\n" + "\n".join(lines) if lines else "."), {"deadlines": rows})


@TOOLS.tool("todays_study", "The study sessions planned for a day.", Level.READ, {"day": DATE})
def todays_study(s: StudyDesk, a: dict[str, Any]) -> Out:
    rows = s.sessions_on(a.get("day", ""))
    lines = [f"#{r['id']} {r['course']}: {r['topic']} ({r['minutes']} min){' - done' if r['done'] else ''}"
             for r in rows]
    return Out(f"{len(rows)} session(s)" + (":\n" + "\n".join(lines) if lines else ". Nothing planned."),
               {"sessions": rows})


@TOOLS.tool("cards_due", "Flashcards due for review, optionally for one course.", Level.READ,
            {"course": COURSE, "limit": {"type": "integer"}})
def cards_due(s: StudyDesk, a: dict[str, Any]) -> Out:
    rows = s.due_cards(a.get("course", ""), limit=int(a.get("limit") or 20))
    return Out(f"{len(rows)} card(s) due" + (": " + " | ".join(f"#{c['id']} {c['front']}" for c in rows[:10])
                                             if rows else "."), {"cards": rows})


# -- DRAFT ------------------------------------------------------------------------------------------------------

@TOOLS.tool("add_course_topics", "Add a course and the topics to revise, each with a difficulty 1 (easy) to 3 "
            "(hard): hard topics get more sessions.", Level.DRAFT,
            {"course": COURSE, "topics": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"}, "difficulty": {"type": "integer"}}}}}, ["course", "topics"])
def add_course_topics(s: StudyDesk, a: dict[str, Any]) -> Out:
    rows = s.add_topics(a["course"], a["topics"])
    return Out(f"{a['course']}: {len(rows)} topic(s).", {"topics": rows})


@TOOLS.tool("add_deadline", "Add an exam, assignment, test or presentation date.", Level.DRAFT,
            {"course": COURSE, "title": {"type": "string"}, "due": DATE,
             "kind": {"type": "string", "enum": list(KINDS)}, "weight": {"type": "number",
                                                                        "description": "Percent of the grade."}},
            ["course", "due"])
def add_deadline(s: StudyDesk, a: dict[str, Any]) -> Out:
    d = s.add_deadline(a["course"], a.get("title", ""), a["due"], kind=a.get("kind") or "assignment",
                       weight=a.get("weight"))
    return Out(f"Deadline #{d['id']}: {d['course']} {d['title']} ({d['kind']}) on {d['due']}.", {"deadline": d})


@TOOLS.tool("finish_deadline", "Mark a deadline done.", Level.DRAFT, {"deadline_id": {"type": "integer"}},
            ["deadline_id"])
def finish_deadline(s: StudyDesk, a: dict[str, Any]) -> Out:
    d = s.finish_deadline(a["deadline_id"])
    return Out(f"Done: {d['course']} {d['title']}.", {"deadline": d})


@TOOLS.tool("build_study_plan", "Build the revision plan: every course's topics fitted into the days before its "
            "exam, courses mixed within a day, the day before each exam kept for a full review. Replaces the "
            "sessions not yet done.", Level.DRAFT,
            {"start": DATE, "hours_per_day": {"type": "number"}})
def build_study_plan(s: StudyDesk, a: dict[str, Any]) -> Out:
    p = s.build_plan(start=a.get("start", ""), hours_per_day=a.get("hours_per_day"))
    findings = [finding(f"{course}: {n} session(s) do not fit before the exam",
                        action="add study hours a day, start earlier, or drop the easiest topics", entity=course)
                for course, n in p["not_fitted"].items()]
    return Out(f"Plan: {p['sessions']} session(s) over {p['days']} day(s), {p['per_day']} a day, for "
               + ", ".join(f"{c} (exam {d})" for c, d in p["exams"].items()) + f". File: {p['path']}",
               {"plan": p}, findings)


@TOOLS.tool("finish_study_session", "Mark a planned study session done.", Level.DRAFT,
            {"session_id": {"type": "integer"}}, ["session_id"])
def finish_study_session(s: StudyDesk, a: dict[str, Any]) -> Out:
    r = s.finish_session(a["session_id"])
    return Out(f"Session #{r['id']} ({r['topic']}) done.", {"session": r})


@TOOLS.tool("save_notes", "Save study notes for a topic (the student's own notes, or a summary of their "
            "material).", Level.DRAFT, {"course": COURSE, "topic": {"type": "string"}, "text": {"type": "string"}},
            ["course", "topic", "text"])
def save_notes(s: StudyDesk, a: dict[str, Any]) -> Out:
    r = s.save_notes(a["course"], a["topic"], a["text"])
    return Out(f"Notes saved: {r['course']} / {r['topic']} ({r['words']} words). File: {r['path']}", {"notes": r})


@TOOLS.tool("add_flashcards", "Add flashcards (front = question, back = answer) to a course. A card already there "
            "is skipped.", Level.DRAFT,
            {"course": COURSE, "cards": {"type": "array", "items": {"type": "object", "properties": {
                "front": {"type": "string"}, "back": {"type": "string"}}}}}, ["course", "cards"])
def add_flashcards(s: StudyDesk, a: dict[str, Any]) -> Out:
    r = s.add_cards(a["course"], a["cards"])
    return Out(f"{r['added']} card(s) added" + (f", {r['skipped']} already there." if r["skipped"] else "."),
               {"cards": r})


@TOOLS.tool("review_flashcard", "Record how well a card was remembered: 0 blank ... 3 with effort ... 5 perfect. "
            "Schedules the next review (spaced repetition).", Level.DRAFT,
            {"card_id": {"type": "integer"}, "grade": {"type": "integer"}}, ["card_id", "grade"])
def review_flashcard(s: StudyDesk, a: dict[str, Any]) -> Out:
    r = s.review(a["card_id"], a["grade"])
    return Out(f"Card #{r['id']}: next review {r['due']} (in {r['interval']} day(s)).", {"card": r})


@TOOLS.tool("export_flashcards", "Export a course's flashcards for Anki (tab-separated text).", Level.DRAFT,
            {"course": COURSE}, ["course"])
def export_flashcards(s: StudyDesk, a: dict[str, Any]) -> Out:
    r = s.export_cards(a["course"])
    return Out(f"{r['cards']} card(s) exported for Anki: {r['path']}", {"export": r})


@TOOLS.tool("add_source", "Record a source the student used: a book, a journal article or a web page, with its "
            "authors ('Surname, Given' or 'Given Surname'), year and details.", Level.DRAFT,
            {"course": COURSE, "type": {"type": "string", "enum": list(SOURCE_TYPES)}, "title": {"type": "string"},
             "authors": {"type": "array", "items": {"type": "string"}}, "year": {"type": "string"},
             "publisher": {"type": "string"}, "journal": {"type": "string"}, "volume": {"type": "string"},
             "issue": {"type": "string"}, "pages": {"type": "string"}, "doi": {"type": "string"},
             "url": {"type": "string"}, "site": {"type": "string"}, "accessed": DATE},
            ["course", "type", "title"])
def add_source(s: StudyDesk, a: dict[str, Any]) -> Out:
    src = s.add_source(a["course"], {k: v for k, v in a.items() if k != "course"})
    return Out(f"Source #{src['id']} added: {src['apa']}", {"source": src})


@TOOLS.tool("write_reference_list", "The course's reference list in APA 7 or Harvard, sorted, as a file.",
            Level.DRAFT, {"course": COURSE, "style": {"type": "string", "enum": list(STYLES)}}, ["course"])
def write_reference_list(s: StudyDesk, a: dict[str, Any]) -> Out:
    r = s.bibliography(a["course"], a.get("style", ""))
    return Out(f"{len(r['entries'])} reference(s), {r['style'].upper()}:\n" + "\n".join(r["entries"])
               + f"\nFile: {r['path']}", {"references": r})


# -- the agent and its checklists ---------------------------------------------------------------------------------

TEMPLATE = AgentTemplate(
    id="study_coach", name="Study Coach", worker_type=EXT,
    mission="Help a student learn and keep on top of their courses: a revision plan that fits, flashcards on "
            "spaced repetition, notes summarised from their material, sources referenced properly, no deadline "
            "missed. Never write work that is handed in for marks.",
    handles=["revision plans", "deadlines", "flashcards", "notes", "references"],
    tools=TOOLS.names() + ["read_file", "list_dir"], max_auto_risk=RiskLevel.LOW,
    triggers=[Schedule(every="weekdays", at="07:30")],
    memory_schema={"course": "how the student is doing, what they find hard"},
    default_workflow="daily_study", artifacts=["revision plan", "flashcards", "notes", "reference lists"],
    setup_questions=["Study hours a day", "Session length", "Reference style"], extension_id=EXT)

WORKFLOWS = [
    Workflow(id="daily_study", name="Today's study", description="Every weekday morning: today's sessions, "
             "deadlines, cards due.", template_id=TEMPLATE.id, trigger=Schedule(every="weekdays", at="07:30"),
             build_steps=lambda meta: [step("Where things stand", "study_overview"),
                                       step("Today's sessions", "todays_study"),
                                       step("Flashcards due", "cards_due")],
             goal="Plan today's study"),
    Workflow(id="revision_plan", name="Build the revision plan", description="Fit every topic before its exam.",
             template_id=TEMPLATE.id, trigger=Trigger(),
             build_steps=lambda meta: [step("Deadlines ahead", "list_deadlines"),
                                       step("Build the plan", "build_study_plan"),
                                       step("Today's sessions", "todays_study")],
             goal="Build the revision plan"),
]

SKILLS = [Skill("students.integrity", "What a study coach does and does not do", always=True, text=(
    "You help a student learn; you never do their assessed work. Do not write essays, assignments, lab reports, "
    "answers to graded questions or anything they will hand in, and do not paraphrase a source for them to pass "
    "off as their own. If asked, say so kindly and offer what you can do: explain the idea, quiz them, outline "
    "how they might approach it, check their references. Notes and flashcards come from their own material. "
    "Reference lists use the details they give you; ask for a missing year, publisher or URL rather than "
    "inventing one."))]

INTENT_RULES = [
    rule(EXT, TEMPLATE.id, r"\b(?:revision|study|exam)\s+(?:plan|timetable|schedule)\b", workflow="revision_plan",
         reply="Building your revision plan: every topic fitted before its exam."),
    rule(EXT, TEMPLATE.id, r"\b(?:today'?s\s+study|my\s+study\s+day|what\s+to\s+study\s+today)\b",
         workflow="daily_study", reply="Here's today: sessions, deadlines, and cards due.",
         verb=r"\b(?:please\s+)?(?:show|give|plan|run|start|get|check)\b"),
    rule(EXT, TEMPLATE.id, r"\bflash\s?cards?\b", reply="On the flashcards.",
         verb=r"\b(?:please\s+)?(?:make|create|add|write|review|export|quiz\s+me\s+on)\b"),
    rule(EXT, TEMPLATE.id, r"\b(?:reference\s+list|bibliography|references|citations?)\b",
         reply="Building the reference list from your sources.",
         verb=r"\b(?:please\s+)?(?:make|create|write|build|format|do|add)\b"),
    rule(EXT, TEMPLATE.id, r"\b(?:exam|assignment|deadline|test)\b", reply="Adding it to your deadlines.",
         verb=r"\b(?:please\s+)?(?:add|log|record|note|save)\b"),
]


class StudentsExtension(PackExtension):
    manifest = ExtensionManifest.load(HERE)
    STORE = StudyDesk
    TOOLS = TOOLS
    TEMPLATES = [TEMPLATE]
    WORKFLOWS = WORKFLOWS
    SKILLS = SKILLS
    INTENT_RULES = INTENT_RULES
    FOLDER = "Study"
    DEFAULTS = {"hours_per_day": 2, "session_minutes": 45, "style": "apa"}
    ROLE = "a study coach for a student"
    ACTIONS = [("Today", "daily_study"), ("Rebuild plan", "revision_plan")]
    NOTES = "Every tool is READ or DRAFT. Nothing is submitted, and no tool writes work that is handed in."

    def context_line(self, s: StudyDesk) -> str:
        o = s.overview()
        nxt = o["next"][0] if o["next"] else None
        return (f"Student: {o['courses']} course(s) · {s.hours_per_day:g} h a day in {s.session_minutes}-minute "
                f"sessions · references in {s.style.upper()} · "
                + (f"next: {nxt['course']} {nxt['title']} on {nxt['due']}" if nxt else "no deadlines ahead"))

    def snapshot(self, s: StudyDesk) -> dict[str, Any]:
        o = s.overview()
        return {"title": "Study", "meta": f"{s.hours_per_day:g} h a day · {s.session_minutes}-minute sessions · "
                                          f"{s.style.upper()} references",
                "numbers": [("Today", len(o["today"])), ("Cards due", o["cards_due"]),
                            ("Deadlines ahead", len(o["next"])), ("Late", len(o["late"]))],
                "lists": [("Today", [(f"{r['course']}: {r['topic']}{' ✓' if r['done'] else ''}", "")
                                     for r in o["today"]]),
                          ("Deadlines", [(f"{d['due']} {d['course']}: {d['title']} ({d['days_left']} d)", "")
                                         for d in o["next"]]),
                          ("Plan", [("Revision plan", os.path.join(s.root, "Plans", "Revision plan.md"))])]}
