"""A tax practice's desk: clients, their returns, the documents still missing,
the chasers, the workpapers and the assessments - one folder.

    <root>/practice.json   checklists per return type, chase spacing, deadline rules
    <root>/practice.sqlite clients, returns, documents, chases
    <root>/Chasers  Workpapers

No tax law lives here. The practitioner sets each return's deadline (or a
rule: months after the period end) and the documents each kind of return
needs; the pack keeps the calendar, chases clients in escalating steps a
week apart, totals a client's statement into a workpaper that ties out,
and compares the tax assessed with the tax filed. Filing happens in the
authority's portal, by the practitioner.
"""
from __future__ import annotations

import calendar
import csv
import os
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from typing import Any

from extensions.packkit import Store, slug

STAGES = ("not_started", "waiting_docs", "preparing", "review", "ready", "filed", "assessed")
OPEN = STAGES[:5]
CHECKLISTS = {
    "individual": ["Employment income summary", "Bank interest", "Dividends", "Pension contributions",
                   "Gift Aid or charitable donations", "Other income"],
    "company": ["Trial balance", "Bank statements to the year end", "Fixed asset additions", "Loans and director's account"],
    "vat": ["Sales listing", "Purchase listing", "Bank statements"],
}
TONES = {1: "friendly", 2: "firm", 3: "final"}


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


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    return date(d.year + y, m + 1, min(d.day, calendar.monthrange(d.year + y, m + 1)[1]))


