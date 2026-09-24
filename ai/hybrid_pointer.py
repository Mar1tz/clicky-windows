"""
Hybrid pointer — Tora pointing accuracy upgrade.

The original grid-locator was inaccurate because it asked the LLM to guess
pixel coordinates from a numbered grid overlay. Vision models trained on
natural images don't have pixel-precise spatial reasoning, so they'd often
pick a neighboring cell or off-by-one row, sending Tora's blue cursor to
the wrong button.

This module is the local half of a three-tier resolver:

  Tier 1  Windows UI Automation (UIA)     <1 s, capped at ~1.2 s   pixel-perfect
          Walks the OS accessibility tree, finds elements by name/role.
          Works in every standard UI: Chrome/Edge/Firefox, VS Code, IDEs,
          Office, Electron apps, native Win32, WinUI/UWP, Settings, etc.

  Tier 2  Offline OCR (RapidOCR/ONNX)     ~2 s at 1080p, idle CPU  text-perfect
          For canvas apps where UIA gives no tree: CapCut, Figma, Photoshop,
          games, older Java/Swing UIs. Runs entirely on CPU, no API calls.

  Tier 3  The vision model                ~3-4 s    handles anything
          Lives in ai/gemini_vision.py and is run by the manager, because
          it is a network call and these two are not.

Both tiers here are free, and UIA is usually the fastest of the three. But
"local" is not "instant": in an app with no accessibility tree both miss,
and waiting for them to before asking the model cost 13-32 s per walkthrough
step in CapCut. So the manager starts the model too when they are slow, or
straight away in an app it already knows has no tree (APP-23).

Public API:
    find_target(query: str) -> Optional[Target]

The caller (companion_manager) treats Target as opaque — it has .center_xy
in LOGICAL screen pixels, ready to feed directly into the overlay pointer.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Optional, List, Tuple

log = logging.getLogger("tora.pointer")


@dataclass
class Target:
    """A pointing target with exact pixel coordinates in LOGICAL screen space."""
    x: int                # center X
    y: int                # center Y
    bbox: Tuple[int, int, int, int]   # (left, top, right, bottom)
    label: str            # what we matched (button text, control type, etc.)
    source: str           # "uia" | "ocr" | "vision"
    confidence: float     # 0.0–1.0

    @property
    def center_xy(self) -> Tuple[int, int]:
        return (self.x, self.y)


# ──────────────────────────────────────────────────────────────────────────────
#  TIER 1 — Windows UI Automation
# ──────────────────────────────────────────────────────────────────────────────

# ⚠ uiautomation reports ControlTypeName WITH the suffix — "ButtonControl",
# not "Button". These sets were written without it, so nothing ever matched
# and the interactive-type boost below never once fired. Both forms are
# accepted now so a future rename cannot silently break it again.
_INTERACTIVE_TYPES = {
    # Most reliable click targets in UIA
    "Button", "Hyperlink", "MenuItem", "TabItem", "TreeItem", "ListItem",
    "RadioButton", "CheckBox", "ComboBox", "Edit", "Text",
    "Custom",  # often used by Electron / web apps
}
_INTERACTIVE_TYPES |= {t + "Control" for t in _INTERACTIVE_TYPES}


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


# Words that carry no target information — the model (and users) pad queries
# with them, and they must not count toward a match or "button" would hit
# every button on screen. Stripped before scoring.
_STOPWORDS = frozenset({
    "the", "a", "an", "to", "on", "in", "of", "my", "me", "this", "that",
    "button", "icon", "menu", "tab", "option", "link", "field", "box",
    "where", "is", "find", "show", "go", "get", "click", "open", "it",
})


# Control types a user can actually act on. A name that matches exactly is
# still the wrong answer when it belongs to a label or a status readout and
# the real button is three nodes further on.
_ACTIONABLE = (
    "Button", "Hyperlink", "MenuItem", "TabItem", "ListItem", "CheckBox",
    "RadioButton", "Edit", "ComboBox", "TreeItem", "SplitButton", "Slider",
    "Spinner", "Thumb", "DataItem", "HeaderItem", "ToolBar", "Menu",
)


def _is_actionable(ctrl_type: str) -> bool:
    t = (ctrl_type or "").replace("Control", "")
    return t in _ACTIONABLE


def _score_match(query: str, element_name: str, element_type: str) -> float:
    """Fuzzy match score 0..1, matched on WHOLE WORDS, not raw substrings.

    The earlier version scored any substring relationship ("q in name or name
    in q"), which pointed at the wrong control constantly: a one-letter query
    matched nine controls at 0.85, "new" matched "News" at 0.95, and ties went
    to whatever was first in the tree. This scores by shared words instead:

      • exact match                     → 1.0
      • the query's meaningful words are words of the name (and vice-versa)
        → high, weighted by how much of each side is covered
      • a single-word query that is a near-complete prefix of a name word
        (an icon paraphrase like "file" → "Files") → modest
      • anything else                   → 0

    So "the settings gear" still finds "Settings" (shared word), but "s" and
    "new"/"News" no longer produce confident false matches — they fall through
    to the vision model, which is the right backstop.
    """
    q = _normalize(query)
    name = _normalize(element_name)
    if not q or not name:
        return 0.0

    if q == name:
        base = 1.0
    else:
        q_words = q.split()
        n_words = name.split()
        n_set = set(n_words)
        # Meaningful query words (fall back to all words if it is all filler).
        q_key = set(w for w in q_words if w not in _STOPWORDS) or set(q_words)
        inter = q_key & n_set
        if inter:
            name_cov = len(inter) / max(len(n_set), 1)
            query_cov = len(inter) / max(len(q_key), 1)
            base = 0.5 * name_cov + 0.45 * query_cov
        else:
            # A single short word that is almost the whole of a name word:
            # "file" → "Files" (ratio 0.8), but NOT "new" → "News" (0.75).
            base = 0.0
            if len(q_words) == 1 and len(q_words[0]) >= 3:
                token = q_words[0]
                for w in n_words:
                    if w.startswith(token) and len(token) >= 0.8 * len(w):
                        base = 0.55
                        break

    if element_type in _INTERACTIVE_TYPES:
        base = min(1.0, base + 0.05)
    return round(base, 3)


# Control types worth naming to the model. Text and Custom are matchable
# when pointing but far too noisy to list — a browser page is thousands of
# Text nodes, and burying six real buttons in them helps nobody.
_NAMEABLE_TYPES = {
    "Button", "Hyperlink", "MenuItem", "TabItem", "TreeItem",
    "RadioButton", "CheckBox", "ComboBox", "ListItem",
}
_NAMEABLE_TYPES |= {t + "Control" for t in _NAMEABLE_TYPES}


# Present on every window. Matched as a prefix because Windows labels them
# per-window ("Close Settings", "Minimize Notepad") — an exact-match filter
# let every one of them through.
_CHROME_PREFIXES = ("minimize", "maximize", "restore", "close", "system menu")


def _is_chrome(name: str) -> bool:
    n = name.lower()
    return any(n.startswith(p) for p in _CHROME_PREFIXES)


def _pointing_root(hwnd: Optional[int] = None):
    """The UIA subtree a pointing query should search.

    Never Tora's own windows. The agent panel shows "Looking for Send",
    the timeline shows "find_on_screen(name=Compose)", the approval
    dialog shows the tool's arguments - and when any of them is in
    front, a walk from the foreground control finds those labels and
    reports the pointer's own reflection as a hit. So: a caller may name
    a window (`hwnd`); otherwise the foreground window is used unless it
    is ours, in which case the topmost window that is NOT ours is.
    """
    import os

    import uiautomation as auto

    me = os.getpid()
    if hwnd:
        try:
            ctrl = auto.ControlFromHandle(hwnd)
            if ctrl is not None and ctrl.ProcessId != me:
                return ctrl
            log.warning("refusing to point inside Tora's own window (hwnd=%s)", hwnd)
        except Exception:
            pass
    root = None
    try:
        root = auto.GetForegroundControl()
    except Exception:
        root = None
    try:
        if root is not None and root.ProcessId == me:
            for w in auto.GetRootControl().GetChildren():      # z-order: topmost first
                try:
                    rect = w.BoundingRectangle
                    if (w.ProcessId != me and (w.Name or "").strip()
                            and rect and rect.width() > 0 and rect.height() > 0):
                        return w
                except Exception:
                    continue
    except Exception:
        pass
    return root if root is not None else auto.GetRootControl()


def list_pointables(
    limit: int = 40, *, max_nodes: int = 1400, max_depth: int = 16,
    hwnd: Optional[int] = None,
) -> List[Tuple[int, int, str, str]]:
    """Named, on-screen controls from the foreground window.

    Returns (center_x, center_y, name, control_type) in PHYSICAL pixels. This
    is walked once per turn and the result feeds both the control-name list
    the model is told about and directional coreference ("the one to its
    left"), so neither pays for a second tree walk — repeated UIA walks are
    the slow part in Electron apps like VS Code.

    Bounded on purpose: the useful controls (menus, toolbars, tabs) sit near
    the top of the tree, and an unbounded walk of a deep Electron tree can
    take many seconds. Names only — never values, never anything typed.
    """
    try:
        import uiautomation as auto
    except ImportError:
        return []
    try:
        root = _pointing_root(hwnd)
    except Exception:
        return []
    if root is None:
        return []

    out: List[Tuple[int, int, str, str]] = []
    seen = set()
    queue: List[Tuple["auto.Control", int]] = [(root, 0)]
    visited = 0
    while queue and visited < max_nodes and len(out) < limit:
        node, depth = queue.pop(0)
        visited += 1
        try:
            name = (node.Name or "").strip()
            ctrl_type = node.ControlTypeName or ""
            rect = node.BoundingRectangle
        except Exception:
            continue

        if (name and ctrl_type in _NAMEABLE_TYPES
                and rect and rect.width() > 0 and rect.height() > 0
                and 1 < len(name) <= 40):
            key = name.lower()
            if key not in seen and not _is_chrome(name):
                seen.add(key)
                cx = int((rect.left + rect.right) // 2)
                cy = int((rect.top + rect.bottom) // 2)
                out.append((cx, cy, name, ctrl_type))

        if depth < max_depth:
            try:
                queue.extend((c, depth + 1) for c in node.GetChildren())
            except Exception:
                continue
    return out


def pointable_names(limit: int = 28, *, hwnd: Optional[int] = None) -> List[str]:
    """Just the names, for telling the model what it can point at by name."""
    return [name for _, _, name, _ in list_pointables(limit=limit, hwnd=hwnd)][:limit]


def nearest_in_direction(
    ax: float, ay: float, direction: str,
    *, candidates: Optional[List[Tuple[int, int, str, str]]] = None,
) -> Optional[Target]:
    """The named control nearest to (ax, ay) in a given direction.

    ax/ay are PHYSICAL pixels (the anchor the user referred to — "it"). Picks
    the closest control that lies in the requested direction, penalising drift
    off the axis so "the one to the left" is genuinely to the left and roughly
    level, not diagonally away. direction is left|right|up|down|next.

    `candidates` is the (cx, cy, name, type) snapshot from list_pointables —
    pass the one already gathered this turn so this never walks the tree a
    second time. Only when none is supplied does it walk (bounded), for
    callers with no snapshot in hand. Returns a Target in physical px, or None.
    """
    cands = candidates if candidates is not None else list_pointables(limit=80)
    if not cands:
        return None

    best = None
    best_score = None
    for cx, cy, name, ctrl_type in cands:
        dx, dy = cx - ax, cy - ay
        if direction == "left":
            if dx >= -4:
                continue
            along, perp = -dx, abs(dy)
        elif direction == "right":
            if dx <= 4:
                continue
            along, perp = dx, abs(dy)
        elif direction == "up":
            if dy >= -4:
                continue
            along, perp = -dy, abs(dx)
        elif direction == "down":
            if dy <= 4:
                continue
            along, perp = dy, abs(dx)
        else:                                   # "next" — beside, so horizontal
            if abs(dx) < 4:
                continue
            along, perp = abs(dx), abs(dy)
        # Off-axis drift hurts three times as much as distance along the axis,
        # so a control level with the anchor wins over a nearer diagonal one.
        score = along + 3 * perp
        if best_score is None or score < best_score:
            best_score = score
            best = (cx, cy, name)

    if best is None:
        return None
    cx, cy, name = best
    return Target(
        x=cx, y=cy, bbox=(cx - 20, cy - 20, cx + 20, cy + 20),
        label=name, source="uia", confidence=0.6,
    )


def focused_element() -> Optional[Tuple[str, str, bool]]:
    """(control_type, name, is_password) for whatever has keyboard focus."""
    try:
        import uiautomation as auto
        node = auto.GetFocusedControl()
        if node is None:
            return None
        ctrl_type = node.ControlTypeName or ""
        name = (node.Name or "").strip()
        is_password = bool(getattr(node, "IsPassword", False))
        return ctrl_type, name, is_password
    except Exception:
        return None


def focused_value() -> Optional[str]:
    """The text in whatever field has keyboard focus, or None.

    None - not "" - when there is no focused field, it exposes no value, or
    it is a password: a password box is never read, not even its length.
    """
    try:
        import uiautomation as auto
        node = auto.GetFocusedControl()
        if node is None or bool(getattr(node, "IsPassword", False)):
            return None
        pattern = node.GetPattern(auto.PatternId.ValuePattern)
        if pattern is None:
            return None
        return str(pattern.Value or "")
    except Exception:
        return None


def _find_via_uia(
    query: str, min_score: float = 0.5, *, max_seconds: float = 1.2,
    hwnd: Optional[int] = None,
) -> Optional[Target]:
    """Walk the UIA tree for the foreground window, find the best match.

    Bounded by TIME, and it stops the moment it finds a clear winner. Both
    matter enormously: every node touched here is a COM round trip, and the
    unbounded version measured **25 seconds** on an Electron window — which
    blocked the point_at tool, so the assistant went silent for 25 s before it
    could say a word. A near-exact match ends the walk immediately, and the
    deadline caps the worst case.
    """
    try:
        import uiautomation as auto
    except ImportError:
        log.warning("uiautomation not installed — Tier 1 (UIA) disabled")
        return None

    try:
        # The active app's window - or the app behind Tora's own windows,
        # or the window the caller named. See _pointing_root.
        root = _pointing_root(hwnd)
    except Exception as e:
        log.debug("UIA root lookup failed: %s", e)
        return None

    best: Optional[Tuple[float, "auto.Control"]] = None
    second: float = 0.0            # runner-up score, for the ambiguity check
    # Bounded walk — UIA trees can be huge in Chrome/Electron
    queue: List[Tuple["auto.Control", int]] = [(root, 0)]
    visited = 0
    MAX_NODES = 3500
    MAX_DEPTH = 40

    deadline = time.monotonic() + max_seconds
    while queue and visited < MAX_NODES:
        # Every node, not every 32nd: a node is four COM calls into the app,
        # and an app busy drawing (CapCut rendering captions) answers each
        # slowly - 32 of them ran the 1.2 s walk for many seconds (APP-23).
        # A clock read is nothing next to one COM call.
        if time.monotonic() > deadline:
            log.debug("UIA walk hit its %.1fs deadline for %r", max_seconds, query)
            break
        node, depth = queue.pop(0)
        visited += 1
        try:
            name = node.Name or ""
            ctrl_type = node.ControlTypeName or ""
            rect = node.BoundingRectangle  # mss/uia returns Rect
        except Exception:
            continue
        # Skip off-screen / zero-size
        if not rect or rect.width() <= 0 or rect.height() <= 0:
            pass
        else:
            # Name only. Reading AutomationId here cost another COM round
            # trip on nearly every node for a signal that almost never decided
            # the match — pure latency.
            score = _score_match(query, name, ctrl_type)
            if score >= 0.92 and _is_actionable(ctrl_type):
                # A clear winner: stop walking. There is nothing better to
                # find, and every further node is another COM call.
                best = (score, node)
                break
            if score >= 0.92:
                # An exact name on something you cannot click — a status-bar
                # readout, a label, a tooltip. The field log has the arrow
                # landing on a "google.com" TextControl in the corner of the
                # screen while the real link sat in the middle. Keep it as the
                # answer if nothing better turns up, but keep looking.
                score -= 0.06
            if score >= min_score:
                if best is None or score > best[0]:
                    second = best[0] if best is not None else second
                    best = (score, node)
                elif score > second:
                    second = score

        if depth < MAX_DEPTH:
            try:
                for child in node.GetChildren():
                    queue.append((child, depth + 1))
            except Exception:
                continue

    if not best:
        log.debug("UIA: no match for %r (scanned %d nodes)", query, visited)
        return None

    score, node = best
    # Ambiguity guard: if a second control scored almost as high, we cannot
    # tell which the user meant — pointing at the first-in-tree one is a
    # coin-flip. Unless the winner is a strong, near-exact match, hand the
    # question to the vision model instead of guessing.
    if score < 0.85 and (score - second) < 0.15:
        log.info("UIA ambiguous for %r (best=%.2f second=%.2f) — deferring",
                 query, score, second)
        return None
    r = node.BoundingRectangle
    cx = int((r.left + r.right) // 2)
    cy = int((r.top + r.bottom) // 2)
    log.info("UIA hit: %r -> %s [%s] @ (%d,%d) score=%.2f",
             query, node.Name, node.ControlTypeName, cx, cy, score)
    return Target(
        x=cx, y=cy,
        bbox=(int(r.left), int(r.top), int(r.right), int(r.bottom)),
        label=node.Name or node.ControlTypeName,
        source="uia",
        confidence=score,
    )


# ──────────────────────────────────────────────────────────────────────────────
#  TIER 2 — Offline OCR (RapidOCR, ONNX)
# ──────────────────────────────────────────────────────────────────────────────

_ocr_engine = None
_ocr_warned = False          # the missing-OCR warning fired 14x per session


def _get_ocr():
    global _ocr_engine
    if _ocr_engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _ocr_engine = RapidOCR()
        except ImportError:
            global _ocr_warned
            if not _ocr_warned:
                _ocr_warned = True
                log.warning("rapidocr-onnxruntime not installed — Tier 2 (OCR) disabled")
            return None
        except Exception as e:
            log.warning("OCR engine init failed: %s", e)
            return None
    return _ocr_engine


def _monitors() -> list[dict]:
    """Every monitor, as mss describes them (physical px, not the union)."""
    import mss
    make = getattr(mss, "MSS", None) or mss.mss
    with make() as sct:
        return [dict(m) for m in sct.monitors[1:]]


def _monitor_for(hwnd: Optional[int] = None) -> Optional[dict]:
    """The monitor the app being pointed into is on.

    mss's monitors[1] is NOT the primary monitor: on a PC with three
    screens it was the right-hand one, so OCR read whichever screen that
    was while CapCut sat on another, and could never find a thing there
    (APP-23). The window's own centre decides, as ScreenShot does.
    """
    monitors = _monitors()
    if not monitors:
        return None
    centre = None
    try:
        r = _pointing_root(hwnd).BoundingRectangle
        if r and r.width() > 0 and r.height() > 0:
            centre = ((r.left + r.right) // 2, (r.top + r.bottom) // 2)
    except Exception:  # noqa: BLE001 - fall back to the foreground window
        centre = None
    if centre is None:
        try:
            from tutor import foreground_window_center
            centre = foreground_window_center()
        except Exception:  # noqa: BLE001
            centre = None
    if centre is not None:
        cx, cy = centre
        for m in monitors:
            if m["left"] <= cx < m["left"] + m["width"] and m["top"] <= cy < m["top"] + m["height"]:
                return m
    return next((m for m in monitors if m.get("is_primary")), monitors[0])


# One OCR at a time, and its answer shared. It is CPU-bound: four at once
# took 8.3-8.8 s EACH on an 8-core PC where one takes 2.2 s, and a guide
# asks several times for the same screen (step one is pointed at twice,
# the next step's lookup overlaps the last). A caller who asked while a
# read of the same monitor was under way takes that read.
_ocr_lock = threading.Lock()
_ocr_last: Optional[tuple] = None      # (monitor key, captured at, detections)
OCR_SHARE_S = 0.5                      # a read begun this long before you asked is yours


def _ocr_screen(monitor: dict, asked_at: float):
    """RapidOCR's detections for one monitor - shared, see _ocr_lock."""
    global _ocr_last
    key = (monitor["left"], monitor["top"], monitor["width"], monitor["height"])
    with _ocr_lock:
        last = _ocr_last
        if last is not None and last[0] == key and last[1] >= asked_at - OCR_SHARE_S:
            return last[2]
        ocr = _get_ocr()
        if ocr is None:
            return None
        import mss
        import numpy as np
        from PIL import Image
        make = getattr(mss, "MSS", None) or mss.mss
        captured_at = time.monotonic()
        with make() as sct:
            raw = sct.grab({k: monitor[k] for k in ("left", "top", "width", "height")})
        image = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        result, _ = ocr(np.array(image))
        _ocr_last = (key, captured_at, result)
        return result


def _find_via_ocr(query: str, screenshot_path: Optional[str] = None,
                  pil_image=None, min_score: float = 0.5, *,
                  hwnd: Optional[int] = None) -> Optional[Target]:
    """OCR the monitor the app is on, fuzzy-match the query against its text.

    Given an image, that image is read and the result is in its own
    pixels. Otherwise the result is in screen pixels: the monitor's origin
    is added, which it never was - a hit on any screen but the one at
    (0, 0) sent the arrow to the same spot on the wrong monitor."""
    ocr = _get_ocr()
    if ocr is None:
        return None

    try:
        offset = (0, 0)
        if pil_image is None and screenshot_path is None:
            monitor = _monitor_for(hwnd)
            if monitor is None:
                return None
            offset = (int(monitor["left"]), int(monitor["top"]))
            result = _ocr_screen(monitor, time.monotonic())
        else:
            import numpy as np
            img_arr = np.array(pil_image) if pil_image is not None else screenshot_path
            result, _ = ocr(img_arr)
        if not result:
            log.debug("OCR returned no detections")
            return None

        best: Optional[Tuple[float, dict]] = None
        for det in result:
            # RapidOCR format: [bbox(4 corners), text, score]
            try:
                box, text, conf = det
            except Exception:
                continue
            # RapidOCR 1.2.x hands the score back as a STRING ('0.848…'), so
            # `conf < 0.4` raised TypeError on the first detection, the outer
            # handler logged "OCR tier failed" and this entire tier has never
            # once returned a result — every accessibility-tree miss went
            # straight to the vision model, slower and paid for.
            try:
                conf = float(conf)
            except (TypeError, ValueError):
                conf = 1.0
            if conf < 0.4:
                continue
            # OCR splits words at random ("Exte nsions"), which no amount of
            # fuzzy word matching recovers. Compare on a space-free form too.
            score = max(_score_match(query, text, ""),
                        _score_match(query.replace(" ", ""),
                                     "".join(str(text).split()), ""))
            if score >= min_score and (best is None or score > best[0]):
                # Compute bbox from 4 corners
                xs = [int(p[0]) for p in box]
                ys = [int(p[1]) for p in box]
                best = (score, {
                    "text": text,
                    "bbox": (min(xs), min(ys), max(xs), max(ys)),
                    "conf": conf,
                })

        if not best:
            log.debug("OCR: no text matched %r", query)
            return None

        score, hit = best
        ox, oy = offset
        l, t, r, b = hit["bbox"]
        l, t, r, b = l + ox, t + oy, r + ox, b + oy
        cx, cy = (l + r) // 2, (t + b) // 2
        log.info("OCR hit: %r -> %r @ (%d,%d) score=%.2f",
                 query, hit["text"], cx, cy, score)
        return Target(
            x=cx, y=cy, bbox=(l, t, r, b),
            label=hit["text"], source="ocr",
            confidence=score * hit["conf"],
        )
    except Exception as e:
        log.warning("OCR tier failed: %s", e)
        return None


# ──────────────────────────────────────────────────────────────────────────────
#  TIER 3 — the vision model
# ──────────────────────────────────────────────────────────────────────────────
#
# Not reachable from here any more. Tiers 1 and 2 are synchronous Windows
# calls; asking a model is a network round trip, and pretending otherwise
# means blocking whichever thread called find_target. The manager owns an
# event loop, so it runs ai.gemini_vision.locate() itself when this returns
# None — see CompanionManager._locate_target.
#
# What used to be here drew a numbered grid over the screenshot and asked a
# model which cell the element was in, then cropped and asked again: two
# round trips, two image encodings, and roughly 25-50 px of accuracy. Gemini
# 3 grounds boxes natively, so one call now does better than that did.


# ──────────────────────────────────────────────────────────────────────────────
#  PUBLIC API
# ──────────────────────────────────────────────────────────────────────────────

def find_target(
    query: str,
    *,
    screenshot=None,
    pil_image=None,
    skip_uia: bool = False,
    skip_ocr: bool = False,
    hwnd: Optional[int] = None,
) -> Optional[Target]:
    """Resolve a natural-language pointing query into pixel coordinates.

    Tries UIA then OCR - both local - and returns the first hit above the
    tier's confidence threshold. Returns None when neither finds it, which
    is the caller's cue to ask the vision model. "Local" is not "instant":
    the UIA walk is capped at ~1.2 s, and OCR of a 1080p screen takes about
    2 s on an idle 8-core PC and much longer on a busy one - which is why
    the manager does not wait for this before asking the model (APP-23).

    Args:
        query:      what the user asked Tora to point at (e.g. "save button",
                    "send icon", "login text field").
        screenshot: ScreenShot object (from screen.capture). Unused by these
                    tiers; kept so callers can pass it uniformly.
        pil_image:  PIL.Image of the full screen — used by OCR. Optional; OCR
                    captures its own if not provided.
        skip_*:     Force-skip a tier (for testing or perf-sensitive paths).

    Returns:
        Target with .center_xy in logical screen pixels, or None.
    """
    if not query or not query.strip():
        return None

    # Tier 1: UIA — fast, free, often perfect
    if not skip_uia:
        t = _find_via_uia(query, hwnd=hwnd)
        if t is not None and t.confidence >= 0.5:
            return t

    # Tier 2: OCR — text-based fallback for canvas apps
    if not skip_ocr:
        t = _find_via_ocr(query, pil_image=pil_image, hwnd=hwnd)
        if t is not None and t.confidence >= 0.5:
            return t

    log.info("local pointer tiers missed %r — vision model next", query)
    return None


__all__ = ["Target", "find_target", "pointable_names", "focused_element", "nearest_in_direction"]
