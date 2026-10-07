"""HeyTora's icon on Windows is the brand mark (ui/app_icon.py).

Until 7 Oct 2026 the .exe - and with it the desktop shortcut, the Start
menu, Explorer and the installer - still wore the old blue triangle that
assets/make_icon.py drew, while the app inside wore the brand mark. Now the
build's icon.ico is the mark, cropped to fill its square, at every size.
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PyQt6")
pytest.importorskip("PyQt6.QtSvg")

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_the_exe_icon_is_the_brand_mark_at_every_size(qapp, tmp_path):
    from PIL import Image
    from ui import app_icon
    out = tmp_path / "icon.ico"
    assert app_icon.write_ico(out) == [16, 20, 24, 32, 40, 48, 64, 128, 256]
    ico = Image.open(out)
    assert sorted(ico.info["sizes"]) == [(s, s) for s in app_icon.SIZES], "each size drawn for itself"
    ico.size = (256, 256)
    big = ico.copy().convert("RGBA")
    left, top, right, bottom = big.getbbox()
    assert max(right - left, bottom - top) >= 0.85 * 256, "the mark fills its square, not a corner of it"
    opaque = [px for px in big.getdata() if px[3] > 200]
    r, g, b = (sum(px[i] for px in opaque) / len(opaque) for i in range(3))
    assert b > 200 and r < 60, "the brand blue (#009EFD)"


def test_the_build_and_the_windows_use_it():
    assert "write_ico" in (ROOT / "assets" / "make_icon.py").read_text(encoding="utf-8")
    assert "app_icon.qicon()" in (ROOT / "main.py").read_text(encoding="utf-8")
    assert 'icon="assets/icon.ico"' in (ROOT / "heytora.spec").read_text(encoding="utf-8")
    assert "SetupIconFile=assets\\icon.ico" in (ROOT / "installer.iss").read_text(encoding="utf-8")


def test_a_missing_svg_is_not_a_crash(qapp, tmp_path):
    from ui import app_icon
    assert app_icon.frames(svg=tmp_path / "nope.svg") == []
    with pytest.raises(RuntimeError):
        app_icon.write_ico(tmp_path / "icon.ico", svg=tmp_path / "nope.svg")
