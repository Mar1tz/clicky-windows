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
| M6  | Green suite + bootstrap committed  | 1     | 🔵     | M2–M5      |
| M7  | Companion integration (delegation) | 1     | ⬜     | M6         |
| M8  | Active Agents panel                | 1     | ⬜     | M6         |
| M9  | Activity timeline + logs           | 1     | ⬜     | M8         |
| M10 | Plan viewer + approval dialog      | 1     | ⬜     | M8         |
| M11 | Tray entry + agent settings page   | 1     | ⬜     | M8         |
| M12 | Open-source framework research     | 2     | ⬜     | M6         |
| M13 | Gemini agent provider              | 2     | ⬜     | M6         |
| M14 | Claude agent provider              | 2     | ⬜     | M13        |
| M15 | OpenAI + local + custom providers  | 2     | ⬜     | M13        |
| M16 | Connected accounts UI + routing    | 2     | ⬜     | M11, M15   |
| M17 | LLM planning + verification default| 2     | ⬜     | M13        |
| M18 | Durable tasks, resume after restart| 3     | ⬜     | M7         |
| M19 | Browser worker                     | 3     | ⬜     | M12, M15   |
| M20 | Computer-control worker            | 3     | ⬜     | M15        |
| M21 | Multi-agent collaboration          | 3     | ⬜     | M17, M18   |
| M22 | Autonomous coding loop             | 3     | ⬜     | M17        |
| M23 | Ship: packaging, limits, safety    | 3     | ⬜     | M16, M18   |

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

## M6 · Green suite + bootstrap committed 🔵

**Goal** Everything four agents built in parallel is committed, green, and
starts from one function call. This is the gate to Phase 1's UI work.

**Owns** `agents/bootstrap.py`, `tests/agents/test_bootstrap.py`, plus
fixes to whichever tests are red (coordinate with the package owner if the
sessions are still live).

**State right now**
* `agents/bootstrap.py` is written but **not committed**.
* All of M2–M4's work is **untracked** — `git status` shows eight
  `agents/*` directories and ~10 test files with no commit behind them.
* Suite: **192 passing, 3 failing.**

**The three failures** (all worker-owned, all small)
1. `test_planning_templates.py::test_folder_plan_from_goal_or_subdir`
2. `test_understanding_classifier.py::test_task_utterances[write a script called backup.py…]`
3. `test_understanding_classifier.py::test_chat_utterances[is the project set up correctly?]`

Read each failure before changing anything: decide whether the test or the
code states the intent correctly. (2) and (3) are classifier edge cases —
"write a script called backup.py" should extract `backup.py` as a hint, and
"is the project set up correctly?" is a question despite containing "set up".

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

## M7 · Companion integration — the app can delegate ⬜

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

**HANDOFF** _(none yet)_

---

## M8 · Active Agents panel ⬜

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

**HANDOFF** _(none yet)_

---

## M9 · Activity timeline + logs ⬜

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

**HANDOFF** _(none yet)_

---

## M10 · Plan viewer + approval dialog ⬜

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

**HANDOFF** _(none yet)_

---

## M11 · Tray entry + agent settings page ⬜

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

**HANDOFF** _(none yet)_

---

# Phase 2 — Plug the AI in

## M12 · Open-source framework research ⬜

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

**HANDOFF** _(none yet)_

---

## M13 · Gemini agent provider ⬜

**Goal** The first real AI behind `AgentProvider`. Gemini first because the
app already speaks it (`ai/gemini.py`, REST, no vendor SDK).

**Owns** `agents/providers/gemini.py`, `tests/agents/test_providers_gemini.py`

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

## M16 · Connected accounts UI + routing settings ⬜

**Goal** Spec §6–7 — the user picks providers and connects their own accounts.

**Owns** `agents/ui/accounts.py`, `agents/providers/accounts.py` (extend).

**Build** Settings section: per-provider row (status dot, key field or
"Connected", Test button calling `health_check()`, enable toggle, default
model). A routing table the user can override: task kind → provider. Keys go
to the OS credential store or env vars — **never** into the JSON file.

**Done when** Adding a key in the UI makes that provider available to the
router without an app restart, and removing it falls back cleanly.

**HANDOFF** _(none yet)_

---

## M17 · LLM planning and verification by default ⬜

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

**HANDOFF** _(none yet)_

---

# Phase 3 — Advanced capabilities

## M18 · Durable tasks, resume after restart ⬜

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

**HANDOFF** _(none yet)_

---

## M19 · Browser worker ⬜

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

**HANDOFF** _(none yet)_

---

## M20 · Computer-control worker ⬜

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

**HANDOFF** _(none yet)_

---

## M21 · Multi-agent collaboration ⬜

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

**HANDOFF** _(none yet)_

---

## M22 · Autonomous coding loop ⬜

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

**HANDOFF** _(none yet)_

---

## M23 · Ship: packaging, limits, safety ⬜

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

**Done when** A built `.exe` on a clean Windows VM completes the MyApp task
from voice, and the safety checklist in the PR description is ticked with
evidence.

**HANDOFF** _(none yet)_

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