class Practice(Store):
    SETTINGS_FILE = "practice.json"
    DB = "practice.sqlite"
    FOLDERS = ("Chasers", "Workpapers")
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS clients (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        email TEXT NOT NULL DEFAULT '', reference TEXT NOT NULL DEFAULT '', created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS returns (id INTEGER PRIMARY KEY, client_id INTEGER NOT NULL, kind TEXT NOT NULL,
        year TEXT NOT NULL, period_end TEXT NOT NULL, due TEXT NOT NULL, stage TEXT NOT NULL DEFAULT 'waiting_docs',
        filed TEXT NOT NULL DEFAULT '', filed_tax REAL, assessed_tax REAL, created TEXT NOT NULL,
        UNIQUE(client_id, kind, year));
    CREATE TABLE IF NOT EXISTS docs (id INTEGER PRIMARY KEY, return_id INTEGER NOT NULL, name TEXT NOT NULL,
        received TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', UNIQUE(return_id, name));
    CREATE TABLE IF NOT EXISTS chases (id INTEGER PRIMARY KEY, return_id INTEGER NOT NULL, at TEXT NOT NULL,
        level INTEGER NOT NULL, path TEXT NOT NULL);
    """

    def num(self, key: str, default: float) -> float:
        v = self.cfg.get(key)
        return float(default if v in (None, "") else v)

    @property
    def firm(self) -> str:
        return self.cfg.get("firm") or "Your tax adviser"

    # -- clients and returns -----------------------------------------------------------------------------------

    def client(self, name: str, *, email: str = "", reference: str = "", create: bool = False) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise ValueError("which client?")
        r = self.one("SELECT * FROM clients WHERE name = ?", (name,))
        if r is None and not create:
            found = self.rows("SELECT * FROM clients WHERE name LIKE ? OR reference = ?", (f"%{name}%", name))
            if len(found) > 1:
                raise ValueError(f"'{name}' matches {', '.join(c['name'] for c in found[:5])} - which client?")
            r = found[0] if found else None
            if r is None:
                raise KeyError(f"no client {name!r}")
        if r is None:
            if email and "@" not in email:
                raise ValueError(f"{email!r} is not an email address")
            self.write("INSERT INTO clients(name, email, reference, created) VALUES (?, ?, ?, ?)",
                       (name, email.strip(), reference.strip(), _now()))
            r = self.one("SELECT * FROM clients WHERE name = ?", (name,))
        elif email or reference:
            self.write("UPDATE clients SET email = COALESCE(NULLIF(?, ''), email), reference = COALESCE(NULLIF(?, ''), "
                       "reference) WHERE id = ?", (email.strip(), reference.strip(), r["id"]))
            r = self.one("SELECT * FROM clients WHERE id = ?", (r["id"],))
        return r

    def add_return(self, client: str, kind: str, year: str, period_end: str, *, due: str = "", email: str = "",
                   documents: list[str] | None = None) -> dict[str, Any]:
        kind = (kind or "").lower().strip()
        if not kind or not str(year).strip():
            raise ValueError("a return needs its kind and year")
        end = _day(period_end)
        rule = (self.cfg.get("deadline_rules") or {}).get(kind)
        if due:
            deadline = _day(due)
        elif rule:
            deadline = add_months(end, int(rule.get("months", 0))) + timedelta(days=int(rule.get("days", 0)))
        else:
            raise ValueError(f"when is a {kind} return due? Give the date, or set a deadline rule for {kind} returns")
        if deadline <= end:
            raise ValueError("the deadline must be after the period end")
        c = self.client(client, email=email, create=True)
        if self.one("SELECT 1 FROM returns WHERE client_id = ? AND kind = ? AND year = ?", (c["id"], kind, str(year))):
            raise ValueError(f"{c['name']} already has a {kind} return for {year}")
        rid = self.write("INSERT INTO returns(client_id, kind, year, period_end, due, created) VALUES (?, ?, ?, ?, ?, ?)",
                         (c["id"], kind, str(year).strip(), end.isoformat(), deadline.isoformat(), _now()))
        wanted = documents if documents is not None else (
            (self.cfg.get("checklists") or {}).get(kind) or CHECKLISTS.get(kind, []))
        for name in wanted:
            if str(name).strip():
                self.write("INSERT OR IGNORE INTO docs(return_id, name) VALUES (?, ?)", (rid, str(name).strip()))
        return self.ret(rid)

    def ret(self, ref: Any, kind: str = "", year: str = "") -> dict[str, Any]:
        if str(ref).isdigit():
            r = self.one("SELECT r.*, c.name AS client, c.email FROM returns r JOIN clients c ON c.id = r.client_id "
                         "WHERE r.id = ?", (int(ref),))
        else:
            c = self.client(str(ref))
            sql = ("SELECT r.*, c.name AS client, c.email FROM returns r JOIN clients c ON c.id = r.client_id "
                   "WHERE r.client_id = ?")
            params: list[Any] = [c["id"]]
            if kind:
                sql += " AND r.kind = ?"; params.append(kind.lower())
            if year:
                sql += " AND r.year = ?"; params.append(str(year))
            rows = self.rows(sql + " ORDER BY r.due DESC", params)
            open_rows = [x for x in rows if x["stage"] in OPEN] or rows
            if len(open_rows) > 1:
                raise ValueError(f"{c['name']} has {len(open_rows)} returns ("
                                 + ", ".join(f"{x['kind']} {x['year']}" for x in open_rows) + ") - which one?")
            r = open_rows[0] if open_rows else None
        if r is None:
            raise KeyError(f"no return {ref!r}")
        r["documents"] = self.rows("SELECT name, received, note FROM docs WHERE return_id = ? ORDER BY id", (r["id"],))
        r["missing"] = [d["name"] for d in r["documents"] if not d["received"]]
        return r

    def returns(self, *, open_only: bool = True) -> list[dict[str, Any]]:
        rows = self.rows("SELECT id FROM returns" + (" WHERE stage IN ('not_started', 'waiting_docs', 'preparing', "
                                                      "'review', 'ready')" if open_only else "") + " ORDER BY due, id")
        return [self.ret(r["id"]) for r in rows]

    def receive(self, ref: Any, names: list[str], *, note: str = "", when: str = "") -> dict[str, Any]:
        r = self.ret(ref)
        got = _day(when, date.today()).isoformat()
        unknown = []
        for name in names or []:
            match = [d["name"] for d in r["documents"] if d["name"].lower() == str(name).strip().lower()] or \
                    [d["name"] for d in r["documents"] if str(name).strip().lower() in d["name"].lower()]
            if len(match) != 1:
                unknown.append(str(name))
                continue
            self.write("UPDATE docs SET received = ?, note = ? WHERE return_id = ? AND name = ?",
                       (got, note.strip(), r["id"], match[0]))
        r = self.ret(r["id"])
        if not r["missing"] and r["stage"] == "waiting_docs":
            self.write("UPDATE returns SET stage = 'preparing' WHERE id = ?", (r["id"],))
            r = self.ret(r["id"])
        r["unknown"] = unknown
        return r

    def set_stage(self, ref: Any, stage: str, *, filed_tax: Any = None, when: str = "") -> dict[str, Any]:
        stage = (stage or "").lower().strip()
        if stage not in STAGES or stage == "assessed":
            raise ValueError(f"stage is one of {', '.join(s for s in STAGES if s != 'assessed')}")
        r = self.ret(ref)
        if stage == "filed":
            if filed_tax in (None, ""):
                raise ValueError("record the tax on the return as filed - the assessment is checked against it")
            if r["missing"]:
                raise ValueError(f"still missing: {', '.join(r['missing'])} - mark them received or remove them first")
            self.write("UPDATE returns SET stage = 'filed', filed = ?, filed_tax = ? WHERE id = ?",
                       (_day(when, date.today()).isoformat(), float(filed_tax), r["id"]))
        else:
            self.write("UPDATE returns SET stage = ? WHERE id = ?", (stage, r["id"]))
        return self.ret(r["id"])

    def assessment(self, ref: Any, assessed_tax: Any) -> dict[str, Any]:
        r = self.ret(ref)
        if r["filed_tax"] is None:
            raise ValueError(f"{r['client']}'s {r['kind']} {r['year']} return is not filed yet")
        assessed = float(assessed_tax)
        self.write("UPDATE returns SET stage = 'assessed', assessed_tax = ? WHERE id = ?", (assessed, r["id"]))
        diff = round(assessed - r["filed_tax"], 2)
        return {**self.ret(r["id"]), "difference": diff,
                "agrees": abs(diff) <= self.num("assessment_tolerance", 1.0)}

    # -- chasing ----------------------------------------------------------------------------------------------

    def chase(self, as_of: str = "") -> dict[str, Any]:
        """One chaser per return still missing documents, if the last was a week ago or more.
        Each is firmer than the one before: friendly, firm, then final."""
        today = _day(as_of, date.today())
        spacing = int(self.num("chase_days", 7))
        sent, waiting, exhausted = [], [], []
        for r in self.returns():
            if not r["missing"] or r["stage"] not in ("not_started", "waiting_docs"):
                continue
            last = self.one("SELECT max(at) AS at, count(*) AS n FROM chases WHERE return_id = ?", (r["id"],))
            if last["at"] and (today - date.fromisoformat(last["at"][:10])).days < spacing:
                waiting.append(r)
                continue
            level = (last["n"] or 0) + 1
            if level > 3:
                exhausted.append(r)
                continue
            path = self._chaser(r, level, today)
            self.write("INSERT INTO chases(return_id, at, level, path) VALUES (?, ?, ?, ?)",
                       (r["id"], today.isoformat() + "T09:00:00", level, path))
            sent.append({"client": r["client"], "return": f"{r['kind']} {r['year']}", "level": level,
                         "tone": TONES[level], "missing": r["missing"], "path": path, "due": r["due"]})
        return {"drafted": sent, "waiting": [x["client"] for x in waiting],
                "exhausted": [{"client": x["client"], "return": f"{x['kind']} {x['year']}", "due": x["due"]}
                              for x in exhausted]}

    def _chaser(self, r: dict[str, Any], level: int, today: date) -> str:
        items = "\n".join(f"  - {m}" for m in r["missing"])
        days = (date.fromisoformat(r["due"]) - today).days
        opening = {1: "To get your return done in good time, I still need:",
                   2: f"I am still waiting for the following, and your {r['kind']} return is due on {r['due']}:",
                   3: f"This is my last reminder. Your {r['kind']} return is due on {r['due']} - in {days} day(s) - "
                      "and I cannot finish it without:"}[level]
        closing = {1: "A photo or scan is fine.", 2: "Could you send them this week?",
                   3: "If they do not reach me soon, the return may be late, and late returns can bring penalties. "
                      "Please call me if anything is holding you up."}[level]
        msg = EmailMessage()
        msg["To"] = r["email"] or r["client"]
        msg["Subject"] = f"Your {r['kind']} return {r['year']}: documents needed" + (" (final reminder)" if level == 3 else "")
        msg["X-Unsent"] = "1"
        msg.set_content(f"Dear {r['client']},\n\n{opening}\n\n{items}\n\n{closing}\n\nKind regards,\n{self.firm}\n")
        folder = self.folder(os.path.join("Chasers", slug(r["client"])))
        path = os.path.join(folder, f"{today.isoformat()} {r['kind']}-{slug(r['year'])} chaser {level}.eml")
        with open(path, "wb") as fh:
            fh.write(bytes(msg))
        return path

    # -- workpapers ---------------------------------------------------------------------------------------------

    def workpaper(self, ref: Any, path: str) -> dict[str, Any]:
        """A client's transactions (CSV: date, description, amount, optional category) totalled by
        category; anything without one is listed, and the categories must add back to the whole."""
        r = self.ret(ref)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"no file {path}")
        rules = {k: [w.lower() for w in v] for k, v in (self.cfg.get("category_words") or {
            "Interest": ["interest"], "Dividends": ["dividend"], "Salary": ["salary", "payroll", "wages"],
            "Rent received": ["rent from", "rental income"], "Pension": ["pension"],
            "Donations": ["donation", "charity"]}).items()}
        totals: dict[str, float] = {}
        loose, whole, n = [], 0.0, 0
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for raw in csv.DictReader(fh):
                row = {(k or "").lower().strip(): (v or "").strip() for k, v in raw.items()}
                amount_text = (row.get("amount") or "").replace(",", "")
                if not amount_text:
                    continue
                amount = float(amount_text)
                desc = row.get("description") or row.get("details") or ""
                cat = row.get("category") or next((k for k, words in rules.items()
                                                   if any(w in desc.lower() for w in words)), "")
                n += 1
                whole += amount
                if not cat:
                    loose.append({"date": row.get("date", ""), "description": desc, "amount": amount})
                    cat = "Not yet categorised"
                totals[cat] = round(totals.get(cat, 0.0) + amount, 2)
        if not n:
            raise ValueError("no transactions with an amount in the file")
        folder = self.folder(os.path.join("Workpapers", slug(r["client"])))
        out = os.path.join(folder, f"{r['kind']}-{slug(r['year'])} workpaper.csv")
        with open(out, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["category", "total"])
            for cat, amt in sorted(totals.items()):
                w.writerow([cat, f"{amt:.2f}"])
            w.writerow(["TOTAL", f"{round(whole, 2):.2f}"])
        return {"client": r["client"], "return": f"{r['kind']} {r['year']}", "path": out, "totals": totals,
                "transactions": n, "total": round(whole, 2), "uncategorised": loose,
                "ties_out": round(sum(totals.values()), 2) == round(whole, 2)}

    def overview(self, as_of: str = "") -> dict[str, Any]:
        today = _day(as_of, date.today())
        rows = self.returns()
        for r in rows:
            r["days_left"] = (date.fromisoformat(r["due"]) - today).days
        urgent = int(self.num("urgent_days", 30))
        return {"as_of": today.isoformat(), "open": rows,
                "late": [r for r in rows if r["days_left"] < 0],
                "urgent": [r for r in rows if 0 <= r["days_left"] <= urgent and r["missing"]],
                "by_stage": {s: sum(1 for r in rows if r["stage"] == s) for s in OPEN},
                "to_assess": self.rows("SELECT r.id, c.name AS client, r.kind, r.year, r.filed FROM returns r JOIN "
                                       "clients c ON c.id = r.client_id WHERE r.stage = 'filed' ORDER BY r.filed")}
