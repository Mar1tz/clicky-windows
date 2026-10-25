"""One coordinate contract (PT-08, the Massive Direction Plan).

Physical px (Windows, UI Automation, mss), logical px (Qt, the overlay), a
picture's own pixels and the model's 0-1000 on it are named, and converted
in one module, screen/monitors.py; every picture keeps where it was cut
from and the size it was sent at. The owner's layout - a 125% screen either
side of a 100% one - is the case that matters: a point found on the right
monitor lands on the right monitor.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from screen import monitors
from screen.monitors import Frame, Logical, Monitor, Phys

ROOT = Path(__file__).resolve().parents[2]

# 125% | 100% (main) | 125%, as measured on 30 Sep: Qt keeps each corner.
LEFT = Monitor("L", -2400, 0, 2400, 1350, 1.25)
MAIN = Monitor("M", 0, 0, 1920, 1080, 1.0, primary=True)
RIGHT = Monitor("R", 1920, 0, 2400, 1350, 1.25)
LAYOUT = [LEFT, MAIN, RIGHT]


@pytest.fixture
def layout(monkeypatch):
    monkeypatch.setattr(monitors, "monitors", lambda: LAYOUT)


def test_the_spaces_are_named_and_round_trip(layout):
    p = Phys(1920 + 1200, 675)                    # the middle of the right monitor
    q = monitors.to_logical(*p)
    assert isinstance(q, Logical) and q == (1920 + 960, 540)
    back = monitors.to_physical(*q)
    assert isinstance(back, Phys) and abs(back.x - p.x) < 1e-6 and abs(back.y - p.y) < 1e-6


def test_a_frame_keeps_its_crop_and_scale(layout):
    # The right monitor, sent to the model at 1024 wide.
    f = Frame(RIGHT.left, RIGHT.top, RIGHT.width, RIGHT.height, 1024, 576)
    assert f.scale == pytest.approx((2400 / 1024, 1350 / 576))
    assert f.phys(512, 288) == Phys(1920 + 1200, 675)
    assert f.from_normalized(500, 500) == Phys(1920 + 1200, 675)
    assert f.normalized(Phys(1920 + 1200, 675)) == (500.0, 500.0)
    assert f.logical(f.from_normalized(500, 500)) == (1920 + 960, 540)
    assert f.logical_rect() == (1920, 0, 1920, 1080)


def test_a_frame_recorded_at_capture_needs_no_monitors():
    f = Frame(1920, 0, 2400, 1350, 1024, 576, 1920, 0, 1.25)
    assert f.logical(Phys(1920 + 1200, 675)) == (1920 + 960, 540)
    assert f.physical(Logical(1920 + 960, 540)) == (1920 + 1200, 675)


def test_the_vision_point_lands_on_the_right_monitor(monkeypatch):
    """The model boxes the middle of the right monitor's picture."""
    import asyncio

    from ai import gemini_vision

    async def box(*a, **kw):
        return {"found": True, "box": [450, 450, 550, 550], "label": "Save"}
    monkeypatch.setattr(gemini_vision.gemini, "generate_json", box)
    f = Frame(1920, 0, 2400, 1350, 1024, 576, 1920, 0, 1.25)
    found = asyncio.run(gemini_vision.locate(
        "Save", screenshot_jpeg_b64="x", original_width=1024, original_height=576,
        physical_width=2400, physical_height=1350, physical_left=1920, physical_top=0,
        dpi_scale=1.25, frame=f))
    assert found is not None and (found.x, found.y) == (1920 + 960, 540)


def test_nothing_else_divides_by_a_scaling():
    """The conversions live in screen/monitors.py; a sum by hand elsewhere is
    how points landed a quarter of a screen to the left before."""
    pattern = re.compile(r"/\s*(?:shot\.)?dpi(?:_scale)?\b|physical_left\s*\+.*/\s*s\b")
    offenders = []
    for folder in ("ai", "ui", "tutor_features"):
        for path in (ROOT / folder).rglob("*.py"):
            if pattern.search(path.read_text(encoding="utf-8", errors="replace")):
                offenders.append(str(path.relative_to(ROOT)))
    if pattern.search((ROOT / "companion_manager.py").read_text(encoding="utf-8")):
        offenders.append("companion_manager.py")
    assert not offenders, offenders
