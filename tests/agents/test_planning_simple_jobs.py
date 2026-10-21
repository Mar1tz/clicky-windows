"""AG-07: a job that makes one thing is not planned by a model.

"make a basic HTML page", "Create an HTML file named index.html that displays
'Hello World'" and "Create a single HTML page that displays 'Hello World'"
were each planned - a model call of several seconds - into one step. The
plan's time is logged for every job now too.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from agents.core import ProviderResponse
from agents.planning.planner import ProviderPlanner, is_simple_job
from agents.providers import MockProvider

from ._fakes import make_context


@pytest.mark.parametrize("goal, simple", [
    ("make a basic HTML page", True),
    ('Create an HTML file named index.html that displays the text "Hello World".', True),
    ("Create a single HTML page that displays 'Hello World'.", True),
    ("Create an e-commerce website for a flower shop", False),
    ("Build a one-page website for my coffee shop", False),
    ("Write notes.md and then commit it", False),
    ("Help me respond to this email.", False),
    ("Write a.txt and b.txt", False),
])
def test_what_counts_as_one_thing(goal, simple):
    assert is_simple_job(make_context(goal=goal)) is simple


def test_a_one_file_job_is_one_step_without_a_model_call():
    provider = MockProvider(script=[ProviderResponse(text='{"steps": ["a", "b"]}')])
    ctx = make_context(provider=provider, goal="make a basic HTML page")
    plan = asyncio.run(ProviderPlanner().plan(ctx))
    assert len(plan.steps) == 1 and provider.calls == []


def test_a_skill_job_is_still_planned_by_its_skill():
    provider = MockProvider(script=[ProviderResponse(text='{"steps": ["open the list", "add milk"]}')])
    ctx = make_context(provider=provider, goal="make a basic shopping list")
    ctx.extras = {**(ctx.extras or {}), "skills": ["Always add milk."]}
    plan = asyncio.run(ProviderPlanner().plan(ctx))
    assert [s.title for s in plan.steps] == ["open the list", "add milk"]
