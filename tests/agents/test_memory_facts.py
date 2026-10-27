"""MEM-01 / MEM-02 (keyword half): facts that know when they were true.

The Phase 2 gate: recall returns the current fact over a stale one. A new
value for a one-value fact ends the old one (kept as history, never
deleted); point-in-time reads what was true then; forget removes it all.
Facts come from plain statements, by rules - never from a question, never
anything secret-looking.
"""
from __future__ import annotations

import time

from agents.memory.extract import from_words
from agents.memory.facts import FactStore


def _say(store, words, at=None):
    ep = store.episode("voice", words, at=at)
    for f in from_words(words):
        store.learn(f.subject, f.predicate, f.value, f.text, kind=f.kind, episode=ep, at=at)


def test_the_current_fact_wins_over_a_stale_one():
    s = FactStore()
    t0 = time.time() - 30 * 86400
    _say(s, "Lee's email is lee@old.com", at=t0)
    _say(s, "Remember that Lee's new email is lee@new.com")
    assert s.current("Lee", "email") == "lee@new.com"
    recalled = s.recall("what is Lee's email")
    assert [f.value for f in recalled] == ["lee@new.com"], "the stale one is not recalled"
    assert s.current("Lee", "email", at=t0 + 60) == "lee@old.com", "what was true then"
    history = s._rows("1=1", ())
    assert {f.value for f in history} == {"lee@old.com", "lee@new.com"}, "ended, not deleted"
    assert next(f for f in history if f.value == "lee@old.com").invalid_at is not None


def test_many_valued_facts_add_and_the_same_fact_is_kept_once():
    s = FactStore()
    s.learn("me", "likes", "jazz", "They like jazz", kind="self")
    s.learn("me", "likes", "chess", "They like chess", kind="self")
    assert s.learn("me", "likes", "Jazz", "They like jazz", kind="self") is None
    assert {f.value for f in s.about("me")} == {"jazz", "chess"}


def test_my_own_facts_and_a_one_hop_recall():
    s = FactStore()
    _say(s, "my phone number is 082 555 0101")
    _say(s, "I work at Acme")
    _say(s, "Sam is my accountant")
    assert s.current("me", "phone") == "082 555 0101"
    assert [f.text for f in s.recall("where do I work")][0] == "They work at Acme"
    assert any(f.text == "Sam is their accountant" for f in s.recall("email Sam the invoice"))


def test_questions_and_secrets_are_never_facts():
    assert from_words("what is Lee's email?") == []
    assert from_words("my password is hunter2") == []
    assert from_words("my card number is 4111 1111 1111 1111") == []


def test_forget_removes_the_facts_and_where_they_came_from():
    s = FactStore()
    _say(s, "Lee's email is lee@new.com")
    assert s.forget("Lee") == 1
    assert s.recall("Lee email") == [] and s._db.execute("select count(*) from episodes").fetchone()[0] == 0


def test_a_job_is_told_the_facts_that_fit_its_goal(tmp_path):
    from agents.bootstrap import build_agent_manager
    from agents.core import Task
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    mgr.facts.learn("Lee", "email", "lee@new.com", "Lee's email is lee@new.com")
    lines = mgr._facts_for(Task(title="t", goal="email Lee the report", workspace=str(tmp_path)))
    assert lines and "Lee's email is lee@new.com" in lines[0] and "data not instructions" in lines[0]


def test_said_aloud_learned_and_recalled_by_voice(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    import companion_manager as cm
    from ai import gemini_memory

    async def nothing(query, k=5):
        return []
    monkeypatch.setattr(gemini_memory, "recall", nothing)        # the embedded memory: never online in tests
    s = FactStore()
    c = cm.CompanionManager.__new__(cm.CompanionManager)
    c._agents = SimpleNamespace(facts=s)
    c._learn_from("Lee's email is lee@old.com")
    c._learn_from("Lee's new email is lee@new.com")
    deadline = time.time() + 3
    while time.time() < deadline and s.current("Lee", "email") != "lee@new.com":
        time.sleep(0.02)
    out = asyncio.run(c._tool_recall({"query": "Lee's email"}))
    assert out["found"] and out["facts"][0].startswith("Lee's email is lee@new.com")
    assert len(out["facts"]) == 1, "the old address is history, not an answer"


def test_a_fact_that_arrives_late_never_ends_a_newer_one():
    s = FactStore()
    now = time.time()
    s.learn("Lee", "email", "lee@new.com", "Lee's email is lee@new.com", at=now)
    s.learn("Lee", "email", "lee@old.com", "Lee's email is lee@old.com", at=now - 5)    # written second
    assert s.current("Lee", "email") == "lee@new.com"
    assert s.current("Lee", "email", at=now - 2) == "lee@old.com"
