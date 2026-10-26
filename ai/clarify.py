"""Clarify only when it matters (OR-05, the Massive Direction Plan).

An action that sends, pays, deletes or posts can go wrong in four ways -
the wrong person, file, amount or account - and only those are worth a
question. Asked, they are asked once, together ("Who should it go to, and
how much?"), and whatever is safe to prepare (a draft, finding the file)
is done meanwhile. Anything else goes ahead without asking.
"""
from __future__ import annotations

import re

_W = re.IGNORECASE
_SEND = re.compile(r"\b(send|email|e-mail|message|text|forward|share|invite)\b", _W)
_PAY = re.compile(r"\b(pay|transfer|tip|refund|send\s+money|wire)\b", _W)
_DELETE = re.compile(r"\b(delete|remove|erase|trash|archive)\b", _W)
_POST = re.compile(r"\b(post|tweet|publish|upload)\b", _W)

_TO_SOMEONE = re.compile(r"\bto\s+(?!do\b|the\s+(?:team|channel|group)\b)([A-Z][\w'-]+|me|my\s+\w+|him|her|them|"
                         r"[\w.+-]+@[\w-]+\.\w+|everyone|the\s+(?:team|channel|group))", re.I)
# "pay Sam 40 dollars": the person straight after the verb, by their capital.
_PAY_WHO = re.compile(r"\b(?i:pay|tip|refund)\s+(?!The\b|It\b|This\b|That\b|My\b)([A-Z][\w'-]+)")
_AMOUNT = re.compile(r"(\$|£|€|\b(?:usd|eur|gbp|dollars?|pounds?|euros?|kr)\b|\b\d+(?:[.,]\d+)?\b)", _W)
_A_FILE = re.compile(r"\b[\w\- ]+\.(?:docx?|xlsx?|pptx?|pdf|txt|csv|png|jpe?g|zip)\b|[a-z]:\\|\b(the|my)\s+\w+\s+"
                     r"(?:file|folder|document|doc|photo|picture|spreadsheet|sheet|email|draft|message)\b", _W)
_AN_ACCOUNT = re.compile(r"\b(linkedin|twitter|x\.com|facebook|instagram|slack|teams|discord|gmail|outlook|"
                         r"youtube|tiktok|github|my\s+\w+\s+account)\b", _W)

QUESTIONS = {"recipient": "who should it go to", "amount": "how much",
             "file": "which file", "account": "on which account"}


def missing(text: str, *, has_file: bool = False, has_referent: bool = False) -> list[str]:
    """What a send / pay / delete / post would need and was not told."""
    t = text or ""
    out: list[str] = []
    paying = bool(_PAY.search(t))
    if paying:
        if not _AMOUNT.search(t):
            out.append("amount")
        if not (_TO_SOMEONE.search(t) or _PAY_WHO.search(t)):
            out.append("recipient")
    elif _SEND.search(t) and not _TO_SOMEONE.search(t):
        out.append("recipient")
    if _DELETE.search(t) and not (has_file or has_referent or _A_FILE.search(t)):
        out.append("file")
    if _POST.search(t) and not _AN_ACCOUNT.search(t):
        out.append("account")
    return out


def question(slots: list[str]) -> str:
    """One bundled question for everything missing."""
    parts = [QUESTIONS[s] for s in slots]
    if not parts:
        return ""
    joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"Before I do that: {joined}?"
