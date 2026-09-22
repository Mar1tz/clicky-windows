# Tora Agent Workforce — Milestones

One milestone = one sitting for one agent. Each is self-contained: an
agent that has never seen this project can read its block, do the work,
prove it with tests, and hand off. If an agent runs out of context
mid-milestone, the next one resumes from the same block plus the
`HANDOFF` note that agent leaves behind.

Target: `docs/agents/ARCHITECTURE.md` is the design; this file is the order
of work. `CLAUDE.md` is the multi-session rulebook.

---

## How to run a milestone (read this first, every time)

**Starting a milestone**

1. Read, in this order: `CLAUDE.md` → `docs/agents/ARCHITECTURE.md` →
   `agents/core/models.py` → `agents/core/interfaces.py` → this file's
   milestone block.
2. Check nothing is half-finished: `git status --short` and
   `python -m pytest tests/agents -q`.
3. Set the milestone's status here to 🔵 and commit that line alone.
4. Build only the files the block lists under **Owns**.

**Finishing a milestone**

1. `python -m pytest tests/agents -q` is green — the whole suite, not just
   your tests.
2. Set status ✅ here, fill in **Landed** with the commit hash.
3. Commit: `git add <your paths> docs/agents/MILESTONES.md` then
   `git commit -m "[m<N>] <what you did>"`.
4. Push only if you are the manager session.

**Running out of context mid-milestone**

Before you stop, append to the milestone's **HANDOFF** line: what is done,
what file you were in, the next concrete action, and anything you learned
that is not in the code. Commit whatever compiles (tests may be red — say
so in the commit message with `WIP`). The next agent starts by reading that
line.

**Status:** ⬜ not started · 🔵 in progress · 🟡 needs review · ✅ done · ⛔ blocked

---

## Board

| #   | milestone                          | phase | status | depends on |
|-----|------------------------------------|-------|--------|------------|
| M0  | Repo, GitHub, test harness         | 0     | ✅     | —          |
| M1  | Core contracts                     | 0     | ✅     | M0         |
| M2  | Tool framework                     | 1     | ✅     | M1         |
| M3  | Providers, workers, understanding  | 1     | ✅     | M1         |
| M4  | Memory, planning, verification, persistence | 1 | ✅ | M1      |
| M5  | Runtime, orchestrator, manager     | 1     | ✅     | M1         |
| M6  | Green suite + bootstrap committed  | 1     | ✅     | M2–M5      |
| M7  | Companion integration (delegation) | 1     | ✅     | M6         |
| M8  | Active Agents panel                | 1     | ✅     | M6         |
| M9  | Activity timeline + logs           | 1     | ✅     | M8         |
| M10 | Plan viewer + approval dialog      | 1     | ✅     | M8         |
| M11 | Tray entry + agent settings page   | 1     | ✅     | M8         |
| M12 | Open-source framework research     | 2     | ✅     | M6         |
| M13 | **OpenAI-compatible provider (gateway)** | 2 | ✅ | M12    |
| M14 | ~~Claude provider~~ → folded into M13 | 2  | ➖     | —          |
| M15 | Direct Gemini provider             | 2     | ✅     | M13        |
| M16 | Connected accounts UI + routing    | 2     | ✅     | M11, M13   |
| M17 | LLM planning + verification default| 2     | ✅     | M13        |
| M18 | Durable tasks, resume after restart| 3     | ✅     | M7         |
| M19 | Browser worker                     | 3     | ✅     | M12, M13   |
| M20 | Computer-control worker            | 3     | ✅     | M13        |
| M21 | Multi-agent collaboration          | 3     | ✅     | M17, M18   |
| M22 | Autonomous coding loop             | 3     | ✅     | M17        |
| M23 | Ship: packaging, limits, safety    | 3     | 🟡     | M16, M18   |

Phase 1 = the framework with no real AI. Phase 2 = plug the AI in.
Phase 3 = the capabilities that need both.

---

# Phase 0 — Foundation

## M0 · Repo, GitHub, test harness ✅

**Goal** Version control before four agents touch one tree.
**Landed** `e60d219`, `534d82e`. Remote `https://github.com/Mar1tz/clicky-windows` (private).
**Notes** `.env` is git-ignored; only `.env.example` is tracked. `pytest` +
`pytest-asyncio` installed, `pytest.ini` sets `asyncio_mode = auto`.

## M1 · Core contracts ✅

**Goal** One vocabulary every package shares, so four agents could build in
parallel without seeing each other's code.
**Landed** `534d82e` — `agents/core/{models,events,interfaces}.py`, 11 tests.
**Notes** `agents/core` is frozen. Changing it means a sweep of every
package; propose the diff before editing.

---

# Phase 1 — The framework (no AI required)

## M2 · Tool framework ✅

**Goal** Everything an agent can DO, behind one registry with one choke point.
**Owns** `agents/tools/**`, `tests/agents/test_tools_*.py`
**Landed** worker 1, ~1500 lines: `Registry` (schema validation, policy
enforcement, approval, dry-run, timing, `TOOL_CALL`/`TOOL_RESULT` events),
file tools (workspace-guarded), terminal tools, git tools, browser/computer
stubs that fail cleanly in Phase 1.

## M3 · Providers, workers, understanding ✅

**Goal** The AI-shaped hole, plus the loop that fills it, plus the thing that
decides "is this chat or a job?".
**Owns** `agents/providers/**`, `agents/workers/**`, `agents/understanding/**`
**Landed** worker 2: `MockProvider`, `Router`, `AccountStore`, `Worker`
(scripted + ReAct paths), six worker types, `HeuristicIntentClassifier` +
`ProviderIntentClassifier`.

## M4 · Memory, planning, verification, persistence ✅

**Goal** The agent remembers, plans before acting, checks its own work, and
survives the process.
**Owns** `agents/memory/**`, `agents/planning/**`, `agents/verification/**`,
`agents/persistence/**`, `supabase/migrations/0002_agent_tasks.sql`
**Landed** worker 3: `SQLiteTaskStore`, `SQLiteMemoryStore` + `AgentMemory`,
`TemplatePlanner` (React/Python/folder templates with pre-filled tool calls)
+ `ProviderPlanner`, `RuleVerifier`.

## M5 · Runtime, orchestrator, manager ✅

**Goal** The state machine and the background thread — "assign it and walk away".
**Owns** `agents/manager/**`, `tests/agents/test_manager.py`, `test_e2e.py`
**Landed** `7bea1a6`. `AgentRuntime` (daemon thread + asyncio loop),
`Orchestrator` (PLANNING → RUNNING → VERIFYING → terminal, replan budget),
`AgentManager` (submit/pause/resume/cancel/approve, agent-state tracking).
17 manager tests + the e2e acceptance test.

---

## M6 · Green suite + bootstrap committed ✅

**Goal** Everything four agents built in parallel is committed, green, and
starts from one function call. This is the gate to Phase 1's UI work.

**Owns** `agents/bootstrap.py`, `tests/agents/test_bootstrap.py`, plus
fixes to whichever tests are red (coordinate with the package owner if the
sessions are still live).

**Landed** `351900e` (49 files: every Phase 1 package + bootstrap) and
`d61029e` (the three fixes). Suite: **202 passing, 0 failing.**

**The three failures, and what they actually were**

1. `test_planning_templates.py::test_folder_plan_from_goal_or_subdir` —
   `TemplateSpec.goal` is lower-cased for matching, and `FolderTemplate`
   extracted the folder name from that same string, so "a folder called
   Notes" created `notes`. Fixed by carrying `goal_raw` alongside `goal`
   and extracting names from the raw text. **Any future template that
   pulls a user-visible name out of the goal must read `goal_raw`.**
2. `test_chat_utterances[is the project set up correctly?]` — the real bug.
   `_QUESTION_AUX` ended in a literal **0x08 backspace byte** instead of a
   `\b` word boundary: invisible in an editor, in `grep`, and in a diff, but
   it made the pattern unmatchable, so every "is/does/should …?" question
   containing an imperative was classified as a task. Repaired byte-wise; a
   sweep of all 60 agent/test files found this as the only occurrence.
   The `\b` also correctly stops "issue" matching "is".
3. `test_task_utterances[write a script called backup.py]` — `script` was
   in the work-object list but not in the coding-signal list, so scripts
   classified as GENERAL. Added `script` plus a code-file-extension
   alternative (`.py`, `.js`, `.ts`, …).

**Lesson for later milestones:** when a regex "obviously should match" and
doesn't, print `repr(pattern.pattern)` before rewriting the logic around it.

**Done when**
* `python -m pytest tests/agents -q` → 0 failed.
* `python -c "from agents.bootstrap import build_agent_manager"` works.
* This runs clean from a cold start and creates real files:
  ```
  python -c "from agents.bootstrap import build_agent_manager as b; import tempfile,os; w=tempfile.mkdtemp(); m=b(workspace_root=w,in_memory=True).start(); t=m.wait(m.submit('Create a new folder called MyApp and build the foundation of a React application inside it.').id,30); print(t.status.value, sorted(os.listdir(os.path.join(w,'MyApp')))); m.stop()"
  ```
  → `completed ['.gitignore', 'README.md', 'index.html', 'package.json', 'src', 'vite.config.js']`
* One commit containing every package (`[m6] Phase 1 framework: tools,
  providers, workers, understanding, memory, planning, verification,
  persistence, bootstrap`), pushed.

**HANDOFF** _(none yet)_

---

## M7 · Companion integration — the app can delegate ✅

**Goal** A user talking to Tora can say "create a folder called MyApp and
build a React foundation in it" and an agent starts. Both routes: the live
voice model (tool call) and the cascade route (classifier).

