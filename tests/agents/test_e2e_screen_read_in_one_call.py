"""The screen's controls in one call (SC-06, the speed review of 17 Oct 2026).

The screen line - the window, its focus and the controls Tora can point at by
name - took 0.8 s at the median, walking the accessibility tree node by node
from Python. Windows now builds the tree of named, pointable controls itself
and hands it back cached, in one call; the walk stays as the fallback.
"""
from __future__ import annotations

from types import SimpleNamespace

from ai import hybrid_pointer as hp


def test_only_named_pointable_controls_once_each():
    seen: set = set()
    keep = hp._pointable("Save", "ButtonControl", (10, 10, 50, 30), seen)
    assert keep == (30, 20, "Save", "ButtonControl")
    assert hp._pointable("save", "ButtonControl", (60, 10, 90, 30), seen) is None, "once, by name"
    assert hp._pointable("Body text", "TextControl", (0, 0, 9, 9), seen) is None, "not something to point at"
    assert hp._pointable("Gone", "ButtonControl", (5, 5, 5, 20), seen) is None, "no size"
    assert hp._pointable("Minimize", "ButtonControl", (0, 0, 9, 9), seen) is None, "the window's own buttons"
    assert hp._pointable("x" * 41, "ButtonControl", (0, 0, 9, 9), seen) is None, "a sentence, not a name"


def test_an_app_that_will_not_build_the_cache_is_walked(monkeypatch):
    monkeypatch.setattr(hp, "_pointing_root", lambda hwnd=None: SimpleNamespace())

    def refuses(_root, _limit):
        raise OSError("the app said no")
    monkeypatch.setattr(hp, "_cached_pointables", refuses)
    monkeypatch.setattr(hp, "_walk_pointables", lambda root, limit, nodes, depth: [(1, 2, "Send", "ButtonControl")])
    assert hp.list_pointables() == [(1, 2, "Send", "ButtonControl")]


def test_the_cached_tree_is_read_top_first_and_skips_what_is_off_screen(monkeypatch):
    def el(name, kind, kids=(), off=False):
        rect = SimpleNamespace(left=0, top=0, right=20, bottom=10)
        arr = SimpleNamespace(Length=len(kids), GetElement=lambda i: kids[i]) if kids else None
        return SimpleNamespace(CachedName=name, CachedControlType=kind, CachedBoundingRectangle=rect,
                               CachedIsOffscreen=off, GetCachedChildren=lambda: arr)

    import uiautomation as auto
    B = auto.ControlType.ButtonControl
    tree = el("Window", auto.ControlType.WindowControl, kids=[
        el("Deep group", auto.ControlType.GroupControl, kids=[el("Inner", B)]),
        el("Top button", B),
        el("Scrolled away", B, off=True)])

    class UI:
        ControlViewCondition = object()

        def CreatePropertyCondition(self, *_a): return object()
        def CreateOrCondition(self, *_a): return object()
        def CreateAndCondition(self, *_a): return object()
        def CreateNotCondition(self, *_a): return object()
        def CreateCacheRequest(self): return SimpleNamespace(AddProperty=lambda _p: None)

    from uiautomation import uiautomation as _ua
    monkeypatch.setattr(_ua._AutomationClient, "instance", classmethod(lambda cls: SimpleNamespace(IUIAutomation=UI())))
    monkeypatch.setattr(hp, "_FILTER", None)
    root = SimpleNamespace(Element=SimpleNamespace(BuildUpdatedCache=lambda _req: tree))
    names = [n for _x, _y, n, _t in hp._cached_pointables(root, 10)]
    assert names == ["Top button", "Inner"], "breadth-first, nothing off screen"


# ── SC-07: searching by name reads the same one-call tree ────────────────────

def _cached(name, kind, rect=(0, 0, 100, 30), kids=()):
    arr = SimpleNamespace(Length=len(kids), GetElement=lambda i: kids[i]) if kids else None
    return SimpleNamespace(CachedName=name, CachedControlType=kind,
                           CachedBoundingRectangle=SimpleNamespace(left=rect[0], top=rect[1], right=rect[2], bottom=rect[3]),
                           GetCachedChildren=lambda: arr)


def _takealot_in_opera():
    return _cached("Takealot.com: Online Shopping - Opera", "Window", (0, 0, 1920, 1040), [
        _cached("Address bar area", "Pane", (0, 30, 1920, 80),
                [_cached("Search or enter address", "Edit", (200, 40, 900, 70))]),
        _cached("Takealot.com: Online Shopping | SA's leading online store", "Document", (0, 80, 1920, 1040),
                [_cached("Search products", "Edit", (400, 180, 1200, 220))])])


def _search(monkeypatch, top, **kw):
    kinds = {"Window": "WindowControl", "Pane": "PaneControl", "Edit": "EditControl", "Document": "DocumentControl"}
    monkeypatch.setattr(hp, "_pointing_root", lambda hwnd=None: SimpleNamespace())
    monkeypatch.setattr(hp, "named_controls", lambda root, **_k: hp._read_named(top, 40, kinds))
    monkeypatch.setattr(hp, "_walked_controls", lambda *a, **k: (_ for _ in ()).throw(AssertionError("walked")))
    return hp._find_via_uia("Search", **kw)


def test_the_steps_own_words_still_pick_the_pages_search(monkeypatch):
    assert _search(monkeypatch, _takealot_in_opera()) is None, "two close scores: no guess"
    hit = _search(monkeypatch, _takealot_in_opera(),
                  context="Click the search bar on Takealot, type laptops, and press Enter.")
    assert hit is not None and hit.label == "Search products" and (hit.x, hit.y) == (800, 200)


def test_the_trail_is_what_a_control_sits_inside_not_the_window():
    trails = {name: trail for name, _k, _r, trail in hp._read_named(_takealot_in_opera(), 40, {})}
    assert trails["Search products"] == " takealot com online shopping sa s leading online store"
    assert trails["Search or enter address"] == " address bar area"
    assert trails["Address bar area"] == "", "the window's title is everyone's: not counted"
