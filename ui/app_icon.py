"""HeyTora's icon, as Windows shows it: the .exe's (the desktop shortcut,
the Start menu, Explorer, the installer, Apps - through assets/icon.ico,
which assets/make_icon.py writes at build time) and every window's title
bar and taskbar button.

Until 7 Oct 2026 the .exe still wore the old blue triangle while the app
inside wore the brand mark. The brand SVG (assets/brand/heytora-icon.svg)
sits small in a big square - right for the tray, whose "on" dot goes in the
corner - so here it is cropped to the mark, with a thin margin, and the icon
fills its square like any app's. It is drawn from the vector at every size,
never scaled down from a bitmap, so 16 px is as sharp as it can be.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QIcon, QImage, QPainter, QPixmap

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
MARGIN = 0.06                     # of the icon's side, on every edge


def brand_svg() -> Path:
    from ui import tokens
    return tokens.assets_dir() / "brand" / "heytora-icon.svg"


def _renderer(svg: Optional[Path] = None):
    from PyQt6.QtSvg import QSvgRenderer
    r = QSvgRenderer(str(svg or brand_svg()))
    return r if r.isValid() else None


def glyph_box(renderer) -> QRectF:
    """The mark's own bounds in the SVG's units, squared and with the margin
    added - found from a 512 px drawing of the whole canvas."""
    side = 512
    img = QImage(side, side, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    renderer.render(p, QRectF(0, 0, side, side))
    p.end()
    from PIL import Image
    raw = bytes(img.constBits().asstring(img.sizeInBytes()))
    box = Image.frombuffer("RGBA", (side, side), raw, "raw", "BGRA", 0, 1).getbbox()
    vb = renderer.viewBoxF()
    if not box:
        return vb
    left, top, right, bottom = box
    edge = max(right - left, bottom - top) / (1 - 2 * MARGIN)
    cx, cy = (left + right) / 2, (top + bottom) / 2
    scale = vb.width() / side
    return QRectF(vb.left() + (cx - edge / 2) * scale, vb.top() + (cy - edge / 2) * scale,
                  edge * scale, edge * scale)


def frames(sizes=SIZES, svg: Optional[Path] = None) -> list[QImage]:
    """The icon at each size, transparent around the mark. Empty when the
    SVG cannot be read."""
    r = _renderer(svg)
    if r is None:
        return []
    r.setViewBox(glyph_box(r))
    out = []
    for s in sizes:
        img = QImage(s, s, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r.render(p, QRectF(0, 0, s, s))
        p.end()
        out.append(img)
    return out


def qicon() -> QIcon:
    """For QApplication.setWindowIcon: every title bar and taskbar button."""
    icon = QIcon()
    for img in frames():
        icon.addPixmap(QPixmap.fromImage(img))
    return icon


def write_ico(path: Path, svg: Optional[Path] = None) -> list[int]:
    """assets/icon.ico for the build (heytora.spec, installer.iss): every
    size Windows asks for, each drawn for its size. Returns the sizes."""
    from PIL import Image
    images = []
    for img in frames(SIZES, svg):
        raw = bytes(img.constBits().asstring(img.sizeInBytes()))
        images.append(Image.frombuffer("RGBA", (img.width(), img.height()), raw, "raw", "BGRA", 0, 1).copy())
    if not images:
        raise RuntimeError(f"cannot read {svg or brand_svg()}")
    # Pillow's ICO writer takes the largest frame as the base and the others
    # as they are - not scaled down from it (see the 16x16-only icon of old).
    images[-1].save(path, format="ICO", sizes=[im.size for im in images], append_images=images[:-1])
    return [im.width for im in images]