**Owns** `companion_manager.py`, `config.py`, `ai/gemini_realtime.py`
(manager-owned files — one agent at a time, no exceptions).

**Four edits, all additive**

1. `config.py` — append a `DELEGATION:` paragraph to `_TECHNICAL_RULES`
   (ends at the `NARRATION SYNC:` block, ~line 161): when the user asks to
   BUILD something on their machine, call `delegate_task` instead of
   narrating steps; never for questions or anything about the screen.
2. `ai/gemini_realtime.py` — add a `delegate_task` declaration to
   `tool_declarations()` after `recall` (~line 239). Params: `goal`
   (required), `title`, `workspace_hint`, `kind` (enum coding/file/
   terminal/research).
3. `companion_manager.py` — four insertions:
   * `sig_agent_event = pyqtSignal(object)` next to `sig_activity` (~line 238).
   * `self._agents = None` in `__init__` before the loop thread starts
     (~line 374); `self._start_agents()` at the end of `start()` (~line 390);
     `self._agents.stop(timeout=3)` at the top of `shutdown()` (~line 429).
     `_start_agents()` wraps `build_agent_manager().start()` in try/except and
     subscribes `self.sig_agent_event.emit` to the bus — a broken agent
     package must never stop the companion from running.
   * `"delegate_task": self._tool_delegate_task` in `_tool_handlers()`
     (~line 843), plus the handler: build a `Task`, `submit_task`, wait up to
     1.5 s for the plan, return `{ok, task_id, agent, workspace, steps[]}` so
     the model reads the steps back while the agent already works.
   * In `_cascade_turn`, after the user-skills block and **before** the screen
     capture (~line 1755): `task = await self._delegate_if_task(transcript)`
     → if not None, `await self._reply_local(describe_plan(task))` and return.

**Watch out**
* `_delegate_if_task` must not block the companion's loop — the classifier
  may become provider-backed in M17, so run it via `run_in_executor`.
* Writing these patches with a bash heredoc fails on the nested quotes;
  use the Edit/Write tools or a Python patch script read from a file.
* Import `agents.*` lazily inside the functions, never at module top.

**Done when**
* `python -c "import companion_manager"` clean; `python main.py` starts and
  the tray icon appears with no new errors in `tora.log`.
* `delegate_task` appears in `tool_declarations()`.
* `tests/agents/test_integration_companion.py` (new, yours): imports
  `CompanionManager` without Qt event loop, asserts the handler is registered
  and that `_delegate_if_task` returns `None` for "what is a React component?"
  and a `Task` for the MyApp request, using a manager built with
  `in_memory=True`.

**Landed** All four edits plus 16 integration tests. Suite: **218 passing**.
Verified against the real app, not just tests — `python main.py` logged
`agent workforce ready — workspace C:\Users\torbe\Documents\Tora Projects`,
ran 40 s, zero errors.

**Notes for whoever touches this next**
* `_wait_for_plan` is a `@staticmethod`. Copying it onto a test stub with
  `X = CompanionManager._wait_for_plan` silently rebinds `self` as the first
  argument; wrap it in `staticmethod(...)`. Cost 20 minutes once already.
* The delegation check sits **after** voice commands and skills (cheaper and
  more specific) and **before** the screenshot, so a build request never pays
  for a screen capture. `test_companion_exposes_the_handler_and_the_signal`
  asserts that ordering against the source — if you move it, that test fails
  on purpose.
* `_start_agents()` swallows everything. A broken `agents/` package leaves
  `self._agents = None` and every other feature untouched; both delegation
  paths return cleanly in that state (two tests cover it).

**HANDOFF** Complete. Next agent starts at **M8** (Active Agents panel) —
`sig_agent_event(object)` is already emitting `agents.core.Event` on the Qt
thread, so the panel has a live feed to bind to on day one.

---

## M8 · Active Agents panel ✅

**Goal** Spec §10 — the window that makes it feel like a workforce:
🟢 Application Builder · Step 4/10 · ███████░░░ 70% · Creating Navbar.tsx ·
[Pause] [View Logs] [Stop].

**Owns** `agents/ui/**`, `tests/agents/test_ui_*.py`

**Build**
* `agents/ui/bridge.py` — `AgentEventBridge(QObject)`: subscribes to the
  `EventBus`, re-emits on the GUI thread via `pyqtSignal`. This is the only
  place PyQt6 and the agent runtime meet. Signals: `agent_changed(object)`,
  `task_changed(object)`, `event(object)`, `approval_required(object)`.
* `agents/ui/agents_panel.py` — `ActiveAgentsPanel(QWidget)`: one card per
  agent (name, status dot, current task, `Step i/n`, progress bar, current
  action, Pause/Resume, Logs, Stop). Empty state: "No agents running. Ask
  Tora to build something."
* `agents/ui/__init__.py` — `open_agents_window(manager, parent=None)`.
* Styling comes from `ui/design.py` tokens (`PANEL_QSS`, `font()`,
  `STATE_LISTENING` for the running dot). Do not invent a second palette.

**Done when**
* `python -m agents.ui.demo` opens a window driven by a real in-memory
  `AgentManager` running the MyApp task, and the bar visibly fills.
* Pause/Stop buttons actually change task status.
* Tests use `QApplication` from a fixture with `QT_QPA_PLATFORM=offscreen`
  and assert the card's label text after feeding events through the bridge.

**Landed** `agents/ui/{bridge,agents_panel,demo}.py` + 14 tests. Suite:
**232 passing**. Verified by screenshotting the real panel driven by a real
workforce: a live frame caught mid-run reads
`Application Builder · Running · Step 3/10 · coding: create_file · 30%`
with a green dot and enabled buttons, and the finished cards grey out.

**Notes for whoever touches this next**
* `AgentEventBridge` is the only PyQt6 import point under `agents/` (the
  `grep` for it should return `agents/ui` plus the rule's own docstring in
  `agents/__init__.py`). Keep it that way — widgets stay single-threaded
  because the bridge's queued signals do the hand-off.
* `_on_event` runs on the **runtime** thread. Anything slow added there
  stalls the agent that published the event. It catches `RuntimeError` on
  purpose: that is a closed window's deleted C++ object, not a bug.
* The scroll viewport and its inner holder both default to the window
  colour and show through as a white band between cards. Fixed with
  `QScrollArea > QWidget > QWidget { background: transparent; }` plus
  `viewport().setAutoFillBackground(False)`; if cards ever get a white
  gutter again, that is what regressed.
* Cards are keyed by `AgentState.spec.id`. A new `AgentSpec` means a new
  card, which is correct — it cost one confused test to learn.

**Follow-up (small, not blocking):** the action line reads
`coding: create_file` where the spec's mock-up says `Creating Navbar.tsx`.
That string comes from the worker's `ctx.progress(...)` call in
`agents/workers/base.py`, not from the panel. Worth making human-readable
when M9 touches the timeline.

**HANDOFF** Complete. Next agent starts at **M9** (activity timeline) —
`ActiveAgentsPanel.view_logs(task_id)` already fires from each card's Logs
button and is unconnected, waiting for the timeline window.

---

## M9 · Activity timeline + logs ✅

**Goal** Spec §11 — every action, timestamped, per task:
`21:40 Created project folder` / `21:42 Installed dependencies` / a file tree.

**Owns** `agents/ui/timeline.py`, `agents/ui/logs.py`, tests.

**Build** A scrollable timeline fed from two sources: `TaskStore.events(task_id)`
for history and the live bridge for new events. Group tool calls under their
step. Render file-creating results as a small tree. Icons/colour by level.
"View Logs" on an agent card opens this filtered to that task.

**Done when** Opening the timeline for a completed task after an app restart
shows the full history from SQLite (proves persistence works), and a live
task appends rows as they happen.

**Landed** `agents/ui/timeline.py` + 13 tests. Suite: **245 passing**.
`test_history_survives_a_restart` is the real gate: it runs a task, throws
the manager away, rebuilds one on the same SQLite file, and asserts the
replayed timeline is **identical** to the live one. The Logs button from M8
is now connected (`open_timeline`), one window per task.

**Notes for whoever touches this next**
* `describe(event) -> (headline, detail)` is the whole rendering contract,
  and it is pure — test it directly rather than through widgets. An empty
  headline means "skip this row".
* Three kinds of noise were worth removing, and the reasoning generalises:
  a `create_file` call was dumping the entire file into the row (now
  `content=342 chars`); `STEP_DONE` repeated what its own tool rows had
  already said (now skipped when the step ran tools, kept when it did not,
  since an LLM-only step has nothing else); and the verifier's summary
  already counts its checks, so the tally was printed twice.
* 57 rows became 47 for the same task. If a new event kind makes the
  column noisy again, fix it in `describe`, not by hiding the kind.
* `_HIDDEN` drops `TASK_PROGRESS` and `AGENT_STATUS`: they fire per step
  and belong on the card, not in the log.

**HANDOFF** Complete. Next agent starts at **M10** (plan viewer + approval
dialog). `AgentManager.approve(request_id, bool)` and the
`APPROVAL_REQUIRED` event already exist and are covered by manager tests;
`AgentEventBridge.approval_required` already re-emits the request on the
Qt thread, so M10 is a dialog plus wiring, not new plumbing.

---

## M10 · Plan viewer + approval dialog ✅

**Goal** Spec §9 — the user sees the plan before/while it runs, and
`APPROVAL_REQUIRED` gets a real dialog instead of a silent park.

**Owns** `agents/ui/plan_view.py`, `agents/ui/approval.py`, tests.

