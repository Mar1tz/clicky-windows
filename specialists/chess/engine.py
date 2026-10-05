"""A small chess engine: the rules, a search, and the move said in words.

Why it is here (docs/SHOW_THE_NEXT_MOVE.md, SN-06): "what's my best move?"
was answered by a vision model's opinion of a screenshot - slow, paid, and
not a chess player. A position read exactly (SN-05) comes here instead:
legal moves by the rules, an alpha-beta search for about a second, and the
move said the way a coach would - "Knight from b1 to c3: it develops a
piece." Pure Python, written here: no dependency and no licence question
(the python-chess library and Stockfish are GPL). It plays a solid club
game, which is what the question needs; it is not Stockfish.

The board is 0x88: square = rank * 16 + file, and `square & 0x88` is
non-zero exactly when a step has left the board. Positions are immutable -
play() returns a new one - which keeps the search simple and the rules
easy to check (the perft counts in the tests are the standard ones).

    pos = Position.from_fen(fen)          # or Position.start()
    pos = pos.play_san("e4")              # a move list replays into a position
    best = best_move(pos, seconds=0.8)    # Best(san="Nf3", says="Knight from g1 to f3: ...")
"""
from __future__ import annotations

import re
import time
from typing import NamedTuple, Optional

FILES = "abcdefgh"
START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
VALUE = {"p": 100, "n": 320, "b": 330, "r": 500, "q": 900, "k": 0}
NAMES = {"p": "pawn", "n": "knight", "b": "bishop", "r": "rook", "q": "queen", "k": "king"}
MATE = 100_000

KNIGHT = (33, 31, 18, 14, -33, -31, -18, -14)
KING = (16, -16, 1, -1, 17, 15, -17, -15)
BISHOP = (17, 15, -17, -15)
ROOK = (16, -16, 1, -1)
SQUARES = tuple(r * 16 + f for r in range(8) for f in range(8))

# Chess sites write moves with piece symbols too (chess.com's move list).
FIGURINES = str.maketrans({"♔": "K", "♕": "Q", "♖": "R", "♗": "B", "♘": "N", "♙": "",
                           "♚": "K", "♛": "Q", "♜": "R", "♝": "B", "♞": "N", "♟": ""})
_SAN = re.compile(r"^([NBRQK])?([a-h])?([1-8])?x?([a-h][1-8])=?([NBRQ])?$")


def square(name: str) -> int:
    name = name.strip().lower()
    if len(name) != 2 or name[0] not in FILES or name[1] not in "12345678":
        raise ValueError(f"{name!r} is not a square")
    return (int(name[1]) - 1) * 16 + FILES.index(name[0])


def name_of(sq: int) -> str:
    return FILES[sq & 7] + str((sq >> 4) + 1)


class Move(NamedTuple):
    frm: int
    to: int
    promo: str = ""          # "q" "r" "b" "n"
    flag: str = ""           # "" | "double" | "ep" | "castle"

    @property
    def uci(self) -> str:
        return name_of(self.frm) + name_of(self.to) + self.promo


def _own(white: bool):
    return str.isupper if white else str.islower


