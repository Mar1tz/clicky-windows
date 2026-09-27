"""A small employer's payroll run: employees, gross pay, the checks, the files.

    <root>/payroll.json   currency, overtime multiplier, minimum hourly rate, leave, filing day
    <root>/payroll.sqlite employees, pay runs and their lines, leave taken
    <root>/Timesheets  Payslips  Bank files  Journals

Tax and social security are the law of each country and change every
year, so they are never worked out here: they come in from the payroll
software's export (or are typed in). What this does, with no model, is
the rest: gross pay from salaries and timesheets with overtime, net pay,
and the checks a payroll reviewer makes - a pay swing against last
month, starters and leavers, two people paid into one bank account, pay
under the minimum rate, deductions missing - then, once the run is
approved, payslips, a bank payment file and a balanced journal. Nobody
is paid from here: the bank file is uploaded to the bank by a person.
"""
from __future__ import annotations

import csv
import os
from datetime import date, datetime
from typing import Any

from extensions.accounting.pdfgen import write_pdf
from extensions.packkit import Store, slug

DEDUCTIONS = ("tax", "social", "pension", "other")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _r(v: float) -> float:
    return round(v + 1e-9, 2)


def _period(p: str) -> str:
    p = (p or "").strip()[:7]
    try:
        date.fromisoformat(p + "-01")
    except ValueError:
        raise ValueError(f"{p!r} is not a pay period (YYYY-MM)") from None
    return p