**Build** Plan list with per-step status ticks. Approval dialog showing the
tool, its arguments, the risk level and the workspace, with Approve / Deny /
"always allow this tool for this agent" (the last one mutates
`AgentSpec.permissions.allowed_tools`). Wire to
`AgentManager.approve(request_id, bool)`.

**Done when** Running a task with `require_plan_approval=True` shows the
dialog, Deny cancels the task, Approve runs it; a HIGH-risk tool call
(`delete_path`) raises the dialog mid-run.

**Landed** `agents/ui/{plan_view,approval}.py` + 13 tests, and one
**`agents/core` change** (below). Suite: **260 passing**. All four gate
cases are covered against real tasks: plan approve → COMPLETED, plan deny
→ CANCELLED with nothing written, mid-run tool approval (ceiling set to
SAFE so real file writes need a human), and deny mid-run → FAILED with
"permission denied".

**Core change — `PermissionPolicy.always_allow`**
The dialog's "Always allow &lt;tool&gt;" could not be built on the existing
fields. `allowed_tools` is a whitelist **filter**: adding a tool to it only
narrows what may run, so the risk ceiling would still block the call and
the user would be asked again on the very next step. Added
`always_allow: list[str]` plus `allow_always(name)`, checked after
`denied_tools` and before the risk gate, so a standing yes lifts one tool
over the ceiling but never over an explicit veto. Documented in
`ARCHITECTURE.md` §4 and covered by two tests in `test_core.py`.

**Notes for whoever touches this next**
* Approvals are two-threaded: the task parks on the runtime thread while
  the dialog lives on the GUI thread. In a test, `manager.wait()` therefore
  **deadlocks** — nothing pumps Qt, so the dialog never appears and the
  approval never resolves. Use `_pump_until(app, predicate)` from
  `test_ui_approval.py`.
* `ApprovalController` shows one dialog at a time and denies on any
  exception. A request raised with no controller attached leaves the task
  parked in `WAITING_APPROVAL` forever — call `attach_approvals(manager)`
  once, early.
* A widget that skips its stylesheet because it is "embedded" renders
  **white-on-white**: the embedded `PlanView` inside the approval dialog
  did exactly that, and every test still passed because `lines` was
  correct. Only the screenshot caught it. Style always; skip only the
  window chrome.

**HANDOFF** Complete. Next agent starts at **M11** (tray entry + settings).
`open_agents_window`, `open_timeline`, `open_plan` and `attach_approvals`
are all exported from `agents.ui` and ready for the tray menu to call.

---

## M11 · Tray entry + agent settings page ✅

**Goal** The workforce is reachable without voice, and its safety rails are
user-visible.

**Owns** `ui/tray.py` (manager-owned — coordinate), `agents/ui/settings.py`.

**Build** Tray menu: "Agents…" opens the panel, with a badge when any agent
is running. Settings page: workspace root (default `~/Documents/Tora
Projects`), auto-approve risk level (Safe/Low/Medium/High), require plan
approval toggle, max concurrent agents, "clear agent history". Persist via
the existing `config.py` mechanism.

**Done when** Settings survive a restart and actually change behaviour —
setting auto-approve to `Safe` makes a `create_file` step raise an approval.

**Landed** `agents/ui/settings.py`, four `TORA_AGENT_*` settings in
`config.py`, the tray entries, `main._wire_agents`, and concurrency
enforcement in `AgentManager`. 13 tests. Suite: **273 passing**. Verified
against the running app: it starts clean and logs `agent workforce ready`.

**Scope call — concurrency was implemented, not just displayed**
The block listed "max concurrent agents" as a settings field, but nothing
enforced it, and a setting that silently does nothing is worse than no
setting. `AgentManager` now queues past the limit (`_active` / `_queue`,
0 = unlimited) and starts the next task when a slot frees. This overlaps
the limits work in M23; what remains there is per-task wall clock, token
and cost budgets.

Two things queueing broke that were fixed with it:
* `wait()` watched only `run.future`, which a queued task does not have
  yet — it returned instantly with PENDING. It now watches the status and
  uses the future as the fast path.
* `cancel()` set a flag nothing would read until a slot freed, so a
  cancelled queue entry sat at PENDING forever. It is now retired
  immediately.

**Notes for whoever touches this next**
* Settings apply to the **default** policy only. Agents already running
  keep the rails they were created with — tightening the ceiling must not
  retroactively unapprove work the user already allowed. There is a test.
* `build_agent_manager()` takes `None` for its policy arguments and fills
  them from `config.py`, so the page is not decorative. Pass explicit
  values in tests.
* `main._wire_agents` calls `attach_approvals` — without it, a task that
  needs a human parks in `WAITING_APPROVAL` forever and looks like a hang.
  A test asserts that call is still there.
* The tray rebuilds its menu only when the running count actually changes;
  agent events fire several times a second and a menu rebuilt under an
  open cursor flickers.

**HANDOFF** Complete. **Phase 1 is finished** — M0–M11 all ✅. Next agent
starts at **M12** (open-source framework research), the last cheap
milestone before real providers land in M13.

---

# Phase 2 — Plug the AI in

## M12 · Open-source framework research ✅

**Goal** Spec §14 — decide what to reuse before writing more from scratch.

**Owns** `docs/agents/FRAMEWORK_RESEARCH.md`

**Cover** LangGraph, CrewAI, AutoGen, OpenHands, SWE-agent, OpenAI Agents
SDK, browser-use. For each: what it does, what it would replace here, its
dependency weight (this ships as a **PyInstaller bundle on Windows,
Python 3.14** — wheels and size are hard constraints), licence, and a
verdict: adopt / borrow the idea / skip. End with a table mapping our
packages to "keep custom" or "replace with X", and a specific
recommendation for the browser layer (M19), which is the most likely
genuine adopt.

**Done when** The doc names a decision per framework with a reason, not a
summary of their READMEs.

**Landed** `docs/agents/FRAMEWORK_RESEARCH.md`, decided against live PyPI
metadata rather than recollection.

**Result: one adopt, two borrowed ideas, no new required dependency.**
* **Playwright → adopt in M19.** 2 declared deps, a Python 3.14
  classifier, browsers downloaded on first use rather than bundled.
  Optional: without it the browser tools fail exactly as the stubs do now.
* **CrewAI and OpenHands are blocked outright** — both declare
  `requires_python <3.14`, so they cannot install into this app at all.
  OpenHands also pulls **85** dependencies.
* **LangGraph and the OpenAI Agents SDK lose on fit, not weight**: both
  would replace a working, tested orchestrator that has pause/resume/
  cancel, approval gating and a durable event log, with a control flow
  that does not.
* **Borrowed:** LangGraph's declared-DAG edges for M21; SWE-agent's
  finding that tool *ergonomics* beat model choice, for M22.

**The finding that changes Phase 2 — the OmniRoute gateway**
`https://ai.tmaritz.xyz/v1` is OpenAI-compatible and fronts **1295
models** (Claude, Gemini, GPT, open weights) behind one surface. Verified
live: `claude/claude-sonnet-4-6` returned `finish_reason=tool_calls` with
`create_folder({"path": "MyApp"})` in ~2 s.

* **It always streams.** Every `/chat/completions` reply is
  `text/event-stream` even with no `stream` parameter, so `json.loads` on
  the body raises `JSONDecodeError`. Reassemble SSE deltas; tool-call
  arguments arrive fragmented across chunks, indexed by position.
* **Reliability is per route**: `aug/*` → 502, `agy/gemini-2.5-flash` →
  90 s timeout, `zm/*` → 403 (separate entitlement). `claude/`, `agy/`
  and `cc/` Claude routes all worked. `Router.pick()` will need health
  tracking so a dead route is not handed to a running agent.
* Credentials live in `.env` (git-ignored) as `TORA_OMNIROUTE_API_KEY`
  and `TORA_OMNIROUTE_BASE_URL`, matching `AccountStore`'s
  `TORA_<PROVIDER>_API_KEY` convention. **Never commit them.**

**Board changed as a result:** M13/M14/M15 were a Gemini adapter, then a
Claude adapter, then OpenAI + local. One `OpenAICompatibleProvider`
reaches all of them, so M13 absorbs M14, and M15 becomes the *direct*
Gemini provider that is still needed later for the **Live websocket** the
voice path uses — no OpenAI-shaped gateway exposes that.

**HANDOFF** Complete. Next agent starts at **M13**, which is now the
single provider milestone. Read the SSE and per-route-health notes above
before writing the client; they were learned the slow way.

---

## M13 · OpenAI-compatible provider ✅

**Goal** One adapter that reaches Claude, Gemini, GPT and open weights
through any `/chat/completions` endpoint — a gateway, api.openai.com, or
a local server. Replaces the three adapters M13/M14/M15 originally were.

**Owns** `agents/providers/openai_compatible.py`, `agents/providers/__init__.py`,
`tests/agents/test_providers_openai.py`, `tests/agents/conftest.py`

**Landed** 27 offline tests (via `httpx.MockTransport`) + 2 live tests.
Suite: **299 passing, 2 skipped**. Verified live end to end: a real model
planned, called `create_file`, and wrote a real haiku to disk through the
full orchestrator.

**What the live gateway forced into the design**
* **SSE always.** Replies are `text/event-stream` even unasked, so we now
  always request the stream and reassemble — one code path instead of two.
* **Tool arguments arrive fragmented.** The name lands in one chunk, the
  JSON arguments dribble across later ones, keyed by `index`. Both are
  concatenated, never replaced. Parallel calls occupy separate slots.
* **Malformed argument JSON is kept as `{"_raw": …}`**, not dropped: the
  registry then rejects it with a message the model can act on, whereas a
  silent drop looks like a no-op.
