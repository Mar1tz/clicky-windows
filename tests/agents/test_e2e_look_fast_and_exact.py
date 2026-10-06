"""Wave 2 of docs/SHOW_THE_NEXT_MOVE.md: fast, where they look, exact.

UD-01 - fast first. The deep model (about 23x the price, 8-20 s) used to be
picked by the question's words - 11 of 20 everyday questions - and by the
voice's `careful` flag. Now every look starts fast and escalates once, only
when the fast look is not sure or has nothing to draw.
UD-02 - the window in front, cropped, and the mouse as a hint.
UD-03 - a ring round a button lands on the button's own rectangle.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import companion_manager as cm
from ai import gemini, gemini_vision, look_region
from ai.gemini import Models

# -- UD-01: fast first, deep only when needed ----------------------------------------------------------------

def _reply(answer="Click Sign in.", actions=None, sure=True, tokens=(900, 60, 0)):
    body = {"answer": answer, "actions": actions if actions is not None else
            [{"label": "Sign in", "box": [100, 100, 140, 300], "kind": "rect"}], "sure": sure}
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(body)}]}}],
            "usageMetadata": {"promptTokenCount": tokens[0], "candidatesTokenCount": tokens[1],
                              "thoughtsTokenCount": tokens[2]}}


@pytest.fixture
def models(monkeypatch):
    """Scripted replies per model; records which models were asked."""
    asked, script = [], {}

    async def fake_generate(model, *_a, **_kw):
        asked.append("deep" if model == Models.VISION_DEEP else "fast")
        out = script.get(asked[-1])
        if isinstance(out, Exception):
            raise out
        return out
    monkeypatch.setattr(gemini, "generate", fake_generate)
    return SimpleNamespace(asked=asked, script=script)


def test_a_sure_fast_look_is_the_answer_one_cheap_call(models):
    models.script["fast"] = _reply()
    look = asyncio.run(gemini_vision.look("which one should I pick?", "img"))
    assert models.asked == ["fast"] and look.path == "fast" and look.answer == "Click Sign in."


def test_not_sure_hands_it_to_the_deep_model_once(models):
    models.script["fast"] = _reply(sure=False)
    models.script["deep"] = _reply(answer="The second one.", tokens=(1300, 80, 1400))
    look = asyncio.run(gemini_vision.look("which one should I pick?", "img"))
    assert models.asked == ["fast", "deep"] and look.path == "fast→deep"
    assert look.usage["promptTokenCount"] == 2200 and look.usage["thoughtsTokenCount"] == 1400, "both calls counted"


def test_a_what_to_do_answer_with_nothing_to_draw_escalates(models):
    models.script["fast"] = _reply(answer="Probably the second one.", actions=[])
    models.script["deep"] = _reply()
    look = asyncio.run(gemini_vision.look("which one should I pick?", "img"))
    assert models.asked == ["fast", "deep"] and look.actions


def test_a_question_about_the_screen_with_nothing_to_draw_does_not(models):
    models.script["fast"] = _reply(answer="It's a bar chart of sales by month.", actions=[])
    asyncio.run(gemini_vision.look("what is on my screen", "img"))
    assert models.asked == ["fast"]


@pytest.mark.parametrize("q", ["what's wrong with my code", "read the small print", "check this formula"])
def test_dense_reading_goes_deep_first(models, q):
    models.script["deep"] = _reply()
    look = asyncio.run(gemini_vision.look(q, "img"))
    assert models.asked == ["deep"] and look.path == "deep"


@pytest.mark.parametrize("q", ["what's my best move?", "where should I play to win?", "what's the best move here"])
def test_a_move_in_a_game_goes_deep_first(models, q):
    """On the comparison the fast model called a mate-in-one 'you are
    checkmated' - and was sure. A game move is judgement worth the deep look."""
    models.script["deep"] = _reply(answer="Rook from a1 to a8.")
    assert asyncio.run(gemini_vision.look(q, "img")).path == "deep" and models.asked == ["deep"]


