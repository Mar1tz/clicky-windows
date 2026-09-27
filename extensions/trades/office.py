"""A tradesperson's office: price list, quotes, the job diary, invoices and
certificates - one folder, done in the evening from the day's notes.

    <root>/trades.json   labour rate, markup, call-out fee, VAT, terms, working hours
    <root>/office.sqlite materials, quotes, jobs, invoices, certificates
    <root>/Quotes  Invoices  Parts  Outbox

The sums are code: a quote priced from the price list with the markup,
labour, call-out and VAT; a diary that will not double-book and knows the
free slots; parts lists per supplier for the week's jobs; an invoice built
from the job; certificates that come due for renewal. The model reads the
job description or photos and says which parts and how many hours.
Nothing is sent or ordered from here.
"""
from __future__ import annotations

import calendar
import csv
import json
import os
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from typing import Any, Optional

from extensions.accounting.pdfgen import write_pdf
from extensions.packkit import Store, slug


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _money(v: float) -> float:
    return round(v + 1e-9, 2)


def _day(value: Any, default: Optional[date] = None) -> date:
    if not value:
        if default is None:
            raise ValueError("a date is needed (YYYY-MM-DD)")
        return default
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        raise ValueError(f"{value!r} is not a date (YYYY-MM-DD)") from None


def _when(value: str) -> datetime:
    try:
        return datetime.fromisoformat(str(value).strip().replace("T", " "))
    except ValueError:
        raise ValueError(f"{value!r} is not a start time like 2026-10-02 09:00") from None


