"""The one-window app's design language.

Tora grew as a tray app: every surface — agents, extensions, the accounting
dashboard, settings, API keys — is its own floating window, and the only way
between them is the tray menu. This module is the palette and the type scale
for the single window that holds all of them instead.

It deliberately does NOT touch ui/design.py. That file dresses the overlay
and the floating panel, which sit on top of the user's desktop and have to
stay translucent and unobtrusive. An application window is a different
problem: it is opaque, it is looked at for minutes rather than glanced at,
and it can afford depth, spacing and a type hierarchy. The two share their
blues so the product still reads as one thing.

Everything here is a value, never a widget. Widgets are in app_widgets.py.
"""

from __future__ import annotations

# ── Palette ───────────────────────────────────────────────────────────────────
# Near-black rather than black: a true #000 window on an OLED panel makes the
# rounded corners disappear into the desktop and the card edges vanish.
BG            = "#0B0C10"      # the window itself
BG_SIDEBAR    = "#0E0F14"      # navigation rail
SURFACE       = "#14161C"      # cards
SURFACE_HOVER = "#191C24"
SURFACE_SUNK  = "#101218"      # inputs, wells, code
BORDER        = "#232733"
BORDER_STRONG = "#2E3442"

ACCENT        = "#0A84FF"      # the same blue as the pointer and the orbs
ACCENT_SOFT   = "rgba(10, 132, 255, 0.12)"
ACCENT_EDGE   = "rgba(10, 132, 255, 0.45)"

TEXT          = "#F2F4F8"
TEXT_DIM      = "#98A0B3"
TEXT_FAINT    = "#5C6479"

OK            = "#32C86E"
WARN          = "#FFB020"
DANGER        = "#FF5A5A"
BUSY          = "#0A84FF"

# State colours, keyed by AppState.name.lower() so the status pill can take
# whatever the manager emits without a translation table.
STATE_COLOUR = {
    "idle": TEXT_FAINT,
    "listening": OK,
    "thinking": BUSY,
    "speaking": WARN,
}

# ── Geometry ──────────────────────────────────────────────────────────────────
WINDOW_W, WINDOW_H = 1180, 760
WINDOW_MIN_W, WINDOW_MIN_H = 940, 600
RADIUS        = 14             # the window and its big surfaces
RADIUS_CARD   = 12
RADIUS_SMALL  = 8
SIDEBAR_W     = 244
TITLEBAR_H    = 44
GUTTER        = 28             # page padding
GAP           = 16             # between cards

FONT = "Segoe UI"


