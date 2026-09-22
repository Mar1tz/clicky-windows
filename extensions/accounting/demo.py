"""The demo client: ABC Manufacturing (Pty) Ltd, June-August 2026.

Deterministic books with the exceptions a good month-end should find
already planted, so every workflow can be shown - and tested - with no
credentials and no real data:

  * Steelco Supplies paid R84,200 on 22 Aug with no invoice in the books
    (their statement in the inbox shows invoice SS-2250 we never got)
  * Acme Packaging invoice AP-7731 entered twice; it is also 15% over PO-1001
  * FastFreight FF-551 paid twice (12 and 14 Aug)
  * three August VAT transactions with no supporting document
  * advertising up ~50% on July (Google Ads +47%, plus an Uber ride coded there);
    Microsoft 365 coded to repairs
  * no electricity bill for August (accrual), a full-year insurance
    premium expensed in one month (prepayment)
  * R3,150 sitting in suspense since July
  * two bank lines nobody has explained
  * a R50,000 manual "adjustment" to sales, posted by 'admin' on a Sunday night
  * Highveld Mining owes R92,000, 69 days overdue
  * payroll: one salary up 35%, a new employee sharing another's bank
    account, and wages in the ledger R6,500 short of the payroll register
  * Steelco changed its bank details on 20 Aug

The inbox holds five documents for the invoice-processing workflow: a
clean invoice that matches its PO, a duplicate, an unknown supplier, a
receipt, and a supplier statement.
"""
from __future__ import annotations

import os
from typing import Optional

from .books import BANK, Ledger, Line, to_major, to_minor
from .jurisdiction import profile
from .pdfgen import write_pdf

COMPANY = "ABC Manufacturing (Pty) Ltd"
PERIOD = "2026-08"
AS_OF = "2026-08-31"

CUSTOMERS = [
    ("Mzansi Retail", "accounts@mzansiretail.example", 30),
    ("Cape Fabrication", "ap@capefab.example", 30),
    ("Karoo Foods", "finance@karoofoods.example", 30),
    ("Durban Logistics", "pay@durbanlogistics.example", 30),
    ("Highveld Mining Supplies", "creditors@highveldmining.example", 30),
]
SUPPLIERS = [  # name, default account, bank account, bank changed
    ("Acme Packaging", "5000", "62811234567", ""),
    ("Steelco Supplies", "5000", "1049876543", "2026-08-20T09:12:00"),
    ("FastFreight Couriers", "5000", "4102233445", ""),
    ("Office National", "6300", "7788990011", ""),
    ("Google Ads", "6000", "", ""),
    ("Vodacom Business", "6900", "", ""),
    ("Microsoft", "6600", "", ""),
    ("City Power", "6800", "", ""),
    ("Santam Insurance", "6200", "", ""),
]
EMPLOYEES = {  # id: (name, gross, tax, bank)
    "E001": ("Thandi Nkosi", 38000, 6500, "FNB 62001"),
    "E002": ("Johan van Wyk", 42000, 7800, "SBSA 1002"),
    "E003": ("Priya Naidoo", 35000, 5600, "ABSA 4051"),
    "E004": ("Sipho Dlamini", 28000, 3900, "NED 7710"),
}


