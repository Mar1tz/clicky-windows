"""Skills: how the user likes something done, as a text file.

A skill adds know-how, never an ability. It is a Markdown file with a short
front matter (name, when, and optionally apps, ask_before, every) and a free
text body - steps, prices, people - in the user's own words. Tora carries it
out with the tools she already has, so writing one takes a minute and can't
break anything. docs/agents/SKILLS.md is the plan this implements.

Two folders. The starter skills ship with the app (`skills/` in the repo)
and are never edited in place; the user's own live in the data folder. A
user file with a starter skill's id is that skill, edited - it wins, and
deleting it brings the original back.

Choosing one is two steps, so a message costs what it did before: `match`
is a word check with no network, and only when it finds something does a
model choose between the few it found (`pick`).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("agents.skills")

# The fields a skill's front matter may have; anything else is kept, unused.
FIELDS = ("name", "when", "apps", "ask_before", "every")
STATE_FILE = "skills.json"            # in the user's folder: which skills are off


@dataclass
class Skill:
    id: str                            # the file's name, without .md
    name: str
    when: str                          # in plain words, when Tora uses it
    body: str = ""                     # the steps and what to know
    apps: str = ""
    ask_before: list[str] = field(default_factory=list)
    every: str = ""                    # a schedule ("Friday 16:00"), or ""
    source: str = "starter"            # "starter" | "yours" | "edited"
    path: str = ""
    enabled: bool = True

    def prompt(self) -> str:
        """The skill as the planner and the worker are told it. ask_before
        is said as a hard rule: it may add asking, never remove it (the
        approval gate's own floor is in code, see AgentManager)."""
        parts = [f"## The user's skill: {self.name}",
                 "The user wrote down how they want this done. Follow it; where it is "
                 "silent, use good judgement and the tools you have."]
        if self.apps:
            parts.append(f"Apps: {self.apps}")
        if self.body.strip():
            parts.append(self.body.strip())
        if self.ask_before:
            parts.append("Hard rule - stop and ask the user before: " + "; ".join(self.ask_before) + ".")
        return "\n\n".join(parts)

    def to_text(self) -> str:
        """The file this skill is saved as."""
        head = [f"name: {_one_line(self.name)}", f"when: {_one_line(self.when)}"]
        if self.apps:
            head.append(f"apps: {_one_line(self.apps)}")
        if self.ask_before:
            head.append("ask_before: " + ", ".join(_one_line(a) for a in self.ask_before))
        if self.every:
            head.append(f"every: {_one_line(self.every)}")
        return "---\n" + "\n".join(head) + "\n---\n\n" + self.body.strip() + "\n"

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "when": self.when, "body": self.body,
                "apps": self.apps, "ask_before": list(self.ask_before), "every": self.every,
                "source": self.source, "enabled": self.enabled}


class SkillError(ValueError):
    """A skill file that can't be used, and why - in words for the user."""


def parse(text: str, skill_id: str) -> Skill:
    """A skill from its file. Raises SkillError with the reason."""
    text = (text or "").replace("\r\n", "\n").lstrip("﻿")
    head: dict[str, str] = {}
    body = text
    if text.lstrip().startswith("---"):
        lines = text.lstrip().split("\n")
        try:
            end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
        except StopIteration:
            raise SkillError("the top block has no closing ---") from None
        for line in lines[1:end]:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            key, sep, value = line.partition(":")
            if not sep:
                raise SkillError(f"this line at the top isn't 'name: value': {line.strip()[:60]}")
            head[key.strip().lower().replace(" ", "_").replace("-", "_")] = value.strip()
        body = "\n".join(lines[end + 1:])
    when = head.get("when", "").strip()
    if not when:
        raise SkillError("it has no 'when:' line, so Tora wouldn't know when to use it")
    name = head.get("name", "").strip() or skill_id.replace("-", " ").capitalize()
    asks = [a.strip() for a in re.split(r"[,;]", head.get("ask_before", "")) if a.strip()]
    return Skill(id=skill_id, name=name, when=when, body=body.strip(), apps=head.get("apps", "").strip(),
                 ask_before=asks, every=head.get("every", "").strip())


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s[:60] or "skill"


def starter_dir() -> Path:
    """skills/ next to main.py, or inside the PyInstaller bundle (heytora.spec)."""
    import sys
    base = getattr(sys, "_MEIPASS", None)
    return (Path(base) if base else Path(__file__).resolve().parents[2]) / "skills"


def user_dir() -> Path:
    override = os.environ.get("TORA_SKILLS_DIR")
    if override:
        return Path(override)
    try:
        from config import data_dir
        return data_dir() / "skills"
    except Exception:  # noqa: BLE001 - config is optional for the agent package
        return Path.home() / ".heytora" / "skills"


