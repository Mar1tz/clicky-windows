# Tora Agent System — Architecture

The autonomous-agent layer that turns "create a folder called MyApp and
build a React foundation in it" into a background job the user can watch,
pause and stop. This document is the contract. The code contract is
`agents/core/` (models, events, interfaces) — read both before building.

## 1. Flow

```
 user text ──► IntentClassifier ──► is_task? ──no──► normal chat path
                                        │yes
                                        ▼
                               AgentManager.submit()
                                        │  Task(PENDING)
                                        ▼
                                  Orchestrator.run_task()          (agent runtime loop,
                                        │                            daemon thread)
             PLANNING ◄─────────────────┤
                │  Planner.plan(ctx) → Plan(steps)
                │  [policy may require plan approval → WAITING_APPROVAL]
             RUNNING
                │  for step in steps:
                │      Worker.execute_step(step, ctx)     ← provider ⇄ ToolRegistry.execute
                │      Verifier.verify_step(step, ctx)    ← "did it really happen?"
                │      fail → Planner.replan() once, else FAILED
                │      progress = done/total → TASK_PROGRESS event
             VERIFYING
                │  Verifier.verify_task(ctx)
             COMPLETED | FAILED | CANCELLED
```

Every arrow above publishes on the `EventBus`. The UI, the persistence
layer and the memory layer are all just subscribers.

## 2. Packages and owners

| package                | owner    | what lives there                                               |
|------------------------|----------|----------------------------------------------------------------|
| `agents/core`          | manager  | models, EventBus, ABCs. **Frozen** — ask before changing.       |
| `agents/manager`       | manager  | AgentRuntime (thread+loop), AgentManager, Orchestrator          |
| `agents/tools`         | worker 1 | `Registry(ToolRegistry)`, file / terminal / git / browser / computer tools |
| `agents/providers`     | worker 2 | `MockProvider`, `Router(ProviderRouter)`, `AccountStore`        |
| `agents/workers`       | worker 2 | `Worker(BaseWorker)` ReAct loop + coding/file/terminal/… types  |
| `agents/understanding` | worker 2 | `HeuristicIntentClassifier`, `ProviderIntentClassifier`         |
| `agents/memory`        | worker 3 | `SQLiteMemoryStore(MemoryStore)`, `AgentMemory` façade          |
| `agents/planning`      | worker 3 | `TemplatePlanner`, `ProviderPlanner`                            |
| `agents/verification`  | worker 3 | `RuleVerifier`                                                  |
| `agents/persistence`   | worker 3 | `SQLiteTaskStore(TaskStore)`                                    |
| `agents/ui`            | worker 1 | Qt bridge + Active Agents panel + timeline (round 2)            |
| `tests/agents`         | everyone | `test_<package>_*.py`                                           |

Cross-package imports go **only** through `agents.core`. A worker package
never imports another worker package. The manager package imports all of
them and wires them together.

## 3. Threading model

Same as `CompanionManager`: one asyncio loop on a daemon thread
(`AgentRuntime`). All planner/worker/verifier/tool code is `async` and
runs there. Qt code lives only in `agents/ui`, which subscribes to the
bus and re-emits via `pyqtSignal` (queued to the GUI thread). **No package
except `agents/ui` may import PyQt6.**

Pause / resume / cancel are cooperative through `RunControl`: workers
`await ctx.control.checkpoint()` before every tool call.

## 4. Permissions

`PermissionPolicy` on the `AgentSpec` says the max `RiskLevel` a call may
have to be auto-approved. `Registry.execute()` is the single choke point:
it checks the policy, and when a call exceeds it, awaits
`ctx.approver(ApprovalRequest)`. The runtime's approver publishes
`APPROVAL_REQUIRED`, parks the task in `WAITING_APPROVAL`, and resolves
when the UI calls `AgentManager.approve(request_id, ok)`.

`PermissionPolicy` has four fields, checked in order of authority:
`denied_tools` (a hard veto) → `allowed_tools` (a whitelist *filter*, when
set) → `always_allow` (a user's standing yes for one tool, which lifts it
over the ceiling) → `max_auto_risk` (the ceiling). `always_allow` exists
because "Always allow delete_path" cannot be expressed by adding the tool
to `allowed_tools`: that list only narrows, so the risk ceiling would still
stop the call and the user would be asked again on the next step.

File tools additionally refuse paths outside `ToolContext.workspace`
(`ctx.is_inside_workspace`). Nothing an agent does should touch the rest
of the disk unless the policy's `workspace_roots` says so.

## 5. Providers

`AgentProvider.complete(messages, tools) -> ProviderResponse` is the
tool-calling interface. Phase 1 ships only `MockProvider` (scripted).
Phase 2 adds `GeminiAgentProvider` / `ClaudeAgentProvider` /
`OpenAIAgentProvider` / `LocalProvider`, each in `agents/providers/<name>.py`,
registered with the `Router` by `TaskKind` (coding→Claude, vision→Gemini,
fast→local). The user's choices live in `ConnectedAccount`s
(`AccountStore`), never hard-coded.

## 6. Determinism first

The Phase 1 planner is template-based and **pre-fills `Step.tool_calls`**.
A worker that receives a step with pre-filled calls executes them directly
without consulting the provider. That gives an end-to-end run with zero
network: classify → plan → tools → verify → COMPLETED. LLM-driven steps
(empty `tool_calls`) use the ReAct loop and are exercised with
`MockProvider` in tests.

## 7. Phase 1 acceptance test (`tests/agents/test_e2e.py`, manager-owned)

```
text = "Create a new folder called MyApp and build the foundation of a React application inside it."
mgr  = AgentManager(workspace_root=tmp_path, provider=MockProvider(), ...)
task = mgr.submit(text)                   # classifier says is_task → Task
wait until task.status.is_terminal
assert task.status is COMPLETED
assert (tmp_path/"MyApp"/"package.json").exists()
assert (tmp_path/"MyApp"/"src"/"App.jsx").exists()
events = store.events(task.id) ; assert PLAN_CREATED, STEP_DONE×N, VERIFICATION in kinds
```

(`npm install` is a MEDIUM-risk step the template marks optional; the
test runs with it skipped so CI needs no Node.)

## 8. Conventions

* Python 3.14, dataclasses, full type hints, module docstring explaining
  the *why*. Match the tone of `agents/core/models.py`.
* Tools return `ToolResult.failure()` for expected failures; never raise.
* Windows: `subprocess` with `creationflags=CREATE_NO_WINDOW`, UTF-8
  everywhere, `shell=True` only for `npm`/`npx` (they are `.cmd` shims).
* No new third-party dependencies without asking the manager (PyInstaller
  bundle size and Py 3.14 wheels are both constraints).
* Tests: `python -m pytest tests/agents -q` must be green before you report.
  Use `tmp_path`; never touch the real user profile or network.

## 9. Roadmap

**Phase 1 (now):** core ✓ · tools · providers(mock) · workers · understanding ·
memory · planning · verification · persistence · manager · e2e test · Qt UI ·
companion_manager integration.

**Phase 2:** real providers (Gemini first — reuse `ai/gemini.py` REST), settings
UI for provider choice, connected accounts, ProviderPlanner/ProviderVerifier
as defaults.

**Phase 3:** browser worker (evaluate `browser-use`), computer worker bridging
`ai/hybrid_pointer.py`, multi-agent tasks (Orchestrator spawns sub-tasks with
dependencies), long-running tasks surviving app restart (TaskStore resume).