* **Health is per model, not per endpoint.** Three strikes benches a route
  for 120 s and the provider falls through its chain. If every candidate
  is benched it tries anyway — a stale health record must never make the
  provider refuse to work.
* **`response.model` reports the route we called**, not the upstream's
  self-reported name: a gateway answers "claude-sonnet-4-6" for several
  route ids, and the route is what health, routing and reproduction need.

**The bug worth remembering**
`build_agent_manager` always passed `fallback=MockProvider()`, and the
gateway was registered only `if fallback is None`. So the app ran on the
mock while looking fully configured — an agent "completed" a task having
written nothing, and the tests all passed. Only running it for real
caught it. `gateway` is now an explicit argument that is never inferred
from `fallback`, and there is a regression test.

**`tests/agents/conftest.py` keeps the suite offline.** With real
credentials in `.env`, every test would otherwise make paid network
calls. Gateway variables are cleared per test; live tests are marked
`@pytest.mark.live` and skipped unless you run `pytest tests/agents -m live`.

**HANDOFF** Complete. Next is **M15** (direct Gemini provider for the Live
websocket) or **M17** (make `ProviderPlanner`/`ProviderVerifier` the
defaults now that a real provider exists). M17 is the higher-value one:
the planner still falls back to templates for anything it does not
recognise, so "build me a CRM" cannot be planned yet.

## M15 · Direct Gemini provider ✅

**Goal (revised)** The **zero-setup** provider. Tora already requires a
Google key for the voice path, so with nothing else configured the agents
must still think — planning, verification and the coding loop against
Gemini's `generateContent` with function calling. A gateway account, when
the user adds one, outranks it. (The Live websocket needs no agent
provider: it is the voice path's transport, and agents are background
text workers.)

**Owns** `agents/providers/gemini.py`, `tests/agents/test_providers_gemini.py`

**Landed** `GeminiAgentProvider`, registered by `build_router` at priority
10 (below any gateway, above the mock) whenever a Google key exists. 16
offline tests + 1 live; suite **421 passing**.

**Verified as far as the account allows.** The live test reached Google
and got **HTTP 402 Payment Required** — a correctly formed request
stopped only by the depleted credits (see the log diagnosis after M8).
It skips on 402/429/403 and will pass unchanged once the account is
topped up.

**Design notes**
* **The key is a header, not a query string.** The app's own client
  sends `?key=…`, which is what `httpx` logs at INFO and how the key
  reached `tora.log` 166 times. This provider sends `x-goog-api-key`;
  a test asserts `key=` never appears in the URL.
* **Ids for an API that has none.** Gemini's `functionCall` parts carry
  no id and its `functionResponse` wants the *name*. We mint an id per
  call and, when encoding a tool turn, recover the name from the
  assistant turn that issued it — so the registry's id-based contract
  holds unchanged.
* Tool schemas are scrubbed of keywords Gemini's OpenAPI subset rejects
  (`additionalProperties`, `pattern`, `title`, …) rather than translated.
* A blocked prompt (`promptFeedback.blockReason`) is a `ProviderError`,
  not an empty success.
* `conftest.py` now also blanks `GOOGLE_API_KEY` and `cfg.google_api_key`,
  or every test would have gone online the moment this landed.

**HANDOFF** Complete. One milestone left: **M23**.

**Build** `GeminiAgentProvider(AgentProvider)`: map `ProviderMessage[]` →
`contents[]` (roles user/model, `functionCall`/`functionResponse` parts),
`ToolSpec[]` → `tools[{functionDeclarations}]`, response → `ProviderResponse`
with `tool_calls` populated from `functionCall` parts. Reuse the existing
httpx client and key handling; do not add a dependency. Register in
`build_router()` for all kinds when a key exists, with `MockProvider` as
fallback. `capabilities()`: tools ✓, vision ✓.

**Done when** Tests pass with a stubbed transport (record/replay JSON, no
network in CI), and a manual run with a real key completes the MyApp task
using the **fallback** planner path (empty `tool_calls` → ReAct loop),
proving the loop works against a live model.

**HANDOFF** _(none yet)_

---

## M14 · Claude agent provider ⬜

**Goal** The coding-quality provider. Routed to `TaskKind.CODING`.

**Owns** `agents/providers/claude.py`, tests.

**Build** Same shape as M13 against the Anthropic Messages API: `tools` as
`input_schema`, assistant `tool_use` blocks → `ToolCall`, `tool_result`
blocks for results, `stop_reason == "tool_use"` → `finish_reason="tool_calls"`.
Load the `claude-api` skill before writing this — model IDs and parameters
must not be guessed. Key from `TORA_CLAUDE_API_KEY` via `AccountStore`.

**Done when** Router sends `TaskKind.CODING` to Claude and `VISION` to
Gemini, proven by a test asserting `router.pick(...)` per kind.

**HANDOFF** _(none yet)_

---

## M15 · OpenAI, local, and custom providers ⬜

**Goal** Spec §6 — no lock-in. Four providers plus "bring your own endpoint".

**Owns** `agents/providers/openai.py`, `local.py`, `custom.py`, tests.

**Build** OpenAI: Chat Completions tool-calling. Local: Ollama/LM Studio at
`http://localhost:11434` — `capabilities(local=True, cost_tier=0)`, used for
`TaskKind.FAST`. Custom: OpenAI-compatible base URL from settings. All three
share one JSON-schema→tool mapping helper; factor it out rather than copying.

**Done when** `build_router()` assembles whichever providers have credentials,
and the router's kind→provider table matches spec §7 (coding→Claude,
vision→Gemini, fast→local).

**HANDOFF** _(none yet)_

---

## M16 · Connected accounts UI + routing settings ✅

**Goal** Spec §6–7 — the user picks providers and connects their own accounts.

**Owns** `agents/ui/accounts.py`, `agents/providers/accounts.py` (extend).

**Build** Settings section: per-provider row (status dot, key field or
"Connected", Test button calling `health_check()`, enable toggle, default
model). A routing table the user can override: task kind → provider. Keys go
to the OS credential store or env vars — **never** into the JSON file.

**Done when** Adding a key in the UI makes that provider available to the
router without an app restart, and removing it falls back cleanly.

**Landed** `agents/ui/accounts.py`, `ConnectedAccount.{base_url,models,kinds}`
in core, `config.set_agent_env`, `provider_from_account`, and bootstrap
now reads the saved `AccountStore`. 18 tests; suite **340 passing**.
Screenshot-verified with a live Test click: `OK — 1282 models`.

**Shape:** two sections, because they are two different questions.
*Endpoints* — name, URL, key, on/off, and a Test button that makes a real
request (a green tick that never called anything would be worse than no
button). *Routing* — an ordered model chain per task kind, editable as
comma-separated text, seeded from the measured `GATEWAY_MODELS`.

**Where things live, and why**
* Keys → environment + `.env` via `config.set_agent_env`, whose `TORA_`
  prefix check is the guard against writing arbitrary variables. There is
  a test that reads the accounts JSON off disk and asserts the secret is
  absent — that file gets synced, backed up and pasted into bug reports.
* Endpoints and chains → `AccountStore` JSON. A chain is stored as a
  kind-scoped account row (`kinds=["coding"]`), which `build_router`
  registers at **priority 50, above the catch-all endpoint's 40**. At
  equal priority the router picked the general one for coding — a bug the
  tests caught on the first run.
* Saved accounts outrank the bare environment, and the dialog seeds one
  card from `TORA_OMNIROUTE_*` so an existing `.env` setup is visible
  rather than presenting an empty page to someone already wired.
* `apply_to` rebuilds `manager.router` in place — no restart.
* Bootstrap passes no `AccountStore` for `in_memory`/`offline` runs, so a
  test never reads or writes the real profile.

**HANDOFF** Complete. **Phase 2 is done** except M15 (direct Gemini Live
provider, voice only). Phase 3 starts at **M18** (resume after restart) —
it depends only on M7 and makes long tasks survive the app closing, which
is the spec's §3 promise — or **M19** (Playwright browser worker).

---

## M17 · LLM planning and verification by default ✅

**Goal** Spec §9/§12 with a real model: plans for arbitrary goals
("build me a CRM"), and verification that judges rather than pattern-matches.

**Owns** `agents/planning/planner.py` (extend), `agents/verification/`,
`agents/understanding/classifier.py` (extend).

**Build** Make `ProviderPlanner` the default when a provider is connected,
with `TemplatePlanner` as fallback on any parse failure (keep the templates —
they are the zero-cost fast path for the common cases). Add
`ProviderVerifier`: sends the step, its results and the real filesystem state,
asks for a pass/fail judgement with reasons; combine with `RuleVerifier`
(rules first, model only for what rules cannot check). Switch
`AgentManager.classifier` to `ProviderIntentClassifier`.

**Done when** "Build me a CRM application" produces a sensible multi-step
plan, and a deliberately broken step (file written empty) is caught by the
verifier and triggers a replan.

**Landed** `agents/verification/provider_verifier.py`,
`TieredIntentClassifier`, and bootstrap defaults that switch on whether a
real provider is connected. 24 new tests; suite **322 passing, 2 live**.

**Both halves of the gate, verified live:**
* "Build me a CRM application" → an 11–12 step plan: project structure and
  git, dependencies, an Express backend with CRUD for Contacts/Deals/Tasks,
  Tailwind config, reusable components, four pages, React Router wiring,
  npm scripts, commit. Nothing template-shaped about it.
* An empty `App.jsx` passes `RuleVerifier` (the file exists) and is failed
  by the model, which is shown the real bytes on disk. Orchestrator already
  turns a failed step verification into a replan.

