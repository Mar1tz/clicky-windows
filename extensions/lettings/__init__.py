"""The Lettings extension: rent, repairs, renewals and deposits for rented homes.

    create_extension() -> LettingsExtension   (see pack.py)
"""
from __future__ import annotations


def create_extension():
    from .pack import LettingsExtension
    return LettingsExtension()
