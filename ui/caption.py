"""
Top-of-screen caption island — the words Tora is saying, as it says them.

A dark glass capsule that extrudes from the top edge of the monitor the user
is actually looking at, fills with Tora's speech a word at a time, and
retracts when the answer ends. Modelled on the macOS Dynamic Island, adapted
for a screen with no notch: it floats 12px below the edge rather than
pretending to be part of the bezel.

This module is pure state + painting. It knows nothing about widgets, signals
or threads: CursorOverlay owns one instance, feeds it text through Qt slots,
calls tick() from its 60fps timer and draw() from paintEvent. Everything here
therefore runs on the Qt main thread and needs no locking.

The hard problem is not the drawing, it is the text. Gemini Live emits speech
transcript fragments several times a second — often sub-word, with punctuation
arriving late — and a caption that re-lays-out on every fragment looks broken.
Four independent layers keep it still:

  1. whole words only        a partial word is never rendered
  2. paced reveal            tokens leave a queue at a rate, not on arrival
  3. width ratchet           the pill only ever grows, in 24px steps, and is
                             re-evaluated on a word reveal, never per frame
  4. fixed wrap width        text wraps against a width locked for the whole
                             utterance, not the animating pill width

Motion is a single presence spring `p` that drives position, scale and alpha
together so they can never disagree; size is a plain lerp with no overshoot,
because a pill that bounces past its width and back is exactly the "cheap"
look this file exists to avoid.
"""

from __future__ import annotations

import math
import re
import time
from typing import Optional

from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QPainterPath,
    QFont, QFontInfo, QFontMetricsF, QLinearGradient,
)

from ui import overlay_theme as theme


# ── Tunables ─────────────────────────────────────────────────────────────────
# From the tokens module, which holds values only - so importing it pulls in
# no widget code, which is what this module must avoid.
CAP_BLUE = theme.color(theme.CLASSIC_BLUE)

CAP_TOP_MARGIN   = 12.0     # floats below the edge; Windows has no notch
CAP_PAD_X        = 20.0
CAP_PAD_Y        = 12.0
CAP_IND_W        = 21.0     # 4 bars × 3px + 3 gaps × 3px
CAP_IND_GAP      = 13.0
CAP_SIDE         = CAP_IND_W + CAP_IND_GAP   # mirrored on the right so the
                                             # text block is optically centred
CAP_LINE_GAP     = 5.0
CAP_W_MIN        = 236.0
CAP_W_SEED       = 120.0
CAP_H_SEED       = 30.0
CAP_RADIUS_MAX   = 22.0
CAP_MAX_CHARS    = 52       # live-reading ceiling; past ~55 the return saccade mis-lands
CAP_W_QUANTUM    = 24.0
CAP_W_DEADBAND   = 10.0

# Reveal governor (words per second)
CAP_LAG_S        = 0.35
CAP_WPS_MIN      = 2.6
CAP_WPS_PRE      = 3.0      # chunks streaming before the voice has started
CAP_WPS_SPEAK    = 7.5      # voice is playing — allowed to catch up
CAP_WPS_DRAIN    = 12.0     # answer is over, flush what is left
CAP_WPS_BULK     = 2.7      # ≈162 wpm, the TTS pace, for one-shot paragraphs
CAP_BULK_WORDS   = 12

# Cadence
CAP_BEAT_SENT    = 0.55     # ≈200ms breath after . ! ?
CAP_BEAT_CLAUSE  = 0.25
CAP_BUDGET_FLOOR = -2.0

# Timing
CAP_WORD_REVEAL_S   = 0.16
CAP_HILITE_S        = 0.38
CAP_HILITE_SENT_S   = 0.45
CAP_PARTIAL_FLUSH_S = 0.45  # a trailing partial word is committed after this silence
CAP_DEAD_AIR_S      = 1.2
CAP_BACKSTOP_S      = 20.0  # a hung provider can never pin the pill open
CAP_LINGER_CANCEL_S = 0.45
CAP_BARGE_CONFIRM_S = 1.5
CAP_EXIT_K          = 0.22  # ≈266ms
CAP_EXIT_K_FAST     = 0.34  # ≈150ms — cancel / user started talking

CAP_PUNCT_GLUE    = ",.!?;:…\"')]"
CAP_PUNCT_OVERHANG = 12.0
CAP_ADV_CACHE_MAX = 2000
CAP_BAR_PROFILE   = (0.62, 1.00, 0.86, 0.48)   # asymmetric: reads as a voice, not a graphic

