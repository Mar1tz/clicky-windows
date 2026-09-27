"""A small sales CRM in one folder: contacts, deals, what happened, what is next.

    <root>/sales.json   currency, days before a deal counts as stale, stage odds
    <root>/crm.sqlite   contacts, deals, activities
    <root>/Outbox  Imports  Reports

The arithmetic a salesperson does in their head, done properly and with no
model: the pipeline by stage, a forecast weighted by each stage's odds, the
follow-ups due, the deals nobody has touched, the win rate and how long a
deal takes. Follow-up emails are .eml drafts in Outbox; nothing is sent.
"""
from __future__ import annotations

import csv
import os
from datetime import date, datetime
from email.message import EmailMessage
from typing import Any, Optional

from extensions.packkit import Store, slug

STAGES = ("lead", "qualified", "proposal", "negotiation", "won", "lost")
OPEN = ("lead", "qualified", "proposal", "negotiation")
ODDS = {"lead": 10, "qualified": 25, "proposal": 50, "negotiation": 75, "won": 100, "lost": 0}
KINDS = ("call", "email", "meeting", "note")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _day(value: Any, default: Optional[date] = None) -> date:
    if not value:
        if default is None:
            raise ValueError("a date is needed (YYYY-MM-DD)")
        return default
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        raise ValueError(f"{value!r} is not a date (YYYY-MM-DD)") from None


