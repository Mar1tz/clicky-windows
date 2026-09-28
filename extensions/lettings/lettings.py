"""A letting agent's or landlord's desk: properties, tenancies, rent, repairs,
renewals, certificates and deposits - one folder.

    <root>/lettings.json   currency, repair targets, letter steps, renewal warning
    <root>/lettings.sqlite properties, tenancies, payments, contractors, repairs, certificates, deposits, letters
    <root>/Letters  Works orders  Statements

The rent roll is arithmetic: rent falls due on the tenancy's day each
month, payments (matched from the bank by reference or surname) pay off
the oldest month first, and whatever is left is arrears, dated from the
oldest unpaid month. Arrears letters escalate - reminder, second, final -
but no legal notice is ever drafted: those depend on the law where the
property is, so the pack says so and stops. Deposits are settled only
with the landlord's approval. Nothing is sent or paid from here.
"""
from __future__ import annotations

import calendar
import csv
import hashlib
import json
import os
import re
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from typing import Any

from extensions.packkit import Store, slug

PRIORITIES = ("urgent", "high", "normal")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _day(value: Any, default: date | None = None) -> date:
    if not value:
        if default is None:
            raise ValueError("a date is needed (YYYY-MM-DD)")
        return default
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        raise ValueError(f"{value!r} is not a date (YYYY-MM-DD)") from None


def _due(year: int, month: int, day_: int) -> date:
    return date(year, month, min(day_, calendar.monthrange(year, month)[1]))