class SkillLibrary:
    """Every skill, read from disk each time it is asked (they are a few
    small files, and the user may have just edited one in Notepad)."""

    def __init__(self, starter: Optional[Path] = None, user: Optional[Path] = None):
        self.starter = Path(starter) if starter is not None else starter_dir()
        self.user = Path(user) if user is not None else user_dir()
        self.problems: dict[str, str] = {}          # file name -> why it was skipped

    # -- reading ---------------------------------------------------------------------

    def all(self) -> list[Skill]:
        """Every skill, on or off, the user's copy winning; by name."""
        found: dict[str, Skill] = {}
        self.problems = {}
        off = set(self._state().get("off", []))
        for folder, source in ((self.starter, "starter"), (self.user, "yours")):
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.md")):
                try:
                    skill = parse(path.read_text(encoding="utf-8", errors="replace"), path.stem)
                except SkillError as e:
                    self.problems[path.name] = str(e)
                    log.warning("skill %s skipped: %s", path.name, e)
                    continue
                except OSError as e:
                    self.problems[path.name] = f"it could not be read ({e.strerror or e})"
                    continue
                if source == "yours" and skill.id in found:
                    source = "edited"
                skill.source, skill.path = source, str(path)
                skill.enabled = skill.id not in off
                found[skill.id] = skill
        return sorted(found.values(), key=lambda s: s.name.lower())

    def enabled(self) -> list[Skill]:
        return [s for s in self.all() if s.enabled]

    def get(self, skill_id: str) -> Optional[Skill]:
        return next((s for s in self.all() if s.id == skill_id), None)

    def catalogue(self, skills: Optional[list[Skill]] = None) -> str:
        """One line per skill that is on: what a model sees to choose one."""
        return "\n".join(f"- {s.id}: {s.name} - use when {s.when}"
                         for s in (skills if skills is not None else self.enabled()))

    # -- writing (the user's folder only) ---------------------------------------------------

    def save(self, skill: Skill) -> Skill:
        """Write a skill to the user's folder. A new one gets an id from its
        name; editing a starter skill saves a copy with the same id."""
        if not skill.name.strip():
            raise SkillError("give it a name")
        if not skill.when.strip():
            raise SkillError("say when Tora should use it")
        if not skill.id:
            taken = {s.id for s in self.all()}
            base = slug(skill.name)
            skill.id, n = base, 2
            while skill.id in taken:
                skill.id, n = f"{base}-{n}", n + 1
        self.user.mkdir(parents=True, exist_ok=True)
        path = self.user / f"{skill.id}.md"
        path.write_text(skill.to_text(), encoding="utf-8")
        log.info("skill saved: %s", skill.id)
        return self.get(skill.id) or skill

    def delete(self, skill_id: str) -> bool:
        """Delete the user's file. A starter skill can't be deleted, only
        turned off; deleting an edited one brings the original back."""
        path = self.user / f"{skill_id}.md"
        if not path.is_file():
            return False
        path.unlink()
        return True

    def set_enabled(self, skill_id: str, on: bool) -> None:
        state = self._state()
        off = set(state.get("off", []))
        (off.discard if on else off.add)(skill_id)
        state["off"] = sorted(off)
        self._save_state(state)

    def _state(self) -> dict[str, Any]:
        try:
            return json.loads((self.user / STATE_FILE).read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            return {}

    def _save_state(self, state: dict[str, Any]) -> None:
        self.user.mkdir(parents=True, exist_ok=True)
        (self.user / STATE_FILE).write_text(json.dumps(state, indent=2), encoding="utf-8")

    # -- choosing -------------------------------------------------------------------------

    def match(self, text: str, limit: int = 5) -> list[Skill]:
        """The skills whose name or 'when' shares a real word with the
        request - no network, microseconds. Best first."""
        words = _words(text)
        if not words:
            return []
        scored = []
        for s in self.enabled():
            hits = len(words & _words(f"{s.name} {s.when}"))
            if hits:
                scored.append((hits, s))
        scored.sort(key=lambda p: (-p[0], p[1].name))
        return [s for _h, s in scored[:limit]]

    async def pick(self, text: str, provider: Any = None, *, timeout: float = 8.0) -> Optional[Skill]:
        """The skill this request is asking for, or None. A model chooses
        between the word matches; with no model, a single strong match is
        taken, anything less is not (a wrong skill is worse than none)."""
        found = self.match(text)
        if not found:
            return None
        if provider is None:
            strong = [s for s in found if len(_words(text) & _words(f"{s.name} {s.when}")) >= 2]
            return strong[0] if len(strong) == 1 else None
        from agents.core import ProviderMessage
        system = ("You match a user's request to one of their saved skills. A skill fits only if "
                  "the request is asking for exactly that job. Reply with JSON only: "
                  '{"skill": "<id>"} or {"skill": null}.')
        ask = f"Request: {text.strip()}\n\nSkills:\n{self.catalogue(found)}"
        try:
            resp = await asyncio.wait_for(
                provider.complete([ProviderMessage(role="user", content=ask)],
                                  tools=None, system=system, max_tokens=60),
                timeout=timeout)
            raw = resp.text or ""
            m = re.search(r"\{.*\}", raw, re.S)
            chosen = (json.loads(m.group(0)) if m else {}).get("skill")
        except Exception as exc:  # noqa: BLE001 - no skill is always a safe answer
            log.info("skill pick unavailable (%s)", type(exc).__name__)
            return None
        return next((s for s in found if s.id == chosen), None)


# -- words -------------------------------------------------------------------------------

_STOP = frozenset("""
a an and are as at be by can could do does for from get go got have how i if in into is it its
just me my need of on or our please some something that the their them then there these this to
up us use user users want wants was we what when where which who will with would you your yours
make made makes new one all any also time ask asks asked tora hey thing things
""".split())


def _stem(w: str) -> str:
    """Crude on purpose: invoice / invoices / invoicing all become "invoic"."""
    w = w.strip("'")
    for end in ("ing", "ed", "es", "s"):
        if len(w) > len(end) + 3 and w.endswith(end):
            w = w[: -len(end)]
            break
    return w[:-1] if len(w) > 4 and w.endswith("e") else w


def _words(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z][a-z']+", (text or "").lower())
            if len(w) > 2 and w not in _STOP}


def _one_line(s: str) -> str:
    return " ".join(str(s or "").split())
