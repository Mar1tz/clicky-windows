"""The Online store extension: a back room that reorders before you run out.

    create_extension() -> StoreExtension   (see pack.py)
"""
from __future__ import annotations


def create_extension():
    from .pack import StoreExtension
    return StoreExtension()
