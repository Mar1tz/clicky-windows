"""
Top-of-screen caption island — the words Tora is saying, as it says them.

A black notch that slides down out of the top edge of the monitor the user
is actually looking at, fills with Tora's speech a word at a time, and slides
back up when the answer ends. Modelled on the iPhone's Dynamic Island and the
notch apps for Mac and Windows (NotchNook, Notchify): pure black, flush with
the top of the screen, square where it meets the edge and rounded below.

It is ONE SIZE from the first word to the last. The old pill grew a step at
a time as the answer came in, and the user hated watching it grow; now the
words move and the notch does not. Older lines roll up into the bezel.

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
  3. fixed size              the notch never changes size, so nothing
                             around the words moves
  4. fixed wrap width        text wraps against a width locked for the whole
                             utterance

And one more thing makes the words match the voice. Gemini Live sends an
answer's text and audio together, far faster than Tora speaks it, and says
the turn is complete while most of it is still waiting in the player. So
the live voice's clock (set_voice_clock) decides when each word shows - as
the voice reaches it - and the notch stays until the voice stops. Without a
clock (a walkthrough line in the Windows voice) the words pace themselves.

Motion is one presence value `p` that eases in and out in a set time with no
overshoot: a notch that bounced would come away from the edge it hangs from.
"""

from __future__ import annotations

import math
import re
import time

from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import (
    QPainter, QColor, QBrush, QPainterPath,
    QFont, QFontInfo, QFontMetricsF, QLinearGradient,
)


# ── Tunables ─────────────────────────────────────────────────────────────────
# Duplicated from overlay.py on purpose: this module must not import ui.*
CAP_BLUE = QColor(0x33, 0x80, 0xFF)

