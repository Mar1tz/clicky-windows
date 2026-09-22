"""Accounting providers: the books behind a client, picked by configuration.

    make_provider(workspace) -> IAccountingProvider

Agents ask the interface for operations; which system answers is the
client's `provider` setting. Phase 1 ships three that need no
credentials; the cloud connectors are declared (so the UI can show them
and the integration order is explicit) but not yet available.
"""
from __future__ import annotations

import os

from agents.extensions import IntegrationSpec

from .base import (
    Account, BankTransaction, IAccountingProvider, InvoiceRecord, LedgerLine, ProviderUnsupported,
)
from .books_provider import BooksProvider, LocalBooksProvider, MockAccountingProvider
from .csv_excel import CSVExcelProvider

# Highest tier first: an API always beats a file, a file always beats the screen.
INTEGRATIONS = [
    IntegrationSpec("xero", "Xero", tier="oauth", available=False, needs=["TORA_XERO_CLIENT_ID"],
                    notes="Phase 2: OAuth2 + Accounting API"),
    IntegrationSpec("quickbooks", "QuickBooks Online", tier="oauth", available=False,
                    needs=["TORA_QUICKBOOKS_CLIENT_ID"], notes="Phase 2: OAuth2 + Accounting API"),
    IntegrationSpec("sage", "Sage Business Cloud", tier="oauth", available=False,
                    needs=["TORA_SAGE_CLIENT_ID"], notes="Phase 2"),
    IntegrationSpec("pastel", "Sage Pastel (desktop)", tier="file", available=False,
                    notes="Phase 2: import/export files; UI automation only as a last resort"),
    IntegrationSpec("custom_erp", "Custom ERP", tier="api", available=False,
                    notes="Phase 2: an adapter per ERP's API"),
    IntegrationSpec("csv", "Spreadsheet / CSV exports", tier="file", available=True,
                    notes="Reads exports from the client's Data folder; posts by writing import files"),
    IntegrationSpec("local", "Local books (this computer)", tier="file", available=True,
                    notes="A real double-entry ledger in the client folder"),
    IntegrationSpec("mock", "Demo books (mock data)", tier="file", available=True,
                    notes="ABC Manufacturing, Jun-Aug 2026, with planted exceptions"),
]


def make_provider(ws) -> IAccountingProvider:
    """The provider a ClientWorkspace is configured for."""
    kind = (ws.provider or "local").lower()
    data = ws.data
    os.makedirs(data, exist_ok=True)
    if kind == "mock":
        return MockAccountingProvider(os.path.join(data, "books.sqlite"), exports_dir=ws.path("Exports"),
                                      documents_dir=ws.documents)
    if kind == "local":
        p = LocalBooksProvider(os.path.join(data, "books.sqlite"), exports_dir=ws.path("Exports"))
        if not p.ledger.is_set_up:
            prof = ws.profile()
            p.ledger.setup(ws.client, currency=prof.currency, tax_rate=prof.tax_rate,
                           fiscal_year_start=prof.fiscal_year_start, tax_label=prof.tax_label,
                           renames=prof.renames)
        return p
    if kind == "csv":
        folder = ws.provider_settings.get("folder") or ws.path("Data")
        os.makedirs(folder, exist_ok=True)
        return CSVExcelProvider(folder, exports_dir=ws.path("Exports"), company=ws.client)
    spec = next((i for i in INTEGRATIONS if i.id == kind), None)
    raise ProviderUnsupported(
        f"{spec.name if spec else kind} is not connected in this version ({spec.notes if spec else 'unknown provider'}). "
        "Use 'csv' with exports from it, or 'local' books, for now.")


__all__ = [
    "Account", "BankTransaction", "IAccountingProvider", "InvoiceRecord", "LedgerLine",
    "ProviderUnsupported", "BooksProvider", "LocalBooksProvider", "MockAccountingProvider",
    "CSVExcelProvider", "INTEGRATIONS", "make_provider",
]
