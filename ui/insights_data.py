"""The Insights page's numbers - only what Tora measured.

Wispr Flow's Insights page is the reference for the shape (tiles, a usage
breakdown, a streak calendar) but not the content: words per minute and
"top 3%" are dictation numbers, and a percentile is a claim we cannot make.
Everything here is counted from the journal (every question, the app in
front, each walkthrough and how long it took, each review) and from the
account's own month. Pure functions over plain rows, so they test without
a database or a window.
"""

from __future__ import annotations

import time
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Iterable, Optional

WEEKS = 20                                  # the streak calendar's width
OWN_WINDOWS = ("ai companion", "heytora")   # asked with Tora in front: no other app


def summarise(rows: Iterable[tuple], walkthrough_seconds: float = 0.0,
              walk_tag: str = "walkthrough", today: Optional[date] = None,
              now: Optional[float] = None) -> dict:
    """rows: (created_at, tags, app_key, streak) for every journal entry."""
    today = today or date.today()
    now = now if now is not None else time.time()
    rows = list(rows)
    questions = [r for r in rows if walk_tag not in (r[1] or "")]
    walks = [r for r in rows if walk_tag in (r[1] or "")]

    week = now - 7 * 86400
    this_week = sum(1 for r in questions if r[0] >= week)
    last_week = sum(1 for r in questions if week - 7 * 86400 <= r[0] < week)
    change = None
    if last_week:
        change = round((this_week - last_week) / last_week * 100)

    apps = Counter()
    for r in rows:
        app = (r[2] or "").strip()
        if app and not any(own in app.lower() for own in OWN_WINDOWS):
            apps[app] += 1
    total_apps = sum(apps.values())
    top = [(name, n, n / total_apps) for name, n in apps.most_common(5)] if total_apps else []

    per_day = Counter(date.fromtimestamp(r[0]) for r in rows)
    current = _streak_back(per_day, today)
    longest = _longest(per_day)

    return {
        "questions": len(questions),
        "this_week": this_week,
        "last_week": last_week,
        "change": change,                          # % against last week, or None
        "walkthroughs": len(walks),
        "guided_minutes": round(walkthrough_seconds / 60),
        "remembered": sum(1 for r in rows if int(r[3] or 0) >= 1),
        "learnable": len(rows),
        "apps": top,                               # [(app, count, share)]
        "apps_used": len(apps),
        "days": per_day,                           # date -> questions
        "streak": current,
        "longest": longest,
        "active_days": sum(1 for n in per_day.values() if n),
    }


def _streak_back(per_day: Counter, today: date) -> int:
    """Days in a row with a question, up to today - or yesterday, while
    today has none yet (the streak is not broken until the day is over)."""
    day = today if per_day.get(today) else today - timedelta(days=1)
    n = 0
    while per_day.get(day):
        n += 1
        day -= timedelta(days=1)
    return n


def _longest(per_day: Counter) -> int:
    best = run = 0
    prev = None
    for day in sorted(d for d, n in per_day.items() if n):
        run = run + 1 if prev is not None and day - prev == timedelta(days=1) else 1
        best = max(best, run)
        prev = day
    return best


def calendar(per_day: Counter, today: Optional[date] = None, weeks: int = WEEKS) -> list[list[tuple[date, int]]]:
    """Columns of weeks (Sunday first), oldest left, ending with this week:
    [[(date, questions), ...7], ...]. Days after today are left out."""
    today = today or date.today()
    start = today - timedelta(days=(today.weekday() + 1) % 7)       # this week's Sunday
    start -= timedelta(weeks=weeks - 1)
    cols = []
    for w in range(weeks):
        col = []
        for d in range(7):
            day = start + timedelta(weeks=w, days=d)
            if day <= today:
                col.append((day, per_day.get(day, 0)))
        cols.append(col)
    return cols


def daily(per_day: Counter, days: int = 21, today: Optional[date] = None) -> list[int]:
    """Questions a day for the last `days` days, oldest first, today last."""
    today = today or date.today()
    return [per_day.get(today - timedelta(days=days - 1 - i), 0) for i in range(days)]


