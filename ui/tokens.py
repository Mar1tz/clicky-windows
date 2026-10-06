"""HeyTora's design tokens: the website's palette and type, in Qt terms.

The app and heytora.com are meant to read as one product, and they had
drifted: the site is a warm soft grey with white cards and near-black type
(heytora-web/src/styles/globals.css, `.tora-soft` on <body>), while the app
was a blue-tinted near-black with Segoe UI and three different blues. This
module copies the site's token NAMES and VALUES so a change on one side has
an obvious twin on the other:

    web (globals.css)        here
    --background             bg
    --foreground             text
    --card / --card-border   surface / border_strong
    --muted                  surface_sunk
    --muted-foreground       text_dim
    --border                 border

Two deliberate differences from the site:

* The accent is the CURSOR's blue, #3380FF (ui/overlay.py's CURSOR_BLUE),
  not the logo's #00B6FD. The pointer is the thing users actually look at,
  so it is the blue the app is spent in.
* The app follows Windows' light/dark setting unless the user picks one in
  Settings. The light palette is the site's; the dark one is the site's
  `.dark` set (pure black, neutral greys - never blue-grey, which reads as
  a dashboard).

Values only; no widgets. ui/app_theme.py turns these into the stylesheet.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

# ── The three blues, one job each ─────────────────────────────────────────────
#
# BLUE       the accent: primary buttons, switches that are on, focus,
#            selection, links, progress, the "active" dot - and Tora's cursor
#            by default. White type on it is 3.7:1: fine for bold button
#            labels, not body text, so it is a fill and an indicator.
# LOGO_BLUE  the wordmark's swoosh, and the first stop of the brand ribbon.
# ICON_BLUE  the app icon (assets/brand/heytora-icon.svg). It lives in that
#            file; it is named here so nothing else reaches for it.
BLUE = "#3380FF"
BLUE_HOVER = "#4A8FFF"
BLUE_PRESS = "#2A6FE0"
LOGO_BLUE = "#00B6FD"
ICON_BLUE = "#009EFD"
# A chosen chip or card fills with a pale tint of the accent.
BLUE_TINT = "#EAF2FF"
BLUE_TINT_DARK = "#13233F"
# Type and glyphs on the blue, on a pack tile, on a gradient's deep end.
ON_COLOUR = "#FFFFFF"
# Windows' own red under the pointer on a title bar's close button. The
# window draws its own title bar (ui/window_frame.py), so it says it here.
WINDOW_CLOSE = "#C42B1C"

# What Tora's cursor may wear (Make Tora yours). One colour from each of the
# website's gradient families; the first is the default and the app's accent.
# The app's accent does not follow the choice - only the cursor does.
CURSOR_COLOURS = (
    ("blue", "Blue", "#3380FF"),
    ("cyan", "Sky", "#00B6FD"),
    ("violet", "Violet", "#8C7CFF"),
    ("pink", "Rose", "#E48AD8"),
    ("teal", "Teal", "#2FB8C8"),
    ("amber", "Amber", "#F29B38"),
)


def cursor_hex(key: str) -> str:
    return next((h for k, _n, h in CURSOR_COLOURS if k == key), CURSOR_COLOURS[0][2])


# ── Paper: drawn content ──────────────────────────────────────────────────────
#
# Things the app DRAWS rather than lays out - the equation card, the peeker's
# card, pack and capability cards on a gradient, the windows in the tour
# scenes - are content, and content stays light in both themes (§4: content,
# not frame). They all take their greys from here, not from the theme.

@dataclass(frozen=True)
class Paper:
    card: str = "#FFFFFF"
    ground: str = "#F4F3F0"      # the site's soft grey
    fill: str = "#EBE9E5"        # quiet buttons, a muted chip, a track
    sunk: str = "#ECEAE6"        # an unchosen card's face
    line: str = "#E6E4E0"        # hairlines inside a card
    edge: str = "#DCDAD6"        # a card's own edge
    ink: str = "#292929"         # type
    dim: str = "#5C5C5C"         # secondary type
    faint: str = "#8A8884"       # labels, captions
    ghost: str = "#9A9894"       # type on an unchosen card
    off_a: str = "#D9D7D3"       # an unchosen tile, light end
    off_b: str = "#BDBAB5"       # an unchosen tile, deep end
    grid: str = "#EEF3FB"        # squared paper
    pen: str = "#1F3A68"         # handwriting on squared paper
    ok: str = "#22A55B"


PAPER = Paper()


# The one dark moment in the light flow - "Nice job." - and its ribbon.
@dataclass(frozen=True)
class Stage:
    ground: str = "#141414"
    text: str = "#FAFAFA"
    dim: str = "#A1A1A1"
    button: str = "#F4F3F0"
    button_text: str = "#141414"


STAGE = Stage()


# ── The ribbon ────────────────────────────────────────────────────────────────
# The stroke that runs through the logo, through the site's "How it works",
# and behind the first launch's test screens.
RIBBON = (LOGO_BLUE, BLUE, "#A48CFF")                      # soft, behind content
RIBBON_POOLS = ("#7CCBFF", "#A48CFF")                     # the corner glows
RIBBON_FILLED = (LOGO_BLUE, "#3D7BFF", "#8C7CFF", "#E9A2FF", "#FFC6DE")   # the closing screen


# ── Content colour: the website's gradient families ───────────────────────────
# Copied from heytora-web (src/components/home). Same names, same hexes, so
# the app and the site stay in step. A thing keeps its family everywhere.

# what-gradient.tsx: one family per capability. (wash, blobs)
CAPABILITY = {
    "ask":   ("#D7EBFF", ("#7CCBFF", "#00B6FD", "#1E7FE8", "#8FE3F0", "#E3B0FF")),
    "show":  ("#D5DDFF", ("#2B63D6", "#6F7CF5", "#4F86E8", "#A48CFF", "#CFE9FF")),
    "teach": ("#F1E0FA", ("#B89CFF", "#F0A6FF", "#E48AD8", "#FFC6DE", "#D9D2FF")),
    "do":    ("#D6F4F0", ("#7FB6FF", "#39D5C6", "#2FB8C8", "#8FE8D6", "#BDF2E6")),
    "build": ("#FBF3DD", ("#9CE9DA", "#FFE08A", "#F6B96B", "#FFF0C4", "#B6EE7A")),
}

# how.tsx: one family per step, used in order.
STEP = (
    ("#BDEBFF", ("#00B6FD", "#3D7BFF", "#7FE3D0", "#BDEBFF")),
    ("#FFE3C4", ("#FF9A3C", "#FFC08A", "#DDF08A", "#FFE3C4")),
    ("#D9D2FF", ("#8C7CFF", "#E9A2FF", "#FFC6DE", "#D9D2FF")),
    ("#D6F7EE", ("#2FD0C0", "#B6EE7A", "#7CC8FF", "#D6F7EE")),
)

# A two-stop tile per kind of work, white glyph (the website's packs.tsx):
# the onboarding's skill cards.
WORK_TILES = {
    "ledger":    ("#00B6FD", "#3D7BFF"),
    "briefcase": ("#FFA24C", "#FF6B6B"),
    "cap":       ("#7CC4FF", "#00A3E8"),
    "code":      ("#4A505B", "#1F2329"),
    "chart":     ("#5B8CFF", "#1E4FD6"),
    "video":     ("#FF6B6B", "#E0314B"),
    "calendar":  ("#A99BFF", "#6F5BF5"),
    "mic":       ("#C9A0FF", "#8C5BF5"),
    "wallet":    ("#FFC27A", "#F28C3A"),
}

# what.tsx LOOK: the tour scenes' colours - (tile, pane, deep).
SCENE_LOOKS = (
    ("#00B6FD", "#E4F4FF", "#0089C2"),  # Ask - the brand cyan
    ("#9D8CFF", "#EDEBFF", "#6A55E8"),  # Show me - indigo, violet
    ("#F29BD8", "#FCEAF6", "#C94A9E"),  # Teach me - lilac into pink
    ("#39D5C6", "#E1F7F3", "#139C8F"),  # Do it - teal
    ("#F5C451", "#FFF4DC", "#C58A06"),  # Build it - warm amber, cream
)

# Tora's voices, one site colour each (Make Tora yours).
VOICE_COLOURS = ("#00B6FD", "#6F7CF5", "#E48AD8", "#39D5C6", "#F6B96B", "#3D7BFF",
                 "#A48CFF", "#FF9A3C", "#2FB8C8", "#F0A6FF", "#7FB6FF", "#9CE9DA")


# ── Shape ─────────────────────────────────────────────────────────────────────
# Four radii and the pill. Nothing in between.
RADIUS_SM = 8         # inputs, small chips, a cell
RADIUS_MD = 12        # cards, option cards, chips, wells
RADIUS_LG = 16        # floating cards, popups, a window in a picture
RADIUS_XL = 20        # the one card a screen is about (sign in, You're set)

# Button heights. One size per context: md in the app, lg for a screen's one
# action (the first launch's Continue), sm for answers inside a card.
BUTTON_H = {"sm": 30, "md": 34, "lg": 44}


def pill(height: int) -> int:
    """A pill's radius for a control this tall. One less than half: Qt drops
    a border-radius larger than half the height, and the button goes square."""
    return height // 2 - 1


# ── Elevation ─────────────────────────────────────────────────────────────────
# (blur, y offset, alpha in the light theme). Shadows are for floating things
# only; a card on the page has a hairline, not a shadow.
SHADOW = {
    "card":  (28, 6, 30),     # a drawn card on a gradient or the ribbon
    "float": (48, 12, 45),    # a floating card: tests, popups, pickers
    "hero":  (60, 16, 60),    # the one card a screen is about
}


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str               # the window ground
    sidebar: str          # navigation rail
    surface: str          # cards
    surface_hover: str    # a row under the pointer
    surface_sunk: str     # inputs, wells
    border: str           # hairlines - the main structural device
    border_strong: str    # edges of raised surfaces, switch tracks
    text: str
    text_dim: str
    text_faint: str
    inverse: str          # a plate in the text colour (keycaps, badges)
    on_inverse: str
    shadow_alpha: int     # how dark a floating surface's shadow is
    # The shell (DESIGN.md §3B): the frame - title bar and sidebar - is one
    # ground a step darker than the panel the pages sit on, as in Wispr Flow.
    frame: str = ""
    nav_hover: str = ""   # a sidebar row under the pointer
    nav_plate: str = ""   # the page you are on
    ok: str = "#22A55B"
    warn: str = "#E08A00"
    danger: str = "#E5484D"


# heytora-web `.tora-soft`: 40 14% 95% ground, white cards, 0 0% 16% type.
LIGHT = Palette(
    name="light",
    bg="#F4F3F0",
    sidebar="#EDEBE7",
    surface="#FFFFFF",
    surface_hover="#EBE9E5",
    surface_sunk="#FAF9F7",
    border="#DCDAD6",
    border_strong="#CFCCC6",
    text="#292929",
    text_dim="#5C5C5C",
    text_faint="#8A8884",
    inverse="#292929",
    on_inverse="#FFFFFF",
    shadow_alpha=40,
    frame="#ECEAE6",
    nav_hover="#E4E1DC",
    nav_plate="#DEDBD5",
)

# heytora-web `.dark`: #000 ground, 4-8% cards, 12-15% hairlines. The frame
# is the site's pure black; the panel the pages sit on is a step up from it.
DARK = Palette(
    name="dark",
    bg="#0B0B0B",
    sidebar="#000000",
    surface="#151515",
    surface_hover="#1F1F1F",
    surface_sunk="#111111",
    border="#222222",
    border_strong="#2E2E2E",
    text="#FAFAFA",
    text_dim="#A1A1A1",
    text_faint="#6B6B6B",
    inverse="#FAFAFA",
    on_inverse="#000000",
    shadow_alpha=150,
    frame="#000000",
    nav_hover="#141414",
    nav_plate="#1E1E1E",
    ok="#32C86E",
    warn="#FFB020",
    danger="#FF5A5A",
)

# The site's one easing curve, cubic-bezier(0.22, 1, 0.36, 1): fast out,
# long settle. Use with ease() below for every QPropertyAnimation.
EASE_POINTS = ((0.22, 1.0), (0.36, 1.0))
DURATION_FAST = 160
DURATION = 260


def ease():
    """A QEasingCurve matching the site's --tora-ease."""
    from PyQt6.QtCore import QEasingCurve, QPointF
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    (x1, y1), (x2, y2) = EASE_POINTS
    curve.addCubicBezierSegment(QPointF(x1, y1), QPointF(x2, y2), QPointF(1.0, 1.0))
    return curve


