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


def test_she_has_only_the_tours_own_tools():
    from ui.onboarding_interview import _voice_class, tools
    names = [f["name"] for f in tools()[0]["functionDeclarations"]]
    assert sorted(names) == ["heard_name", "interview_done", "make_list", "press_continue", "ready_jobs",
                            "skip_interview"], "no pointing, clicking or looking at will"
    voice = _voice_class()(tools={})
    assert voice._setup("hi")["tools"] == tools()


def test_the_work_she_picks_is_from_the_skills_list():
    from ui.onboarding_interview import tools
    from ui.setup_wizard import USES
    done = next(f for f in tools()[0]["functionDeclarations"] if f["name"] == "interview_done")
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


def test_the_list_she_makes_is_cleaned():
    from ui.onboarding_interview import Interview
    events = []
    iv = Interview(lambda kind, value: events.append((kind, value)))
    out = asyncio.run(iv._make_list({"items": ["  Call   the bank ", "", "x" * 90] + ["more"] * 5}))
    assert events == [("list", ["Call the bank", "x" * 60, "more", "more", "more"])]
    assert out == {"ok": True, "written": 5}


def test_the_screen_goes_to_her_before_the_question_ends(monkeypatch):
    import ui.onboarding_interview as I
    monkeypatch.setattr(I, "ANSWER_WAIT_S", 0)
    from ui.onboarding_interview import Interview
    sent = []

    class Conv:
        ready = True
        speaking = False

        async def send_context(self, text):
            sent.append(("context", text))

        async def send_image(self, jpeg):
            sent.append(("image", jpeg))

        async def end_user_audio(self, stop=None):
            sent.append(("end",))

        async def ask_text(self, text):
            sent.append(("ask", text))
    iv = Interview(lambda *_a: None)
    iv._conv = Conv()
    iv._heard("what is this?")
    asyncio.run(iv._release(("JPEG", "[look]")))
    assert sent == [("context", "[look]"), ("image", "JPEG"), ("end",)]
    sent.clear()
    asyncio.run(iv._release(None))
    assert sent == [("end",)], "a plain answer: no picture"


# ── never stuck, and skipping always wins (29 Oct) ──────────────────────────────

def _engine():
    from ui.onboarding_interview import Interview
    events = []
    return Interview(lambda kind, value: events.append((kind, value))), events


def test_skip_in_their_own_words_is_heard_without_her():
    from types import SimpleNamespace
    iv, events = _engine()
    iv._interviewing = True
    iv._turn_end(SimpleNamespace(heard="Can we please skip this?", said=""))
    iv._turn_end(SimpleNamespace(heard="can we please skip", said=""))
    assert [k for k, _v in events].count("skip") == 1
    iv2, events2 = _engine()
    iv2._interviewing = True
    iv2._turn_end(SimpleNamespace(heard="I skipped breakfast and code all day", said=""))
    assert "skip" not in [k for k, _v in events2]


def test_her_skip_tool_keeps_what_she_had():
    iv, events = _engine()
    iv.name = "Dylan"
    asyncio.run(iv._skip_tool({"work": ["Developer"], "facts": ["Builds HeyTora"]}))
    kind, answers = events[-1]
    assert kind == "skip" and answers.uses == ["Developer"] and answers.name == "Dylan"


def test_a_closed_voice_is_their_turn_not_a_wait():
    iv, events = _engine()
    iv._lost("server closed the session")
    assert events == [("state", "your_turn")]


def test_no_answer_is_their_turn_again(monkeypatch):
    import ui.onboarding_interview as I
    monkeypatch.setattr(I, "ANSWER_WAIT_S", 0)
    iv, events = _engine()
    iv._conv = type("C", (), {"speaking": False})()
    asyncio.run(iv._watch_answer())
    assert events == [("state", "your_turn")]


def test_what_they_say_while_she_reopens_is_kept_in_order():
    sent = []

    class Conv:
        ready = False

        async def push_audio(self, data):
            sent.append(data)
    iv, _events = _engine()
    iv._conv = Conv()
    asyncio.run(iv._push(b"1"))
    asyncio.run(iv._push(b"2"))
    assert sent == [], "held while she is away"
    iv._conv.ready = True
    asyncio.run(iv._push(b"3"))
    assert sent == [b"1", b"2", b"3"]


def test_holding_the_keys_reopens_a_closed_voice():
    iv, _events = _engine()
    ran = []
    iv._conv = type("C", (), {"ready": False, "barge_in": lambda self: None})()
    iv._run = lambda coro: (ran.append(coro.__name__), coro.close())
    iv._open_mic = lambda: None
    iv.hold(True)
    assert ran == ["_ensure"]


def test_a_circle_with_no_words_is_still_answered(monkeypatch):
    import ui.onboarding_interview as I
    monkeypatch.setattr(I, "ANSWER_WAIT_S", 0)
    sent = []

    class Conv:
        ready = True
        speaking = False

        def barge_in(self): pass
        async def send_context(self, text): pass
        async def send_image(self, jpeg): pass

        async def end_user_audio(self, stop=None):
            sent.append("end")

        async def ask_text(self, text):
            sent.append(text)
    iv, _events = _engine()
    iv._conv = Conv()
    iv._open_mic = lambda: None
    iv._run = lambda coro: coro.close()
    iv.hold(True)                                  # held, circled, said nothing
    iv._held = False
    asyncio.run(iv._release(("JPEG", "[circled]")))
    assert sent == ["end", I.QUIET_ASK], "29 Oct: a silent circle got nothing back"


def test_she_presses_continue_only_when_it_can_be_pressed():
    iv, events = _engine()
    out = asyncio.run(iv._press_continue({}))
    assert out["pressed"] is False and events == [], "she says what is left instead"
    iv.can_continue = lambda: True
    assert asyncio.run(iv._press_continue({})) == {"pressed": True}
    assert events == [("press", "continue")]
