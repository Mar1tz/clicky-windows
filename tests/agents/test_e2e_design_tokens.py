"""One set of values for the whole UI (docs/ui/DESIGN.md, ui/tokens.py).

The first launch, the tour scenes and the app window each grew their own
colours, radii and shadows - 71 raw colours in one file, 46 in another,
eleven different radii - so the same grey existed under three spellings and
a change had to be made three times. They were consolidated into
ui/tokens.py (values) and ui/kit.py (how they are drawn). These tests keep
it that way: a colour written straight into a UI file fails here.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HEX = re.compile(r"#[0-9A-Fa-f]{6}\b")

# Files that take every colour from ui/tokens.py.
CONSOLIDATED = (
    "ui/kit.py",
    "ui/app_theme.py",
    "ui/app_widgets.py",
    "ui/app_window.py",
    "ui/app_pages.py",
    "ui/onboarding_parts.py",
    "ui/setup_wizard.py",
    "ui/tray_popup.py",
)


def _code_hexes(path: Path) -> list[str]:
    """Raw colours in code - not in comments or docstrings' prose."""
    found = []
    for line in path.read_text(encoding="utf-8").splitlines():
        code = line.split("#", 1)[0] if line.lstrip().startswith("#") else line
        found += HEX.findall(code)
    return found


def test_ui_files_take_their_colours_from_tokens():
    offenders = {f: _code_hexes(ROOT / f) for f in CONSOLIDATED}
    offenders = {f: h for f, h in offenders.items() if h}
    assert not offenders, f"use a ui/tokens.py name instead: {offenders}"


def test_the_tour_keeps_other_apps_colours_in_one_place():
    """A depicted application's colours live in DEPICTED (BRAND.md §2: they
    stop at its window's edge); everything else in the tour is a token."""
    src = (ROOT / "ui/tour_scenes.py").read_text(encoding="utf-8")
    head, _, rest = src.partition("DEPICTED = {")
    assert rest, "tour_scenes.DEPICTED is where depicted apps' colours go"
    rest = rest.split("\n}\n", 1)[1]
    stray = HEX.findall(head) + HEX.findall(rest)
    assert not stray, f"a tour colour outside DEPICTED: {stray}"


def test_the_scales_are_the_scales():
    from ui import tokens
    assert (tokens.RADIUS_SM, tokens.RADIUS_MD, tokens.RADIUS_LG, tokens.RADIUS_XL) == (8, 12, 16, 20)
    for h in tokens.BUTTON_H.values():
        assert tokens.pill(h) < h / 2, "Qt drops a radius over half the height: the pill goes square"
    assert set(tokens.SHADOW) == {"card", "float", "hero"}


def test_three_blues_each_named_once():
    from ui import tokens
    blues = {tokens.BLUE, tokens.LOGO_BLUE, tokens.ICON_BLUE}
    assert len(blues) == 3
    icon = (ROOT / "assets/brand/heytora-icon.svg").read_text(encoding="utf-8")
    assert tokens.ICON_BLUE.lower() in icon.lower(), "ICON_BLUE is the icon's own colour"
