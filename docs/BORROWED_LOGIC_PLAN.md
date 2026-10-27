# HeyTora — Borrowed Logic Plan

Oct 27, 2026 · @sadas

## Purpose and the one rule

HeyTora should rebuild the best ideas from 12 open-source agent projects inside its own Python 3.14 code. It should not bundle those projects. Most of them are TypeScript services, Docker stacks or cloud platforms. HeyTora is one Windows desktop app built with PyInstaller.

Paste this document after "HeyTora — How It All Works" when you ask an AI for changes. That document says how the system works today. This one says what to add, where it lands, and in what order.

**Port or copy, per repo:**

- **Port the logic** (the default). Read the source, then rebuild the idea in the HeyTora folder named in its section. Use HeyTora's own types from `agents/core`. All 12 repos allow this.
- **Copy code across** only when all three are true: the repo is MIT or Apache-2.0, it is Python, and it needs no new dependency. Keep the original licence header and add a line to `THIRD_PARTY_NOTICES.md`.
- **Never copy** anything from AutoGPT's `autogpt_platform` folder. It is under the PolyForm Shield licence, which has a noncompete clause. Port ideas only, from the docs.
- **Ask first** before adding any repo as a pip dependency. This is the existing rule: PyInstaller size and Python 3.14 wheels.

Every existing ground rule still holds. That covers asyncio under `agents/`, `ToolResult.failure()` instead of raising, one `Registry.execute()` choke point, secrets in Credential Manager, and tests with no network.

## The 12 repos at a glance

Five repos change HeyTora's engine first: trigger.dev, OpenHands, browser-use, Stagehand and Graphiti. The other seven add features on top of that engine.

