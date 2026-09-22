"""The Accounting extension: a digital accounting team for Tora's agent platform.

    create_extension() -> AccountingExtension

See docs/agents/EXTENSIONS.md for the extension design and
docs/agents/ACCOUNTING.md for what the pack does and how to try it.
"""
from __future__ import annotations


def create_extension():
    from .extension import AccountingExtension
    return AccountingExtension()