def _ran_seconds(t: dict) -> float:
    """How long an agent job ran: from when it was made to when it last
    moved (its finish, for a finished one). Recorded, not estimated - it
    includes any time it waited on you."""
    start, end = float(t.get("created") or 0), float(t.get("when") or 0)
    return max(0.0, end - start) if start and end else 0.0


def time_worked(walks: Iterable[tuple], tasks: Iterable[dict], today: Optional[date] = None,
                now: Optional[float] = None, days: int = 7) -> dict:
    """Time Tora worked for you - measured, never estimated: the seconds she
    guided each walkthrough (journal) plus the time each finished agent job
    ran (its own timestamps). By day for the last week, this week against
    last week, and the two parts."""
    today = today or date.today()
    now = now if now is not None else time.time()
    per_day: Counter = Counter()
    guided = agents = 0.0
    week = now - 7 * 86400
    this_week = last_week = 0.0
    events = [(float(ts), float(sec), "guide") for ts, sec in walks if sec]
    events += [(float(t.get("when") or 0), _ran_seconds(t), "agent") for t in tasks
                if t.get("status") in ("completed", "failed", "cancelled", "rejected")]
    for ts, sec, kind in events:
        if not sec or not ts:
            continue
        if kind == "guide":
            guided += sec
        else:
            agents += sec
        per_day[date.fromtimestamp(ts)] += sec
        if ts >= week:
            this_week += sec
        elif ts >= week - 7 * 86400:
            last_week += sec
    daily = [round(per_day.get(today - timedelta(days=days - 1 - i), 0.0) / 60) for i in range(days)]
    change = round((this_week - last_week) / last_week * 100) if last_week else None
    return {"total_min": round((guided + agents) / 60), "guided_min": round(guided / 60),
            "agents_min": round(agents / 60), "this_week_min": round(this_week / 60),
            "last_week_min": round(last_week / 60), "change": change, "daily": daily}


def handled(summary: dict, tasks: Iterable[dict]) -> dict:
    """Things Tora handled, by the three things she does: Ask (questions
    answered), Show (walkthroughs guided), Do (agent jobs finished)."""
    do = sum(1 for t in tasks if t.get("status") == "completed")
    ask, show = int(summary.get("questions", 0)), int(summary.get("walkthroughs", 0))
    return {"ask": ask, "show": show, "do": do, "total": ask + show + do}


def minutes(m: int) -> str:
    """"45 min", "1h 12m", "3h"."""
    m = int(m)
    if m < 60:
        return f"{m} min"
    h, r = divmod(m, 60)
    return f"{h}h {r}m" if r else f"{h}h"


def plan(summary: Optional[dict]) -> list[tuple[str, int, Optional[int]]]:
    """This month on the plan: (what, used, limit or None for unlimited)."""
    if not summary:
        return []
    limits, usage = summary.get("limits") or {}, summary.get("usage") or {}
    out = []
    for kind, words in (("talk", "Talk messages"), ("agent", "Agent messages"),
                        ("dictation", "Dictations")):
        if kind in limits:
            lim = limits[kind]
            out.append((words, int(usage.get(kind) or 0), None if lim is None else int(lim)))
    return out


def agents_month(tasks: Iterable[dict], today: Optional[date] = None) -> dict:
    """This month's agent jobs by how they ended (bridge.tasks rows)."""
    today = today or date.today()
    first = datetime(today.year, today.month, 1).timestamp()
    done = failed = running = 0
    recent = []
    for t in tasks:
        if float(t.get("when") or 0) < first:
            continue
        status = t.get("status", "")
        if status == "completed":
            done += 1
        elif status in ("failed", "cancelled"):
            failed += 1
        else:
            running += 1
        recent.append(t)
    ran = sum(_ran_seconds(t) for t in recent if t.get("status") in ("completed", "failed", "cancelled", "rejected"))
    return {"done": done, "failed": failed, "running": running, "recent": recent[:3],
            "ran_min": round(ran / 60)}
