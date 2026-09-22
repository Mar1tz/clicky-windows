"""AccountingJurisdictionProfile: where the client is, as configuration.

The pack assists with workflows and preparation; it does not give tax or
legal advice, and it never assumes every client follows the same rules.
Everything jurisdiction-specific — currency, what the sales tax is
called and its standard rate, the fiscal year, date order, filing
periods, document requirements, deadlines — is data in a profile the
accountant can edit, not logic in the agents.

The presets are starting points with typical values. Each carries a
`verify` note, and every deadline the pack computes from one is labelled
"typical — confirm with the authority". Rates and rules change; the
profile is where the accountant corrects them.
"""
from __future__ import annotations

import calendar
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any


@dataclass
class FilingRule:
    """A recurring obligation. `every` months per period (1 monthly,
    2 bi-monthly, 3 quarterly, 12 annual), due `due_days` after period end,
    or on `due_day` of the month after (whichever the rule uses)."""
    name: str
    every: int = 1
    due_days: int = 0
    due_day: int = 0
    anchor_month: int = 1          # a month in which some period ENDS (aligns bi-monthly/quarterly)
    note: str = ""


@dataclass
class AccountingJurisdictionProfile:
    code: str
    country: str
    currency: str
    currency_symbol: str
    tax_label: str
    tax_rate: float
    fiscal_year_start: str                 # MM-DD
    day_first_dates: bool = True
    tax_registration_label: str = "Tax number"
    reporting_periods: str = "monthly"
    filings: list[FilingRule] = field(default_factory=list)
    document_requirements: list[str] = field(default_factory=list)
    retention_years: int = 5
    renames: dict[str, str] = field(default_factory=dict)
    verify: str = ("Typical values only. Confirm rates, thresholds and deadlines with the tax "
                   "authority or the client's adviser before relying on them.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AccountingJurisdictionProfile":
        data = dict(data)
        data["filings"] = [f if isinstance(f, FilingRule) else FilingRule(**f)
                           for f in data.get("filings", [])]
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})

    def money(self, amount: float) -> str:
        sign = "-" if amount < 0 else ""
        return f"{sign}{self.currency_symbol}{abs(amount):,.2f}"

    def deadlines(self, start: date, days: int = 120) -> list[dict[str, Any]]:
        """Upcoming filing deadlines in [start, start+days], typical dates."""
        end = start + timedelta(days=days)
        out = []
        for rule in self.filings:
            y, m = start.year - 1, 1
            while date(y, m, 1) <= end:
                if rule.every <= 1 or (m - rule.anchor_month) % rule.every == 0:
                    period_end = date(y, m, calendar.monthrange(y, m)[1])
                    if rule.due_day:
                        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
                        due = date(ny, nm, min(rule.due_day, calendar.monthrange(ny, nm)[1]))
                    else:
                        due = period_end + timedelta(days=rule.due_days)
                    if start <= due <= end:
                        out.append({"name": rule.name, "period_end": period_end.isoformat(),
                                    "due": due.isoformat(), "note": rule.note,
                                    "basis": "typical - confirm with the authority"})
                y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return sorted(out, key=lambda d: d["due"])


# -- presets (labels and typical dates, not law) ------------------------------------

