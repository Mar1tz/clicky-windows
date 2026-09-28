"""A household's money: statements, categories, budgets, subscriptions, bills
and warranties - one folder.

    <root>/household.json  currency, category words, subscription rules
    <root>/money.sqlite    transactions, category rules, budgets, warranties
    <root>/Statements  Receipts  Reports

All arithmetic, no model: statements from any bank's CSV (one amount
column, or money in / money out) imported once however often they are
dropped in; each payment put in a category by the words in it or a rule
the household taught; spending against each month's budget; payments
that recur every month found as subscriptions and bills, with their
yearly cost and any price rise; warranties about to run out. Nothing is
paid or cancelled from here.
"""
from __future__ import annotations

import calendar
import csv
import hashlib
import os
import re
import statistics
from datetime import date, datetime, timedelta
from typing import Any

from extensions.packkit import Store

WORDS = {
    "Income": ["salary", "payroll", "wages", "pension credit", "child benefit", "refund"],
    "Groceries": ["tesco", "sainsbury", "asda", "aldi", "lidl", "morrisons", "waitrose", "co-op", "ocado", "woolworths",
                  "pick n pay", "checkers", "spar", "walmart", "kroger", "whole foods", "costco", "supermarket"],
    "Eating out": ["restaurant", "cafe", "coffee", "mcdonald", "kfc", "nando", "starbucks", "costa", "pret",
                   "uber eats", "deliveroo", "just eat", "doordash", "pizza"],
    "Transport": ["uber", "bolt", "shell", "bp ", "esso", "petrol", "fuel", "train", "tfl", "parking", "trainline"],
    "Subscriptions": ["netflix", "spotify", "disney", "apple.com", "amazon prime", "youtube premium", "icloud", "adobe",
                      "microsoft", "audible", "patreon"],
    "Utilities": ["electric", "energy", "octopus", "british gas", "edf", "eon", "water", "council tax", "eskom"],
    "Phone and internet": ["vodafone", "o2", "ee ", "three", "bt ", "sky", "virgin media", "telkom", "mtn", "verizon",
                           "at&t", "broadband"],
    "Housing": ["rent", "mortgage", "letting"],
    "Insurance": ["insurance", "aviva", "direct line"],
    "Health": ["pharmacy", "boots", "chemist", "dentist", "gym", "puregym"],
    "Shopping": ["amazon", "ebay", "argos", "ikea", "primark", "john lewis", "zara"],
}


def _day(value: Any) -> date:
    s = str(value or "").strip()
    for text in (s, s[:10]):
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d/%m/%y", "%m/%d/%Y"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
    raise ValueError(f"{value!r} is not a date this pack can read")


def _amt(v: Any) -> float:
    s = str(v or "").replace(",", "").replace("£", "").replace("$", "").replace("€", "").replace("R", "").strip()
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    return float(s) if s not in ("", "-") else 0.0


def payee(description: str) -> str:
    """The recurring part of a bank description: 'NETFLIX.COM 866-579 REF 12345' -> 'netflix com'."""
    words = re.sub(r"[^a-z& ]+", " ", description.lower()).split()
    words = [w for w in words if w not in ("ref", "card", "payment", "dd", "so", "fp", "pos", "to", "from", "debit", "direct")]
    return " ".join(words[:2]) or description.lower()[:20]


