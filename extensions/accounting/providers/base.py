"""IAccountingProvider: what the accounting agents may ask of a set of books.

Agents never see Xero, QuickBooks, Sage or a spreadsheet; they see this
interface, and the client's workspace says which implementation backs
it. A capability the backing system lacks is reported, not faked:
`supports("release_payment_batch")` is False for books that have no
bank connection, and the tool says so instead of pretending.

Integration priority (see IntegrationSpec.tier): an official API, then
OAuth, then file import/export, then browser automation, then Windows UI
automation, then vision. A provider is picked per client from the
highest tier that is configured; nothing here defaults to clicking.

Amounts cross this boundary as floats rounded to 2dp in the client's
currency. Anything that does arithmetic converts to minor units first
(extensions.accounting.books.to_minor).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


class ProviderUnsupported(RuntimeError):
    """The backing system cannot do this. Tools turn it into a failure that
    names the next-best route (usually: export a file for import)."""


@dataclass
class Account:
    code: str
    name: str
    type: str                       # asset | liability | equity | income | expense
    balance: float = 0.0
    taxable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BankTransaction:
    id: str
    account: str
    date: str
    description: str
    amount: float                   # + money in, - money out
    reconciled: bool = False
    journal_ref: str = ""
    suggested_account: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class InvoiceRecord:
    number: str
    kind: str                       # sales | purchase
    party: str
    date: str
    due: str
    subtotal: float
    tax: float
    total: float
    paid: float = 0.0
    status: str = "open"            # open | paid | void | draft
    reference: str = ""             # the supplier's own invoice number, for purchases
    po_number: str = ""
    document: str = ""
    items: list[dict[str, Any]] = field(default_factory=list)

    @property
    def outstanding(self) -> float:
        return round(self.total - self.paid, 2)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["outstanding"] = self.outstanding
        return d


@dataclass
class LedgerLine:
    journal: str
    date: str
    description: str
    account: str
    account_name: str
    debit: float
    credit: float
    reference: str = ""
    source: str = ""
    author: str = ""
    created_at: str = ""
    attachments: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


READ_METHODS = (
    "company_info", "accounts", "trial_balance", "general_ledger", "bank_accounts",
    "bank_transactions", "invoices", "contacts", "purchase_orders", "payroll",
    "payroll_periods", "profit_and_loss", "balance_sheet", "tax_summary", "balance",
)
WRITE_METHODS = (
    "import_bank_statement", "post_journal", "post_bill", "post_invoice", "post_bank_line",
    "apply_bank_match", "record_payment", "attach_document", "upsert_contact",
)
SENSITIVE_METHODS = ("release_payment_batch", "submit_tax_return")


class IAccountingProvider(ABC):
    """One client's books. Every method is async so an HTTP provider fits
    the same shape as a local file."""

    name: str = "provider"
    display_name: str = "Accounting provider"
    tier: str = "file"
    capabilities: frozenset[str] = frozenset(READ_METHODS)

    def supports(self, method: str) -> bool:
        return method in self.capabilities

    def require(self, method: str) -> None:
        if not self.supports(method):
            raise ProviderUnsupported(
                f"{self.display_name} cannot {method.replace('_', ' ')}. "
                + ("Export the draft for import instead." if method.startswith("post")
                   else "Do this step in the accounting system itself."))

    # -- read ----------------------------------------------------------------------
    @abstractmethod
    async def company_info(self) -> dict[str, Any]: ...
    @abstractmethod
    async def accounts(self) -> list[Account]: ...
    @abstractmethod
    async def trial_balance(self, as_of: Optional[str] = None) -> dict[str, Any]: ...
    @abstractmethod
    async def general_ledger(self, start: Optional[str] = None, end: Optional[str] = None,
                             account: Optional[str] = None, limit: int = 500) -> list[LedgerLine]: ...
    @abstractmethod
    async def bank_accounts(self) -> list[Account]: ...
    @abstractmethod
    async def bank_transactions(self, account: Optional[str] = None, start: Optional[str] = None,
                                end: Optional[str] = None,
                                unreconciled_only: bool = False) -> list[BankTransaction]: ...
    @abstractmethod
    async def invoices(self, kind: str = "sales", status: str = "all") -> list[InvoiceRecord]: ...

    async def contacts(self, kind: str = "") -> list[dict[str, Any]]:
        return []

    async def purchase_orders(self, supplier: str = "") -> list[dict[str, Any]]:
        return []

    async def payroll(self, period: str) -> list[dict[str, Any]]:
        return []

    async def payroll_periods(self) -> list[str]:
        return []

    async def balance(self, account: str, *, start: Optional[str] = None,
                      as_of: Optional[str] = None) -> float:
        self.require("balance")
        return 0.0

    async def profit_and_loss(self, start: str, end: str) -> dict[str, Any]:
        self.require("profit_and_loss")
        return {}

    async def balance_sheet(self, as_of: str) -> dict[str, Any]:
        self.require("balance_sheet")
        return {}

    async def tax_summary(self, start: str, end: str) -> dict[str, Any]:
        self.require("tax_summary")
        return {}

    # -- write (POST level: only ever called from post_drafts) ---------------------------
    async def import_bank_statement(self, rows: list[dict[str, Any]], *, account: str = "",
                                    source_file: str = "") -> dict[str, Any]:
        self.require("import_bank_statement")
        return {}

    async def post_journal(self, date: str, description: str, lines: list[dict[str, Any]], *,
                           reference: str = "", attachments: Optional[list[str]] = None) -> str:
        self.require("post_journal")
        return ""

    async def post_bill(self, supplier: str, date: str, items: list[dict[str, Any]], *,
                        reference: str = "", po_number: str = "", document: str = "",
                        due_days: int = 30) -> str:
        self.require("post_bill")
        return ""

    async def post_invoice(self, customer: str, date: str, items: list[dict[str, Any]], *,
                           due_days: int = 30, reference: str = "") -> str:
        self.require("post_invoice")
        return ""

    async def post_bank_line(self, line_id: str, account: str, *, tax_rate: Optional[float] = None,
                             attachments: Optional[list[str]] = None) -> str:
        self.require("post_bank_line")
        return ""

    async def apply_bank_match(self, line_id: str, target: str) -> str:
        self.require("apply_bank_match")
        return ""

    async def record_payment(self, number: str, amount: Optional[float] = None, *,
                             date: Optional[str] = None, account: str = "") -> str:
        self.require("record_payment")
        return ""

    async def attach_document(self, target: str, path: str) -> None:
        self.require("attach_document")

    async def upsert_contact(self, name: str, kind: str, **fields: Any) -> dict[str, Any]:
        self.require("upsert_contact")
        return {}

    # -- sensitive -------------------------------------------------------------------------
    async def release_payment_batch(self, payments: list[dict[str, Any]], *, reference: str) -> str:
        self.require("release_payment_batch")
        return ""

    async def submit_tax_return(self, summary: dict[str, Any], *, reference: str) -> str:
        self.require("submit_tax_return")
        return ""

    def close(self) -> None:
        """Release files/sockets. Called when a client session is dropped."""