class Lettings(Store):
    SETTINGS_FILE = "lettings.json"
    DB = "lettings.sqlite"
    FOLDERS = ("Letters", "Works orders", "Statements")
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS properties (id INTEGER PRIMARY KEY, address TEXT NOT NULL UNIQUE COLLATE NOCASE,
        landlord TEXT NOT NULL DEFAULT '', landlord_email TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS tenancies (id INTEGER PRIMARY KEY, ref TEXT NOT NULL UNIQUE COLLATE NOCASE,
        property_id INTEGER NOT NULL, tenants TEXT NOT NULL, email TEXT NOT NULL DEFAULT '', rent REAL NOT NULL,
        due_day INTEGER NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL DEFAULT '', deposit REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'active');
    CREATE TABLE IF NOT EXISTS payments (id INTEGER PRIMARY KEY, tenancy_id INTEGER NOT NULL, date TEXT NOT NULL,
        amount REAL NOT NULL, note TEXT NOT NULL DEFAULT '', fingerprint TEXT NOT NULL UNIQUE);
    CREATE TABLE IF NOT EXISTS contractors (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        trade TEXT NOT NULL, email TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS repairs (id INTEGER PRIMARY KEY, property_id INTEGER NOT NULL, issue TEXT NOT NULL,
        priority TEXT NOT NULL, reported TEXT NOT NULL, contractor_id INTEGER, status TEXT NOT NULL DEFAULT 'open',
        done TEXT NOT NULL DEFAULT '', cost REAL);
    CREATE TABLE IF NOT EXISTS certs (id INTEGER PRIMARY KEY, property_id INTEGER NOT NULL, kind TEXT NOT NULL,
        expires TEXT NOT NULL, UNIQUE(property_id, kind));
    CREATE TABLE IF NOT EXISTS deposits (id INTEGER PRIMARY KEY, tenancy_id INTEGER NOT NULL UNIQUE, amount REAL NOT NULL,
        deductions TEXT NOT NULL, to_tenant REAL NOT NULL, to_landlord REAL NOT NULL, status TEXT NOT NULL,
        settled TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS letters (id INTEGER PRIMARY KEY, tenancy_id INTEGER NOT NULL, kind TEXT NOT NULL,
        level INTEGER NOT NULL, at TEXT NOT NULL, path TEXT NOT NULL);
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
    def agent(self) -> str:
        return self.cfg.get("agent") or "Your letting agent"

    # -- properties and tenancies -------------------------------------------------------------------------------

    def prop(self, ref: str, *, landlord: str = "", landlord_email: str = "", create: bool = False) -> dict[str, Any]:
        ref = (ref or "").strip()
        if not ref:
            raise ValueError("which property?")
        r = self.one("SELECT * FROM properties WHERE address = ?", (ref,))
        if r is None:
            found = self.rows("SELECT * FROM properties WHERE address LIKE ?", (f"%{ref}%",))
            if len(found) > 1:
                raise ValueError(f"'{ref}' matches {', '.join(p['address'] for p in found[:5])} - which one?")
            r = found[0] if found else None
        if r is None:
            if not create:
                raise KeyError(f"no property {ref!r}")
            pid = self.write("INSERT INTO properties(address, landlord, landlord_email) VALUES (?, ?, ?)",
                             (ref, landlord.strip(), landlord_email.strip()))
            r = self.one("SELECT * FROM properties WHERE id = ?", (pid,))
        return r

    def let(self, ref: str, address: str, tenants: str, rent: Any, due_day: int, start: str, *, end: str = "",
            deposit: Any = 0, email: str = "", landlord: str = "", landlord_email: str = "") -> dict[str, Any]:
        ref, tenants = (ref or "").strip(), (tenants or "").strip()
        if not ref or not tenants:
            raise ValueError("a tenancy needs a reference and the tenants' names")
        rent, due_day = float(rent), int(due_day)
        if rent <= 0 or not 1 <= due_day <= 31:
            raise ValueError("rent above zero, due on a day of the month (1-31)")
        s = _day(start)
        e = _day(end) if end else None
        if e and e <= s:
            raise ValueError("the tenancy must end after it starts")
        p = self.prop(address, landlord=landlord, landlord_email=landlord_email, create=True)
        if self.one("SELECT 1 FROM tenancies WHERE property_id = ? AND status = 'active'", (p["id"],)):
            raise ValueError(f"{p['address']} already has an active tenancy")
        tid = self.write("INSERT INTO tenancies(ref, property_id, tenants, email, rent, due_day, start, end, deposit) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (ref, p["id"], tenants, email.strip(), rent, due_day,
                                                               s.isoformat(), e.isoformat() if e else "", float(deposit or 0)))
        return self.tenancy(tid)

    def tenancy(self, ref: Any) -> dict[str, Any]:
        sql = ("SELECT t.*, p.address, p.landlord, p.landlord_email FROM tenancies t JOIN properties p ON "
               "p.id = t.property_id WHERE ")
        r = self.one(sql + "t.id = ?", (int(ref),)) if str(ref).isdigit() else self.one(sql + "t.ref = ?", (str(ref),))
        if r is None:
            found = self.rows(sql + "(t.tenants LIKE ? OR p.address LIKE ?) ORDER BY t.status = 'active' DESC",
                              (f"%{ref}%", f"%{ref}%"))
            live = [x for x in found if x["status"] == "active"] or found
            if len(live) > 1:
                raise ValueError(f"'{ref}' matches {', '.join(x['ref'] for x in live[:5])} - which tenancy?")
            r = live[0] if live else None
        if r is None:
            raise KeyError(f"no tenancy {ref!r}")
        return r

    def tenancies(self) -> list[dict[str, Any]]:
        return [self.tenancy(r["id"]) for r in self.rows("SELECT id FROM tenancies WHERE status = 'active' ORDER BY ref")]

    # -- rent ----------------------------------------------------------------------------------------------------

    def record_payment(self, ref: Any, amount: Any, when: str = "", note: str = "") -> dict[str, Any]:
        t = self.tenancy(ref)
        amt = float(amount)
        if amt <= 0:
            raise ValueError("a payment is above zero")
        d = _day(when, date.today()).isoformat()
        fp = hashlib.sha1(f"{t['id']}|{d}|{amt:.2f}|{note}|{_now()}".encode()).hexdigest()
        self.write("INSERT INTO payments(tenancy_id, date, amount, note, fingerprint) VALUES (?, ?, ?, ?, ?)",
                   (t["id"], d, amt, note.strip(), fp))
        return self.balance(t["id"], d)

    def import_rent(self, path: str) -> dict[str, Any]:
        """Money in from a bank CSV (date, description, amount). A row is matched to a tenancy by its
        reference in the description, or else the tenant's surname; unmatched rows are listed."""
        if not os.path.isfile(path):
            raise FileNotFoundError(f"no file {path}")
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rows = [{(k or "").lower().strip(): (v or "").strip() for k, v in r.items()} for r in csv.DictReader(fh)]
        live = self.tenancies()
        matched, unmatched, dup = 0, [], 0
        for r in rows:
            amount = float((r.get("amount") or r.get("money in") or r.get("credit") or "0").replace(",", "") or 0)
            desc = r.get("description") or r.get("details") or r.get("reference") or ""
            if amount <= 0:
                continue
            low = desc.lower()
            hits = [t for t in live if t["ref"].lower() in low] or \
                   [t for t in live if any(len(w) > 2 and re.search(rf"\b{re.escape(w.lower())}\b", low)
                                          for name in t["tenants"].split(",") for w in name.split()[-1:])]
            if len(hits) != 1:
                unmatched.append({"date": r.get("date", ""), "description": desc, "amount": amount})
                continue
            d = _day(r.get("date")).isoformat()
            fp = hashlib.sha1(f"bank|{d}|{desc}|{amount:.2f}".encode()).hexdigest()
            if self.one("SELECT 1 FROM payments WHERE fingerprint = ?", (fp,)):
                dup += 1
                continue
            self.write("INSERT INTO payments(tenancy_id, date, amount, note, fingerprint) VALUES (?, ?, ?, ?, ?)",
                       (hits[0]["id"], d, amount, desc, fp))
            matched += 1
        return {"matched": matched, "already_there": dup, "unmatched": unmatched}

    def balance(self, ref: Any, as_of: str = "") -> dict[str, Any]:
        """Rent due each month on the due day from the start; payments pay the oldest month first."""
        t = self.tenancy(ref)
        today = _day(as_of, date.today())
        start = date.fromisoformat(t["start"])
        last = min(today, date.fromisoformat(t["end"])) if t["end"] else today
        dues, y, m = [], start.year, start.month
        while True:
            d = _due(y, m, t["due_day"])
            if d > last:
                break
            if d >= start:
                dues.append(d)
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        paid = self.one("SELECT coalesce(sum(amount), 0) AS s FROM payments WHERE tenancy_id = ? AND date <= ?",
                        (t["id"], today.isoformat()))["s"]
        left, oldest = paid, None
        for d in dues:
            if left + 1e-9 >= t["rent"]:
                left -= t["rent"]
            else:
                oldest = d
                break
        due_total = round(len(dues) * t["rent"], 2)
        arrears = round(max(0.0, due_total - paid), 2)
        return {"ref": t["ref"], "tenants": t["tenants"], "address": t["address"], "rent": t["rent"],
                "months_due": len(dues), "due": due_total, "paid": round(paid, 2), "arrears": arrears,
                "credit": round(max(0.0, paid - due_total), 2),
                "days_late": (today - oldest).days if oldest and arrears > 0 else 0,
                "oldest_unpaid": oldest.isoformat() if oldest and arrears > 0 else ""}

    def rent_roll(self, as_of: str = "") -> dict[str, Any]:
        rows = [self.balance(t["id"], as_of) for t in self.tenancies()]
        return {"tenancies": rows, "rent_a_month": round(sum(r["rent"] for r in rows), 2),
                "arrears": round(sum(r["arrears"] for r in rows), 2),
                "in_arrears": sorted([r for r in rows if r["arrears"] > 0], key=lambda r: -r["days_late"])}

    def arrears_letters(self, as_of: str = "") -> dict[str, Any]:
        """Reminder at 3 days late, second at 14, final at 30 - each step once per run of arrears."""
        today = _day(as_of, date.today())
        steps = [(int(self.num("reminder_days", 3)), 1), (int(self.num("second_days", 14)), 2),
                 (int(self.num("final_days", 30)), 3)]
        drafted, beyond = [], []
        for b in self.rent_roll(today.isoformat())["in_arrears"]:
            level = max((lvl for days, lvl in steps if b["days_late"] >= days), default=0)
            if not level:
                continue
            t = self.tenancy(b["ref"])
            since = b["oldest_unpaid"]
            sent = self.one("SELECT max(level) AS lvl FROM letters WHERE tenancy_id = ? AND kind = 'arrears' AND at >= ?",
                            (t["id"], since))["lvl"] or 0
            if level > sent:
                drafted.append({**b, "level": level, "path": self._arrears_letter(t, b, level, today)})
            if level == 3:
                beyond.append(b)
        return {"drafted": drafted, "final_stage": beyond}

    def _arrears_letter(self, t: dict[str, Any], b: dict[str, Any], level: int, today: date) -> str:
        body = {1: f"Our records show {self.money(b['arrears'])} of rent for {t['address']} is unpaid, due since "
                   f"{b['oldest_unpaid']}. If you have already paid, thank you - please let us know the date.",
                2: f"{self.money(b['arrears'])} of rent for {t['address']} is now {b['days_late']} days overdue. "
                   "Please pay it within seven days, or contact us to agree how it will be paid.",
                3: f"Despite our earlier letters, {self.money(b['arrears'])} of rent for {t['address']} remains unpaid "
                   f"after {b['days_late']} days. Please contact us urgently to agree a plan."}[level]
        msg = EmailMessage()
        msg["To"] = t["email"] or t["tenants"]
        msg["Subject"] = {1: "Rent reminder", 2: "Rent overdue", 3: "Rent arrears - please contact us"}[level] + \
            f" - {t['address']}"
        msg["X-Unsent"] = "1"
        msg.set_content(f"Dear {t['tenants']},\n\n{body}\n\nYours sincerely,\n{self.agent}\n")
        path = os.path.join(self.folder(os.path.join("Letters", slug(t["ref"]))),
                            f"{today.isoformat()} arrears {level}.eml")
        with open(path, "wb") as fh:
            fh.write(bytes(msg))
        self.write("INSERT INTO letters(tenancy_id, kind, level, at, path) VALUES (?, 'arrears', ?, ?, ?)",
                   (t["id"], level, today.isoformat(), path))
        return path

    # -- repairs ---------------------------------------------------------------------------------------------------

    def add_contractor(self, name: str, trade: str, *, email: str = "", phone: str = "") -> dict[str, Any]:
        if not (name or "").strip() or not (trade or "").strip():
            raise ValueError("a contractor needs a name and a trade")
        self.write("INSERT INTO contractors(name, trade, email, phone) VALUES (?, ?, ?, ?) ON CONFLICT(name) DO UPDATE "
                   "SET trade = excluded.trade, email = excluded.email, phone = excluded.phone",
                   (name.strip(), trade.strip().lower(), email.strip(), phone.strip()))
        return self.one("SELECT * FROM contractors WHERE name = ?", (name.strip(),)) or {}

    def report_repair(self, address: str, issue: str, priority: str = "normal", when: str = "") -> dict[str, Any]:
        priority = (priority or "normal").lower()
        if priority not in PRIORITIES:
            raise ValueError(f"priority is {', '.join(PRIORITIES)}")
        if not (issue or "").strip():
            raise ValueError("what is wrong?")
        p = self.prop(address)
        rid = self.write("INSERT INTO repairs(property_id, issue, priority, reported) VALUES (?, ?, ?, ?)",
                         (p["id"], issue.strip(), priority, _day(when, date.today()).isoformat()))
        return self.repair(rid)

    def repair(self, rid: Any) -> dict[str, Any]:
        r = self.one("SELECT r.*, p.address, c.name AS contractor, c.email AS contractor_email FROM repairs r "
                     "JOIN properties p ON p.id = r.property_id LEFT JOIN contractors c ON c.id = r.contractor_id "
                     "WHERE r.id = ?", (int(rid),))
        if r is None:
            raise KeyError(f"no repair #{rid}")
        r["target"] = (date.fromisoformat(r["reported"]) +
                       timedelta(days=int(self.num(f"{r['priority']}_days", {"urgent": 1, "high": 7, "normal": 28}[r["priority"]])))).isoformat()
        return r

    def assign(self, rid: Any, contractor: str = "", trade: str = "") -> dict[str, Any]:
        r = self.repair(rid)
        if r["status"] == "done":
            raise ValueError(f"repair #{r['id']} is done")
        if contractor:
            c = self.one("SELECT * FROM contractors WHERE name LIKE ?", (f"%{contractor.strip()}%",))
        else:
            c = self.one("SELECT * FROM contractors WHERE trade = ? ORDER BY id", ((trade or "").strip().lower(),))
        if c is None:
            raise KeyError(f"no contractor {contractor or trade!r}; add one first")
        self.write("UPDATE repairs SET contractor_id = ?, status = 'assigned' WHERE id = ?", (c["id"], r["id"]))
        r = self.repair(r["id"])
        msg = EmailMessage()
        msg["To"] = c["email"] or c["name"]
        msg["Subject"] = f"Works order #{r['id']} ({r['priority']}) - {r['address']}"
        msg["X-Unsent"] = "1"
        msg.set_content(f"Hello {c['name']},\n\nPlease attend {r['address']}:\n\n  {r['issue']}\n\nPriority: "
                        f"{r['priority']} - to be done by {r['target']}. Please reply with a date and a quote if "
                        f"over the agreed limit.\n\nThanks,\n{self.agent}\n")
        path = os.path.join(self.folder("Works orders"), f"{r['id']:04d} {slug(r['address'])}.eml")
        with open(path, "wb") as fh:
            fh.write(bytes(msg))
        return {**r, "works_order": path}

    def complete(self, rid: Any, cost: Any = None, when: str = "") -> dict[str, Any]:
        r = self.repair(rid)
        self.write("UPDATE repairs SET status = 'done', done = ?, cost = ? WHERE id = ?",
                   (_day(when, date.today()).isoformat(), float(cost) if cost not in (None, "") else None, r["id"]))
        return self.repair(r["id"])

    def open_repairs(self, as_of: str = "") -> list[dict[str, Any]]:
        today = _day(as_of, date.today())
        rows = [self.repair(r["id"]) for r in self.rows("SELECT id FROM repairs WHERE status != 'done' ORDER BY reported")]
        for r in rows:
            r["days_over"] = max(0, (today - date.fromisoformat(r["target"])).days)
        return rows

    # -- compliance, renewals, deposits ----------------------------------------------------------------------------

    def set_certificate(self, address: str, kind: str, expires: str) -> dict[str, Any]:
        p = self.prop(address)
        if not (kind or "").strip():
            raise ValueError("which certificate?")
        self.write("INSERT INTO certs(property_id, kind, expires) VALUES (?, ?, ?) ON CONFLICT(property_id, kind) "
                   "DO UPDATE SET expires = excluded.expires", (p["id"], kind.strip(), _day(expires).isoformat()))
        return {"address": p["address"], "kind": kind.strip(), "expires": _day(expires).isoformat()}

    def compliance(self, as_of: str = "", days: int = 30) -> list[dict[str, Any]]:
        today = _day(as_of, date.today())
        rows = self.rows("SELECT c.*, p.address FROM certs c JOIN properties p ON p.id = c.property_id WHERE c.expires <= ? "
                         "ORDER BY c.expires", ((today + timedelta(days=int(days))).isoformat(),))
        for r in rows:
            r["days_left"] = (date.fromisoformat(r["expires"]) - today).days
        return rows

    def renewals(self, as_of: str = "") -> list[dict[str, Any]]:
        today = _day(as_of, date.today())
        horizon = today + timedelta(days=int(self.num("renewal_days", 60)))
        return [dict(t, days_left=(date.fromisoformat(t["end"]) - today).days) for t in self.tenancies()
                if t["end"] and today <= date.fromisoformat(t["end"]) <= horizon]

    def propose_deposit(self, ref: Any, deductions: list[dict[str, Any]]) -> dict[str, Any]:
        t = self.tenancy(ref)
        if not t["deposit"]:
            raise ValueError(f"{t['ref']} has no deposit recorded")
        items = []
        for d in deductions or []:
            amount, reason = float(d.get("amount") or 0), str(d.get("reason") or "").strip()
            if amount <= 0 or not reason:
                raise ValueError("each deduction needs an amount above zero and the reason (with evidence)")
            items.append({"reason": reason, "amount": round(amount, 2)})
        taken = round(sum(i["amount"] for i in items), 2)
        if taken > t["deposit"]:
            raise ValueError(f"deductions of {self.money(taken)} are more than the {self.money(t['deposit'])} deposit")
        existing = self.one("SELECT * FROM deposits WHERE tenancy_id = ?", (t["id"],))
        if existing and existing["status"] == "settled":
            raise ValueError(f"{t['ref']}'s deposit is already settled")
        self.write("INSERT INTO deposits(tenancy_id, amount, deductions, to_tenant, to_landlord, status) VALUES "
                   "(?, ?, ?, ?, ?, 'proposed') ON CONFLICT(tenancy_id) DO UPDATE SET deductions = excluded.deductions, "
                   "to_tenant = excluded.to_tenant, to_landlord = excluded.to_landlord",
                   (t["id"], t["deposit"], json.dumps(items), round(t["deposit"] - taken, 2), taken))
        return self.deposit(t["ref"])

    def deposit(self, ref: Any) -> dict[str, Any]:
        t = self.tenancy(ref)
        r = self.one("SELECT * FROM deposits WHERE tenancy_id = ?", (t["id"],))
        if r is None:
            raise KeyError(f"no deposit proposal for {t['ref']}")
        r["deductions"] = json.loads(r["deductions"])
        r["ref"] = t["ref"]
        return r

    def settle_deposit(self, ref: Any, when: str = "") -> dict[str, Any]:
        d = self.deposit(ref)
        if d["status"] == "settled":
            raise ValueError(f"{d['ref']}'s deposit is already settled")
        self.write("UPDATE deposits SET status = 'settled', settled = ? WHERE id = ?",
                   (_day(when, date.today()).isoformat(), d["id"]))
        self.write("UPDATE tenancies SET status = 'ended' WHERE ref = ?", (d["ref"],))
        return self.deposit(d["ref"])

    def overview(self, as_of: str = "") -> dict[str, Any]:
        today = _day(as_of, date.today())
        roll = self.rent_roll(today.isoformat())
        repairs = self.open_repairs(today.isoformat())
        return {"as_of": today.isoformat(), "roll": roll, "repairs": repairs,
                "late_repairs": [r for r in repairs if r["days_over"] > 0],
                "unassigned": [r for r in repairs if r["status"] == "open"],
                "compliance": self.compliance(today.isoformat()), "renewals": self.renewals(today.isoformat())}
