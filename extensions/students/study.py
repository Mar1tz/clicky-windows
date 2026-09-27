"""A student's desk: courses, deadlines, a revision plan, flashcards, notes
and sources - one folder.

    <root>/study.json      hours a day, session length, reference style
    <root>/study.sqlite    courses, topics, deadlines, sessions, cards, sources
    <root>/Notes  Plans  Flashcards  References

The mechanical parts of studying are code here, with no model:
  * a revision plan that fits each course's topics (harder ones get more
    sessions) into the days before its exam, mixing courses within a day,
    with the day before each exam kept for a full review - and says so
    plainly when the hours do not fit;
  * flashcards on SM-2 spaced repetition, exportable to Anki;
  * reference lists in APA 7 or Harvard from the sources' details.
The model summarises notes and writes cards and practice questions from
the student's material. Nothing here writes work that is handed in.
"""
from __future__ import annotations

import csv
import os
from datetime import date, datetime, timedelta
from typing import Any, Optional

from extensions.packkit import Store, slug

KINDS = ("exam", "assignment", "test", "presentation", "other")
STYLES = ("apa", "harvard")
SOURCE_TYPES = ("book", "article", "web")


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


# -- spaced repetition (SM-2) --------------------------------------------------------------------------------

def sm2(grade: int, reps: int, interval: int, ease: float) -> tuple[int, int, float]:
    """The next (reps, interval days, ease) after a review graded 0-5."""
    if not 0 <= grade <= 5:
        raise ValueError("grade is 0 (blank) to 5 (perfect)")
    if grade < 3:
        reps, interval = 0, 1
    else:
        interval = 1 if reps == 0 else 6 if reps == 1 else max(1, round(interval * ease))
        reps += 1
    ease = max(1.3, ease + (0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02)))
    return reps, interval, round(ease, 2)


# -- references ------------------------------------------------------------------------------------------------

def _initials(given: str) -> str:
    parts = [p for p in given.replace("-", " - ").split() if p]
    out = []
    for p in parts:
        out.append("-" if p == "-" else f"{p[0].upper()}.")
    return " ".join(out).replace(" - ", "-")


def _person(name: str) -> tuple[str, str]:
    """'Ada Lovelace' or 'Lovelace, Ada' -> ('Lovelace', 'A.')"""
    name = name.strip()
    if "," in name:
        surname, given = [p.strip() for p in name.split(",", 1)]
    else:
        bits = name.split()
        surname, given = bits[-1], " ".join(bits[:-1])
    return surname, _initials(given)


def _authors(names: list[str], style: str) -> str:
    people = [_person(n) for n in names if n.strip()]
    if not people:
        return ""
    if style == "apa":
        cited = [f"{s}, {i}".rstrip(", ") for s, i in people]
        if len(cited) > 20:
            cited = cited[:19] + ["...", cited[-1]]
            return ", ".join(cited[:-1]) + " " + cited[-1]
        return cited[0] if len(cited) == 1 else ", ".join(cited[:-1]) + ", & " + cited[-1]
    cited = [f"{s}, {i}".rstrip(", ") for s, i in people]
    return cited[0] if len(cited) == 1 else ", ".join(cited[:-1]) + " and " + cited[-1]