class Position:
    """A position. Immutable: play() returns the next one."""

    __slots__ = ("board", "white", "castling", "ep", "halfmove", "fullmove", "kings")

    def __init__(self, board: list, white: bool, castling: str, ep: int, halfmove: int, fullmove: int,
                 kings: Optional[tuple] = None):
        self.board = board
        self.white = white
        self.castling = castling
        self.ep = ep
        self.halfmove = halfmove
        self.fullmove = fullmove
        self.kings = kings or (board.index("K") if "K" in board else -1,
                               board.index("k") if "k" in board else -1)

    # -- reading and writing -------------------------------------------------------------------------------

    @classmethod
    def start(cls) -> "Position":
        return cls.from_fen(START_FEN)

    @classmethod
    def from_fen(cls, fen: str) -> "Position":
        parts = (fen or "").split()
        if len(parts) < 2:
            raise ValueError(f"{fen!r} is not a FEN")
        rows = parts[0].split("/")
        if len(rows) != 8:
            raise ValueError("a FEN board has 8 ranks")
        board = ["."] * 128
        for i, row in enumerate(rows):
            rank, f = 7 - i, 0
            for ch in row:
                if ch.isdigit():
                    f += int(ch)
                elif ch in "pnbrqkPNBRQK" and f < 8:
                    board[rank * 16 + f] = ch
                    f += 1
                else:
                    raise ValueError(f"bad rank {row!r} in the FEN")
            if f != 8:
                raise ValueError(f"rank {row!r} is not 8 squares")
        if parts[1] not in ("w", "b"):
            raise ValueError("the side to move is w or b")
        castling = "" if len(parts) < 3 or parts[2] == "-" else parts[2]
        ep = -1 if len(parts) < 4 or parts[3] == "-" else square(parts[3])
        half = int(parts[4]) if len(parts) > 4 else 0
        full = int(parts[5]) if len(parts) > 5 else 1
        return cls(board, parts[1] == "w", castling, ep, half, full)

    def fen(self) -> str:
        rows = []
        for rank in range(7, -1, -1):
            row, empty = "", 0
            for f in range(8):
                p = self.board[rank * 16 + f]
                if p == ".":
                    empty += 1
                    continue
                if empty:
                    row, empty = row + str(empty), 0
                row += p
            rows.append(row + (str(empty) if empty else ""))
        return " ".join(["/".join(rows), "w" if self.white else "b", self.castling or "-",
                         name_of(self.ep) if self.ep >= 0 else "-", str(self.halfmove), str(self.fullmove)])

    def __repr__(self) -> str:
        return f"Position({self.fen()!r})"

    def piece_at(self, name: str) -> str:
        return self.board[square(name)]

    def problems(self) -> list[str]:
        """Why this cannot be a real position - for a board read from pixels
        (SN-07), where a misread square must be caught, not played from."""
        b = [p for p in (self.board[s] for s in SQUARES) if p != "."]
        out = []
        for side, k, pawn in (("White", "K", "P"), ("Black", "k", "p")):
            if b.count(k) != 1:
                out.append(f"{side} has {b.count(k)} kings")
            if b.count(pawn) > 8:
                out.append(f"{side} has {b.count(pawn)} pawns")
            if sum(1 for p in b if p.isupper() == (side == "White")) > 16:
                out.append(f"{side} has more than 16 pieces")
        if any(self.board[r * 16 + f] in "Pp" for r in (0, 7) for f in range(8)):
            out.append("a pawn is on the first or last rank")
        if not out and self.attacked(self.kings[0 if not self.white else 1], self.white):
            out.append("the side not to move is in check")
        return out

    # -- the rules -----------------------------------------------------------------------------------------

    def attacked(self, sq: int, by_white: bool) -> bool:
        """Is `sq` attacked by the white (True) or black pieces?"""
        if sq < 0:
            return False
        b = self.board
        pawn, knight, bishop, rook, queen, king = ("PNBRQK" if by_white else "pnbrqk")
        for d in ((-15, -17) if by_white else (15, 17)):
            t = sq + d
            if not t & 0x88 and b[t] == pawn:
                return True
        for d in KNIGHT:
            t = sq + d
            if not t & 0x88 and b[t] == knight:
                return True
        for d in KING:
            t = sq + d
            if not t & 0x88 and b[t] == king:
                return True
        for dirs, a, c in ((BISHOP, bishop, queen), (ROOK, rook, queen)):
            for d in dirs:
                t = sq + d
                while not t & 0x88:
                    p = b[t]
                    if p != ".":
                        if p == a or p == c:
                            return True
                        break
                    t += d
        return False

    def in_check(self) -> bool:
        return self.attacked(self.kings[0 if self.white else 1], not self.white)

    def pseudo_moves(self) -> list[Move]:
        """Every move by the rules of how pieces move, before "does it leave
        my own king in check" (legal_moves, and the search, check that)."""
        b, white = self.board, self.white
        own = _own(white)
        out: list[Move] = []
        fwd, start, last = (16, 1, 7) if white else (-16, 6, 0)
        for s in SQUARES:
            p = b[s]
            if p == "." or not own(p):
                continue
            t = p.lower()
            if t == "p":
                to = s + fwd
                if not to & 0x88 and b[to] == ".":
                    if to >> 4 == last:
                        out += [Move(s, to, pr) for pr in "qrbn"]
                    else:
                        out.append(Move(s, to))
                        if s >> 4 == start and b[to + fwd] == ".":
                            out.append(Move(s, to + fwd, "", "double"))
                for d in ((15, 17) if white else (-15, -17)):
                    to = s + d
                    if to & 0x88:
                        continue
                    q = b[to]
                    if q != "." and not own(q):
                        if to >> 4 == last:
                            out += [Move(s, to, pr) for pr in "qrbn"]
                        else:
                            out.append(Move(s, to))
                    elif to == self.ep:
                        out.append(Move(s, to, "", "ep"))
            elif t in "nk":
                for d in (KNIGHT if t == "n" else KING):
                    to = s + d
                    if not to & 0x88 and (b[to] == "." or not own(b[to])):
                        out.append(Move(s, to))
                if t == "k":
                    out += self._castles(s)
            else:
                dirs = BISHOP if t == "b" else ROOK if t == "r" else BISHOP + ROOK
                for d in dirs:
                    to = s + d
                    while not to & 0x88:
                        q = b[to]
                        if q == ".":
                            out.append(Move(s, to))
                        else:
                            if not own(q):
                                out.append(Move(s, to))
                            break
                        to += d
        return out

    def _castles(self, s: int) -> list[Move]:
        b, white = self.board, self.white
        home, (kside, qside), rook = (4, "KQ", "R") if white else (116, "kq", "r")
        if s != home or self.attacked(s, not white):
            return []
        out = []
        if kside in self.castling and b[s + 3] == rook and b[s + 1] == b[s + 2] == "." \
                and not self.attacked(s + 1, not white) and not self.attacked(s + 2, not white):
            out.append(Move(s, s + 2, "", "castle"))
        if qside in self.castling and b[s - 4] == rook and b[s - 1] == b[s - 2] == b[s - 3] == "." \
                and not self.attacked(s - 1, not white) and not self.attacked(s - 2, not white):
            out.append(Move(s, s - 2, "", "castle"))
        return out

    def play(self, m: Move) -> "Position":
        b = self.board[:]
        p = b[m.frm]
        captured = b[m.to]
        b[m.to] = (m.promo.upper() if self.white else m.promo) if m.promo else p
        b[m.frm] = "."
        if m.flag == "ep":
            b[m.to - 16 if self.white else m.to + 16] = "."
            captured = "p"
        elif m.flag == "castle":
            rook_from, rook_to = (m.frm + 3, m.frm + 1) if m.to > m.frm else (m.frm - 4, m.frm - 1)
            b[rook_to], b[rook_from] = b[rook_from], "."
        rights = self.castling
        if rights:
            for s, lost in ((4, "KQ"), (0, "Q"), (7, "K"), (116, "kq"), (112, "q"), (119, "k")):
                if m.frm == s or m.to == s:
                    rights = "".join(c for c in rights if c not in lost)
        kings = self.kings
        if p in "Kk":
            kings = (m.to, kings[1]) if p == "K" else (kings[0], m.to)
        return Position(b, not self.white, rights,
                        m.frm + (16 if self.white else -16) if m.flag == "double" else -1,
                        0 if p in "Pp" or captured != "." else self.halfmove + 1,
                        self.fullmove + (0 if self.white else 1), kings)

    def is_legal_after(self, nxt: "Position") -> bool:
        """After a move of ours: is our own king safe?"""
        return not nxt.attacked(nxt.kings[0 if self.white else 1], nxt.white)

    def legal_moves(self) -> list[Move]:
        return [m for m in self.pseudo_moves() if self.is_legal_after(self.play(m))]

    # -- notation -------------------------------------------------------------------------------------------

    def san(self, m: Move, legal: Optional[list[Move]] = None) -> str:
        """Standard algebraic notation: Nf3, exd5, O-O, e8=Q+, Rad1#."""
        b = self.board
        if m.flag == "castle":
            s = "O-O" if m.to > m.frm else "O-O-O"
        else:
            p = b[m.frm]
            capture = b[m.to] != "." or m.flag == "ep"
            if p in "Pp":
                s = (FILES[m.frm & 7] + "x" if capture else "") + name_of(m.to)
                if m.promo:
                    s += "=" + m.promo.upper()
            else:
                rivals = [o for o in (legal if legal is not None else self.legal_moves())
                          if o.to == m.to and o.frm != m.frm and b[o.frm] == p]
                dis = ""
                if rivals:
                    if all((o.frm & 7) != (m.frm & 7) for o in rivals):
                        dis = FILES[m.frm & 7]
                    elif all((o.frm >> 4) != (m.frm >> 4) for o in rivals):
                        dis = str((m.frm >> 4) + 1)
                    else:
                        dis = name_of(m.frm)
                s = p.upper() + dis + ("x" if capture else "") + name_of(m.to)
        nxt = self.play(m)
        if nxt.in_check():
            s += "#" if not nxt.legal_moves() else "+"
        return s

    def parse_san(self, text: str) -> Move:
        """A move as written - "Nf3", "exd5", "O-O", "e8=Q", "♘f3", "Nbd7+",
        "0-0", "e4!?" - to the one legal move it names."""
        t = (text or "").translate(FIGURINES).strip()
        t = re.sub(r"[+#!?]|e\.p\.|\s", "", t).replace("0", "O")
        legal = self.legal_moves()
        if t in ("O-O", "O-O-O"):
            want = 2 if t == "O-O" else -2
            for m in legal:
                if m.flag == "castle" and m.to - m.frm == want:
                    return m
            raise ValueError(f"{text} is not legal here")
        mt = _SAN.match(t)
        if not mt:
            raise ValueError(f"{text!r} is not a move")
        piece, ffile, frank, to, promo = mt.groups()
        to_sq = square(to)
        hits = []
        for m in legal:
            p = self.board[m.frm]
            if m.to != to_sq:
                continue
            if piece:
                if p.upper() != piece:
                    continue
            elif p not in "Pp":
                continue
            if ffile and FILES[m.frm & 7] != ffile:
                continue
            if frank and str((m.frm >> 4) + 1) != frank:
                continue
            if m.promo and (promo or "Q").lower() != m.promo:
                continue
            hits.append(m)
        if len(hits) != 1:
            raise ValueError(f"{text} is {'ambiguous' if hits else 'not legal'} here")
        return hits[0]

    def play_san(self, text: str) -> "Position":
        return self.play(self.parse_san(text))

    @classmethod
    def from_moves(cls, moves: list[str], fen: str = START_FEN) -> "Position":
        """A move list, replayed: the exact position a site's game is in."""
        pos = cls.from_fen(fen)
        for i, san in enumerate(moves):
            try:
                pos = pos.play_san(san)
            except ValueError as exc:
                raise ValueError(f"move {i + 1} ({san}): {exc}") from None
        return pos


