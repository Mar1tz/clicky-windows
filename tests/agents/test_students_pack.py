"""The Students pack on a fresh study folder: no credentials, no network.

A revision plan that fits (and says when it does not), flashcards on SM-2,
reference lists in APA 7 and Harvard, deadlines, and the line it never
crosses: nothing here writes work that is handed in.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from agents.bootstrap import build_agent_manager
from agents.core import PermissionPolicy, RiskLevel, TaskStatus, ToolCall, ToolContext
from extensions.students.study import reference, sm2

START = date(2026, 10, 1)


def d(n: int) -> str:
    return (START + timedelta(days=n)).isoformat()


@pytest.fixture
def mgr(tmp_path):
    m = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    m.extensions.enable("students")
    m.extensions.configure("students", {"hours_per_day": 1.5, "session_minutes": 45})
    m.start()
    yield m
    m.stop()


def _desk(mgr):
    return mgr.extensions.get("students").ensure_store(mgr.extensions._context("students"))


def _run(mgr, tool: str, **args):
    ctx = ToolContext(workspace=_desk(mgr).root, policy=PermissionPolicy(max_auto_risk=RiskLevel.LOW))
    return mgr.runtime.call(mgr.tools.execute(ToolCall(tool, args), ctx), timeout=60)


def test_nothing_is_submitted_and_the_line_is_in_the_prompt(mgr):
    ext = mgr.extensions.get("students")
    assert set(ext.TOOLS.levels().values()) == {"read", "draft"}
    rules = " ".join(s.text for s in ext.get_skills())
    assert "never do their assessed work" in rules and "essays" in rules
    assert mgr.extensions.match_intent("write my essay on the causes of the first world war") is None


def test_the_revision_plan_fits_topics_before_each_exam(mgr):
    _run(mgr, "add_course_topics", course="Biology", topics=[{"name": "Cells", "difficulty": 3},
                                                             {"name": "Genetics", "difficulty": 2},
                                                             {"name": "Ecology", "difficulty": 1}])
    _run(mgr, "add_course_topics", course="History", topics=[{"name": "Cold War", "difficulty": 2}])
    _run(mgr, "add_deadline", course="Biology", title="Final", due=d(6), kind="exam")
    _run(mgr, "add_deadline", course="History", title="Paper 1", due=d(4), kind="exam")
    r = _run(mgr, "build_study_plan", start=d(0))
    assert r.ok, r.error
    p = r.data["plan"]
    assert p["per_day"] == 2 and p["not_fitted"] == {} and r.data["findings"] == []
    s = _desk(mgr)
    days = {d(i): [(x["course"], x["topic"]) for x in s.sessions_on(d(i))] for i in range(7)}
    assert days[d(3)][0] == ("History", "Review everything"), "the day before an exam is for review"
    assert days[d(5)][0] == ("Biology", "Review everything")
    bio = [t for day in days.values() for c, t in day if c == "Biology" and t != "Review everything"]
    assert sorted(bio) == ["Cells", "Cells", "Cells", "Ecology", "Genetics", "Genetics"], "hard topics come back"
    assert all(len(set(day)) == len(day) for day in days.values()), "no topic twice in a day"
    assert all(len(day) <= 2 for day in days.values())
    assert days[d(6)] == [], "nothing on the exam day itself"


def test_a_plan_that_cannot_fit_says_so(mgr):
    _run(mgr, "add_course_topics", course="Maths", topics=[{"name": f"Topic {i}", "difficulty": 3} for i in range(6)])
    _run(mgr, "add_deadline", course="Maths", title="Exam", due=d(3), kind="exam")
    r = _run(mgr, "build_study_plan", start=d(0), hours_per_day=0.75)
    assert [f["title"] for f in r.data["findings"]] == ["Maths: 16 session(s) do not fit before the exam"]
    assert not _run(mgr, "build_study_plan", start=d(10)).ok, "no exam ahead is a clear no"


def test_flashcards_follow_spaced_repetition(mgr):
    r = _run(mgr, "add_flashcards", course="Biology", cards=[{"front": "Powerhouse of the cell?", "back": "Mitochondria"},
                                                           {"front": "DNA base pairs?", "back": "A-T, C-G"}])
    assert r.data["cards"] == {"added": 2, "skipped": 0}
    assert _run(mgr, "add_flashcards", course="Biology", cards=[{"front": "DNA base pairs?", "back": "x"}]).data[
        "cards"] == {"added": 0, "skipped": 1}
    due = _run(mgr, "cards_due").data["cards"]
    assert len(due) == 2
    first = _run(mgr, "review_flashcard", card_id=due[0]["id"], grade=5).data["card"]
    assert first["interval"] == 1 and first["reps"] == 1
    assert len(_run(mgr, "cards_due").data["cards"]) == 1, "a remembered card leaves today's pile"
    assert sm2(5, 1, 1, 2.6) == (2, 6, 2.7)
    assert sm2(4, 2, 6, 2.5) == (3, 15, 2.5)
    assert sm2(1, 3, 15, 2.5)[:2] == (0, 1), "a forgotten card starts again"
    assert sm2(0, 0, 0, 1.3)[2] == 1.3, "ease never drops below 1.3"
    out = _run(mgr, "export_flashcards", course="Biology").data["export"]
    with open(out["path"], encoding="utf-8") as fh:
        assert fh.read().splitlines()[2] == "Powerhouse of the cell?\tMitochondria"
    assert not _run(mgr, "review_flashcard", card_id=1, grade=7).ok


def test_references_in_apa_and_harvard():
    book = {"type": "book", "title": "Thinking, fast and slow", "authors": ["Daniel Kahneman"], "year": "2011",
            "publisher": "Farrar, Straus and Giroux"}
    article = {"type": "article", "title": "Deep learning", "authors": ["LeCun, Yann", "Bengio, Yoshua",
                                                                         "Hinton, Geoffrey"],
               "year": "2015", "journal": "Nature", "volume": "521", "issue": "7553", "pages": "436-444",
               "doi": "10.1038/nature14539"}
    web = {"type": "web", "title": "Climate change", "site": "World Health Organization", "year": "2023",
           "url": "https://www.who.int/health-topics/climate-change", "accessed": "2026-09-27"}
    assert reference(book, "apa") == "Kahneman, D. (2011). *Thinking, fast and slow*. Farrar, Straus and Giroux."
    assert reference(article, "apa") == ("LeCun, Y., Bengio, Y., & Hinton, G. (2015). Deep learning. *Nature*, "
                                         "*521*(7553), 436-444. https://doi.org/10.1038/nature14539")
    assert reference(web, "apa") == ("World Health Organization (2023). *Climate change*. "
                                     "https://www.who.int/health-topics/climate-change")
    assert reference(book, "harvard") == "Kahneman, D. (2011) *Thinking, fast and slow*. Farrar, Straus and Giroux."
    assert reference(article, "harvard") == ("LeCun, Y., Bengio, Y. and Hinton, G. (2015) 'Deep learning', "
                                             "*Nature*, 521(7553), pp. 436-444.")
    assert reference(web, "harvard").endswith("Available at: https://www.who.int/health-topics/climate-change "
                                              "(Accessed: 2026-09-27).")
    assert reference({"type": "book", "title": "X", "authors": ["Jean-Paul Sartre"], "year": "1943"}, "apa") == \
        "Sartre, J.-P. (1943). *X*."


def test_the_reference_list_is_sorted_and_written(mgr):
    _run(mgr, "add_source", course="Psychology", type="book", title="Thinking, fast and slow",
         authors=["Daniel Kahneman"], year="2011", publisher="Farrar, Straus and Giroux")
    _run(mgr, "add_source", course="Psychology", type="book", title="Influence", authors=["Robert Cialdini"],
         year="1984", publisher="Harper")
    assert not _run(mgr, "add_source", course="Psychology", type="web", title="No link").ok, "a web page needs its URL"
    r = _run(mgr, "write_reference_list", course="Psychology", style="harvard").data["references"]
    assert [e.split(",")[0] for e in r["entries"]] == ["Cialdini", "Kahneman"]
    with open(r["path"], encoding="utf-8") as fh:
        assert fh.read().startswith("# References (Harvard)")


def test_today_runs_as_a_workflow_and_flags_late_work(mgr):
    today = date.today()
    _run(mgr, "add_deadline", course="History", title="Essay", due=(today - timedelta(days=2)).isoformat())
    _run(mgr, "add_deadline", course="History", title="Exam", due=(today + timedelta(days=9)).isoformat(), kind="exam")
    task = mgr.wait(mgr.submit("show me today's study").id, 120)
    assert task.status is TaskStatus.COMPLETED, task.error
    assert "History: 'Essay' was due" in " ".join(f["title"] for f in task.metadata.get("findings", []))


def test_requests_route_to_the_coach(mgr):
    for text, workflow in (("please make me a revision plan", "revision_plan"),
                           ("show me today's study", "daily_study"),
                           ("make flashcards from my biology notes", ""),
                           ("add my chemistry exam on 12 November", ""),
                           ("format my reference list in Harvard", "")):
        intent = mgr.extensions.match_intent(text)
        assert intent is not None and intent.metadata["template"] == "study_coach", text
        assert intent.metadata.get("workflow", "") == workflow, text
