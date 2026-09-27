"""The Trades extension: the office side of a plumber, electrician or builder.

    create_extension() -> TradesExtension   (see pack.py)
"""
from __future__ import annotations


def create_extension():
    from .pack import TradesExtension
    return TradesExtension()
