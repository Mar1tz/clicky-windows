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

# Letters of any alphabet, not a-z: "nächste" was read as "n chste" and
# refused as a question on 24 Sep (LG-03).
_WORDS = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)*")

# Checked in this order: a sentence with "stop" in it is a stop even if it
# also says "next", "go back" is back even though it contains "go", and
# "it's not working again" is a cry for help, not a request to hear the same
# words a second time.
_PHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("stop", ("stop", "cancel", "never mind", "nevermind", "quit", "end it",
              "that's enough", "thats enough", "forget it", "exit", "abort",
              "stop the guide", "stop the walkthrough",
              # German, Afrikaans/Dutch, Spanish, French
              "stopp", "aufhören", "abbrechen", "beenden", "hou op", "stoppen",
              "para", "detente", "arrête", "arrête")),
    ("pause", ("pause", "pause it", "pause the guide", "pause the walkthrough",
               "take a break", "let's take a break", "lets take a break",
               "pauze", "pausa")),
    ("back", ("go back", "back", "previous", "last step", "step back",
              "the one before",
              "zurück", "zurueck", "terug", "atrás", "atras", "retour")),
    ("where", ("where is it", "where's it", "where is that", "where's that",
               "where do i click", "where should i click", "i can't find",
               "i cant find", "can't see it", "cant see it", "show me where",
               "point to it", "point at it", "where is the", "where's the",
               "help me find", "i don't see it", "i dont see it")),
    ("do_it", ("do it for me", "you do it", "just do it", "do that for me",
               "click it for me", "press it for me", "do the rest",
               "do it yourself")),
    ("help", ("i'm stuck", "im stuck", "i am stuck", "stuck", "help",
              "help me", "it's not working", "its not working",
              "it doesn't work", "it doesnt work", "that didn't work",
              "that didnt work", "i don't know how", "i dont know how",
              "i don't get it", "i dont get it", "i'm lost", "im lost")),
    ("repeat", ("repeat", "again", "say that again", "what was that",
                "come again", "pardon", "sorry what", "one more time",
                "what did you say", "say it again",
                "nochmal", "noch mal", "noch einmal", "wiederholen", "wie bitte",
                "weer", "herhaal", "otra vez", "repite", "répète", "encore")),
    ("next", ("next", "next step", "done", "i did it", "did it", "i've done it",
              "ive done it", "finished", "i'm done", "im done", "move on",
              "carry on", "keep going", "go on", "continue", "skip", "skip it",
              "clicked it", "i clicked", "that's done", "thats done",
              "what's next", "whats next", "then what", "necks", "nex",
              "nixt",
              # Other languages (LG-03). The live model understands them and
              # calls guide_step; this check must not overrule it.
              "nächste", "nächster", "nächstes", "nächster schritt", "weiter",
              "fertig", "erledigt", "geschafft", "gemacht",       # German
              "volgende", "klaar", "gedoen", "verder",            # Afrikaans / Dutch
              "siguiente", "listo", "hecho", "sigue",             # Spanish
              "suivant", "fini", "c'est fait",                    # French
              "próximo", "proximo", "pronto", "feito",            # Portuguese
              "prossimo", "avanti", "fatto",                      # Italian
              "nästa", "klart", "färdig")),                       # Swedish
)

# What people say to acknowledge an instruction. Not a command: the guide
# moves on by itself when the step is actually done, and advancing on
# "okay" skipped steps people had only just heard.
_ACKS = frozenset({"ok", "okay", "alright", "all right", "right", "got it",
                   "sure", "cool", "yep", "yes", "yeah", "mm", "mhm", "uh huh",
                   "ja", "alles klar", "gut", "verstanden", "sí", "si", "oui",
                   "d'accord", "sim"})

# "next" as the transcript heard it: "net" on 24 Sep, refused, and the model -
# which heard "next" in the audio - called guide_step 41 times. Real words,
# so they only count when they are the whole of a short utterance: "net" is
# next, "what net do I pick" is not.
_NEXT_MISHEARD = frozenset({"net", "nets", "neck", "nest", "text", "nekst", "necst",
                            "nexed", "knacks"})

# The start of a question, in the languages above. A short utterance that
# starts like this is never taken for a command, however often it is asked.
_QUESTION_STARTS = frozenset({
    "what", "how", "where", "why", "when", "which", "who", "is", "are", "can",
    "could", "does", "do", "should", "will", "would", "was", "wie", "wo",
    "warum", "wann", "welche", "welcher", "ist", "kann", "wat", "hoe", "waar",
    "qué", "que", "cómo", "como", "dónde", "donde", "quoi", "comment", "où"})


def _normalise(text: str) -> str:
    return " ".join(_WORDS.findall(str(text or "").casefold().replace("’", "'")))


def _has_phrase(text: str, phrase: str) -> bool:
    """Whole-word containment: "next" is in "okay next", not in "nextcloud"."""
    return re.search(rf"(?:^| ){re.escape(phrase)}(?: |$)", text) is not None


# "I'm not done yet" contains "done"; "don't stop" contains "stop". A move,
# a stop or a pause is exactly the thing that must not happen on a misread,
# so any of these anywhere in the sentence takes those off the table, and so
# does "don't do it for me". (Back, repeat, where and help survive it: "I
# didn't hear that, say it again" is still a repeat, and "it doesn't work"
# is the very thing someone stuck says.) A negated command does not end the
# reading - "I don't know how, do it for me" goes on to be a call for help.
_NEGATIONS = ("not", "don't", "dont", "didn't", "didnt", "haven't", "havent",
              "hasn't", "hasnt", "isn't", "isnt", "wait", "hang on", "hold on",
              "not yet", "nope",
              "nicht", "noch nicht", "warte", "moment", "nee", "nog nie",
              "todavía no", "pas encore")
_NEGATABLE = frozenset({"next", "stop", "pause", "do_it"})


def guide_intent(heard: str) -> Optional[str]:
    """next / back / repeat / stop / pause / where / help / do_it, "ack", or
    None for anything else.

    None means the person said something that is not a walkthrough command
    — almost always a question. "ack" means they acknowledged the step.
    "skip" reads as next: it is the same move.
    """
    text = _normalise(heard)
    if not text:
        return None
    negated = any(_has_phrase(text, n) for n in _NEGATIONS)
    for intent, phrases in _PHRASES:
        if any(_has_phrase(text, p) for p in phrases):
            if negated and intent in _NEGATABLE:
                continue
            return intent
    if text in _ACKS:
        return "ack"
    words = text.split()
    if (len(words) <= 3 and not negated and words[0] not in _QUESTION_STARTS
            and any(w in _NEXT_MISHEARD for w in words)):
        return "next"
    return None


def looks_like_command(heard: str) -> bool:
    """Short, and not shaped like a question: the kind of utterance where the
    live model - which heard the audio - is likelier right than a transcript
    of it. guide_step lets the model's second ask through for these (LG-03),
    and for nothing else."""
    raw = str(heard or "")
    text = _normalise(raw)
    words = text.split()
    if not words or len(words) > 3 or "?" in raw:
        return False
    if any(_has_phrase(text, n) for n in _NEGATIONS):
        return False                    # "dann nicht" is a no, however often
    return words[0] not in _QUESTION_STARTS
