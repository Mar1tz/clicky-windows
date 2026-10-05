"""The Marketing extension: a marketer for one brand.

    create_extension() -> MarketingExtension   (see pack.py)
"""
from __future__ import annotations


def create_extension():
    from .pack import MarketingExtension
    return MarketingExtension()