# -- judging a position ----------------------------------------------------------------------------------------

def _table(rows: str) -> tuple[int, ...]:
    return tuple(int(x) for x in rows.split())


# The "simplified evaluation function" tables (T. Michniewski), rank 8 first,
# from White's side. Small, well known, and enough for solid moves.
PST = {
    "p": _table("""0 0 0 0 0 0 0 0  50 50 50 50 50 50 50 50  10 10 20 30 30 20 10 10  5 5 10 25 25 10 5 5
                   0 0 0 20 20 0 0 0  5 -5 -10 0 0 -10 -5 5  5 10 10 -20 -20 10 10 5  0 0 0 0 0 0 0 0"""),
    "n": _table("""-50 -40 -30 -30 -30 -30 -40 -50  -40 -20 0 0 0 0 -20 -40  -30 0 10 15 15 10 0 -30
                   -30 5 15 20 20 15 5 -30  -30 0 15 20 20 15 0 -30  -30 5 10 15 15 10 5 -30
                   -40 -20 0 5 5 0 -20 -40  -50 -40 -30 -30 -30 -30 -40 -50"""),
    "b": _table("""-20 -10 -10 -10 -10 -10 -10 -20  -10 0 0 0 0 0 0 -10  -10 0 5 10 10 5 0 -10
                   -10 5 5 10 10 5 5 -10  -10 0 10 10 10 10 0 -10  -10 10 10 10 10 10 10 -10
                   -10 5 0 0 0 0 5 -10  -20 -10 -10 -10 -10 -10 -10 -20"""),
    "r": _table("""0 0 0 0 0 0 0 0  5 10 10 10 10 10 10 5  -5 0 0 0 0 0 0 -5  -5 0 0 0 0 0 0 -5
                   -5 0 0 0 0 0 0 -5  -5 0 0 0 0 0 0 -5  -5 0 0 0 0 0 0 -5  0 0 0 5 5 0 0 0"""),
    "q": _table("""-20 -10 -10 -5 -5 -10 -10 -20  -10 0 0 0 0 0 0 -10  -10 0 5 5 5 5 0 -10
                   -5 0 5 5 5 5 0 -5  0 0 5 5 5 5 0 -5  -10 5 5 5 5 5 0 -10  -10 0 5 0 0 0 0 -10
                   -20 -10 -10 -5 -5 -10 -10 -20"""),
    "k": _table("""-30 -40 -40 -50 -50 -40 -40 -30  -30 -40 -40 -50 -50 -40 -40 -30  -30 -40 -40 -50 -50 -40 -40 -30
                   -30 -40 -40 -50 -50 -40 -40 -30  -20 -30 -30 -40 -40 -30 -30 -20  -10 -20 -20 -20 -20 -20 -20 -10
                   20 20 0 0 0 0 20 20  20 30 10 0 0 10 30 20"""),
    "k_end": _table("""-50 -40 -30 -20 -20 -30 -40 -50  -30 -20 -10 0 0 -10 -20 -30  -30 -10 20 30 30 20 -10 -30
                       -30 -10 30 40 40 30 -10 -30  -30 -10 30 40 40 30 -10 -30  -30 -10 20 30 30 20 -10 -30
                       -30 -30 0 0 0 0 -30 -30  -50 -30 -30 -30 -30 -30 -30 -50"""),
}


