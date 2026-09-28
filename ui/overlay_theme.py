"""The overlay's design tokens: every colour, size and timing of the island,
the cursor and the step chip, in one place.

The overlay grew three blues, three type set-ups and a glow on everything,
because each piece carried its own literals. The redesign (the "HeyTora
overlay redesign" doc, milestones OD-01..09) draws only from these values,
and where the HeyTora window already has one (ui/app_theme.py) the overlay
takes the same value, so the two finally match.

Values only - no widgets - so ui/caption.py can import it without pulling
in any widget code.

The CLASSIC_* values are the current look's, kept here while both looks
exist behind the "new_overlay" switch. They go when the new look becomes
the only one (OD-09).
"""
from __future__ import annotations

from PyQt6.QtGui import QColor, QFont, QFontInfo

from ui import app_theme as _app

STYLE_CLASSIC = "classic"
STYLE_NEW = "new"
STYLES = (STYLE_CLASSIC, STYLE_NEW)

# -- colour (the app's where it has one) ----------------------------------------------------------
ACCENT = _app.ACCENT              # cursor, glyphs, badge edge, target ring
ACCENT_SOFT_A = 0.12              # the badge fill, as the app's ACCENT_SOFT
SURFACE = _app.SURFACE            # island and chip
SURFACE_A = 0.96
BORDER = _app.BORDER_STRONG       # 1 px round the island and chip
TEXT = _app.TEXT
TEXT_DIM = _app.TEXT_DIM          # what Tora is doing, e.g. "Checking your screen..."
OUTLINE = "#FFFFFF"               # the cursor's edge, so it shows on any screen
OUTLINE_A = 0.95
SHADOW = "#000000"
SHADOW_A = 0.35
SHADOW_Y = 4.0
SHADOW_BLUR = 12.0
CURSOR_SHADOW_BLUR = 2.0

# -- type -----------------------------------------------------------------------------------------
FONT_FAMILY = "Segoe UI Variable Text"
FONT_FALLBACK = "Segoe UI"
ISLAND_TEXT_PX = 14
CHIP_TEXT_PX = 13
BADGE_TEXT_PX = 11

# -- shape and size (logical px; Qt applies each monitor's scaling) ---------------------------------
ISLAND_R1 = 18.0                  # one line: a capsule
ISLAND_R2 = 16.0                  # two lines
ISLAND_W_MIN = 160.0
ISLAND_W_MAX = 440.0
ISLAND_H1 = 36.0
ISLAND_H2 = 58.0
ISLAND_TOP = 10.0                 # below the top of the work area
ISLAND_PAD_X = 14.0
ISLAND_GLYPH_W = 16.0
ISLAND_GLYPH_GAP = 8.0
ISLAND_LINE_GAP = 4.0

ARROW_H = 20.0                    # the pointer arrow, tip to tail
OUTLINE_W = 1.5
FOLLOW_DX = 14.0                  # from your own pointer
FOLLOW_DY = 10.0

RING_W = 2.0
RING_R = 14.0                     # when the target's bounds are unknown: a 28 px circle
RING_PAD = 4.0                    # round known bounds

CHIP_H = 28.0
CHIP_R = 10.0
CHIP_GAP = 8.0                    # from the target ring
CHIP_PAD_X = 10.0
CHIP_MAX_CHARS = 32
BADGE_R = 6.0
BADGE_H = 18.0
KEYCAP_R = 4.0

# -- motion (seconds) -------------------------------------------------------------------------------
FADE_S = 0.15
ISLAND_IN_S = 0.20
STEP_MOVE_S = 0.35
EXPLAIN_MOVE_MIN_S = 0.9
EXPLAIN_MOVE_MAX_S = 1.4
RING_IN_S = 0.15
LINGER_S = 1.5
DIM_WHILE_TALKING = 0.6           # the chip, while you are speaking

# -- the classic look, until OD-09 --------------------------------------------------------------------
CLASSIC_BLUE = "#3380FF"          # the triangle, bubble, card and caption of the current look


def color(hex_value: str, alpha: float = 1.0) -> QColor:
    """A token as a QColor, with the alpha (0-1) it is drawn at."""
    c = QColor(hex_value)
    c.setAlphaF(max(0.0, min(1.0, float(alpha))))
    return c


def font(px: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """The overlay's face at a pixel size, falling back to Segoe UI where the
    variable font is not installed (Windows 10)."""
    f = QFont(FONT_FAMILY)
    f.setPixelSize(int(px))
    f.setWeight(weight)
    if QFontInfo(f).family() != FONT_FAMILY:
        f = QFont(FONT_FALLBACK)
        f.setPixelSize(int(px))
        f.setWeight(weight)
    return f
