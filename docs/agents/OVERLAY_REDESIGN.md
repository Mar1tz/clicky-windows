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