| Repo | What it is | What HeyTora takes | Lands in | How | Licence | Phase |
| --- | --- | --- | --- | --- | --- | --- |
| [trigger.dev](https://github.com/triggerdotdev/trigger.dev) | Durable background jobs (TypeScript) | Checkpointed runs, waitpoints, retries, queues, idempotency keys | `agents/manager` | Port | Apache-2.0 | 1 |
| [OpenHands](https://github.com/OpenHands/OpenHands) | Coding agent (Python) | Action/observation event stream, stuck detector, context condenser, microagents | `agents/core`, `agents/workers`, `devlink/` | Port, copy small parts | MIT (core) | 1 |
| [browser-use](https://github.com/browser-use/browser-use) | Browser agent (Python) | Indexed page elements, multi-action steps, loop detection, secret placeholders | `agents/workers/browser.py`, `agents/tools` | Port, copy small parts | MIT | 1 |
| [Stagehand](https://github.com/browserbase/stagehand) | act / extract / observe (TypeScript) | Three-verb browser API, cached actions that heal themselves | `agents/tools/browser_tab_tools.py` | Port | MIT | 1 |
| [Graphiti](https://github.com/getzep/graphiti) | Temporal knowledge graph (Python) | Episodes, entities, facts with valid-from/valid-to, hybrid search | `agents/memory` | Port onto SQLite | Apache-2.0 | 2 |
| [Langfuse](https://github.com/langfuse/langfuse) | LLM tracing and evals | Trace / span / generation model, cost per job, scores, datasets | `ai/speed.py`, new `agents/telemetry` | Port the data model | MIT (non-ee) | 2 |
| [Composio](https://github.com/ComposioHQ/composio) | Managed app integrations | Toolkit catalog, auth configs, connected accounts, triggers | `integrations/` | Use via MCP, port the shape | MIT | 2 |
| [SwarmClaw](https://github.com/swarmclawai/swarmclaw) | Self-hosted multi-agent runtime (TypeScript) | Swarms, delegation, plugin hooks, reflection memory | `agents/manager`, new `agents/plugins` | Port | Check repo | 3 |
| [AutoGPT](https://github.com/Significant-Gravitas/AutoGPT) | Agent builder and block graphs | Blocks with typed inputs and outputs, a visual graph for projects | `agents/manager/graph.py`, `ui/agents_page.py` | Ideas only | PolyForm Shield (platform) | 3 |
| [Steel](https://github.com/steel-dev/steel-browser) | Browser sessions API | Session objects, saved cookies and storage, live view, recording | `integrations/browser_bridge.py` | Port | Apache-2.0 | 3 |
| [BeatBrowser](https://github.com/BeatAPI/BeatBrowser) | Agent control of your signed-in Chrome | Background TaskSpaces, takeover, pause, task recovery after restarts | `connectors/browser/` | Port | MIT | 2 |
| [OpenMuse](https://github.com/CopilotKit/openmuse) | Personal agent on AG-UI (TypeScript) | Streamed live task progress, pause/resume, human review steps | `agents/ui`, `ui/agents_page.py` | Port | MIT | 3 |

Phase 1 is the engine. Phase 2 is memory, insight and integrations. Phase 3 is the surface and scale.

## Orchestration: durable jobs, swarms, block graphs

Jobs must survive sleep, a crash or a restart, and carry on from the last finished step. Today a job lives on the runtime loop and is lost if HeyTora closes mid-run.

### From trigger.dev → `agents/manager`

trigger.dev's v4 engine adds a primitive called a [waitpoint](https://trigger.dev/blog/v4-beta-launch): something that blocks a run until a condition is met. One waitpoint can block many runs, and one run can be blocked by many waitpoints. Its [wait tokens](https://trigger.dev/docs/wait-for-token) take a timeout, tags and an idempotency key, and end as completed or timed out.

Build these into HeyTora:

1. **Checkpoint every step.** After each step, `orchestrator.py` writes the step's result and the plan cursor to `agents.db` before the next step starts. On boot, `AgentManager` finds RUNNING jobs and puts them back in the queue. The new state is RESUMABLE, and each job resumes from its cursor.
2. **Never replay an uncertain action.** Each tool declares `replay_safe: bool` on its spec. If a crash interrupted a step with a tool that is not replay-safe, the job resumes paused and Tora asks. Examples are `type_text`, any send, and any MCP write.
3. **Waitpoints replace the single WAITING_APPROVAL state.** Add a `Waitpoint` dataclass to `agents/core/models.py` with these fields: `id`, `kind`, `job_ids`, `timeout`, `idempotency_key`, `status` (WAITING, COMPLETED or TIMED_OUT) and `output`. It has four kinds:
   - `approval`: today's dialog.
   - `answer`: a clarify question.
   - `time`: wait until 9:00, or for two hours.
   - `event`: a file appears, an email from Lee arrives, or a build finishes.
4. **Retries per tool, before a replan.** Each tool gets a `RetryPolicy`: attempts, backoff and which error codes count as transient. `max_replans` is only used after the retries run out.
5. **Named queues with concurrency limits.** This extends today's claims. The `screen` queue has limit 1, `browser` has 1 per profile, `network` has 4 and `cpu` has 2. A job waits in its queue rather than failing a claim.
6. **Idempotency keys on `submit_task`.** The same key within its TTL returns the existing job. `followups.py` becomes one user of this.
7. **Schedules with a time zone and a missed-run rule.** A skill's `every:` gains `tz:` (default Africa/Johannesburg) and `missed: skip | run_once`. The second option matters for when the PC was asleep.

### From SwarmClaw → `agents/manager` and a new `agents/plugins`

[SwarmClaw](https://docsearch.algolia.com/mcp/docs/repo/swarmclawai/swarmclaw) is a self-hosted multi-agent runtime. It has durable memory, MCP tools, delegation and scheduling. Its plugin registry, [SwarmForge](https://github.com/swarmclawai/swarmforge), shows the hook pattern worth copying. Examples are a shell gate that blocks dangerous commands, a token limiter, and a filter that redacts keys and personal data.

- **Delegation between agents.** A worker can call `delegate(spec, goal)` to start a child job and wait on it through a waitpoint. Projects in `graph.py` become one way to declare this ahead of time.
- **Plugin hooks.** These are Python modules in `%LOCALAPPDATA%\HeyTora\plugins` with `before_tool_call`, `after_tool_call`, `after_step` and `after_job` hooks. They run inside `Registry.execute()`, so they can veto a call but can never skip approval. HeyTora ships three: `shell_gate`, `token_limiter` and `redact_output`.
- **Reflection notes with near-duplicate skip.** After each job, write one short lesson to memory. Skip it if a very similar note already exists. This links to the Graphiti section.
- **Workspace handoff file.** Each job's workspace gets a `context.json` with its goal, inputs and results so far. A child job or a resumed job reads it first.

### From AutoGPT → `agents/manager/graph.py` (ideas only)

AutoGPT's platform builds agents from **blocks**. A block is a node with typed inputs and outputs, wired into a graph and run as an execution. Its code is under PolyForm Shield, so take only the idea:

- Give each `TaskNode` an `inputs` and `outputs` schema. The orchestrator checks that a node's output fits the next node's input before it moves on.
- Let a finished project be saved as a reusable **agent** with a name and inputs, for example "weekly invoice run (month)". Saved agents appear on the Agents page.
- A visual graph editor on the Agents page comes later, after the Clicky-style design arrives.

## Memory: a time-aware graph (from Graphiti)

Tora's memory should store facts that know when they became true and when they stopped being true. Today `agents/memory/store.py` keeps short tagged notes, so a stale note ("Lee's email is old@...") wins as often as a fresh one.

[Graphiti](https://pypi.org/project/graphiti-core/) builds a knowledge graph as new data arrives, with no batch rebuild. It uses a [bi-temporal model](https://pypi.org/project/graphiti-core/), tracking both when an event happened and when it was learned. It searches with embeddings, BM25 keywords and graph traversal together. When a new fact contradicts an old one, it ends the old fact rather than deleting it.

Graphiti needs Neo4j, FalkorDB or another graph server, and HeyTora cannot ship one. So rebuild the model on SQLite, which HeyTora already uses (`agent_memory.db`).

| Table | Holds | Key columns |
| --- | --- | --- |
| `episodes` | Raw source: a voice turn, a finished job, an email Tora read | `id`, `source`, `content`, `occurred_at`, `ingested_at` |
| `entities` | People, files, apps, projects, places | `id`, `name`, `kind`, `summary`, `embedding` |
| `facts` | One relationship between two entities | `id`, `subject_id`, `predicate`, `object_id`, `text`, `valid_at`, `invalid_at`, `episode_id` |
| `facts_fts` | FTS5 index on fact text, for the keyword half of search | `text` |

**How it runs:**

1. After each turn or job, an episode is written. Extraction runs in the background on the agents' loop with a cheap model. It pulls entities and facts as typed dataclasses: Person, File, Project, Preference.
2. Before a new fact is saved, it is compared with live facts that have the same subject and predicate. If they contradict, the old fact gets `invalid_at = now` and stays as history.
3. `recall(query)` runs FTS5 keyword search and embedding similarity together. It then walks one hop from the top entities and ranks results by graph distance to what is on screen now. `ai/references.py` uses the same call to resolve "Lee" or "the invoice".
4. Point-in-time questions such as "what was the plan last week" filter on `valid_at <= t < invalid_at`.
5. Keep group ids: `user:`, `project:<workspace>` and `job:<id>`. Today's project-shared memory maps straight onto them.

**Privacy carries over unchanged.** Nothing from a sensitive window becomes an episode. Passwords, codes and card numbers are never extracted. A "forget X" request removes the facts and their episodes, not just marks them invalid.

You can copy Graphiti's extraction prompts and dedupe logic with the Apache-2.0 notice. Do not add `graphiti-core` as a dependency.

## Browser: four repos, one browser stack

The browser worker should read a page as a numbered list of controls and act in a few verbs. It should replay actions it has learned without a model call, and own its own tabs inside the user's Chrome. Every part still goes through the existing extension and `browser_bridge.py` (127.0.0.1:47124) and through `Registry.execute()`.

### From browser-use → `agents/workers/browser.py`

1. **Indexed page state.** The extension returns only the visible, interactive elements as lines such as `[12] button "Send"` or `[13] textbox "To" = "lee@…"`. Elements new since the last step are marked with `*`. The model answers with an index, never a CSS selector. This fits HeyTora's rule of data first and pixels last. A screenshot is added only when the list is ambiguous.
2. **Several actions per step.** The model may return up to 5 actions. The batch stops early if the page navigates or its element list changes.
3. **Loop detection.** The same action on the same page hash 3 times in a row triggers a nudge to the model, then a replan.
4. **Placeholders for personal data.** The model sees `{{user.address}}`, and the tool fills in the real value at the last moment. This keeps details out of prompts and traces. It does not change the rule that password, code and card boxes are never filled.

### From Stagehand → `agents/tools/browser_tab_tools.py`

Add three tools on top of the low-level ones:

| Tool | Does | Risk |
| --- | --- | --- |
| `browser_observe(instruction)` | Returns candidate actions (element, method, arguments) without doing them | SAFE |
| `browser_act(instruction)` | Does one action described in plain words | MEDIUM, HIGH if it sends or submits |
| `browser_extract(instruction, schema)` | Returns data shaped to a schema (a dataclass) | SAFE |

`observe` also feeds `point_at`. Tora can mark the button she is about to press before the approval dialog shows.

**Self-healing action cache.** Store each resolved action in `browser_actions.db`, keyed by (site, instruction, page-structure hash). Next time, replay it with no model call. If the element is gone, fall back to the model and overwrite the entry. This makes repeat jobs, such as a weekly invoice download, fast and cheap.

### From BeatBrowser → `connectors/browser/`

BeatBrowser is the closest match to HeyTora's own browser companion. It runs agents in the user's signed-in Chrome with [background TaskSpaces](https://github.com/BeatAPI/BeatBrowser/releases/tag/v1.3.1): named pages, progress, takeover, pause, resume and stop. Its tasks recover after an extension reload or browser restart. Unfinished tasks come back paused, and uncertain input is not replayed.

- **TaskSpaces.** A job owns the tabs it opened, which are recorded in extension storage with the job id. It never touches tabs the user opened unless the goal names them. Cleanup closes only job-owned tabs.
- **Takeover button.** This goes on the job card. The user can take a tab back mid-job, and the job pauses until they hand it back.
- **Recovery.** On reconnect, the bridge lists unfinished TaskSpaces. Their jobs resume as RESUMABLE (see Orchestration). A missing tab is reattached only on the user's yes.
- **Doctor check.** Add a `browser_doctor()` health check to `health.py`. It reports whether the bridge is up, whether the extension is online, and whether the version matches.

### From Steel → `integrations/browser_bridge.py`

Steel treats a browser **session** as an object with an id. That session saves cookies and storage, has a live view and records every step. Take the object model, not the server:

- A `BrowserSession` record holds `id`, `job_id`, `tabs`, `started_at` and a step log. Each step stores its action, the URL and a small screenshot, all in `agents.db`. The Agents page timeline replays it.
- An optional **isolated agent browser** is for jobs that should not use the user's profile. This needs a headless Chromium driver, which is a new dependency, so ask before adding it.

## Coding agent: the event stream and safety nets (from OpenHands)

OpenHands' core idea should become HeyTora's worker contract. A worker emits **actions**, the runtime returns **observations**, and both are appended to one event stream that is the job's whole history. This also completes DevLink's DL-06 to DL-08 milestones.

**1. Actions and observations in `agents/core/events.py`.** Every tool call becomes an `Action` event: run command, edit file, browse, message the user, finish. Every result becomes an `Observation`: output, diff, error, page state. The worker's next prompt is built from the stream. `agents.db` stores the stream as-is, so a job can be replayed, audited or resumed exactly.

**2. Stuck detector in `agents/workers/base.py`.** Stop or replan when any of these happens:

- The same action and observation repeat 3 times.
- The same error repeats 3 times.
- The worker talks to itself with no tool call 3 times.
- Two actions alternate back and forth for 6 steps.

This is cheaper than hitting the token budget in `limits.py`.

**3. Context condenser.** When a job's stream passes about 60% of the model's context, older events are summarised into one condensation event. The goal, the last 10 events and every unresolved error are kept word for word. Long jobs then stop failing on context size.

**4. Microagents become skill triggers.** OpenHands loads short knowledge files when a keyword appears, such as `git` or `pytest`. HeyTora's skills already have a `when:` field. Add `triggers: [keywords]` so a skill is injected into the worker's prompt the moment the goal or an observation mentions one. An example is a `delphi` skill for the StyleHub PAT build.

**5. Confirmation mode and a risk check on commands.** Before a terminal command runs, a small rule-based analyser rates it. Commands that delete, write outside the workspace, run `curl | sh`, push or change the registry are rated HIGH. HIGH commands go to approval regardless of `max_auto_risk`. This sits in `terminal_tools.py` and matches SwarmClaw's shell gate.

**6. DevLink execution loop (DL-06 to DL-08).**

1. The coding worker writes changes as a diff, never straight to disk.
2. Tora shows the diff in VS Code through DevLink, and the user approves it hunk by hunk.
3. On approval it applies the diff and runs the project's tests (DL-07, with approval).
4. It reads the failures as observations and loops, capped at 5 rounds. Then it reports the result: green, or the failures that remain.

OpenHands runs code in a Docker sandbox. HeyTora does not need one, because workspace roots and approvals are its sandbox. Add an optional Windows Sandbox or WSL runtime later.

## Integrations: hundreds of apps through Composio

Composio is already in HeyTora's MCP catalog as a "Link" connection. The work now is to treat it as the fallback for any app HeyTora has no native connector for, and to copy its three-part model into `integrations/`.

**Composio's model, rebuilt in `integrations/connections.py`:**

| Concept | Meaning | In HeyTora |
| --- | --- | --- |
| Toolkit | One app's set of tools (Gmail, Slack, HubSpot) | A catalog entry in `catalog.py` |
| Auth config | How that app signs in (OAuth, key, none) and which scopes | `auth` block in `mcp_servers.json`, secret in the vault |
| Connected account | One user's live sign-in to that toolkit | A row in `connections.json`, with status ACTIVE, EXPIRED or REVOKED |
| Trigger | An event from the app (new email, new deal, new Slack message) | A waitpoint of kind `event` (see Orchestration) |

**What to build:**

1. **Catalog fallback.** Suppose the user says "connect HubSpot" and HubSpot has no native entry. `catalog.py` then offers it through the user's Composio link. It still follows CI-10's rule of one offer per session.
2. **Load only the tools a job needs.** Composio exposes thousands of tools, and loading them all would flood the planner. Use a tool router: the planner first searches tool names and descriptions for the goal, then loads only the 5 to 15 tools that match. Apply the same router to every large MCP server.
3. **Triggers as job starters.** A skill can say `on: composio.slack.new_message from:#sales`. The event completes a waitpoint, or starts the skill's job. This needs a webhook receiver, so route triggers through the HeyTora server (Supabase) rather than opening a port on the PC.
4. **Health per connected account.** `list_connections` shows when each sign-in expires. Tora offers to reconnect before a scheduled job would fail.

**Unchanged:** a Composio tool marked read-only is MEDIUM, and any other is HIGH and always asks. Everything it returns reaches a model as data, never as instructions.

## Observability: traces, costs and scores (from Langfuse)

Every voice turn and every job should leave one trace. The trace shows each model call, tool call and wait, with its time, tokens and rand cost. The existing debug guide says job failures are only in `agents.db` and `tora.log` is silent about most of them. A trace closes that gap.

**Langfuse's data model, stored locally in a new `traces.db`:**

| Record | One per | Holds |
| --- | --- | --- |
| Trace | Voice turn or job | `id`, `kind`, `correlation_id`, user goal, outcome, total ms, total tokens, total cost |
| Span | Step, tool call, waitpoint, UIA read | parent id, name, start, end, input/output (truncated, redacted), status |
| Generation | Model call | model, prompt tokens, output tokens, cost, latency, time to first audio for voice |
| Score | A judgement on a trace | name (`verified`, `user_thumbs`, `stuck`), value, source (verifier, user, rule) |
| Session | One conversation span of Gemini Live | `session_id`, its traces |

**Where it hooks in:**

1. **The EventBus.** The tracer is just another subscriber, so no worker code changes. `agents/manager` already has correlation ids, which become trace ids.
2. **`ai/speed.py`.** Its timing line per question becomes a trace with spans for hearing, model, tool and first audio. The daily scorecard is a query over `traces.db`.
3. **The verifier.** `RuleVerifier` and `ProviderVerifier` results are written as scores, so a failed job shows exactly which check it failed.
4. **Cost.** A price table per model, in USD, converted to rands at a configured rate. It enforces the "no paid calls without an estimate" rule. Each job card shows its running cost, and `limits.py` can cap jobs in rand as well as tokens.
5. **Datasets for evals.** Mark a trace as a test case from the Agents page, such as "this pointing went wrong". `tools/look_eval.py` and `voice_eval.py` read those cases as their datasets. Regressions then show up before a release.

**Redaction before writing.** The same redactor as `diagnostics.py` and the `redact_output` plugin runs before anything is written. Nothing from sensitive windows is traced. Traces stay on the PC unless the user exports them. A self-hosted Langfuse server is optional for the developer, never for end users.

## Live agent UI and the phone (from OpenMuse)

HeyTora should stream every job's progress as typed events that any screen can draw. That includes the job cards, the Agents page and, later, a phone. Humans should be able to pause, resume, cancel and review a job from any of those screens.

[OpenMuse](https://futuretools.io/tools/openmuse-2091d600) is CopilotKit's MIT-licensed personal agent. It runs a persistent browser, an optional terminal and file management, and streams live task progress to iOS, Android and web. Users can pause, resume, cancel and add human review steps. It is built on the AG-UI protocol.

**What to take:**

1. **One event vocabulary for the UI.** Map EventBus events onto AG-UI's event types: run started and finished, step started and finished, text message chunks, tool call start, arguments and end, state snapshot and delta. Do this in `agents/ui/stream.py`. The orbs and the Agents page then render from that one stream instead of from ad-hoc signals.
2. **Human review steps.** A `TaskNode` can be marked `review: true`. The job then pauses at a waitpoint and shows its output for the user to approve, edit or reject before the next node runs. This is the same waitpoint as an approval, with editable output.
3. **Phone companion (later).** The HeyTora server relays the same AG-UI stream to a phone over the user's account. The phone can watch a job, answer a clarify question, approve or deny, and stop all jobs. Approvals from the phone still show exactly what will be sent, paid or deleted. This waits on the server deploy that is already pending.

Don't copy OpenMuse's UI. The cursor and window design stay on hold until the Clicky-style design arrives (PT-12, WX-02, WX-03, WX-13, WX-15).

## New milestones and order of work

The plan has 18 new milestones in three gated phases, and each phase must pass its gate before the next starts. Finish OR-02 (typed intent) first: commit it and run the full suite, because waitpoints and the event stream build on it. The IDs below are proposed. Renumber them to fit the Massive Direction Plan.

Roadmap (the original doc draws this as a diagram):

- **Phase 1, the engine:** OR-30 to OR-33, EN-30 to EN-32, BR-01, BR-02. Gate: a killed job resumes, a looping job is stopped, the suite is green.
- **Phase 2, memory, insight, integrations:** BR-03, MEM-01, MEM-02, OB-01, OB-02, CI-30. Gate: recall prefers the current fact, every job has a trace with its cost, one Composio app connects end to end.
- **Phase 3, surface and scale:** OR-34, OR-35, BR-04. Starts when the owner's design arrives and the server is deployed.

Phase 1 rebuilds the engine. Memory, tracing and integrations follow only once jobs survive restarts. The surface waits for the owner's design.

| ID | Milestone | From | Phase |
| --- | --- | --- | --- |
| OR-30 | Step checkpoints and resume after restart (RESUMABLE) | trigger.dev | 1 |
| OR-31 | `replay_safe` on every tool; uncertain steps resume paused | trigger.dev, BeatBrowser | 1 |
| OR-32 | Waitpoints: approval, answer, time, event | trigger.dev | 1 |
| OR-33 | Retry policy per tool; named queues with limits | trigger.dev | 1 |
| EN-30 | Action/observation event stream in `agents/core` | OpenHands | 1 |
| EN-31 | Stuck detector and context condenser | OpenHands | 1 |
| EN-32 | Command risk check; plugin hooks in `Registry.execute()` | OpenHands, SwarmClaw | 1 |
| BR-01 | Indexed page state, multi-action steps, loop detection | browser-use | 1 |
| BR-02 | `browser_observe` / `act` / `extract` with action cache | Stagehand | 1 |
| BR-03 | TaskSpaces, takeover, browser recovery, doctor check | BeatBrowser | 2 |
| MEM-01 | Temporal facts on SQLite, contradiction handling | Graphiti | 2 |
| MEM-02 | Hybrid recall (FTS5 + embeddings + one hop) | Graphiti | 2 |
| OB-01 | Local traces, generations, scores, rand cost | Langfuse | 2 |
| OB-02 | Eval datasets from flagged traces | Langfuse | 2 |
| CI-30 | Composio fallback, tool router, account health | Composio | 2 |
| OR-34 | Delegation, typed node inputs/outputs, saved agents | SwarmClaw, AutoGPT | 3 |
| OR-35 | AG-UI event stream, human review steps, phone relay | OpenMuse | 3 |
| BR-04 | Browser session log and replay; optional isolated browser | Steel | 3 |

**Gates:**

- **Phase 1 passes** when a job killed mid-run resumes correctly after a restart, a looping job is stopped by the stuck detector, and the full suite is green.
- **Phase 2 passes** when recall returns the current fact over a stale one in tests, every job shows a trace with its rand cost, and one Composio app connects end to end.
- **Phase 3 starts** when the owner's design arrives and the server is deployed.

**Licensing cautions:**

- AutoGPT's `autogpt_platform` folder is [PolyForm Shield](https://github.com/Significant-Gravitas/AutoGPT/blob/master/LICENSE), which has a noncompete clause. HeyTora competes in the same space, so copy nothing from it.
- Langfuse and OpenHands keep enterprise folders (`ee/`, `enterprise/`) under separate licences. Port only from their MIT parts.
- Confirm SwarmClaw's licence file before copying any code. It is TypeScript, so porting is the plan anyway.
- Every copied file keeps its original header and gets a line in `THIRD_PARTY_NOTICES.md`.

## Sources

As of 27 Oct 2026. Repos: [SwarmClaw](https://github.com/swarmclawai/swarmclaw) · [trigger.dev](https://github.com/triggerdotdev/trigger.dev) · [Graphiti](https://github.com/getzep/graphiti) · [Steel](https://github.com/steel-dev/steel-browser) · [Langfuse](https://github.com/langfuse/langfuse) · [AutoGPT](https://github.com/Significant-Gravitas/AutoGPT) · [Composio](https://github.com/ComposioHQ/composio) · [OpenMuse](https://github.com/CopilotKit/openmuse) · [browser-use](https://github.com/browser-use/browser-use) · [OpenHands](https://github.com/OpenHands/OpenHands) · [Stagehand](https://github.com/browserbase/stagehand) · [BeatBrowser](https://github.com/BeatAPI/BeatBrowser)

Pages used:

- [trigger.dev v4 waitpoints](https://trigger.dev/blog/v4-beta-launch)
- [trigger.dev wait tokens](https://trigger.dev/docs/wait-for-token)
- [Graphiti on PyPI](https://pypi.org/project/graphiti-core/)
- [SwarmClaw docs index](https://docsearch.algolia.com/mcp/docs/repo/swarmclawai/swarmclaw)
- [SwarmForge plugins](https://github.com/swarmclawai/swarmforge)
- [BeatBrowser 1.3.1 release](https://github.com/BeatAPI/BeatBrowser/releases/tag/v1.3.1)
- [OpenMuse overview](https://futuretools.io/tools/openmuse-2091d600)
- [AutoGPT licence](https://github.com/Significant-Gravitas/AutoGPT/blob/master/LICENSE)

Details for browser-use, Stagehand, Steel, Langfuse, Composio and OpenHands are from their public docs. Check them against each repo's current code before porting.

---

## Review against the code (27 Oct 2026)

Read before building any milestone above. A good part of Phase 1 already exists under Massive Direction Plan ids; each new milestone is the **delta** on top of it, not a rebuild.

**Already built - extend, don't redo:**

| Plan item | What already exists | Where | The delta still to build |
| --- | --- | --- | --- |
| OR-30 checkpoints and resume | Every step, its tool calls and results are saved to `agents.db` as they happen; TOOL_CALL is committed before a tool runs (EN-06). On boot `AgentManager.recover()` brings unfinished jobs back PAUSED with a Resume button; `_resume_recovered` continues from the step cursor | `agents/manager/agent_manager.py` `recover`, `_resume_recovered` | Resume on its own (RESUMABLE) when the interrupted step was replay-safe - once per job, so a job that crashed HeyTora can't loop it (the reason `recover` pauses today) |
| OR-31 `replay_safe` | A call that never answered is judged by risk: MEDIUM or HIGH means "may have run", and the job comes back NEEDS_ATTENTION (EN-07) | `_unsettled_effect` | An explicit `replay_safe` on `ToolSpec`, used instead of the risk guess (reading a file is MEDIUM-safe to rerun; `type_text` is not) |
| OR-33 retries and queues | Retry classes - transient, unknown effect, bad input, no permission, blocked, mismatch (OR-14), RETRY_WAIT; claims give the screen and the browser to one job at a time and one writer per folder (OR-10); leases (OR-11) | `agents/manager` | A `RetryPolicy` per tool; named queues with limits (`network`, `cpu`) beside the claims |
| OR-34 typed node inputs | Project nodes already pass their results to the nodes after them, with `done_when` and `on_fail` (OR-08) | `agents/manager/graph.py` | Input/output schemas, saved agents |
| EN-30 event stream | TOOL_CALL / TOOL_RESULT / STEP_* events are already one ordered stream per job in `agents.db` (`store.events`) | `agents/core/events.py`, `agents/persistence` | Typed `Action` / `Observation` records over it, and worker prompts built from the stream |
| MEM-01 temporal facts | Memory rows have provenance, dates, scope, expiry, confidence (EN-13); forget removes indexes too (EN-14); contradictions are shown, never overwritten (EN-15) | `agents/memory` | Entities and facts with `valid_at` / `invalid_at`, FTS5 |
| Correlation ids, plan vs actual | EN-18, OR-09 | `agents/manager/correlation.py` | Trace ids reuse them (OB-01) |
| Composio | In the catalog as a "link" connection, with the MCP tool router by server name | `integrations/catalog.py`, `agents/tools/mcp_tools.py` | Fallback for unknown apps; a tool router by *tool* description inside a big server |

**Corrections:**

1. **Time zone.** Default `tz:` to Windows' own time zone, not a fixed Africa/Johannesburg: users live in other zones, and a PC's zone can change. Likewise make the cost currency a setting (rand by default for the owner).
2. **Embeddings cost money.** MEM-02's embedding half is a model call per fact and per recall. Build FTS5 plus the one-hop walk first; embeddings behind a switch, after a cost estimate and a yes (the existing rule).
3. **The isolated agent browser needs no new dependency.** `playwright` is already in `requirements.txt` (Chromium downloaded on first use; Edge headless is broken, so Chromium it is).
4. **Composio triggers need the server.** Webhooks route through the HeyTora server, which is not deployed yet, so CI-30's trigger part is blocked like the phone relay. The fallback, tool router and account health can go ahead.
5. **BR-01 to BR-03 change the extension.** They need `connectors/browser` changes and a reload of the extension by the user; ship them before the Chrome Web Store submission, not after.
6. **Composio privacy.** Data for apps reached through Composio passes through Composio's cloud. Say so once when a Composio app is first used.

**Build order (adjusted):** OR-02 (finish) → OR-31 → OR-30 → OR-32 → EN-30 → EN-31 → OR-33 → EN-32 → BR-01 → BR-02, then the Phase 1 gate. `replay_safe` comes before automatic resume, because resume must know what is safe to run again.
