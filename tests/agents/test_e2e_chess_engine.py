"""The chess engine on the PC (docs/SHOW_THE_NEXT_MOVE.md, SN-06).

"What's my best move?" was a vision model's opinion of a screenshot. Now a
position read exactly goes to an engine: legal moves by the rules, a short
search, and the move said like a coach would. The rules are proven the
standard way - perft, the number of positions reachable at each depth,
against the published counts for positions built to catch generator bugs
(castling through check, en passant pins, promotions).
"""
from __future__ import annotations

import time

import pytest

from specialists.chess import Position, best_move
from specialists.chess.engine import START_FEN, evaluate

KIWIPETE = "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1"
POS3 = "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1"
POS4 = "r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1"


def perft(pos: Position, depth: int) -> int:
    if depth == 0:
        return 1
    moves = pos.legal_moves()
    if depth == 1:
        return len(moves)
    return sum(perft(pos.play(m), depth - 1) for m in moves)


@pytest.mark.parametrize("fen, counts", [
    (START_FEN, [20, 400, 8902]),
    (KIWIPETE, [48, 2039]),                 # castling both ways, pins, promotions near
    (POS3, [14, 191, 2812]),                # en passant that would expose the king
    (POS4, [6, 264]),                       # promotions, checks, castling rights lost
])
def test_the_rules_count_every_position_exactly(fen, counts):
    pos = Position.from_fen(fen)
    for depth, n in enumerate(counts, start=1):
        assert perft(pos, depth) == n, f"perft({depth}) of {fen}"


def test_a_fen_reads_back_exactly():
    for fen in (START_FEN, KIWIPETE, POS3, POS4, "8/8/8/8/8/8/8/K6k b - - 12 70"):
        assert Position.from_fen(fen).fen() == fen


def test_a_move_list_replays_into_the_exact_position():
    pos = Position.from_moves(["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "O-O"])
    assert pos.fen() == "r1bqkbnr/1ppp1ppp/p1n5/1B2p3/4P3/5N2/PPPP1PPP/RNBQ1RK1 b kq - 1 4"


@pytest.mark.parametrize("written, uci", [
    ("Nf3", "g1f3"), ("♘f3", "g1f3"), ("e4!?", "e2e4"), ("e4", "e2e4"),
])
def test_moves_as_sites_write_them(written, uci):
    assert Position.start().parse_san(written).uci == uci


def test_notation_out_and_in_agree_including_hard_cases():
    for fen in (KIWIPETE, POS3, POS4):
        pos = Position.from_fen(fen)
        legal = pos.legal_moves()
        for m in legal:
            assert pos.parse_san(pos.san(m, legal)) == m, (fen, pos.san(m, legal))


def test_castling_promotion_and_en_passant_are_written_right():
    pos = Position.from_fen(KIWIPETE)
    sans = {pos.san(m) for m in pos.legal_moves()}
    assert {"O-O", "O-O-O", "Nxf7", "Bxa6", "Qxf6", "dxe6"} <= sans
    pos4 = Position.from_fen(POS4)            # in check: exactly six ways out
    assert {m.uci for m in pos4.legal_moves()} == {"g1h1", "f1f2", "c4c5", "d2d4", "f3d4", "b4c5"}
    promo = Position.from_fen("4k3/1P6/8/8/8/8/8/4K3 w - - 0 1")
    assert {"b8=Q+", "b8=R+", "b8=B", "b8=N"} == {promo.san(m) for m in promo.legal_moves() if m.promo}
    ep = Position.from_fen("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 2")
    assert "exd6" in {ep.san(m) for m in ep.legal_moves()}
    assert ep.play_san("exd6").fen() == "4k3/8/3P4/8/8/8/8/4K3 b - - 0 2"


def test_illegal_and_ambiguous_moves_are_refused():
    with pytest.raises(ValueError, match="not legal"):
        Position.start().parse_san("Ke2")
    with pytest.raises(ValueError, match="move 3"):
        Position.from_moves(["e4", "e5", "Qh8"])
    knights = Position.from_fen("4k3/8/8/8/8/8/8/1N2KN2 w - - 0 1")    # Nb1 and Nf1 both reach d2
    with pytest.raises(ValueError, match="ambiguous"):
        knights.parse_san("Nd2")
    assert knights.parse_san("Nbd2").uci == "b1d2"


@pytest.mark.parametrize("fen, best, why", [
    ("6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1", "Ra8#", "checkmate"),                 # back rank
    ("4k3/8/8/3q4/8/8/3R4/4K3 w - - 0 1", "Rxd5", "wins the queen"),               # a free queen
    ("r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4", "Qxf7#", "checkmate"),
])
def test_it_never_misses_a_mate_or_a_free_piece(fen, best, why):
    b = best_move(Position.from_fen(fen), seconds=1.0)
    assert b.san == best and why in b.why, b


def test_it_does_not_give_away_its_queen():
    # Qxd5 takes a pawn - and the c6 pawn takes the queen back.
    pos = Position.from_fen("4k3/8/2p5/3p4/8/8/8/3QK3 w - - 0 1")
    b = best_move(pos, seconds=1.0)
    assert b.san != "Qxd5", b
    assert evaluate(pos.play(b.move)) < 0, "after its move White is still the one ahead"


def test_the_opening_move_is_sensible_and_said_like_a_coach():
    b = best_move(Position.start(), seconds=0.8)
    assert b.san in {"e4", "d4", "Nf3", "Nc3", "c4"}, b.san
    assert b.says.startswith(("Pawn from", "Knight from")) and b.says.endswith(".")
    from specialists.chess import explain
    ruy = Position.from_moves(["e4", "e5", "Nf3", "Nc6"])
    assert explain(ruy, ruy.parse_san("Bb5")) == "it develops a piece"          # the knight is defended
    hang = Position.from_fen("4k3/8/2n5/8/8/8/8/R3K3 w - - 0 1")
    assert explain(hang, hang.parse_san("Rc1")) == "it attacks the knight on c6"  # and nothing defends it
    assert explain(Position.start(), Position.start().parse_san("e4")) == "it takes the centre"


def test_it_answers_within_its_time():
    pos = Position.from_fen(KIWIPETE)
    t0 = time.monotonic()
    b = best_move(pos, seconds=0.8)
    assert time.monotonic() - t0 < 2.5 and b.depth >= 2, (b.depth, b.nodes)


def test_no_move_when_the_game_is_over():
    assert best_move(Position.from_fen("7k/6Q1/6K1/8/8/8/8/8 b - - 0 1")) is None        # mated
    assert best_move(Position.from_fen("7k/8/6QK/8/8/8/8/8 b - - 0 1")) is None         # stalemate


def test_a_misread_board_is_caught():
    assert Position.start().problems() == []
    assert "Black has 0 kings" in Position.from_fen("rnbq1bnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQ - 0 1").problems()
    assert any("pawn is on the first" in p for p in
               Position.from_fen("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNP w KQkq - 0 1").problems())
    assert "the side not to move is in check" in Position.from_fen("4k3/8/8/8/8/8/4Q3/4K3 b - - 0 1").problems() \
        or Position.from_fen("4k3/4Q3/8/8/8/8/8/4K3 w - - 0 1").problems() == ["the side not to move is in check"]