def evaluate(pos: Position) -> int:
    """Centipawns, from the side to move's point of view."""
    b = pos.board
    heavy = sum(VALUE[b[s].lower()] for s in SQUARES if b[s] not in ".PpKk")
    endgame = heavy <= 1600 or ("Q" not in b and "q" not in b)
    score = 0
    for s in SQUARES:
        p = b[s]
        if p == ".":
            continue
        t = p.lower()
        table = PST["k_end"] if (t == "k" and endgame) else PST[t]
        r, f = s >> 4, s & 7
        if p.isupper():
            score += VALUE[t] + table[(7 - r) * 8 + f]
        else:
            score -= VALUE[t] + table[r * 8 + f]
    return score if pos.white else -score


# -- the search --------------------------------------------------------------------------------------------------

class _OutOfTime(Exception):
    pass


class _Search:
    def __init__(self, deadline: float) -> None:
        self.deadline = deadline
        self.nodes = 0

    def tick(self) -> None:
        self.nodes += 1
        if self.nodes & 511 == 0 and time.monotonic() > self.deadline:
            raise _OutOfTime

    @staticmethod
    def order(pos: Position, moves: list[Move], first: Optional[Move] = None) -> list[Move]:
        b = pos.board

        def key(m: Move) -> int:
            if m == first:
                return -10_000
            victim = b[m.to]
            k = 0
            if victim != "." or m.flag == "ep":
                k -= 10 * VALUE[(victim if victim != "." else "p").lower()] - VALUE[b[m.frm].lower()]
            if m.promo:
                k -= VALUE[m.promo]
            return k
        return sorted(moves, key=key)

    def negamax(self, pos: Position, depth: int, alpha: int, beta: int, ply: int) -> int:
        self.tick()
        if depth <= 0:
            return self.quiesce(pos, alpha, beta, 0)
        legal = 0
        for m in self.order(pos, pos.pseudo_moves()):
            nxt = pos.play(m)
            if not pos.is_legal_after(nxt):
                continue
            legal += 1
            score = -self.negamax(nxt, depth - 1, -beta, -alpha, ply + 1)
            if score >= beta:
                return score
            if score > alpha:
                alpha = score
        if not legal:
            return -(MATE - ply) if pos.in_check() else 0
        return alpha

    def quiesce(self, pos: Position, alpha: int, beta: int, qdepth: int) -> int:
        self.tick()
        stand = evaluate(pos)
        if stand >= beta or qdepth >= 6:
            return stand
        alpha = max(alpha, stand)
        b = pos.board
        loud = [m for m in pos.pseudo_moves() if b[m.to] != "." or m.flag == "ep" or m.promo]
        for m in self.order(pos, loud):
            nxt = pos.play(m)
            if not pos.is_legal_after(nxt):
                continue
            score = -self.quiesce(nxt, -beta, -alpha, qdepth + 1)
            if score >= beta:
                return score
            alpha = max(alpha, score)
        return alpha