class Household(Store):
    SETTINGS_FILE = "household.json"
    DB = "money.sqlite"
    FOLDERS = ("Statements", "Receipts", "Reports")
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS txns (id INTEGER PRIMARY KEY, date TEXT NOT NULL, description TEXT NOT NULL,
        amount REAL NOT NULL, category TEXT NOT NULL DEFAULT '', account TEXT NOT NULL DEFAULT '',
        fingerprint TEXT NOT NULL UNIQUE, source TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS rules (pattern TEXT PRIMARY KEY COLLATE NOCASE, category TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS budgets (category TEXT PRIMARY KEY COLLATE NOCASE, monthly REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS warranties (id INTEGER PRIMARY KEY, item TEXT NOT NULL, bought TEXT NOT NULL,
        months INTEGER NOT NULL, expires TEXT NOT NULL, price REAL, shop TEXT NOT NULL DEFAULT '',
        receipt TEXT NOT NULL DEFAULT '');
    """

    @property
    def currency(self) -> str:
        return self.cfg.get("currency") or "GBP"

    def money(self, v: float) -> str:
        return f"{self.currency} {v:,.2f}"

    # -- statements --------------------------------------------------------------------------------------------

    def categorise(self, description: str, amount: float) -> str:
        d = f" {description.lower()} "
        for r in self.rows("SELECT * FROM rules ORDER BY length(pattern) DESC"):
            if r["pattern"].lower() in d:
                return r["category"]
        words = {**WORDS, **(self.cfg.get("category_words") or {})}
        for cat, keys in words.items():
            if cat == "Income" and amount <= 0:
                continue
            # Whole words only: "tfl" is Transport for London, not the middle of "netflix".
            if any(re.search(r"(?<![a-z0-9])" + re.escape(k.strip().lower()) + r"(?![a-z0-9])", d) for k in keys):
                return cat
        return "Income" if amount > 0 and "transfer" not in d else ""

    def import_statement(self, path: str, account: str = "") -> dict[str, Any]:
        """Any bank's CSV: a date, a description, and either one amount column (money out negative)
        or separate money in / money out columns. A row already imported is skipped."""
        if not os.path.isfile(path):
            raise FileNotFoundError(f"no file {path}")
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rows = [{(k or "").lower().strip(): (v or "").strip() for k, v in r.items()} for r in csv.DictReader(fh)]
        if not rows:
            raise ValueError("the statement is empty")
        cols = rows[0].keys()
        c_date = next((c for c in cols if c in ("date", "transaction date", "posted date", "posting date")), None)
        c_desc = next((c for c in cols if c in ("description", "details", "memo", "narrative", "payee", "name",
                                                  "transaction description")), None)
        c_amt = next((c for c in cols if c in ("amount", "value", "amount (gbp)", "amount (usd)")), None)
        c_in = next((c for c in cols if c in ("money in", "paid in", "credit", "credits", "deposit")), None)
        c_out = next((c for c in cols if c in ("money out", "paid out", "debit", "debits", "withdrawal")), None)
        if not c_date or not c_desc or not (c_amt or c_in or c_out):
            raise ValueError("the statement needs a date, a description and an amount (or money in / out) column")
        added = dup = 0
        seen: dict[str, int] = {}
        with self.lock, self.db:
            for r in rows:
                if not r.get(c_date):
                    continue
                amount = _amt(r.get(c_amt)) if c_amt else _amt(r.get(c_in)) - abs(_amt(r.get(c_out)))
                d, desc = _day(r[c_date]).isoformat(), r.get(c_desc) or ""
                key = f"{account}|{d}|{desc.lower()}|{amount:.2f}"
                n = seen[key] = seen.get(key, 0) + 1          # two identical coffees on one day are two coffees
                fp = hashlib.sha1(f"{key}|{n}".encode()).hexdigest()
                if self.db.execute("SELECT 1 FROM txns WHERE fingerprint = ?", (fp,)).fetchone():
                    dup += 1
                    continue
                self.db.execute("INSERT INTO txns(date, description, amount, category, account, fingerprint, source) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?)", (d, desc, amount, self.categorise(desc, amount),
                                                                 account, fp, os.path.basename(path)))
                added += 1
        return {"added": added, "already_there": dup,
                "uncategorised": self.one("SELECT count(*) AS n FROM txns WHERE category = ''")["n"]}

    def teach(self, pattern: str, category: str) -> dict[str, Any]:
        pattern, category = (pattern or "").strip(), (category or "").strip()
        if len(pattern) < 3 or not category:
            raise ValueError("give at least three characters of the description and the category")
        self.write("INSERT INTO rules(pattern, category) VALUES (?, ?) ON CONFLICT(pattern) DO UPDATE SET "
                   "category = excluded.category", (pattern, category))
        self.write("UPDATE txns SET category = ? WHERE lower(description) LIKE ?", (category, f"%{pattern.lower()}%"))
        n = self.one("SELECT count(*) AS n FROM txns WHERE lower(description) LIKE ?", (f"%{pattern.lower()}%",))["n"]
        return {"pattern": pattern, "category": category, "recategorised": n}

    def uncategorised(self, limit: int = 30) -> list[dict[str, Any]]:
        return self.rows("SELECT * FROM txns WHERE category = '' ORDER BY date DESC LIMIT ?", (int(limit),))

    # -- budgets -----------------------------------------------------------------------------------------------

    def set_budget(self, category: str, monthly: Any) -> dict[str, Any]:
        v = float(monthly)
        if v < 0 or not (category or "").strip():
            raise ValueError("a budget needs a category and an amount of zero or more")
        self.write("INSERT INTO budgets(category, monthly) VALUES (?, ?) ON CONFLICT(category) DO UPDATE SET "
                   "monthly = excluded.monthly", (category.strip(), v))
        return {"category": category.strip(), "monthly": v}

    def month(self, month: str = "") -> dict[str, Any]:
        m = (month or date.today().isoformat()[:7])[:7]
        rows = self.rows("SELECT category, sum(amount) AS total, count(*) AS n FROM txns WHERE date LIKE ? "
                         "GROUP BY category", (f"{m}%",))
        spent = {(r["category"] or "Not categorised"): round(-r["total"], 2) for r in rows if r["category"] != "Income"}
        income = next((round(r["total"], 2) for r in rows if r["category"] == "Income"), 0.0)
        budgets = {r["category"]: r["monthly"] for r in self.rows("SELECT * FROM budgets")}
        lines = []
        for cat in sorted(set(spent) | set(budgets)):
            s, b = spent.get(cat, 0.0), budgets.get(cat)
            lines.append({"category": cat, "spent": s, "budget": b,
                          "left": round(b - s, 2) if b is not None else None,
                          "over": b is not None and s > b})
        out = round(sum(spent.values()), 2)
        return {"month": m, "income": income, "spent": out, "saved": round(income - out, 2), "lines": lines,
                "over": [x for x in lines if x["over"]]}

    # -- recurring payments ------------------------------------------------------------------------------------

    def recurring(self, as_of: str = "") -> list[dict[str, Any]]:
        """Payments to the same payee about a month apart, three times or more."""
        today = _day(as_of) if as_of else date.today()
        groups: dict[str, list[dict[str, Any]]] = {}
        for t in self.rows("SELECT * FROM txns WHERE amount < 0 AND date <= ? ORDER BY date", (today.isoformat(),)):
            groups.setdefault(payee(t["description"]), []).append(t)
        out = []
        for name, ts in groups.items():
            if len(ts) < 3:
                continue
            dates = [date.fromisoformat(t["date"]) for t in ts]
            gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
            gap = statistics.median(gaps)
            if not 26 <= gap <= 35:
                continue
            last, prev = -ts[-1]["amount"], -ts[-2]["amount"]
            nxt = dates[-1] + timedelta(days=round(gap))
            if (today - dates[-1]).days > 45:
                continue                                    # stopped: not a live subscription
            out.append({"payee": name, "description": ts[-1]["description"], "category": ts[-1]["category"],
                        "amount": round(last, 2), "yearly": round(last * 12, 2), "times": len(ts),
                        "last": dates[-1].isoformat(), "next": nxt.isoformat(), "days_to_next": (nxt - today).days,
                        "price_rise": round(last - prev, 2) if last > prev * 1.01 else 0.0})
        return sorted(out, key=lambda r: -r["yearly"])

    # -- warranties ---------------------------------------------------------------------------------------------

    def add_warranty(self, item: str, bought: str, months: int, *, price: Any = None, shop: str = "",
                     receipt: str = "") -> dict[str, Any]:
        if not (item or "").strip():
            raise ValueError("what was bought?")
        b = _day(bought)
        months = int(months)
        if not 1 <= months <= 120:
            raise ValueError("a warranty lasts 1 to 120 months")
        y, m = divmod(b.month - 1 + months, 12)
        exp = date(b.year + y, m + 1, min(b.day, calendar.monthrange(b.year + y, m + 1)[1]))
        wid = self.write("INSERT INTO warranties(item, bought, months, expires, price, shop, receipt) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)", (item.strip(), b.isoformat(), months, exp.isoformat(),
                                                          float(price) if price not in (None, "") else None,
                                                          shop.strip(), receipt.strip()))
        return self.one("SELECT * FROM warranties WHERE id = ?", (wid,)) or {}

    def expiring(self, as_of: str = "", days: int = 60) -> list[dict[str, Any]]:
        today = _day(as_of) if as_of else date.today()
        return self.rows("SELECT * FROM warranties WHERE expires >= ? AND expires <= ? ORDER BY expires",
                         (today.isoformat(), (today + timedelta(days=int(days))).isoformat()))

    # -- the report ----------------------------------------------------------------------------------------------

    def report(self, month: str = "") -> dict[str, Any]:
        m = self.month(month)
        subs = self.recurring(f"{m['month']}-28")
        biggest = self.rows("SELECT date, description, amount FROM txns WHERE date LIKE ? AND amount < 0 "
                            "ORDER BY amount LIMIT 5", (f"{m['month']}%",))
        lines = [f"# Where the money went: {m['month']}", "",
                 f"In {self.money(m['income'])} · out {self.money(m['spent'])} · "
                 f"{'saved' if m['saved'] >= 0 else 'short'} {self.money(abs(m['saved']))}", "",
                 "| Category | Spent | Budget | Left |", "|---|---:|---:|---:|"]
        for x in sorted(m["lines"], key=lambda x: -x["spent"]):
            lines.append(f"| {x['category']} | {x['spent']:,.2f} | "
                         f"{'' if x['budget'] is None else format(x['budget'], ',.2f')} | "
                         f"{'' if x['left'] is None else format(x['left'], ',.2f')} |")
        lines += ["", "## Biggest payments", ""] + [f"- {b['date']} {b['description']}: {-b['amount']:,.2f}" for b in biggest]
        lines += ["", f"## Regular payments ({self.money(sum(s['yearly'] for s in subs))} a year)", ""] + [
            f"- {s['description']}: {s['amount']:,.2f} a month" + (f" (up {s['price_rise']:,.2f})" if s["price_rise"] else "")
            for s in subs]
        path = os.path.join(self.folder("Reports"), f"{m['month']} where the money went.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        return {**m, "path": path, "regular": subs}

    def overview(self, as_of: str = "") -> dict[str, Any]:
        today = _day(as_of) if as_of else date.today()
        subs = self.recurring(today.isoformat())
        return {"as_of": today.isoformat(), "month": self.month(today.isoformat()[:7]), "regular": subs,
                "due_soon": [s for s in subs if 0 <= s["days_to_next"] <= 7],
                "price_rises": [s for s in subs if s["price_rise"]], "warranties": self.expiring(today.isoformat()),
                "uncategorised": self.one("SELECT count(*) AS n FROM txns WHERE category = ''")["n"]}
