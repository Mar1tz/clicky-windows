# Open-source agent frameworks — what to reuse, what to keep

Spec §14: before building more from scratch, decide what an existing
framework should be doing for us. This is a decision document, not a
survey — every entry ends in **adopt**, **borrow**, or **skip**.

Researched 2026-09-21 against live PyPI metadata (versions and
constraints below are what `pypi.org/pypi/<name>/json` returned that day,
not recollection).

---

## The constraints that decide it

Tora is not a Python service. It is **a PyQt6 desktop app, shipped as a
PyInstaller bundle, on Windows, running Python 3.14**, whose agent layer
is already built and passing 273 tests. That ordering matters: we are not
choosing a framework to build on, we are asking whether any framework
earns a place in a system that already works.

Four filters, applied in order:

1. **Does it install on Python 3.14?** Not "probably" — declared.
2. **What does it drag in?** Every transitive dependency is bundle size,
   a PyInstaller hidden-import to discover, and a thing that can fail to
   find a wheel on the next upgrade.
3. **Does it want to own the control flow?** Our orchestrator already
   owns plan → execute → verify, with pause/resume/cancel, approvals and
   a durable event log. A framework that insists on driving is a rewrite.
4. **Does it solve a problem we still have?** Phase 1 has no gaps in
   orchestration. The real gaps are browser control (M19), computer
   control (M20) and multi-agent decomposition (M21).

## What PyPI actually says

| package | version | requires_python | declared deps | 3.14 classifier |
|---|---|---|---|---|
| `langgraph` | 1.2.12 | `>=3.10` | 6 | no (≤3.13) |
| `crewai` | 1.15.22 | **`<3.14,>=3.10`** | 31 | — |
| `autogen-agentchat` | 0.7.5 | `>=3.10` | 1 (meta) | — |
| `openhands-ai` | 1.11.0 | **`<3.14,>=3.12`** | **85** | no (≤3.13) |
| `sweagent` | 0.0.1 | `>=3.9` | 15 | — |
| `openai-agents` | 0.22.3 | `>=3.10` | 12 | **yes** |
| `browser-use` | 0.13.10 | `<4.0,>=3.11` | 37 | — |
| `playwright` | 1.63.0 | `>=3.10` | **2** | **yes** |
| `openai` | 3.16.2 | `>=3.10` | 6 | **yes** |
| `litellm` | 1.102.0 | `<3.15,>=3.10` | 14 | — |

Two candidates are eliminated by the first filter before any judgement
is involved: **CrewAI** and **OpenHands** both declare `<3.14`. They
cannot be installed into this app as it stands.

---

## Verdicts

### LangGraph — **skip** (borrow one idea)

Graph-structured agent execution with checkpointing, and the closest
thing here to what our orchestrator does. Only 6 declared dependencies,
so the weight is acceptable, but it has no 3.14 classifier and it wants
to own the control flow: nodes, edges, and a checkpointer holding the
state we already keep in `Task` + `SQLiteTaskStore`. Adopting it means
rewriting `Orchestrator`, `RunControl` and the persistence layer to fit
its state model, in exchange for capabilities we have.

**Borrow:** its explicit graph edges are a better answer to **M21**
(multi-agent) than ad-hoc parent/child task metadata. When we implement
sub-task dependencies, model them as a declared DAG — nodes with named
edges, resolved before execution — rather than letting each agent spawn
children at will. That is LangGraph's insight, and we can have it in
about 100 lines without the dependency.

### CrewAI — **skip** (blocked)

`requires_python <3.14`: it will not install. Beyond that, 31
dependencies for role-playing agent teams whose value is mostly prompt
scaffolding ("you are a senior researcher…"), which is `AgentSpec.goal`
plus a system prompt here. Even on a supported Python this would be a
large dependency for a thin idea.

### AutoGen (`autogen-agentchat`) — **skip**

Conversational multi-agent: agents talk to each other to converge. The
meta-package is thin, but the real weight is in `autogen-core` and the
extensions, and the model is a poor fit for a desktop assistant — chat
between agents burns tokens and wall-clock on a machine where the user
is watching a progress bar. Our **M21** plan (shared project memory as
the coordination channel, no direct agent-to-agent chat) is deliberately
the opposite, and cheaper.

### OpenHands — **skip** (blocked, and enormous)

`requires_python <3.14` **and 85 declared dependencies**. It is a
full agent platform with its own runtime, sandbox and UI — a product we
would be embedding, not a library. Nothing to adopt.

**Borrow:** its per-action sandboxing discipline is the right instinct
for **M20** (computer control). Our equivalent is the workspace guard
plus `RiskLevel`, which is cheaper and already built.

### SWE-agent — **skip** (borrow the agent-computer interface idea)

Research code (`sweagent 0.0.1`, 15 deps), aimed at benchmark runs on
GitHub issues rather than embedding in an app.

**Borrow:** its core published finding — that agent performance depends
more on *tool ergonomics* than on model choice — is directly actionable
for **M22**. Concretely: tool output should be compact and structured,
errors should say what to do next, and a file edit should fail loudly
when the target is ambiguous rather than guessing. Our `edit_file`
already requires `old_string` to match exactly once, which is the same
principle; the lesson is to keep applying it as we add tools.

### OpenAI Agents SDK (`openai-agents`) — **skip** (closest call)

The only orchestration framework here that declares **Python 3.14**, and
at 12 dependencies it is not unreasonable. Clean primitives: agents,
handoffs, guardrails, sessions, tracing.