def qss() -> str:
    """The whole window's stylesheet.

    One sheet rather than per-widget styling: Qt resolves these by object
    name and class, so a new page picks up the look by naming its parts
    correctly instead of restating colours.
    """
    return f"""
    /* ── frame ─────────────────────────────────────────────────────────── */
    /* No border and no radius: the window wears a NATIVE frame, and Windows
       rounds and shadows it. Every region paints its own background, so no
       part of the app depends on the one underneath it showing through. */
    QWidget#appRoot {{ background: {BG}; }}
    QStackedWidget#stack {{ background: {BG}; }}
    QWidget {{
        color: {TEXT};
        font-family: "{FONT}";
        font-size: 13px;
    }}

    /* ── sidebar ───────────────────────────────────────────────────────── */
    QWidget#sidebar {{
        background: {BG_SIDEBAR};
        border-right: 1px solid {BORDER};
    }}
    QLabel#wordmark {{ font-size: 17px; font-weight: 700; letter-spacing: 3px; }}
    QLabel#wordmarkSub {{ color: {TEXT_FAINT}; font-size: 10px; letter-spacing: 1px; }}
    QLabel#navGroup {{
        color: {TEXT_FAINT}; font-size: 10px; font-weight: 700;
        letter-spacing: 1.4px; padding: 0px 14px;
    }}
    QPushButton#navItem {{
        background: transparent; border: none; border-radius: {RADIUS_SMALL}px;
        color: {TEXT_DIM}; font-size: 13px; font-weight: 500;
        text-align: left; padding: 9px 12px 9px 14px;
    }}
    QPushButton#navItem:hover {{ background: {SURFACE}; color: {TEXT}; }}
    QPushButton#navItem:checked {{
        background: {ACCENT_SOFT}; color: {TEXT}; font-weight: 600;
    }}

    /* ── page furniture ────────────────────────────────────────────────── */
    QLabel#pageTitle  {{ font-size: 24px; font-weight: 700; }}
    QLabel#pageLede   {{ color: {TEXT_DIM}; font-size: 13px; }}
    QLabel#sectionTitle {{
        font-size: 11px; font-weight: 700; color: {TEXT_FAINT};
        letter-spacing: 1.2px;
    }}
    QLabel#cardTitle    {{ font-size: 14px; font-weight: 600; }}
    QLabel#cardSub      {{ color: {TEXT_DIM}; font-size: 12px; }}
    QLabel#dim          {{ color: {TEXT_DIM}; }}
    QLabel#faint        {{ color: {TEXT_FAINT}; font-size: 12px; }}
    QLabel#metricValue  {{ font-size: 26px; font-weight: 700; }}
    QLabel#metricLabel  {{ color: {TEXT_DIM}; font-size: 11px; letter-spacing: .6px; }}
    QLabel#mono {{
        font-family: "Cascadia Mono", "Consolas", monospace;
        color: {TEXT_DIM}; font-size: 12px;
    }}

    QFrame#card {{
        background: {SURFACE}; border: 1px solid {BORDER};
        border-radius: {RADIUS_CARD}px;
    }}
    QFrame#cardQuiet {{
        background: transparent; border: 1px solid {BORDER};
        border-radius: {RADIUS_CARD}px;
    }}
    QFrame#row {{
        background: transparent; border: none;
        border-top: 1px solid {BORDER}; border-radius: 0px;
    }}
    QFrame#well {{
        background: {SURFACE_SUNK}; border: 1px solid {BORDER};
        border-radius: {RADIUS_SMALL}px;
    }}
    QFrame#hr {{ background: {BORDER}; border: none; max-height: 1px; }}

    /* ── controls ──────────────────────────────────────────────────────── */
    QPushButton#primary {{
        background: {ACCENT}; border: none; border-radius: {RADIUS_SMALL}px;
        color: #FFFFFF; font-size: 13px; font-weight: 600; padding: 8px 16px;
    }}
    QPushButton#primary:hover  {{ background: #2E97FF; }}
    QPushButton#primary:pressed {{ background: #0069D9; }}
    QPushButton#ghost {{
        background: transparent; border: 1px solid {BORDER_STRONG};
        border-radius: {RADIUS_SMALL}px; color: {TEXT};
        font-size: 13px; font-weight: 500; padding: 7px 14px;
    }}
    QPushButton#ghost:hover {{ background: {SURFACE_HOVER}; border-color: {ACCENT_EDGE}; }}
    QPushButton#danger {{
        background: transparent; border: 1px solid rgba(255, 90, 90, .45);
        border-radius: {RADIUS_SMALL}px; color: {DANGER};
        font-size: 13px; font-weight: 500; padding: 7px 14px;
    }}
    QPushButton#danger:hover {{ background: rgba(255, 90, 90, .10); }}
    QPushButton#link {{
        background: transparent; border: none; color: {ACCENT};
        font-size: 12px; font-weight: 600; padding: 2px;
    }}

    QLineEdit, QComboBox, QPlainTextEdit {{
        background: {SURFACE_SUNK}; border: 1px solid {BORDER};
        border-radius: {RADIUS_SMALL}px; padding: 7px 10px;
        selection-background-color: {ACCENT};
    }}
    QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {ACCENT_EDGE}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    /* The chevron is painted by ui.app_widgets.Select. Every CSS way of
       drawing one collapses to a dash inside Qt's down-arrow subcontrol, and
       a combo box with no chevron looks exactly like a read-only field. */
    QComboBox::down-arrow {{ image: none; width: 0px; height: 0px; }}
    QComboBox QAbstractItemView {{
        background: {SURFACE}; border: 1px solid {BORDER};
        selection-background-color: {ACCENT_SOFT}; outline: none;
    }}

    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollBar:vertical {{
        background: transparent; width: 10px; margin: 4px 2px 4px 0px;
    }}
    QScrollBar::handle:vertical {{
        background: {BORDER_STRONG}; border-radius: 4px; min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {TEXT_FAINT}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
    """
