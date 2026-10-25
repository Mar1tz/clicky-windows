"""The calibration probe's arithmetic (PT-09, the Massive Direction Plan).

A crosshair meant at a logical point is "seen" in a monitor's picture where
the conversions say it should be; the probe finds it and converts it back.
Held at 100, 125 and 150% and mixed scaling, with monitors to the left of
and above the main one - the layouts that broke pointing before.
"""
from __future__ import annotations

import pytest
from PIL import Image, ImageDraw

from screen import calibrate
from screen.monitors import Frame, Monitor


def _layouts():
    main = Monitor("MAIN", 0, 0, 1920, 1080, 1.0, primary=True)
    yield "all 100%", [main, Monitor("R", 1920, 0, 1920, 1080, 1.0)]
    yield "125% either side", [Monitor("L", -2400, 0, 2400, 1350, 1.25), main,
                               Monitor("R", 1920, 0, 2400, 1350, 1.25)]
    yield "150% main, 100% left", [Monitor("L", -1920, 0, 1920, 1080, 1.0),
                                   Monitor("MAIN", 0, 0, 2880, 1620, 1.5, primary=True)]
    yield "125% above", [main, Monitor("UP", 0, -1350, 2400, 1350, 1.25)]
    yield "150% left and above", [main, Monitor("UL", -2880, -1620, 2880, 1620, 1.5)]


def _picture(m: Monitor, at, sent_width: int):
    """What a grab of `m` would show with the crosshair at logical `at`,
    downscaled to `sent_width` like a look's picture."""
    p = m.to_physical(*at)
    img = Image.new("RGB", (m.width, m.height), (30, 30, 30))
    d = ImageDraw.Draw(img)
    x, y = p[0] - m.left, p[1] - m.top
    arm = calibrate.ARM_LOGICAL * m.scale
    d.line([(x - arm, y), (x + arm, y)], fill=calibrate.MARK, width=4)
    d.line([(x, y - arm), (x, y + arm)], fill=calibrate.MARK, width=4)
    h = round(m.height * sent_width / m.width)
    return img.resize((sent_width, h), Image.Resampling.NEAREST)


@pytest.mark.parametrize("name,layout", list(_layouts()))
def test_a_mark_is_seen_where_it_was_meant(name, layout):
    for m in layout:
        for at in calibrate.targets(m):
            for sent in (m.width, 1280):
                img = _picture(m, at, sent)
                frame = Frame(m.left, m.top, m.width, m.height, img.width, img.height)
                seen = calibrate.find_mark(img)
                assert seen is not None
                err = calibrate.error(at, seen, frame, layout)
                assert err <= 2.0, f"{name} / {m.name} at {at} sent {sent}: {err:.1f} px off"


def test_no_mark_is_no_reading():
    assert calibrate.find_mark(Image.new("RGB", (64, 64), (0, 0, 0))) is None
