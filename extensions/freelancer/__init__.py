"""The Freelancer extension: a back office for one person.

    create_extension() -> FreelancerExtension

Keeps its money in the Accounting pack's ledger; see extension.py.
"""
from __future__ import annotations


def create_extension():
    from .extension import FreelancerExtension
    return FreelancerExtension()