def reference(src: dict[str, Any], style: str) -> str:
    """One entry of a reference list, Markdown italics for titles."""
    style = style.lower()
    if style not in STYLES:
        raise ValueError(f"style is one of {', '.join(STYLES)}")
    authors = _authors(src.get("authors") or [], style) or src.get("site") or src.get("publisher") or ""
    year = str(src.get("year") or "n.d.")
    title, kind = src["title"].strip().rstrip("."), src["type"]
    pub = f" {src['publisher'].strip().rstrip('.')}." if src.get("publisher") else ""
    if style == "apa":
        head = f"{authors} ({year})." if authors else f"*{title}*. ({year})."
        if kind == "book":
            return f"{head}{f' *{title}*.' if authors else ''}{pub}"
        if kind == "article":
            vol = f", *{src['volume']}*" if src.get("volume") else ""
            iss = f"({src['issue']})" if src.get("issue") else ""
            pages = f", {src['pages']}" if src.get("pages") else ""
            link = f" {src['doi'] if str(src.get('doi', '')).startswith('http') else 'https://doi.org/' + src['doi']}" \
                if src.get("doi") else (f" {src['url']}" if src.get("url") else "")
            return f"{head} {title}. *{src.get('journal', '')}*{vol}{iss}{pages}.{link}"
        site = f" {src['site']}." if src.get("site") and src.get("site") != authors else ""
        return f"{head} *{title}*.{site} {src.get('url', '')}".rstrip()
    # Harvard
    head = f"{authors} ({year})" if authors else f"*{title}* ({year})"
    if kind == "book":
        return f"{head} *{title}*.{pub}" if authors else f"{head}.{pub}"
    if kind == "article":
        vol = f", {src['volume']}" if src.get("volume") else ""
        iss = f"({src['issue']})" if src.get("issue") else ""
        pages = f", pp. {src['pages']}" if src.get("pages") else ""
        return f"{head} '{title}', *{src.get('journal', '')}*{vol}{iss}{pages}."
    accessed = f" (Accessed: {src['accessed']})" if src.get("accessed") else ""
    return f"{head} *{title}*. Available at: {src.get('url', '')}{accessed}."


