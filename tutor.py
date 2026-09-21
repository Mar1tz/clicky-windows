"""
Tutor-layer helpers: active window detection, privacy masking, "next" detection,
and locate-query classification. Kept out of companion_manager.py so the
orchestrator stays readable.
"""

from __future__ import annotations

import ctypes
import re
from ctypes import wintypes


# ── Active-window title (for per-app context memory) ─────────────────────────

def active_window_title() -> str:
    try:
        u = ctypes.windll.user32
        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return ""
        n = u.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value or ""
    except Exception:
        return ""


def app_key(title: str) -> str:
    """Reduce a noisy window title to a stable per-app key.

    Examples:
      "Premiere Pro - project.prproj"  -> "Premiere Pro"
      "YouTube — Google Chrome"        -> "Google Chrome"
      "VS Code — main.py"              -> "VS Code"
    """
    if not title:
        return "desktop"
    # Take the right-most app name chunk (usually after last "-" or "—")
    parts = re.split(r"\s[-—–|]\s", title)
    return (parts[-1] if parts else title).strip()[:48] or "desktop"


# ── Locate-query classification ──────────────────────────────────────────────

LOCATE_RE = re.compile(
    r"\b(where\s+(is|do|can|should)|how\s+do\s+i\s+(click|find|open|access|use|get\s+to)|"
    r"point\s+(at|to)|show\s+me\s+(the|where)|click\s+(the|on)|find\s+the|"
    r"locate\s+the|highlight\s+the)\b",
    re.IGNORECASE,
)

MULTISTEP_RE = re.compile(
    r"\b(how\s+(do\s+i|to)\s+(export|install|configure|set\s*up|setup|publish|"
    r"deploy|enable|disable|build|launch|download|upload|record))\b",
    re.IGNORECASE,
)

NEXT_RE = re.compile(r"^\s*(next|continue|go\s*on|keep\s*going|what'?s?\s*next)[\s.!?]*$",
                     re.IGNORECASE)

STOP_RE = re.compile(r"^\s*(stop|quit|cancel|never\s*mind|nevermind)[\s.!?]*$",
                     re.IGNORECASE)


def is_locate(q: str) -> bool:
    return LOCATE_RE.search(q) is not None


def is_multistep(q: str) -> bool:
    return MULTISTEP_RE.search(q) is not None


def is_next(q: str) -> bool:
    return NEXT_RE.match(q or "") is not None


def is_stop(q: str) -> bool:
    return STOP_RE.match(q or "") is not None


# ── New voice classifiers ────────────────────────────────────────────────────

REPEAT_RE = re.compile(
    r"^\s*(repeat|say\s+(it|that)\s*again|say\s+again|once\s+more|"
    r"what\s+(did\s+you|d['’]you)\s+say)[\s.!?]*$",
    re.IGNORECASE,
)

JOURNAL_TODAY_RE = re.compile(
    r"\bwhat\s+(did|have)\s+i\s+(learn|learned|asked)\s+(today|so\s+far)\b",
    re.IGNORECASE,
)

JOURNAL_WEEK_RE = re.compile(
    r"\bwhat\s+(did|have)\s+i\s+(learn|learned)\s+(this\s+week|recently|"
    r"in\s+the\s+past\s+week)\b",
    re.IGNORECASE,
)

QUIZ_REVIEW_RE = re.compile(
    r"\b(quiz\s+me|review\s+me|test\s+me)(\s+on\s+(what\s+i\s+learned|my\s+notes))?\b",
    re.IGNORECASE,
)

# ── Identity questions ────────────────────────────────────────────────────────
# Vision models tend to refuse to identify people in images even when the
# answer is trivially in their training data. So when the user asks "who is X"
# / "tell me about X" / "what does X do" — we strip the screenshot and answer
# from text + web search. is_identity_question() returns True when:
#   • the query starts with a who/what-is question word, AND
#   • the rest looks like a proper noun (a person/thing name, not a generic word)
#
# False on things like "who is on my screen", "what is this", "who am I" — those
# legitimately want the screenshot.
IDENTITY_RE = re.compile(
    r"^\s*"
    r"(who\s+(is|are|was|were)|"
    r"tell\s+me\s+about|"
    r"what\s+(is|does|do)|"
    r"info\s+(about|on)|"
    r"how\s+old\s+is)"
    r"\s+"
    # require what follows to NOT be a screen-referring phrase
    r"(?!"
    r"this|that|it|my\s+screen|on\s+(my\s+)?screen|going\s+on|"
    r"happening|the\s+screen|here|i\s|i\b)"
    r".+",
    re.IGNORECASE,
)