def seed_demo(L: Ledger, *, documents_dir: str = "", inbox_dir: str = "") -> None:
    za = profile("ZA")
    L.setup(COMPANY, preset="generic", currency=za.currency, tax_rate=za.tax_rate,
            fiscal_year_start=za.fiscal_year_start, tax_label=za.tax_label, renames=za.renames)

    def doc(name: str, lines: list[str]) -> str:
        if not documents_dir:
            return ""
        return write_pdf(os.path.join(documents_dir, name), lines, title=name.rsplit(".", 1)[0])

    for name, email, terms in CUSTOMERS:
        L.upsert_contact(name, "customer", email=email, terms_days=terms, created_at="2025-03-01T08:00:00")
    for name, acct, bank, changed in SUPPLIERS:
        L.upsert_contact(name, "supplier", default_account=acct, bank_account=bank,
                         created_at="2025-03-01T08:00:00", bank_changed_at=changed)

    L.add_purchase_order("PO-1001", "Acme Packaging", "2026-07-28", 10000,
                         [{"description": "Corrugated boxes 600x400", "quantity": 2000, "unit_price": 5.0}])
    L.add_purchase_order("PO-1002", "Steelco Supplies", "2026-07-10", 40000,
                         [{"description": "Mild steel sheet 2mm", "quantity": 80, "unit_price": 500.0}])
    L.add_purchase_order("PO-1003", "Acme Packaging", "2026-08-25", 5000,
                         [{"description": "Stretch wrap rolls", "quantity": 100, "unit_price": 50.0}])

    L.post("2026-06-01", "Opening balances", [Line(BANK, debit=to_minor(650000)),
                                              Line("1500", debit=to_minor(320000)),
                                              Line("3000", credit=to_minor(970000))],
           source="opening", created_at="2026-06-01T08:00:00")

    def bank_expense(when: str, desc: str, gross: float, account: str, *, rate: Optional[float] = None,
                     support: bool = True) -> int:
        jid = L.expense(when, desc, gross, account, tax_rate=rate, source="bank",
                        tags=[desc.split(" - ")[0].title()])
        if support:
            path = doc(os.path.join("Receipts", f"{when} {desc.split(' - ')[0].title()}.pdf"),
                       [f"{desc}", f"Date: {when}", f"Amount: R{gross:,.2f}", "Tax invoice"])
            if path:
                L.attach(jid, path)
        return jid

    # -- the three months ---------------------------------------------------------------
    ads = {6: 17250.0, 7: 23000.0, 8: 33810.0}
    for m in (6, 7, 8):
        mm = f"2026-{m:02d}"
        bank_expense(f"{mm}-01", "RENT - PROPERTY CO", 35000, "6500", rate=0)
        bank_expense(f"{mm}-05", "VODACOM - BUSINESS", 2300, "6900")
        bank_expense(f"{mm}-08", "MICROSOFT - 365 BUSINESS", 1840, "6600" if m < 8 else "7200")
        bank_expense(f"{mm}-20", "GOOGLE ADS - CAMPAIGNS", ads[m], "6000", support=(m < 8))
        if m < 8:
            bank_expense(f"{mm}-15", "CITY POWER - ELECTRICITY", 6900 if m == 6 else 7245, "6800")
        bank_expense(f"{mm}-{'30' if m == 6 else '31'}", "SERVICE FEE - BANK", 385, "6100", rate=0,
                     support=False)
        # payroll register + the wages journal
        staff = dict(EMPLOYEES)
        if m == 8:
            staff["E004"] = ("Sipho Dlamini", 37800, 6200, "NED 7710")
            staff["E005"] = ("Lerato Mokoena", 30000, 4500, "SBSA 1002")
        for eid, (name, gross, tax, bank) in staff.items():
            L.add_payroll(mm, eid, name, gross, gross - tax, tax, bank)
        net = sum(g - t for _, g, t, _ in staff.values())
        tax = sum(t for _, _, t, _ in staff.values())
        wages = net + tax if m < 8 else 176300          # August's journal is R6,500 short
        L.post(f"{mm}-25", f"SALARIES - {mm}", [Line("7000", debit=to_minor(wages)),
                                                 Line(BANK, credit=to_minor(net)),
                                                 Line("2500", credit=to_minor(wages - net))],
               source="payroll", created_at=f"{mm}-25T10:00:00")
        if m > 6:
            L.post(f"{mm}-07", "SARS - EMP201", [Line("2500", debit=to_minor(23800)),
                                                 Line(BANK, credit=to_minor(23800))],
                   source="bank", created_at=f"{mm}-07T09:00:00")

    # -- sales -------------------------------------------------------------------------------
    def invoice(when: str, customer: str, net: float, what: str, due_days: int = 30) -> str:
        return L.create_document("invoice", customer, [{"description": what, "quantity": 1, "unit_price": net}],
                                 when=when, due_days=due_days)["number"]

    i1 = invoice("2026-06-02", "Highveld Mining Supplies", 80000, "Fabricated brackets batch 44", due_days=21)
    i2 = invoice("2026-06-05", "Mzansi Retail", 100000, "Shelving units x 400")
    i3 = invoice("2026-06-12", "Cape Fabrication", 50000, "Laser cutting services June")
    i3b = invoice("2026-06-20", "Karoo Foods", 90000, "Cold-room shelving install")
    i4 = invoice("2026-07-08", "Karoo Foods", 30000, "Stainless prep tables x 12")
    invoice("2026-07-10", "Durban Logistics", 16000, "Pallet racking repairs")
    invoice("2026-07-15", "Durban Logistics", 20000, "Racking extension phase 2")
    i7 = invoice("2026-07-25", "Mzansi Retail", 110000, "Shelving units x 440")
    i7b = invoice("2026-07-28", "Cape Fabrication", 120000, "Laser cutting services July")
    invoice("2026-08-12", "Cape Fabrication", 140000, "Laser cutting services August")
    invoice("2026-08-20", "Mzansi Retail", 150000, "Shelving units x 600")
    invoice("2026-08-26", "Karoo Foods", 86000, "Stainless prep tables x 30")
    L.pay_document(i2, when="2026-07-05")
    L.pay_document(i3, when="2026-07-20")
    L.pay_document(i3b, when="2026-07-18")
    L.pay_document(i7b, when="2026-08-25")
    L.pay_document(i4, when="2026-08-10")
    L.pay_document(i7, when="2026-08-28")
    del i1

    # -- purchases ---------------------------------------------------------------------------
    def bill(when: str, supplier: str, net: float, what: str, account: str, ref: str, po: str = "",
             support: bool = True) -> str:
        path = doc(os.path.join("Invoices", f"{when} {supplier} {ref}.pdf"),
                   [f"TAX INVOICE {ref}", f"From: {supplier}", f"To: {COMPANY}", f"Date: {when}",
                    f"PO: {po or '-'}", f"{what}", f"Subtotal: R{net:,.2f}", f"VAT 15%: R{net * 0.15:,.2f}",
                    f"Total: R{net * 1.15:,.2f}"]) if support else ""
        return L.create_document("bill", supplier, [{"description": what, "quantity": 1, "unit_price": net,
                                                     "account": account}],
                                 when=when, reference=ref, po_number=po, file_path=path)["number"]

    b_steel = bill("2026-07-15", "Steelco Supplies", 40000, "Mild steel sheet 2mm x 80", "5000", "SS-2201", "PO-1002")
    bill("2026-08-04", "Acme Packaging", 11500, "Corrugated boxes 600x400 x 2300", "5000", "AP-7731", "PO-1001")
    bill("2026-08-11", "Acme Packaging", 11500, "Corrugated boxes 600x400 x 2300", "5000", "AP-7731", "PO-1001")
    b_ff = bill("2026-08-06", "FastFreight Couriers", 7000, "Courier - July consignments", "5000", "FF-551")
    bill("2026-08-09", "Office National", 2500, "Printer toner and paper", "6300", "ON-3321", support=False)
    L.pay_document(b_steel, when="2026-08-05")
    L.pay_document(b_ff, when="2026-08-12")

    # -- the planted problems ---------------------------------------------------------------------
    L.post("2026-08-14", "FASTFREIGHT COURIERS - PAYMENT", [Line("2000", debit=to_minor(8050)),
                                                          Line(BANK, credit=to_minor(8050))],
           source="bank", tags=["FastFreight Couriers"], created_at="2026-08-14T11:02:00")
    L.post("2026-08-22", "STEELCO SUPPLIES - PAYMENT", [Line("2000", debit=to_minor(84200)),
                                                      Line(BANK, credit=to_minor(84200))],
           source="bank", tags=["Steelco Supplies"], created_at="2026-08-22T08:30:00")
    bank_expense("2026-08-03", "SANTAM - ANNUAL PREMIUM 2026/27", 24000, "6200", rate=0)
    bank_expense("2026-08-18", "UBER - TRIP", 460, "6000", support=False)
    L.post("2026-07-29", "EFT CREDIT REF 55120", [Line(BANK, debit=to_minor(3150)),
                                                  Line("1900", credit=to_minor(3150))],
           source="bank", created_at="2026-07-29T15:00:00")
    L.post("2026-08-30", "Adjustment - per MD", [Line("1100", debit=to_minor(50000)),
                                                 Line("4000", credit=to_minor(50000))],
           source="manual", author="admin", created_at="2026-08-30T22:47:00")

    # -- rules the bookkeeper has learnt ---------------------------------------------------------
    for pattern, account, rate in (("RENT", "6500", 0), ("VODACOM", "6900", None), ("MICROSOFT", "6600", None),
                                   ("GOOGLE ADS", "6000", None), ("CITY POWER", "6800", None),
                                   ("SERVICE FEE", "6100", 0), ("UBER", "6700", 0), ("SANTAM", "6200", 0)):
        L.learn(pattern, account, tax_rate=rate)

    # -- the bank statement: every bank movement, plus two nobody recorded -----------------------
    rows = []
    for r in L.db.execute("SELECT j.date, j.description, l.debit, l.credit FROM lines l JOIN journal j "
                          "ON j.id = l.journal_id WHERE l.account = ? ORDER BY j.date, j.id", (BANK,)):
        amount = to_major(r["debit"] - r["credit"])
        rows.append({"Date": r["date"], "Description": r["description"].upper(), "Amount": f"{amount:.2f}"})
    rows.append({"Date": "2026-08-27", "Description": "CASH DEPOSIT BRANCH 4471", "Amount": "12000.00"})
    rows.append({"Date": "2026-08-29", "Description": "DEBIT ORDER REF 88213", "Amount": "-1299.00"})
    rows = [r for r in rows if r["Description"] != "OPENING BALANCES"]
    rows.sort(key=lambda r: r["Date"])
    L.import_statement(rows, account=BANK, source_file="FNB statement Jun-Aug 2026 (demo)")

    if inbox_dir:
        seed_inbox(inbox_dir)


