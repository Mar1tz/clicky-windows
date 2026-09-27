"""The Video creator extension: a producer for a one-person channel.

    create_extension() -> CreatorExtension   (see pack.py)
"""
from __future__ import annotations


def create_extension():
    from .pack import CreatorExtension
    return CreatorExtension()
