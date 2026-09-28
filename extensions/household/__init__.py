"""The Household finance extension: where the money went, and what goes out every month.

    create_extension() -> HouseholdExtension   (see pack.py)
"""
from __future__ import annotations


def create_extension():
    from .pack import HouseholdExtension
    return HouseholdExtension()
