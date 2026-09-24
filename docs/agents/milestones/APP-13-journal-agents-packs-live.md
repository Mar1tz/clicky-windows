# APP-13 — the Journal, Agents, Extensions and Accounting pages are live

**Status: done.** 35 new tests, 1117 passing.

## The problem

The last four pages were still drawings (M29):

* **Journal:** three made-up questions "due for review" and three made-up
  entries.
* **Agents:** an "Application Builder" stuck at 70%, and a plan and a
  timeline typed in by hand.
* **Extensions:** four packs, three of which do not exist, and a
  "Browse packs" button with nothing behind it.
* **Accounting:** a page of invented exceptions and a "Run month-end"
  button that did nothing.

## What was built

**Journal** reads the journal Tora keeps.

* **Due for review.** You see the question, answer it in your head, open
  the answer, then say **I knew it** (it comes back in a few days) or
  **Not yet** (it comes back tomorrow). This is an honest review: the
  voice "quiz me" still marks every answer right without hearing one.
  With nothing due, the page says when the next one comes back.
* **Recent.** Questions and walkthroughs, newest first. A walkthrough
  shows its steps. Opening a review does not show the answer under Recent.
* **Lesson recording and Workflow capture.** These start and stop through
  the tray's own handlers, so the notifications and file paths are
  unchanged. The capture card says plainly that it records typing too.
* **Keep a journal.** The tray's switch. With it off, the page says new
  questions are not being kept.
* **Open folder** and **Attach document** do what the tray's items do.

**Agents** embeds the workforce's own panel, the one the tray's "Active
Agents" opens: a card per agent with progress, Pause and Stop, and a box
to give them a job.

* It is built once, and the whole page shares one event listener. Opening
  or redrawing the page never adds a listener to the workforce.
* **Recent tasks** lists what actually ran and how it ended. A failed
  task gives its reason.
* **Waiting for you** appears only while an action waits for a yes.
* **Details**, or **Logs** on an agent's card, shows that task's plan and
  activity log inside the page, instead of opening another window.
* **Stop all** is the tray's. **Agent settings** goes to the Settings page.

**Extensions** lists the packs that are really there.

* Each has its real switch, its integrations by name (QuickBooks, not
  "quickbooks"), and **Open dashboard**. A pack that will not switch says
  why.
* Turning a pack on or off anywhere redraws the page. That includes by
  voice and from the tray's window: the registry announces it on the
  workforce's event bus.

**Accounting** is the pack's own dashboard (client picker, month-end,
exceptions, drafts, reports), embedded.

* With the pack off, the page offers **Turn on Accounting**.
* The dashboard is kept while the pack stays on, because it holds the
  chosen client. It refreshes itself every four seconds, but only while
  the page can be seen; its clock stops when the pack is turned off.

## Things fixed along the way

* **Light-grey buttons, tabs and pop-ups.** A pack's unnamed buttons and
  tabs, and the message boxes it opens, drew as Windows' light-grey
  controls. Worse, a message box took the window's near-white text on
  Windows' light dialog, so it could not be read. The theme now gives
  them the app's look (a fallback: named styles still win).
* **"Debtors  creditors".** A tab reads "&" as a keyboard shortcut, so
  "Debtors & creditors" lost its ampersand. It is escaped when the
  dashboard is embedded. The floating dashboard has the same bug; that is
  the pack's and worker 1's code, so it is reported, not changed.
* **Long titles.** A long question or task title no longer widens the
  page past the window (`Row(wrap=True)`).

## Proof

`tests/agents/test_e2e_app_journal.py` (15):

* What is due comes from the journal.
* Grading moves an entry's next review: three days out when you knew it,
  tomorrow when you did not.
* A review keeps its answer hidden under Recent.
* Walkthroughs show their steps.
* Every button is the tray's.
* A window opened on its own never opens the journal.
* A finished turn redraws the page once.

`tests/agents/test_e2e_app_agents_packs.py` (20):

* The panel is embedded once, with one listener.
* A task's plan and history are shown in the page, not a new window.
* Waiting approvals appear only while something waits.
* A progress tick does not redraw the lists.
* The real pack switches on and off, and a refusal is said.
* **Open dashboard** lands on the Accounting page.
* The dashboard is kept, stopped when the pack is off, and paused while
  unseen.
* A dashboard that fails to build says why.

Two of these were checked by removing the behaviour they test: both
failed, then passed once it was restored. All four pages were rendered
with real fonts and checked, along with a message box and an input
dialog opened inside the window.