# The notch: one size, whatever is said.
CAP_W            = 460.0    # ≈52 characters; past ~55 the return saccade mis-lands
CAP_W_MIN        = 300.0    # on a small screen it takes 60% of the width, not less
CAP_H            = 42.0     # one line; a finished line rolls up into the bezel
CAP_RADIUS       = 18.0     # the bottom corners
CAP_SHOULDER     = 8.0      # the inward curve where it meets the top edge
CAP_PAD_X        = 18.0
CAP_IND_W        = 21.0     # 4 bars × 3px + 3 gaps × 3px
CAP_IND_GAP      = 12.0
CAP_LINE_GAP     = 4.0
CAP_IN_S         = 0.22     # slides down out of the edge
CAP_OUT_S        = 0.20     # and back up into it
CAP_OUT_S_FAST   = 0.12     # cancelled, or the user started talking

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
CAP_PARTIAL_FLUSH_S = 0.45  # a trailing partial word is committed after this silence
CAP_DEAD_AIR_S      = 1.2
CAP_BACKSTOP_S      = 20.0  # a hung provider can never pin the pill open
CAP_LINGER_CANCEL_S = 0.45
CAP_BARGE_CONFIRM_S = 1.5
CAP_VOICE_LEAD_S    = 0.15  # a word shows this much before the voice says it

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
        f.setPointSizeF(11.5)
        f.setWeight(QFont.Weight.Medium)
        if QFontInfo(f).family() != "Segoe UI Variable Display":
            f = QFont("Segoe UI")
            f.setPointSizeF(11.5)
            f.setWeight(QFont.Weight.Medium)
        # The 1.5% tighten is what stops Segoe reading as a system dialog.
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 98.5)
        self._font = f
        # Built against the widget so measured text matches painted text.
        self._fm = QFontMetricsF(f, device) if device is not None else QFontMetricsF(f)
        self._lh = self._fm.height()

        self.needs_screen: bool = False

        # Keeping time with the voice. Tora's live answer arrives as text and
        # audio together, far faster than she speaks it, so the words are
        # revealed by how far the VOICE has got, not by when they arrived.
        self._voice = None              # () -> (seconds heard, seconds received) | None
        self._clock = None              # this tick's reading
        self._chars_in = 0              # characters of this answer received
        self._chars_out = 0             # ... and shown
        self._idle_wait = False         # the answer is over but still being spoken

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
        self._exit_s = CAP_OUT_S
        self._max_w = CAP_W
        self._content_max = CAP_W - 2 * CAP_PAD_X - CAP_IND_W - CAP_IND_GAP

        # Simulation
        self._p = 0.0                   # presence: 0 = up inside the edge, 1 = down
        self._pv = 0.0
        self._w = CAP_W
        self._h = CAP_H
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
            # Caught mid-retraction: keep p where it is so the notch comes
            # back down from there instead of starting inside the edge.
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
        self._chars_in += len(t)

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
            self._idle_wait = False
            self._seal(0.0, CAP_OUT_S_FAST)
            self._state = "exit"
            return
        if name == "IDLE" and self._voice_live():
            # The server has finished sending, but most of the answer is still
            # to be spoken. The notch stays, and this is handled when the
            # voice stops (tick).
            self._idle_wait = True
            return
        if name == "IDLE":
            self._pump_tokens(time.monotonic(), force=True)
            if self._no_more:
                visible = sum(ln.n for ln in self._lines)
                self._seal(_clamp(1.1 + 0.30 * visible, 1.6, 3.4), CAP_OUT_S)
            else:
                # Esc, or an error path falling through its finally: cancelled.
                self._queue.clear()
                self._pending = ""
                self._seal(CAP_LINGER_CANCEL_S, CAP_OUT_S_FAST)

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

    def set_voice_clock(self, clock) -> None:
        """How far Tora's voice has got: a callable returning (seconds heard,
        seconds received) of the answer being spoken, or None when she is not
        speaking (CompanionManager.voice_clock). With it the words keep time
        with the voice; without it they are paced by a words-per-second guess."""
        self._voice = clock

    def _voice_live(self) -> bool:
        if self._voice is None:
            return False
        try:
            return self._voice() is not None
        except Exception:
            return False

    # ── Driven by the overlay ────────────────────────────────────────────────

    def tick(self, now: float, screen: QRectF, ox: float, oy: float) -> None:
        if self._last_t == 0.0:
            self._last_t = now
        dt = min(0.05, max(0.0, now - self._last_t))
        self._last_t = now
        self._now = now
        try:
            self._clock = self._voice() if self._voice is not None else None
        except Exception:
            self._clock = None

        if (self._state not in ("hidden", "exit") and not self._speaking
                and self._clock is None
                and now - self._last_chunk_at > CAP_BACKSTOP_S):
            self._queue.clear()
            self._pending = ""
            self._exit_s = CAP_OUT_S
            self._state = "exit"

        self._pump_tokens(now)
        self._govern(now, dt)
        if self._dirty:
            self._relayout()
        if self._idle_wait and self._clock is None:
            # The voice has finished: now the answer is over.
            self._idle_wait = False
            self.on_state("IDLE")

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
            # Clip to the monitor, so the notch comes out of THAT monitor's top
            # edge and never shows on the display above it.
            p.setClipRect(self._clip)
            rect = self._pill
            a = self._alpha
            self._draw_body(p, rect, a)
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
        self._exit_s = CAP_OUT_S
        self._roll = 0.0
        self._energy = 0.0
        self._barge_ticks = 0
        self._frozen_at = 0.0
        self._last_chunk_at = time.monotonic()
        self._word_at = 0.0
        self._last_reveal_at = 0.0
        self._chars_in = self._chars_out = 0
        self._idle_wait = False
        if fresh:
            self._p = self._pv = 0.0
            self._swap = 0.0
            self.needs_screen = True

    def _seal(self, hold: float, exit_s: float) -> None:
        self._sealed = True
        self._hold = hold
        self._exit_s = exit_s

    # ── Simulation ───────────────────────────────────────────────────────────

    def _substep(self) -> None:
        # In and out in a set time; _geometry eases it. No overshoot: the notch
        # hangs from the edge and must never come away from it.
        if self._state == "exit":
            self._p = max(0.0, self._p - (1.0 / 60.0) / self._exit_s)
        else:
            self._p = min(1.0, self._p + (1.0 / 60.0) / CAP_IN_S)
        self._pv = 0.0
        self._roll += (0.0 - self._roll) * 0.22
        if self._out_line and self._roll < 0.02:
            self._out_line = ""
            self._roll = 0.0
        self._swap += (0.0 - self._swap) * 0.30
        self._energy *= 0.90

    def _geometry(self, screen: QRectF, ox: float, oy: float) -> None:
        # Ease out on the way down (quick, then settles), in on the way up.
        u = self._p
        p = 1.0 - (1.0 - u) ** 3
        w = float(round(self._w))
        h = float(round(self._h))
        cx = screen.x() + screen.width() / 2.0 - ox
        top = screen.y() - oy
        # Flush with the top of the screen at rest. Above that it is clipped,
        # so it comes down out of the edge rather than fading in.
        y = top - h * (1.0 - p)
        x = float(round(cx - w / 2.0))
        self._pill = QRectF(x, float(round(y)), w, h)
        self._clip = QRectF(screen.x() - ox, screen.y() - oy,
                            screen.width(), screen.height())
        # Solid the whole way: it is a thing coming out of the edge, not a
        # ghost fading in. The words fade in over the last part of the move.
        self._alpha = 1.0 if u > 0.0 else 0.0
        self._text_alpha = _clamp((p - 0.45) / 0.55, 0.0, 1.0)

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
            if tok == "-" and (self._queue or self._words):
                tok = "–"              # "Sure - it's": a pause mid-sentence, not a list bullet
            elif not tok or tok in ("-", ">", "•"):
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
        clock = self._clock
        if clock is not None and not self._sealed:
            # Keep time with the voice. Text and audio of an answer arrive
            # together, so the share of the audio heard is the share of the
            # text said: a word shows as the voice reaches it. The voice
            # brings its own pauses, so no cadence beats here.
            heard, received = clock
            if received <= 0.0:
                return
            said = (heard + CAP_VOICE_LEAD_S) / received * self._chars_in
            while self._queue and self._chars_out <= said:
                self._reveal_one(now)
            self._budget = 0.0
            return
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
        self._chars_out += len(tok) + 1
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

        # Roll-up: drop the leading line permanently - it slides up into the
        # bezel. A "keep the last N words" cap would shift the first visible
        # line's wrap points as words fall off, so text on screen would jump.
        while len(lines) > 1:
            gone = lines.pop(0)
            del self._words[:gone.n]
            self._out_line = gone.text
            self._roll = 1.0

        self._lines = lines

    def set_screen_width(self, width: float) -> None:
        """Called once per episode by the overlay after the monitor is locked.
        The notch's width is a reading-comfort ceiling (≈52 characters), not
        a fraction of the monitor: it does not grow on a 49-inch panel, only
        narrows on a small laptop screen. Its size is then fixed until the
        answer is over."""
        self._max_w = min(CAP_W, max(CAP_W_MIN, 0.6 * width))
        self._content_max = self._max_w - 2 * CAP_PAD_X - CAP_IND_W - CAP_IND_GAP
        self._w = self._max_w
        self._h = CAP_H

    # ── Painting ─────────────────────────────────────────────────────────────

    def _body_path(self, w: float, h: float) -> QPainterPath:
        """The notch at the origin, cached: square along the top edge, with a
        small inward curve on each side where it meets the edge (the way a
        MacBook's notch meets the bezel), and rounded bottom corners."""
        key = (round(w), round(h))
        path = self._path_cache.get(key)
        if path is not None:
            return path
        if len(self._path_cache) > 24:
            self._path_cache.clear()
        r, s = min(CAP_RADIUS, h / 2.0), CAP_SHOULDER
        k = 0.4477 * r                     # a quarter circle as one cubic
        path = QPainterPath()
        path.moveTo(-s, 0)
        path.quadTo(0, 0, 0, s)            # left shoulder
        path.lineTo(0, h - r)
        path.cubicTo(0, h - k, k, h, r, h)
        path.lineTo(w - r, h)
        path.cubicTo(w - k, h, w, h - k, w, h - r)
        path.lineTo(w, s)
        path.quadTo(w, 0, w + s, 0)        # right shoulder
        path.closeSubpath()
        self._path_cache[key] = path
        return path

    def _draw_body(self, p: QPainter, rect: QRectF, a: float) -> None:
        # Pure black and nothing else - no rim, no glow, no shadow - so it
        # reads as part of the screen's edge, like the notches it is modelled on.
        p.save()
        p.translate(rect.x(), rect.y())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(0, 0, 0, int(255 * a))))
        p.drawPath(self._body_path(rect.width(), rect.height()))
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

        # No halo: on black the blue bars are enough, the way the Dynamic
        # Island's own activity marks are.
        merge = _clamp(1.0 - e / 0.06, 0.0, 1.0)
        fill = QLinearGradient(0, cy - 10, 0, cy + 10)
        fill.setColorAt(0.0, QColor(111, 173, 255, int(235 * a)))
        fill.setColorAt(1.0, QColor(46, 111, 232, int(235 * a)))

        if merge > 0.98:
            # At rest: one small dot with the same 3.4 rad/s pulse as the
            # cursor card's sparkle.
            rr = 3.5 + 0.45 * math.sin(now * 3.4)
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
        tx = rect.x() + CAP_PAD_X + CAP_IND_W + CAP_IND_GAP

        # Clipped to the notch itself, so a finished line slides up and
        # disappears into the top edge. A little room on the right for
        # punctuation that overhangs the wrap width.
        clip_right = rect.right() - CAP_PAD_X + CAP_PUNCT_OVERHANG / 2.0
        p.setClipRect(
            QRectF(tx - 2.0, rect.y(), clip_right - tx + 2.0, rect.height()),
            Qt.ClipOperation.IntersectClip,
        )
        p.setFont(self._font)

        # The line sits in the middle of the notch. When it is full it slides
        # up into the edge and the next one comes up from below.
        roll_off = self._roll * step
        live_base = rect.center().y() + (fm.ascent() - fm.descent()) / 2.0 + roll_off
        if abs(self._roll) < 0.02:
            live_base = float(round(live_base))   # crisp at rest, smooth mid-roll
        out_base = live_base - step

        base_rgb = (255, 255, 255)
        col = QColor(*base_rgb)

        if self._out_line and self._roll > 0.02:
            col.setAlpha(int(140 * self._roll * ta))
            p.setPen(col)
            p.drawText(QPointF(tx, out_base), self._out_line)

        lines = self._lines
        if not lines:
            return

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
            wx = tx + (self._advance(prefix + " ") if prefix else 0.0)
            end_x = wx + self._advance(tail)
            # The words are already white; the newest one is marked by its
            # rise and fade-in alone.
            c = QColor(*base_rgb, int(255 * ta * ease))
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
