"""What did the person SAY during a walkthrough — a command, or something else?

The live model decides when to call `guide_step`, and it decides from
audio. Mid-walkthrough, almost everything a person says is not a command:
"what does this button do?", "okay", "hang on", "is this the right one?".
When the model reads one of those as "next", the guide moves on without
them — which is the "it skipped ahead" complaint, the quieter cousin of the
23 September runaway.

This module is the check on that call. It reads the transcript of what the
person actually said and answers one question: which walkthrough command,
if any, is in it? `guide_step` then refuses a move the person did not ask
for, and turns "where is it?" into pointing again rather than moving on.

It is deliberately generous with real commands and strict about everything
else. A transcription of "next" as "necks" still counts; "okay" does not,
because "okay" usually means "understood", and the guide already moves on
by itself the moment the step is actually done.
"""

from __future__ import annotations

import re
from typing import Optional

_WORDS = re.compile(r"[a-z']+")

# Checked in this order: a sentence with "stop" in it is a stop even if it
# also says "next", and "go back" is back even though it contains "go".
_PHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("stop", ("stop", "cancel", "never mind", "nevermind", "quit", "end it",
              "that's enough", "thats enough", "forget it", "exit", "abort",
              "stop the guide", "stop the walkthrough")),
    ("back", ("go back", "back", "previous", "last step", "step back",
              "the one before")),
    ("repeat", ("repeat", "again", "say that again", "what was that",
                "come again", "pardon", "sorry what", "one more time",
                "what did you say", "say it again")),
    ("where", ("where is it", "where's it", "where is that", "where's that",
               "where do i click", "where should i click", "i can't find",
               "i cant find", "can't see it", "cant see it", "show me where",
               "point to it", "point at it", "where is the", "where's the")),
    ("next", ("next", "next step", "done", "i did it", "did it", "i've done it",
              "ive done it", "finished", "i'm done", "im done", "move on",
              "carry on", "keep going", "go on", "continue", "skip", "skip it",
              "clicked it", "i clicked", "that's done", "thats done",
              "what's next", "whats next", "then what", "necks", "nex",
              "nixt")),
)

# What people say to acknowledge an instruction. Not a command: the guide
# moves on by itself when the step is actually done, and advancing on
# "okay" skipped steps people had only just heard.
_ACKS = frozenset({"ok", "okay", "alright", "all right", "right", "got it",
                   "sure", "cool", "yep", "yes", "yeah", "mm", "mhm", "uh huh"})


def _normalise(text: str) -> str:
    return " ".join(_WORDS.findall(str(text or "").lower().replace("’", "'")))


def _has_phrase(text: str, phrase: str) -> bool:
    """Whole-word containment: "next" is in "okay next", not in "nextcloud"."""
    return re.search(rf"(?:^| ){re.escape(phrase)}(?: |$)", text) is not None


# "I'm not done yet" contains "done"; "don't stop" contains "stop". A move
# or a stop is exactly the thing that must not happen on a misread, so any
# of these anywhere in the sentence takes those two off the table. (Back,
# repeat and where survive it: "I didn't hear that, say it again" is still
# a repeat.)
_NEGATIONS = ("not", "don't", "dont", "didn't", "didnt", "haven't", "havent",
              "hasn't", "hasnt", "isn't", "isnt", "wait", "hang on", "hold on",
              "not yet", "nope")
_NEGATABLE = frozenset({"next", "stop"})


def guide_intent(heard: str) -> Optional[str]:
    """next / back / repeat / stop / where, "ack", or None for anything else.

    None means the person said something that is not a walkthrough command
    — almost always a question. "ack" means they acknowledged the step.
    """
    text = _normalise(heard)
    if not text:
        return None
    negated = any(_has_phrase(text, n) for n in _NEGATIONS)
    for intent, phrases in _PHRASES:
        if any(_has_phrase(text, p) for p in phrases):
            if negated and intent in _NEGATABLE:
                return None
            return intent
    if text in _ACKS:
        return "ack"
    return None
