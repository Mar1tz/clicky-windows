# Students pack

*The fourth industry pack, built on the shared kit. Code: `extensions/students/`.
Tests: `tests/agents/test_students_pack.py`, offline and with no model.*

This pack is a study coach. It keeps a revision plan that fits before each
exam, flashcards on spaced repetition, notes, reference lists and every
deadline in view.

**The line it never crosses:** Tora helps a student learn. It never writes
work that is handed in for marks. No tool produces an essay or an answer to
submit. The rule is in the prompt (`students.integrity`), and "write my
essay…" is not routed to this pack.

## What it does without a model

* **Revision plan.** Each course's topics are fitted into the days before
  its next exam:
  * a hard topic (3) gets three sessions, a medium one (2) two, an easy one (1) one;
  * courses are mixed within a day, and no topic comes twice in one day;
  * the day before each exam is kept for "Review everything";
  * nothing is planned on the exam day.

  Sessions that do not fit come back as a finding ("Maths: 16 sessions do
  not fit"), not as a plan that quietly skips topics. Rebuilding replaces
  only the sessions not yet done. The plan is written to
  `Plans/Revision plan.md`.
* **Flashcards** are scheduled with SM-2 (grades 0–5; ease never drops below
  1.3). They export to Anki as tab-separated text.
* **Reference lists** are formatted in APA 7 or Harvard:
  * books, journal articles (volume, issue, pages, DOI) and web pages
    (site, URL, date accessed);
  * authors given as "Given Surname" or "Surname, Given";
  * hyphenated initials kept (J.-P.);
  * APA's "&" before the last author and Harvard's "and".

  The list is sorted and written to `References/`.
* **Deadlines** cover exams, assignments, tests and presentations, with a
  weight and days left. Late ones are flagged, and so is three or more
  missed sessions.

The model summarises the student's own material into notes and writes
cards and practice questions from it.

## Levels

Every tool is READ or DRAFT, and nothing is submitted.

## Workflows

* **Today's study** runs every weekday at 07:30 when `auto_daily_study` is
  on. It gives today's sessions, deadlines ahead, what is late or missed, and
  the cards due.
* **Build the revision plan** lists the deadlines, builds the plan and shows
  today.

## Try it

Say "make me a revision plan", "show me today's study", "make flashcards
from my biology notes", "add my chemistry exam on 12 November" or "format
my reference list in Harvard".
