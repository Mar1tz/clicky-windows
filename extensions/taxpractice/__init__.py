"""The Tax practice extension: deadlines kept, documents chased, assessments checked.

    create_extension() -> TaxPracticeExtension   (see pack.py)
"""
from __future__ import annotations


def create_extension():
    from .pack import TaxPracticeExtension
    return TaxPracticeExtension()