class CRM(Store):
    SETTINGS_FILE = "sales.json"
    DB = "crm.sqlite"
    FOLDERS = ("Outbox", "Imports", "Reports")
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS contacts (id INTEGER PRIMARY KEY, name TEXT NOT NULL, company TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
        created TEXT NOT NULL);
    CREATE UNIQUE INDEX IF NOT EXISTS contact_key ON contacts(lower(name), lower(company));
    CREATE TABLE IF NOT EXISTS deals (id INTEGER PRIMARY KEY, title TEXT NOT NULL, contact_id INTEGER,
        company TEXT NOT NULL DEFAULT '', value REAL NOT NULL DEFAULT 0, stage TEXT NOT NULL DEFAULT 'lead',
        expected TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, closed TEXT NOT NULL DEFAULT '',
        lost_reason TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS activities (id INTEGER PRIMARY KEY, deal_id INTEGER, contact_id INTEGER,
        kind TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', at TEXT NOT NULL, next_date TEXT NOT NULL DEFAULT '',
        next_done INTEGER NOT NULL DEFAULT 0);
    """

    @property
    def currency(self) -> str:
        return self.cfg.get("currency") or "USD"

    @property
    def stale_days(self) -> int:
        return int(self.cfg.get("stale_days") or 14)

    def odds(self, stage: str) -> int:
        return int((self.cfg.get("odds") or {}).get(stage, ODDS[stage]))

    def money(self, v: float) -> str:
        return f"{self.currency} {v:,.0f}"

    # -- contacts -------------------------------------------------------------------------------------------

    def add_contact(self, name: str, *, company: str = "", email: str = "", phone: str = "",
                    notes: str = "") -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise ValueError("a contact needs a name")
        if email and "@" not in email:
            raise ValueError(f"{email!r} is not an email address")
        found = self.one("SELECT id FROM contacts WHERE lower(name) = lower(?) AND lower(company) = lower(?)",
                         (name, company.strip()))
        if found:
            self.write("UPDATE contacts SET email = COALESCE(NULLIF(?, ''), email), phone = COALESCE(NULLIF(?, ''), "
                       "phone), notes = COALESCE(NULLIF(?, ''), notes) WHERE id = ?", (email, phone, notes, found["id"]))
            return self.contact(found["id"])
        cid = self.write("INSERT INTO contacts(name, company, email, phone, notes, created) VALUES (?, ?, ?, ?, ?, ?)",
                         (name, company.strip(), email.strip(), phone.strip(), notes.strip(), _now()))
        return self.contact(cid)

    def contact(self, ref: Any) -> dict[str, Any]:
        r = self.one("SELECT * FROM contacts WHERE id = ?", (int(ref),)) if str(ref).strip().isdigit() else None
        if r is None:
            rows = self.rows("SELECT * FROM contacts WHERE name LIKE ? OR email = ? OR company LIKE ?",
                             (f"%{str(ref).strip()}%", str(ref).strip(), f"%{str(ref).strip()}%"))
            exact = [c for c in rows if c["name"].lower() == str(ref).strip().lower()]
            rows = exact or rows
            if len(rows) > 1:
                raise ValueError(f"'{ref}' matches {', '.join(c['name'] + (' (' + c['company'] + ')' if c['company'] else '') for c in rows[:5])} - which one?")
            r = rows[0] if rows else None
        if r is None:
            raise KeyError(f"no contact {ref!r}")
        return r

    def import_contacts(self, path: str) -> dict[str, int]:
        """A CSV from HubSpot, Pipedrive, Outlook or a spreadsheet: name (or first +
        last name), company/organization, email, phone. Existing people are updated."""
        if not os.path.isfile(path):
            raise FileNotFoundError(f"no file {path}")
        added = updated = skipped = 0
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            cols = {k.lower().strip(): k for k in reader.fieldnames or []}
            pick = lambda row, *names: next((row.get(cols[n]) or "" for n in names if n in cols), "")  # noqa: E731
            for row in reader:
                name = pick(row, "name", "full name", "contact name").strip() or " ".join(
                    p for p in (pick(row, "first name", "firstname").strip(), pick(row, "last name", "lastname").strip()) if p)
                if not name:
                    skipped += 1
                    continue
                company = pick(row, "company", "company name", "organization", "organisation", "account").strip()
                existed = self.one("SELECT 1 FROM contacts WHERE lower(name) = lower(?) AND lower(company) = lower(?)",
                                   (name, company))
                email = pick(row, "email", "e-mail", "email address", "e-mail address").strip()
                self.add_contact(name, company=company, email=email if "@" in email else "",
                                 phone=pick(row, "phone", "phone number", "mobile").strip())
                added, updated = (added, updated + 1) if existed else (added + 1, updated)
        return {"added": added, "updated": updated, "skipped": skipped}

    # -- deals ------------------------------------------------------------------------------------------------

    def add_deal(self, title: str, *, contact: Any = None, company: str = "", value: Any = 0, stage: str = "lead",
                 expected: str = "") -> dict[str, Any]:
        title = (title or "").strip()
        if not title:
            raise ValueError("a deal needs a name")
        if stage not in OPEN:
            raise ValueError(f"a new deal starts at one of {', '.join(OPEN)}")
        value = float(value or 0)
        if value < 0:
            raise ValueError("the value cannot be negative")
        c = self.contact(contact) if contact not in (None, "") else None
        did = self.write("INSERT INTO deals(title, contact_id, company, value, stage, expected, created) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (title, c["id"] if c else None, company.strip() or (c["company"] if c else ""), value, stage,
                          _day(expected).isoformat() if expected else "", _now()))
        return self.deal(did)

    def deal(self, ref: Any) -> dict[str, Any]:
        r = self.one("SELECT * FROM deals WHERE id = ?", (int(ref),)) if str(ref).strip().isdigit() else None
        if r is None:
            rows = self.rows("SELECT * FROM deals WHERE title LIKE ? OR company LIKE ? ORDER BY stage IN ('won', 'lost')",
                             (f"%{str(ref).strip()}%", f"%{str(ref).strip()}%"))
            live = [d for d in rows if d["stage"] in OPEN] or rows
            if len(live) > 1:
                raise ValueError(f"'{ref}' matches {', '.join(d['title'] for d in live[:5])} - which one?")
            r = live[0] if live else None
        if r is None:
            raise KeyError(f"no deal {ref!r}")
        last = self.one("SELECT max(at) AS at FROM activities WHERE deal_id = ?", (r["id"],))
        r["last_touch"] = (last or {}).get("at") or r["created"]
        r["odds"] = self.odds(r["stage"])
        if r["contact_id"]:
            r["contact"] = self.contact(r["contact_id"])["name"]
        return r

    def deals(self, stage: str = "") -> list[dict[str, Any]]:
        if stage and stage not in STAGES and stage != "open":
            raise ValueError(f"stage is one of {', '.join(STAGES)} or open")
        where = {"": "", "open": " WHERE stage IN ('lead', 'qualified', 'proposal', 'negotiation')"}.get(
            stage, " WHERE stage = ?")
        rows = self.rows("SELECT id FROM deals" + where + " ORDER BY expected = '', expected, id",
                         (stage,) if where.endswith("?") else ())
        return [self.deal(r["id"]) for r in rows]

    def move(self, ref: Any, stage: str, *, reason: str = "", when: str = "") -> dict[str, Any]:
        stage = (stage or "").lower().strip()
        if stage not in STAGES:
            raise ValueError(f"stage is one of {', '.join(STAGES)}")
        d = self.deal(ref)
        if stage == "lost" and not reason.strip():
            raise ValueError("say why it was lost - it is the most useful thing a CRM remembers")
        closed = _day(when, date.today()).isoformat() if stage in ("won", "lost") else ""
        self.write("UPDATE deals SET stage = ?, closed = ?, lost_reason = ? WHERE id = ?",
                   (stage, closed, reason.strip() if stage == "lost" else "", d["id"]))
        self.log(d["id"], "note", f"Moved to {stage}" + (f": {reason.strip()}" if reason.strip() else ""))
        return self.deal(d["id"])

    # -- activities -------------------------------------------------------------------------------------------

    def log(self, deal: Any = None, kind: str = "note", note: str = "", *, contact: Any = None,
            next_date: str = "", when: str = "") -> dict[str, Any]:
        kind = (kind or "note").lower()
        if kind not in KINDS:
            raise ValueError(f"kind is one of {', '.join(KINDS)}")
        d = self.deal(deal) if deal not in (None, "") else None
        c = self.contact(contact) if contact not in (None, "") else (
            self.contact(d["contact_id"]) if d and d["contact_id"] else None)
        if d is None and c is None:
            raise ValueError("log it against a deal or a contact")
        at = (_day(when).isoformat() + "T12:00:00") if when else _now()
        with self.lock, self.db:
            if d is not None:
                self.db.execute("UPDATE activities SET next_done = 1 WHERE deal_id = ? AND next_done = 0", (d["id"],))
            elif c is not None:
                self.db.execute("UPDATE activities SET next_done = 1 WHERE contact_id = ? AND deal_id IS NULL "
                                "AND next_done = 0", (c["id"],))
            aid = self.db.execute("INSERT INTO activities(deal_id, contact_id, kind, note, at, next_date) "
                                  "VALUES (?, ?, ?, ?, ?, ?)",
                                  (d["id"] if d else None, c["id"] if c else None, kind, note.strip(), at,
                                   _day(next_date).isoformat() if next_date else "")).lastrowid
        return self.one("SELECT * FROM activities WHERE id = ?", (aid,)) or {}

    def follow_ups(self, as_of: str = "") -> list[dict[str, Any]]:
        today = _day(as_of, date.today())
        rows = self.rows("SELECT a.*, d.title AS deal, c.name AS contact FROM activities a "
                         "LEFT JOIN deals d ON d.id = a.deal_id LEFT JOIN contacts c ON c.id = a.contact_id "
                         "WHERE a.next_done = 0 AND a.next_date != '' AND a.next_date <= ? "
                         "AND (d.stage IS NULL OR d.stage IN ('lead', 'qualified', 'proposal', 'negotiation')) "
                         "ORDER BY a.next_date", (today.isoformat(),))
        for r in rows:
            r["days_late"] = (today - date.fromisoformat(r["next_date"])).days
        return rows

    def stale(self, as_of: str = "") -> list[dict[str, Any]]:
        today = _day(as_of, date.today())
        out = []
        for d in self.deals("open"):
            idle = (today - date.fromisoformat(d["last_touch"][:10])).days
            planned = self.one("SELECT 1 FROM activities WHERE deal_id = ? AND next_done = 0 AND next_date >= ?",
                               (d["id"], today.isoformat()))
            if idle >= self.stale_days and not planned:
                out.append({**d, "idle_days": idle})
        return sorted(out, key=lambda d: -d["value"])

    # -- numbers ------------------------------------------------------------------------------------------------

    def pipeline(self) -> dict[str, Any]:
        by = {s: {"count": 0, "value": 0.0} for s in STAGES}
        for r in self.rows("SELECT stage, count(*) AS n, sum(value) AS v FROM deals GROUP BY stage"):
            by[r["stage"]] = {"count": r["n"], "value": r["v"] or 0.0}
        won, lost = by["won"]["count"], by["lost"]["count"]
        cycles = [(date.fromisoformat(r["closed"]) - date.fromisoformat(r["created"][:10])).days
                  for r in self.rows("SELECT created, closed FROM deals WHERE stage = 'won' AND closed != ''")]
        return {"stages": by, "open_value": sum(by[s]["value"] for s in OPEN),
                "weighted": round(sum(by[s]["value"] * self.odds(s) / 100 for s in OPEN), 2),
                "win_rate": round(won / (won + lost) * 100) if won + lost else None,
                "avg_days_to_win": round(sum(cycles) / len(cycles)) if cycles else None}

    def forecast(self, month: str = "") -> dict[str, Any]:
        month = (month or date.today().isoformat()[:7]).strip()[:7]
        if len(month) != 7 or month[4] != "-":
            raise ValueError("month is YYYY-MM")
        rows = [self.deal(r["id"]) for r in self.rows(
            "SELECT id FROM deals WHERE stage IN ('lead', 'qualified', 'proposal', 'negotiation') AND expected LIKE ?",
            (f"{month}%",))]
        won = self.rows("SELECT value FROM deals WHERE stage = 'won' AND closed LIKE ?", (f"{month}%",))
        undated = self.one("SELECT count(*) AS n FROM deals WHERE stage IN ('lead', 'qualified', 'proposal', "
                           "'negotiation') AND expected = ''")["n"]
        return {"month": month, "deals": rows, "best_case": sum(d["value"] for d in rows),
                "weighted": round(sum(d["value"] * d["odds"] / 100 for d in rows), 2),
                "won": sum(r["value"] for r in won), "undated": undated}

    # -- email drafts ---------------------------------------------------------------------------------------------

    def draft_email(self, deal: Any, subject: str, body: str) -> dict[str, Any]:
        d = self.deal(deal)
        if not d["contact_id"]:
            raise ValueError(f"'{d['title']}' has no contact to write to")
        c = self.contact(d["contact_id"])
        if not (body or "").strip():
            raise ValueError("the email needs a body")
        msg = EmailMessage()
        msg["To"] = c["email"] or c["name"]
        if self.cfg.get("email"):
            msg["From"] = self.cfg["email"]
        msg["Subject"] = (subject or f"Following up: {d['title']}").strip()
        msg["X-Unsent"] = "1"
        msg.set_content(body.strip() + "\n")
        path = os.path.join(self.folder("Outbox"),
                            f"{datetime.now().strftime('%Y%m%d-%H%M%S')} {slug(c['name'])} {slug(d['title'])}.eml")
        with open(path, "wb") as fh:
            fh.write(bytes(msg))
        return {"path": path, "to": msg["To"], "subject": msg["Subject"], "deal": d["title"],
                "no_email": not c["email"]}

    def overview(self, as_of: str = "") -> dict[str, Any]:
        today = _day(as_of, date.today())
        p = self.pipeline()
        return {"as_of": today.isoformat(), "pipeline": p, "follow_ups": self.follow_ups(today.isoformat()),
                "stale": self.stale(today.isoformat()), "forecast": self.forecast(today.isoformat()[:7]),
                "contacts": self.one("SELECT count(*) AS n FROM contacts")["n"],
                "closing_soon": [d for d in self.deals("open") if d["expected"]
                                 and 0 <= (date.fromisoformat(d["expected"]) - today).days <= 7]}