**Design: rules first, model only where rules are silent**
`CompositeVerifier` fails fast when rules fail (a missing file is not a
judgement call and paying a model to discuss it invites an argument), asks
the model when every check said "no rule" (nobody actually checked — the
real gap), and always asks on the final task verdict, because that is the
one the user sees. Evidence is the filesystem — sizes and opening bytes —
not the agent's own account of what it did. A broken judge degrades to the
rules' verdict, never to a bare pass: a verifier that rubber-stamps when
it fails is worse than none.

**The latency problem this milestone created, and fixed**
Making `ProviderIntentClassifier` the default put a ~4 s provider call in
front of **every voice turn**, including plain chat — on the one path
where Tora's whole value is answering immediately. `TieredIntentClassifier`
takes the heuristic's answer when it is confident (0.9 for an
imperative + object, 0.75 for a clear question) and spends a model call
only in the 0.5 "unsure" band. Measured after: classification is **0.0 s**
for both "Build me a CRM application" and "What is a React component?",
while "sort out my project" still reaches the model.

**Model routes are measured, not assumed.** The `fast` chain originally led
with `agy/gemini-3.6-flash-low`, which **times out**, and
`agy/gemini-3.5-flash-low` returns HTTP 200 with the body "Gemini 3.5 Flash
is no longer available" — healthy-looking and useless. `cc/claude-sonnet-4-6`
answered in **0.5 s** and now leads the chain despite not being a small
model. Re-measure before reordering `GATEWAY_MODELS`.

**Two robustness fixes found by running it:**
* `AgentManager.understand()` raised `TimeoutError` out of `submit()` when
  the classifier hung, taking the voice turn with it. It now degrades to
  "this is chat" — losing a delegation beats inventing one or crashing.
* Providers' `httpx.AsyncClient` was never closed, leaking a connection
  pool per shutdown and surfacing as an unawaited-coroutine warning that
  looked like an agent bug. `stop()` now closes them on the runtime loop
  before the loop ends.