It loses on filter 4, not on weight. It would replace `Worker`,
`ProviderRouter` and part of `AgentManager` — roughly 900 lines that
exist, are tested, and do exactly what we need including
pause/resume/cancel and approval gating, which the SDK does not offer in
the shape our UI requires. Its handoff model is also an alternative to
M21 worth re-reading when we get there.

**Reconsider if:** we ever want hosted tracing, or multi-agent handoffs
turn out much harder than expected in M21.

### browser-use — **skip in favour of Playwright directly**

37 declared dependencies, and it bundles its own LLM-driven page
reasoning — a second agent loop inside ours, with its own prompts and
its own model calls, which our orchestrator could neither pause nor
verify. It also declares no 3.14 classifier.

### Playwright — **ADOPT for M19**

The one clear adopt. Two declared dependencies, an explicit **3.14
classifier**, and it solves a problem we genuinely cannot solve
ourselves: a real browser with a real accessibility tree.

Terms that keep it safe in a PyInstaller bundle:

* `pip install playwright` ships no browsers. `playwright install
  chromium` downloads ~150 MB **at first use**, into the user profile,
  not into our installer.
* Make it an **optional dependency**. `agents/tools/browser_tools.py`
  already returns `ToolResult.failure("not available in Phase 1")`; the
  absence of Playwright should behave identically. The workforce keeps
  working, minus browser tools.
* Wrap it behind our own tool specs (`open_page`, `extract_information`,
  `click_element`, `fill_field`) so the agent loop, the approval gate and
  the event log stay ours.

### LiteLLM — **skip (superseded by the gateway)**

The usual reason to add LiteLLM is one interface over many providers.
The OmniRoute gateway now does exactly that, server-side, over plain
HTTP (see below). Adding 14 dependencies to solve a solved problem is
not worth it.

---

## The provider finding that changes Phase 2

Tested live against `https://ai.tmaritz.xyz/v1` on 2026-09-21:

* **1295 models across 28 prefixes**, fronting Claude, Gemini, GPT and
  open-weight models through one OpenAI-compatible surface.
* Tool calling works. `claude/claude-sonnet-4-6` returned
  `finish_reason=tool_calls` with
  `create_folder({"path": "MyApp"})` in **~2.0–2.4 s**.
* **It always streams.** Every `/chat/completions` response comes back as
  `text/event-stream`, even with no `stream` parameter. A client that
  calls `json.loads(body)` gets a `JSONDecodeError` — this cost an hour
  of confusion and is the single most important integration detail here.
* Reliability is per-route, not global. `aug/*` returned **502**,
  `agy/gemini-2.5-flash` **timed out at 90 s**, and `zm/*` returned
  **403** (separate entitlement). The three Claude routes — `claude/`,
  `agy/`, `cc/` — all worked.

**Consequence: M13, M14 and M15 collapse into one milestone.** The plan
was a Gemini adapter, then a Claude adapter, then OpenAI + local. One
`OpenAICompatibleProvider` — base URL and key from `AccountStore`, SSE
reassembly, `ToolSpec` → `tools[].function`, `tool_calls` deltas →
`ToolCall` — reaches all of them. No new dependency: `httpx` is already
required and already speaks streaming.

What the gateway does **not** remove:

* A direct Gemini provider is still wanted eventually, because the app's
  voice path uses the Gemini **Live websocket**, which no OpenAI-shaped
  gateway exposes.
* Per-route health matters. `Router.pick()` should prefer routes that
  have recently answered, and fall back rather than hand a 502 to a
  running agent. That is new work the original plan did not budget for.

---

## Summary: our packages, and what happens to them

| package | verdict | why |
|---|---|---|
| `agents/core` | **keep** | Contracts are the asset. Nothing external improves them. |
| `agents/manager` | **keep** | Pause/resume/cancel + approvals + durable events; no framework offers this shape. |
| `agents/tools` | **keep**, extend | Registry is the policy choke point. Add Playwright-backed browser tools in M19. |
| `agents/providers` | **keep**, simplify | One OpenAI-compatible adapter replaces three planned ones. |
| `agents/workers` | **keep** | Apply SWE-agent's tool-ergonomics lesson in M22. |
| `agents/planning` | **keep** | Templates are the zero-cost fast path; `ProviderPlanner` handles the rest. |
| `agents/verification` | **keep** | Rules first, model second. No framework does verification this way. |
| `agents/persistence` | **keep** | SQLite + event log already outlives the process. |
| browser layer (M19) | **adopt Playwright** | Optional dependency, 2 deps, 3.14 support, browsers downloaded on first use. |
| multi-agent (M21) | **borrow LangGraph's DAG** | Declared edges, resolved before execution. No dependency. |

**Net: one adopt (Playwright, optional, in M19), two borrowed ideas, and
no new required dependency.** The reason is not conservatism — it is that
the frameworks worth having either cannot install on 3.14 (CrewAI,
OpenHands) or would replace working, tested code with a different
control flow (LangGraph, OpenAI Agents SDK) while removing capabilities
our UI depends on.

## Recommended milestone changes

* **M13/M14/M15 → one milestone**: `OpenAICompatibleProvider` against the
  gateway, with SSE reassembly and per-route health tracking. Keep a
  direct Gemini provider as a later, separate item for the Live API.
* **M19**: Playwright, optional, wrapped in our own tool specs.
* **M21**: declare sub-task dependencies as a DAG up front.
* **M22**: treat tool ergonomics as the deliverable, not just the loop.
