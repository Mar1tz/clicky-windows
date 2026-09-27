"""The Payroll extension: pay runs checked the way a reviewer would.

    create_extension() -> PayrollExtension   (see pack.py)
"""
from __future__ import annotations


def create_extension():
    from .pack import PayrollExtension
    return PayrollExtension()