**HANDOFF** Complete. Phase 2 is effectively done bar **M16** (connected
accounts UI, now mostly "let the user edit the gateway URL, key and model
chains from the settings page"). Phase 3 starts at **M18** (resume after
restart) or **M19** (Playwright browser worker, per M12's one adopt).

---

# Phase 3 — Advanced capabilities

## M18 · Durable tasks, resume after restart ✅

**Goal** Spec §3 taken seriously: close the app, reopen it, the job is still
there — running or resumable.

**Owns** `agents/manager/agent_manager.py`, `agents/persistence/`, tests.

**Build** On startup, load non-terminal tasks from `TaskStore`; mark tasks
that were RUNNING as PAUSED with "interrupted by restart"; offer Resume in
the panel. Resume re-enters the orchestrator at `task.step_index` with the
stored plan — which is why steps are idempotent (`create_folder` uses
`exist_ok`, `create_file` refuses to clobber). Audit every tool for
idempotency as part of this milestone.

**Done when** A test kills and rebuilds the manager mid-task and the resumed
run completes the remaining steps without redoing finished ones.

**Landed** `AgentManager.recover()` / `_resume_recovered()`,
`Orchestrator.run_task(resume=True)`, and an idempotency fix to
`create_file`. 8 tests; suite **348 passing**.

**How it works**
* `start()` calls `recover()`: every stored task that was PENDING,
  PLANNING, RUNNING, VERIFYING or WAITING_APPROVAL comes back **PAUSED**
  with `metadata["interrupted"]` and shows in the panel with a Resume
  button. Its agent card is rebuilt from `store.list_agents()`.
* **Nothing restarts on its own.** The process may have died *because* of
  that task; re-running it unasked would loop. There is a test asserting
  no `STEP_STARTED` fires after recovery until a human resumes.
* `resume()` on a task with no live run re-enters the orchestrator with
  `resume=True`: planning is skipped (the plan is in `task.steps`), any
  step that was RUNNING is reset to PENDING, and execution continues from
  `task.step_index`. A PENDING task with no plan is planned from the top.
* The gate test stages an interruption (3 done, 1 running, rest pending,
  later files deleted) and asserts the resumed run fires **exactly**
  `total − 3` step starts and completes.

**The idempotency audit**
Resume re-runs the step that was RUNNING at the crash, and that step may
have already finished its write. `create_folder` was fine (`exist_ok`).
`create_file` refused with "already exists", which would have failed work
that was actually done. It now succeeds on **identical** content
(`created=False`) and still refuses on different content — re-running
produces the same state, which is what idempotent should mean.

**HANDOFF** Complete. Next: **M19** (Playwright browser worker).

---

## M19 · Browser worker ✅

**Goal** Spec §5 browser tools for real: open a page, extract information,
fill a form.

**Owns** `agents/tools/browser_tools.py` (replace the stubs),
`agents/workers/browser.py`, tests.

**Build** Follow M12's verdict. Default expectation: Playwright driven
directly (chromium only, downloaded on first use, not bundled) or
`browser-use` if the research says it earns its weight. Tools:
`open_page`, `extract_information`, `click_element`, `fill_field`,
`screenshot_page`. All HIGH risk. Never auto-approve on a page with a
password field.

**Done when** An agent can open a local test HTML file, extract a value and
report it, with the browser dependency optional — absent, the tools fail
cleanly exactly as the stubs do today.

**Landed** `agents/tools/browser_tools.py` (real), `playwright` as an
optional requirement, 17 tests. Suite **363 passing**. The gate runs the
browser worker through the full orchestrator against a local `file://`
page and reads `$42` back. Playwright 1.63 installs on 3.14 as M12 said.

**Tools:** `open_page`, `extract_information` (whole page + links, or one
selector), `click_element` (selector or visible text), `fill_field`,
`screenshot_page` (workspace-guarded), `close_browser` (SAFE). All others
HIGH. `open_page` accepts only `http(s)://` and `file://`.

**The password rule is enforced in the tool, not the policy.** `fill_field`
on a page with a password field calls `ctx.approver` itself, so a standing
"always allow fill_field" cannot extend to credentials — that setting is
for repetition of the same risk, not escalation into secrets. The approval
request carries `value="•••"`, never the text. Two tests.

**Lifecycle:** one `BrowserSession` per task, keyed by `task_id`, launched
lazily. `get_session` subscribes to that task's `TASK_STATUS` and closes
the browser on any terminal state, so a crashed agent leaves no Chromium
behind. The bus publishes on the runtime loop — the loop Playwright lives
on — which is what makes scheduling the close from the callback correct.
A test that emitted from a different loop produced "Task was destroyed
but it is pending"; that is the symptom if this ever regresses.

**HANDOFF** Complete. Next: **M20** (computer-control worker, bridging
`ai/hybrid_pointer.py`).

---

## M20 · Computer-control worker ✅

**Goal** Spec §5 computer tools: move, click, type — bridged to the pointing
stack this app already has.

**Owns** `agents/tools/computer_tools.py`, `agents/workers/computer.py`.

**Build** Bridge to `ai/hybrid_pointer.py` (UI Automation first, OCR, then
vision) so `click("Save")` resolves by **name**, not coordinates — the same
reason `point_at` takes a name. Add `move_cursor`, `click`, `type_text`,
`key_press`, `screenshot`. All HIGH risk, always approval-gated, with a
global kill switch (Esc) that cancels every computer-control task.
**Never** auto-approve these, whatever the policy says — hard-code the floor.

**Done when** An agent opens Notepad, types a line and saves it, entirely
through approved tool calls, and Esc aborts mid-run.

**Landed** `agents/tools/computer_tools.py` (real), `always_ask` in
`Registry.execute`, `AgentManager.cancel_all(kinds=)`, Esc wiring in
`main.py`. 13 offline tests + 1 live; suite **373 passing**.

**Tools:** `find_on_screen` (SAFE — look without touching), `screenshot`
(SAFE, JPEG into the workspace), `move_cursor`, `click`, `type_text`,
`key_press`. Targets are **names**, resolved through
`ai/hybrid_pointer.find_target` (UIA, then OCR); coordinates are accepted
only as a fallback, and an unknown name fails with "use find_on_screen"
rather than guessing.

**The floor lives in the choke point.** Every acting tool declares
`always_ask = True`, and `Registry.execute` honours it above any policy,
ceiling or `always_allow`: a human is asked every single time. Tests
cover a HIGH ceiling, a standing "always allow click", and — importantly
— that `delete_path` at a HIGH ceiling still does *not* ask, so the floor
is opt-in per tool rather than a change to HIGH risk in general.

**Kill switch.** `computer_tools.kill_switch` trips every action into a
failure; `main.py` routes both Esc and the tray's Stop through it and
calls `cancel_all(kinds=[COMPUTER])`, so a coding task is not caught by
Esc. The switch resets on the next `TASK_CREATED`.

**Input goes through an injectable `Backend`** (pynput for real); tests
inject a recorder, so the suite never moves the mouse. The live gate
(`test_live_notepad_type_and_save`, `-m live`) opens Notepad and types on
the real desktop — run it hands-off. It was not run during the build
because it types into whichever window has focus.

**HANDOFF** Complete. Next: **M21** (multi-agent collaboration).

---

## M21 · Multi-agent collaboration ✅

**Goal** Spec §13 — "build me a complete website" spawns Frontend, Backend,
Testing agents that work together.

**Owns** `agents/manager/orchestrator.py` (extend), `agents/manager/graph.py`.

**Build** Sub-tasks: `Task.metadata["parent_id"]` and `["depends_on"]`; the
orchestrator runs independent sub-tasks concurrently and blocks dependents.
A `SupervisorPlanner` decomposes a large goal into sub-tasks with a worker
type each. Shared project memory is the coordination channel (scope
`project:<workspace>`) — no direct agent-to-agent messaging in v1. The panel
groups children under the parent.

**Done when** One request produces ≥3 agents, two of which run at the same
time, with the dependent one starting only after its dependency completes.

**Landed** `agents/manager/graph.py` (`TaskGraph`, `TaskNode`,
`TemplateDecomposer`, `ProviderDecomposer`), `AgentManager.submit_project`
+ `_run_project`, panel grouping. 23 tests; suite **396 passing**. The
gate test records each child's run window and asserts backend and
frontend overlapped while tests started only after both ended.

**Shape**
* "Build me a complete website" → a **parent task** whose Steps are the
  nodes and whose agent is a *Project Lead*, plus one child task and agent
  per node (Backend Agent, Frontend Agent, Testing Agent). The panel shows
  the children indented under the lead; the plan view and timeline show a
  project exactly as they show any task.
* The DAG is **declared and validated before anything runs** — cycles,
  self-edges, unknown and duplicate keys are `GraphError`s at planning
  time, not deadlocks discovered later. Borrowed from LangGraph, per M12.
* Children are ordinary tasks: they queue under `max_concurrent`, pause,
  cancel and resume like any other. Cancelling the parent cancels them.
* A failed child fails the project and **skips** its dependants with
  `blocked: <key> failed`; independent siblings still finish.
* **Coordination is shared memory, not messages.** When a child completes,
  its title and result are written to `project:<workspace>` memory — which
  is what the next child's planner already reads. No agent-to-agent chat;
  cheaper, inspectable in the timeline, and it survives restarts.
* Recursion guard: a child is never decomposed, however its goal reads.

**Two things found by testing, not by design**
* Cancelling a project only took effect after the *next child finished*,
  because the runner blocked on the wake event and checked cancellation
  only at the top of the loop — by then the child's work was done. It
  now wakes every 250 ms as well.
* The first `AGENT_STATUS` for any agent fires from `create_agent()`,
  before its task is linked, so a project member's card was born without
  a parent and never indented. Cards now re-home when the link appears.

**HANDOFF** Complete. Next: **M22** (autonomous coding loop).

---

## M22 · Autonomous coding loop ✅

**Goal** The agent writes code, runs the tests, reads the failure, fixes it,
repeats — the thing that makes it an employee rather than a scaffolder.

**Owns** `agents/workers/coding.py` (extend), `agents/verification/`.

**Build** A `run_tests` tool (detects pytest / npm test / vitest), and a
coding-worker loop: implement → test → on failure feed the output back with
the relevant file contents → fix → retest, bounded by attempts and wall
clock. Every attempt is an event so the timeline shows the struggle.

**Done when** Given a repo with one deliberately failing test, the agent
fixes it within three attempts, and the loop gives up cleanly (FAILED, with
the last output) when the bug is unfixable.

**Landed** `agents/tools/test_tools.py` (`run_tests`), a tool-result hook
in `Worker._run_react`, attempt accounting in `CodingWorker`, and a
verifier rule for test runs. 9 tests; suite **405 passing**. Both gate
cases pass with a scripted model: a red run is fixed and goes green on
attempt 2, and three consecutive reds fail the step carrying the last
report — at 3 test runs, not the 20-iteration budget.

**Tool ergonomics are the deliverable (SWE-agent's finding, per M12).**
`run_tests` does the reading for the model: it names the failing tests,
extracts each assertion and its `file:line`, and says what to do next.
Green is one line; red is under a screen. A test asserts the red report
is under 1,200 characters. Runner detection is by evidence — pytest
markers, `package.json`'s test script (vitest when it says so) — and
npm's placeholder "no test specified" script is not mistaken for one.

**The discipline around the loop.** Every red `run_tests` is an *attempt*
announced in the timeline (`Test attempt 2/3: 1 failed — test_add`); the
third consecutive red fails the step with the last report. A green run
resets the count, so a step that fixes one bug and uncovers another gets
a fresh budget — tested with four reds that never make three in a row.

**Two things the loop changed elsewhere**
* `RuleVerifier` failed any step containing an `ok=False` result. For
  `run_tests` a red run is information, not an error: the step's claim
  is "tests pass *now*", so only the **last** test run counts. Every other
  tool's failure still fails the step.
* pytest's `-q` prints a bare `2 passed in 0.02s`, not the banner the
  parser expected, so counts read as 0. It now reads the last few lines
  regardless of shape.

**Also closed:** the M8 follow-up. The panel's action line now reads
`Creating Navbar.tsx` / `Running tests` rather than `coding: create_file`
(`describe_call` in `agents/workers/base.py`).

**HANDOFF** Complete. Remaining: **M15** (direct Gemini Live provider,
voice only) and **M23** (ship: packaging, limits, safety — including the
`tora.log` key leak).

---

## M23 · Ship: packaging, limits, safety 🟡

**Goal** The workforce survives being installed on someone else's machine.

**Owns** `tora.spec`, `installer.iss`, `BUILD.md`, `TESTING.md`,
`agents/manager/limits.py`.

**Build** PyInstaller: `agents/**` hidden imports, the SQLite DBs under
`%LOCALAPPDATA%\Tora`, first-run workspace creation. Limits: max concurrent
agents, per-task wall clock, per-task token/cost budget with a visible
counter, a global "stop all agents" control. Safety review: no tool can
escape the workspace, no auto-approved HIGH risk, keys never logged, agent
events scrubbed of file contents over N bytes. Add an agent section to
`TESTING.md` with a manual QA script.

**⚠ Known issue, found during M7 — fix before shipping (or sooner)**
`httpx` logs every request URL at INFO, and the Gemini key travels as a
query parameter, so **the API key is written in plaintext to `tora.log`** —
166 occurrences in one local log already. `main.py` calls that log "the #1
debugging tool", i.e. exactly the file a user would paste into a bug
report. One-line fix in `main.py`'s `_setup_logging`:
`logging.getLogger("httpx").setLevel(logging.WARNING)`. Then rotate the
existing logs, and audit `ai/*.py` for any other place a key reaches a log
line. Not caused by the agent system; it predates it.

**Done when** A built `.exe` on a clean Windows VM completes the MyApp task
from voice, and the safety checklist in the PR description is ticked with
evidence.

**Landed** `logsafe.py`, `agents/manager/limits.py`, `MeteredProvider`,
two limit settings, "Stop all agents", event scrubbing, `collect_submodules("agents")`
in `tora.spec`, TESTING.md §26, BUILD.md notes. 22 new tests; suite
**443 passing**.

**The key leak, closed both ways.** `logsafe.py` holds httpx/httpcore at
WARNING (no request lines), puts a `RedactingFilter` on every handler
(`?key=`, `AIza…`, `sk-…`, `Bearer …`, GitHub tokens), and **scrubs the
existing logs on startup**. Run against the real log directory:
**168 lines redacted, 0 remaining.** The app's own client still sends
`?key=` on six httpx calls in `ai/gemini.py` and `ai/model_registry.py`;
moving those to the `x-goog-api-key` header is the right follow-up, but
it could not be verified while Gemini credits are depleted, and the
filter makes it unnecessary for the leak.

**Limits, not labels.** `TaskLimits(wall_clock_s, max_tokens)` from config
(defaults 30 min / 300k). `MeteredProvider` wraps whatever the router
chose, counts every call, publishes `USAGE` events (the card shows
`Step 4/10 · 12.3k tokens`), and refuses a call past either limit; the
orchestrator also checks before each step. A task over a limit is
FAILED with which limit and by how much. Tested through the orchestrator
for both limits.

**Safety checklist — each item is a test in `test_ship.py`:**
* no tool escapes the workspace (7 tools tried, none did) ✓
* no HIGH-risk tool runs unasked under the default ceiling ✓
* computer control asks even at a HIGH ceiling (`always_ask`) ✓
* events carry `<50000 chars>`, not file contents; the real call is untouched ✓
* keys never reach the log (`logsafe`) ✓
* both real providers send keys as headers, not query strings ✓
* agents default outside the app folder ✓

**Build verified locally.** `python -m PyInstaller tora.spec` produced
`dist/Tora/Tora.exe` (78 MB exe, 1.3 GB bundle). Run for 45 s, the
**frozen** app logged `agent workforce ready — workspace …\Tora Projects`
with no errors, and — with `logsafe` active — wrote **zero** httpx request
lines and zero key-shaped strings. So the spec bundles `agents/**`
correctly and the workforce starts inside the exe.

**What is NOT verified, honestly:** the exe on a **clean VM** (a machine
with no Python), and the MyApp task *from voice* in the frozen build —
voice needs Gemini credits. TESTING.md §26 is the script for both.

**HANDOFF** Everything but the clean-VM run is done. To finish: install
`dist/Tora` on a machine without Python, say the MyApp request, tick §26.

**HANDOFF** _(none yet)_

---

## M24 · Industry extension framework ✅

**Goal** Industry packs plug into the agent platform without teaching the
core any industry. A pack is a removable folder.

**Owns** `agents/extensions/*`, `agents/manager/scheduler.py`,
`agents/tools/spreadsheet_tools.py`, `agents/ui/extensions.py`, and the
generic core hooks listed below. Design: `docs/agents/EXTENSIONS.md`.

**Build** `IAgentExtension` (get_agent_templates / tools / workers /
skills / workflows / permissions / integrations / dashboard modules /
document types / validation rules / intent rules / context lines /
scheduled jobs + lifecycle hooks), `ExtensionManifest`, and
`ExtensionRegistry` (discover, install, enable, configure, disable,
uninstall, restore), which records everything a pack registered so it can
unplug exactly that. Scheduler: `Schedule` + `FolderWatch` triggers, no
overlap, no firing for past slots, missed slot fires once, state kept
across restarts. Generic hooks in core: `Registry.unregister`,
`WorkerRegistry.unregister`, tool flags `sensitive` / `confirm_word` /
`ask_reason` / `soft_fail`, `Finding` + `EventKind.FINDING`,
`data["findings"]` / `data["checks"]` lifted onto the task,
exception-first result text, `Intent.metadata`, `ctx.extras["skills"]`
and `["context_lines"]` in worker prompts, `TemplateSpec.extras`, and a
typed-word approval for sensitive calls (`approve(..., confirmation=)`,
never granted per task, never by voice). UI: Extensions switchboard,
dashboard frame (a tab per module), sensitive approval dialog, FINDING
rows in the timeline, tray "Dashboard…" / "Extensions…". Voice:
`manage_agents` list/enable/disable_extension, open_dashboard,
create_agent; enabled packs listed in the per-turn context.

**Done when** A toy pack with no industry knowledge loads, routes an
utterance, runs a workflow, raises a finding, and unloads leaving the
registries exactly as they were.

**Landed** `test_extensions_framework.py` (toy pack: plug/unplug,
routing, state/restore, activation offer, tool-clash rollback, schedule
maths, once-per-slot, folder watch, sensitive approvals). Core
spreadsheet tools with `test_tools_spreadsheet.py`.

---

## M25 · Accounting extension, Phase 1 ✅

**Goal** The first industry pack: a digital accounting team that prepares
and surfaces exceptions, with the accountant in control of anything that
changes the books, moves money or files a return. All of it runs on mock
data with no credentials. What it does: `docs/agents/ACCOUNTING.md`.

**Owns** `extensions/accounting/**`, `tests/agents/test_accounting_*.py`,
`tests/agents/test_ui_extensions.py`.

**Built**
* Manifest, knowledge layer (skills per role), `AccountingJurisdictionProfile`
  with generic/ZA/GB/US/EU/AU presets (typical values, never advice),
  `ClientWorkspace` (one folder per client, `accounting.json`).
* Permission model: `ActionLevel` READ/DRAFT/POST/SENSITIVE mapped onto
  RiskLevel + always_ask + sensitive; `ConfidencePolicy` (draft / review /
  ask), per client.
* `IAccountingProvider` + `MockAccountingProvider` (seeded demo company with
  planted exceptions), `LocalBooksProvider` (real double-entry ledger),
  `CSVExcelProvider` (reads exports, "posts" by writing import files). Xero,
  QuickBooks, Sage, Pastel and custom ERP are declared and unavailable.
* 61 tools (category `accounting`): reads, 15 checks, drafts, documents
  (PDF/DOCX/EML/OCR extraction with per-field confidence), reports/packs
  (xlsx with formulas), `post_drafts` (POST), `release_payment_batch` and
  `submit_tax_return` (SENSITIVE).
* `DraftStore`: drafts, the client's open exceptions, processed documents.
* 8 templates (Bookkeeper, AP, AR, Month-End, Tax Preparation, Audit
  Assistant, Payroll Review, Management Reporting), one `AccountingWorker`,
  9 workflows + accountant-initiated post/release/submit runs, intent rules
  with client/period extraction, validation rules.
* Dashboard module (client, period, month-end %, needs attention, drafts,
  debtors & creditors, deadlines, agents, reports, run buttons) and the
  agent setup wizard.

**Landed** `test_accounting_pack.py` (20 tests: load/unload, tool levels,
questions stay chat, three providers answer the same questions,
unconnected providers say so, confidence thresholds, duplicates, invoice
pipeline on real PDFs, supplier statement, bookkeeping without touching
the books, month-end surfaces all 18 planted exceptions, books unchanged
by a close, missing documents, POST approval + denial, always_ask beats
always_allow, sensitive blocking with the typed word, tax return
sensitive, restart restores pack/agents/drafts, interrupted month-end
resumes) and `test_ui_extensions.py`. The demo month-end runs offline in
about 2 s.

---

## M26 · Accounting extension, Phase 2: real systems ⬜

**Goal** The same agents against the accountant's real systems.

**Owns** `extensions/accounting/providers/*`, a new
`extensions/accounting/connectors/` (email, storage), connected-accounts UI
additions (manager).

**Build, in integration-priority order (API > OAuth > file > browser > UIA > vision)**
1. `XeroProvider` (OAuth2 PKCE, Accounting API): accounts, TB, journals,
   bank transactions, invoices/bills, contacts, POs; post = create
   draft/authorised invoices/bills/manual journals; attachments.
   Tokens in the OS keyring, never in `.env` or logs (extend `logsafe`).
2. `QuickBooksProvider`, `SageProvider` on the same interface; run the
   provider-abstraction test against each with recorded fixtures (no
   network in the default suite; `-m live` for the real sandbox).
3. Email: IMAP (generic) then Microsoft Graph / Gmail API. Attachments
   land in the client's Inbox, so the folder watch and invoice workflow
   run unchanged. Sending reminders is a POST-level tool that only runs
   when the client's policy allows it.
4. OneDrive / SharePoint / Google Drive as document sources (sync to
   the client folder).
5. Bank feeds where available (Open Banking / aggregator) as another
   `import_bank_statement` source.
6. Practice-wide dashboard: deadlines and open exceptions across clients.

**Done when** A month-end on a Xero demo company produces the same
exception categories as the mock books, a posted draft appears in Xero as
a draft/authorised document, and no token appears in any log.

---

## M27 · Desktop control: ask the app, don't click at it ✅

**Goal** Any app on this PC, driven by voice or by an agent, in the
background, with the user confirming anything that acts. Design and the
research behind it: `docs/agents/DESKTOP_CONTROL.md`.

**Owns** `agents/control/**`, `agents/tools/control_tools.py`, the
per-call approval hooks in `agents/tools/registry.py`, the voice fast
path in `companion_manager.py`.

**Built**
* A tier ladder every action declares itself on - App Action, app API/CLI,
  deep link, system key, background UI, on-screen input, vision - and the
  registry reports which rung did the work.
* Machine discovery: Start Menu apps, every registered URI scheme (254 on
  the dev machine), which CLIs exist. Cached, ~0.7 s cold.
* System layer with no focus stealing: media keys (so "next song" works
  while Spotify is minimised), volume, now-playing from the media app's
  window title, window focus/minimise/maximise/close, clipboard.
* Background UI Automation: list a window's controls and invoke / toggle /
  select / expand / set_value / read them where they stand, with the
  legacy DoDefaultAction fallback. Tora's own windows stay invisible.
* Adapters: Windows itself, Spotify, VS Code / Cursor / Windsurf /
  Antigravity (open_project resolves a NAME from the editor's own recent
  list; `code chat` and Cursor's prompt deep link cover "put this in the
  chat"), and six browsers.
* Tools: `list_app_actions`, `app_action`, `ui_control`, `open_link`;
  the computer worker leads with them and falls back to clicking.
* Asking is per action: PASSIVE never asks, REVERSIBLE/CHANGES ask once
  with a grant key for that action alone ("always allow spotify.next"),
  SENSITIVE asks every time. `asks_for_call` / `grant_key` /
  `ask_reason_for` are generic tool hooks; `ApprovalRequest.grant_key`
  carries it; the dialog says whether the screen will move.
* Voice fast path: `desktop_action` runs one action with no task or plan,
  confirmed through the same dialog (`AgentManager.request_confirmation`,
  plus standing grants).
* Classifier: "spotify next song", "turn the volume down", "open
  bluetooth settings", "minimise the Chrome window" are tasks for the
  computer worker; questions still stay questions.

**Landed** `test_control_desktop.py` (14 tests: catalogue, per-action
asking, denial, per-action grants, tier reporting, unavailable routes,
kill switch, no-task confirmation, the voice path, and read-only checks
against the real machine). Suite **547 passing**.

**Next (Phase 2, in order)** picture-in-picture desktop (UFO2's RDP
loopback) so even on-screen actions never touch the user's screen;
Windows App Actions + MCP as tier 1; Spotify Web API as a connected
account; WinRT media session; learn-by-watching workflows; more adapters.

---

## Appendix — the acceptance test that defines "done" for Phase 1

`tests/agents/test_e2e.py` already encodes it. Any milestone that breaks it
is not finished:

```
"Create a new folder called MyApp and build the foundation of a React
 application inside it."
    → classified as a task, not chat
    → Application Builder agent appears
    → plan with N steps, visible
    → MyApp/package.json, index.html, src/main.jsx, src/App.jsx on disk
    → every step verified against the filesystem
    → COMPLETED, with the full history readable after a restart
```

---

# After M23 — integration review: does it work *with Tora*?

A deep read of the seams between the workforce and the voice companion,
done after every milestone was green. Tests could not have found these;
they are about what a person experiences.

**Landed** `_on_agent_event` / `_say_when_idle` / `agent_context_line` /
`_tool_manage_agents` in `companion_manager.py`, `manage_agents` in the
live tool declarations, `agents/ui/orbs.py`, a `sig_agents_ready` signal.
26 tests; suite **469 passing**. Verified in the running app: workforce
ready **0.77 s** after start, off the GUI thread, zero tracebacks.

**What was missing, and what closed it**
1. *Silence on completion.* An agent finished and Tora said nothing. Now
   every top-level task announces its end — spoken if Tora is idle
   (never interrupting an answer), and always a tray toast. Failures name
   the reason; cancellations stay silent (the user did that); a project's
   children defer to the Project Lead.
2. *Tora did not know about its agents.* "Is my app ready?" got a blank.
   `agent_context_line()` now adds one line to every turn's context —
   who is working, on what, how far, and what it is doing — plus the last
   two results, so the model answers from fact and can mention progress
   unprompted.
3. *No voice control.* `manage_agents(status|pause|resume|cancel|approve|deny)`
   is a live-model tool. "Stop that", "how's it going", "approve it" all
   resolve without guessing.
4. *Approvals were screen-only.* An approval request is now spoken —
   "Backend Agent needs your approval to delete path. Say approve or
   deny" — and toasted, so a hands-off user hears it.
5. *~650 ms on the GUI thread at startup.* Building the workforce blocked
   the tray icon. It builds on a worker thread now, and `sig_agents_ready`
   tells `main.py` when to wire the UI — which also fixed a bug the change
   itself introduced (wiring ran before the workforce existed).

**The orbs.** One per working agent, top-right, stacked downward: a dark
disc with the agent's initial inside a progress ring that sweeps
clockwise and shifts blue → cyan → green. A ring with no progress yet
carries a slow comet, so "planning" never looks like "stuck". Paused is
amber with a pause glyph; failure flashes red and holds 5 s; done fills
green with a tick and fades after 2.6 s. Hover for name, task and current
action; click to open the panel. One 60 fps timer that stops when nothing
moves — a test asserts a paused orb costs no frames. Window flags copy
`ui/overlay.py`, so it behaves like the cursor overlay, not a second app.

---

# Field report: "open the Devin app" got stuck

The first real use after the integration review. The user asked for an
app to be opened; it went to the Computer Agent, whose only way to open
anything was Win key → type → Enter — **three approvals** for one step —
and the dialog's **"Always allow key_press" did nothing** (M20's floor),
so it asked again on every keystroke. A button that silently does
nothing is the worst kind of bug: the user did the right thing and the
system ignored it.

**Fixed with three things**
* `open_app(name)` — MEDIUM risk, one step. Searches the Start Menu
  (per-user, all-users, Desktop) with exact → prefix → substring
  matching, skipping uninstallers and docs, falls back to PATH, launches
  the way a Start Menu click does, waits for the window and brings it
  forward. `list_windows` (SAFE) and `focus_window` (LOW) alongside. On
  this machine `open_app("Devin")` resolves to its `.lnk` directly.
* **Per-task grants.** `AgentManager.grant_for_task(task_id, tool)` lets a
  person allow an `always_ask` tool for the rest of *one* job. The floor
  still holds — no policy or setting can do this — but a human decision
  about this task can. It dies with the task and is never persisted. The
  dialog now offers **"Allow key_press for this task"** for those tools
  and keeps "Always allow" only where it is true.
* Worker prompts that reach for the right tool: open an app with
  `open_app`, never with the Windows key; see what is open with
  `list_windows` before touching the mouse.

**Two test bugs found on the way, both worth remembering**
* `` word boundaries became literal 0x08 backspace bytes when a regex
  travelled through a shell heredoc — the M6 bug, again, in my own code
  this time. Regex containing backslashes must be written with the Write
  tool or assembled from `chr(92)`, never piped through bash.
* A test that created a `QApplication` without holding a reference made
  the interpreter crash at exit with code 127 and no summary line. UI
  tests keep the app in a module-scoped fixture for exactly this reason.

482 passing.

---

# Field report 2: what the logs said after real use

Seven real tasks in `agents.db` and the pointer log behind them. Two
completed; the failures all shared root causes that tests could not have
shown, because they are about the desktop the agent is looking at.

**1. The pointer found its own reflection.** Log:
`UIA hit: 'text box' -> Looking for text box [TextControl]` and
`'Compose' -> find_on_screen(name=Compose)`. The agent searched for a
name, the Active Agents panel *displayed* "Looking for text box", and the
UIA walk — which starts from the foreground window — matched that label
inside Tora's own window. The same bug affects voice pointing: ask for
the Send button with the panel in front and Tora can point at its own
caption. `ai/hybrid_pointer._pointing_root()` now never returns a window
from this process; when Tora is in front it walks the topmost window
that is not ours, and callers may name a window (`hwnd`). Pinned by a
test that asserts the root's `ProcessId` is never ours.

**2. Guessing names into the void.** 'message input field', 'chat input',
'Type a message', 'Message Devin', 'Send message', 'Ask a followup',
'input', 'textarea' — eight misses in a row, each a model round trip,
until the iteration budget ran out. Three fixes: `find_on_screen` on a
miss now returns **the controls that ARE visible**, so the model picks a
real name; every computer tool takes `window=<title>` to target one app
and bring it forward; and `type_text(window=…)` types into the app's own
focused input, which for chat apps means never hunting for the box.

**3. OCR was off.** `rapidocr-onnxruntime not installed — Tier 2 (OCR)
disabled`. It installs fine on 3.14 (`onnxruntime` was already present)
and now loads in ~2 s. Placeholder text like "Message Devin" is readable
even when the Electron tree gives the box no name.

**4. A failed look failed the step.** `verification failed: 1/3 checks
passed; failed: find_on_screen, find_on_screen` — the verifier counted
a miss as a failure. Now a failed **SAFE** (read-only) tool never fails a
step that ended DONE; only acting tools can. Same principle as the
run_tests rule from M22.

**5. Planned a step the worker could not do.** 'Find and open Tora's
website' burned 67k tokens on "List workspace directory contents" —
a computer agent with no `list_dir`. The planner was told every tool in
the registry. It is now told the **worker's** tools (`extras["tool_names"]`),
and the computer worker gained `read_file`/`list_dir` and a 16-iteration
budget.

**6. Judged a UI task by the disk.** 'open notepad and describe what
Tora is' failed with "No files were written to disk". The model verifier
is now told to judge against the goal — for open/show/do-on-screen
goals the evidence is the open windows, which it is now shown.

**7. Routing.** 'open notepad…' went to the coding worker. "open <app>"
and "in <app>" now classify as COMPUTER, checked before BROWSER so
"open opera" is an app, not a URL.

485 passing.

### Field report 3: "it said it opened Devin but I don't see it"

The task log read like a success: `open_app Devin` → `list_windows` →
`focus_window` → `type_text hello` → `key_press enter` → verified ✓. The
screen said otherwise. Three things were wrong, each fixed at its root:

**1. The agent could see — and drive — Tora's own windows.** With Devin
not open, `list_windows` offered "Active Agents - Tora - AI Companion",
the agent focused it, typed "hello" into the panel's own text box and
pressed Enter, which submitted "hello" as a *new task*. A workforce that
can operate its own controls is a loop waiting to happen. Now Tora's
windows do not exist as far as the agent is concerned: `list_windows`,
`focus_window`, `_hwnd_for` and `_pointing_root` all skip any window
whose process is Tora's **or** whose title ends in `APP_TITLE_SUFFIX`
(the title check catches Tora windows from another process — the demo
and the test suite run a workforce next to a running Tora).

**2. `open_app` called a dead launch a success.** Devin.exe exited
straight away (a downloaded update pending) and the tool said
`launched Devin (no window seen yet)` with ok=True, so the worker
carried on. It now waits 10 s, then **fails** with what happened:
"its process exited straight away — it may be applying an update…
Do NOT assume it is open."

**3. The planner didn't know Devin was installed.** It planned "Open
Devin in the browser". The manager now passes `extras["context_lines"]`
to planner and worker: for COMPUTER/GENERAL tasks that is the Start
Menu (`installed_apps()`), plus the rule "if the goal names an
application, the FIRST step is open_app".

Also from the same log: `key_press` now decodes a stringified list
(`"['ctrl','s']"`) and chords (`"ctrl+s"`); `screenshot` returns the
JPEG (`image_b64`) and the worker appends it as a user turn so the model
actually *sees* what it captured rather than describing it from
imagination; the registry scrubs result payloads over `EVENT_ARG_LIMIT`
out of the TOOL_RESULT event so a 100 KB frame never lands in the
timeline or the task store.

490 passing.

### Field report 4: "Create a florist website" failed with the website on disk

`index.html` (28 KB) was written. The agent then checked it with
`Get-Item "...index.html" | Select-Object Name, Length, LastWriteTime`, and
`run_command` sent that to **cmd.exe**: `'Get-Item' is not recognized`. The
RuleVerifier fails a step on any failed non-SAFE call, so the finished step
failed, the one replan hit the same wall (the model then worked out
`powershell -Command` and succeeded, but the earlier failure still
counted), and the task ended FAILED with styles.css, script.js and the
commit never run. The same bug failed two earlier builds (`Start-Process`).

**1. Commands run in the shell they are written for.** `run_command`
detects PowerShell (Verb-Noun cmdlets, `$variables`, `$env:`) and runs it
through `powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass
-Command` with UTF-8 output. Everything else still goes through cmd.exe,
which npm/npx `.cmd` shims need. A `shell` argument (auto | cmd |
powershell) lets the model choose, and the tool description says so.
Results carry `data["shell"]`.

**2. A failed look is not a failed step.** `run_command` marks
`data["read_only"]` when every part of the line only inspects (Get-Item,
Test-Path, dir, type, git status, npm ls, …; a redirect or any acting part
makes it acting). The RuleVerifier skips failed read-only calls, the same
rule M20 made for SAFE tools. A failed `npm run build` still fails its step.

Tests: PowerShell really runs in PowerShell (the field command verbatim),
read-only classification, the verifier rule, and a full manager replay of
the florist run with the live model's calls, which is now COMPLETED.
