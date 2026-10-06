"""Wave 3 of docs/SHOW_THE_NEXT_MOVE.md: never just words, never twice, priced.

UD-04 - a what-to-do answer that only SAID it ("Click Checkout to pay")
still gets its arrow, from the answer's own words, cheapest tier first.
UD-05 - asked again about a screen that has not changed: the last answer
and drawing again, with no call.
UD-06 - every look's cost, and the scorecard's average and fast/deep split.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
from types import SimpleNamespace

import pytest

import companion_manager as cm
from ai import gemini_vision, look_region
from ai.gemini_realtime import Turn

# -- UD-04: never just words ----------------------------------------------------------------------------------

@pytest.mark.parametrize("said, target", [
    ("Click Checkout to proceed to payment.", "Checkout"),
    ("Click the blue Sign in button.", "blue Sign in"),
    ("Pick the Arctic Fan for thirty-nine dollars, which has a 4.3 rating.", "Arctic Fan"),
    ("Click Free up space to manage your storage.", "Free up space"),
    ("Toggle the switch next to Dark mode.", "switch next to Dark mode"),
    ("Type your card number into the second field.", "second field"),
    ("May had the highest sales.", ""),
    ("Maybe take some time off and travel.", ""),
])
def test_what_the_answer_says_to_act_on(said, target):
    assert cm._target_from_answer(said) == target


def _turn_manager():
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    mgr._pointed_this_turn = False
    mgr._pointing_held = False
    mgr._heard_buf = []
    mgr._turn_seq = 0
    mgr.sig_point_release = SimpleNamespace(emit=lambda: None)
    mgr.sig_response_done = SimpleNamespace(emit=lambda *a: None)
    mgr._record_turn = lambda *a: None
    mgr._emit_state = lambda *a: None
    mgr._metered_talk = lambda: False
    pointed = []

    async def point_to(label, hold=False):
        pointed.append(label)
        return False
    mgr._point_to = point_to
    mgr._submit = lambda coro: asyncio.run(coro)
    return mgr, pointed


@pytest.mark.parametrize("heard, said, expect", [
    ("what should I click next?", "Click Checkout to pay.", ["Checkout"]),
    ("which one should I pick?", "Pick the Arctic Fan.", ["Arctic Fan"]),
    ("where is the search bar", "It's at the top.", ["search bar"]),          # the old safety net, as before
    ("what should I do with my weekend?", "Maybe go for a walk.", []),        # nothing on screen named
    ("what is this chart about?", "Sales by month.", []),                      # not a what-to-do question
])
def test_a_words_only_answer_still_gets_its_arrow(heard, said, expect):
    mgr, pointed = _turn_manager()
    mgr._on_realtime_turn_end(Turn(heard=heard, said=said))
    assert pointed == expect


def test_an_answer_that_drew_is_left_alone():
    mgr, pointed = _turn_manager()
    mgr._pointed_this_turn = True
    mgr._on_realtime_turn_end(Turn(heard="what should I click next?", said="Click Checkout to pay."))
    assert pointed == []


# -- UD-05: never pay twice -------------------------------------------------------------------------------------

def _png_b64(colour=(240, 240, 240), mark=None):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (320, 180), colour)
    d = ImageDraw.Draw(im)
    d.rectangle((40, 40, 140, 90), fill=(30, 90, 200))
    if mark:
        d.line(mark, fill=(255, 0, 0), width=2)
    buf = io.BytesIO()
    im.save(buf, "JPEG")
    return base64.b64encode(buf.getvalue()).decode()


def test_the_same_screen_has_the_same_sketch_even_with_an_arrow_on_it():
    a = look_region.fingerprint(_png_b64())
    assert look_region.same_screen(a, look_region.fingerprint(_png_b64()))
    assert look_region.same_screen(a, look_region.fingerprint(_png_b64(mark=(200, 120, 260, 150)))), "Tora's own arrow"
    other = look_region.fingerprint(_png_b64(colour=(20, 20, 20)))
    assert not look_region.same_screen(a, other)
    assert look_region.fingerprint("not an image") is None and not look_region.same_screen(None, None)


def _look_manager(monkeypatch, image):
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    drawn = []
    mgr.sig_draw = SimpleNamespace(emit=drawn.append)
    mgr.sig_clear_drawings = SimpleNamespace(emit=lambda: None)
    mgr._privacy_guard = False
    mgr._activity = lambda what: None
    mgr._heard_buf = []
    mgr._last_look = None
    shot = SimpleNamespace(base64_jpeg=image)

    async def capture(**_kw):
        return [shot]
    mgr._capture_shots = capture
    mgr._active_shot = lambda screens: shot
    mgr._shape_from_tag = lambda tag: {"tag": tag}
    monkeypatch.setattr(cm, "active_window_title", lambda: "Shop")
    calls = []

    async def fake_look(question, image, **kw):
        calls.append(question)
        return gemini_vision.Look(answer="Click Checkout.", sure=True, path="fast", model="flash", cost=0.0014,
                                  actions=[gemini_vision.Action("Checkout", (700, 720, 760, 860), "rect")])
    monkeypatch.setattr(gemini_vision, "look", fake_look)
    return mgr, drawn, calls


def test_asking_again_on_an_unchanged_screen_makes_no_call(monkeypatch):
    mgr, drawn, calls = _look_manager(monkeypatch, _png_b64())
    first = asyncio.run(mgr._tool_look_at_screen({"question": "how do I pay?"}))
    again = asyncio.run(mgr._tool_look_at_screen({"question": "How do I pay"}))
    assert len(calls) == 1, "the second answer came from the first look"
    assert again["say"] == first["say"] and again["reused"] is True
    assert drawn == [{"tag": "[RECT:720,700,860,760:tora]"}] * 2, "and its drawing is put back"


def test_show_me_again_reuses_a_different_question_does_not(monkeypatch):
    mgr, drawn, calls = _look_manager(monkeypatch, _png_b64())
    asyncio.run(mgr._tool_look_at_screen({"question": "how do I pay?"}))
    mgr._heard_buf = ["where was it again?"]
    asyncio.run(mgr._tool_look_at_screen({"question": "where is the checkout button"}))
    assert len(calls) == 1
    mgr._heard_buf = ["is shipping free?"]
    asyncio.run(mgr._tool_look_at_screen({"question": "is shipping free?"}))
    assert len(calls) == 2, "a new question about the same screen is a new look"


def test_a_changed_or_old_screen_is_looked_at_again(monkeypatch):
    mgr, drawn, calls = _look_manager(monkeypatch, _png_b64())
    asyncio.run(mgr._tool_look_at_screen({"question": "how do I pay?"}))
    mgr._last_look["at"] -= 200                       # over three minutes ago
    asyncio.run(mgr._tool_look_at_screen({"question": "how do I pay?"}))
    assert len(calls) == 2
    mgr._capture_shots = lambda **kw: asyncio.sleep(0, [SimpleNamespace(base64_jpeg=_png_b64(colour=(10, 10, 10)))])
    mgr._active_shot = lambda screens: screens[0]
    asyncio.run(mgr._tool_look_at_screen({"question": "how do I pay?"}))
    assert len(calls) == 3, "the screen changed"


# -- UD-06: priced -------------------------------------------------------------------------------------------------

def test_what_a_look_costs():
    assert gemini_vision.cost_usd("gemini-3.8-flash", {"promptTokenCount": 1000, "candidatesTokenCount": 100}) == \
        pytest.approx(0.0008)
    pro = gemini_vision.cost_usd("gemini-3.1-pro-preview",
                                 {"promptTokenCount": 1300, "candidatesTokenCount": 80, "thoughtsTokenCount": 400})
    assert pro == pytest.approx((1300 * 2.5 + 480 * 15) / 1e6), "thinking is billed as output"


def test_the_scorecard_has_the_cost_and_the_paths(caplog):
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    mgr._ans_stats = None
    for path, cost in (("fast", 0.0014), ("fast→deep", 0.0094), ("reused", 0.0)):
        mgr._ans = {"heard": 0.0, "asked": 0.0, "tool": 0.5, "look": 2.0, "drawing": 2.1}
        mgr._note_answer(path=path, cost=cost)
        with caplog.at_level(logging.INFO, logger="tora.manager"):
            mgr._log_answer("Click Checkout.")
    assert "look cost avg $0.0036" in caplog.text and "(fast 1, fast→deep 1, reused 1)" in caplog.text
