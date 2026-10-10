"""
A move on a board is drawn between the squares Tora names.

On 10 Oct 2026, a user's chess game: Tora said "knight from g1 to f3" four
times running - the user even told her "you pointed an arrow from G1 to
G3" - and every arrow ended on g3. The words were right and the boxes were
not: a vision model reads a position well, but puts its box on an empty
square a file or a rank off, on the fast model and the deep one alike.

So the board is found in the screenshot itself. It is a checker of 8 x 8
squares, and the borders between them are long straight edges at an even
spacing, rising and falling in turn down each one - nothing else on a
screen looks like that. The arrow then goes from the centre of the square
the answer names to the centre of the square it names. The model's boxes
only decide which way up the board is (white at the bottom or black), and
an end the answer does not name goes on the square under the model's box.

Numpy on the picture the look already took: tens of milliseconds, no call.
Anything that is not plainly a board leaves the model's boxes as they were.
"""

from __future__ import annotations

import base64
import io
import logging
import math
import re
from dataclasses import dataclass
from typing import Optional

_log = logging.getLogger("tora.board")

FILES = "abcdefgh"
_SQ = r"([a-h][1-8])"
# "g1 to f3", "g1-f3", "e4xd5", "g1f3"
_PAIR_RE = re.compile(rf"\b{_SQ}\s*(?:-|–|—|→|->|x|to|takes(?:\s+on)?|captures(?:\s+on)?)\s*{_SQ}\b",
                      re.IGNORECASE)
_UCI_RE = re.compile(rf"\b{_SQ}{_SQ}\b", re.IGNORECASE)
# "from g1"; else "the knight on g1" - unless that is where it goes ("takes
# the pawn on e5")
_FROM_RE = re.compile(rf"\bfrom\s+(?:the\s+)?{_SQ}\b", re.IGNORECASE)
_STANDS_RE = re.compile(rf"\b(?:knight|bishop|rook|queen|king|pawn)\s+(?:on|at)\s+{_SQ}\b", re.IGNORECASE)
# "to f3", "takes on e5", "takes the pawn on e5" - never a bare "on", which
# is as often where the piece stands
_TO_RE = re.compile(rf"\b(?:to|onto|takes?|captures?)\s+(?:on\s+|the\s+\w+\s+(?:on|at)\s+)?{_SQ}\b",
                    re.IGNORECASE)
# "Nf3", "Bxc4", "exd5" - case matters: N is a knight, b is a file
_SAN_RE = re.compile(r"\b(?:[KQRBN][a-h]?[1-8]?x?|[a-h]x)([a-h][1-8])\b")

_INSET = 0.09         # where a square's own colour is sampled, from each corner
_MIN_SQUARE = 10      # px; a board smaller than 80 px is not drawn on
_RIGHT_SQUARES = 52   # of 64 - a highlight, a coordinate or a card over a corner is fine


@dataclass
class Board:
    """Where the board is in the picture, in its pixels."""
    left: float
    top: float
    size: float        # one square
    width: int         # the picture
    height: int

    def centre(self, col: int, row: int) -> tuple[float, float]:
        return self.left + (col + 0.5) * self.size, self.top + (row + 0.5) * self.size

    def cell_near(self, x: float, y: float) -> Optional[tuple[int, int]]:
        """The square under (x, y) - or the edge square, half a square out."""
        c = math.floor((x - self.left) / self.size)
        r = math.floor((y - self.top) / self.size)
        if not (-1 <= c <= 8 and -1 <= r <= 8):
            return None
        if (x - self.left) / self.size < -0.5 or (x - self.left) / self.size > 8.5:
            return None
        if (y - self.top) / self.size < -0.5 or (y - self.top) / self.size > 8.5:
            return None
        return min(7, max(0, c)), min(7, max(0, r))

    def box(self, col: int, row: int) -> tuple[int, int, int, int]:
        """The square as a look's box: [ymin, xmin, ymax, xmax], 0-1000."""
        x0, y0 = self.left + col * self.size, self.top + row * self.size
        x1, y1 = x0 + self.size, y0 + self.size
        return (round(y0 * 1000 / self.height), round(x0 * 1000 / self.width),
                round(y1 * 1000 / self.height), round(x1 * 1000 / self.width))


def cell(name: str, white_bottom: bool) -> tuple[int, int]:
    """(col, row) from the top left, of a square named like "g1"."""
    f, r = FILES.index(name[0].lower()), int(name[1]) - 1
    return (f, 7 - r) if white_bottom else (7 - f, r)


def name_of(col: int, row: int, white_bottom: bool) -> str:
    return (FILES[col] + str(8 - row)) if white_bottom else (FILES[7 - col] + str(row + 1))