@pytest.mark.parametrize("q", ["which one should I pick?", "what should I click next", "where do I click to sign in",
                               "what should I do next"])
def test_everyday_what_to_do_questions_stay_fast(q):
    assert not gemini_vision.needs_judgement(q) and not gemini_vision.needs_reading(q)


def test_when_one_model_fails_the_other_answers(models):
    models.script["fast"] = RuntimeError("busy")
    models.script["deep"] = _reply()
    assert asyncio.run(gemini_vision.look("which one?", "img")).path == "fast→deep"
    models.asked.clear()
    models.script.update(fast=_reply(sure=False), deep=RuntimeError("timeout"))
    assert asyncio.run(gemini_vision.look("which one?", "img")).path == "fast (deep failed)"


def test_the_everyday_questions_no_longer_go_deep_by_their_words():
    everyday = ["what is my best move", "which one should I pick", "how do I export this video",
                "why is this cell red", "explain this chart", "what does this error mean", "check my email draft",
                "what does this graph show"]
    assert not [q for q in everyday if gemini_vision.needs_reading(q)]


def test_the_voice_is_told_careful_is_for_dense_reading_only():
    import config
    rules = " ".join(config._REALTIME_RULES.split())
    assert "Pass careful:true only for dense reading" in rules


# -- UD-02: the window in front, and the mouse --------------------------------------------------------------

MON = (0, 0, 1920, 1080)


@pytest.mark.parametrize("window, region", [
    ((0, 0, 1920, 1040), None),                       # maximised: the monitor is the window
    ((100, 100, 300, 200), None),                     # too small to be worth a crop
    ((480, 135, 1440, 945), (480, 135, 960, 810)),    # a window in the middle
    ((-50, 500, 900, 1300), (0, 500, 900, 580)),      # hanging off the monitor: the visible part
])
def test_the_window_in_front_or_the_whole_monitor(window, region):
    assert look_region.window_region(window, MON) == region


def test_a_box_on_the_crop_lands_on_the_same_pixels_of_the_monitor():
    region = (480, 135, 960, 810)                     # its centre is the monitor's centre
    assert look_region.to_monitor_box((500, 500, 500, 500), region, MON) == (500, 500, 500, 500)
    # the crop's top-left corner is the monitor's (480, 135)
    assert look_region.to_monitor_box((0, 0, 0, 0), region, MON) == (125, 250, 125, 250)
    second = (1920, 0, 2560, 1440)                    # a monitor to the right, at its own size
    r = look_region.box_to_rect((250, 250, 750, 750), second)
    assert r == (2560.0, 360.0, 3840.0, 1080.0) and look_region.rect_to_box(r, second) == (250, 250, 750, 750)


def test_the_mouse_is_a_hint_only_when_the_question_points():
    frame = (480, 135, 960, 810)
    hint = look_region.pointer_hint("which one should I pick?", (960, 540), frame)
    assert "x=500, y=500" in hint
    assert look_region.pointer_hint("what is on my screen", (960, 540), frame) == "", "not pointing: no tokens"
    assert look_region.pointer_hint("what is this?", (10, 10), frame) == "", "the mouse is not on the image"


# -- UD-03: the real control ------------------------------------------------------------------------------------

def test_a_box_round_a_button_snaps_to_the_button():
    box = (100.0, 100.0, 300.0, 150.0)                # the model's box, a bit off
    button = (110.0, 95.0, 290.0, 145.0)
    assert look_region.snap_rect(box, button) == button


@pytest.mark.parametrize("element, why", [
    ((0.0, 0.0, 1900.0, 1000.0), "a whole pane"),
    ((180.0, 110.0, 200.0, 130.0), "a tiny icon inside the box"),
    (None, "no control there - a picture, a game"),
])
def test_it_is_not_snapped_to_something_else(element, why):
    assert look_region.snap_rect((100.0, 100.0, 300.0, 150.0), element, (0, 0, 1920, 1040)) is None, why


# -- the three together in the look --------------------------------------------------------------------------