# ── type ──────────────────────────────────────────────────────────────────────
# Plus Jakarta Sans, the site's one face (SIL OFL, assets/fonts/OFL.txt).
# Segoe UI stands in if the files are missing, e.g. a stripped build.

BRAND_FONT = "Plus Jakarta Sans"
FALLBACK_FONT = "Segoe UI"
_font_family = ""


def assets_dir() -> Path:
    """assets/ next to main.py, or inside the PyInstaller bundle."""
    base = getattr(sys, "_MEIPASS", None)
    return (Path(base) if base else Path(__file__).resolve().parents[1]) / "assets"


def load_fonts() -> str:
    """Register the brand face once; returns the family to ask for.

    Needs a QGuiApplication. Safe to call again - the second call is free.
    """
    global _font_family
    if _font_family:
        return _font_family
    family = FALLBACK_FONT
    try:
        from PyQt6.QtGui import QFontDatabase
        for ttf in sorted((assets_dir() / "fonts").glob("PlusJakartaSans-*.ttf")):
            if QFontDatabase.addApplicationFont(str(ttf)) >= 0:
                family = BRAND_FONT
    except Exception:  # noqa: BLE001 - a missing font is cosmetic
        family = FALLBACK_FONT
    _font_family = family
    return family


def font_family() -> str:
    return _font_family or FALLBACK_FONT


