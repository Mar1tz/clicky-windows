# TASKS — agent system build board

Manager: `clicky-windows-main-0f`. Workers report by `SendMessage` when a
task's tests are green and the commit is in. Status: ⬜ todo · 🔵 in progress
· 🟡 in review · ✅ merged · ⛔ blocked.

| id    | owner | status | task                                                        |
|-------|-------|--------|-------------------------------------------------------------|
| M-0   | mgr   | ✅     | git init, GitHub remote, contracts (`agents/core`), docs     |
| W1-1  | w1    | 🔵     | Tool framework: `Registry`, file/terminal/git tools, stubs  |
| W2-1  | w2    | 🔵     | `MockProvider`, `Router`, `AccountStore`, `Worker` loop, worker types, intent classifier |
| W3-1  | w3    | 🔵     | `SQLiteTaskStore`, `SQLiteMemoryStore`+`AgentMemory`, `TemplatePlanner`/`ProviderPlanner`, `RuleVerifier`, supabase 0002 |
| M-1   | mgr   | 🔵     | `AgentRuntime` (thread+loop), `AgentManager`, `Orchestrator`, `test_e2e.py` |
| W1-2  | w1    | ⬜     | `agents/ui`: Qt bridge, Active Agents panel, timeline, plan view, approval dialog, standalone demo |
| W2-2  | w2    | ⬜     | `docs/agents/FRAMEWORK_RESEARCH.md` (LangGraph/CrewAI/AutoGen/OpenHands/SWE-agent/OpenAI Agents SDK/browser-use) + `GeminiAgentProvider` adapter over `ai/gemini.py` |
| W3-2  | w3    | ⬜     | `ProviderVerifier`, memory summarisation, task resume from `TaskStore` after restart |
| M-2   | mgr   | ⬜     | Wire into `companion_manager.py` chat path + tray "Agents…" + settings provider picker |

## Task specs

Full specs were sent by message and are reproduced in short here so a
worker can recover after context compaction. Always re-read `CLAUDE.md`,
`docs/agents/ARCHITECTURE.md` and `agents/core/*.py` first.

### W1-1 — agents/tools
`registry.py` `Registry(ToolRegistry)`: validate args vs JSON schema (own
mini validator), enforce `ctx.policy` → `ctx.approver(ApprovalRequest)`,
dry-run, time, emit `TOOL_CALL`/`TOOL_RESULT`, never raise. `file_tools.py`
create_folder/create_file/write_file/edit_file/read_file/list_dir/move_path/
delete_path(HIGH) all guarded by `ctx.is_inside_workspace`. `terminal_tools.py`
run_command/install_package/execute_script (MEDIUM, CREATE_NO_WINDOW, utf-8,
20k-char caps). `git_tools.py` init/status/diff/log/add/commit/branch/push(HIGH).
`browser_tools.py`/`computer_tools.py` real specs, HIGH, "not available in
Phase 1". `default_registry()`. Tests `test_tools_*.py`.

### W2-1 — agents/providers, agents/workers, agents/understanding
`MockProvider` (script or handler mode, records calls). `Router` (priority,
hint, vision, GENERAL fallback, LookupError). `AccountStore` (JSON, no
secrets; keys via `TORA_<PROVIDER>_API_KEY`). `build_router()`. `Worker`:
scripted path (pre-filled `step.tool_calls` executed directly) and ReAct
path (provider ⇄ registry, max_iterations, checkpoint before every call,
progress events). Worker types coding/file/terminal/research/browser/computer
+ `WorkerRegistry`. `HeuristicIntentClassifier` + `ProviderIntentClassifier`
(workspace_hint extraction, kind, title, reply). Tests.

### W3-1 — agents/persistence, agents/memory, agents/planning, agents/verification
`SQLiteTaskStore` (tasks/plans/events/agents, WAL, lock, `attach(bus)`).
`SQLiteMemoryStore` (FTS5-or-LIKE) + `AgentMemory` façade + `project_summary`.
`TemplatePlanner` (react app / python project / folder / fallback; pre-filled
tool calls; `metadata["subdir"]`; install only when `metadata["install"]`) +
`ProviderPlanner` (JSON plan, falls back) + `replan`. `RuleVerifier`
(per-tool checks, `verify_task`). `supabase/migrations/0002_agent_tasks.sql`.
Tests.

### M-1 — agents/manager
`runtime.py` `AgentRuntime`: daemon thread + asyncio loop, `submit(coro)`,
`call(coro, timeout)`. `orchestrator.py` `run_task(ctx)`: PLANNING → plan
(optional approval) → RUNNING (execute_step → verify_step → replan once) →
VERIFYING → terminal; progress + all events; persists via `TaskStore`.
`agent_manager.py` `AgentManager`: create_agent, submit(text|Task),
pause/resume/cancel, approve, list_agents/list_tasks/get_task, `events`.
`tests/agents/test_e2e.py`: the React-foundation acceptance run with
`MockProvider`, no network, no Node.
