"""The overlay redesign (the "HeyTora overlay redesign" doc, OD-01..09).

One test section per milestone. The classic look stays exactly as it is
behind the new_overlay switch until the new one is signed off, so these
tests check the new look and that nothing leaks between the two.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")

ROOT = Path(__file__).resolve().parents[2]
# Files that draw the new look. They take every colour from ui/overlay_theme.py.
NEW_LOOK_FILES = ["ui/overlay_paint.py"]


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def overlay(qapp):
    from ui.overlay import CursorOverlay
    o = CursorOverlay()
    yield o
    o._tick_timer.stop()
    o.deleteLater()


# -- OD-01: tokens, the switch, the preview ---------------------------------------------------------------

def test_the_overlay_takes_the_apps_colours(qapp):
    from ui import app_theme, overlay_theme as t
    assert (t.ACCENT, t.SURFACE, t.BORDER, t.TEXT, t.TEXT_DIM) == (
        app_theme.ACCENT, app_theme.SURFACE, app_theme.BORDER_STRONG, app_theme.TEXT, app_theme.TEXT_DIM)
    assert t.color(t.ACCENT, 0.5).alphaF() == pytest.approx(0.5, abs=0.01)


def test_the_classic_blue_is_defined_once(qapp):
    from ui import caption, overlay, overlay_theme as t
    for f in ("ui/overlay.py", "ui/caption.py"):
        src = (ROOT / f).read_text(encoding="utf-8")
        assert not re.search(r"0x33,\s*0x80,\s*0xFF|3380FF", src, re.I), f"{f} defines its own blue"
    assert overlay.CURSOR_BLUE.name() == caption.CAP_BLUE.name() == t.CLASSIC_BLUE.lower()


def test_the_new_look_holds_no_colours_of_its_own():
    for f in NEW_LOOK_FILES:
        path = ROOT / f
        if not path.exists():
            continue                                   # written in a later milestone
        src = path.read_text(encoding="utf-8")
        assert not re.search(r"QColor\(\s*\d", src), f"{f}: a colour literal - use ui/overlay_theme"
        assert not re.search(r"#[0-9A-Fa-f]{6}\b", src), f"{f}: a hex colour - use ui/overlay_theme"


def test_the_switch_is_saved_and_classic_until_signed_off():
    import config
    assert config.Config.SWITCHES["new_overlay"] == ("TORA_NEW_OVERLAY", False)
    assert config.cfg.switch("new_overlay") is False
    assert 'set_style("new" if cfg.switch("new_overlay") else "classic")' in (ROOT / "main.py").read_text(
        encoding="utf-8")


def test_the_cursor_and_the_island_switch_together(overlay):
    assert overlay.style == "classic" and overlay._caption.style == "classic"
    overlay.set_style("new")
    assert overlay.style == overlay._caption.style == "new"
    with pytest.raises(ValueError):
        overlay.set_style("sparkly")
    overlay.set_style("classic")
    assert overlay._caption.style == "classic"


def test_the_preview_covers_every_state():
    from PyQt6.QtCore import QRectF

    from ui.overlay_preview import REQUIRED, Stage, scenes
    names = [s.name for s in scenes(Stage(QRectF(0, 0, 1920, 1040)))]
    assert set(names) >= REQUIRED and len(names) == len(set(names))


def test_the_preview_renders_every_state_in_both_looks_without_a_window(qapp, tmp_path):
    from ui.overlay_preview import BACKDROPS, REQUIRED, shots
    paths = shots(str(tmp_path), ("classic", "new"), settle_scale=0.05)
    assert len(paths) == 2 * len(REQUIRED) * len(BACKDROPS)
    assert all(Path(p).stat().st_size > 2000 for p in paths)
    assert {p.split("-", 2)[2].rsplit("-", 1)[0] for p in map(lambda x: Path(x).name, paths)} == REQUIRED
