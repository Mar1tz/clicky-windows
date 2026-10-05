"""The one-window app's design language.

Tora grew as a tray app: every surface — agents, extensions, the accounting
dashboard, settings, API keys — is its own floating window, and the only way
between them is the tray menu. This module is the stylesheet and the type
scale for the single window that holds all of them instead.

The values come from ui/tokens.py, which is heytora.com's palette and type
in Qt terms: the site's warm soft grey with white cards in light, its pure
black in dark, Plus Jakarta Sans, and the cursor's blue as the one accent.
The module-level names below (BG, TEXT, ACCENT…) are filled by apply(), so
a theme change rewrites them and the next qss() / paintEvent picks it up.
Read them as `T.BG` at use time, never `from ui.app_theme import BG`.

Everything here is a value, never a widget. Widgets are in app_widgets.py.
"""

from __future__ import annotations

from ui import tokens

# ── Palette (filled by apply) ─────────────────────────────────────────────────
PALETTE: tokens.Palette = tokens.LIGHT
BG = BG_SIDEBAR = SURFACE = SURFACE_HOVER = SURFACE_SUNK = ""
BORDER = BORDER_STRONG = TEXT = TEXT_DIM = TEXT_FAINT = ""
INVERSE = ON_INVERSE = ""
FRAME = NAV_HOVER = NAV_PLATE = ""
OK = WARN = DANGER = BUSY = ""

ACCENT        = tokens.BLUE    # the cursor's blue, in every theme
ACCENT_HOVER  = tokens.BLUE_HOVER
ACCENT_PRESS  = tokens.BLUE_PRESS
def _rgba(hex_colour: str, alpha: float) -> str:
    h = hex_colour.lstrip("#")
    return f"rgba({int(h[0:2], 16)}, {int(h[2:4], 16)}, {int(h[4:6], 16)}, {alpha})"


ACCENT_SOFT   = _rgba(tokens.BLUE, 0.12)    # a selection's plate
ACCENT_EDGE   = _rgba(tokens.BLUE, 0.55)    # a focused edge
ACCENT_PLATE  = _rgba(tokens.BLUE, 0.20)    # a notice row under the pointer
ON_ACCENT     = tokens.ON_COLOUR

# State colours, keyed by AppState.name.lower() so the status pill can take
# whatever the manager emits without a translation table. Thinking is the
# blue: it is the cursor's colour while Tora is working.
STATE_COLOUR: dict[str, str] = {}


def apply(palette: tokens.Palette) -> None:
    """Make `palette` the current one. Callers then re-run qss()."""
    global PALETTE, BG, BG_SIDEBAR, SURFACE, SURFACE_HOVER, SURFACE_SUNK
    global BORDER, BORDER_STRONG, TEXT, TEXT_DIM, TEXT_FAINT, INVERSE, ON_INVERSE
    global OK, WARN, DANGER, BUSY, STATE_COLOUR, FRAME, NAV_HOVER, NAV_PLATE
    PALETTE = palette
    BG, BG_SIDEBAR = palette.bg, palette.sidebar
    SURFACE, SURFACE_HOVER, SURFACE_SUNK = palette.surface, palette.surface_hover, palette.surface_sunk
    BORDER, BORDER_STRONG = palette.border, palette.border_strong
    TEXT, TEXT_DIM, TEXT_FAINT = palette.text, palette.text_dim, palette.text_faint
    INVERSE, ON_INVERSE = palette.inverse, palette.on_inverse
    FRAME, NAV_HOVER, NAV_PLATE = palette.frame, palette.nav_hover, palette.nav_plate
    OK, WARN, DANGER, BUSY = palette.ok, palette.warn, palette.danger, ACCENT
    STATE_COLOUR = {
        "idle": TEXT_FAINT,
        "listening": OK,
        "thinking": BUSY,
        "speaking": WARN,
    }


def is_dark() -> bool:
    return PALETTE.name == "dark"