def named_move(*texts: str) -> tuple[Optional[str], Optional[str]]:
    """(from, to): the squares a sentence names - "knight from g1 to f3",
    "Nf3", "takes on e5". Either can be None. The first text that names a
    whole move wins; otherwise each end comes from the first that names it."""
    for text in texts:
        m = _PAIR_RE.search(text or "") or _UCI_RE.search(text or "")
        if m and m.group(1).lower() != m.group(2).lower():
            return m.group(1).lower(), m.group(2).lower()
    texts = tuple(t for t in texts if t)
    dst = next((m.group(1).lower() for t in texts for m in [_TO_RE.search(t) or _SAN_RE.search(t)] if m), None)
    src = next((m.group(1).lower() for t in texts for m in [_FROM_RE.search(t)] if m), None)
    src = src or next((m.group(1).lower() for t in texts for m in _STANDS_RE.finditer(t)
                       if m.group(1).lower() != dst), None)
    return src, (dst if dst != src else None)


# ── Finding the board ────────────────────────────────────────────────────────

def _decode(image_b64: str):
    try:
        import numpy as np
        from PIL import Image
        img = Image.open(io.BytesIO(base64.b64decode(image_b64))).convert("RGB")
        return np.asarray(img, dtype=np.int32)
    except Exception:  # noqa: BLE001 - not a picture: nothing to find
        return None


def _profile(diff, axis: int):
    """How much each line between two pixels looks like a border between
    squares: edges that rise AND fall along it (a checker flips at every
    square), so a window's one-sided edge scores little. A pixel's slack
    either way."""
    import numpy as np
    pos = np.clip(diff, 0, None).sum(axis=axis, dtype=np.float64)
    neg = np.clip(-diff, 0, None).sum(axis=axis, dtype=np.float64)
    p = np.sqrt(pos * neg)
    q = p.copy()
    q[1:] = np.maximum(q[1:], p[:-1])
    q[:-1] = np.maximum(q[:-1], p[1:])
    return q


def _lattices(p, s_lo: float, s_hi: float, keep: int = 3, step: float = 0.5) -> list[tuple[float, int, float]]:
    """The best (score, start, square) for 7 evenly spaced inner borders in
    profile p - p[i] is the border before pixel i + 1. A lattice whose few
    lines sit on one huge edge does not win: each line counts at most twice
    the median of its seven."""
    import numpy as np
    n = len(p)
    k = np.arange(1, 8)
    found: list[tuple[float, int, float]] = []
    s = s_lo
    while s <= s_hi:
        last = int(n + 1 - 8 * s)
        if last < 0:
            break
        o = np.arange(0, last + 1)
        vals = p[np.rint(o[:, None] + k[None, :] * s).astype(int) - 1]
        cap = 2 * np.partition(vals, 3, axis=1)[:, 3:4]      # the median of seven
        score = np.minimum(vals, cap).sum(axis=1)
        i = int(np.argmax(score))
        found.append((float(score[i]), int(o[i]), float(s)))
        s += step
    found.sort(reverse=True)
    best: list[tuple[float, int, float]] = []
    for cand in found:
        if all(abs(cand[1] - b[1]) > b[2] / 4 or abs(cand[2] - b[2]) > 1.0 for b in best):
            best.append(cand)
        if len(best) >= keep:
            break
    return best


def _square_colours(rgb, left: float, top: float, s: float):
    """Each square's own colour: the median of its four corners, where no
    piece reaches - a coordinate printed in one corner does not move it."""
    import numpy as np
    h, w = rgb.shape[:2]
    inset, half = max(2.0, _INSET * s), max(1, int(0.04 * s))
    out = np.zeros((8, 8, 3))
    for r in range(8):
        for c in range(8):
            x0, y0 = left + c * s, top + r * s
            samples = []
            for fx, fy in ((inset, inset), (s - inset, inset), (inset, s - inset), (s - inset, s - inset)):
                x, y = min(w - 1, max(0, int(x0 + fx))), min(h - 1, max(0, int(y0 + fy)))
                patch = rgb[max(0, y - half):y + half + 1, max(0, x - half):x + half + 1].reshape(-1, 3)
                samples.append(np.median(patch, axis=0))
            out[r, c] = np.median(samples, axis=0)
    return out


def _is_checker(colours) -> bool:
    import numpy as np
    odd = (np.add.outer(np.arange(8), np.arange(8)) % 2).astype(bool)
    a, b = np.median(colours[~odd], axis=0), np.median(colours[odd], axis=0)
    if np.abs(a - b).sum() < 45:
        return False
    da, db = np.abs(colours - a).sum(-1), np.abs(colours - b).sum(-1)
    return int(np.where(odd, db < da, da < db).sum()) >= _RIGHT_SQUARES


