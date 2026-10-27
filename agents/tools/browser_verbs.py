"""Three verbs for a web page - observe, act, extract (BR-02, after Stagehand).

The page's numbered controls (BR-01, browser_tools.controls) are the low
level; these are the words a job thinks in:

* browser_observe("the send button") - the controls that fit, best first,
  each with what doing it would mean. Reads only.
* browser_act("press send") - does it. The control is, in order: the one
  the job named by number; the one this instruction meant on this page
  before (the action cache); the one clear best fit. Anything less sure
  fails with the candidates, so the job picks a number - and that choice
  is remembered.
* browser_extract("the prices") - tables as rows and the passages that fit,
  for the job to read as data.

The action cache (browser_actions.db in the data folder) is what makes a
weekly job cheap: "download the invoice" on the supplier's page is found
the same way every time with no choosing. When the page changed and the
remembered control is gone, it falls back and the new choice replaces the
old one - the cache heals itself.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from typing import Any, Optional
from urllib.parse import urlsplit

from agents.core import RiskLevel, ToolContext, ToolResult

from .base import Tool, make_spec
from .browser_tools import CATEGORY, _BrowserTool, _by_index, controls, fill_in

log = logging.getLogger("agents.tools.browser")

_STOP = {"the", "a", "an", "on", "in", "to", "of", "for", "and", "click", "press", "tap", "hit", "button", "link",
         "box", "field", "type", "enter", "fill", "select", "choose", "into", "with", "my", "this", "that", "it",
         "please", "then", "go", "open"}
_ROLE_WORDS = {"button": ("button", "submit"), "link": ("link",), "box": ("textbox",), "field": ("textbox",),
               "checkbox": ("checkbox",), "tab": ("tab",), "menu": ("menuitem",), "dropdown": ("select",)}


def _norm(word: str) -> str:
    """"Invoices." and "invoice" are one word to a person."""
    word = word.strip(".")
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def _words(text: str) -> list[str]:
    return [_norm(w) for w in re.findall(r"[a-z0-9@.]+", (text or "").lower())
            if len(w.strip(".")) > 1 and w.strip(".") not in _STOP]


def rank(instruction: str, items: list[dict]) -> list[tuple[float, dict]]:
    """Controls by how well their label fits the instruction, best first."""
    want = set(_words(instruction))
    roles = {r for w, rs in _ROLE_WORDS.items() if w in (instruction or "").lower() for r in rs}
    out = []
    for c in items:
        label = set(_words(c.get("label", "")))
        if not label:
            continue
        score = len(want & label) * 2.0 - 0.25 * len(label - want)
        if want and label == want:
            score += 2.0
        if roles and c.get("role") in roles:
            score += 1.0
        if score > 0:
            out.append((score, c))
    return sorted(out, key=lambda sc: -sc[0])


def _clear_best(ranked: list[tuple[float, dict]]) -> Optional[dict]:
    if not ranked or ranked[0][0] < 2.0:
        return None
    if len(ranked) > 1 and ranked[1][0] >= ranked[0][0] * 0.75:
        return None                          # two near-equals: the job picks, nothing is guessed
    return ranked[0][1]


def _line(c: dict) -> str:
    value = f' = "{c["value"]}"' if c.get("value") else ""
    return f'[{c["i"]}] {c["role"]} "{c["label"]}"{value}'


def _site(url: str) -> str:
    p = urlsplit(url or "")
    return f"{p.scheme}://{p.netloc}{p.path}".rstrip("/") if p.scheme != "file" else f"file://{p.path}"


# -- the action cache -------------------------------------------------------------

class ActionCache:
    """(page, instruction) -> the control it meant (role and label), on disk."""

    def __init__(self, path: str = "") -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path or ":memory:", check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS actions (site TEXT, instruction TEXT, role TEXT, label TEXT, "
                         "uses INTEGER DEFAULT 0, updated REAL, PRIMARY KEY (site, instruction))")
        self._db.commit()

    @staticmethod
    def _key(instruction: str) -> str:
        return " ".join(_words(instruction))

    def get(self, site: str, instruction: str) -> Optional[tuple[str, str]]:
        with self._lock:
            row = self._db.execute("SELECT role, label FROM actions WHERE site=? AND instruction=?",
                                   (site, self._key(instruction))).fetchone()
        return (row[0], row[1]) if row else None

    def put(self, site: str, instruction: str, role: str, label: str) -> None:
        with self._lock:
            self._db.execute("INSERT INTO actions (site, instruction, role, label, uses, updated) VALUES (?,?,?,?,1,?) "
                             "ON CONFLICT(site, instruction) DO UPDATE SET role=excluded.role, label=excluded.label, "
                             "uses=actions.uses+1, updated=excluded.updated",
                             (site, self._key(instruction), role, label, time.time()))
            self._db.commit()


_cache: Optional[ActionCache] = None


def cache() -> ActionCache:
    global _cache
    if _cache is None:
        try:
            from config import data_dir
            path = str(data_dir() / "browser_actions.db")
        except Exception:  # noqa: BLE001 - outside the app: this run only
            path = ""
        _cache = ActionCache(path)
    return _cache


def use_cache(c: Optional[ActionCache]) -> None:
    global _cache
    _cache = c


# -- the verbs ----------------------------------------------------------------------

class BrowserObserve(_BrowserTool):
    spec = make_spec(
        "browser_observe",
        "The controls on this page that fit what you want to do, best first, with numbers. Reads only.",
        risk=RiskLevel.SAFE, category=CATEGORY, replay_safe=True,
        properties={"instruction": {"type": "string"}}, required=["instruction"],
    )

    async def act(self, page, args, ctx):
        instruction = str(args.get("instruction") or "")
        items, _ = await controls(page, ctx)
        ranked = rank(instruction, items)[:5]
        if not ranked:
            return self.fail(f"nothing on {page.url} fits {instruction!r}", candidates=[])
        lines = [_line(c) + (" - type into it" if c["role"] in ("textbox", "select") else " - click it")
                 for _, c in ranked]
        return self.ok("\n".join(lines), candidates=[c["i"] for _, c in ranked], url=page.url)


class BrowserAct(_BrowserTool):
    spec = make_spec(
        "browser_act",
        "Do one thing said in words ('press Send'), with a value to type if any. Unsure: it returns the "
        "candidates - call again with index. Remembered for this page.",
        risk=RiskLevel.HIGH, category=CATEGORY,
        properties={"instruction": {"type": "string"},
                    "value": {"type": "string"},
                    "index": {"type": "integer"}},
        required=["instruction"],
    )

    async def act(self, page, args, ctx):
        instruction = str(args.get("instruction") or "")
        items, _ = await controls(page, ctx)
        site = _site(page.url)
        by_i = {c["i"]: c for c in items}
        chosen, how = None, ""
        if args.get("index") is not None:
            chosen, how = by_i.get(int(args["index"])), "named"
            if chosen is None:
                return self.fail(f"there is no control [{args['index']}] on the page now - observe it again")
        if chosen is None:
            held = cache().get(site, instruction)
            if held is not None:
                chosen = next((c for c in items if (c["role"], c["label"]) == held), None)
                how = "remembered" if chosen is not None else ""
        if chosen is None:
            ranked = rank(instruction, items)
            chosen, how = _clear_best(ranked), "matched"
            if chosen is None:
                return self.fail(f"not sure which control {instruction!r} means - call again with index: "
                                 + "; ".join(_line(c) for _, c in ranked[:5]),
                                 candidates=[c["i"] for _, c in ranked[:5]])
        if chosen.get("secret"):
            return self.fail("that is a password box - the person signs in themselves (ask_to_sign_in)")
        loc = _by_index(page, chosen["i"])
        shown = str(args.get("value") or "")
        if chosen["role"] in ("textbox", "select") and shown:
            value, missing = fill_in(shown, ctx)
            if missing:
                return self.fail(missing)
            if chosen["role"] == "select":
                await loc.first.select_option(label=value)
            else:
                await loc.first.fill(value)
            did = f"typed {shown[:60]!r} into {_line(chosen)}"
        else:
            await loc.first.click()
            did = f"clicked {_line(chosen)}"
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=3000)
        except Exception:  # noqa: BLE001
            pass
        cache().put(site, instruction, chosen["role"], chosen["label"])
        return self.ok(f"{did} ({how})", how=how, index=chosen["i"], url=page.url)


class BrowserExtract(_BrowserTool):
    spec = make_spec(
        "browser_extract",
        "Data off this page for what you need: tables as rows and the passages that fit.",
        risk=RiskLevel.SAFE, category=CATEGORY, replay_safe=True,
        properties={"instruction": {"type": "string"}}, required=["instruction"],
    )

    _JS = """() => {
      const tables = Array.from(document.querySelectorAll('table')).slice(0, 5).map(t => {
        const rows = Array.from(t.rows).map(r => Array.from(r.cells).map(c => c.innerText.trim().replace(/\\s+/g, ' ')));
        return rows.slice(0, 60);
      });
      const blocks = Array.from(document.querySelectorAll('h1,h2,h3,p,li,dt,dd,td,span,div'))
        .filter(e => e.children.length === 0).map(e => e.innerText.trim().replace(/\\s+/g, ' '))
        .filter(t => t.length > 1 && t.length < 400);
      return {tables, blocks: blocks.slice(0, 800)};
    }"""

    async def act(self, page, args, ctx):
        instruction = str(args.get("instruction") or "")
        got = await page.evaluate(self._JS)
        want = set(_words(instruction))
        tables = []
        for rows in got.get("tables") or []:
            if len(rows) >= 2 and rows[0]:
                head = rows[0]
                tables.append([dict(zip(head, r)) for r in rows[1:] if len(r) == len(head)])
        passages = [b for b in got.get("blocks") or [] if want & set(_words(b))][:20]
        if not tables and not passages:
            return self.fail(f"nothing on {page.url} fits {instruction!r}")
        text = json.dumps({"tables": tables, "passages": passages}, ensure_ascii=False)[:20000]
        return self.ok(text, tables=tables, passages=passages, url=page.url)


def tools() -> list[Tool]:
    return [BrowserObserve(), BrowserAct(), BrowserExtract()]
