"""
In a chess game the move is the engine's, on the position the screen shows.

10 Oct 2026, chess.com, after 1.e4 e5 2.Nf3 d5 3.Nc3 dxe4: asked for the
best move, Tora said "Knight from f3 to e4, to capture the pawn" and drew
it - a square no knight on f3 can reach. A vision model reads a board well
and plays chess badly. So on a game look the model only READS (the `chess`
field, ai/gemini_vision.py): the move list as the page prints it, and the
board as a FEN. Here that becomes an exact position:

  1. the move list, replayed from the start (specialists/chess/engine.py) -
     exact, castling and en passant included; else
  2. the FEN, which is only where the pieces stand.

Either is believed only when the screenshot agrees with it, square by
square (look_board.read_squares): a piece where it says a piece is, of its
colour, and nothing where it says nothing. That also settles which way up
the board is. Then the engine picks the move, says why in a coach's words,
and the arrow goes on the board's own squares.

Anything that does not check out - no board found, a list that does not
replay, a reading the picture contradicts - leaves the look as it was.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

from ai import look_board
from specialists.chess import engine

_log = logging.getLogger("tora.chess")

ENGINE_S = 1.2        # the engine's thinking time: depth 4-5
_MIN_SEEN = 32        # squares the picture must show clearly enough to check
_MARGIN = 6           # the board turned round must be plainly worse

_NUMBER_RE = re.compile(r"^\d+\.+")              # "12." "12..." and the "1." of "1.e4"
_RESULTS = {"1-0", "0-1", "1/2-1/2", "½-½", "*"}


@dataclass
class Played:
    say: str                     # what Tora says: "Knight from f3 to e5: it wins the pawn."
    action: Optional[object]     # the arrow (gemini_vision.Action), None when there is no move
    note: str                    # for the look's line in tora.log
    game: Optional["Game"] = None  # the position it was played from: where the next question starts


@dataclass
class Game:
    """The last position Tora was sure of. The next "and now?" starts here:
    their move and the reply are found from the picture alone (follow)."""
    pos: engine.Position
    white_bottom: bool
    last: Optional[engine.Move] = None      # the move that led to it, as the site highlights it


@dataclass
class Seen:
    """A board in a screenshot, read square by square (look_board.read_squares)."""
    board: object                 # look_board.Board
    squares: list                 # ".", "w", "b" or "?" - row by row from the top left
    lit: set                      # the squares a highlight tints


def moves_of(raw) -> list[str]:
    """A move list as a model writes it - ["1. e4", "e5"], ["e4 e5 Nf3"] -
    as moves alone."""
    out = []
    for item in raw or []:
        for tok in str(item).split():
            tok = _NUMBER_RE.sub("", tok).strip()
            if tok and tok not in _RESULTS:
                out.append(tok)
    return out


def _cells(placement: str) -> list[str]:
    """A FEN's pieces, square by square from a8 to h1 ("." empty)."""
    out = []
    for ch in placement.replace("/", ""):
        out += ["."] * int(ch) if ch.isdigit() else [ch]
    return out


def _placement(cells: list[str]) -> str:
    rows = []
    for r in range(8):
        row, empty = "", 0
        for ch in cells[r * 8:(r + 1) * 8]:
            if ch == ".":
                empty += 1
                continue
            row, empty = row + (str(empty) if empty else ""), 0
            row += ch
        rows.append(row + (str(empty) if empty else ""))
    return "/".join(rows)


def _from_fen(placement: str, white: bool) -> Optional[engine.Position]:
    """A position from where the pieces stand: `white` to move, castling
    only where the king and its rook are still at home, no en passant."""
    try:
        pos = engine.Position.from_fen(f"{placement} w - - 0 1")
    except ValueError:
        return None
    rights = "".join(r for r, k, rook, at in (("K", "e1", "h1", "KR"), ("Q", "e1", "a1", "KR"),
                                              ("k", "e8", "h8", "kr"), ("q", "e8", "a8", "kr"))
                     if pos.piece_at(k) == at[0] and pos.piece_at(rook) == at[1])
    try:
        pos = engine.Position.from_fen(f"{placement} {'w' if white else 'b'} {rights or '-'} - 0 1")
    except ValueError:
        return None
    return None if pos.problems() else pos


def _pattern(pos: engine.Position) -> list[str]:
    """What the picture should show for `pos`, white at the bottom: ".",
    "w" or "b" for each square from a8 to h1."""
    b = pos.board
    return ["." if b[r * 16 + f] == "." else "w" if b[r * 16 + f].isupper() else "b"
            for r in range(7, -1, -1) for f in range(8)]


