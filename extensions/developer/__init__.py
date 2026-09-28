"""The Software developers extension: the chores around the code.

    create_extension() -> DeveloperExtension   (see pack.py)
"""
from __future__ import annotations


def create_extension():
    from .pack import DeveloperExtension
    return DeveloperExtension()