def is_identity_question(q: str) -> bool:
    """Detects 'who is <person>'-style queries that should NOT include a screenshot."""
    return bool(q) and IDENTITY_RE.match(q.strip()) is not None


def is_repeat(q: str) -> bool:
    return REPEAT_RE.match(q or "") is not None


def is_journal_today(q: str) -> bool:
    return JOURNAL_TODAY_RE.search(q or "") is not None


def is_journal_week(q: str) -> bool:
    return JOURNAL_WEEK_RE.search(q or "") is not None


def is_quiz_review(q: str) -> bool:
    return QUIZ_REVIEW_RE.search(q or "") is not None


# ── Privacy guard — block sensitive windows from being screenshotted ──────────

PRIVACY_BLOCKLIST = (
    r"\b(password|credential|secret|keepass|bitwarden|1password|lastpass|"
    r"authenticator|banking|sign\s*in|login|\.env)\b"
)
_PRIVACY_RE = re.compile(PRIVACY_BLOCKLIST, re.IGNORECASE)


def is_sensitive_window(title: str) -> bool:
    return bool(title) and _PRIVACY_RE.search(title) is not None


# ── Coreference: "that", "the one next to it", "go back there" ────────────────
#
# When the user points back at something already mentioned instead of naming
# it, these turn the phrasing into something the pointer can resolve. The live
# model handles a lot of this itself once it is told what "that" refers to,
# but these are the guarantee — see CompanionManager._resolve_reference.

# A direction relative to a known anchor. "the one to its left", "below that".
def deixis_direction(q: str) -> str | None:
    q = (q or "").lower()
    if re.search(r"\b(to the left|left of|left one|on the left|leftmost)\b", q):
        return "left"
    if re.search(r"\b(to the right|right of|right one|on the right|rightmost)\b", q):
        return "right"
    if re.search(r"\b(above|over it|on top|top one|up from)\b", q):
        return "up"
    if re.search(r"\b(below|beneath|under it|underneath|down from|bottom one)\b", q):
        return "down"
    if re.search(r"\b(next to|beside|adjacent|next one)\b", q):
        return "next"
    return None


# A pure reference back to the last target: "that", "the same", "go back".
# Deliberately anchored to the WHOLE utterance (optional leading verb, then the
# pronoun and nothing else) so "go back to the settings page" — which names a
# real target — does NOT match, while "go back there" does.
_BACKREF_RE = re.compile(
    # optional verb words (point/click/show/take/go/put/highlight/bring/where)
    r"^\s*(?:(?:point|click|show|take|go|come|put|highlight|find|bring|"
    r"where'?s?|where\s+is)\s+)*"
    # optional filler (me/at/on/to/the/back/right)
    r"(?:(?:me|at|on|to|the|back|right)\s+)*"
    # and it must END on a bare back-reference token
    r"(that|this|it|there|same|same\s+one|same\s+place|that\s+one|"
    r"back\s+there|back|there\s+again|that\s+again)"
    r"[\s.!?]*$",
    re.IGNORECASE,
)


def is_backref(q: str) -> bool:
    return _BACKREF_RE.match(q or "") is not None


# The anchor named after "... of X" ("to the left of the File menu" → "the
# File menu"). Empty when the anchor is implicit (the last target).
def anchor_after_of(q: str) -> str:
    m = re.search(r"\bof\s+(.+?)[\s.!?]*$", q or "", re.IGNORECASE)
    return m.group(1).strip() if m else ""


_PRONOUN_RE = re.compile(
    r"^\s*(?:the\s+)?(it|that|this|there|same|same\s+one|that\s+one)\s*$",
    re.IGNORECASE,
)


def is_pronoun(q: str) -> bool:
    return _PRONOUN_RE.match(q or "") is not None


# ── Resume a paused guide ─────────────────────────────────────────────────────

_RESUME_RE = re.compile(
    r"^\s*(?:(?:please|hey|ok|okay)\s+)?"
    r"(continue|resume|carry\s+on|keep\s+going|pick\s+up|go\s+on)"
    r"(?:\s+(?:with\s+)?(?:the\s+)?(?:guide|walkthrough|steps|tutorial|lesson))?"
    r"[\s.!?]*$",
    re.IGNORECASE,
)