def test_a_look_at_a_window_maps_back_snaps_and_hints(monkeypatch):
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    drawn = []
    mgr.sig_draw = SimpleNamespace(emit=drawn.append)
    mgr.sig_clear_drawings = SimpleNamespace(emit=lambda: None)
    mgr._privacy_guard = False
    mgr._activity = lambda what: None
    shot = SimpleNamespace(base64_jpeg="whole", physical_left=0, physical_top=0,
                           physical_width=1920, physical_height=1080)

    async def capture(**_kw):
        return [shot]
    mgr._capture_shots = capture
    mgr._active_shot = lambda screens: shot
    mgr._shape_from_tag = lambda tag: {"tag": tag}
    import tutor
    monkeypatch.setattr(tutor, "foreground_window_rect", lambda: (480, 135, 1440, 945))
    monkeypatch.setattr(cm, "capture_region", lambda *a: ("crop", 960, 810))
    monkeypatch.setattr(cm, "active_window_title", lambda: "Shop")
    monkeypatch.setattr(look_region, "cursor_position", lambda: (960.0, 540.0))
    # The real "Buy" button, as Windows has it: the model's box is a little off.
    monkeypatch.setattr(look_region, "control_chain_at",
                        lambda x, y, limit=None: [((900.0, 500.0, 1020.0, 580.0), "ButtonControl")])
    seen = {}

    async def fake_look(question, image, **kw):
        seen.update(image=image, **kw)
        return gemini_vision.Look(answer="Pick the middle one.", sure=True, path="fast", model="flash",
                                  actions=[gemini_vision.Action("Buy", (440, 440, 560, 560), "rect")])
    monkeypatch.setattr(gemini_vision, "look", fake_look)
    out = asyncio.run(mgr._tool_look_at_screen({"question": "which one should I pick?"}))
    assert seen["image"] == "crop", "the window, not the whole monitor"
    assert "x=500, y=500" in seen["context"], "the mouse went in as a hint"
    # Buy at (900,500)-(1020,580) on the monitor, in the drawing's 0-1000 terms:
    assert out["highlights"][0]["tag"] == "[RECT:469,463,531,537:tora]"
    assert drawn == [{"tag": "[RECT:469,463,531,537:tora]"}]


# -- SP-04: thinking under control ----------------------------------------------------------------------------

def test_gemini_3_is_told_a_thinking_level_not_a_budget_it_ignores():
    """Sent thinkingBudget 0, gemini-3.8-flash still thought on 14 of 23 test
    screens; the pro model spent 2,050 tokens on a 1,536 budget."""
    assert gemini.thinking(0, "gemini-3.8-flash") == {"thinkingConfig": {"thinkingLevel": "low"}}
    assert gemini.thinking("low", "gemini-3.1-pro-preview") == {"thinkingConfig": {"thinkingLevel": "low"}}
    assert gemini.thinking(None, "gemini-3.8-flash") == {}, "unsaid stays unsaid"
    assert gemini.thinking(2048, "gemini-3.1-pro-preview") == {"thinkingConfig": {"thinkingBudget": 2048}}, \
        "the planner's budget is not measured by a bench yet - unchanged"


def test_a_model_that_still_takes_a_budget_gets_the_budget_a_level_means():
    assert gemini.thinking(0, "gemini-2.5-flash") == {"thinkingConfig": {"thinkingBudget": 0}}
    assert gemini.thinking("low", "gemini-2.5-pro") == {"thinkingConfig": {"thinkingBudget": 1024}}
    assert gemini.thinking(0, "gemini-2.5-pro") == {}, "a pro model refuses 0"


def test_the_deep_look_asks_for_the_low_level(monkeypatch):
    sent = {}

    async def fake_generate(model, contents, **kw):
        sent[model] = kw.get("think")
        return _reply()
    monkeypatch.setattr(gemini, "generate", fake_generate)
    asyncio.run(gemini_vision.look("what's the best move?", "img", deep=True))
    assert sent[Models.VISION_DEEP] == "low"
    assert gemini.thinking(sent[Models.VISION_DEEP], Models.VISION_DEEP)["thinkingConfig"] == {"thinkingLevel": "low"}


