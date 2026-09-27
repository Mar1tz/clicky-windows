# CLAUDE.md — HeyTora (repo folder: clicky-windows-main)

Windows desktop AI companion: PyQt6 UI, asyncio on daemon threads, Gemini
Live for voice/vision, `companion_manager.py` as the hub. We are adding an
autonomous agent system under `agents/` — see `docs/agents/ARCHITECTURE.md`.
Industry packs (Accounting first) plug in under `extensions/` — see
`docs/agents/EXTENSIONS.md`; no industry knowledge goes into `agents/`.

## Multi-session development protocol

Four Claude Code sessions work in **this same folder, on `main`**. The
manager session (`clicky-windows-main-0f`) plans, assigns, reviews and
pushes. Three worker sessions build. Conflicts are prevented by
**directory ownership**, not by branches — so the rules below matter.

### Ownership map

| you are   | you own (may create/edit)                                                    |
|-----------|-------------------------------------------------------------------------------|
| manager   | `agents/core`, `agents/manager`, `agents/extensions`, `companion_manager.py`, `main.py`, `config.py`, `ui/*`, docs, `CLAUDE.md`, `TASKS.md`, `tests/agents/test_core*.py`, `tests/agents/test_e2e*.py` |
| worker 1  | `agents/tools`, `agents/ui`, `tests/agents/test_tools_*.py`, `tests/agents/test_ui_*.py` |
| worker 2  | `agents/providers`, `agents/workers`, `agents/understanding`, `tests/agents/test_providers_*.py`, `test_workers_*.py`, `test_understanding_*.py` |
| worker 3  | `agents/memory`, `agents/planning`, `agents/verification`, `agents/persistence`, `supabase/migrations/0002*`, `tests/agents/test_memory_*.py`, `test_planning_*.py`, `test_verification_*.py`, `test_persistence_*.py` |
| pack owner | `extensions/<pack>/` (one pack per session), `tests/agents/test_<pack>_*.py`; the framework in `agents/extensions` is the manager's |

Rules:
1. **Never edit a file outside your ownership.** If you need a change in
   `agents/core` (a new field, a new ABC method) — message the manager with
   the exact diff you want; do not apply it yourself.
2. **Never `git add -A`, `git add .`, or `git commit -a`.** Stage only your
   own paths: `git add agents/tools tests/agents/test_tools_*.py`. Another
   session may have half-written files in the tree.
3. **Never push.** The manager pushes. Never rebase, reset, stash or
   checkout — those rewrite the shared working tree under everyone.
4. Commit message prefix: `[w1]`, `[w2]`, `[w3]`, `[mgr]`. If `git commit`
   fails with `index.lock`, wait two seconds and retry.
5. Before reporting done: `python -m pytest tests/agents -q` is green
   (all tests, not only yours — if someone else's test breaks because of
   your change, say so in the report).
6. Report with `SendMessage` to the manager: commit hash, files touched,
   what works, what you skipped, questions. Keep it under 20 lines.
7. Your assignment is in `TASKS.md` under your id. If your context gets
   compacted, re-read `CLAUDE.md`, `docs/agents/ARCHITECTURE.md`,
   `agents/core/*.py` and your `TASKS.md` entry before continuing.

### Code conventions

* Python 3.14. Dataclasses + type hints. Module docstrings say *why*.
  Match `agents/core/models.py` in tone and density.
* Everything under `agents/` is `asyncio`; only `agents/ui` may import PyQt6.
* Import other packages' types only via `from agents.core import ...`.
* Tools return `ToolResult.failure()` for expected failures; never raise.
* No new third-party dependencies without asking (PyInstaller size,
  Py 3.14 wheels). Stdlib `sqlite3`, `subprocess`, `json` are fine.
* Windows first: `subprocess` with `creationflags=subprocess.CREATE_NO_WINDOW`,
  `encoding="utf-8", errors="replace"`, `shell=True` only for `npm`/`npx`.
* Tests use `tmp_path` and `:memory:`; never touch the user profile or the network.

### Running things

```
python main.py                      # the app
python -m pytest tests/agents -q    # agent tests
```

`.env` holds real keys and is git-ignored — never read it into a commit.
