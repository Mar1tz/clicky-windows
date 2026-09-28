"""A teacher's markbook and planner: classes, work, scores, rubrics, reports.

    <root>/teaching.json   category weights, grade boundaries, report length, teacher's name
    <root>/teaching.sqlite classes, students, assignments, scores, rubrics
    <root>/Reports  Plans  Letters

The arithmetic of teaching, with no model: weighted averages and grades,
missing work, students whose last three scores are sliding; a rubric
turns the levels the teacher chose into a score and feedback; report
comments drafted from each student's own figures and kept inside the
report's length; a term plan that fits each unit's lessons into the real
lesson days around the holidays. Parent letters are drafts about their
own child only. Nothing is sent or published from here.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import date, timedelta
from email.message import EmailMessage
from typing import Any

from extensions.packkit import Store, slug

BOUNDARIES = [("A", 80), ("B", 70), ("C", 60), ("D", 50), ("E", 40), ("U", 0)]


def _day(value: Any) -> date:
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        raise ValueError(f"{value!r} is not a date (YYYY-MM-DD)") from None


class Classroom(Store):
    SETTINGS_FILE = "teaching.json"
    DB = "teaching.sqlite"
    FOLDERS = ("Reports", "Plans", "Letters")
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS classes (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE);
    CREATE TABLE IF NOT EXISTS students (id INTEGER PRIMARY KEY, class_id INTEGER NOT NULL, name TEXT NOT NULL,
        parent_email TEXT NOT NULL DEFAULT '', UNIQUE(class_id, name));
    CREATE TABLE IF NOT EXISTS assignments (id INTEGER PRIMARY KEY, class_id INTEGER NOT NULL, name TEXT NOT NULL,
        max REAL NOT NULL, category TEXT NOT NULL DEFAULT 'classwork', due TEXT NOT NULL DEFAULT '', rubric_id INTEGER,
        UNIQUE(class_id, name));
    CREATE TABLE IF NOT EXISTS scores (assignment_id INTEGER NOT NULL, student_id INTEGER NOT NULL, score REAL,
        feedback TEXT NOT NULL DEFAULT '', PRIMARY KEY(assignment_id, student_id));
    CREATE TABLE IF NOT EXISTS rubrics (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        criteria TEXT NOT NULL);
    """

    @property
    def teacher(self) -> str:
        return self.cfg.get("teacher") or "Your child's teacher"

    def boundaries(self) -> list[tuple[str, float]]:
        b = self.cfg.get("boundaries")
        return sorted(((k, float(v)) for k, v in b.items()), key=lambda x: -x[1]) if b else BOUNDARIES

    def grade(self, pct: float) -> str:
        return next((g for g, floor in self.boundaries() if pct >= floor), self.boundaries()[-1][0])

    # -- classes -------------------------------------------------------------------------------------------------

    def klass(self, name: str, create: bool = False) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise ValueError("which class?")
        r = self.one("SELECT * FROM classes WHERE name = ?", (name,))
        if r is None:
            if not create:
                found = self.rows("SELECT * FROM classes WHERE name LIKE ?", (f"%{name}%",))
                if len(found) == 1:
                    return found[0]
                raise KeyError(f"no class {name!r}") if not found else ValueError(
                    f"'{name}' matches {', '.join(c['name'] for c in found)} - which class?")
            cid = self.write("INSERT INTO classes(name) VALUES (?)", (name,))
            r = self.one("SELECT * FROM classes WHERE id = ?", (cid,))
        return r

    def import_class(self, name: str, path: str) -> dict[str, Any]:
        """CSV: name (or first + last name), optional parent email."""
        if not os.path.isfile(path):
            raise FileNotFoundError(f"no file {path}")
        c = self.klass(name, create=True)
        added = 0
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for raw in csv.DictReader(fh):
                r = {(k or "").lower().strip(): (v or "").strip() for k, v in raw.items()}
                student = r.get("name") or r.get("student") or " ".join(
                    x for x in (r.get("first name", ""), r.get("last name", "")) if x)
                if not student:
                    continue
                email = r.get("parent email") or r.get("email") or ""
                self.write("INSERT INTO students(class_id, name, parent_email) VALUES (?, ?, ?) ON CONFLICT(class_id, name) "
                           "DO UPDATE SET parent_email = COALESCE(NULLIF(excluded.parent_email, ''), parent_email)",
                           (c["id"], student, email))
                added += 1
        return {"class": c["name"], "students": len(self.roster(c["name"])), "rows": added}

    def roster(self, name: str) -> list[dict[str, Any]]:
        return self.rows("SELECT * FROM students WHERE class_id = ? ORDER BY name", (self.klass(name)["id"],))

    def student(self, name: str, klass: str = "") -> dict[str, Any]:
        sql = "SELECT s.*, c.name AS class FROM students s JOIN classes c ON c.id = s.class_id WHERE s.name LIKE ?"
        params: list[Any] = [f"%{(name or '').strip()}%"]
        if klass:
            sql += " AND c.id = ?"
            params.append(self.klass(klass)["id"])
        found = self.rows(sql, params)
        exact = [s for s in found if s["name"].lower() == (name or "").strip().lower()]
        found = exact or found
        if len(found) > 1:
            raise ValueError(f"'{name}' matches {', '.join(s['name'] + ' (' + s['class'] + ')' for s in found)} - which?")
        if not found:
            raise KeyError(f"no student {name!r}")
        return found[0]

    # -- work and scores ---------------------------------------------------------------------------------------

    def add_assignment(self, klass: str, name: str, max_score: Any, *, category: str = "classwork", due: str = "",
                       rubric: str = "") -> dict[str, Any]:
        c = self.klass(klass)
        mx = float(max_score)
        if mx <= 0 or not (name or "").strip():
            raise ValueError("a piece of work needs a name and a top mark above zero")
        rid = self.one("SELECT id FROM rubrics WHERE name = ?", (rubric.strip(),))["id"] if rubric else None
        aid = self.write("INSERT INTO assignments(class_id, name, max, category, due, rubric_id) VALUES (?, ?, ?, ?, ?, ?)",
                         (c["id"], name.strip(), mx, (category or "classwork").lower(), _day(due).isoformat() if due else "", rid))
        return self.one("SELECT * FROM assignments WHERE id = ?", (aid,)) or {}

    def assignment(self, klass: str, name: str) -> dict[str, Any]:
        c = self.klass(klass)
        r = self.one("SELECT * FROM assignments WHERE class_id = ? AND name LIKE ?", (c["id"], f"%{(name or '').strip()}%"))
        if r is None:
            raise KeyError(f"no work {name!r} in {c['name']}")
        return r

    def record_scores(self, klass: str, work: str, scores: dict[str, Any]) -> dict[str, Any]:
        a = self.assignment(klass, work)
        unknown, done = [], 0
        for who, score in (scores or {}).items():
            try:
                s = self.student(who, klass)
            except (KeyError, ValueError):
                unknown.append(who)
                continue
            value = None if score in (None, "", "missing", "absent") else float(score)
            if value is not None and not 0 <= value <= a["max"]:
                raise ValueError(f"{s['name']}: {value:g} is outside 0-{a['max']:g}")
            self.write("INSERT INTO scores(assignment_id, student_id, score) VALUES (?, ?, ?) ON CONFLICT(assignment_id, "
                       "student_id) DO UPDATE SET score = excluded.score", (a["id"], s["id"], value))
            done += 1
        return {"work": a["name"], "recorded": done, "unknown": unknown}

    # -- rubrics --------------------------------------------------------------------------------------------------

    def set_rubric(self, name: str, criteria: list[dict[str, Any]]) -> dict[str, Any]:
        clean = []
        for c in criteria or []:
            levels = [{"points": float(x["points"]), "descriptor": str(x.get("descriptor", "")).strip()}
                      for x in c.get("levels") or []]
            if not str(c.get("name") or "").strip() or len(levels) < 2:
                raise ValueError("each criterion needs a name and at least two levels")
            clean.append({"name": c["name"].strip(), "levels": sorted(levels, key=lambda x: x["points"])})
        if not clean:
            raise ValueError("a rubric needs criteria")
        self.write("INSERT INTO rubrics(name, criteria) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET criteria = "
                   "excluded.criteria", (name.strip(), json.dumps(clean)))
        return {"name": name.strip(), "criteria": clean, "max": sum(c["levels"][-1]["points"] for c in clean)}

    def mark(self, klass: str, work: str, who: str, chosen: dict[str, Any]) -> dict[str, Any]:
        """The teacher's chosen level per criterion -> score and feedback."""
        a = self.assignment(klass, work)
        if not a["rubric_id"]:
            raise ValueError(f"{a['name']} has no rubric")
        crit = json.loads(self.one("SELECT criteria FROM rubrics WHERE id = ?", (a["rubric_id"],))["criteria"])
        s = self.student(who, klass)
        total, well, next_ = 0.0, [], []
        for c in crit:
            pts = chosen.get(c["name"])
            if pts is None:
                raise ValueError(f"no level chosen for '{c['name']}'")
            level = next((lv for lv in c["levels"] if lv["points"] == float(pts)), None)
            if level is None:
                raise ValueError(f"'{c['name']}' has no level worth {pts}")
            total += level["points"]
            (well if level["points"] == c["levels"][-1]["points"] else next_).append(
                (c["name"], level["descriptor"], c["levels"][min(c["levels"].index(level) + 1, len(c["levels"]) - 1)]["descriptor"]))
        top = sum(c["levels"][-1]["points"] for c in crit)
        score = round(total / top * a["max"], 1)
        feedback = ("What went well: " + ("; ".join(f"{n.lower()} - {d}" for n, d, _ in well) or "you have made a start")
                    + ". Even better if: " + ("; ".join(f"{n.lower()} - {up}" for n, _, up in next_) or "you keep this up")
                    + ".")
        self.write("INSERT INTO scores(assignment_id, student_id, score, feedback) VALUES (?, ?, ?, ?) ON CONFLICT"
                   "(assignment_id, student_id) DO UPDATE SET score = excluded.score, feedback = excluded.feedback",
                   (a["id"], s["id"], score, feedback))
        return {"student": s["name"], "work": a["name"], "score": score, "max": a["max"], "feedback": feedback}

    # -- the markbook ---------------------------------------------------------------------------------------------

    def student_record(self, s: dict[str, Any], as_of: date | None = None) -> dict[str, Any]:
        today = as_of or date.today()
        rows = self.rows("SELECT a.*, sc.score, sc.feedback FROM assignments a LEFT JOIN scores sc ON sc.assignment_id = "
                         "a.id AND sc.student_id = ? WHERE a.class_id = ? ORDER BY a.due = '', a.due, a.id",
                         (s["id"], s["class_id"]))
        done = [r for r in rows if r["score"] is not None]
        missing = [r["name"] for r in rows if r["score"] is None and r["due"] and date.fromisoformat(r["due"]) < today]
        weights = self.cfg.get("weights") or {}
        by_cat: dict[str, list[float]] = {}
        for r in done:
            by_cat.setdefault(r["category"], []).append(r["score"] / r["max"] * 100)
        cat_avg = {k: sum(v) / len(v) for k, v in by_cat.items()}
        if weights and any(k in weights for k in cat_avg):
            used = {k: float(weights.get(k, 0)) for k in cat_avg if float(weights.get(k, 0)) > 0}
            avg = sum(cat_avg[k] * w for k, w in used.items()) / sum(used.values()) if used else 0.0
        else:
            pcts = [r["score"] / r["max"] * 100 for r in done]
            avg = sum(pcts) / len(pcts) if pcts else 0.0
        last = [r["score"] / r["max"] * 100 for r in done][-3:]
        falling = len(last) == 3 and last[0] > last[1] > last[2] and last[0] - last[2] >= 10
        return {"student": s["name"], "class": s.get("class", ""), "average": round(avg, 1),
                "grade": self.grade(avg) if done else "-", "done": len(done), "missing": missing,
                "falling": falling, "by_category": {k: round(v, 1) for k, v in cat_avg.items()}}

    def markbook(self, klass: str, as_of: str = "") -> dict[str, Any]:
        c = self.klass(klass)
        today = _day(as_of) if as_of else date.today()
        rows = [self.student_record({**s, "class": c["name"]}, today) for s in self.roster(c["name"])]
        return {"class": c["name"], "students": rows,
                "class_average": round(sum(r["average"] for r in rows if r["done"]) / max(1, sum(1 for r in rows if r["done"])), 1),
                "missing": [r for r in rows if r["missing"]], "falling": [r for r in rows if r["falling"]]}

    def report_comments(self, klass: str, as_of: str = "") -> dict[str, Any]:
        mb = self.markbook(klass, as_of)
        limit = int(self.cfg.get("comment_chars") or 600)
        lines, out = [f"# Report comments: {mb['class']}", ""], []
        for r in mb["students"]:
            first = r["student"].split()[0]
            parts = [f"{first} has averaged {r['average']:g}% this term (grade {r['grade']})." if r["done"]
                     else f"{first} has no marked work yet this term."]
            if r["by_category"] and len(r["by_category"]) > 1:
                best = max(r["by_category"], key=r["by_category"].get)
                worst = min(r["by_category"], key=r["by_category"].get)
                parts.append(f"Their strongest area is {best}; the next step is to bring {worst} up to the same level.")
            if r["falling"]:
                parts.append("Recent marks have dipped, and a steady routine will help get them back on track.")
            if r["missing"]:
                parts.append(f"{len(r['missing'])} piece(s) of work are still to be handed in: {', '.join(r['missing'])}.")
            text = " ".join(parts)
            if len(text) > limit:
                text = text[:limit - 1].rsplit(" ", 1)[0] + "…"
            out.append({"student": r["student"], "comment": text, "chars": len(text)})
            lines += [f"## {r['student']}", "", text, ""]
        path = os.path.join(self.folder("Reports"), f"{slug(mb['class'])} report comments.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        return {"class": mb["class"], "comments": out, "path": path, "limit": limit}

    def plan_term(self, klass: str, units: list[dict[str, Any]], start: str, end: str, weekdays: list[int],
                  holidays: list[str] | None = None) -> dict[str, Any]:
        """Fit each unit's lessons, in order, into the lesson days between start and end."""
        c = self.klass(klass)
        first, last = _day(start), _day(end)
        if last < first or not weekdays:
            raise ValueError("the term needs a start before its end, and the weekdays the class meets (0 = Monday)")
        off: set[date] = set()
        for h in holidays or []:
            a, _, b = str(h).partition("..")
            d0, d1 = _day(a), _day(b) if b else _day(a)
            while d0 <= d1:
                off.add(d0)
                d0 += timedelta(days=1)
        days = []
        d = first
        while d <= last:
            if d.weekday() in {int(w) for w in weekdays} and d not in off:
                days.append(d)
            d += timedelta(days=1)
        plan, i = [], 0
        for u in units or []:
            n = int(u.get("lessons") or 0)
            if not str(u.get("title") or "").strip() or n <= 0:
                raise ValueError("each unit needs a title and a number of lessons")
            for k in range(1, n + 1):
                plan.append({"date": days[i].isoformat() if i < len(days) else "", "unit": u["title"].strip(),
                             "lesson": f"{k} of {n}"})
                i += 1
        unplaced = sum(1 for p in plan if not p["date"])
        path = os.path.join(self.folder("Plans"), f"{slug(c['name'])} {first.isoformat()} term plan.csv")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["date", "unit", "lesson"])
            w.writeheader()
            w.writerows(plan)
        return {"class": c["name"], "lesson_days": len(days), "lessons": len(plan), "unplaced": unplaced,
                "spare": max(0, len(days) - len(plan)), "plan": plan, "path": path}

    def parent_letter(self, who: str, klass: str = "") -> dict[str, Any]:
        s = self.student(who, klass)
        r = self.student_record(s)
        if not r["missing"]:
            raise ValueError(f"{s['name']} has no missing work to write about")
        msg = EmailMessage()
        msg["To"] = s["parent_email"] or f"Parent of {s['name']}"
        msg["Subject"] = f"{s['name']}: work to hand in"
        msg["X-Unsent"] = "1"
        items = "\n".join(f"  - {m}" for m in r["missing"])
        msg.set_content(f"Dear parent or carer,\n\nI wanted to let you know that {s['name'].split()[0]} still has the "
                        f"following work to hand in for {s['class']}:\n\n{items}\n\nIf there is anything making it "
                        f"hard to complete, please let me know and we can find a way forward.\n\nKind regards,\n"
                        f"{self.teacher}\n")
        path = os.path.join(self.folder("Letters"), f"{slug(s['name'])} missing work.eml")
        with open(path, "wb") as fh:
            fh.write(bytes(msg))
        return {"student": s["name"], "missing": r["missing"], "path": path, "no_email": not s["parent_email"]}