# Surfaces that must restyle when the palette changes (the app window, the
# tray popup). Plain callables, so this module stays free of Qt objects.
_listeners: list = []


def on_change(callback) -> None:
    _listeners.append(callback)


def refresh(choice: str) -> None:
    """Re-resolve the palette for a Settings choice ("system" | "light" |
    "dark") and tell every surface. Called when the choice changes and when
    Windows flips its own light/dark setting."""
    before = PALETTE
    apply(tokens.resolve(choice))
    if PALETTE is before:
        return
    for callback in list(_listeners):
        try:
            callback()
        except Exception:  # noqa: BLE001 - one surface must not stop the rest
            import logging
            logging.getLogger("tora").debug("theme listener failed", exc_info=True)


apply(tokens.LIGHT)

# ── Geometry ──────────────────────────────────────────────────────────────────
WINDOW_W, WINDOW_H = 1180, 760
WINDOW_MIN_W, WINDOW_MIN_H = 940, 600
RADIUS        = 14             # the window and its big surfaces
RADIUS_CARD   = tokens.RADIUS_MD    # the site's large plates
RADIUS_SMALL  = tokens.RADIUS_SM    # the site's --radius
BUTTON_PILL   = tokens.pill(tokens.BUTTON_H["md"])
SIDEBAR_W     = 228            # open
SIDEBAR_SHUT  = 64             # closed: icons only
TITLEBAR_H    = 46
PANEL_RADIUS  = tokens.RADIUS_LG
GUTTER        = 32             # page padding
COLUMN_W      = 1040           # a page's content: a centred column, never wider
GAP           = 16             # between cards


def font() -> str:
    return tokens.font_family()


# Kept for callers that read it as a constant; tokens.load_fonts() runs
# before any window is built, so by then this is the brand face.
FONT = tokens.BRAND_FONT


