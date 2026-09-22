# One document per milestone

`../DESKTOP_CONTROL_ROADMAP.md` lists the milestones. This folder holds the
spec for each one, written just before it is built.

Name a file `DC-NN-<slug>.md` and give it these sections:

1. **The complaint** — in the user's words, verbatim where possible.
2. **What happens today** — the current behaviour, with `file.py:line`
   references and, where there is one, the log line that shows it.
3. **Why it happens** — the mechanism, not the guess.
4. **What should happen** — behaviour, not implementation.
5. **Surface** — every file the change touches, and what it does to each.
6. **Out of scope** — what this milestone deliberately leaves broken, so
   the next one has something to do.
7. **Proof** — the tests, named. A milestone whose proof cannot be stated
   as a test is not ready to be built.

One milestone, one commit.