def _replay(moves: list[str]) -> tuple[engine.Position, Optional[engine.Move]]:
    """The position a move list leads to, and its last move."""
    pos, last = engine.Position.start(), None
    for i, san in enumerate(moves):
        try:
            last = pos.parse_san(san)
        except ValueError as exc:
            raise ValueError(f"move {i + 1} ({san}): {exc}") from None
        pos = pos.play(last)
    return pos, last


def _shown_last(last: Optional[engine.Move], wb: bool, seen: list[str], lit: set) -> bool:
    """A site tints the last move's two squares. A move list a move behind
    the board - read before the reply came, or cut short - is caught here:
    its last move is not where the highlight is."""
    if last is None or not lit:
        return True
    ends = []
    for sq in (last.frm, last.to):
        c, r = look_board.cell(engine.name_of(sq), wb)
        ends.append(r * 8 + c)
    return all(i in lit for i in ends if seen[i] != "?")


def _fits(pos: engine.Position, seen: list[str], max_wrong: int) -> Optional[tuple[bool, int]]:
    """(white at the bottom, squares contradicted) when the picture agrees
    with the position one way up and plainly not the other; else None."""
    expected = _pattern(pos)
    shown = [i for i, s in enumerate(seen) if s != "?"]
    if len(shown) < _MIN_SEEN:
        return None
    wrong = {wb: sum(1 for i in shown if seen[i] != (expected[i] if wb else expected[63 - i]))
             for wb in (True, False)}
    wb = min(wrong, key=wrong.get)
    if wrong[wb] > max_wrong or wrong[not wb] < wrong[wb] + _MARGIN:
        return None
    return wb, wrong[wb]


def _readings(chess: dict):
    """The positions a look's reading can mean, best first: (where it came
    from, the position, its last move or None, the side the reading says is
    at the bottom or None)."""
    moves = moves_of(chess.get("moves"))
    if moves:
        try:
            pos, last = _replay(moves)
            yield "the move list", pos, last, None
        except ValueError as exc:
            _log.info("chess: the move list does not replay (%s)", exc)
    placement = str(chess.get("fen") or "").split(" ")[0]
    if placement.count("/") == 7:
        said = chess.get("white_at_bottom")
        said = said if isinstance(said, bool) else None
        # The side to move is the one at the bottom: the user's, who asked.
        # A model writes some FENs as the board looks, top row first, when
        # black is at the bottom - so that reading is tried turned round too.
        try:
            cells = _cells(placement)
        except ValueError:
            cells = []
        if len(cells) == 64:
            for name, p in (("its reading of the board", placement),
                            ("its reading of the board, turned round", _placement(cells[::-1]))):
                for white in (True, False):
                    pos = _from_fen(p, white)
                    if pos is not None:
                        yield name, pos, None, said


def see(image_b64: str) -> Optional[Seen]:
    """The chess board in the picture, read square by square - None when
    there is plainly no board."""
    rgb = look_board._decode(image_b64) if image_b64 else None
    board = look_board.find_board(rgb) if rgb is not None else None
    if board is None:
        return None
    lit: set = set()
    return Seen(board, look_board.read_squares(rgb, board, lit), lit)


def follow(game: Optional[Game], seen: Seen) -> Optional[tuple[engine.Position, Optional[engine.Move]]]:
    """The game on screen, moved on from `game` by at most two moves - their
    move and the reply - found with no model at all: every legal
    continuation is checked against the picture, square by square, and the
    last move against the site's highlight. None when no position fits, or
    more than one does (then the game is read again)."""
    if game is None:
        return None
    wb = game.white_bottom
    shown = [i for i, s in enumerate(seen.squares) if s != "?"]
    if len(shown) < _MIN_SEEN:
        return None
    want = [seen.squares[i] if wb else seen.squares[63 - i] for i in range(64)]   # a8..h1
    order = [i if wb else 63 - i for i in shown]
    fits: dict = {}

    def check(pos: engine.Position, last: Optional[engine.Move], plies: int) -> None:
        got = _pattern(pos)
        if all(got[i] == want[i] for i in order) and _shown_last(last, wb, seen.squares, seen.lit):
            fits.setdefault(pos.fen().rsplit(" ", 2)[0], (plies, -_lit_ends(last, wb, seen), pos, last))

    check(game.pos, game.last, 0)
    for m in game.pos.legal_moves():
        once = game.pos.play(m)
        check(once, m, 1)
        for reply in once.legal_moves():
            check(once.play(reply), reply, 2)
    if not fits:
        return None
    # The fewest moves that explain the picture, and of those the one the
    # highlight shows: a move from one hidden square to another (a rook
    # under Tora's card) explains it as well as no move at all.
    best = min(f[:2] for f in fits.values())
    left = [f for f in fits.values() if f[:2] == best]
    return (left[0][2], left[0][3]) if len(left) == 1 else None