# -- SP-05: fewer words back ----------------------------------------------------------------------------------

def test_the_fast_look_writes_no_observations_and_the_deep_one_keeps_its_trail(monkeypatch):
    sent = {}

    async def fake_generate_json(model, contents, schema, **kw):
        sent[model] = (schema, kw["system"])
        return {"answer": "Click Sign in.", "actions": [], "sure": False}
    monkeypatch.setattr(gemini, "generate_json", fake_generate_json)
    asyncio.run(gemini_vision.look("where do I sign in?", "img"))      # unsure: fast, then deep
    fast_schema, fast_system = sent[Models.VISION_FAST]
    deep_schema, deep_system = sent[Models.VISION_DEEP]
    assert "observations" not in fast_schema["properties"] and "observations" not in fast_system
    assert "observations" in deep_schema["properties"] and "observations" in deep_system
    assert fast_schema["required"] == ["answer", "actions", "sure"]


# -- SP-06: the right image size ------------------------------------------------------------------------------

def test_the_fast_look_sees_medium_and_the_deep_look_the_full_image(monkeypatch):
    sent = {}

    async def fake_generate_json(model, contents, schema, **kw):
        sent[model] = kw.get("media_resolution")
        return {"answer": "Click Sign in.", "actions": [], "sure": False}
    monkeypatch.setattr(gemini, "generate_json", fake_generate_json)
    asyncio.run(gemini_vision.look("where do I sign in?", "img"))      # unsure: fast, then deep
    assert sent == {Models.VISION_FAST: "medium", Models.VISION_DEEP: None}
    monkeypatch.setattr(gemini_vision, "RESOLUTION", "low")            # the bench's --resolution
    asyncio.run(gemini_vision.look("where do I sign in?", "img"))
    assert sent == {Models.VISION_FAST: "low", Models.VISION_DEEP: "low"}


def test_a_resolution_goes_in_the_generation_config(monkeypatch):
    bodies = []

    class Reply:
        status_code = 200

        @staticmethod
        def json():
            return {"candidates": []}

    async def fake_post(url, body, timeout):
        bodies.append(body)
        return Reply()
    monkeypatch.setattr(gemini, "_post", fake_post)
    asyncio.run(gemini.generate("gemini-3.8-flash", [], media_resolution="medium"))
    asyncio.run(gemini.generate("gemini-3.8-flash", []))
    assert bodies[0]["generationConfig"]["mediaResolution"] == "MEDIA_RESOLUTION_MEDIUM"
    assert "mediaResolution" not in bodies[1]["generationConfig"], "unsaid: the model's default"


# -- SP-07: typed questions follow the same rule --------------------------------------------------------------

@pytest.mark.parametrize("q", [
    "which one should I pick?", "what should I click next", "why is this cell red", "explain this chart",
    "what does this error mean", "check my email draft", "what does this graph show", "how do I export this video",
    "what's wrong with this form", "which plan is better", "where is the save button", "what does this icon do",
    "how to change my password", "review this slide", "is this the right file", "what's the cheapest one",
    "recommend a font for this", "what is this table about", "fix the layout of this page", "summarise this page"])
def test_a_typed_everyday_question_goes_to_the_fast_model(q):
    from ai.gemini_provider import GeminiProvider
    assert GeminiProvider.pick_model(q, True, None) == Models.VISION_FAST


@pytest.mark.parametrize("q", ["what's my best move?", "where should I play to win this game?",
                               "what's wrong with this code?", "read the small print in this contract"])
def test_a_typed_game_move_or_dense_reading_goes_deep(q):
    from ai.gemini_provider import GeminiProvider
    assert GeminiProvider.pick_model(q, True, None) == Models.VISION_DEEP
    assert GeminiProvider.pick_model(q, True, "my-model") == "my-model", "the tray's choice always wins"


# -- SP-08: sharper routing words -----------------------------------------------------------------------------

