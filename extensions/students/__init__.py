"""The Students extension: a study coach that never does the assessed work.

    create_extension() -> StudentsExtension   (see pack.py)
"""
from __future__ import annotations


def create_extension():
    from .pack import StudentsExtension
    return StudentsExtension()