def qss() -> str:
    """The whole window's stylesheet.

    One sheet rather than per-widget styling: Qt resolves these by object
    name and class, so a new page picks up the look by naming its parts
    correctly instead of restating colours.
    """
    family = font()
    return f"""
    /* ── frame ─────────────────────────────────────────────────────────── */
    /* The window draws its own title bar (ui/window_frame.py); Windows
       still rounds, shadows and snaps it. The title bar and sidebar are
       one ground (FRAME); the pages sit on a raised panel inside it. */
    QWidget#appRoot {{ background: {FRAME}; }}
    QWidget#topBar, QWidget#sidebar {{ background: transparent; }}
    QFrame#panel {{
        background: {BG}; border: 1px solid {BORDER};
        border-radius: {PANEL_RADIUS}px;
    }}
    QStackedWidget#stack {{ background: transparent; }}
    QWidget#page {{ background: transparent; }}
    QWidget {{
        color: {TEXT};
        font-family: "{family}", "{tokens.FALLBACK_FONT}";
        font-size: 13px;
    }}
    QToolTip {{
        background: {INVERSE}; color: {ON_INVERSE}; border: none;
        border-radius: 6px; padding: 5px 8px; font-size: 12px;
    }}

    /* ── sidebar ───────────────────────────────────────────────────────── */
    /* The rows are painted (ui/app_shell.py); these are what is laid out. */
    QLabel#stateChip {{ color: {TEXT_DIM}; font-size: 12px; font-weight: 600; }}
    /* The wordmark in type, when the SVG cannot be drawn. */
    QLabel#wordmark {{ font-size: 20px; font-weight: 700; letter-spacing: -0.3px; }}

    /* ── page furniture ────────────────────────────────────────────────── */
    /* Bold at every heading size, tracking tightening as it grows (BRAND §3). */
    QLabel#pageTitle  {{ font-size: 26px; font-weight: 700; letter-spacing: -0.4px; }}
    QLabel#keycap {{
        background: {ACCENT_SOFT}; color: {ACCENT}; border: 1px solid {ACCENT_EDGE};
        border-radius: {RADIUS_SMALL}px; padding: 0px 8px;
        font-size: 24px; font-weight: 700; letter-spacing: -0.3px;
    }}
    QLabel#pageLede   {{ color: {TEXT_DIM}; font-size: 14px; }}
    QLabel#sectionTitle {{
        font-size: 10px; font-weight: 600; color: {TEXT_FAINT};
        letter-spacing: 1.5px;
    }}
    QLabel#cardTitle    {{ font-size: 15px; font-weight: 600; letter-spacing: -0.15px; }}
    QLabel#cardSub      {{ color: {TEXT_DIM}; font-size: 12px; }}
    QLabel#dim          {{ color: {TEXT_DIM}; }}
    QLabel#faint        {{ color: {TEXT_FAINT}; font-size: 12px; }}
    QLabel#metricValue  {{ font-size: 28px; font-weight: 700; letter-spacing: -0.4px; }}
    QLabel#metricLabel  {{ color: {TEXT_FAINT}; font-size: 10px; font-weight: 600; letter-spacing: 1.4px; }}
    QLabel#mono {{
        font-family: "Cascadia Mono", "Consolas", monospace;
        color: {TEXT_DIM}; font-size: 12px;
    }}

    /* Filled, never outlined as well: one container level per page
       (DESIGN.md §3C), and a white plate on the soft grey is edge enough. */
    QFrame#card {{
        background: {SURFACE}; border: none;
        border-radius: {RADIUS_CARD}px;
    }}

    /* ── page parts (ui/app_parts.py) ──────────────────────────────────── */
    /* Cards are filled; a list is a hairline round rows on the page's own
       ground (Wispr's history) - so the cards lead and the list recedes. */
    QFrame#sideCard {{
        background: {SURFACE}; border: none; border-radius: {tokens.RADIUS_XL}px;
    }}
    QFrame#listBox {{
        background: transparent; border: 1px solid {BORDER}; border-radius: {tokens.RADIUS_LG}px;
    }}
    QLineEdit#searchField {{
        background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 17px;
        padding: 0px 10px 0px 4px; font-size: 13px;
    }}
    QLineEdit#searchField:focus {{ border: 1px solid {ACCENT_EDGE}; }}
    QFrame#hoverRow {{ background: transparent; border: none; }}
    QWidget#rowActions {{ background: transparent; }}
    QPushButton#rowAction {{
        background: transparent; border: none; border-radius: {RADIUS_SMALL}px; padding: 0px;
    }}
    QPushButton#rowAction:hover {{ background: {NAV_HOVER}; }}
    QLabel#rowTime  {{ color: {TEXT_FAINT}; font-size: 12px; }}
    QLabel#rowTitle {{ font-size: 14px; font-weight: 600; }}
    QLabel#rowSub   {{ color: {TEXT_DIM}; font-size: 13px; }}
    QLabel#rowApp   {{ color: {TEXT_FAINT}; font-size: 12px; }}
    QLabel#statValue {{ font-size: 30px; font-weight: 700; letter-spacing: -0.6px; }}
    QLabel#statLabel {{ color: {TEXT_DIM}; font-size: 14px; }}
    QLabel#footTitle {{ font-size: 14px; font-weight: 700; }}
    /* ── the Settings dialog (ui/settings_dialog.py) ───────────────────── */
    QFrame#settingsCard {{ background: {SURFACE}; border: none; border-radius: {tokens.RADIUS_XL}px; }}
    QFrame#settingsNav {{
        background: {BG}; border: none; border-right: 1px solid {BORDER};
        border-top-left-radius: {tokens.RADIUS_XL}px; border-bottom-left-radius: {tokens.RADIUS_XL}px;
    }}
    QStackedWidget#settingsStack {{ background: transparent; }}
    /* On the dialog's white, a group is the page's soft grey (Wispr's blocks). */
    QFrame#settingsCard QFrame#card {{ background: {BG}; border: none; border-radius: {RADIUS + 0}px; }}
    QFrame#plansBox {{ background: transparent; border: 1px solid {BORDER}; border-radius: {RADIUS + 0}px; }}
    QFrame#vr {{ background: {BORDER}; border: none; }}
    QLabel#planName  {{ font-size: 18px; font-weight: 700; }}
    QLabel#planPrice {{ font-size: 26px; font-weight: 700; letter-spacing: -0.5px; }}
    QLabel#planFlag  {{
        background: {ACCENT_SOFT}; color: {ACCENT}; border-radius: 9px;
        font-size: 11px; font-weight: 700; padding: 2px 8px;
    }}
    QFrame#infoCard {{ background: {SURFACE}; border: none; border-radius: {tokens.RADIUS_LG}px; }}
    QLabel#infoTitle {{ font-size: 15px; font-weight: 700; }}
    QLabel#infoBig   {{ font-size: 22px; font-weight: 700; letter-spacing: -0.4px; }}
    QLabel#infoNote  {{ color: {TEXT_DIM}; font-size: 10px; font-weight: 700; letter-spacing: 1.3px; }}
    QLabel#infoFaint {{ color: {TEXT_FAINT}; font-size: 12px; }}
    QLabel#tileValue {{ font-size: 30px; font-weight: 700; letter-spacing: -0.6px; }}
    QLabel#tileLine  {{ font-size: 13px; }}
    QLabel#tileLineValue {{ color: {TEXT_DIM}; font-size: 13px; }}
    QFrame#upNext {{ background: {SURFACE}; border: none; border-radius: {tokens.RADIUS_XL}px; }}
    QLabel#upTitle  {{ font-size: 22px; font-weight: 700; letter-spacing: -0.4px; }}
    QLabel#upSub    {{ color: {TEXT_DIM}; font-size: 14px; }}
    QLabel#upPct    {{ color: {TEXT_DIM}; font-size: 13px; font-weight: 600; }}
    QLabel#upPrompt {{ font-size: 17px; font-weight: 600; letter-spacing: -0.2px; }}
    QLabel#emptyTitle {{ font-size: 15px; font-weight: 600; }}
    QLabel#emptySub   {{ color: {TEXT_DIM}; font-size: 13px; }}
    QFrame#banner {{ background: {_rgba(WARN, 0.12)}; border: none; border-radius: {RADIUS_CARD}px; }}
    QLabel#bannerText {{ color: {TEXT}; font-size: 13px; font-weight: 600; }}
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
    /* Buttons are full-radius pills, as on the site. The primary is the
       cursor's blue; everything else is an outline in the page's own ink. */
    QPushButton#primary {{
        background: {ACCENT}; border: none; border-radius: {BUTTON_PILL}px;
        color: {ON_ACCENT}; font-size: 13px; font-weight: 700;
        padding: 8px 18px; min-height: 18px;
    }}
    QPushButton#primary:hover  {{ background: {ACCENT_HOVER}; }}
    QPushButton#primary:pressed {{ background: {ACCENT_PRESS}; }}
    QPushButton#primary:disabled {{ background: {BORDER_STRONG}; color: {TEXT_FAINT}; }}
    QPushButton#ghost {{
        background: {SURFACE}; border: 1px solid {BORDER_STRONG};
        border-radius: {BUTTON_PILL}px; color: {TEXT};
        font-size: 13px; font-weight: 600; padding: 7px 16px; min-height: 18px;
    }}
    QPushButton#ghost:hover {{ background: {SURFACE_HOVER}; }}
    QPushButton#danger {{
        background: transparent; border: 1px solid {DANGER};
        border-radius: {BUTTON_PILL}px; color: {DANGER};
        font-size: 13px; font-weight: 600; padding: 7px 16px; min-height: 18px;
    }}
    QPushButton#danger:hover {{ background: {SURFACE_HOVER}; }}
    QPushButton#link {{
        background: transparent; border: none; color: {ACCENT};
        font-size: 12px; font-weight: 600; padding: 2px;
    }}
    QPushButton#link:hover {{ color: {ACCENT_HOVER}; }}

    /* ── what the embedded windows bring ───────────────────────────────── */
    /* A pack's dashboard is drawn by the pack, and its buttons and tabs
       have no names here. These give them the same pills and tabs as the
       rest. A named rule above still wins wherever it applies. */
    QPushButton {{
        background: {SURFACE}; border: 1px solid {BORDER_STRONG};
        border-radius: {tokens.pill(tokens.BUTTON_H['sm'])}px; color: {TEXT};
        font-size: 13px; font-weight: 600; padding: 6px 14px;
    }}
    QPushButton:hover {{ background: {SURFACE_HOVER}; }}
    QPushButton:disabled {{ color: {TEXT_FAINT}; border-color: {BORDER}; }}
    QTabWidget::pane {{
        background: {SURFACE}; border: 1px solid {BORDER};
        border-radius: {RADIUS_SMALL}px; top: -1px;
    }}
    QTabBar::tab {{
        background: transparent; color: {TEXT_DIM}; border: none;
        border-bottom: 2px solid transparent; padding: 7px 14px; margin-right: 2px;
        font-weight: 600;
    }}
    QTabBar::tab:hover {{ color: {TEXT}; }}
    QTabBar::tab:selected {{ color: {TEXT}; border-bottom-color: {ACCENT}; }}
    QMessageBox, QInputDialog {{ background: {SURFACE}; }}

    QLineEdit, QComboBox, QPlainTextEdit, QTextEdit {{
        background: {SURFACE}; border: 1px solid {BORDER_STRONG};
        border-radius: {RADIUS_SMALL}px; padding: 7px 10px;
        selection-background-color: {ACCENT}; selection-color: {ON_ACCENT};
    }}
    QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
        border: 1px solid {ACCENT};
    }}
    QSpinBox {{
        background: {SURFACE}; border: 1px solid {BORDER_STRONG};
        border-radius: {RADIUS_SMALL}px; padding: 6px 8px;
        selection-background-color: {ACCENT}; selection-color: {ON_ACCENT};
    }}
    QSpinBox:focus {{ border: 1px solid {ACCENT}; }}
    QSpinBox::up-button, QSpinBox::down-button {{ border: none; background: transparent; width: 16px; }}
    QCheckBox {{ spacing: 8px; font-size: 13px; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px; border-radius: 4px;
        border: 1px solid {BORDER_STRONG}; background: {SURFACE};
    }}
    QCheckBox::indicator:checked {{ background: {ACCENT}; border: 1px solid {ACCENT}; }}

    /* The agents' own panels, embedded in Settings (app_pages._tidy). */
    QLabel#setLabel, QLabel#acctLabel {{ font-size: 13px; font-weight: 600; color: {TEXT}; }}
    QLabel#setHint, QLabel#acctHint {{ font-size: 12px; color: {TEXT_FAINT}; }}
    QPushButton#setSave, QPushButton#acctSave {{
        background: {ACCENT}; border: none; border-radius: {BUTTON_PILL}px;
        color: {ON_ACCENT}; font-size: 13px; font-weight: 700; padding: 8px 18px;
    }}
    QPushButton#setSave:hover, QPushButton#acctSave:hover {{ background: {ACCENT_HOVER}; }}
    QFrame#acctCard {{
        background: transparent; border: 1px solid {BORDER}; border-radius: {RADIUS_CARD}px;
    }}
    QWidget#embedded, QWidget#acctHolder {{ background: transparent; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    /* The chevron is painted by ui.app_widgets.Select. Every CSS way of
       drawing one collapses to a dash inside Qt's down-arrow subcontrol, and
       a combo box with no chevron looks exactly like a read-only field. */
    QComboBox::down-arrow {{ image: none; width: 0px; height: 0px; }}
    QComboBox QAbstractItemView {{
        background: {SURFACE}; border: 1px solid {BORDER};
        selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT};
        outline: none; padding: 4px;
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