def is_resume(q: str) -> bool:
    return _RESUME_RE.match(q or "") is not None


# ── App classification: does the accessibility tree see this app's content? ───
#
# The single biggest speed lever is NOT looking at the screen when the control
# names already answer the question. Most apps (Settings, Office, Explorer,
# native Win32) expose their content to UIA, so "where is X" is a ~5 ms name
# lookup. Browsers expose their chrome (tabs, buttons, address bar) but NOT the
# page content. Games, media players, image and PDF viewers expose almost
# nothing. Telling the model which of these it is stops it reaching for a
# ~1 s vision call when a name would do — and makes it look when it must.

def active_process_name() -> str:
    """Executable name of the foreground window's process, lowercased ('' on failure)."""
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return ""
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        open_proc = kernel32.OpenProcess
        open_proc.restype = wintypes.HANDLE
        open_proc.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        h = open_proc(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not h:
            return ""
        try:
            qn = kernel32.QueryFullProcessImageNameW
            qn.restype = wintypes.BOOL
            qn.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                           ctypes.POINTER(wintypes.DWORD)]
            buf = ctypes.create_unicode_buffer(512)
            size = wintypes.DWORD(512)
            if qn(h, 0, buf, ctypes.byref(size)):
                import os
                return os.path.basename(buf.value).lower()
            return ""
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        return ""


_BROWSERS = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe",
    "operagx.exe", "vivaldi.exe", "arc.exe", "iexplore.exe",
}
_OPAQUE_APPS = {
    # media
    "vlc.exe", "mpc-hc64.exe", "mpc-hc.exe", "wmplayer.exe", "mpv.exe",
    "spotify.exe",
    # image / design canvases
    "photos.exe", "photoshop.exe", "gimp.exe", "figma.exe", "illustrator.exe",
    "blender.exe", "krita.exe", "mspaint.exe",
    # pdf
    "acrobat.exe", "acrord32.exe", "sumatrapdf.exe",
    # common game launchers/engines expose little; the control-count heuristic
    # in app_class catches the rest.
    "steam.exe",
}


def app_class(process_name: str, control_count: int) -> str:
    """'web' | 'opaque' | 'named' — how readable this app's content is to UIA.

    web:    a browser. Chrome/tabs/buttons are namable; page CONTENT is not.
    opaque: a game, media player, image or PDF viewer — almost nothing namable.
    named:  a normal app whose controls the tree exposes (the common case).
    """
    p = (process_name or "").lower()
    if p in _BROWSERS:
        return "web"
    if p in _OPAQUE_APPS:
        return "opaque"
    # No known name, and the tree yielded barely any controls → treat as
    # opaque (a game or a custom-drawn canvas).
    if control_count <= 1:
        return "opaque"
    return "named"


def look_hint(cls: str) -> str:
    """The routing line the model is given for when to call look_at_screen."""
    if cls == "web":
        return (
            " This is a web browser: you can point at tabs, buttons and the "
            "address bar by name, but the PAGE CONTENT is not readable from "
            "names — call look_at_screen for anything about what the page "
            "shows or says."
        )
    if cls == "opaque":
        return (
            " This app's content is not readable from control names — call "
            "look_at_screen for anything about what is actually on screen."
        )
    return (
        " This app exposes its controls, so the names above usually answer "
        "'where is…' without a look."
    )


# ── Which monitor is the user actually on? ────────────────────────────────────
#
# With more than one monitor, mss returns them in its own order — screens[0] is
# NOT necessarily the one with the active window. Pointing that always used
# screens[0] would look at (and point on) the wrong monitor entirely. This
# gives the physical rect of the foreground window so the manager can pick the
# ScreenShot whose bounds contain it.

def foreground_window_rect():
    """(left, top, right, bottom) of the foreground window in PHYSICAL px, or None."""
    try:
        u = ctypes.windll.user32
        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return None
        rect = wintypes.RECT()
        if not u.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        return None


def foreground_window_center():
    """(x, y) centre of the foreground window in PHYSICAL px, or None."""
    r = foreground_window_rect()
    if r is None:
        return None
    return ((r[0] + r[2]) // 2, (r[1] + r[3]) // 2)