PRESETS: dict[str, AccountingJurisdictionProfile] = {
    "generic": AccountingJurisdictionProfile(
        code="generic", country="(not set)", currency="USD", currency_symbol="$",
        tax_label="Sales Tax", tax_rate=0.0, fiscal_year_start="01-01", day_first_dates=False,
        document_requirements=["Supplier invoice or receipt for every expense",
                               "Bank statement for every bank account and month"],
    ),
    "ZA": AccountingJurisdictionProfile(
        code="ZA", country="South Africa", currency="ZAR", currency_symbol="R",
        tax_label="VAT", tax_rate=15.0, fiscal_year_start="03-01",
        tax_registration_label="VAT number", reporting_periods="monthly",
        filings=[FilingRule("VAT201 return", every=2, due_day=25, anchor_month=2,
                            note="Category A bi-monthly; eFiling deadlines can differ"),
                 FilingRule("EMP201 (PAYE/UIF/SDL)", every=1, due_day=7)],
        document_requirements=["Valid tax invoice for input VAT claims",
                               "Supplier VAT number on tax invoices above the full-invoice threshold",
                               "Bank statements for every account and month"],
        retention_years=5, renames={"2200": "VAT Control", "1100": "Trade Debtors",
                                    "2000": "Trade Creditors"},
    ),
    "GB": AccountingJurisdictionProfile(
        code="GB", country="United Kingdom", currency="GBP", currency_symbol="£",
        tax_label="VAT", tax_rate=20.0, fiscal_year_start="04-06",
        tax_registration_label="VAT registration number", reporting_periods="quarterly",
        filings=[FilingRule("VAT return (MTD)", every=3, due_days=38, anchor_month=3,
                            note="Due 1 month and 7 days after the quarter end"),
                 FilingRule("PAYE/NIC payment", every=1, due_day=22)],
        document_requirements=["VAT invoice to reclaim input VAT", "Digital records (MTD)"],
        retention_years=6, renames={"2200": "VAT Control", "1100": "Trade Debtors",
                                    "2000": "Trade Creditors", "7100": "Subsistence & Entertaining"},
    ),
    "US": AccountingJurisdictionProfile(
        code="US", country="United States", currency="USD", currency_symbol="$",
        tax_label="Sales Tax", tax_rate=0.0, fiscal_year_start="01-01", day_first_dates=False,
        tax_registration_label="EIN", reporting_periods="monthly",
        filings=[FilingRule("Estimated tax payment", every=3, due_days=15, anchor_month=3,
                            note="Federal quarterly estimates; state rules vary"),
                 FilingRule("Sales tax return", every=1, due_day=20, note="Varies by state")],
        document_requirements=["Receipts for expenses", "W-9 on file for contractors (1099)"],
        retention_years=7,
    ),
    "EU": AccountingJurisdictionProfile(
        code="EU", country="European Union (generic)", currency="EUR", currency_symbol="€",
        tax_label="VAT", tax_rate=21.0, fiscal_year_start="01-01",
        tax_registration_label="VAT ID", reporting_periods="quarterly",
        filings=[FilingRule("VAT return", every=3, due_days=30, anchor_month=3,
                            note="Rate and deadline vary by member state")],
        document_requirements=["Compliant VAT invoice", "VAT ID of EU B2B customers"],
        retention_years=10, renames={"2200": "VAT Control"},
    ),
    "AU": AccountingJurisdictionProfile(
        code="AU", country="Australia", currency="AUD", currency_symbol="A$",
        tax_label="GST", tax_rate=10.0, fiscal_year_start="07-01",
        tax_registration_label="ABN", reporting_periods="quarterly",
        filings=[FilingRule("BAS", every=3, due_days=28, anchor_month=3,
                            note="Quarterly BAS; some quarters differ")],
        document_requirements=["Tax invoice for GST credits above the threshold"],
        retention_years=5, renames={"2200": "GST Control"},
    ),
}

ALIASES = {"south africa": "ZA", "sa": "ZA", "uk": "GB", "united kingdom": "GB", "england": "GB",
           "usa": "US", "united states": "US", "america": "US", "europe": "EU", "australia": "AU"}


def profile(code_or_name: str = "generic", overrides: dict[str, Any] | None = None) -> AccountingJurisdictionProfile:
    key = (code_or_name or "generic").strip()
    key = ALIASES.get(key.lower(), key)
    base = PRESETS.get(key) or PRESETS.get(key.upper()) or PRESETS["generic"]
    if not overrides:
        return AccountingJurisdictionProfile.from_dict(base.to_dict())
    return AccountingJurisdictionProfile.from_dict({**base.to_dict(), **overrides})