class Best(NamedTuple):
    move: Move
    san: str
    frm: str              # "b1"
    to: str               # "c3"
    score: int            # centipawns for the side to move; near MATE is a forced mate
    depth: int
    nodes: int
    why: str              # "it develops a piece"
    says: str             # "Knight from b1 to c3: it develops a piece."


def best_move(pos: Position, seconds: float = 0.8, max_depth: int = 6) -> Optional[Best]:
    """The engine's move, searched deeper and deeper until `seconds` run out.
    None when there is no legal move (mate or stalemate)."""
    legal = pos.legal_moves()
    if not legal:
        return None
    search = _Search(time.monotonic() + max(0.05, seconds))
    best, best_score, done = _Search.order(pos, legal)[0], 0, 0
    for depth in range(1, max_depth + 1):
        try:
            alpha, choice = -MATE * 2, None
            for m in _Search.order(pos, legal, first=best):
                score = -search.negamax(pos.play(m), depth - 1, -MATE * 2, -alpha, 1)
                if choice is None or score > alpha:
                    alpha, choice = score, m
        except _OutOfTime:
            break
        best, best_score, done = choice, alpha, depth
        if abs(best_score) >= MATE - 100 or len(legal) == 1:
            break
    why = explain(pos, best)
    return Best(best, pos.san(best, legal), name_of(best.frm), name_of(best.to), best_score, done,
                search.nodes, why, f"{describe(pos, best)}: {why}.")


