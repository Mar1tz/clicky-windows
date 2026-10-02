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

# The cursor's blue. White type on it is 3.7:1 - fine for bold button
# labels, not for body text, so it is a fill and an indicator, not a text colour
# on light grounds.
BLUE = "#3380FF"
BLUE_HOVER = "#4A8FFF"
BLUE_PRESS = "#2A6FE0"
# The logo's swoosh. Kept for the wordmark only.
LOGO_BLUE = "#00B6FD"


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
)

# heytora-web `.dark`: #000 ground, 4% cards, 12-15% hairlines.
DARK = Palette(
    name="dark",
    bg="#000000",
    sidebar="#070707",
    surface="#0F0F0F",
    surface_hover="#1A1A1A",
    surface_sunk="#121212",
    border="#1F1F1F",
    border_strong="#2E2E2E",
    text="#FAFAFA",
    text_dim="#A1A1A1",
    text_faint="#6B6B6B",
    inverse="#FAFAFA",
    on_inverse="#000000",
    shadow_alpha=150,
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
