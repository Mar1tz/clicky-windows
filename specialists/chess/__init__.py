"""Chess: the rules, an engine and a coach's reason, all on the PC.

    from specialists.chess import Position, best_move
    pos = Position.start().play_san("e4").play_san("e5")
    best = best_move(pos)            # best.san "Nf3", best.says "Knight from g1 to f3: it develops a piece."
"""
from .engine import Best, Move, Position, best_move, explain

__all__ = ["Best", "Move", "Position", "best_move", "explain"]