def seed_inbox(inbox_dir: str) -> list[str]:
    """Five documents for the invoice-processing workflow."""
    os.makedirs(inbox_dir, exist_ok=True)
    docs = {
        "Acme_Packaging_AP-7790.pdf": [
            "TAX INVOICE", "Acme Packaging", "VAT No: 4120255871", "Invoice No: AP-7790",
            "Invoice Date: 28/08/2026", "Due Date: 27/09/2026", f"Bill To: {COMPANY}", "Order No: PO-1003",
            "Stretch wrap rolls  100 x R50.00   R5,000.00", "Subtotal: R5,000.00", "VAT 15%: R750.00",
            "Total Due: R5,750.00", "Bank: FNB 62811234567"],
        "FastFreight_FF-551.pdf": [
            "TAX INVOICE", "FastFreight Couriers", "Invoice Number: FF-551", "Date: 06/08/2026",
            "Courier - July consignments", "Subtotal R7,000.00", "VAT R1,050.00", "TOTAL R8,050.00"],
        "invoice_BrightSpark_Electrical_BSE-104.pdf": [
            "INVOICE", "BrightSpark Electrical cc", "Invoice #: BSE-104", "Date: 29/08/2026",
            "Electrical repairs - workshop DB board and lighting", "Labour and materials",
            "Amount excl VAT: R3,000.00", "VAT: R450.00", "Total: R3,450.00",
            "Please pay into: Capitec 1455667788"],
        "receipt_Seattle_Coffee.pdf": [
            "Seattle Coffee Company", "Sandton City", "RECEIPT", "30/08/2026 08:14",
            "2 x Flat white  R78.00", "1 x Muffin  R42.00", "Client meeting", "TOTAL R186.00",
            "VAT incl R24.26", "Paid: Card ****4471"],
        "Steelco_statement_Aug2026.pdf": [
            "STATEMENT OF ACCOUNT", "Steelco Supplies", f"Customer: {COMPANY}", "Statement date: 31/08/2026",
            "Date        Reference   Description           Debit       Credit",
            "15/07/2026  SS-2201     Invoice               46,000.00",
            "05/08/2026  PMT         Payment received                  46,000.00",
            "18/08/2026  SS-2250     Invoice               84,200.00",
            "22/08/2026  PMT         Payment received                  84,200.00",
            "Balance due: 0.00"],
    }
    paths = []
    for name, lines in docs.items():
        paths.append(write_pdf(os.path.join(inbox_dir, name), lines))
    bank_dir = os.path.join(inbox_dir, "Bank")
    os.makedirs(bank_dir, exist_ok=True)
    csv_path = os.path.join(bank_dir, "FNB_2026-09_week1.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        fh.write("Account: 62001234567 ABC Manufacturing\n\n")
        fh.write("Date,Description,Money In,Money Out,Balance\n")
        fh.write("01/09/2026,RENT - PROPERTY CO,,35000.00,\n")
        fh.write("02/09/2026,MICROSOFT - 365 BUSINESS,,1840.00,\n")
        fh.write("03/09/2026,MZANSI RETAIL PAYMENT INV-0010,23000.00,,\n")
        fh.write("04/09/2026,TAKEALOT ONLINE ORDER 99812,,2299.00,\n")
        fh.write("05/09/2026,VODACOM - BUSINESS,,2300.00,\n")
    paths.append(csv_path)
    return paths
