"""A video channel's studio: the pipeline from idea to published, the scripts,
sponsor deals and channel numbers - one folder.

    <root>/creator.json   channel name, platform, speaking pace (words a minute)
    <root>/studio.sqlite  videos, deals, stats
    <root>/Scripts  Shot lists  Uploads  Stats

The mechanical parts of making a video are done here, with no model: a
script's length at the creator's pace, a shot list from its sections and
[B-roll: ...] markers, chapter timestamps from its headings, and upload
details checked against YouTube's rules before the creator pastes them.
Writing the script, the title and the hook is the model's part; this is
where it is kept. Nothing is uploaded or published from here.
"""
from __future__ import annotations

import csv
import os
import re
from datetime import date, datetime
from typing import Any, Optional

from extensions.packkit import Store, slug

STAGES = ("idea", "scripting", "filming", "editing", "ready", "published")
DEAL_STATES = ("pitched", "agreed", "delivered", "invoiced", "paid", "lost")
OPEN_DEALS = ("pitched", "agreed")

# YouTube's limits for what the creator pastes into Studio.
TITLE_MAX = 100
DESCRIPTION_MAX = 5000
TAGS_MAX = 500
CHAPTERS_MIN = 3
CHAPTER_MIN_SECONDS = 10

_MARKER = re.compile(r"^\s*\[(b-?roll|shot|cut ?to|insert)\s*:?\s*(?P<what>[^\]]*)\]\s*$", re.I)
_HEADING = re.compile(r"^\s*#{1,3}\s+(?P<title>.+?)\s*$")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _date(value: Any) -> str:
    if not value:
        return ""
    try:
        return date.fromisoformat(str(value).strip()[:10]).isoformat()
    except ValueError:
        raise ValueError(f"{value!r} is not a date (YYYY-MM-DD)") from None


