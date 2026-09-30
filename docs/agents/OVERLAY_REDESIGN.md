# Overlay redesign — built, then removed

The plan was the Claude Doc "HeyTora overlay redesign — island, cursor and
step chip" (https://claude.ai/code/artifact/d5f5a5a1-f7e1-4f23-8e48-195b546b0a42):
nine milestones, OD-01 to OD-09. It replaced the triangle cursor with an
arrow whose tip is the point, and the caption pill with a solid bar of one
fixed size (440 x 58). A step label sat beside the target, and Settings got
an "On screen" card.

All nine milestones were built (6d63c32 to 9ccf88e). The user said yes to a
screenshot review
(https://claude.ai/artifact/Uv3bg5zLBhJ1pjBa1RQQcP), then saw the new cursor
and island in the running app and asked for them to be reversed
(2026-09-30).

The redesign is removed. Every file it touched is back to how it was at
fe6acda, the commit before OD-01, and its own modules are deleted:
`ui/overlay_theme.py`, `ui/overlay_paint.py`, `ui/overlay_chip.py`,
`ui/overlay_preview.py` and `tests/agents/test_e2e_overlay_design.py`.
The Teachers pack's `extensions/teachers/classroom.py`, committed alongside
OD-04/05 in 3fc5b53, stays.

The work is still in git history. The last commit with the new look in it
is e090124.

Lesson: judge a new look of the overlay in motion on the user's own screen
before building more milestones on it; a yes on screenshots was not enough.

## What came next: the notch (2026-09-30)

The user then asked for the top island only, from four references: the
iPhone's Dynamic Island, NotchNook on a Mac, a Windows weather pill and
Notchify. The request was one fixed size that never grows as Tora talks.
`ui/caption.py` is now a pure black notch, flush with the top of the screen.
It is square where it meets the edge, with a small inward curve on each
side, and rounded below. It is 460 x 42 px (60% of a small screen) and shows
one line of white words; a finished line rolls up into the edge. The cursor
is still the classic triangle.

This time the user saw it live on their screen first: a one-line and a
two-line version, played side by side. They chose one line. Tests:
`tests/agents/test_e2e_caption_notch.py`.
