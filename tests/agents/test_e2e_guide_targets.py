"""LG-05: the right target, and fast.

24 Sep, 23:04, step three - "Click the search bar on Takealot, type laptops,
and press Enter", target "Search":

  * UIA found two, 0.75 and 0.70 - the page's search box and the browser's
    own - and gave up as ambiguous. The step says "on Takealot"; the walk
    never looked.
  * vision came back '"box": [964,' and finish=MAX_TOKENS: no arrow.
  * OCR, 8.3 s later, found "Q Search" at (566, 1056): the Windows taskbar.
"""
from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

from ai import hybrid_pointer

STEP = "Click the search bar on Takealot, type laptops, and press Enter."


class _Rect:
    def __init__(self, l, t, r, b):
        self.left, self.top, self.right, self.bottom = l, t, r, b

    def width(self):
        return self.right - self.left

    def height(self):
        return self.bottom - self.top


class _Node:
    def __init__(self, name, kind, rect=(0, 0, 100, 30), children=()):
        self.Name, self.ControlTypeName = name, kind
        self.BoundingRectangle = _Rect(*rect)
        self._children = list(children)

    def GetChildren(self):
        return self._children


def _opera_on_takealot():
    """The browser window: its toolbar's search box, and the page's."""
    browser_box = _Node("Search or enter address", "EditControl", (200, 40, 900, 70))
    page_box = _Node("Search products", "EditControl", (400, 180, 1200, 220))
    toolbar = _Node("Address bar area", "PaneControl", (0, 30, 1920, 80), [browser_box])
    page = _Node("Takealot.com: Online Shopping | SA's leading online store", "DocumentControl",
                 (0, 80, 1920, 1040), [page_box])
    # The window's own title names the page too - which is why the root's
    # name must not count as where a control is.
    return _Node("Takealot.com: Online Shopping - Opera", "WindowControl",
                 (0, 0, 1920, 1040), [toolbar, page])


def _walk(monkeypatch, root, **kw):
    monkeypatch.setitem(sys.modules, "uiautomation", SimpleNamespace())
    monkeypatch.setattr(hybrid_pointer, "_pointing_root", lambda hwnd=None: root)
    return hybrid_pointer._find_via_uia("Search", **kw)


def test_without_the_step_the_two_searches_are_a_coin_flip(monkeypatch):
    """What happened on 24 Sep: two close scores, so no answer."""
    assert _walk(monkeypatch, _opera_on_takealot()) is None


def test_the_steps_own_words_pick_the_pages_search(monkeypatch):
    hit = _walk(monkeypatch, _opera_on_takealot(), context=STEP)
    assert hit is not None and hit.label == "Search products", "on Takealot means the page"
    assert hit.bbox == (400, 180, 1200, 220)


def test_the_window_title_does_not_count_as_where_a_control_is(monkeypatch):
    """The window is titled after the page, so every control 'contains'
    Takealot if the root counts - and the tie stays a tie."""
    root = _opera_on_takealot()
    root._children[1].Name = "Online store"          # the page no longer says it
    assert _walk(monkeypatch, root, context=STEP) is None


def test_a_word_in_a_controls_own_name_does_not_tip_it(monkeypatch):
    """"press Enter" is a key, not a clue: the browser's "Search or ENTER
    address" gains nothing from it."""
    from ai.hybrid_pointer import _context_words
    words = _context_words(STEP, "Search")
    assert "enter" in words and "takealot" in words and "search" not in words
    hit = _walk(monkeypatch, _opera_on_takealot(), context=STEP)
    assert hit.label != "Search or enter address"


# ── vision ───────────────────────────────────────────────────────────────────

def test_the_vision_box_is_exactly_four_numbers():
    from ai.gemini_vision import _LOCATE_SCHEMA
    box = _LOCATE_SCHEMA["properties"]["box"]
    assert box["minItems"] == box["maxItems"] == 4


def test_vision_is_told_what_the_step_is_and_has_room_to_answer(monkeypatch):
    from ai import gemini_vision
    sent = {}

    async def generate_json(model, contents, schema, **kw):
        sent["text"] = contents[0]["parts"][1]["text"]
        sent["max_tokens"] = kw.get("max_tokens")
        return {"found": True, "label": "Search", "box": [100, 200, 140, 600]}

    monkeypatch.setattr(gemini_vision.gemini, "generate_json", generate_json)
    found = asyncio.run(gemini_vision.locate(
        "Search", hint=STEP, screenshot_jpeg_b64="AAAA", original_width=1024,
        original_height=576, physical_width=1920, physical_height=1080))
    assert found is not None
    assert STEP in sent["text"] and sent["max_tokens"] >= 2048


# ── the step's words reach every tier ────────────────────────────────────────

def test_a_steps_instruction_goes_down_with_its_target(monkeypatch):
    from companion_manager import CompanionManager
    import companion_manager as cm

    seen = {}

    def find_target(query, **kw):
        seen["local"] = kw.get("context")
        return hybrid_pointer.Target(x=10, y=20, bbox=(0, 0, 20, 40), label=query,
                                     source="uia", confidence=0.9)

    monkeypatch.setattr(hybrid_pointer, "find_target", find_target)
    monkeypatch.setattr(cm, "active_window_title", lambda: "Takealot.com - Opera")
    mgr = CompanionManager.__new__(CompanionManager)
    mgr._privacy_guard = False
    mgr._dpi_scale = lambda: 1.0
    mgr._activity = lambda *_a: None
    step = SimpleNamespace(target="Search", instruction=STEP)
    found = asyncio.run(mgr._step_target(object(), 2, step))
    assert found is not None and seen["local"] == STEP