def stamp(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def seconds_of(ts: str) -> int:
    parts = [int(p) for p in str(ts).strip().split(":")]
    if not 2 <= len(parts) <= 3:
        raise ValueError(f"{ts!r} is not a timestamp like 1:05")
    out = 0
    for p in parts:
        out = out * 60 + p
    return out


def parse_script(text: str) -> list[dict[str, Any]]:
    """Sections of a script: its '## heading', the spoken paragraphs (with
    word counts) and the b-roll markers. Text before the first heading is
    the intro."""
    sections = [{"heading": "Intro", "paragraphs": [], "broll": []}]
    para: list[str] = []

    def flush():
        if para:
            sections[-1]["paragraphs"].append(" ".join(para))
            para.clear()

    for line in (text or "").splitlines():
        h, m = _HEADING.match(line), _MARKER.match(line)
        if h:
            flush()
            if not sections[-1]["paragraphs"] and not sections[-1]["broll"] and len(sections) == 1:
                sections[-1]["heading"] = h.group("title")
            else:
                sections.append({"heading": h.group("title"), "paragraphs": [], "broll": []})
        elif m:
            flush()
            sections[-1]["broll"].append(m.group("what").strip() or "b-roll")
        elif line.strip():
            para.append(line.strip())
        else:
            flush()
    flush()
    for s in sections:
        s["words"] = sum(len(p.split()) for p in s["paragraphs"])
    return [s for s in sections if s["paragraphs"] or s["broll"]]


class Studio(Store):
    SETTINGS_FILE = "creator.json"
    DB = "studio.sqlite"
    FOLDERS = ("Scripts", "Shot lists", "Uploads", "Stats")
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS videos (
        id INTEGER PRIMARY KEY, slug TEXT NOT NULL UNIQUE, title TEXT NOT NULL, stage TEXT NOT NULL DEFAULT 'idea',
        idea TEXT NOT NULL DEFAULT '', script TEXT NOT NULL DEFAULT '', words INTEGER NOT NULL DEFAULT 0,
        planned TEXT NOT NULL DEFAULT '', published TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '',
        upload TEXT NOT NULL DEFAULT '', created TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS deals (
        id INTEGER PRIMARY KEY, brand TEXT NOT NULL, fee REAL NOT NULL DEFAULT 0, deliverable TEXT NOT NULL,
        video_id INTEGER, due TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pitched',
        notes TEXT NOT NULL DEFAULT '', created TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS stats (
        id INTEGER PRIMARY KEY, imported TEXT NOT NULL, source TEXT NOT NULL, title TEXT NOT NULL,
        views INTEGER NOT NULL DEFAULT 0, watch_hours REAL NOT NULL DEFAULT 0, subscribers INTEGER NOT NULL DEFAULT 0,
        impressions INTEGER NOT NULL DEFAULT 0, ctr REAL
    );
    """

    @property
    def name(self) -> str:
        return self.cfg.get("name") or "My channel"

    @property
    def wpm(self) -> int:
        return max(60, int(self.cfg.get("wpm") or 150))

    def seconds_for(self, words: int) -> float:
        return words / self.wpm * 60

    # -- videos ---------------------------------------------------------------------------------------------

    def add_idea(self, title: str, idea: str = "", planned: str = "") -> dict[str, Any]:
        title = (title or "").strip()
        if not title:
            raise ValueError("the idea needs a working title")
        base, n = slug(title), 1
        s = base
        while self.one("SELECT 1 FROM videos WHERE slug = ?", (s,)):
            n += 1
            s = f"{base}-{n}"
        vid = self.write("INSERT INTO videos(slug, title, idea, planned, created) VALUES (?, ?, ?, ?, ?)",
                         (s, title, idea.strip(), _date(planned), _now()))
        return self.video(vid)

    def video(self, ref: Any) -> dict[str, Any]:
        r = None
        if isinstance(ref, int) or str(ref).strip().isdigit():
            r = self.one("SELECT * FROM videos WHERE id = ?", (int(ref),))
        if r is None:
            r = self.one("SELECT * FROM videos WHERE slug = ?", (slug(str(ref)),))
        if r is None:
            found = self.rows("SELECT * FROM videos WHERE title LIKE ?", (f"%{str(ref).strip()}%",))
            if len(found) > 1:
                raise ValueError(f"'{ref}' matches {len(found)} videos: "
                                 + ", ".join(v["title"] for v in found[:5]) + " - which one?")
            r = found[0] if found else None
        if r is None:
            raise KeyError(f"no video {ref!r}")
        r["minutes"] = round(self.seconds_for(r["words"]) / 60, 1)
        return r

    def videos(self, stage: str = "") -> list[dict[str, Any]]:
        if stage and stage not in STAGES:
            raise ValueError(f"stage is one of {', '.join(STAGES)}")
        rows = self.rows("SELECT id FROM videos" + (" WHERE stage = ?" if stage else "") + " ORDER BY id",
                         (stage,) if stage else ())
        return [self.video(r["id"]) for r in rows]

    def set_stage(self, ref: Any, stage: str, *, url: str = "", when: str = "") -> dict[str, Any]:
        stage = (stage or "").strip().lower()
        if stage not in STAGES:
            raise ValueError(f"stage is one of {', '.join(STAGES)}")
        v = self.video(ref)
        published = (_date(when) or date.today().isoformat()) if stage == "published" else v["published"]
        self.write("UPDATE videos SET stage = ?, published = ?, url = COALESCE(NULLIF(?, ''), url) WHERE id = ?",
                   (stage, published, url.strip(), v["id"]))
        return self.video(v["id"])

    def save_script(self, ref: Any, text: str) -> dict[str, Any]:
        if not (text or "").strip():
            raise ValueError("the script is empty")
        v = self.video(ref)
        path = os.path.join(self.folder("Scripts"), f"{v['slug']}.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text.rstrip() + "\n")
        words = sum(s["words"] for s in parse_script(text))
        self.write("UPDATE videos SET script = ?, words = ?, stage = CASE WHEN stage = 'idea' THEN 'scripting' "
                   "ELSE stage END WHERE id = ?", (path, words, v["id"]))
        return self.video(v["id"])

    def _script(self, v: dict[str, Any]) -> list[dict[str, Any]]:
        if not v["script"] or not os.path.isfile(v["script"]):
            raise ValueError(f"'{v['title']}' has no script yet")
        with open(v["script"], encoding="utf-8") as fh:
            sections = parse_script(fh.read())
        if not sections:
            raise ValueError(f"the script for '{v['title']}' has no spoken lines")
        return sections

    def shot_list(self, ref: Any) -> dict[str, Any]:
        v = self.video(ref)
        rows, n = [], 0
        for s in self._script(v):
            for p in s["paragraphs"]:
                n += 1
                rows.append({"#": n, "section": s["heading"], "type": "A-roll", "what": p[:80],
                             "seconds": round(self.seconds_for(len(p.split())))})
            for b in s["broll"]:
                n += 1
                rows.append({"#": n, "section": s["heading"], "type": "B-roll", "what": b, "seconds": ""})
        path = os.path.join(self.folder("Shot lists"), f"{v['slug']}.csv")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["#", "section", "type", "what", "seconds"])
            w.writeheader()
            w.writerows(rows)
        return {"video": v["title"], "path": path, "shots": rows,
                "a_roll": sum(r["type"] == "A-roll" for r in rows), "b_roll": sum(r["type"] == "B-roll" for r in rows)}

    def chapters(self, ref: Any) -> dict[str, Any]:
        """Timestamps from the script's headings at the creator's pace. A
        section shorter than a chapter may be never gets one: it stays in the
        chapter before. An opening too short to stand alone (a 5-second hook)
        takes the name of the first real section after it, still at 0:00."""
        v = self.video(ref)
        out: list[list[Any]] = []
        at = 0.0
        for s in self._script(v):
            length = self.seconds_for(s["words"])
            if not out:
                out.append([0.0, s["heading"]])
            elif length >= CHAPTER_MIN_SECONDS:
                if at - out[-1][0] < CHAPTER_MIN_SECONDS:
                    out[-1][1] = s["heading"]
                else:
                    out.append([at, s["heading"]])
            at += length
        chapters = [{"at": stamp(t), "title": title} for t, title in out]
        return {"video": v["title"], "chapters": chapters, "length": stamp(at),
                "problems": check_chapters(chapters)}

    def write_upload(self, ref: Any, title: str, description: str, tags: list[str],
                     chapters: Optional[list[dict[str, str]]] = None) -> dict[str, Any]:
        v = self.video(ref)
        tags = [t.strip() for t in (tags or []) if str(t).strip()]
        chapters = list(chapters or [])
        problems = check_upload(title, description, tags) + (check_chapters(chapters) if chapters else [])
        body = description.rstrip()
        if chapters:
            body += "\n\n" + "\n".join(f"{c['at']} {c['title']}" for c in chapters)
        path = os.path.join(self.folder("Uploads"), f"{v['slug']}.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"TITLE\n{title.strip()}\n\nDESCRIPTION\n{body}\n\nTAGS\n{', '.join(tags)}\n")
        self.write("UPDATE videos SET upload = ? WHERE id = ?", (path, v["id"]))
        return {"video": v["title"], "path": path, "problems": problems, "title_length": len(title.strip()),
                "description_length": len(body), "tags_length": len(", ".join(tags))}

    # -- deals -------------------------------------------------------------------------------------------------

    def add_deal(self, brand: str, deliverable: str, *, fee: Any = 0, due: str = "", video: Any = None,
                 status: str = "agreed", notes: str = "") -> dict[str, Any]:
        brand, deliverable = (brand or "").strip(), (deliverable or "").strip()
        if not brand or not deliverable:
            raise ValueError("a deal needs the brand and what you owe them")
        if status not in DEAL_STATES:
            raise ValueError(f"status is one of {', '.join(DEAL_STATES)}")
        fee = float(fee or 0)
        if fee < 0:
            raise ValueError("the fee cannot be negative")
        vid = self.video(video)["id"] if video not in (None, "") else None
        did = self.write("INSERT INTO deals(brand, fee, deliverable, video_id, due, status, notes, created) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (brand, fee, deliverable, vid, _date(due), status,
                                                             notes.strip(), _now()))
        return self.deal(did)

    def deal(self, deal_id: Any) -> dict[str, Any]:
        r = self.one("SELECT * FROM deals WHERE id = ?", (int(deal_id),))
        if r is None:
            raise KeyError(f"no deal #{deal_id}")
        return r

    def deals(self, status: str = "") -> list[dict[str, Any]]:
        if status == "open":
            return self.rows("SELECT * FROM deals WHERE status IN ('pitched', 'agreed') ORDER BY due = '', due, id")
        return self.rows("SELECT * FROM deals" + (" WHERE status = ?" if status else "") + " ORDER BY due = '', due, id",
                         (status,) if status else ())

    def set_deal(self, deal_id: Any, status: str) -> dict[str, Any]:
        if status not in DEAL_STATES:
            raise ValueError(f"status is one of {', '.join(DEAL_STATES)}")
        d = self.deal(deal_id)
        self.write("UPDATE deals SET status = ? WHERE id = ?", (status, d["id"]))
        return self.deal(d["id"])

    def deals_due(self, as_of: str = "", days: int = 7) -> dict[str, list[dict[str, Any]]]:
        today = date.fromisoformat(_date(as_of) or date.today().isoformat())
        soon, late = [], []
        for d in self.deals("open"):
            if d["status"] != "agreed" or not d["due"]:
                continue
            left = (date.fromisoformat(d["due"]) - today).days
            if left < 0:
                late.append({**d, "days_late": -left})
            elif left <= days:
                soon.append({**d, "days_left": left})
        return {"late": late, "soon": soon}

    # -- channel numbers -----------------------------------------------------------------------------------------

    def import_stats(self, path: str) -> dict[str, Any]:
        """A YouTube Studio 'Content' export (Table data.csv), or any CSV with
        a title and views column."""
        if not os.path.isfile(path):
            raise FileNotFoundError(f"no file {path}")
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            cols = {k.lower().strip(): k for k in reader.fieldnames or []}
            pick = lambda *names: next((cols[n] for n in names if n in cols), None)  # noqa: E731
            c_title = pick("video title", "title", "content")
            c_views = pick("views")
            if not c_title or not c_views:
                raise ValueError("the file needs a title column and a views column")
            c_hours, c_subs = pick("watch time (hours)", "watch hours"), pick("subscribers")
            c_impr, c_ctr = pick("impressions"), pick("impressions click-through rate (%)", "ctr")
            stamp_, rows = _now(), []
            for raw in reader:
                title = (raw.get(c_title) or "").strip()
                if not title or title.lower() == "total":
                    continue
                num = lambda c, kind=float: kind(float(str(raw.get(c) or 0).replace(",", "") or 0)) if c else 0  # noqa: E731
                rows.append((stamp_, os.path.basename(path), title, num(c_views, int), num(c_hours), num(c_subs, int),
                             num(c_impr, int), num(c_ctr) if c_ctr and raw.get(c_ctr) not in (None, "") else None))
        if not rows:
            raise ValueError("no video rows in the file")
        with self.lock, self.db:
            self.db.executemany("INSERT INTO stats(imported, source, title, views, watch_hours, subscribers, "
                                "impressions, ctr) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
        return self.stats()

    def stats(self, top: int = 5) -> dict[str, Any]:
        last = self.one("SELECT max(imported) AS at FROM stats")
        if not last or not last["at"]:
            return {"imported": "", "videos": 0, "views": 0, "top": [], "low_ctr": []}
        rows = self.rows("SELECT * FROM stats WHERE imported = ? ORDER BY views DESC", (last["at"],))
        ctrs = [r["ctr"] for r in rows if r["ctr"] is not None]
        avg = sum(ctrs) / len(ctrs) if ctrs else None
        low = [r for r in rows if avg is not None and r["ctr"] is not None and r["impressions"] >= 1000
               and r["ctr"] < avg / 2]
        return {"imported": last["at"], "videos": len(rows), "views": sum(r["views"] for r in rows),
                "watch_hours": round(sum(r["watch_hours"] for r in rows), 1),
                "subscribers": sum(r["subscribers"] for r in rows), "avg_ctr": round(avg, 2) if avg else None,
                "top": rows[:max(1, int(top))], "low_ctr": low}

    # -- the whole picture ---------------------------------------------------------------------------------------

    def overview(self, as_of: str = "") -> dict[str, Any]:
        today = _date(as_of) or date.today().isoformat()
        stages = {s: 0 for s in STAGES}
        for r in self.rows("SELECT stage, count(*) AS n FROM videos GROUP BY stage"):
            stages[r["stage"]] = r["n"]
        slipped = self.rows("SELECT id, title, planned, stage FROM videos WHERE planned != '' AND planned < ? "
                            "AND stage != 'published' ORDER BY planned", (today,))
        nxt = self.one("SELECT title, planned FROM videos WHERE planned >= ? AND stage != 'published' "
                       "ORDER BY planned LIMIT 1", (today,))
        ready_no_upload = self.rows("SELECT title FROM videos WHERE stage = 'ready' AND upload = ''")
        due = self.deals_due(today)
        return {"channel": self.name, "platform": self.cfg.get("platform") or "YouTube", "as_of": today,
                "stages": stages, "in_production": sum(stages[s] for s in ("scripting", "filming", "editing")),
                "slipped": slipped, "next": nxt, "ready_without_upload": [r["title"] for r in ready_no_upload],
                "deals_late": due["late"], "deals_soon": due["soon"], "stats": self.stats(3)}


def check_upload(title: str, description: str, tags: list[str]) -> list[str]:
    problems = []
    t = (title or "").strip()
    if not t:
        problems.append("the title is empty")
    elif len(t) > TITLE_MAX:
        problems.append(f"the title is {len(t)} characters; YouTube allows {TITLE_MAX}")
    if len(description or "") > DESCRIPTION_MAX:
        problems.append(f"the description is {len(description)} characters; YouTube allows {DESCRIPTION_MAX}")
    if any(c in (t + (description or "")) for c in "<>"):
        problems.append("YouTube does not allow < or > in the title or description")
    joined = ", ".join(tags)
    if len(joined) > TAGS_MAX:
        problems.append(f"the tags come to {len(joined)} characters; YouTube allows {TAGS_MAX}")
    return problems


def check_chapters(chapters: list[dict[str, str]]) -> list[str]:
    """YouTube shows chapters only if the first is at 0:00, there are at
    least three, and each lasts at least ten seconds."""
    if not chapters:
        return []
    problems = []
    secs = [seconds_of(c["at"]) for c in chapters]
    if secs[0] != 0:
        problems.append("the first chapter must start at 0:00")
    if len(chapters) < CHAPTERS_MIN:
        problems.append(f"YouTube needs at least {CHAPTERS_MIN} chapters; there are {len(chapters)}")
    for (a, b), c in zip(zip(secs, secs[1:]), chapters[1:]):
        if b - a < CHAPTER_MIN_SECONDS:
            problems.append(f"'{c['title']}' starts {b - a} s after the one before; each needs "
                            f"{CHAPTER_MIN_SECONDS} s")
    return problems