@pytest.mark.parametrize("q,title,deep", [
    ("what should I play next?", "Spotify Premium", False),        # a song: SP-01's music screen
    ("what should I play next?", "", False),
    ("what should I play?", "Play chess online - lichess.org - Google Chrome", True),
    ("where should I play now?", "Microsoft Solitaire Collection", True),
    ("where should I play to win?", "", True),                     # "win" is a game word
    ("what's my best move?", "", True),
    ("where is the settings button?", "Play chess online - lichess.org", False),  # not a what-to-do question
])
def test_play_is_a_game_word_only_with_a_game_in_front(q, title, deep):
    assert gemini_vision.needs_judgement(q, title) is deep


def test_the_look_is_given_the_window_in_front(monkeypatch):
    sent = {}

    async def fake_generate_json(model, contents, schema, **kw):
        sent.setdefault("models", []).append(model)
        return {"answer": "Play Blinding Lights.", "actions": [{"label": "Blinding Lights", "box": [1, 1, 9, 9]}],
                "sure": True}
    monkeypatch.setattr(gemini, "generate_json", fake_generate_json)
    asyncio.run(gemini_vision.look("what should I play next?", "img", title="Spotify Premium"))
    assert sent["models"] == [Models.VISION_FAST]
    sent.clear()
    asyncio.run(gemini_vision.look("what should I play next?", "img", title="lichess.org"))
    assert sent["models"] == [Models.VISION_DEEP]


# -- SP-09: the control they mean, not the text inside it -----------------------------------------------------

BOX = (100.0, 100.0, 200.0, 136.0)                    # the model's box, drawn tight round "Sign in"
LABEL = ((115.0, 108.0, 185.0, 128.0), "TextControl")
BUTTON = ((95.0, 96.0, 215.0, 136.0), "ButtonControl")
PANE = ((0.0, 0.0, 1900.0, 1000.0), "PaneControl")


def test_the_label_inside_a_button_snaps_to_the_button():
    assert look_region.snap_rect(BOX, LABEL[0]), "the label alone would have been taken (UD-03)"
    assert look_region.best_control(BOX, [LABEL, BUTTON, PANE], (0, 0, 1920, 1040)) == BUTTON[0]


def test_with_nothing_clickable_the_innermost_that_fits():
    image = ((105.0, 100.0, 195.0, 136.0), "ImageControl")
    group = ((90.0, 90.0, 230.0, 150.0), "GroupControl")
    assert look_region.best_control(BOX, [image, group, PANE]) == image[0]
    assert look_region.best_control(BOX, [PANE], (0, 0, 1920, 1040)) is None, "a pane is never it"
    assert look_region.best_control(BOX, []) is None, "no tree: keep the box"


def test_of_two_clickable_ones_the_nearest_in_size():
    row = ((60.0, 90.0, 300.0, 150.0), "ListItemControl")      # 2.9x the box
    assert look_region.best_control(BOX, [LABEL, BUTTON, row]) == BUTTON[0]


def test_both_ends_of_a_move_are_snapped(monkeypatch):
    """A file dragged to a folder: the file's row and the folder, each exact."""
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    monitor = (0, 0, 1000, 1000)                       # 0-1000 boxes are pixels here
    chains = {(150, 118): [((100.0, 110.0, 200.0, 126.0), "ListItemControl")],
              (650, 518): [((600.0, 500.0, 700.0, 540.0), "TreeItemControl")]}
    monkeypatch.setattr(look_region, "control_chain_at", lambda x, y, limit=None: chains.get((int(x), int(y)), []))
    move = gemini_vision.Action("report.pdf", (108, 104, 128, 196), "move", to_box=(498, 604, 538, 696))
    snapped = asyncio.run(mgr._snap_to_controls([move], monitor, None))
    assert snapped == 2
    assert move.box == look_region.rect_to_box((100.0, 110.0, 200.0, 126.0), monitor)
    assert move.to_box == look_region.rect_to_box((600.0, 500.0, 700.0, 540.0), monitor)