def find_board(rgb) -> Optional[Board]:
    """The 8 x 8 board in the picture, or None when there is not plainly one."""
    import numpy as np
    h, w = rgb.shape[:2]
    if min(h, w) < 8 * _MIN_SQUARE:
        return None
    g = rgb.sum(axis=2)
    dx = np.diff(g, axis=1)
    s_hi = min(h, w) / 8
    for _score, x0, s in _lattices(_profile(dx, 0), _MIN_SQUARE, s_hi):
        # Across the board's own columns only, then back across its rows:
        # each pass sees less of the page around it.
        cols = slice(x0, int(x0 + 8 * s))
        ys = _lattices(_profile(np.diff(g[:, cols], axis=0), 1), s * 0.97, s * 1.03, keep=1, step=0.1)
        if not ys:
            continue
        _sy, y0, s_y = ys[0]
        rows = slice(y0, int(y0 + 8 * s_y))
        xs = _lattices(_profile(dx[rows, :], 0), s * 0.97, s * 1.03, keep=1, step=0.1)
        if xs:
            _sx, x0, s = xs[0]
        size = (s + s_y) / 2
        if x0 + 8 * size > w + 1 or y0 + 8 * size > h + 1:
            continue
        if _is_checker(_square_colours(rgb, x0, y0, size)):
            return Board(float(x0), float(y0), size, w, h)
    return None


def _holds_piece(rgb, board: Board, col: int, row: int) -> bool:
    """Something stands on the square: its middle is not its corners' colour."""
    import numpy as np
    s = board.size
    colour = _square_colours(rgb, board.left + col * s, board.top + row * s, s)[0, 0]
    x, y = board.centre(col, row)
    half = max(1, int(0.15 * s))
    mid = rgb[int(y) - half:int(y) + half + 1, int(x) - half:int(x) + half + 1].reshape(-1, 3)
    return float(np.abs(mid - colour).sum(axis=1).mean()) > 60


def _white_bottom(rgb, board: Board, ends: list[tuple[str, tuple[float, float], bool]]) -> bool:
    """Which way up: the way the named squares land nearest the model's
    boxes. A board turned round puts every square at least a square and a
    half from where it was; a piece the model says moves must stand on its
    square, and that settles a near thing."""
    cost = {}
    for wb in (True, False):
        total = 0.0
        for name, (x, y), moves_from in ends:
            c, r = cell(name, wb)
            cx, cy = board.centre(c, r)
            total += math.hypot(cx - x, cy - y) / board.size
            if moves_from and not _holds_piece(rgb, board, c, r):
                total += 2.0
        cost[wb] = total
    return cost[True] <= cost[False]


def place_moves(actions: list, image_b64: str, answer: str = "") -> str:
    """Put every move in `actions` on the board's squares, in place. Returns
    what it did, for the look's line in tora.log ("" when nothing changed).

    The boxes are in the picture's 0-1000, as the look returned them. The
    spoken answer names the move when there is one move; each move's label
    can name its own."""
    moves = [a for a in actions if getattr(a, "is_move", False)]
    if not moves or not image_b64:
        return ""
    rgb = _decode(image_b64)
    if rgb is None:
        return ""
    board = find_board(rgb)
    if board is None:
        return ""
    h, w = rgb.shape[:2]
    notes = []
    for a in moves:
        start = (a.center[0] * w / 1000, a.center[1] * h / 1000)
        end = (a.to_center[0] * w / 1000, a.to_center[1] * h / 1000)
        near_from, near_to = board.cell_near(*start), board.cell_near(*end)
        if near_from is None and near_to is None:
            continue                            # the model meant something else on the screen
        src, dst = named_move(*([answer] if len(moves) == 1 else []), a.label)
        ends = [(n, p, f) for n, p, f in ((src, start, True), (dst, end, False)) if n]
        wb = _white_bottom(rgb, board, ends) if ends else True
        fc = cell(src, wb) if src else near_from
        tc = cell(dst, wb) if dst else near_to
        if fc is None or tc is None or fc == tc:
            continue
        a.box, a.to_box = board.box(*fc), board.box(*tc)
        a.exact = True
        if not ends:
            notes.append("a move no square was named for, put on the squares under it")
            continue
        said = f"{name_of(*fc, wb)}→{name_of(*tc, wb)}"
        drawn = (f"{name_of(*near_from, wb) if near_from else '?'}→"
                 f"{name_of(*near_to, wb) if near_to else '?'}")
        notes.append(said if drawn == said else f"{said} (its boxes said {drawn})")
        _log.info("board: %.0f px squares at (%.0f,%.0f), %s at the bottom - %s", board.size, board.left,
                  board.top, "white" if wb else "black", notes[-1])
    return "on the board " + ", ".join(notes) if notes else ""
