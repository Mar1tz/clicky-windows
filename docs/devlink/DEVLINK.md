# HeyTora DevLink: built (phases 1 and 2)

The design is *HeyTora DevLink — Deep App Integration Design* (23 Oct 2026, the PDF in the repo
root). It gives Tora structured access to developer tools, starting with VS Code, instead of
screenshots. Its rollout plan ships in four gated phases. This is **phase 1 (foundations)** and
**phase 2 (VS Code, read-only)**. Phase 3 (edits, running tasks, workflows) starts only when its gate
is met: context accuracy and security proven in use.

## What is where

| Design | Code |
| --- | --- |
| VS Code connector | `connectors/vscode/extension.js`: plain JS, no dependencies, no build step |
| Connector gateway | `devlink/gateway.py`: 127.0.0.1, token, one JSON-RPC message per line |
| Shared protocol (resources, events, actions, risk levels) | `devlink/protocol.py` |
| Policy Engine: permissions, pause, data policy | `devlink/policy.py` |
| Redaction, secret files, `.heytoraignore` | `devlink/redact.py` |
| Context Engine: hot state, project, packet | `devlink/context.py` |
| Log Hub: ring buffers, FTS5, event extraction | `devlink/logs.py` + `devlink/store.py` |
| Session timeline, audit log | `devlink/store.py` (SQLite in the data folder, `devlink.db`) |
| The local agent | `devlink/hub.py`: runs on HeyTora's asyncio loop |
| Query tools for the AI | `devlink/tools.py`: the voice's `code_context`; `agents/tools/devlink_tools.py` |
| Permissions screen, audit viewer | Settings → Code editors (`ui/settings_tabs.py: CodeEditorsTab`) |
| Status-bar item with one-click pause | in the extension |
| Installing the connector | `devlink/install.py`: builds the `.vsix` with zipfile, installs with the editor's CLI |

## How it works

1. HeyTora starts the hub (switch `devlink`, on by default). The hub listens on 127.0.0.1 (port 47123,
   or any free one) and writes `devlink.json` (port and per-install token) to the data folder.
2. Settings → Code editors → **Connect** installs the extension into VS Code, Cursor, Windsurf,
   VSCodium or Trae (any VS Code-family editor found under Programs or Program Files).
3. The extension reads `devlink.json`, connects, and says `hello` with the token and the workspace's
   folders. A workspace with nothing granted asks once in VS Code: **Allow**, **Choose…** or
   **Not now**.
4. Granted, it streams events: the active file and cursor, the selection, tabs, unsaved files,
   problems, git, terminal commands with their output and exit codes, tasks, debug output and stops.
   The hub redacts and stores them, extracts failures, and keeps the timeline.
5. Tora's voice calls `code_context` (its rule: first, for any question about code, builds, tests or
   terminals). Agents call `devlink_context`, `devlink_logs` and `devlink_code`. Each answer is
   gated, built from local state plus what only the editor has (the text round the cursor,
   references), redacted, marked as untrusted data, and written to the audit log.

## Permissions

| Capability | Covers |
| --- | --- |
| `read_code` | Files (unsaved edits included), cursor and selection, tabs, problems, symbols, references, git, code search |
| `read_terminals` | Terminal commands, output and exit codes; tasks; the log tools |
| `read_debug` | Debug sessions, their output, where they stopped |

- **Scopes:** `once` (one answer), `session` (until HeyTora restarts) and `always` (saved).
- **Off until granted:** every capability, per workspace. A workspace is the same set of folders,
  in any window.
- **Pause** stops everything and keeps the grants. **Local only** keeps reading and sends the model
  nothing.
- **Phase 3 capabilities** (`edit_files`, `run_tasks`, `run_commands`, `monitor`) are named but
  refused.
- **Never read, whatever is granted:**
  - `.env*`, keys and certificates;
  - `.ssh`, `.aws`, `.azure`, `.kube`, `.gnupg`;
  - credential files;
  - anything matched by a workspace's `.heytoraignore`.
- **Redaction** runs on every line before it is stored, and on every packet before it is sent:
  - known token shapes, such as Google, OpenAI, Anthropic, GitHub, Slack, Stripe, AWS, Supabase and
    JWTs;
  - private key blocks;
  - passwords in URLs;
  - `password=`-style assignments;
  - any long random-looking value.

## Protocol (version 1)

- **Transport:** TCP on 127.0.0.1, one JSON-RPC 2.0 message per line, at most 1 MB per message.
- **Handshake:** the first message must be `hello` with the token. A wrong token is refused. Input
  that looks like HTTP is closed without a reply.
- **Lines** count from 0 everywhere, as VS Code does. HeyTora adds one when it shows them.

| Direction | Messages |
| --- | --- |
| Connector → hub, notifications | `events {events: [{type, payload, ts}]}`. Types are listed in `protocol.EVENTS`; an unknown type is dropped. |
| Connector → hub, requests | `hello`, `policy.grant {capabilities: {cap: scope or null}}`, `policy.pause {paused}`, `status` |
| Hub → connector, requests | `editor.window`, `file.read`, `symbols.workspace`, `symbols.document`, `definition`, `references`, `diagnostics.all` |
| Hub → connector, notifications | `policy.changed {grants, paused}` |

## Where this differs from the design, and why

- **Transport:** plain TCP lines instead of a WebSocket or named pipe. The messages are the same,
  and nothing needs installing on either side. Every fork's Node has `net`; not every fork's has
  `WebSocket`. The security holds without a WebSocket origin check: 127.0.0.1 only, a token, and
  HTTP refused.
- **Token:** read from the data folder's `devlink.json` on each connect, not kept in VS Code's
  SecretStorage. Only this Windows user can read either place.
- **The model's toolset:** Tora's voice and agents call the hub directly, not through an MCP server.
  An MCP server for other clients (Claude Code, Cursor's chat) is a small layer over `devlink/tools.py`
  and fits phase 4.
- **Remote workspaces:** the extension prefers the UI side (`extensionKind: ["ui", "workspace"]`), so
  SSH, WSL and Dev Containers still reach the local HeyTora. Git state and code search then need a
  local folder.

## Not built yet

| Item | Why not now |
| --- | --- |
| Embeddings and semantic search (`sqlite-vec` or LanceDB) | Needs a new dependency and the design's open question settled (local or gateway embeddings). Code search is lexical. |
| Tree-sitter, symbol index | Symbols come from VS Code's own providers, which already parse. |
| Task output | VS Code does not open it to extensions. The design's pseudo-terminal wrapper is phase 3, with running tasks. Tasks report start, end and exit code; their problem-matcher hits arrive as problems. |
| Extension log-file tailing (`logUri`), Docker, shell hooks, browser DevTools, JetBrains | Phase 4. |
| Encryption at rest (SQLCipher / AES-GCM with DPAPI) | `devlink.db` sits in the user's data folder. Its logs are redacted and purged after 7 days. Encrypting full-text-searched text needs a new dependency. |
| `watch(condition)` and background monitoring | Phase 3 ("opt-in background monitoring"). |
| Team/admin policies, the audit export for admins | Phase 4. |

## Trying it

```
python -m pytest tests/agents/test_e2e_devlink.py -q   # 83 tests; the last two run the real extension under Node
```

To try it with a real editor:

1. Start HeyTora.
2. Go to Settings → Code editors → Connect.
3. Open a project in that editor and choose **Allow**.
4. Ask Tora "what's wrong in my code?"

Settings → Code editors → **What HeyTora sent** shows exactly what went to the model.