# The cascade path strips [POINT:…]-style tags before emitting; the realtime
# path never went through that code, so guard against residue here too.
_ANY_BRACKET_TAG = re.compile(r"\[[A-Z]{2,10}(?::[^\]]*)?\]")
_MD_EDGE = "*_`#"


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def _smoothstep(u: float) -> float:
    return u * u * (3.0 - 2.0 * u)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


class _Line:
    __slots__ = ("text", "adv", "n")

    def __init__(self, text: str = "", adv: float = 0.0, n: int = 0) -> None:
        self.text = text
        self.adv = adv
        self.n = n


class CaptionIsland:

    def __init__(self, device=None) -> None:
        # Medium weight, not Regular or DemiBold: two of three monitors here run
        # DPR 1.25 against the primary's 1.0 and a spanning window carries one
        # backing-store DPR, so a light stem goes soft and a bold one blooms.
        f = QFont("Segoe UI Variable Display")
        f.setPointSizeF(12.5)
        f.setWeight(QFont.Weight.Medium)
        if QFontInfo(f).family() != "Segoe UI Variable Display":
            f = QFont("Segoe UI")
            f.setPointSizeF(12.5)
            f.setWeight(QFont.Weight.Medium)
        # The 1.5% tighten is what stops Segoe reading as a system dialog.
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 98.5)
        self._font = f
        # Built against the widget so measured text matches painted text.
        self._fm = QFontMetricsF(f, device) if device is not None else QFontMetricsF(f)
        self._lh = self._fm.height()
        self._avg_adv = self._fm.horizontalAdvance("abcdefghijklmnopqrstuvwxyz ") / 27.0
        self._h1 = self._lh + 2 * CAP_PAD_Y
        self._h2 = 2 * self._lh + CAP_LINE_GAP + 2 * CAP_PAD_Y

        self.needs_screen: bool = False
        self._style = theme.STYLE_CLASSIC      # the redesign's look is "new" (OD-01..09)

        # Text pipeline
        self._pending = ""              # raw chars not yet split into words
        self._queue: list[str] = []     # committed words waiting to be revealed
        self._words: list[str] = []     # revealed words (at most ~2 lines)
        self._lines: list[_Line] = []
        self._out_line = ""             # line rolling off the top
        self._dirty = False
        self._adv: dict[str, float] = {}

        # Episode bookkeeping
        self._state = "hidden"          # hidden | enter | live | linger | exit
        self._mode = "stream"           # stream | bulk
        self._budget = 0.0
        self._chunk_count = 0
        self._first_chunk_words = 0
        self._last_chunk_at = 0.0
        self._word_at = 0.0
        self._last_reveal_at = 0.0
        self._no_more = False
        self._sealed = False
        self._speaking = False
        self._hold = 0.0
        self._linger_until = 0.0
        self._exit_k = CAP_EXIT_K
        self._promoted = False
        self._max_w = 545.0
        self._content_max = 437.0

        # Simulation
        self._p = 0.0                   # presence: position, scale and alpha
        self._pv = 0.0
        self._w = CAP_W_SEED
        self._h = CAP_H_SEED
        self._tw = CAP_W_MIN
        self._th = self._h1
        self._roll = 0.0
        self._swap = 0.0
        self._energy = 0.0
        self._last_t = 0.0
        self._acc = 0.0
        self._barge_ticks = 0
        self._frozen_at = 0.0

        # Resolved every tick, consumed by draw()
        self._pill = QRectF()
        self._clip = QRectF()
        self._scale = 1.0
        self._alpha = 0.0
        self._text_alpha = 0.0
        self._now = 0.0
        self._path_cache: dict[tuple, QPainterPath] = {}

    # ── Public inputs — MAIN THREAD ONLY, reached through Qt signals ─────────

    def active(self) -> bool:
        return self._state != "hidden"

    def push(self, text: str) -> None:
        """A fragment of what Tora is saying. Opens the island on the first
        non-empty fragment of an utterance, regardless of app state — on the
        cascade path SPEAKING is emitted only after the whole answer has
        streamed, so waiting for it would show nothing until the end."""
        if not text:
            return
        t = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
        t = _ANY_BRACKET_TAG.sub("", t)
        if not t.strip():
            return
        now = time.monotonic()

        st = self._state
        if st == "hidden":
            self._begin_episode(fresh=True)
            self._state = "enter"
        elif st == "exit":
            # Caught mid-retraction: keep p, pv, w, h exactly where they are so
            # the pill swings back out instead of restarting from the bezel.
            self._begin_episode(fresh=False)
            self._state = "enter"
        elif self._no_more and not self._queue and not self._pending:
            # A new utterance while the island is still up (guide steps, "say
            # it again"). Swap the text in place; the capsule never re-enters.
            self._begin_episode(fresh=False)
            self._swap = 1.0

        self._chunk_count += 1
        if self._chunk_count == 1:
            self._first_chunk_words = len(t.split())
        self._last_chunk_at = now
        self._frozen_at = 0.0

        # Punctuation that arrives as its own fragment glues onto the previous
        # word rather than becoming a floating token.
        if not self._pending:
            s = t.lstrip()
            j = 0
            while j < len(s) and s[j] in CAP_PUNCT_GLUE:
                j += 1
            if j:
                run, s = s[:j], s[j:]
                if not self._glue_punct(run):
                    s = run + s
                t = s
        self._pending += t

    def finish(self, text: str = "") -> None:
        """Slot for sig_response_done: no more text is coming. This says
        nothing about whether the voice has finished — on the one-shot paths
        they are ~16 seconds apart — so it only flushes and flags."""
        if self._state == "hidden":
            return
        self._no_more = True
        self._pump_tokens(time.monotonic(), force=True)
        # One big dump then done = a paragraph handed to TTS in one go. Reveal
        # it at speaking pace or the caption finishes 15s before the voice.
        if self._chunk_count == 1 and self._first_chunk_words >= CAP_BULK_WORDS:
            self._mode = "bulk"

    def on_state(self, name: str) -> None:
        """AppState by name. SPEAKING never opens and never wipes."""
        if name == "SPEAKING":
            self._speaking = True
            return
        self._speaking = False
        if self._state == "hidden":
            return
        if name in ("LISTENING", "THINKING"):
            # A new question is starting. Whatever was queued will never be
            # spoken; showing it would be a lie.
            self._queue.clear()
            self._pending = ""
            self._seal(0.0, CAP_EXIT_K_FAST)
            self._state = "exit"
            return
        if name == "IDLE":
            self._pump_tokens(time.monotonic(), force=True)
            if self._no_more:
                visible = sum(ln.n for ln in self._lines)
                self._seal(_clamp(1.1 + 0.30 * visible, 1.6, 3.4), CAP_EXIT_K)
            else:
                # Esc, or an error path falling through its finally: cancelled.
                self._queue.clear()
                self._pending = ""
                self._seal(CAP_LINGER_CANCEL_S, CAP_EXIT_K_FAST)

    def note_audio_level(self, level: float) -> None:
        """Barge-in probe. The realtime session clears its player locally and
        emits nothing; the UI only learns a second later when LISTENING lands.
        The mic level is already in hand, so use it — but FREEZE, never wipe:
        this is a heuristic on a microphone and must never destroy text."""
        if self._state == "live" and level > 0.30:
            self._barge_ticks += 1
        else:
            self._barge_ticks = 0
        if self._barge_ticks >= 4 and not self._frozen_at:
            self._frozen_at = time.monotonic()

    def clear(self) -> None:
        self._begin_episode(fresh=True)
        self._state = "hidden"
        self._p = self._pv = 0.0
        self._alpha = self._text_alpha = 0.0

    # ── Driven by the overlay ────────────────────────────────────────────────

    def tick(self, now: float, screen: QRectF, ox: float, oy: float) -> None:
        if self._last_t == 0.0:
            self._last_t = now
        dt = min(0.05, max(0.0, now - self._last_t))
        self._last_t = now
        self._now = now

        if (self._state not in ("hidden", "exit") and not self._speaking
                and now - self._last_chunk_at > CAP_BACKSTOP_S):
            self._queue.clear()
            self._pending = ""
            self._exit_k = CAP_EXIT_K
            self._state = "exit"

        self._pump_tokens(now)
        self._govern(now, dt)
        if self._dirty:
            self._relayout()

        # Fixed substeps: a GC pause or a screenshot capture must not turn into
        # a burst of motion. Cap of 4 keeps a long stall from fast-forwarding.
        self._acc += dt
        steps = 0
        while self._acc >= 1.0 / 60.0 and steps < 4:
            self._substep()
            self._acc -= 1.0 / 60.0
            steps += 1

        st = self._state
        if st == "enter":
            if self._p >= 0.995 and self._pv <= 0.0:
                self._state = "live"
        elif st == "live":
            if self._sealed and not self._queue and not self._pending:
                self._state = "linger"
                self._linger_until = now + self._hold
        elif st == "linger":
            if now >= self._linger_until:
                self._state = "exit"
        elif st == "exit":
            if self._p < 0.02:
                self.clear()
                return

        self._geometry(screen, ox, oy)

    def draw(self, p: QPainter) -> None:
        if self._state == "hidden" or self._alpha < 0.01:
            return
        p.save()
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            # Clip to the monitor BEFORE the scale transform, so the pill is
            # sliced by that monitor's top edge and its shadow never spills
            # onto a neighbouring display.
            p.setClipRect(self._clip)
            rect = self._pill
            cx, cy = rect.center().x(), rect.center().y()
            s = self._scale
            p.translate(cx, cy)
            p.scale(s, s)
            p.translate(-cx, -cy)

            a = self._alpha
            r = min(rect.height() / 2.0, CAP_RADIUS_MAX)
            if self._style == theme.STYLE_CLASSIC:
                self._draw_bloom(p, rect, r, a)
            self._draw_shadow(p, rect, r, a)
            self._draw_body(p, rect, r, a)
            self._draw_indicator(p, rect, a, self._now)
            self._draw_text(p, rect, a, self._now)
        finally:
            p.restore()

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def _begin_episode(self, fresh: bool) -> None:
        self._pending = ""
        self._queue = []
        self._words = []
        self._lines = []
        self._out_line = ""
        self._dirty = False
        self._mode = "stream"
        self._budget = 0.0
        self._chunk_count = 0
        self._first_chunk_words = 0
        self._no_more = False
        self._sealed = False
        self._hold = 0.0
        self._exit_k = CAP_EXIT_K
        self._promoted = False
        self._roll = 0.0
        self._energy = 0.0
        self._barge_ticks = 0
        self._frozen_at = 0.0
        self._last_chunk_at = time.monotonic()
        self._word_at = 0.0
        self._last_reveal_at = 0.0
        # Width ratchet resets: a short follow-up must not inherit a long
        # answer's slab. The physical size lerps down to meet it.
        self._tw = CAP_W_MIN
        self._th = self._h1
        if fresh:
            self._p = self._pv = 0.0
            self._w = CAP_W_SEED
            self._h = CAP_H_SEED
            self._swap = 0.0
            self.needs_screen = True

    def _seal(self, hold: float, exit_k: float) -> None:
        self._sealed = True
        self._hold = hold
        self._exit_k = exit_k

    # ── Simulation ───────────────────────────────────────────────────────────

    def _substep(self) -> None:
        if self._state == "exit":
            np_ = self._p + (0.0 - self._p) * self._exit_k
            # Keep velocity honest so a mid-retraction catch has momentum.
            self._pv = np_ - self._p
            self._p = np_
        else:
            # Traced: peaks 1.116 at frame 8 (~133ms), settles ~400ms. An 11%
            # overshoot — a visible, tasteful pop.
            self._pv = self._pv * 0.60 + (1.0 - self._p) * 0.155
            self._p += self._pv
        # Size is deliberately NOT sprung. Overshoot is delightful on position
        # and fatal on a dimension.
        self._w += (self._tw - self._w) * 0.22
        self._h += (self._th - self._h) * 0.18
        self._roll += (0.0 - self._roll) * 0.22
        if self._out_line and self._roll < 0.02:
            self._out_line = ""
            self._roll = 0.0
        self._swap += (0.0 - self._swap) * 0.30
        self._energy *= 0.90

    def _geometry(self, screen: QRectF, ox: float, oy: float) -> None:
        p = self._p
        w = float(round(self._w))
        h = float(round(self._h))
        cx = screen.x() + screen.width() / 2.0 - ox
        rest_top = screen.y() + CAP_TOP_MARGIN - oy
        # At p=0 the whole pill is above the clip edge: it extrudes from the
        # bezel rather than fading in.
        y = rest_top - (h + CAP_TOP_MARGIN) * (1.0 - p)
        x = float(round(cx - w / 2.0))
        self._pill = QRectF(x, float(round(y)), w, h)
        self._clip = QRectF(screen.x() - ox, screen.y() - oy,
                            screen.width(), screen.height())
        scale = min(1.03, 0.84 + 0.16 * p)
        if self._state in ("live", "linger") and self._style == theme.STYLE_CLASSIC:
            scale *= 1.0 + 0.0015 * math.sin(self._now * 1.7)   # felt, not seen
        self._scale = scale
        # Opaque by p≈0.74: the fade finishes well before the motion settles,
        # which is what makes it read as a solid object arriving.
        self._alpha = _clamp(p * 1.35, 0.0, 1.0)
        self._text_alpha = self._alpha * _clamp((p - 0.35) / 0.65, 0.0, 1.0)

    # ── Text pipeline ────────────────────────────────────────────────────────

    def _advance(self, s: str) -> float:
        v = self._adv.get(s)
        if v is None:
            if len(self._adv) >= CAP_ADV_CACHE_MAX:
                self._adv.clear()
            v = self._fm.horizontalAdvance(s)
            self._adv[s] = v
        return v

    def _pump_tokens(self, now: float, force: bool = False) -> None:
        """Move whole words from the raw buffer into the reveal queue. A
        partial word is never committed unless the stream has gone quiet."""
        pend = self._pending
        if not pend:
            return
        if force or now - self._last_chunk_at > CAP_PARTIAL_FLUSH_S:
            chunk, self._pending = pend, ""
        else:
            i = pend.rfind(" ")
            if i < 0:
                return
            chunk, self._pending = pend[:i], pend[i + 1:]
        for tok in chunk.split():
            tok = tok.strip(_MD_EDGE)
            if not tok or tok in ("-", ">", "•"):
                continue
            self._queue.extend(self._split_long(tok))

    def _glue_punct(self, run: str) -> bool:
        if self._queue:
            self._queue[-1] += run
            return True
        if self._words and self._lines:
            # Grow the line in place. A comma that pushes a word onto the next
            # line is a visible jolt for zero readability gain, so this never
            # re-wraps; the line may overhang by CAP_PUNCT_OVERHANG.
            self._words[-1] += run
            ln = self._lines[-1]
            ln.text += run
            ln.adv = self._advance(ln.text)
            self._ratchet()
            return True
        return False

    def _split_long(self, tok: str) -> list[str]:
        """URLs, Windows paths and CJK arrive as one unwrappable token."""
        if self._advance(tok) <= self._content_max:
            return [tok]
        return [tok[i:i + 4] for i in range(0, len(tok), 4)]

    def _govern(self, now: float, dt: float) -> None:
        """Tokens leave the queue at a rate — a lag filter, not a buffer."""
        if not self._queue:
            return
        if self._frozen_at:
            if now - self._frozen_at < CAP_BARGE_CONFIRM_S:
                return
            self._frozen_at = 0.0          # a cough or a door: resume, nothing lost
        if self._sealed:
            rate = CAP_WPS_DRAIN
        elif self._mode == "bulk":
            rate = CAP_WPS_BULK if self._speaking else 0.0
        else:
            max_wps = CAP_WPS_SPEAK if self._speaking else CAP_WPS_PRE
            rate = _clamp(len(self._queue) / CAP_LAG_S, CAP_WPS_MIN, max_wps)
        self._budget += rate * dt
        while self._budget >= 1.0 and self._queue:
            self._reveal_one(now)
            self._budget -= 1.0

    def _reveal_one(self, now: float) -> None:
        tok = self._queue.pop(0)
        self._words.append(tok)
        self._word_at = now
        self._last_reveal_at = now
        self._energy = min(1.0, self._energy + 0.38)
        self._dirty = True
        # Cadence — the cheapest thing that makes machine text read like speech.
        last = tok[-1:]
        if last in ".!?…":
            # "3.5", "e.g.", "Mr." are not sentence ends.
            if not (len(tok) <= 3 or tok[-2:-1].isdigit() or "." in tok[:-1]):
                self._budget -= CAP_BEAT_SENT
        elif last in ",;:":
            self._budget -= CAP_BEAT_CLAUSE
        self._budget = max(self._budget, CAP_BUDGET_FLOOR)

    def _relayout(self) -> None:
        """Greedy wrap against the FIXED content width. Runs only when a word
        was revealed — never per frame, never per fragment."""
        self._dirty = False
        lines: list[_Line] = []
        cur = ""
        n = 0
        for w in self._words:
            trial = f"{cur} {w}" if cur else w
            if cur and self._advance(trial) > self._content_max:
                lines.append(_Line(cur, self._advance(cur), n))
                cur, n = w, 1
            else:
                cur, n = trial, n + 1
        if cur:
            lines.append(_Line(cur, self._advance(cur), n))

        # Roll-up: drop the leading line permanently. A "keep the last N words"
        # cap would shift the first visible line's wrap points as words fall
        # off, so text already on screen would re-break and jump.
        while len(lines) > 2:
            gone = lines.pop(0)
            del self._words[:gone.n]
            self._out_line = gone.text
            self._roll = 1.0

        self._lines = lines
        if len(lines) >= 2 and not self._promoted:
            # The answer is now known to be long: promote width and height in
            # one step and freeze them for the rest of the episode. Every
            # further 24px step would be pure churn.
            self._promoted = True
            self._tw = self._max_w
            self._th = self._h2
        self._ratchet()

    def _ratchet(self) -> None:
        if self._promoted or not self._lines:
            return
        longest = max(ln.adv for ln in self._lines)
        needed = longest + 2 * CAP_PAD_X + 2 * CAP_SIDE
        if needed > self._tw + CAP_W_DEADBAND:
            steps = math.ceil((needed - CAP_W_MIN) / CAP_W_QUANTUM)
            self._tw = min(self._max_w, CAP_W_MIN + max(0, steps) * CAP_W_QUANTUM)

    def set_style(self, style: str) -> None:
        """"classic" (today's look) or "new" (the redesign). Takes effect on the
        next answer; one already on screen keeps the look it opened with."""
        if style not in theme.STYLES:
            raise ValueError(f"style is one of {', '.join(theme.STYLES)}")
        self._style = style

    @property
    def style(self) -> str:
        return self._style

    def set_screen_width(self, width: float) -> None:
        """Called once per episode by the overlay after the monitor is locked.
        52 characters is a reading-comfort ceiling, not a fraction of the
        monitor: it does not scale up on a 49-inch panel, only down on a
        small laptop screen."""
        raw = CAP_MAX_CHARS * self._avg_adv + 2 * CAP_PAD_X + 2 * CAP_SIDE
        self._max_w = _clamp(raw, 380.0, max(380.0, 0.52 * width))
        self._content_max = self._max_w - 2 * CAP_PAD_X - 2 * CAP_SIDE

    # ── Painting ─────────────────────────────────────────────────────────────

    def _body_path(self, w: float, h: float, r: float) -> QPainterPath:
        """iOS continuous-corner path at the origin, cached. Qt's rounded rect
        is a circular arc and the curvature discontinuity against a capsule
        end is visible at 46px."""
        key = (round(w), round(h), round(r * 2) / 2)
        path = self._path_cache.get(key)
        if path is not None:
            return path
        if len(self._path_cache) > 24:
            self._path_cache.clear()
        path = QPainterPath()
        ext = 1.528665 * r
        if ext > min(w, h) / 2.0:
            # A one-line pill is a true capsule; its ends ARE semicircles.
            path.addRoundedRect(QRectF(0, 0, w, h), r, r)
        else:
            c1, c2, c3, c4, c5, c6 = 1.088198, 0.868407, 0.631098, 0.079584, 0.419, 0.215
            path.moveTo(ext, 0)
            path.lineTo(w - ext, 0)
            path.cubicTo(w - c1 * r, 0, w - c2 * r, 0, w - c3 * r, c4 * r)
            path.cubicTo(w - c5 * r, c6 * r, w - c6 * r, c5 * r, w - c4 * r, c3 * r)
            path.cubicTo(w, c2 * r, w, c1 * r, w, ext)
            path.lineTo(w, h - ext)
            path.cubicTo(w, h - c1 * r, w, h - c2 * r, w - c4 * r, h - c3 * r)
            path.cubicTo(w - c6 * r, h - c5 * r, w - c5 * r, h - c6 * r, w - c3 * r, h - c4 * r)
            path.cubicTo(w - c2 * r, h, w - c1 * r, h, w - ext, h)
            path.lineTo(ext, h)
            path.cubicTo(c1 * r, h, c2 * r, h, c3 * r, h - c4 * r)
            path.cubicTo(c5 * r, h - c6 * r, c6 * r, h - c5 * r, c4 * r, h - c3 * r)
            path.cubicTo(0, h - c2 * r, 0, h - c1 * r, 0, h - ext)
            path.lineTo(0, ext)
            path.cubicTo(0, c1 * r, 0, c2 * r, c4 * r, c3 * r)
            path.cubicTo(c6 * r, c5 * r, c5 * r, c6 * r, c3 * r, c4 * r)
            path.cubicTo(c2 * r, 0, c1 * r, 0, ext, 0)
            path.closeSubpath()
        self._path_cache[key] = path
        return path

    def _draw_bloom(self, p: QPainter, rect: QRectF, r: float, a: float) -> None:
        e = self._energy
        if e <= 0.02:
            return
        p.setPen(Qt.PenStyle.NoPen)
        for inflate, alpha in ((28, 7), (18, 11), (10, 18)):
            c = QColor(CAP_BLUE)
            c.setAlpha(int(alpha * e * a))
            p.setBrush(QBrush(c))
            rr = rect.adjusted(-inflate, -inflate, inflate, inflate)
            p.drawRoundedRect(rr, r + inflate, r + inflate)

    def _draw_shadow(self, p: QPainter, rect: QRectF, r: float, a: float) -> None:
        # A tight contact shadow plus a wide ambient one. One layer reads as a
        # drop shadow from 2009.
        p.setPen(Qt.PenStyle.NoPen)
        for dy, inflate, alpha in ((1, 0, 30), (3, 4, 20), (7, 10, 12), (14, 20, 6)):
            c = QColor(0, 0, 0, int(alpha * a))
            p.setBrush(QBrush(c))
            rr = rect.adjusted(-inflate, -inflate + dy, inflate, inflate + dy)
            p.drawRoundedRect(rr, r + inflate, r + inflate)

    def _draw_body(self, p: QPainter, rect: QRectF, r: float, a: float) -> None:
        w, h = rect.width(), rect.height()
        p.save()
        p.translate(rect.x(), rect.y())

        # Fill: near-black but cool-shifted. Pure black looks like a hole and a
        # flat fill is the number-one tell of "a rounded rectangle with text".
        body = self._body_path(w, h, r)
        g = QLinearGradient(0, 0, 0, h)
        g.setColorAt(0.0, QColor(26, 28, 36, int(240 * a)))
        g.setColorAt(1.0, QColor(9, 10, 14, int(240 * a)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawPath(body)

        # Specular rim: a uniform rim looks like a border, a graded one looks
        # like an edge catching light. Classic look only.
        p.setBrush(Qt.BrushStyle.NoBrush)
        if self._style == theme.STYLE_CLASSIC:
            rim = QLinearGradient(0, 0, 0, h)
            rim.setColorAt(0.00, QColor(255, 255, 255, int(0.17 * 255 * a)))
            rim.setColorAt(0.38, QColor(255, 255, 255, int(0.02 * 255 * a)))
            rim.setColorAt(0.82, QColor(255, 255, 255, int(0.015 * 255 * a)))
            rim.setColorAt(1.00, QColor(255, 255, 255, int(0.055 * 255 * a)))
            p.setPen(QPen(QBrush(rim), 1.0))
            inner = self._body_path(w - 1.0, h - 1.0, max(1.0, r - 0.5))
            p.translate(0.5, 0.5)
            p.drawPath(inner)
            p.translate(-0.5, -0.5)

        # Outer hairline: the only thing that keeps dark glass from dissolving
        # into a white wallpaper.
        p.setPen(QPen(QColor(0, 0, 0, int(150 * a)), 1.0))
        outer = self._body_path(w + 1.0, h + 1.0, r + 0.5)
        p.translate(-0.5, -0.5)
        p.drawPath(outer)
        p.restore()

    def _draw_indicator(self, p: QPainter, rect: QRectF, a: float, now: float) -> None:
        """Four bars driven by word reveals that physically coalesce into one
        breathing orb when the model stalls. Driven by reveals, NOT the mic:
        sig_audio_level is the user's room while Tora is the one talking."""
        e = self._energy
        cy = rect.y() + rect.height() / 2.0
        x0 = rect.x() + CAP_PAD_X
        block_cx = x0 + CAP_IND_W / 2.0
        p.setPen(Qt.PenStyle.NoPen)

        classic = self._style == theme.STYLE_CLASSIC
        if e > 0.02 and classic:
            halo = QColor(CAP_BLUE)
            halo.setAlpha(int(60 * e * a))
            p.setBrush(QBrush(halo))
            p.drawEllipse(QPointF(block_cx, cy), 13.0, 13.0)

        merge = _clamp(1.0 - e / 0.06, 0.0, 1.0)
        fill = QLinearGradient(0, cy - 10, 0, cy + 10)
        fill.setColorAt(0.0, QColor(111, 173, 255, int(235 * a)))
        fill.setColorAt(1.0, QColor(46, 111, 232, int(235 * a)))

        if merge > 0.98:
            # At rest: the same 3.4 rad/s pulse as the cursor island's sparkle
            # (a still dot in the new look).
            rr = 4.0 + 0.55 * math.sin(now * 3.4) if classic else 4.0
            if classic:
                halo = QColor(CAP_BLUE)
                halo.setAlpha(int(0.18 * 255 * a))
                p.setBrush(QBrush(halo))
                p.drawEllipse(QPointF(block_cx, cy), 2.2 * rr, 2.2 * rr)
            p.setBrush(QBrush(fill))
            p.drawEllipse(QPointF(block_cx, cy), rr, rr)
            return

        p.setBrush(QBrush(fill))
        phase = now * 6.0
        bar_w, gap = 3.0, 3.0
        for i in range(4):
            hi = (3.0 + 15.0 * e * CAP_BAR_PROFILE[i]
                  * (0.45 + 0.275 * (math.sin(phase + i * 0.95) + 1.0))
                  + 0.18 * math.sin(phase * 0.37 + i * 2.1))   # slow irrational beat
            bx = x0 + i * (bar_w + gap) + bar_w / 2.0
            bx = _lerp(bx, block_cx, merge)
            bw = _lerp(bar_w, 8.0, merge)
            bh = _lerp(hi, 8.0, merge)
            p.drawRoundedRect(QRectF(bx - bw / 2.0, cy - bh / 2.0, bw, bh),
                              bw / 2.0, bw / 2.0)

    def _draw_text(self, p: QPainter, rect: QRectF, a: float, now: float) -> None:
        ta = self._text_alpha * (1.0 - 0.75 * self._swap)
        if ta < 0.01:
            return
        fm = self._fm
        lh = self._lh
        step = lh + CAP_LINE_GAP
        tx = rect.x() + CAP_PAD_X + CAP_SIDE
        content_w = rect.width() - 2 * CAP_PAD_X - 2 * CAP_SIDE

        # Text is clipped to the pill's inner box so a rolling line vanishes
        # under the rim, not outside it. The clip runs to the right PADDING,
        # not the content box: while the pill is still growing toward a new
        # ratchet target the newest word may sit in the right mirror-spacer,
        # and it must melt into the glass there rather than be sliced.
        clip_right = rect.right() - CAP_PAD_X
        p.setClipRect(
            QRectF(tx - 2.0, rect.y() + CAP_PAD_Y - 1.0,
                   clip_right - tx + 2.0, rect.height() - 2 * CAP_PAD_Y + 2.0),
            Qt.ClipOperation.IntersectClip,
        )
        p.setFont(self._font)

        roll_off = self._roll * step
        live_base = rect.y() + rect.height() - CAP_PAD_Y - fm.descent() + roll_off
        if abs(self._roll) < 0.02:
            live_base = float(round(live_base))   # crisp at rest, smooth mid-roll
        prev_base = live_base - step
        out_base = prev_base - step

        base_rgb = (237, 242, 250)
        col = QColor(*base_rgb)

        if self._out_line and self._roll > 0.02:
            col.setAlpha(int(140 * self._roll * ta))
            p.setPen(col)
            p.drawText(QPointF(tx, out_base), self._out_line)

        lines = self._lines
        if not lines:
            return
        if len(lines) == 2:
            col.setAlpha(int(140 * ta))
            p.setPen(col)
            p.drawText(QPointF(tx, prev_base), lines[0].text)

        live = lines[-1].text
        j = live.rfind(" ")
        prefix, tail = (live[:j], live[j + 1:]) if j >= 0 else ("", live)
        if prefix:
            col.setAlpha(int(255 * ta))
            p.setPen(col)
            p.drawText(QPointF(tx, live_base), prefix)

        # The newest word is drawn on its own so it can fade and rise in at its
        # FINAL x. Text that slides horizontally cannot be fixated on.
        end_x = tx
        if tail:
            u = _clamp((now - self._word_at) / CAP_WORD_REVEAL_S, 0.0, 1.0)
            ease = _smoothstep(u)
            sent = tail[-1:] in ".!?…"
            u3 = _clamp((now - self._word_at) / (CAP_HILITE_SENT_S if sent else CAP_HILITE_S), 0.0, 1.0)
            hil = 0.30 * (1.0 - u3) ** 2
            wx = tx + (self._advance(prefix + " ") if prefix else 0.0)
            wx_r = wx + self._advance(tail)
            end_x = wx_r
            # Melt into the glass over the last 24px before the padding. At
            # rest every word ends >= CAP_SIDE inside that, so this only ever
            # acts during the ~250ms the pill is growing to fit a new word.
            edge = _clamp((clip_right - wx_r) / 24.0, 0.0, 1.0)
            c = QColor(int(_lerp(base_rgb[0], 255, hil)),
                       int(_lerp(base_rgb[1], 255, hil)),
                       int(_lerp(base_rgb[2], 255, hil)),
                       int(255 * ta * ease * edge))
            p.setPen(c)
            p.drawText(QPointF(wx, live_base - 2.5 * (1.0 - ease)), tail)

        # Dead air: a caption that freezes with no explanation reads as a crash.
        if (self._state == "live" and not self._queue and not self._no_more
                and now - self._last_reveal_at > CAP_DEAD_AIR_S):
            p.setPen(Qt.PenStyle.NoPen)
            dy = live_base - fm.xHeight() / 2.0
            for i in range(3):
                pulse = _clamp(0.30 + 0.70 * math.sin(now * 4.2 - i * 0.7), 0.0, 1.0)
                c = QColor(*base_rgb, int(255 * ta * pulse))
                p.setBrush(QBrush(c))
                p.drawEllipse(QPointF(end_x + 8.0 + i * 6.0, dy), 1.1, 1.1)