def _prev(p: str) -> str:
    y, m = int(p[:4]), int(p[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def _rows(path: str) -> list[dict[str, str]]:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"no file {path}")
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return [{(k or "").lower().strip(): (v or "").strip() for k, v in r.items()} for r in csv.DictReader(fh)]


def _f(v: Any) -> float:
    s = str(v or "").replace(",", "").lstrip("£$€R ").strip()
    return float(s) if s not in ("", "-") else 0.0


class Payroll(Store):
    SETTINGS_FILE = "payroll.json"
    DB = "payroll.sqlite"
    FOLDERS = ("Timesheets", "Payslips", "Bank files", "Journals")
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS employees (id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE COLLATE NOCASE,
        name TEXT NOT NULL, pay_type TEXT NOT NULL, rate REAL NOT NULL, bank TEXT NOT NULL DEFAULT '',
        start TEXT NOT NULL, end TEXT NOT NULL DEFAULT '', leave_days REAL NOT NULL DEFAULT 0,
        pension_pct REAL NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, period TEXT NOT NULL UNIQUE, status TEXT NOT NULL
        DEFAULT 'draft', created TEXT NOT NULL, finalised TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS lines (id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, employee_id INTEGER NOT NULL,
        hours REAL NOT NULL DEFAULT 0, overtime REAL NOT NULL DEFAULT 0, gross REAL NOT NULL DEFAULT 0,
        tax REAL NOT NULL DEFAULT 0, social REAL NOT NULL DEFAULT 0, pension REAL NOT NULL DEFAULT 0,
        pension_set REAL, other REAL NOT NULL DEFAULT 0,
        net REAL NOT NULL DEFAULT 0, UNIQUE(run_id, employee_id));
    CREATE TABLE IF NOT EXISTS leave (id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL, day TEXT NOT NULL,
        days REAL NOT NULL, note TEXT NOT NULL DEFAULT '');
    """

    def num(self, key: str, default: float) -> float:
        v = self.cfg.get(key)
        return float(default if v in (None, "") else v)

    @property
    def currency(self) -> str:
        return self.cfg.get("currency") or "GBP"

    def money(self, v: float) -> str:
        return f"{self.currency} {v:,.2f}"

    # -- employees ------------------------------------------------------------------------------------------

    def add_employee(self, code: str, name: str, pay_type: str, rate: Any, *, bank: str = "", start: str = "",
                     leave_days: Any = None, pension_pct: Any = None) -> dict[str, Any]:
        code, name, pay_type = (code or "").strip(), (name or "").strip(), (pay_type or "").lower().strip()
        if not code or not name:
            raise ValueError("an employee needs a payroll code and a name")
        if pay_type not in ("hourly", "salary"):
            raise ValueError("pay type is hourly or salary")
        rate = float(rate)
        if rate <= 0:
            raise ValueError("the rate must be above zero (hourly rate, or yearly salary)")
        started = (date.fromisoformat(start[:10]) if start else date.today()).isoformat()
        self.write("INSERT INTO employees(code, name, pay_type, rate, bank, start, leave_days, pension_pct) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(code) DO UPDATE SET name = excluded.name, "
                   "pay_type = excluded.pay_type, rate = excluded.rate, bank = excluded.bank, "
                   "leave_days = excluded.leave_days, pension_pct = excluded.pension_pct",
                   (code, name, pay_type, rate, bank.replace(" ", ""), started,
                    float(self.num("leave_days", 28) if leave_days in (None, "") else leave_days),
                    float(self.num("pension_pct", 0) if pension_pct in (None, "") else pension_pct)))
        return self.employee(code)

    def employee(self, ref: str) -> dict[str, Any]:
        r = self.one("SELECT * FROM employees WHERE code = ?", ((ref or "").strip(),))
        if r is None:
            found = self.rows("SELECT * FROM employees WHERE name LIKE ?", (f"%{(ref or '').strip()}%",))
            if len(found) > 1:
                raise ValueError(f"'{ref}' matches {', '.join(e['name'] for e in found)} - which one?")
            r = found[0] if found else None
        if r is None:
            raise KeyError(f"no employee {ref!r}")
        return r

    def leaver(self, ref: str, end: str) -> dict[str, Any]:
        e = self.employee(ref)
        self.write("UPDATE employees SET end = ? WHERE id = ?", (date.fromisoformat(end[:10]).isoformat(), e["id"]))
        return self.employee(e["code"])

    def active(self, period: str) -> list[dict[str, Any]]:
        first, last = f"{period}-01", f"{period}-31"
        return self.rows("SELECT * FROM employees WHERE start <= ? AND (end = '' OR end >= ?) ORDER BY code", (last, first))

    # -- the run -----------------------------------------------------------------------------------------------

    def run(self, period: str) -> dict[str, Any]:
        r = self.one("SELECT * FROM runs WHERE period = ?", (_period(period),))
        if r is None:
            raise KeyError(f"no pay run for {period}; start it first")
        r["lines"] = self.rows("SELECT l.*, e.code, e.name, e.pay_type, e.rate, e.bank, e.start, e.end FROM lines l "
                               "JOIN employees e ON e.id = l.employee_id WHERE l.run_id = ? ORDER BY e.code", (r["id"],))
        r["totals"] = {k: _r(sum((ln[k] or 0) for ln in r["lines"])) for k in ("gross", *DEDUCTIONS, "net")}
        return r

    def start(self, period: str) -> dict[str, Any]:
        period = _period(period)
        if self.one("SELECT 1 FROM runs WHERE period = ?", (period,)):
            raise ValueError(f"the {period} run already exists")
        people = self.active(period)
        if not people:
            raise ValueError(f"nobody is employed in {period}")
        with self.lock, self.db:
            rid = self.db.execute("INSERT INTO runs(period, created) VALUES (?, ?)", (period, _now())).lastrowid
            for e in people:
                self.db.execute("INSERT INTO lines(run_id, employee_id) VALUES (?, ?)", (rid, e["id"]))
        self._recalc(period)
        return self.run(period)

    def _draft(self, period: str) -> dict[str, Any]:
        r = self.run(period)
        if r["status"] != "draft":
            raise ValueError(f"the {r['period']} run is final and cannot change")
        return r

    def _recalc(self, period: str) -> None:
        mult = self.num("overtime_multiplier", 1.5)
        r = self.run(period)
        with self.lock, self.db:
            for ln in r["lines"]:
                gross = ln["rate"] / 12 if ln["pay_type"] == "salary" else (ln["hours"] + ln["overtime"] * mult) * ln["rate"]
                e = self.one("SELECT pension_pct FROM employees WHERE id = ?", (ln["employee_id"],))
                pension = ln["pension_set"] if ln["pension_set"] is not None else gross * e["pension_pct"] / 100
                net = gross - (ln["tax"] + ln["social"] + pension + ln["other"])
                self.db.execute("UPDATE lines SET gross = ?, pension = ?, net = ? WHERE id = ?",
                                (_r(gross), _r(pension), _r(net), ln["id"]))

    def set_line(self, period: str, employee: str, **values: Any) -> dict[str, Any]:
        r = self._draft(period)
        e = self.employee(employee)
        ln = next((x for x in r["lines"] if x["employee_id"] == e["id"]), None)
        if ln is None:
            raise KeyError(f"{e['name']} is not in the {r['period']} run")
        sets = {k: float(v) for k, v in values.items() if k in ("hours", "overtime", *DEDUCTIONS) and v not in (None, "")}
        if any(v < 0 for v in sets.values()):
            raise ValueError("hours and deductions cannot be negative")
        if sets:
            cols = [("pension_set" if k == "pension" else k) + " = ?" for k in sets]
            self.write(f"UPDATE lines SET {', '.join(cols)} WHERE id = ?", (*sets.values(), ln["id"]))
        self._recalc(r["period"])
        return next(x for x in self.run(r["period"])["lines"] if x["employee_id"] == e["id"])

    def import_timesheet(self, period: str, path: str) -> dict[str, Any]:
        """CSV: employee code (or name), hours, optional overtime. Rows for one person add up."""
        r = self._draft(period)
        hours: dict[int, list[float]] = {}
        unknown = []
        for row in _rows(path):
            ref = row.get("code") or row.get("employee code") or row.get("employee") or row.get("name") or ""
            try:
                e = self.employee(ref)
            except (KeyError, ValueError):
                unknown.append(ref)
                continue
            h = hours.setdefault(e["id"], [0.0, 0.0])
            h[0] += _f(row.get("hours"))
            h[1] += _f(row.get("overtime") or row.get("overtime hours"))
        in_run = {ln["employee_id"] for ln in r["lines"]}
        with self.lock, self.db:
            for eid, (h, ot) in hours.items():
                if eid in in_run:
                    self.db.execute("UPDATE lines SET hours = ?, overtime = ? WHERE run_id = ? AND employee_id = ?",
                                    (h, ot, r["id"], eid))
        self._recalc(r["period"])
        return {"people": len(hours), "unknown": unknown}

    def import_deductions(self, period: str, path: str) -> dict[str, Any]:
        """The payroll software's figures - CSV: code (or name), tax, social security / NI, pension, other."""
        r = self._draft(period)
        done, unknown = 0, []
        for row in _rows(path):
            ref = row.get("code") or row.get("employee code") or row.get("employee") or row.get("name") or ""
            try:
                e = self.employee(ref)
            except (KeyError, ValueError):
                unknown.append(ref)
                continue
            values = {"tax": row.get("tax") or row.get("income tax") or row.get("paye"),
                      "social": row.get("social") or row.get("ni") or row.get("national insurance")
                      or row.get("social security") or row.get("uif"),
                      "pension": row.get("pension"), "other": row.get("other") or row.get("other deductions")}
            self.set_line(r["period"], e["code"], **{k: _f(v) for k, v in values.items() if v not in (None, "")})
            done += 1
        return {"updated": done, "unknown": unknown}

    # -- the checks -----------------------------------------------------------------------------------------------

    def check(self, period: str) -> dict[str, Any]:
        if self.run(period)["status"] == "draft":
            self._recalc(_period(period))          # a rate changed since the run started
        r = self.run(period)
        prev = self.one("SELECT period FROM runs WHERE period = ? AND status = 'final'", (_prev(r["period"]),))
        before = {ln["code"].lower(): ln for ln in self.run(prev["period"])["lines"]} if prev else {}
        swing = self.num("variance_pct", 10)
        minimum = self.num("minimum_hourly", 0)
        problems: list[dict[str, Any]] = []

        def add(kind: str, who: str, text: str, blocking: bool = False) -> None:
            problems.append({"kind": kind, "employee": who, "text": text, "blocking": blocking})

        banks: dict[str, list[str]] = {}
        for ln in r["lines"]:
            if ln["bank"]:
                banks.setdefault(ln["bank"], []).append(ln["name"])
            else:
                add("bank", ln["name"], f"{ln['name']} has no bank account", blocking=True)
            if ln["net"] < 0:
                add("negative", ln["name"], f"{ln['name']}'s deductions are more than their pay", blocking=True)
            if ln["pay_type"] == "hourly" and ln["hours"] + ln["overtime"] == 0:
                add("no_hours", ln["name"], f"{ln['name']} is hourly and has no hours this period")
            if ln["pay_type"] == "hourly" and minimum and ln["rate"] < minimum:
                add("minimum", ln["name"], f"{ln['name']} is paid {self.money(ln['rate'])} an hour, under the "
                                           f"{self.money(minimum)} minimum you set", blocking=True)
            if ln["gross"] > 0 and ln["tax"] == 0 and ln["social"] == 0:
                add("deductions", ln["name"], f"{ln['name']} has no tax or social security - were the payroll "
                                              "software's figures imported?")
            if ln["pay_type"] == "salary" and ((ln["start"][:7] == r["period"] and not ln["start"].endswith("-01"))
                                               or ln["end"][:7] == r["period"]):
                add("pro_rata", ln["name"], f"{ln['name']} starts or leaves part-way through {r['period']}; "
                                            "the full month's salary is in the run - check the pro rata amount")
            old = before.get(ln["code"].lower())
            if prev and old is None:
                add("starter", ln["name"], f"{ln['name']} is new this period")
            elif old and old["gross"] and abs(ln["gross"] - old["gross"]) / old["gross"] * 100 > swing:
                add("variance", ln["name"], f"{ln['name']}'s gross pay moved from {self.money(old['gross'])} to "
                                            f"{self.money(ln['gross'])}")
        for acct, names in banks.items():
            if len(names) > 1:
                add("shared_bank", ", ".join(names), f"{' and '.join(names)} are paid into the same bank account",
                    blocking=True)
        now = {ln["code"].lower() for ln in r["lines"]}
        for code, old in before.items():
            if code not in now:
                add("leaver", old["name"], f"{old['name']} was paid last period and is not in this run")
        return {"period": r["period"], "problems": problems, "blocking": [p for p in problems if p["blocking"]],
                "totals": r["totals"], "previous": prev["period"] if prev else ""}

    # -- finalising --------------------------------------------------------------------------------------------------

    def finalise(self, period: str) -> dict[str, Any]:
        self._draft(period)
        c = self.check(period)
        r = self.run(period)
        if c["blocking"]:
            raise ValueError("cannot finalise: " + "; ".join(p["text"] for p in c["blocking"]))
        p = r["period"]
        slips = []
        for ln in r["lines"]:
            lines = [f"Employee: {ln['name']} ({ln['code']})", f"Period: {p}", "",
                     f"{'Gross pay':<30}{ln['gross']:>14,.2f}"]
            if ln["pay_type"] == "hourly":
                lines.insert(3, f"Hours {ln['hours']:g} + overtime {ln['overtime']:g} at {ln['rate']:.2f}")
            for k, label in (("tax", "Tax"), ("social", "Social security"), ("pension", "Pension"), ("other", "Other")):
                if ln[k]:
                    lines.append(f"{label:<30}{-ln[k]:>14,.2f}")
            lines += ["", f"{'Net pay ' + self.currency:<30}{ln['net']:>14,.2f}"]
            slips.append(write_pdf(os.path.join(self.folder("Payslips"), f"{p} {slug(ln['name'])}.pdf"), lines,
                                   title=f"{self.cfg.get('employer') or 'Payslip'} - {p}"))
        bank = os.path.join(self.folder("Bank files"), f"{p} net pay.csv")
        with open(bank, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["name", "account", "amount", "reference"])
            for ln in r["lines"]:
                if ln["net"] > 0:
                    w.writerow([ln["name"], ln["bank"], f"{ln['net']:.2f}", f"Salary {p}"])
        t = r["totals"]
        journal = os.path.join(self.folder("Journals"), f"{p} payroll journal.csv")
        entries = [("Wages & Salaries", t["gross"], 0.0), ("Net pay due", 0.0, t["net"]),
                   ("Tax due", 0.0, t["tax"]), ("Social security due", 0.0, t["social"]),
                   ("Pension due", 0.0, t["pension"]), ("Other deductions due", 0.0, t["other"])]
        with open(journal, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "account", "debit", "credit", "description"])
            for acct, dr, cr in entries:
                if dr or cr:
                    w.writerow([f"{p}-28", acct, f"{dr:.2f}", f"{cr:.2f}", f"Payroll {p}"])
        self.write("UPDATE runs SET status = 'final', finalised = ? WHERE id = ?", (_now(), r["id"]))
        return {"period": p, "payslips": slips, "bank_file": bank, "journal": journal, "totals": t,
                "balanced": _r(t["gross"]) == _r(t["net"] + t["tax"] + t["social"] + t["pension"] + t["other"])}

    # -- leave ---------------------------------------------------------------------------------------------------------

    def record_leave(self, employee: str, day: str, days: Any = 1, note: str = "") -> dict[str, Any]:
        e = self.employee(employee)
        d = float(days)
        if not 0 < d <= 31:
            raise ValueError("leave is between half a day and a month at a time")
        self.write("INSERT INTO leave(employee_id, day, days, note) VALUES (?, ?, ?, ?)",
                   (e["id"], date.fromisoformat(day[:10]).isoformat(), d, note.strip()))
        return self.leave_balance(e["code"], day)

    def leave_balance(self, employee: str, as_of: str = "") -> dict[str, Any]:
        """Earned so far this leave year (pro rata from a mid-year start), less taken."""
        e = self.employee(employee)
        today = date.fromisoformat(as_of[:10]) if as_of else date.today()
        m = int(self.num("leave_year_start_month", 1))
        year_start = date(today.year if today.month >= m else today.year - 1, m, 1)
        begin = max(year_start, date.fromisoformat(e["start"]))
        months = (today.year - begin.year) * 12 + today.month - begin.month + 1
        earned = round(e["leave_days"] * min(12, max(0, months)) / 12, 1)
        taken = self.one("SELECT coalesce(sum(days), 0) AS d FROM leave WHERE employee_id = ? AND day >= ? AND day <= ?",
                         (e["id"], year_start.isoformat(), today.isoformat()))["d"]
        return {"employee": e["name"], "year_start": year_start.isoformat(), "entitlement": e["leave_days"],
                "earned": earned, "taken": taken, "left": round(earned - taken, 1)}

    def overview(self, as_of: str = "") -> dict[str, Any]:
        today = date.fromisoformat(as_of[:10]) if as_of else date.today()
        period = today.isoformat()[:7]
        run = self.one("SELECT period, status FROM runs WHERE period = ?", (period,))
        filing_day = int(self.num("filing_day", 0))
        return {"as_of": today.isoformat(), "period": period, "employees": len(self.active(period)),
                "run": run, "last_final": (self.one("SELECT max(period) AS p FROM runs WHERE status = 'final'") or {}).get("p"),
                "filing_due": f"{today.year}-{today.month:02d}-{filing_day:02d}" if filing_day else "",
                "check": self.check(period) if run else None}

