"""Specialists: exact answers for screens Tora recognises, before any pixels.

A screen look asks a vision model, which is slow, paid and a generalist.
Some screens are better read exactly and answered on the PC: a chess game
on chess.com or lichess is a position (from its move list) and an engine's
move. A specialist recognises its screen and returns what to say and what
to draw - the same contract a look has (docs/SHOW_THE_NEXT_MOVE.md, SN-08).

    specialists.chess    the rules, an engine, a reason in words (SN-06)
"""
