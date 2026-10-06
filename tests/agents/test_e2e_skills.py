"""Skills instead of packs (docs/agents/SKILLS.md, 6 Oct 2026).

A skill is a text file - how the user likes a job done - that Tora follows
with the tools she already has. These tests hold what matters about them:
the files are read forgivingly and the user's copy wins; choosing one costs
nothing unless the words match; a skill's job gets its text, its name and
the tools for the desktop, the browser and files; voice can start one or
read it; schedules are read, not guessed; the browser's sign-ins are
listed and can be forgotten; and the app has no packs left in it.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.skills import Skill, SkillLibrary, parse
from agents.skills.library import SkillError

ROOT = Path(__file__).resolve().parents[2]

PLANTS = """---
name: Water the plants
when: the user wants the office plants watered
ask_before: buying anything
---

1. Open the plant app.
2. Log a watering for each plant.
"""


def _lib(tmp_path, starter=None) -> SkillLibrary:
    return SkillLibrary(starter=starter if starter is not None else tmp_path / "starter",
                        user=tmp_path / "mine")


def _write(folder: Path, name: str, text: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.md").write_text(text, encoding="utf-8")


class FakeProvider:
    def __init__(self, reply: str):
        self.reply, self.calls = reply, 0

    async def complete(self, messages, tools=None, system=None, max_tokens=None):
        self.calls += 1
        return SimpleNamespace(text=self.reply)


# ── the files ─────────────────────────────────────────────────────────────────

def test_every_starter_skill_reads_and_says_when_to_use_it():
    lib = SkillLibrary(user=Path(os.environ.get("TEMP", ".")) / "no-such-skills-folder")
    skills = lib.all()
    assert len(skills) >= 10 and not lib.problems, lib.problems
    for s in skills:
        assert s.name and s.when and s.body, s.id
        assert s.source == "starter"


def test_a_skill_file_is_read_and_a_broken_one_is_skipped_with_why(tmp_path):
    _write(tmp_path / "mine", "water-the-plants", PLANTS)
    _write(tmp_path / "mine", "nothing-when", "---\nname: Mystery\n---\nDo it.")
    _write(tmp_path / "mine", "unclosed", "---\nname: Half\nwhen: always")
    lib = _lib(tmp_path)
    [plants] = lib.all()
    assert (plants.name, plants.when, plants.ask_before) == (
        "Water the plants", "the user wants the office plants watered", ["buying anything"])
    assert plants.body.startswith("1. Open the plant app.") and plants.source == "yours"
    assert "no 'when:' line" in lib.problems["nothing-when.md"]
    assert "no closing ---" in lib.problems["unclosed.md"]


def test_your_copy_of_a_starter_skill_wins_and_deleting_it_brings_the_original_back(tmp_path):
    _write(tmp_path / "starter", "water-the-plants", PLANTS)
    lib = _lib(tmp_path)
    mine = lib.get("water-the-plants")
    mine.body = "Just the fern."
    lib.save(mine)
    edited = lib.get("water-the-plants")
    assert edited.source == "edited" and edited.body == "Just the fern."
    assert (tmp_path / "starter" / "water-the-plants.md").read_text(encoding="utf-8") == PLANTS, \
        "a starter skill is never edited in place"
    assert lib.delete("water-the-plants")
    assert lib.get("water-the-plants").source == "starter"
    assert not lib.delete("water-the-plants"), "a starter skill can't be deleted, only turned off"


def test_a_new_skill_gets_its_own_file_name_and_needs_a_name_and_a_when(tmp_path):
    lib = _lib(tmp_path)
    a = lib.save(Skill(id="", name="Send an invoice!", when="billing"))
    b = lib.save(Skill(id="", name="Send an invoice", when="billing again"))
    assert (a.id, b.id) == ("send-an-invoice", "send-an-invoice-2")
    assert parse((tmp_path / "mine" / "send-an-invoice.md").read_text(encoding="utf-8"),
                 "send-an-invoice").when == "billing"
    with pytest.raises(SkillError):
        lib.save(Skill(id="", name="No when", when=" "))


def test_a_skill_turned_off_is_never_offered(tmp_path):
    _write(tmp_path / "mine", "water-the-plants", PLANTS)
    lib = _lib(tmp_path)
    lib.set_enabled("water-the-plants", False)
    assert not lib.get("water-the-plants").enabled
    assert lib.catalogue() == "" and lib.match("water the office plants") == []
    lib.set_enabled("water-the-plants", True)
    assert "water-the-plants: Water the plants - use when" in lib.catalogue()


# ── choosing one ─────────────────────────────────────────────────────────────

def test_only_a_word_match_ever_reaches_the_model(tmp_path):
    lib = SkillLibrary(user=tmp_path / "mine")
    model = FakeProvider('{"skill": "send-an-invoice"}')
    assert asyncio.run(lib.pick("what's the weather like in Durban", model)) is None
    assert model.calls == 0, "a question no skill is about costs nothing"
    picked = asyncio.run(lib.pick("invoice Sarah for last week", model))
    assert picked is not None and picked.id == "send-an-invoice" and model.calls == 1


def test_the_model_saying_none_or_nonsense_means_no_skill(tmp_path):
    lib = SkillLibrary(user=tmp_path / "mine")
    for reply in ('{"skill": null}', "I think the invoice one?", '{"skill": "made-up"}'):
        assert asyncio.run(lib.pick("invoice Sarah", FakeProvider(reply))) is None, reply


def test_without_a_model_only_one_clear_match_is_taken(tmp_path):
    _write(tmp_path / "mine", "water-the-plants", PLANTS)
    lib = _lib(tmp_path)
    assert asyncio.run(lib.pick("water the office plants", None)).id == "water-the-plants"
    assert asyncio.run(lib.pick("plants", None)) is None, "one shared word is not enough to act on"


# ── a skill's job ────────────────────────────────────────────────────────────

@pytest.fixture
def mgr(tmp_path):
    from agents.bootstrap import build_agent_manager
    m = build_agent_manager(workspace_root=str(tmp_path / "work"), in_memory=True, offline=True)
    _write(Path(m.skills.user), "water-the-plants", PLANTS)
    m.start()
    yield m
    m.stop()


def test_the_workforce_has_the_skills_and_keeps_them_out_of_the_profile(mgr, tmp_path):
    assert str(mgr.skills.user).startswith(str(tmp_path)), "in-memory: never the user's folder"
    assert mgr.skills.get("send-an-invoice") is not None, "the starter skills"
    assert not hasattr(mgr, "extensions"), "no packs"


def test_asking_for_a_skills_job_makes_it_that_skills_job(mgr):
    intent = mgr.understand("please water the office plants")
    assert intent.is_task and intent.metadata == {"skill": "water-the-plants"}
    assert "Water the plants" in intent.reply


def test_a_skills_job_is_told_the_skill_and_can_reach_desktop_browser_and_files(mgr):
    from agents.core import Task, TaskKind
    task = Task(title="Water", goal="water the office plants", workspace=mgr.workspace_root,
                kind=TaskKind.COMPUTER, worker_type="computer", metadata={"skill": "water-the-plants"})
    spec = mgr._default_spec(task)
    assert spec.name == "Water the plants"
    for tool in ("open_app", "open_page", "ask_to_sign_in", "read_file", "click",
                 "write_workbook", "render_design"):
        assert tool in spec.tools, tool
    extras = mgr._extras_for(task, mgr.workers.get("computer"), spec)
    [told] = extras["skills"]
    assert "Log a watering for each plant." in told
    assert "Hard rule - stop and ask the user before: buying anything." in told


def test_the_planner_plans_the_skills_steps():
    from agents.planning.planner import ProviderPlanner
    ctx = SimpleNamespace(task=SimpleNamespace(goal="water the plants", metadata={}), workspace="w",
                          tools=SimpleNamespace(names=lambda: []), memory=None,
                          extras={"skills": ["## The user's skill: Water the plants\n1. Open the plant app."]})
    prompt = ProviderPlanner._prompt(ProviderPlanner.__new__(ProviderPlanner), ctx)
    assert "1. Open the plant app." in prompt and "Plan the steps this skill describes." in prompt


def test_the_final_check_judges_by_the_skill_too(tmp_path):
    """Seen 6 Oct: a shopping-list skill said "always add milk and bread";
    the agent did, and the final check failed the job for adding items
    nobody asked for - it was shown the request, never the skill."""
    from agents.core import Task
    from agents.verification.provider_verifier import ProviderVerifier
    skill = "## The user's skill: Shopping list\n3. Always add milk and bread at the end."
    ctx = SimpleNamespace(task=Task(title="List", goal="a shopping list with eggs", workspace=str(tmp_path)),
                          workspace=str(tmp_path), extras={"skills": [skill]})
    v = ProviderVerifier()
    for prompt in (v._task_prompt(ctx),
                   v._step_prompt(SimpleNamespace(title="Write it", description="", summary="",
                                                  tool_calls=[], results=[]), ctx)):
        assert "Always add milk and bread" in prompt and "part of the goal, not extra" in prompt
    ctx.extras = {}
    assert "part of the goal" not in v._task_prompt(ctx), "a job with no skill is judged as before"


# ── by voice ─────────────────────────────────────────────────────────────────

class _Voice:
    from companion_manager import CompanionManager as _CM
    _WORKER_FOR_KIND = _CM._WORKER_FOR_KIND
    _tool_delegate_task = _CM._tool_delegate_task
    _tool_read_skill = _CM._tool_read_skill
    skills_line = _CM.skills_line
    _wait_for_plan = staticmethod(_CM._wait_for_plan)

    def __init__(self, manager):
        self._agents = manager


def test_tora_hears_the_skills_and_starts_one_by_its_id(mgr):
    voice = _Voice(mgr)
    line = voice.skills_line()
    assert "water-the-plants: Water the plants" in line and "delegate_task with skill" in line
    out = asyncio.run(voice._tool_delegate_task({"goal": "water the plants", "skill": "water-the-plants"}))
    assert out["ok"] and out["agent"] == "Water the plants"
    task = mgr.get_task(out["task_id"])
    assert task.metadata["skill"] == "water-the-plants" and task.worker_type == "computer"
    assert "error" in asyncio.run(voice._tool_delegate_task({"goal": "x", "skill": "made-up"}))


def test_tora_reads_a_skill_to_guide_someone_through_it(mgr):
    out = asyncio.run(_Voice(mgr)._tool_read_skill({"id": "water-the-plants"}))
    assert out["ok"] and "Log a watering" in out["steps"] and out["ask_before"] == ["buying anything"]
    assert "error" in asyncio.run(_Voice(mgr)._tool_read_skill({"id": "nope"}))


def test_her_tools_have_skills_and_no_packs():
    from ai.gemini_realtime import tool_declarations
    decls = {d["name"]: d for d in tool_declarations()[0]["functionDeclarations"]}
    assert "skill" in decls["delegate_task"]["parameters"]["properties"]
    assert "read_skill" in decls
    actions = decls["manage_agents"]["parameters"]["properties"]["action"]["enum"]
    assert not any("extension" in a or a in ("open_dashboard", "create_agent") for a in actions)
    from config import _REALTIME_RULES, _TECHNICAL_RULES
    assert "EXTENSIONS" not in _REALTIME_RULES + _TECHNICAL_RULES


# ── on its own schedule ──────────────────────────────────────────────────────

@pytest.mark.parametrize("words, kind, every, at, extra", [
    ("day 08:00", "schedule", "daily", "08:00", {}),
    ("weekdays 9am", "schedule", "weekdays", "09:00", {}),
    ("Friday 16:00", "schedule", "weekly", "16:00", {"weekday": 4}),
    ("every fri at 4pm", "schedule", "weekly", "16:00", {"weekday": 4}),
    ("month 1 09:00", "schedule", "monthly", "09:00", {"day": 1}),
    ("last day of the month 17:00", "schedule", "monthly", "17:00", {"day": -1}),
])
def test_a_schedule_is_read_from_plain_words(words, kind, every, at, extra):
    from agents.skills.schedule import parse_every
    t = parse_every(words)
    assert (t.kind, t.every, t.at) == (kind, every, at), words
    for k, v in extra.items():
        assert getattr(t, k) == v, (words, k)


def test_a_folder_watch_and_words_it_cant_read(tmp_path):
    from agents.skills.schedule import ScheduleError, parse_every
    t = parse_every(f"new *.pdf in {tmp_path}")
    assert (t.kind, t.path, t.patterns) == ("folder", str(tmp_path), ["*.pdf"])
    assert parse_every(f"new file in {tmp_path}").patterns == ["*"]
    for bad in ("sometimes", "Friday 25:00", ""):
        with pytest.raises(ScheduleError):
            parse_every(bad)


def test_a_scheduled_skill_becomes_a_job_and_runs_as_that_skill(mgr, monkeypatch):
    from agents.skills.schedule import sync_jobs
    lib = mgr.skills
    plants = lib.get("water-the-plants")
    plants.every = "weekdays 9am"
    lib.save(plants)
    _write(Path(lib.user), "broken-time", "---\nname: B\nwhen: b\nevery: sometimes\n---\n")
    problems = sync_jobs(mgr)
    assert "broken-time" in problems, "a schedule it can't read is said, not guessed"
    [job] = [j for j in mgr.scheduler.jobs() if j["id"] == "skill:water-the-plants"]
    assert job["trigger"] == "weekdays at 09:00"
    ran = []
    monkeypatch.setattr(mgr, "submit_task", lambda task, agent=None: ran.append(task) or task)
    mgr.scheduler.fire(mgr.scheduler._jobs["skill:water-the-plants"])
    assert ran[0].metadata["skill"] == "water-the-plants" and ran[0].title == "Water the plants"
    lib.set_enabled("water-the-plants", False)
    sync_jobs(mgr)
    assert not [j for j in mgr.scheduler.jobs() if j["id"] == "skill:water-the-plants"], \
        "turned off: not scheduled"


# ── the browser's sign-ins (SK-06) ───────────────────────────────────────────

def test_sites_signed_in_to_are_listed_and_can_all_be_forgotten(tmp_path, monkeypatch):
    from agents.tools import browser_tools as bt
    monkeypatch.setenv("TORA_BROWSER_PROFILE", str(tmp_path / "browser"))
    assert bt.signed_in_sites() == [] and bt.forget_sign_ins() == ""
    bt._remember_site("https://www.xero.com/login", "Xero")
    bt._remember_site("https://mail.google.com/", "Gmail")
    assert {s["site"] for s in bt.signed_in_sites()} == {"xero.com", "mail.google.com"}
    assert bt.forget_sign_ins() == ""
    assert bt.signed_in_sites() == [] and not (tmp_path / "browser").exists()


def test_signing_in_is_asked_first_and_a_no_opens_nothing(tmp_path, monkeypatch):
    from agents.core import ToolContext
    from agents.tools import browser_tools as bt
    if not bt.playwright_available():
        pytest.skip("playwright not installed")
    monkeypatch.setenv("TORA_BROWSER_PROFILE", str(tmp_path / "browser"))
    asked = []

    async def no(req):
        asked.append(req.reason)
        return False

    ctx = ToolContext(workspace=str(tmp_path), task_id="t-sign", approver=no)
    r = asyncio.run(bt.AskToSignIn().run({"url": "https://www.xero.com/login", "site": "Xero"}, ctx))
    assert not r.ok and "didn't want to sign in" in r.error
    assert asked and "Xero needs you to sign in" in asked[0]
    assert bt._Shared.context is None and bt.signed_in_sites() == []


# ── the app ──────────────────────────────────────────────────────────────────

def test_the_bridge_saves_checks_and_drafts_skills(tmp_path, monkeypatch):
    monkeypatch.setenv("TORA_SKILLS_DIR", str(tmp_path / "mine"))
    from ui.app_bridge import AppBridge
    br = AppBridge()
    assert "Runs by itself" in br.save_skill({"name": "X", "when": "x", "every": "sometimes"})
    assert br.save_skill({"name": "", "when": "x"}) == "Give it a name"
    assert br.save_skill({"name": "Water the plants", "when": "plants", "body": "Water them.",
                          "ask_before": "buying, anything over R100", "every": "Friday 16:00"}) == ""
    mine = next(s for s in br.skills() if s["id"] == "water-the-plants")
    assert mine["source"] == "yours" and mine["ask_before"] == ["buying", "anything over R100"]
    br.set_skill_enabled("water-the-plants", False)
    assert not br.skill("water-the-plants")["enabled"]
    assert br.delete_skill("water-the-plants") and br.skill("water-the-plants") is None


def test_a_finished_job_drafts_a_skill_from_its_plan(monkeypatch):
    from ui.app_bridge import AppBridge
    br = AppBridge()
    monkeypatch.setattr(br, "job", lambda _tid: {
        "title": "Chase Bloom", "goal": "chase Bloom about invoice 12",
        "steps": [{"title": "Find invoice 12", "status": "done"},
                  {"title": "Was skipped", "status": "skipped"},
                  {"title": "Draft the reminder", "status": "done"}]})
    draft = br.skill_from_job("t1")
    assert draft["id"] == "" and draft["name"] == "Chase Bloom", "a draft: nothing saved yet"
    assert "1. Find invoice 12\n2. Draft the reminder" in draft["body"]
    assert "chase Bloom about invoice 12" in draft["when"]


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


class _Bridge:
    """What the Skills tab and the editor ask of the bridge."""

    def __init__(self):
        self.done = []
        self.list = [{"id": "send-an-invoice", "name": "Send an invoice", "when": "billing", "body": "",
                      "apps": "", "ask_before": [], "every": "", "source": "starter", "enabled": True,
                      "last": None},
                     {"id": "water-the-plants", "name": "Water the plants", "when": "plants",
                      "body": "", "apps": "", "ask_before": [], "every": "weekdays 9am",
                      "source": "yours", "enabled": False, "last": None}]

    def skills(self):
        return self.list

    def skill_problems(self):
        return {"bad.md": "it has no 'when:' line"}

    def set_skill_enabled(self, sid, on):
        self.done.append(("on", sid, on))

    def save_skill(self, data):
        self.done.append(("save", data))
        return "" if data["name"] else "Give it a name"

    def delete_skill(self, sid):
        self.done.append(("delete", sid))
        return True


def test_the_skills_tab_lists_them_switches_them_and_says_what_was_skipped(qapp):
    from PyQt6.QtWidgets import QLabel
    from ui.app_widgets import Switch
    from ui.skills_page import skills_card
    br, opened = _Bridge(), []
    card = skills_card(br, opened.append)
    texts = [l.text() for l in card.findChildren(QLabel)]
    assert "Send an invoice" in texts and "Water the plants" in texts
    assert any("Runs by itself: weekdays 9am" in t for t in texts)
    assert any(t.startswith("bad.md was skipped") for t in texts)
    switches = card.findChildren(Switch)
    assert [s.isChecked() for s in switches] == [True, False]
    switches[1].toggled.emit(True)
    assert br.done == [("on", "water-the-plants", True)]


def test_the_editor_saves_says_why_not_and_deletes_your_own(qapp):
    from PyQt6.QtWidgets import QLineEdit, QPushButton, QWidget
    from ui.skills_page import open_editor
    host = QWidget()
    host.resize(900, 700)
    br, saved = _Bridge(), []
    dlg = open_editor(host, br, None, lambda: saved.append(1))
    buttons = {b.text(): b for b in dlg.findChildren(QPushButton)}
    assert "Delete" not in buttons, "nothing to delete in a new one"
    buttons["Save skill"].click()
    assert br.done[-1][0] == "save" and not saved, "no name: not saved, and it says why"
    name, when, *_rest = dlg.findChildren(QLineEdit)
    name.setText("Water the plants")
    when.setText("the user wants the plants watered")
    buttons["Save skill"].click()
    assert br.done[-1][1]["name"] == "Water the plants" and saved == [1]
    mine = open_editor(host, br, br.list[1], lambda: saved.append(2))
    {b.text(): b for b in mine.findChildren(QPushButton)}["Delete"].click()
    assert br.done[-1] == ("delete", "water-the-plants") and saved[-1] == 2


def test_there_is_no_packs_page_and_its_old_links_open_skills(qapp, monkeypatch):
    from ui import app_pages
    assert [k for k, *_r in app_pages.PAGES] == ["home", "tora", "insights", "agents", "journal"]
    assert ("skills", "Skills") in app_pages.ToraPage.TABS
    from ui.app_window import ToraApp
    shown = []
    win = ToraApp.__new__(ToraApp)
    monkeypatch.setattr(ToraApp, "show_page", lambda self, key, _o=ToraApp.show_page:
                        shown.append(key) if key == "tora" else _o(self, key))
    win._pages = {"tora": SimpleNamespace(show_tab=lambda k: shown.append(("tab", k)))}
    for old in ("packs", "extensions", "pack/accounting"):
        ToraApp.show_page(win, old)
    assert shown == ["tora", ("tab", "skills")] * 3


def test_the_build_ships_the_starter_skills_and_no_packs():
    spec = (ROOT / "heytora.spec").read_text(encoding="utf-8")
    assert 'datas.append(("skills", "skills"))' in spec
    assert 'collect_submodules("extensions")' not in spec


def test_the_first_launch_only_names_skills_that_exist():
    from agents.skills import SkillLibrary
    from ui.setup_wizard import SKILL_CARDS, SKILLS_FOR, SKILLS_FOR_EVERYONE, WORK, skills_for
    starters = {s.id for s in SkillLibrary(user=Path("no-such-folder")).all()}
    named = {sid for ids in SKILLS_FOR.values() for sid in ids} | set(SKILLS_FOR_EVERYONE) | set(SKILL_CARDS)
    assert named <= starters, named - starters
    assert set(SKILL_CARDS) == starters, "every starter skill has a card"
    assert {w for w, _k in WORK} - {"Something else"} == set(SKILLS_FOR)
    assert skills_for(["Something else"]) == [] and skills_for([]) == [], "then every one stays on"


def test_finishing_the_first_launch_never_turns_off_your_own_skills(tmp_path, monkeypatch):
    monkeypatch.setenv("TORA_SKILLS_DIR", str(tmp_path / "mine"))
    from agents.skills import SkillLibrary
    from ui.setup_wizard import SetupWizard
    _write(tmp_path / "mine", "water-the-plants", PLANTS)
    SetupWizard._save_skills(["Developer"])
    on = {s.id for s in SkillLibrary().enabled()}
    assert "water-the-plants" in on and "tidy-downloads" in on and "send-an-invoice" not in on
    SetupWizard._save_skills(["Something else"])
    assert len(SkillLibrary().enabled()) == len(SkillLibrary().all())
