"""The tour's conversation, phase C (29 Oct 2026): one live voice for "this
is how you talk to me" and "I'm curious about you" - she hears the name,
asks four questions, and hands back what she learned as tool calls, on the
tour's free voice (migration 0014). Without Google: the session is never
opened here; its parts are checked one by one.
"""
from __future__ import annotations

import asyncio
import time


def test_her_instructions_hold_the_name_and_the_four_questions():
    from ui.onboarding_interview import QUESTIONS, prompt
    words = prompt("Dylan")
    assert "The user's name is Dylan" in words
    assert all(q in words for q in QUESTIONS) and len(QUESTIONS) == 4
    assert "heard_name" in words and "interview_done" in words and "[interview]" in words


def test_she_has_only_the_tours_two_tools():
    from ui.onboarding_interview import _voice_class, tools
    names = [f["name"] for f in tools()[0]["functionDeclarations"]]
    assert names == ["heard_name", "interview_done"], "no pointing, clicking or looking here"
    voice = _voice_class()(tools={})
    assert voice._setup("hi")["tools"] == tools()


def test_the_work_she_picks_is_from_the_skills_list():
    from ui.onboarding_interview import tools
    from ui.setup_wizard import USES
    done = tools()[0]["functionDeclarations"][1]
    assert done["parameters"]["properties"]["work"]["items"]["enum"] == list(USES)


def test_what_she_hands_back_is_cleaned():
    from ui.onboarding_interview import answers_from
    a = answers_from({"work": ["Developer", "Astronaut", "Developer", 3], "facts": ["  Runs  a bakery ", ""] * 5,
                      "summary": "Bakes.\n Codes."}, name="Dylan")
    assert a.name == "Dylan" and a.uses == ["Developer"]
    assert a.facts[0] == "Runs a bakery" and len(a.facts) == 5
    assert a.summary == "Bakes. Codes."


def test_answers_become_the_profile_the_skills_and_her_memory(monkeypatch):
    import ai.gemini_memory as M
    import ui.onboarding_interview as I
    from config import cfg
    from ui.setup_wizard import SetupWizard
    got = {}
    monkeypatch.setattr(cfg, "set_profile", lambda name, uses: got.update(profile=(name, uses)))
    monkeypatch.setattr(SetupWizard, "_save_skills", staticmethod(lambda uses: got.update(skills=uses)))
    remembered = []

    async def remember(text, source=""):
        remembered.append((text, source))
        return True
    monkeypatch.setattr(M, "remember", remember)
    I.save(I.Answers(name="Dylan", uses=["Developer"], facts=["Runs HeyTora"], summary="A founder."))
    end = time.time() + 2
    while len(remembered) < 2 and time.time() < end:
        time.sleep(0.01)
    assert got == {"profile": ("Dylan", ["Developer"]), "skills": ["Developer"]}
    assert remembered == [("Runs HeyTora", "user"), ("A founder.", "user")], "said by them"


def test_every_task_on_her_loop_is_the_tours_free_voice():
    from account.service import TOUR_VOICE
    from ui.onboarding_interview import Interview
    iv = Interview(lambda *_a: None)
    iv._loop = asyncio.new_event_loop()
    import threading
    threading.Thread(target=iv._loop.run_forever, daemon=True).start()
    seen = []

    async def look():
        seen.append(TOUR_VOICE.get())
    iv._run(look())
    end = time.time() + 2
    while not seen and time.time() < end:
        time.sleep(0.01)
    iv._loop.call_soon_threadsafe(iv._loop.stop)
    assert seen == [True] and TOUR_VOICE.get() is False, "only there, never here"


def test_the_tools_report_the_name_and_the_answers():
    from ui.onboarding_interview import Interview
    events = []
    iv = Interview(lambda kind, value: events.append((kind, value)))
    asyncio.run(iv._heard_name({"name": "  Dylan "}))
    asyncio.run(iv._done({"work": ["Developer"], "facts": [], "summary": "x"}))
    assert events[0] == ("name", "Dylan") and iv.name == "Dylan"
    assert events[1][0] == "done" and events[1][1].name == "Dylan"


def test_typing_without_a_voice_says_offline():
    from ui.onboarding_interview import Interview
    events = []
    iv = Interview(lambda kind, value: events.append((kind, value)))
    iv._loop = asyncio.new_event_loop()
    import threading
    threading.Thread(target=iv._loop.run_forever, daemon=True).start()
    iv.type("hello")
    end = time.time() + 2
    while not events and time.time() < end:
        time.sleep(0.01)
    iv._loop.call_soon_threadsafe(iv._loop.stop)
    assert events == [("state", "offline")]
