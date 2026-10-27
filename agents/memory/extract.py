"""Facts from what the person says, by rules (MEM-01).

Only their own words, and only plain statements: "Lee's email is
lee@new.com", "my phone number is 082 555 0101", "I work at Acme", "Sam is
my accountant". Nothing is guessed from a question, and nothing that looks
like a password, a code or a card number is ever kept. A model-based
extractor (Graphiti's way, catching facts said in passing) can be added
behind a switch; it costs a model call per turn, so it waits on the
owner's yes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Never kept, whatever the sentence says.
_SECRET = re.compile(r"\b(password|passcode|pin|otp|one[- ]time|verification code|security code|cvv|cvc|"
                     r"card number|iban|social security|ssn)\b|\b(?:\d[ -]?){13,19}\b", re.I)
_QUESTION = re.compile(r"\?\s*$|^\s*(what|who|where|when|why|how|which|is|are|do|does|can|could|would)\b", re.I)

_PRED = {"email": "email", "e-mail": "email", "email address": "email", "phone": "phone", "phone number": "phone",
         "number": "phone", "mobile": "phone", "cell": "phone", "address": "address", "home address": "address",
         "birthday": "birthday", "website": "website", "time zone": "timezone", "timezone": "timezone",
         "company": "company", "job title": "job_title", "title": "job_title"}
_KEY = "|".join(sorted((re.escape(k) for k in _PRED), key=len, reverse=True))
_NAME = r"([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)"

_RULES = [
    # "Lee's email is lee@new.com", "Lee's new phone number is ..."
    (re.compile(_NAME + r"'s\s+(?:new\s+)?(" + _KEY + r")\s+(?:is|=|:)\s+(.+)", re.I), "person"),
    # "my email is ...", "my new address is ..."
    (re.compile(r"\bmy\s+(?:new\s+)?(" + _KEY + r")\s+(?:is|=|:)\s+(.+)", re.I), "me"),
    # "I live in Cape Town", "I work at Acme", "I work for Acme"
    (re.compile(r"\bI\s+(live|work)\s+(?:in|at|for)\s+(.+)", re.I), "me-verb"),
    # "Sam is my accountant"
    (re.compile(_NAME + r"\s+is\s+my\s+([a-z][a-z ]{2,30})$", re.I), "role"),
]


@dataclass
class Learned:
    subject: str
    predicate: str
    value: str
    text: str
    kind: str = "person"


def _clean(value: str) -> str:
    value = re.split(r"\s+(?:and|but|so|because)\s+|[;!]|\.\s|\.$", value.strip(), maxsplit=1)[0]
    return value.strip(" .,'\"")[:200]


def from_words(said: str) -> list[Learned]:
    """Plain statements of fact in what they said; [] for a question or
    anything secret-looking."""
    text = " ".join((said or "").split())
    if not text or _QUESTION.search(text) or _SECRET.search(text):
        return []
    text = re.sub(r"^(?:please\s+)?(?:remember|note|keep in mind)(?:\s+that)?\s+", "", text, flags=re.I)
    out: list[Learned] = []
    for sentence in re.split(r"(?<=[.!])\s+", text):
        for rule, shape in _RULES:
            m = rule.search(sentence)
            if not m:
                continue
            if shape == "person":
                name, key, value = m.group(1), _PRED[m.group(2).lower()], _clean(m.group(3))
                if value:
                    out.append(Learned(name, key, value, f"{name}'s {key.replace('_', ' ')} is {value}"))
            elif shape == "me":
                key, value = _PRED[m.group(1).lower()], _clean(m.group(2))
                if value:
                    out.append(Learned("me", key, value, f"Their {key.replace('_', ' ')} is {value}", "self"))
            elif shape == "me-verb":
                verb, value = m.group(1).lower(), _clean(m.group(2))
                if value:
                    key = "lives_in" if verb == "live" else "works_at"
                    out.append(Learned("me", key, value, f"They {'live in' if verb == 'live' else 'work at'} "
                                                         f"{value}", "self"))
            elif shape == "role":
                name, role = m.group(1), _clean(m.group(2))
                if role and name.lower() not in ("this", "that", "it", "he", "she", "they"):
                    out.append(Learned(name, "role", role, f"{name} is their {role}"))
            break
    return out
