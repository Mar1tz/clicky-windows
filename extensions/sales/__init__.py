"""The Sales CRM extension: a pipeline that stays honest.

    create_extension() -> SalesExtension   (see pack.py)
"""
from __future__ import annotations


def create_extension():
    from .pack import SalesExtension
    return SalesExtension()
