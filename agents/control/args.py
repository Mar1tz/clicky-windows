"""Binding what was said to what an action takes.

This module exists because of one field report: the user asked Tora to
**reduce** the volume and it got **louder**. The mechanism was not a
mis-heard word or a bad model call. It was this:

    async def volume(direction: str = "up", steps: int = 2)

plus two places that threw arguments away without saying so — a JSON
parse failure became `{}`, and an argument whose name the action did not
recognise was popped off the dict. Either way the action ran with its
defaults, and the default was "up".

So the rule here is: **an argument is never silently lost.** Binding
either produces the exact values the action declared, or it produces a
sentence explaining what was wrong, and the action does not run. A
misunderstanding should cost a second and a question, never the opposite
of what was asked.

`bind` is pure and takes no I/O, so the whole contract is testable
without a desktop.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from .model import Param

# What people actually say, mapped to the values actions declare. Voice
# does not produce enum members: "turn it down a bit" reaches us as
# "down", "lower", "quieter", "reduce" or "softer" depending on the model's
# mood, and every one of them means the same thing.
SYNONYMS: dict[str, str] = {
    # direction
    "down": "down", "lower": "down", "quieter": "down", "reduce": "down",
    "softer": "down", "decrease": "down", "less": "down", "minus": "down",
    "up": "up", "higher": "up", "louder": "up", "raise": "up",
    "increase": "up", "more": "up", "plus": "up",
    "mute": "mute", "silence": "mute", "muted": "mute", "off": "mute",
    "unmute": "unmute", "on": "unmute",
    # booleans, which arrive as words
    "true": "true", "yes": "true", "false": "false", "no": "false",
}

_TRUE = {"true", "1", "yes", "y", "on"}
_FALSE = {"false", "0", "no", "n", "off"}


def read_arguments(raw: Any) -> tuple[Optional[dict[str, Any]], str]:
    """Whatever the model sent, as a dict — or (None, why not).

    The live model is asked for a JSON object in a string and sends one
    most of the time. The rest of the time it sends Python-style single
    quotes, a bare value, `key=value`, or JSON with a sentence wrapped
    around it. Each of those used to be thrown away silently; each of them
    is recoverable, and the one that is not has to be said out loud.

    Returns the dict and a short note on how it was read (empty when it was
    plain JSON), or None and the reason it could not be.
    """
    if isinstance(raw, dict):
        return dict(raw), ""
    text = ("" if raw is None else str(raw)).strip()
    if not text:
        return {}, ""

    import ast
    import json

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed, ""
    except ValueError:
        pass

    # Python-ish: {'direction': 'down'}, or a tuple/list we cannot use.
    try:
        parsed = ast.literal_eval(text)
        if isinstance(parsed, dict):
            return {str(k): v for k, v in parsed.items()}, "as a Python literal"
    except (ValueError, SyntaxError):
        pass

    # JSON with prose around it: "sure — {\"direction\": \"down\"}".
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            parsed = json.loads(text[start:end + 1])
            if isinstance(parsed, dict):
                return parsed, "from the JSON inside a sentence"
        except ValueError:
            pass

    # key=value, key: value — one pair or several.
    pairs: dict[str, Any] = {}
    for chunk in text.replace(";", ",").split(","):
        for sep in ("=", ":"):
            if sep in chunk:
                key, _, value = chunk.partition(sep)
                key, value = key.strip().strip("\"'{} "), value.strip().strip("\"'{} ")
                if key and value:
                    pairs[key] = value
                break
    if pairs:
        return pairs, "as key=value pairs"

    # A single bare word: "down". Meaningless here — only the action knows
    # which parameter it belongs to — so it is handed back for bind() to
    # place, under a name no parameter uses.
    if " " not in text:
        return {_BARE: text}, "as a single bare value"
    return None, f"{text[:60]!r} is not a JSON object"


# The key a lone value arrives under, so bind() can put it on the action's
# one required parameter instead of refusing it.
_BARE = "__bare__"


class BindError(ValueError):
    """The arguments cannot be used. The message is written to be read
    back to the model (and, shortened, to the user)."""

    def __init__(self, message: str, *, expected: Optional[list[dict]] = None):
        super().__init__(message)
        self.expected = expected or []


def _closest(name: str, known: Iterable[str]) -> str:
    """The most plausible parameter the caller meant, or ''."""
    import difflib
    matches = difflib.get_close_matches(name.lower(), [k.lower() for k in known],
                                        n=1, cutoff=0.6)
    return matches[0] if matches else ""


def _coerce(value: Any, param: Param) -> Any:
    """One value, as the type the action declared. Raises BindError."""
    kind = (param.type or "string").lower()
    if kind in ("integer", "number"):
        try:
            number = float(str(value).strip())
        except (TypeError, ValueError):
            raise BindError(
                f"'{param.name}' has to be a number, not {value!r}") from None
        if kind == "integer":
            if number != int(number):
                raise BindError(f"'{param.name}' has to be a whole number, "
                                f"not {value!r}")
            return int(number)
        return number
    if kind == "boolean":
        text = str(value).strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        raise BindError(f"'{param.name}' has to be yes or no, not {value!r}")
    return str(value).strip() if not isinstance(value, str) else value.strip()


def _match_choice(value: str, choices: tuple[str, ...]) -> Optional[str]:
    """A declared choice for what the user said, or None."""
    text = str(value).strip().lower()
    lookup = {c.lower(): c for c in choices}
    if text in lookup:
        return lookup[text]
    mapped = SYNONYMS.get(text)
    if mapped and mapped.lower() in lookup:
        return lookup[mapped.lower()]
    # "down a bit", "turn it up" — the choice as a word inside a phrase.
    for word in text.replace("-", " ").split():
        candidate = SYNONYMS.get(word, word)
        if candidate.lower() in lookup:
            return lookup[candidate.lower()]
    return None


def describe(params: tuple[Param, ...]) -> list[dict]:
    """The parameter list, in the shape an error hands back to the model."""
    return [{"name": p.name, "required": p.required, "type": p.type,
             "choices": list(p.choices), "description": p.description}
            for p in params]


def bind(key: str, params: tuple[Param, ...], arguments: dict[str, Any]) -> dict[str, Any]:
    """The arguments an action should be called with. Raises BindError.

    Three rules, each of which was a bug before it was a rule:

      * an unrecognised name is refused, with the nearest real one
        suggested — it used to be dropped, and the action ran on defaults;
      * a value outside a declared `choices` set is refused after the
        synonym table has had a go — `choices` used to be decoration;
      * a missing required argument is refused, naming what is missing.
    """
    known = {p.name: p for p in params}
    out: dict[str, Any] = {}
    incoming = dict(arguments or {})

    # A lone value ("down") belongs to the action's only required
    # parameter, if it has exactly one. Otherwise there is nothing to
    # guess with, and guessing is what this module exists to stop.
    bare = incoming.pop(_BARE, None)
    if bare is not None:
        wanted = [p for p in params if p.required] or list(params)
        if len(wanted) == 1:
            incoming[wanted[0].name] = bare
        else:
            takes = ", ".join(known) or "no arguments"
            raise BindError(
                f"'{bare}' on its own does not say which argument it is. "
                f"{key} takes: {takes}.", expected=describe(params))

    for name, value in incoming.items():
        param = known.get(name)
        if param is None:
            near = _closest(name, known)
            takes = ", ".join(known) or "no arguments"
            hint = f"; did you mean '{near}'?" if near else ""
            raise BindError(
                f"{key} has no argument called '{name}'{hint} It takes: {takes}.",
                expected=describe(params))
        if value is None or (isinstance(value, str) and not value.strip()):
            continue                      # an empty value is an absent one
        if param.choices:
            chosen = _match_choice(value, param.choices)
            if chosen is None:
                raise BindError(
                    f"'{value}' is not one of the values {key} accepts for "
                    f"'{param.name}': {', '.join(param.choices)}.",
                    expected=describe(params))
            out[param.name] = chosen
            continue
        out[param.name] = _coerce(value, param)

    missing = [p.name for p in params if p.required and p.name not in out]
    if missing:
        raise BindError(
            f"{key} needs {', '.join(missing)} and it was not given.",
            expected=describe(params))
    return out