# -- saying it --------------------------------------------------------------------------------------------------

def describe(pos: Position, m: Move) -> str:
    """The move in words: "Knight from b1 to c3", "Castle kingside"."""
    if m.flag == "castle":
        return "Castle kingside" if m.to > m.frm else "Castle queenside"
    piece = NAMES[pos.board[m.frm].lower()].capitalize()
    text = f"{piece} from {name_of(m.frm)} to {name_of(m.to)}"
    return text + (f", becoming a {NAMES[m.promo]}" if m.promo else "")


def _targets(pos: Position, s: int) -> list[int]:
    """The squares the piece on `s` attacks."""
    b = pos.board
    p = b[s]
    t = p.lower()
    if t == "p":
        return [s + d for d in ((15, 17) if p.isupper() else (-15, -17)) if not (s + d) & 0x88]
    if t in "nk":
        return [s + d for d in (KNIGHT if t == "n" else KING) if not (s + d) & 0x88]
    out = []
    for d in (BISHOP if t == "b" else ROOK if t == "r" else BISHOP + ROOK):
        x = s + d
        while not x & 0x88:
            out.append(x)
            if b[x] != ".":
                break
            x += d
    return out


def explain(pos: Position, m: Move) -> str:
    """Why, in a coach's words - from the position, no model. At most two
    reasons: mate, a win of material, check, castling, a new queen,
    development, the centre, a threat, a piece saved."""
    b = pos.board
    mover = b[m.frm].lower()
    nxt = pos.play(m)
    if nxt.in_check() and not nxt.legal_moves():
        return "it's checkmate"
    reasons = []
    victim = "p" if m.flag == "ep" else b[m.to].lower()
    them = not pos.white
    if victim != ".":
        defended = nxt.attacked(m.to, them)
        if not defended or VALUE[victim] > VALUE[mover]:
            reasons.append(f"it wins the {NAMES[victim]}")
        else:
            reasons.append(f"it takes the {NAMES[victim]}")
    if m.promo:
        reasons.append(f"it makes a new {NAMES[m.promo]}")
    if m.flag == "castle":
        reasons.append("it gets your king safe")
    if nxt.in_check():
        reasons.append("it gives check")
    back = 0 if pos.white else 7
    if mover in "nb" and m.frm >> 4 == back and pos.fullmove <= 15:
        reasons.append("it develops a piece")
    if mover == "p" and name_of(m.to) in ("d4", "e4", "d5", "e5") and pos.fullmove <= 10:
        reasons.append("it takes the centre")
    if len(reasons) < 2:
        threats = [x for x in _targets(nxt, m.to)
                   if nxt.board[x] != "." and _own(them)(nxt.board[x]) and nxt.board[x].lower() != "k"
                   and (VALUE[nxt.board[x].lower()] > VALUE[mover] or not nxt.attacked(x, them))]
        if threats and victim == ".":
            top = max(threats, key=lambda x: VALUE[nxt.board[x].lower()])
            reasons.append(f"it attacks the {NAMES[nxt.board[top].lower()]} on {name_of(top)}")
    if len(reasons) < 2 and victim == "." and mover not in "pk" and pos.attacked(m.frm, them) \
            and not nxt.attacked(m.to, them):
        reasons.append(f"it gets your {NAMES[mover]} out of danger")
    return " and ".join(reasons[:2]) or "it's the strongest move here"