def _lit_ends(last: Optional[engine.Move], wb: bool, seen: Seen) -> int:
    """How many ends of `last` the picture shows highlighted."""
    if last is None:
        return 0
    n = 0
    for sq in (last.frm, last.to):
        c, r = look_board.cell(engine.name_of(sq), wb)
        n += (r * 8 + c) in seen.lit
    return n


def from_reading(chess: dict, seen: Seen):
    """(position, its last move, white at the bottom, where it came from,
    squares unclear) from a model's reading of the game - the first reading
    the picture bears out; None when none does."""
    for source, pos, last, said_bottom in _readings(chess or {}):
        # A move list is the exact game: one square the picture reads wrong
        # is a misread. Where pieces stand, read by eye, must match them all.
        fit = _fits(pos, seen.squares, 1 if last else 0)
        if fit is None:
            continue
        wb, wrong = fit
        if not _shown_last(last, wb, seen.squares, seen.lit):
            _log.info("chess: the move list ends with %s, not the move the board shows", engine.name_of(last.to))
            continue
        if source.startswith("its reading"):
            if said_bottom is not None and said_bottom != wb:
                continue                          # read upside down
            if pos.white != wb:
                continue                          # the user's side moves
        return pos, last, wb, source, wrong
    moves = moves_of((chess or {}).get("moves"))
    _log.info("chess: no reading matches the board - %d moves read (... %s), fen %s", len(moves),
              " ".join(moves[-6:]), str((chess or {}).get("fen") or "-")[:80])
    return None


def answer(pos: engine.Position, wb: bool, seen: Seen, note: str, last: Optional[engine.Move] = None,
           model_said: str = "") -> Played:
    """The engine's move on `pos`, said and drawn on the board's squares."""
    from ai.gemini_vision import Action
    side = "White" if pos.white else "Black"
    game = Game(pos, wb, last)
    said = look_board.named_move(model_said)
    if all(said):
        legal = any(engine.name_of(m.frm) == said[0] and engine.name_of(m.to) == said[1]
                    for m in pos.legal_moves())
        note += f"; the look said {said[0]}→{said[1]}" + ("" if legal else ", not a legal move")
    _log.info("chess: %s, %s to move, %s at the bottom - %s", pos.fen(), side.lower(),
              "white" if wb else "black", note)
    if pos.white != wb:
        return Played(f"It's {side}'s move now, not yours - ask me again after it.", None,
                      note + f"; {side.lower()} to move", game)
    best = engine.best_move(pos, seconds=ENGINE_S)
    if best is None:
        over = "checkmate" if pos.in_check() else "stalemate"
        return Played(f"There's no move to make - it's {over}.", None, note + f"; {over}", game)
    board = seen.board
    action = Action(label=best.san, box=board.box(*look_board.cell(best.frm, wb)), kind="move",
                    to_box=board.box(*look_board.cell(best.to, wb)), exact=True)
    return Played(best.says, action, note + f"; the engine plays {best.san} (depth {best.depth})", game)


def play(look, image_b64: str) -> Optional[Played]:
    """The engine's move from a look's own reading of the game - the last
    chance when following and reading the game did not work. None keeps
    the look's move."""
    chess = getattr(look, "chess", None) or {}
    seen = see(image_b64) if chess else None
    if seen is None:
        return None
    found = from_reading(chess, seen)
    if found is None:
        return None
    pos, last, wb, source, wrong = found
    note = f"chess: the position from {source}" + (f" ({wrong} square unclear)" if wrong else "")
    return answer(pos, wb, seen, note, last, getattr(look, "answer", ""))


MISSES_KEPT = 10


def keep_miss(folder, image_b64: str, readings: list) -> None:
    """A board no reading matched: the picture and what was read, kept (the
    last few) so the next "it got it wrong" is fixed from what it saw."""
    import base64
    import json
    from pathlib import Path
    try:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        (folder / f"{stamp}.jpg").write_bytes(base64.b64decode(image_b64))
        (folder / f"{stamp}.json").write_text(json.dumps(readings, indent=1, default=str), encoding="utf-8")
        for old in sorted(folder.glob("*.jpg"))[:-MISSES_KEPT]:
            old.unlink(missing_ok=True)
            old.with_suffix(".json").unlink(missing_ok=True)
    except Exception as e:  # noqa: BLE001 - a diagnosis aid, never in the way
        _log.info("chess: could not keep the miss (%s)", e)
