"""The "Instructions for Tora" box adds to her prompt; it never replaces it.

Found building the Tora page (5 Oct 2026): CUSTOM_INSTRUCTIONS was the
whole system prompt - unset, it defaulted to it - so the box showed "You
are Tora, a VISUAL AI tutor..." and saving "keep answers short" left her
with only that line. Now the box is the user's own words, added after her
prompt; a whole prompt saved by an older build is still honoured as one.
"""
from __future__ import annotations

import pytest

import companion_manager
import config


def _prompt(monkeypatch, system: str, own: str) -> str:
    monkeypatch.setattr(config.cfg, "system_prompt", system)
    monkeypatch.setattr(config.cfg, "custom_instructions", own)
    monkeypatch.setattr(config.cfg, "profile", lambda: ("", []))
    return companion_manager._build_system_prompt()


def test_nothing_set_means_her_prompt_and_an_empty_box(monkeypatch):
    monkeypatch.setenv("CUSTOM_INSTRUCTIONS", "")
    system, own = config._instructions_from_env()
    assert system == config.DEFAULT_SYSTEM_PROMPT and own == ""


def test_the_users_words_are_added_not_swapped_in(monkeypatch):
    monkeypatch.setenv("CUSTOM_INSTRUCTIONS", "Keep answers very short")
    system, own = config._instructions_from_env()
    assert system == config.DEFAULT_SYSTEM_PROMPT and own == "Keep answers very short"
    prompt = _prompt(monkeypatch, system, own)
    assert prompt.startswith("You are Tora"), "her prompt is still there"
    assert "standing instructions - follow them on every answer:\nKeep answers very short" in prompt
    assert "{{CONTEXT}}" not in prompt


def test_a_whole_prompt_saved_by_an_older_build_is_kept_as_the_prompt(monkeypatch):
    monkeypatch.setenv("CUSTOM_INSTRUCTIONS", "You are my tutor.\\n{{CONTEXT}}")
    system, own = config._instructions_from_env()
    assert system == "You are my tutor.\n{{CONTEXT}}" and own == ""


@pytest.mark.parametrize("own", ["", "   "])
def test_no_instructions_add_nothing(monkeypatch, own):
    prompt = _prompt(monkeypatch, config.DEFAULT_SYSTEM_PROMPT, own)
    assert "standing instructions" not in prompt