def app_font(point_size: int = 10):
    """The application-wide QFont. Loads the face if needed.

    Unhinted, with a hair of extra tracking: Plus Jakarta Sans is drawn for
    the web, and Windows' hinting snaps its narrow letters together at body
    sizes - "I sit next to your cursor" read as one word. 0.3 px brings it
    back to how the browser sets it."""
    from PyQt6.QtGui import QFont
    f = QFont(load_fonts(), point_size)
    if f.family() == BRAND_FONT:
        f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.3)
    return f


# ── which palette ─────────────────────────────────────────────────────────────

THEMES = ("system", "light", "dark")


def system_is_dark() -> bool:
    """Windows' "app mode" - what Settings > Personalization > Colors says."""
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QGuiApplication
        hints = QGuiApplication.styleHints()
        if hints is not None:
            scheme = hints.colorScheme()
            if scheme != Qt.ColorScheme.Unknown:
                return scheme == Qt.ColorScheme.Dark
    except Exception:  # noqa: BLE001
        pass
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except Exception:  # noqa: BLE001
        return False


def resolve(choice: str) -> Palette:
    """The palette for a Settings choice: "system" | "light" | "dark"."""
    if choice == "dark":
        return DARK
    if choice == "light":
        return LIGHT
    return DARK if system_is_dark() else LIGHT
