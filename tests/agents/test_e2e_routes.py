"""Six routes, testable without a model (OR-03, OR-04, OR-18).

A labelled set of things people say, each with the route it should take.
The decision table (ai/routes.py) is scored on it: accuracy, and how often
it would ask instead of acting (the clarification rate). Each decision
carries a reason said in plain words. `-s` prints the scorecard.
"""
from __future__ import annotations

import pytest

from ai import routes
from ai.routes import ROUTES, decide

LABELLED = [
    # answer
    ("what does this error mean?", "answer", {}),
    ("why is my laptop so slow", "answer", {}),
    ("who wrote this article", "answer", {}),
    ("how much does a Spotify family plan cost", "answer", {}),
    ("is this email a scam?", "answer", {}),
    ("what's the capital of Portugal", "answer", {}),
    ("explain this paragraph", "answer", {"traced": True}),
    ("what is this", "answer", {"traced": True}),
    # draw
    ("where is the export button", "draw", {}),
    ("where do I find the settings in Teams", "draw", {}),
    ("point at the save icon", "draw", {}),
    ("show me where the downloads are", "draw", {}),
    ("which button turns on captions", "draw", {}),
    ("highlight the search box", "draw", {}),
    ("find the reply all button", "draw", {}),
    # guided
    ("how do I set up two-factor on Gmail", "guided", {}),
    ("how do I export this as a PDF", "guided", {}),
    ("walk me through installing python", "guided", {}),
    ("show me how to add a column in excel", "guided", {}),
    ("teach me to use pivot tables", "guided", {}),
    ("how to change my wallpaper", "guided", {}),
    ("guide me through booking a meeting room", "guided", {}),
    # direct
    ("next song", "direct", {}),
    ("pause the music", "direct", {}),
    ("turn the volume down", "direct", {}),
    ("open bluetooth settings", "direct", {}),
    ("open spotify", "direct", {}),
    ("mute", "direct", {}),
    ("send it to Sarah", "direct", {"pointed": True}),
    ("delete the draft I just wrote", "direct", {}),
    ("post this on LinkedIn", "direct", {"selection": True}),
    ("close this window", "direct", {}),
    # clarify
    ("delete that", "clarify", {}),
    ("send this", "clarify", {}),
    ("remove those", "clarify", {}),
    ("pay it", "clarify", {}),
    # job
    ("build me a landing page for my bakery", "job", {}),
    ("research flights to Lisbon in March", "job", {}),
    ("write a report on last quarter's sales", "job", {}),
    ("make a budget spreadsheet for the trip", "job", {}),
    ("create a website for my portfolio", "job", {}),
    ("draft a presentation about our roadmap", "job", {}),
    ("compare these three laptops and make a summary", "job", {}),
    ("put together an itinerary for Rome", "job", {}),
]


def test_every_route_is_in_the_set():
    assert {r for _, r, _ in LABELLED} == set(ROUTES)


def test_every_reason_is_said_in_plain_words():
    for text, _route, ctx in LABELLED:
        d = decide(text, **ctx)
        assert d.route in ROUTES and d.words and "_" not in d.words


def test_the_table_routes_the_set(capsys):
    wrong = []
    for text, want, ctx in LABELLED:
        got = decide(text, **ctx)
        if got.route != want:
            wrong.append(f"{text!r}: {got} (wanted {want})")
    accuracy = 1 - len(wrong) / len(LABELLED)
    asks = sum(decide(t, **c).route == "clarify" for t, _, c in LABELLED) / len(LABELLED)
    with capsys.disabled():
        print(f"\nroutes: accuracy {accuracy:.0%} on {len(LABELLED)}, clarification rate {asks:.0%}")
        for w in wrong:
            print("  " + w)
    assert accuracy >= 0.95, wrong


@pytest.mark.parametrize("tools,route", [
    (["point_at"], "draw"), (["show_me_how", "point_at"], "guided"), (["desktop_action"], "direct"),
    (["delegate_task"], "job"), ([], "answer"), (["search_web"], "answer")])
def test_what_the_model_did_is_read_as_a_route(tools, route):
    assert routes.model_route(tools) == route


def test_each_turn_logs_the_tables_route_beside_the_models(caplog, monkeypatch):
    import asyncio
    import logging

    import companion_manager as cm
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    mgr._trace = None
    mgr._ref_fresh = lambda: False
    caplog.set_level(logging.INFO, logger="tora")

    async def point(args):
        return {"found": True}
    asyncio.run(mgr._noted("point_at", point)({}))
    mgr._log_route("where is the export button")
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("route:"))
    assert line == "route: table draw (show_me) - they want to be shown where something is; model draw"
    assert mgr._turn_tools == [], "ready for the next answer"


@pytest.mark.parametrize("text,corrects", [
    ("no, I meant the other one", True), ("not that one", True), ("that's not it", True),
    ("wrong button", True), ("no the blue one", False), ("where is the save button", False),
    ("no, not the file menu", True), ("I said the settings", True)])
def test_a_correction_is_counted(text, corrects):
    assert routes.is_correction(text) is corrects
