"""A podcast's production desk: episodes, guests, transcripts, chapters, notes.

    <root>/podcast.json   show name, release weekday, clip length
    <root>/podcast.sqlite episodes, guests
    <root>/Episodes  Guests

From a transcript (SRT, WebVTT, or "[00:12:30] Name: text" lines), with no
model: the episode's length, who spoke for how long, candidate clips of
the right length around the words asked for; chapters checked (start at
0, in order, inside the episode) and written as Podcasting 2.0 JSON
chapters and a plain list; show notes; a release calendar; and a publish
checklist that will not pass an episode whose guest has not signed a
release. Nothing is uploaded or published from here.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta
from typing import Any

from extensions.packkit import Store, slug

STATUSES = ("planned", "booked", "recorded", "edited", "ready", "published")
GUEST_STATUSES = ("invited", "confirmed", "recorded", "released", "declined")
_TIME = r"(\d{1,2}):(\d{2}):(\d{2})(?:[.,](\d{1,3}))?"
_CUE = re.compile(_TIME + r"\s*-->\s*" + _TIME)
_PLAIN = re.compile(r"^\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?\s+(?:(?P<who>[A-Z][\w .'-]{0,30}):\s+)?(?P<text>.+)$")


def _secs(h: str, m: str, s: str, ms: str | None) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + (int((ms or "0").ljust(3, "0")) / 1000)


def stamp(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def at_seconds(ts: Any) -> float:
    if isinstance(ts, (int, float)):
        return float(ts)
    parts = [float(p) for p in str(ts).strip().split(":")]
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"{ts!r} is not a time like 12:30 or 01:02:03")
    out = 0.0
    for p in parts:
        out = out * 60 + p
    return out


def parse_transcript(text: str) -> list[dict[str, Any]]:
    """Cues: start, end (seconds), speaker, text."""
    cues: list[dict[str, Any]] = []
    lines = text.replace("\r", "").split("\n")
    i = 0
    while i < len(lines):
        m = _CUE.search(lines[i])
        if m:
            start, end = _secs(*m.groups()[:4]), _secs(*m.groups()[4:])
            body = []
            i += 1
            while i < len(lines) and lines[i].strip():
                body.append(lines[i].strip())
                i += 1
            raw = " ".join(body)
            who = ""
            v = re.match(r"<v(?:\.[\w.]+)?\s+([^>]+)>", raw)
            if v:
                who, raw = v.group(1).strip(), re.sub(r"</?v[^>]*>", "", raw)
            else:
                n = re.match(r"^([A-Z][\w .'-]{0,30}):\s+(.*)$", raw)
                if n:
                    who, raw = n.group(1).strip(), n.group(2)
            cues.append({"start": start, "end": end, "speaker": who, "text": re.sub(r"<[^>]+>", "", raw).strip()})
            continue
        p = _PLAIN.match(lines[i].strip())
        if p:
            cues.append({"start": at_seconds(p.group(1)), "end": None, "speaker": (p.group("who") or "").strip(),
                         "text": p.group("text").strip()})
        i += 1
    for a, b in zip(cues, cues[1:]):
        if a["end"] is None:
            a["end"] = b["start"]
    if cues and cues[-1]["end"] is None:
        cues[-1]["end"] = cues[-1]["start"] + max(2.0, len(cues[-1]["text"].split()) / 2.5)
    return [c for c in cues if c["text"]]


def check_chapters(chapters: list[dict[str, Any]], duration: float) -> list[str]:
    problems = []
    times = [at_seconds(c.get("at", c.get("start", 0))) for c in chapters]
    if not times:
        return ["no chapters"]
    if times[0] != 0:
        problems.append("the first chapter must start at 00:00:00")
    for (a, b), c in zip(zip(times, times[1:]), chapters[1:]):
        if b <= a:
            problems.append(f"'{c.get('title', '')}' starts before or with the one before it")
    for t, c in zip(times, chapters):
        if duration and t >= duration:
            problems.append(f"'{c.get('title', '')}' starts at {stamp(t)}, after the episode ends ({stamp(duration)})")
        if not str(c.get("title") or "").strip():
            problems.append(f"the chapter at {stamp(t)} has no title")
    return problems


class Podcast(Store):
    SETTINGS_FILE = "podcast.json"
    DB = "podcast.sqlite"
    FOLDERS = ("Episodes", "Guests")
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS guests (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        email TEXT NOT NULL DEFAULT '', topic TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'invited',
        release_signed INTEGER NOT NULL DEFAULT 0, notes TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS episodes (id INTEGER PRIMARY KEY, number INTEGER NOT NULL UNIQUE, title TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'planned', planned TEXT NOT NULL DEFAULT '', published TEXT NOT NULL DEFAULT '',
        guest_id INTEGER, audio TEXT NOT NULL DEFAULT '', transcript TEXT NOT NULL DEFAULT '',
        duration REAL NOT NULL DEFAULT 0, speakers TEXT NOT NULL DEFAULT '{}', chapters TEXT NOT NULL DEFAULT '[]',
        notes TEXT NOT NULL DEFAULT '');
    """

    @property
    def show(self) -> str:
        return self.cfg.get("show") or "My podcast"

    # -- guests ---------------------------------------------------------------------------------------------

    def guest(self, name: str, *, email: str = "", topic: str = "", create: bool = False) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise ValueError("which guest?")
        r = self.one("SELECT * FROM guests WHERE name = ?", (name,))
        if r is None and not create:
            found = self.rows("SELECT * FROM guests WHERE name LIKE ?", (f"%{name}%",))
            if len(found) > 1:
                raise ValueError(f"'{name}' matches {', '.join(g['name'] for g in found)} - which guest?")
            r = found[0] if found else None
            if r is None:
                raise KeyError(f"no guest {name!r}")
        if r is None:
            self.write("INSERT INTO guests(name, email, topic) VALUES (?, ?, ?)", (name, email.strip(), topic.strip()))
            r = self.one("SELECT * FROM guests WHERE name = ?", (name,))
        elif email or topic:
            self.write("UPDATE guests SET email = COALESCE(NULLIF(?, ''), email), topic = COALESCE(NULLIF(?, ''), topic) "
                       "WHERE id = ?", (email.strip(), topic.strip(), r["id"]))
            r = self.one("SELECT * FROM guests WHERE id = ?", (r["id"],))
        return r

    def set_guest(self, name: str, *, status: str = "", release_signed: Any = None) -> dict[str, Any]:
        g = self.guest(name)
        if status and status not in GUEST_STATUSES:
            raise ValueError(f"status is one of {', '.join(GUEST_STATUSES)}")
        self.write("UPDATE guests SET status = COALESCE(NULLIF(?, ''), status), release_signed = ? WHERE id = ?",
                   (status, int(bool(release_signed)) if release_signed is not None else g["release_signed"], g["id"]))
        return self.guest(g["name"])

    # -- episodes ------------------------------------------------------------------------------------------------

    def plan(self, title: str, *, planned: str = "", guest: str = "", number: Any = None) -> dict[str, Any]:
        if not (title or "").strip():
            raise ValueError("an episode needs a working title")
        n = int(number) if number not in (None, "") else (self.one("SELECT max(number) AS n FROM episodes")["n"] or 0) + 1
        if self.one("SELECT 1 FROM episodes WHERE number = ?", (n,)):
            raise ValueError(f"episode {n} already exists")
        gid = self.guest(guest, create=True)["id"] if guest else None
        when = date.fromisoformat(planned[:10]).isoformat() if planned else ""
        self.write("INSERT INTO episodes(number, title, planned, guest_id, status) VALUES (?, ?, ?, ?, ?)",
                   (n, title.strip(), when, gid, "booked" if gid else "planned"))
        return self.episode(n)

    def episode(self, ref: Any) -> dict[str, Any]:
        text = str(ref).strip().lstrip("#")
        r = self.one("SELECT * FROM episodes WHERE number = ?", (int(text),)) if text.isdigit() else None
        if r is None:
            found = self.rows("SELECT * FROM episodes WHERE title LIKE ?", (f"%{str(ref).strip()}%",))
            if len(found) > 1:
                raise ValueError(f"'{ref}' matches episodes {', '.join(str(e['number']) for e in found)} - which one?")
            r = found[0] if found else None
        if r is None:
            raise KeyError(f"no episode {ref!r}")
        r["chapters"] = json.loads(r["chapters"])
        r["speakers"] = json.loads(r["speakers"])
        r["guest"] = self.one("SELECT * FROM guests WHERE id = ?", (r["guest_id"],)) if r["guest_id"] else None
        r["folder"] = self.folder(os.path.join("Episodes", f"{r['number']:03d} {slug(r['title'])}"))
        return r

    def episodes(self, *, open_only: bool = False) -> list[dict[str, Any]]:
        rows = self.rows("SELECT number FROM episodes" + (" WHERE status != 'published'" if open_only else "")
                         + " ORDER BY number")
        return [self.episode(r["number"]) for r in rows]

    def set_status(self, ref: Any, status: str, *, audio: str = "", when: str = "") -> dict[str, Any]:
        if status not in STATUSES:
            raise ValueError(f"status is one of {', '.join(STATUSES)}")
        e = self.episode(ref)
        if status == "published":
            problems = self.checklist(e["number"])["problems"]
            if problems:
                raise ValueError("not ready to publish: " + "; ".join(problems))
        self.write("UPDATE episodes SET status = ?, audio = COALESCE(NULLIF(?, ''), audio), published = ? WHERE id = ?",
                   (status, audio.strip(), (date.fromisoformat(when[:10]) if when else date.today()).isoformat()
                    if status == "published" else e["published"], e["id"]))
        return self.episode(e["number"])

    # -- the transcript ----------------------------------------------------------------------------------------

    def attach_transcript(self, ref: Any, path: str) -> dict[str, Any]:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"no file {path}")
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            cues = parse_transcript(fh.read())
        if not cues:
            raise ValueError("no timed lines in that file (SRT, WebVTT or '[00:01:02] Name: text')")
        e = self.episode(ref)
        talk: dict[str, float] = {}
        for c in cues:
            talk[c["speaker"] or "Unknown"] = talk.get(c["speaker"] or "Unknown", 0.0) + max(0.0, c["end"] - c["start"])
        total = sum(talk.values()) or 1.0
        speakers = {k: {"seconds": round(v, 1), "share": round(v / total * 100)} for k, v in
                    sorted(talk.items(), key=lambda kv: -kv[1])}
        duration = max(c["end"] for c in cues)
        self.write("UPDATE episodes SET transcript = ?, duration = ?, speakers = ? WHERE id = ?",
                   (path, duration, json.dumps(speakers), e["id"]))
        return {"episode": e["number"], "duration": stamp(duration), "seconds": duration, "cues": len(cues),
                "speakers": speakers, "words": sum(len(c["text"].split()) for c in cues)}

    def clips(self, ref: Any, keywords: list[str], *, top: int = 3) -> list[dict[str, Any]]:
        e = self.episode(ref)
        if not e["transcript"]:
            raise ValueError(f"episode {e['number']} has no transcript yet")
        with open(e["transcript"], encoding="utf-8-sig", errors="replace") as fh:
            cues = parse_transcript(fh.read())
        lo, hi = float(self.cfg.get("clip_min") or 20), float(self.cfg.get("clip_max") or 60)
        words = [k.lower() for k in keywords or [] if k.strip()]
        found = []
        for i, first in enumerate(cues):
            text, j = [], i
            while j < len(cues) and cues[j]["end"] - first["start"] <= hi:
                text.append(cues[j]["text"])
                j += 1
            if j == i or cues[j - 1]["end"] - first["start"] < lo:
                continue
            blob = " ".join(text).lower()
            hits = sum(blob.count(w) for w in words) if words else blob.count("?")
            if hits:
                found.append({"start": stamp(first["start"]), "end": stamp(cues[j - 1]["end"]),
                              "seconds": round(cues[j - 1]["end"] - first["start"]), "hits": hits,
                              "speaker": first["speaker"], "text": " ".join(text)[:240]})
        found.sort(key=lambda c: (-c["hits"], c["start"]))
        picked: list[dict[str, Any]] = []
        for c in found:                                   # no two clips overlapping
            s, e_ = at_seconds(c["start"]), at_seconds(c["end"])
            if all(e_ <= at_seconds(p["start"]) or s >= at_seconds(p["end"]) for p in picked):
                picked.append(c)
            if len(picked) >= top:
                break
        return picked

    # -- chapters and notes ------------------------------------------------------------------------------------

    def set_chapters(self, ref: Any, chapters: list[dict[str, Any]]) -> dict[str, Any]:
        e = self.episode(ref)
        problems = check_chapters(chapters, e["duration"])
        clean = [{"startTime": at_seconds(c.get("at", c.get("start", 0))), "title": str(c.get("title") or "").strip()}
                 for c in chapters]
        self.write("UPDATE episodes SET chapters = ? WHERE id = ?", (json.dumps(clean), e["id"]))
        jpath = os.path.join(e["folder"], "chapters.json")
        with open(jpath, "w", encoding="utf-8") as fh:
            json.dump({"version": "1.2.0", "chapters": clean}, fh, indent=2)
        tpath = os.path.join(e["folder"], "chapters.txt")
        with open(tpath, "w", encoding="utf-8") as fh:
            fh.write("\n".join(f"{stamp(c['startTime'])} {c['title']}" for c in clean) + "\n")
        return {"episode": e["number"], "chapters": clean, "problems": problems, "json": jpath, "text": tpath}

    def show_notes(self, ref: Any, summary: str, links: list[str] | None = None) -> dict[str, Any]:
        e = self.episode(ref)
        if not (summary or "").strip():
            raise ValueError("the notes need a summary")
        lines = [f"# {e['number']}. {e['title']}", "", summary.strip(), ""]
        if e["guest"]:
            lines += [f"**Guest:** {e['guest']['name']}" + (f" - {e['guest']['topic']}" if e["guest"]["topic"] else ""), ""]
        if e["chapters"]:
            lines += ["## Chapters", ""] + [f"- {stamp(c['startTime'])} {c['title']}" for c in e["chapters"]] + [""]
        if links:
            lines += ["## Links", ""] + [f"- {x}" for x in links if str(x).strip()] + [""]
        path = os.path.join(e["folder"], "show-notes.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        self.write("UPDATE episodes SET notes = ? WHERE id = ?", (path, e["id"]))
        return {"episode": e["number"], "path": path, "words": len(summary.split())}

    def checklist(self, ref: Any) -> dict[str, Any]:
        e = self.episode(ref)
        problems = []
        if not e["audio"] or not os.path.isfile(e["audio"]):
            problems.append("no final audio file")
        if not e["notes"]:
            problems.append("no show notes")
        if not e["chapters"]:
            problems.append("no chapters")
        else:
            problems += [f"chapters: {p}" for p in check_chapters([{"at": c["startTime"], "title": c["title"]}
                                                                   for c in e["chapters"]], e["duration"])]
        if e["guest"] and not e["guest"]["release_signed"]:
            problems.append(f"{e['guest']['name']} has not signed a release")
        return {"episode": e["number"], "title": e["title"], "problems": problems, "ready": not problems}

    def calendar(self, weeks: int = 8, as_of: str = "") -> dict[str, Any]:
        today = date.fromisoformat(as_of[:10]) if as_of else date.today()
        weekday = int(self.cfg.get("release_weekday") if self.cfg.get("release_weekday") not in (None, "") else 1)
        first = today + timedelta(days=(weekday - today.weekday()) % 7)
        slots = [(first + timedelta(weeks=i)).isoformat() for i in range(int(weeks))]
        planned = {e["planned"]: e for e in self.episodes(open_only=True) if e["planned"]}
        return {"slots": [{"date": s, "episode": (planned[s]["number"], planned[s]["title"]) if s in planned else None}
                          for s in slots],
                "gaps": [s for s in slots if s not in planned],
                "off_day": [e["number"] for e in self.episodes(open_only=True) if e["planned"] and
                            date.fromisoformat(e["planned"]).weekday() != weekday]}

    def overview(self, as_of: str = "") -> dict[str, Any]:
        today = date.fromisoformat(as_of[:10]) if as_of else date.today()
        soon = [e for e in self.episodes(open_only=True) if e["planned"] and
                0 <= (date.fromisoformat(e["planned"]) - today).days <= 7]
        return {"as_of": today.isoformat(), "open": self.episodes(open_only=True),
                "this_week": [{**self.checklist(e["number"]), "planned": e["planned"]} for e in soon],
                "unsigned": self.rows("SELECT name FROM guests WHERE release_signed = 0 AND status IN ('recorded', 'released')"),
                "calendar": self.calendar(4, today.isoformat())}