class Office(Store):
    SETTINGS_FILE = "trades.json"
    DB = "office.sqlite"
    FOLDERS = ("Quotes", "Invoices", "Parts", "Outbox")
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS materials (code TEXT PRIMARY KEY COLLATE NOCASE, name TEXT NOT NULL,
        unit TEXT NOT NULL DEFAULT 'each', cost REAL NOT NULL, supplier TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS quotes (id INTEGER PRIMARY KEY, number TEXT NOT NULL UNIQUE, customer TEXT NOT NULL,
        address TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '', job TEXT NOT NULL, created TEXT NOT NULL,
        valid_until TEXT NOT NULL, lines TEXT NOT NULL, labour_hours REAL NOT NULL, callout REAL NOT NULL,
        subtotal REAL NOT NULL, vat REAL NOT NULL, total REAL NOT NULL, status TEXT NOT NULL DEFAULT 'draft',
        file TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY, quote TEXT NOT NULL DEFAULT '', customer TEXT NOT NULL,
        address TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '', title TEXT NOT NULL, start TEXT NOT NULL,
        hours REAL NOT NULL, status TEXT NOT NULL DEFAULT 'booked', notes TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS invoices (id INTEGER PRIMARY KEY, number TEXT UNIQUE, job_id INTEGER,
        customer TEXT NOT NULL, email TEXT NOT NULL DEFAULT '', lines TEXT NOT NULL, subtotal REAL NOT NULL,
        vat REAL NOT NULL, total REAL NOT NULL, status TEXT NOT NULL DEFAULT 'draft', issued TEXT NOT NULL DEFAULT '',
        due TEXT NOT NULL DEFAULT '', paid REAL NOT NULL DEFAULT 0, file TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS certificates (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, customer TEXT NOT NULL,
        address TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '', issued TEXT NOT NULL,
        valid_months INTEGER NOT NULL, due TEXT NOT NULL, renewed INTEGER NOT NULL DEFAULT 0);
    """

    def num(self, key: str, default: float) -> float:
        v = self.cfg.get(key)
        return float(default if v in (None, "") else v)

    @property
    def currency(self) -> str:
        return self.cfg.get("currency") or "GBP"

    def money(self, v: float) -> str:
        return f"{self.currency} {v:,.2f}"

    @property
    def name(self) -> str:
        return self.cfg.get("name") or "My trade business"

    # -- price list ------------------------------------------------------------------------------------------

    def set_material(self, code: str, name: str, cost: Any, *, unit: str = "each", supplier: str = "") -> dict[str, Any]:
        code, name = (code or "").strip(), (name or "").strip()
        if not code or not name:
            raise ValueError("a material needs a code and a name")
        cost = float(cost)
        if cost < 0:
            raise ValueError("a cost cannot be negative")
        self.write("INSERT INTO materials(code, name, unit, cost, supplier) VALUES (?, ?, ?, ?, ?) ON CONFLICT(code) "
                   "DO UPDATE SET name = excluded.name, unit = excluded.unit, cost = excluded.cost, "
                   "supplier = excluded.supplier", (code, name, unit or "each", cost, supplier.strip()))
        return self.material(code)

    def material(self, ref: str) -> dict[str, Any]:
        r = self.one("SELECT * FROM materials WHERE code = ?", ((ref or "").strip(),))
        if r is None:
            found = self.rows("SELECT * FROM materials WHERE name LIKE ?", (f"%{(ref or '').strip()}%",))
            if len(found) > 1:
                raise ValueError(f"'{ref}' matches {', '.join(m['code'] + ' ' + m['name'] for m in found[:6])} - "
                                 "which code?")
            r = found[0] if found else None
        if r is None:
            raise KeyError(f"'{ref}' is not on the price list; add it first")
        return r

    def import_price_list(self, path: str, supplier: str = "") -> dict[str, int]:
        """A supplier's price list as CSV: code (or SKU), name (or description), cost (or price), unit."""
        if not os.path.isfile(path):
            raise FileNotFoundError(f"no file {path}")
        added = skipped = 0
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for raw in csv.DictReader(fh):
                r = {(k or "").lower().strip(): (v or "").strip() for k, v in raw.items()}
                code = r.get("code") or r.get("sku") or r.get("product code") or ""
                name = r.get("name") or r.get("description") or ""
                cost = (r.get("cost") or r.get("price") or r.get("trade price") or "").replace(",", "").lstrip("£$€")
                if not code or not name or not cost:
                    skipped += 1
                    continue
                self.set_material(code, name, float(cost), unit=r.get("unit") or "each",
                                  supplier=supplier or r.get("supplier") or "")
                added += 1
        return {"added": added, "skipped": skipped}

    # -- pricing -----------------------------------------------------------------------------------------------

    def price(self, parts: list[dict[str, Any]], labour_hours: float, *, callout: Optional[float] = None,
              extras: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
        """Parts at cost plus markup, labour at the rate, call-out, extras, VAT."""
        markup = self.num("markup_pct", 20) / 100
        lines = []
        for p in parts or []:
            m = self.material(str(p.get("code") or p.get("name") or ""))
            qty = float(p.get("qty", p.get("quantity", 1)) or 1)
            if qty <= 0:
                raise ValueError(f"{m['name']}: the quantity must be above zero")
            unit = _money(m["cost"] * (1 + markup))
            lines.append({"kind": "part", "code": m["code"], "description": m["name"], "qty": qty, "unit": unit,
                          "amount": _money(unit * qty), "cost": _money(m["cost"] * qty), "supplier": m["supplier"]})
        hours = float(labour_hours or 0)
        if hours < 0:
            raise ValueError("labour hours cannot be negative")
        rate = self.num("labour_rate", 60)
        if hours:
            lines.append({"kind": "labour", "description": f"Labour ({hours:g} h at {self.money(rate)})", "qty": hours,
                          "unit": rate, "amount": _money(hours * rate)})
        fee = self.num("callout", 0) if callout is None else float(callout)
        if fee:
            lines.append({"kind": "callout", "description": "Call-out", "qty": 1, "unit": fee, "amount": _money(fee)})
        for x in extras or []:
            amount = float(x.get("amount") or 0)
            if not str(x.get("description") or "").strip() or amount <= 0:
                raise ValueError("an extra needs a description and an amount above zero")
            lines.append({"kind": "extra", "description": x["description"].strip(), "qty": 1, "unit": amount,
                          "amount": _money(amount)})
        if not lines:
            raise ValueError("nothing to price: give parts or hours")
        subtotal = _money(sum(line["amount"] for line in lines))
        vat = _money(subtotal * self.num("vat_pct", 0) / 100)
        return {"lines": lines, "labour_hours": hours, "callout": fee, "subtotal": subtotal, "vat": vat,
                "total": _money(subtotal + vat), "materials_cost": _money(sum(line.get("cost", 0) for line in lines))}

    def _pdf(self, folder: str, number: str, customer: str, title: str, lines: list[dict[str, Any]],
             sums: dict[str, Any], extra: list[str]) -> str:
        path = os.path.join(self.folder(folder), f"{number} {slug(customer)}.pdf")
        body = [f"To: {customer}"] + extra + [""]
        for line in lines:
            body.append(f"{line['description'][:52]:<52} {line['qty']:>6g} x {line['unit']:>9.2f} {line['amount']:>11.2f}")
        body += ["", f"{'Subtotal':<73}{sums['subtotal']:>11.2f}",
                 f"{'VAT ' + format(self.num('vat_pct', 0), 'g') + '%':<73}{sums['vat']:>11.2f}",
                 f"{'Total ' + self.currency:<73}{sums['total']:>11.2f}"]
        return write_pdf(path, body, title=f"{self.name} - {title} {number}")

    # -- quotes ------------------------------------------------------------------------------------------------

    def create_quote(self, customer: str, job: str, parts: list[dict[str, Any]], labour_hours: float, *,
                     address: str = "", email: str = "", callout: Optional[float] = None,
                     valid_days: int = 30) -> dict[str, Any]:
        if not (customer or "").strip() or not (job or "").strip():
            raise ValueError("a quote needs the customer and what the job is")
        p = self.price(parts, labour_hours, callout=callout)
        seq = (self.one("SELECT count(*) AS n FROM quotes") or {"n": 0})["n"] + 1
        number = f"Q-{seq:04d}"
        valid = (date.today() + timedelta(days=int(valid_days))).isoformat()
        file = self._pdf("Quotes", number, customer, "Quote", p["lines"], p,
                         [f"Job: {job}", f"Address: {address}" if address else "", f"Valid until {valid}"])
        self.write("INSERT INTO quotes(number, customer, address, email, job, created, valid_until, lines, labour_hours, "
                   "callout, subtotal, vat, total, file) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (number, customer.strip(), address.strip(), email.strip(), job.strip(), _now(), valid,
                    json.dumps(p["lines"]), p["labour_hours"], p["callout"], p["subtotal"], p["vat"], p["total"], file))
        return self.quote(number)

    def quote(self, number: str) -> dict[str, Any]:
        r = self.one("SELECT * FROM quotes WHERE number = ?", ((number or "").strip().upper(),))
        if r is None:
            raise KeyError(f"no quote {number!r}")
        r["lines"] = json.loads(r["lines"])
        return r

    def set_quote(self, number: str, status: str) -> dict[str, Any]:
        if status not in ("sent", "accepted", "declined"):
            raise ValueError("a quote is sent, accepted or declined")
        q = self.quote(number)
        self.write("UPDATE quotes SET status = ? WHERE id = ?", (status, q["id"]))
        return self.quote(q["number"])

    # -- the diary ---------------------------------------------------------------------------------------------

    def _clash(self, start: datetime, hours: float, skip: int = 0) -> Optional[dict[str, Any]]:
        end = start + timedelta(hours=hours)
        for j in self.rows("SELECT * FROM jobs WHERE status = 'booked' AND substr(start, 1, 10) = ? AND id != ?",
                           (start.date().isoformat(), skip)):
            s = _when(j["start"])
            if start < s + timedelta(hours=j["hours"]) and end > s:
                return j
        return None

    def book(self, *, quote: str = "", customer: str = "", title: str = "", start: str, hours: Any = None,
             address: str = "", email: str = "", notes: str = "") -> dict[str, Any]:
        q = self.quote(quote) if quote else None
        if q and q["status"] == "declined":
            raise ValueError(f"{q['number']} was declined")
        customer = (customer or (q["customer"] if q else "")).strip()
        title = (title or (q["job"] if q else "")).strip()
        if not customer or not title:
            raise ValueError("a booking needs the customer and the job")
        hours = float(hours if hours not in (None, "") else (q["labour_hours"] if q and q["labour_hours"] else 1))
        if not 0 < hours <= 12:
            raise ValueError("a booking is between a few minutes and 12 hours")
        begin = _when(start)
        clash = self._clash(begin, hours)
        if clash:
            raise ValueError(f"that clashes with '{clash['title']}' for {clash['customer']} at {clash['start'][11:16]}; "
                             "pick a free slot")
        jid = self.write("INSERT INTO jobs(quote, customer, address, email, title, start, hours, notes) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (q["number"] if q else "", customer, address or (q["address"] if q else ""),
                          email or (q["email"] if q else ""), title, begin.isoformat(sep=" ", timespec="minutes"),
                          hours, notes.strip()))
        if q and q["status"] != "accepted":
            self.write("UPDATE quotes SET status = 'accepted' WHERE id = ?", (q["id"],))
        return self.job(jid)

    def job(self, jid: Any) -> dict[str, Any]:
        r = self.one("SELECT * FROM jobs WHERE id = ?", (int(jid),))
        if r is None:
            raise KeyError(f"no job #{jid}")
        return r

    def day(self, when: str = "") -> list[dict[str, Any]]:
        d = _day(when, date.today()).isoformat()
        return self.rows("SELECT * FROM jobs WHERE status = 'booked' AND substr(start, 1, 10) = ? ORDER BY start", (d,))

    def free_slots(self, when: str = "", min_hours: float = 1.0) -> list[dict[str, str]]:
        d = _day(when, date.today())
        opens, closes = _when(f"{d} {self.cfg.get('day_start') or '08:00'}"), _when(f"{d} {self.cfg.get('day_end') or '17:00'}")
        at, out = opens, []
        for j in self.day(d.isoformat()):
            s = _when(j["start"])
            if (s - at).total_seconds() / 3600 >= min_hours:
                out.append({"from": at.strftime("%H:%M"), "to": s.strftime("%H:%M")})
            at = max(at, s + timedelta(hours=j["hours"]))
        if (closes - at).total_seconds() / 3600 >= min_hours:
            out.append({"from": at.strftime("%H:%M"), "to": closes.strftime("%H:%M")})
        return out

    def finish(self, jid: Any, notes: str = "") -> dict[str, Any]:
        j = self.job(jid)
        self.write("UPDATE jobs SET status = 'done', notes = trim(notes || ' ' || ?) WHERE id = ?", (notes, j["id"]))
        return self.job(j["id"])

    def parts_lists(self, start: str = "", days: int = 7) -> list[dict[str, Any]]:
        first = _day(start, date.today())
        last = first + timedelta(days=int(days) - 1)
        need: dict[str, dict[str, dict[str, Any]]] = {}
        for j in self.rows("SELECT * FROM jobs WHERE status = 'booked' AND quote != '' AND substr(start, 1, 10) "
                           "BETWEEN ? AND ?", (first.isoformat(), last.isoformat())):
            for line in self.quote(j["quote"])["lines"]:
                if line["kind"] != "part":
                    continue
                row = need.setdefault(line["supplier"] or "No supplier set", {}).setdefault(
                    line["code"], {"code": line["code"], "description": line["description"], "qty": 0, "jobs": []})
                row["qty"] += line["qty"]
                row["jobs"].append(f"#{j['id']}")
        out = []
        for supplier, rows in sorted(need.items()):
            path = os.path.join(self.folder("Parts"), f"{first.isoformat()} {slug(supplier)}.csv")
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["code", "description", "qty", "for jobs"])
                for r in rows.values():
                    w.writerow([r["code"], r["description"], f"{r['qty']:g}", " ".join(r["jobs"])])
            out.append({"supplier": supplier, "path": path, "items": list(rows.values())})
        return out

    # -- invoices ------------------------------------------------------------------------------------------------

    def draft_invoice(self, jid: Any, *, hours: Any = None, extras: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
        j = self.job(jid)
        if self.one("SELECT 1 FROM invoices WHERE job_id = ? AND status != 'void'", (j["id"],)):
            raise ValueError(f"job #{j['id']} already has an invoice")
        q = self.quote(j["quote"]) if j["quote"] else None
        parts = [{"code": line["code"], "qty": line["qty"]} for line in (q["lines"] if q else []) if line["kind"] == "part"]
        worked = float(hours) if hours not in (None, "") else j["hours"]
        p = self.price(parts, worked, callout=q["callout"] if q else None, extras=extras)
        iid = self.write("INSERT INTO invoices(job_id, customer, email, lines, subtotal, vat, total) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (j["id"], j["customer"], j["email"], json.dumps(p["lines"]), p["subtotal"], p["vat"], p["total"]))
        return self.invoice(iid)

    def invoice(self, ref: Any) -> dict[str, Any]:
        r = self.one("SELECT * FROM invoices WHERE id = ?", (int(ref),)) if str(ref).isdigit() else \
            self.one("SELECT * FROM invoices WHERE number = ?", (str(ref).strip().upper(),))
        if r is None:
            raise KeyError(f"no invoice {ref!r}")
        r["lines"] = json.loads(r["lines"])
        r["outstanding"] = _money(r["total"] - r["paid"])
        return r

    def issue(self, ref: Any, when: str = "") -> dict[str, Any]:
        inv = self.invoice(ref)
        if inv["status"] != "draft":
            raise ValueError(f"invoice {inv['number'] or inv['id']} is already {inv['status']}")
        issued = _day(when, date.today())
        due = issued + timedelta(days=int(self.num("terms_days", 14)))
        seq = (self.one("SELECT count(*) AS n FROM invoices WHERE number IS NOT NULL") or {"n": 0})["n"] + 1
        number = f"INV-{seq:04d}"
        file = self._pdf("Invoices", number, inv["customer"], "Invoice", inv["lines"], inv,
                         [f"Date: {issued.isoformat()}", f"Due: {due.isoformat()}"])
        self.write("UPDATE invoices SET number = ?, status = 'issued', issued = ?, due = ?, file = ? WHERE id = ?",
                   (number, issued.isoformat(), due.isoformat(), file, inv["id"]))
        out = self.invoice(number)
        out["email"] = self._email(inv["customer"], inv["email"], f"Invoice {number} from {self.name}",
                                   f"Hello,\n\nPlease find attached invoice {number} for {self.money(inv['total'])}, due "
                                   f"{due.isoformat()}.\n\nThank you,\n{self.name}\n", file)
        return out

    def pay(self, number: str, amount: Any = None, when: str = "") -> dict[str, Any]:
        inv = self.invoice(number)
        if inv["status"] not in ("issued", "part-paid"):
            raise ValueError(f"invoice {inv['number'] or inv['id']} is {inv['status']}; issue it first")
        amt = inv["outstanding"] if amount in (None, "") else float(amount)
        if amt <= 0 or amt > inv["outstanding"] + 0.001:
            raise ValueError(f"{self.money(inv['outstanding'])} is outstanding on {inv['number']}")
        paid = _money(inv["paid"] + amt)
        self.write("UPDATE invoices SET paid = ?, status = ? WHERE id = ?",
                   (paid, "paid" if paid >= inv["total"] - 0.001 else "part-paid", inv["id"]))
        return self.invoice(inv["number"])

    def overdue(self, as_of: str = "") -> list[dict[str, Any]]:
        d = _day(as_of, date.today())
        rows = [self.invoice(r["id"]) for r in self.rows(
            "SELECT id FROM invoices WHERE status IN ('issued', 'part-paid') AND due < ? ORDER BY due", (d.isoformat(),))]
        for r in rows:
            r["days_late"] = (d - date.fromisoformat(r["due"])).days
        return rows

    # -- certificates --------------------------------------------------------------------------------------------

    def add_certificate(self, kind: str, customer: str, issued: str, valid_months: int, *, address: str = "",
                        email: str = "") -> dict[str, Any]:
        if not (kind or "").strip() or not (customer or "").strip():
            raise ValueError("a certificate needs its kind and the customer")
        months = int(valid_months)
        if not 1 <= months <= 120:
            raise ValueError("valid for 1 to 120 months")
        start = _day(issued)
        y, m = divmod(start.month - 1 + months, 12)
        due = date(start.year + y, m + 1, min(start.day, calendar.monthrange(start.year + y, m + 1)[1]))
        cid = self.write("INSERT INTO certificates(kind, customer, address, email, issued, valid_months, due) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)", (kind.strip(), customer.strip(), address.strip(), email.strip(),
                                                          start.isoformat(), months, due.isoformat()))
        return self.one("SELECT * FROM certificates WHERE id = ?", (cid,)) or {}

    def renewals(self, as_of: str = "", days: int = 30) -> list[dict[str, Any]]:
        d = _day(as_of, date.today())
        rows = self.rows("SELECT * FROM certificates WHERE renewed = 0 AND due <= ? ORDER BY due",
                         ((d + timedelta(days=int(days))).isoformat(),))
        for r in rows:
            r["days_left"] = (date.fromisoformat(r["due"]) - d).days
        return rows

    def renewal_reminder(self, cid: Any) -> dict[str, Any]:
        c = self.one("SELECT * FROM certificates WHERE id = ?", (int(cid),))
        if c is None:
            raise KeyError(f"no certificate #{cid}")
        path = self._email(c["customer"], c["email"], f"Your {c['kind']} is due on {c['due']}",
                           f"Hello,\n\nYour {c['kind']}" + (f" at {c['address']}" if c["address"] else "")
                           + f" runs out on {c['due']}. Reply with a day that suits and I will book you in.\n\n"
                             f"Thanks,\n{self.name}\n", "")
        return {**c, "email_draft": path}

    def _email(self, customer: str, to: str, subject: str, body: str, attach: str) -> str:
        msg = EmailMessage()
        msg["To"] = to or customer
        msg["Subject"] = subject
        msg["X-Unsent"] = "1"
        msg.set_content(body)
        if attach and os.path.isfile(attach):
            with open(attach, "rb") as fh:
                msg.add_attachment(fh.read(), maintype="application", subtype="pdf", filename=os.path.basename(attach))
        path = os.path.join(self.folder("Outbox"), f"{datetime.now().strftime('%Y%m%d-%H%M%S%f')} {slug(subject)}.eml")
        with open(path, "wb") as fh:
            fh.write(bytes(msg))
        return path

    def overview(self, as_of: str = "") -> dict[str, Any]:
        d = _day(as_of, date.today())
        return {"as_of": d.isoformat(), "today": self.day(d.isoformat()),
                "tomorrow": self.day((d + timedelta(days=1)).isoformat()),
                "quotes_waiting": self.rows("SELECT number, customer, job, total FROM quotes WHERE status IN ('draft', 'sent')"),
                "to_invoice": self.rows("SELECT j.* FROM jobs j LEFT JOIN invoices i ON i.job_id = j.id "
                                        "WHERE j.status = 'done' AND i.id IS NULL ORDER BY j.start"),
                "drafts": self.rows("SELECT id, customer, total FROM invoices WHERE status = 'draft'"),
                "overdue": self.overdue(d.isoformat()), "renewals": self.renewals(d.isoformat())}