class StudyDesk(Store):
    SETTINGS_FILE = "study.json"
    DB = "study.sqlite"
    FOLDERS = ("Notes", "Plans", "Flashcards", "References")
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS courses (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS topics (id INTEGER PRIMARY KEY, course_id INTEGER NOT NULL, name TEXT NOT NULL,
        difficulty INTEGER NOT NULL DEFAULT 2, UNIQUE(course_id, name));
    CREATE TABLE IF NOT EXISTS deadlines (id INTEGER PRIMARY KEY, course_id INTEGER NOT NULL, title TEXT NOT NULL,
        kind TEXT NOT NULL, due TEXT NOT NULL, weight REAL, done INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS sessions (id INTEGER PRIMARY KEY, day TEXT NOT NULL, course_id INTEGER NOT NULL,
        topic TEXT NOT NULL, minutes INTEGER NOT NULL, done INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS cards (id INTEGER PRIMARY KEY, course_id INTEGER NOT NULL, front TEXT NOT NULL,
        back TEXT NOT NULL, reps INTEGER NOT NULL DEFAULT 0, interval INTEGER NOT NULL DEFAULT 0,
        ease REAL NOT NULL DEFAULT 2.5, due TEXT NOT NULL, UNIQUE(course_id, front));
    CREATE TABLE IF NOT EXISTS sources (id INTEGER PRIMARY KEY, course_id INTEGER NOT NULL, data TEXT NOT NULL,
        created TEXT NOT NULL);
    """

    @property
    def hours_per_day(self) -> float:
        return float(self.cfg.get("hours_per_day") or 2)

    @property
    def session_minutes(self) -> int:
        return max(15, int(self.cfg.get("session_minutes") or 45))

    @property
    def style(self) -> str:
        return (self.cfg.get("style") or "apa").lower()

    # -- courses, topics, deadlines ----------------------------------------------------------------------

    def course(self, name: str, *, create: bool = False) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise ValueError("which course?")
        r = self.one("SELECT * FROM courses WHERE name = ?", (name,))
        if r is None:
            found = self.rows("SELECT * FROM courses WHERE name LIKE ?", (f"%{name}%",))
            if len(found) == 1 and not create:
                return found[0]
            if len(found) > 1 and not create:
                raise ValueError(f"'{name}' matches {', '.join(c['name'] for c in found)} - which one?")
            if not create:
                raise KeyError(f"no course {name!r}; add it first")
            cid = self.write("INSERT INTO courses(name, created) VALUES (?, ?)", (name, _now()))
            r = self.one("SELECT * FROM courses WHERE id = ?", (cid,))
        return r

    def courses(self) -> list[dict[str, Any]]:
        return self.rows("SELECT * FROM courses ORDER BY name")

    def add_topics(self, course: str, topics: list[Any]) -> list[dict[str, Any]]:
        c = self.course(course, create=True)
        for t in topics or []:
            name, diff = (t, 2) if isinstance(t, str) else (t.get("name", ""), t.get("difficulty", 2))
            name, diff = str(name).strip(), int(diff or 2)
            if not name:
                continue
            if not 1 <= diff <= 3:
                raise ValueError("difficulty is 1 (easy), 2 or 3 (hard)")
            self.write("INSERT INTO topics(course_id, name, difficulty) VALUES (?, ?, ?) ON CONFLICT(course_id, name) "
                       "DO UPDATE SET difficulty = excluded.difficulty", (c["id"], name, diff))
        return self.rows("SELECT * FROM topics WHERE course_id = ? ORDER BY id", (c["id"],))

    def add_deadline(self, course: str, title: str, due: str, *, kind: str = "assignment",
                     weight: Optional[float] = None) -> dict[str, Any]:
        kind = (kind or "assignment").lower()
        if kind not in KINDS:
            raise ValueError(f"kind is one of {', '.join(KINDS)}")
        c = self.course(course, create=True)
        did = self.write("INSERT INTO deadlines(course_id, title, kind, due, weight) VALUES (?, ?, ?, ?, ?)",
                         (c["id"], (title or kind).strip(), kind, _day(due).isoformat(),
                          float(weight) if weight not in (None, "") else None))
        return self.deadline(did)

    def deadline(self, did: int) -> dict[str, Any]:
        r = self.one("SELECT d.*, c.name AS course FROM deadlines d JOIN courses c ON c.id = d.course_id "
                     "WHERE d.id = ?", (int(did),))
        if r is None:
            raise KeyError(f"no deadline #{did}")
        return r

    def deadlines(self, *, as_of: str = "", include_done: bool = False) -> list[dict[str, Any]]:
        today = _day(as_of, date.today())
        rows = self.rows("SELECT d.*, c.name AS course FROM deadlines d JOIN courses c ON c.id = d.course_id "
                         + ("" if include_done else "WHERE d.done = 0 ") + "ORDER BY d.due, d.id")
        for r in rows:
            r["days_left"] = (date.fromisoformat(r["due"]) - today).days
        return rows

    def finish_deadline(self, did: int) -> dict[str, Any]:
        self.deadline(did)
        self.write("UPDATE deadlines SET done = 1 WHERE id = ?", (int(did),))
        return self.deadline(did)

    # -- the revision plan -------------------------------------------------------------------------------

    def build_plan(self, *, start: str = "", hours_per_day: Optional[float] = None) -> dict[str, Any]:
        """Fit every course's topics into the days before its next exam."""
        first = _day(start, date.today())
        hours = float(hours_per_day or self.hours_per_day)
        per_day = max(1, int(hours * 60 // self.session_minutes))
        exams: dict[int, date] = {}
        for d in self.deadlines(as_of=first.isoformat()):
            if d["kind"] == "exam" and date.fromisoformat(d["due"]) > first:
                exams.setdefault(d["course_id"], date.fromisoformat(d["due"]))
        if not exams:
            raise ValueError("no exams ahead: add exam dates first (add_deadline with kind exam)")
        todo: dict[int, list[str]] = {}
        names = {c["id"]: c["name"] for c in self.courses()}
        for cid in exams:
            topics = self.rows("SELECT name, difficulty FROM topics WHERE course_id = ? ORDER BY difficulty DESC, id",
                               (cid,))
            if not topics:
                topics = [{"name": "Past papers", "difficulty": 2}]
            # A hard topic is revisited: its sessions spread across the plan.
            queue = []
            for rnd in range(3):
                queue += [t["name"] for t in topics if t["difficulty"] > rnd]
            todo[cid] = queue
        plan: list[tuple[str, int, str]] = []
        last = max(exams.values())
        day = first
        while day < last:
            slots = per_day
            used_today: set[tuple[int, str]] = set()
            for cid, exam in sorted(exams.items(), key=lambda e: e[1]):
                if exam - timedelta(days=1) == day and slots:
                    plan.append((day.isoformat(), cid, "Review everything"))
                    slots -= 1
            while slots:
                live = [(cid, len(todo[cid]) / max(1, (exams[cid] - day).days - 1)) for cid in todo
                        if todo[cid] and exams[cid] - timedelta(days=1) > day]
                live = [(cid, u) for cid, u in live if (cid, todo[cid][0]) not in used_today] or live
                if not live:
                    break
                cid = max(live, key=lambda x: x[1])[0]
                topic = todo[cid].pop(0)
                if (cid, topic) in used_today:
                    todo[cid].append(topic)
                    break
                used_today.add((cid, topic))
                plan.append((day.isoformat(), cid, topic))
                slots -= 1
            day += timedelta(days=1)
        with self.lock, self.db:
            self.db.execute("DELETE FROM sessions WHERE done = 0 AND day >= ?", (first.isoformat(),))
            self.db.executemany("INSERT INTO sessions(day, course_id, topic, minutes) VALUES (?, ?, ?, ?)",
                                [(d, cid, t, self.session_minutes) for d, cid, t in plan])
        short = {names[cid]: len(q) for cid, q in todo.items() if q}
        path = os.path.join(self.folder("Plans"), "Revision plan.md")
        lines = [f"# Revision plan from {first.isoformat()}", "",
                 f"{per_day} session(s) of {self.session_minutes} minutes a day.", ""]
        for d in sorted({p[0] for p in plan}):
            lines.append(f"## {d}")
            lines += [f"- {names[cid]}: {t}" for dd, cid, t in plan if dd == d]
            lines.append("")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        return {"path": path, "sessions": len(plan), "days": len({p[0] for p in plan}), "per_day": per_day,
                "exams": {names[cid]: d.isoformat() for cid, d in exams.items()}, "not_fitted": short}

    def sessions_on(self, day: str = "") -> list[dict[str, Any]]:
        d = _day(day, date.today()).isoformat()
        return self.rows("SELECT s.*, c.name AS course FROM sessions s JOIN courses c ON c.id = s.course_id "
                         "WHERE s.day = ? ORDER BY s.id", (d,))

    def finish_session(self, sid: int) -> dict[str, Any]:
        r = self.one("SELECT * FROM sessions WHERE id = ?", (int(sid),))
        if r is None:
            raise KeyError(f"no study session #{sid}")
        self.write("UPDATE sessions SET done = 1 WHERE id = ?", (int(sid),))
        return {**r, "done": 1}

    def missed_sessions(self, as_of: str = "") -> list[dict[str, Any]]:
        d = _day(as_of, date.today()).isoformat()
        return self.rows("SELECT s.*, c.name AS course FROM sessions s JOIN courses c ON c.id = s.course_id "
                         "WHERE s.done = 0 AND s.day < ? ORDER BY s.day", (d,))

    # -- flashcards ---------------------------------------------------------------------------------------------

    def add_cards(self, course: str, cards: list[dict[str, str]]) -> dict[str, int]:
        c = self.course(course, create=True)
        added = skipped = 0
        today = date.today().isoformat()
        for card in cards or []:
            front, back = str(card.get("front") or "").strip(), str(card.get("back") or "").strip()
            if not front or not back:
                raise ValueError("every card needs a front and a back")
            if self.one("SELECT 1 FROM cards WHERE course_id = ? AND front = ?", (c["id"], front)):
                skipped += 1
                continue
            self.write("INSERT INTO cards(course_id, front, back, due) VALUES (?, ?, ?, ?)", (c["id"], front, back, today))
            added += 1
        return {"added": added, "skipped": skipped}

    def due_cards(self, course: str = "", *, as_of: str = "", limit: int = 20) -> list[dict[str, Any]]:
        d = _day(as_of, date.today()).isoformat()
        sql = ("SELECT k.*, c.name AS course FROM cards k JOIN courses c ON c.id = k.course_id WHERE k.due <= ?")
        params: list[Any] = [d]
        if course:
            sql += " AND k.course_id = ?"
            params.append(self.course(course)["id"])
        return self.rows(sql + " ORDER BY k.due, k.id LIMIT ?", params + [max(1, int(limit))])

    def review(self, card_id: int, grade: int, *, as_of: str = "") -> dict[str, Any]:
        k = self.one("SELECT * FROM cards WHERE id = ?", (int(card_id),))
        if k is None:
            raise KeyError(f"no card #{card_id}")
        reps, interval, ease = sm2(int(grade), k["reps"], k["interval"], k["ease"])
        due = (_day(as_of, date.today()) + timedelta(days=interval)).isoformat()
        self.write("UPDATE cards SET reps = ?, interval = ?, ease = ?, due = ? WHERE id = ?",
                   (reps, interval, ease, due, k["id"]))
        return {**k, "reps": reps, "interval": interval, "ease": ease, "due": due}

    def export_cards(self, course: str) -> dict[str, Any]:
        c = self.course(course)
        rows = self.rows("SELECT front, back FROM cards WHERE course_id = ? ORDER BY id", (c["id"],))
        if not rows:
            raise ValueError(f"no cards for {c['name']} yet")
        path = os.path.join(self.folder("Flashcards"), f"{slug(c['name'])}-anki.txt")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            fh.write("#separator:tab\n#html:false\n")
            w = csv.writer(fh, delimiter="\t", lineterminator="\n")
            w.writerows([(r["front"], r["back"]) for r in rows])
        return {"course": c["name"], "path": path, "cards": len(rows)}

    # -- notes and sources --------------------------------------------------------------------------------------

    def save_notes(self, course: str, topic: str, text: str) -> dict[str, Any]:
        c = self.course(course, create=True)
        if not (text or "").strip():
            raise ValueError("the notes are empty")
        folder = self.folder(os.path.join("Notes", slug(c["name"])))
        path = os.path.join(folder, f"{slug(topic or 'notes')}.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"# {topic or 'Notes'}\n\n{text.strip()}\n")
        return {"course": c["name"], "topic": topic, "path": path, "words": len(text.split())}

    def add_source(self, course: str, src: dict[str, Any]) -> dict[str, Any]:
        import json
        c = self.course(course, create=True)
        kind = (src.get("type") or "").lower()
        if kind not in SOURCE_TYPES:
            raise ValueError(f"type is one of {', '.join(SOURCE_TYPES)}")
        if not str(src.get("title") or "").strip():
            raise ValueError("a source needs its title")
        if kind == "web" and not src.get("url"):
            raise ValueError("a web source needs its URL")
        clean = {k: v for k, v in src.items() if v not in (None, "", [])}
        clean["type"] = kind
        if isinstance(clean.get("authors"), str):
            clean["authors"] = [a.strip() for a in clean["authors"].split(";") if a.strip()]
        sid = self.write("INSERT INTO sources(course_id, data, created) VALUES (?, ?, ?)",
                         (c["id"], json.dumps(clean), _now()))
        return {"id": sid, "course": c["name"], **clean, "apa": reference(clean, "apa")}

    def bibliography(self, course: str, style: str = "") -> dict[str, Any]:
        import json
        c = self.course(course)
        style = (style or self.style).lower()
        srcs = [json.loads(r["data"]) for r in self.rows("SELECT data FROM sources WHERE course_id = ?", (c["id"],))]
        if not srcs:
            raise ValueError(f"no sources for {c['name']} yet")
        entries = sorted({reference(s, style) for s in srcs}, key=lambda e: e.lstrip("*").lower())
        path = os.path.join(self.folder("References"), f"{slug(c['name'])}-{style}.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"# References ({'APA 7' if style == 'apa' else 'Harvard'})\n\n" + "\n\n".join(entries) + "\n")
        return {"course": c["name"], "style": style, "entries": entries, "path": path}

    # -- the whole picture ------------------------------------------------------------------------------------------

    def overview(self, as_of: str = "") -> dict[str, Any]:
        today = _day(as_of, date.today())
        ahead = [d for d in self.deadlines(as_of=today.isoformat()) if d["days_left"] >= 0]
        late = [d for d in self.deadlines(as_of=today.isoformat()) if d["days_left"] < 0]
        return {"as_of": today.isoformat(), "courses": len(self.courses()), "next": ahead[:5], "late": late,
                "today": self.sessions_on(today.isoformat()), "missed": self.missed_sessions(today.isoformat()),
                "cards_due": len(self.due_cards(as_of=today.isoformat(), limit=10000))}
