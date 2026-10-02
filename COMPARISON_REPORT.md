# Project comparison report

Compared: C:\Users\torbe\Documents\clicky-windows-main (current) against C:\Users\torbe\Documents\new\clicky-windows-main (comparison snapshot).

Method: file-by-file SHA-256 comparison and line-diff analysis of shared modified files. Git metadata, build output, Python bytecode, pytest cache, and local .env files were excluded. The comparison directory is not a Git repository, so this is a snapshot comparison rather than a commit-history comparison.

## Executive conclusion

This is a major evolution, not a fork with a few feature changes. The current tree is a strict superset of the comparison tree: every comparison file remains present, while the current tree adds 358 meaningful files and substantially reworks 38 of the 79 shared files.

The original application is a compact voice-guided Windows assistant. The current application retains that core, then adds a durable multi-agent workforce, a tool and approval system, vertical business/education extensions, tests, documentation, richer visual guidance, and more deployment support.

## Measured change

| Measure | Comparison snapshot | Current tree | Difference |
| --- | ---: | ---: | ---: |
| Meaningful files | 79 | 437 | +358 (4.5x) |
| Python files | 57 | 342 | +285 (6.0x) |
| Python lines | 11,076 | 82,475 | +71,399 (7.4x) |
| Same relative paths | 79 | 79 | all retained |
| Byte-identical shared files | 41 | 41 | — |
| Modified shared files | 38 | 38 | 48% of shared files |
| Shared-file diff | — | 7,575 added / 2,643 removed lines | +4,932 net |

Most new behaviour lives in new modules rather than edits to existing ones.

## Where the new code lives

| Area added in current tree | Python files | Lines | What it adds |
| --- | ---: | ---: | --- |
| tests/ | 98 | 21,050 | Unit, integration and end-to-end coverage |
| agents/ | 85 | 17,912 | Agent runtime, planning, workers, tools, providers, controls and UI |
| extensions/ | 75 | 16,744 | Industry packs and their local stores/dashboards |
| ai/ | 9 | 4,467 | More model/location/provider plumbing |
| ui/ | 11 | 4,397 | App window, visual overlay chip/painting/preview and supporting UI |
| tutor_features/ | 3 | 1,314 | Guided workflow state and capture enhancements |
| audio/ | 3 | 491 | Additional speech/audio paths |

The largest edited existing files are companion_manager.py (+3,694/-421), config.py (+490/-208), ui/overlay.py (+605/-76), ai/hybrid_pointer.py (+567/-110), main.py (+239/-139), and tutor.py (+216/-4). The original coordinator, configuration boundary, pointing UX, and application startup were deliberately extended rather than replaced.

## Functional and architectural changes

### From one assistant to an agent platform

The current project introduces an agents subsystem with explicit domain models, orchestration, task graphs, scheduling, memory, SQLite persistence, verification, worker types, provider routing, and tools for files, terminal, browser, spreadsheets, apps and Windows controls. main.py and companion_manager.py integrate it into the live application, including task control and approvals.

This is the central architectural shift. The application is no longer only an interactive tutor; it can plan and execute multi-step work with durable state and a dedicated interface.

### New extension/product layer

The current tree has fourteen extension folders: accounting, creator, developer, freelancer, household, lettings, payroll, podcast, sales, store, students, tax practice, teachers and trades. They share extensions/packkit.py and expose per-domain dashboards, workflows and local SQLite-backed records.

This materially widens the scope from a general desktop tutor to a platform with vertical workflows. It also introduces stateful local data that needs backup, migration and retention decisions.

### Richer visual guidance and desktop control

The existing overlay, pointer and guide flow are heavily expanded. New overlay painting, chip placement and preview modules support a redesigned on-screen guide, while the existing pointer gains UI Automation/OCR/figure-detection paths. The core interaction model remains recognisable, but it now has more precise target location and clearer step feedback.

### Provider and audio direction changed

The current requirements favour a Gemini-first implementation with direct HTTP/WebSocket access, while the source tree also includes optional provider adapters and OpenAI-compatible gateway support. The comparison snapshot instead declares Anthropic, OpenAI, Ollama, local Whisper and free/premium TTS packages. Audio, STT and TTS abstractions therefore changed direction, not simply grew.

### Engineering maturity

The comparison snapshot has no test tree. The current tree contains 94 test_*.py files; pytest successfully collects 1,435 tests. Added documentation covers architecture, roadmap, extensions and milestones. This is a major improvement in specification and regression coverage.

## Compatibility and delivery observations

1. The current tree contains substantial untracked work. At review time, the new agent system, most extensions, tests, several provider adapters, and GitHub/community files are untracked in the current Git worktree. They are part of this filesystem comparison but are not included in a normal Git commit or release until explicitly added. This is the main delivery gap.

2. Dependency declaration needs a fresh-install check. Current source contains optional Claude, OpenAI and Ollama adapters, while current requirements.txt intentionally lists the Gemini-first stack and does not declare all of those SDKs. That can be correct if the adapters are optional, but it must be verified with the exact supported installation paths.

3. The safety model is more important now. The added agent system has risk levels, permission policies, approval requests, sensitive-action rules, and wall-clock/token limits. That is good design, but it controls tools that can affect files, processes, browsers and desktop apps. Production defaults should remain conservative and sensitive requests should keep requiring typed confirmation.

4. Secrets handling is improved but still operationally important. The current .gitignore protects .env and all .env.* variants except .env.example, which is stronger than the comparison snapshot. Do not put real keys in examples, reports or commits. API keys exposed in an editor or a chat transcript should be rotated.

## Validation performed

- File inventory and SHA-256 comparison completed successfully.
- Shared-file line diffs completed successfully.
- python -m pytest --collect-only -q completed successfully: 1,435 tests collected in 1.89 seconds.
- A full test run was started, but this review did not obtain a final complete pass/fail summary; this report does not claim the full suite passes.

## Bottom line

The current project is roughly seven times the Python source size and has changed from a Gemini-centric guided desktop assistant into a substantially larger, modular agent-and-extension product. The core is preserved, but the new code changes the product scope, operational risk, packaging needs and release process.

The highest-priority next step is to decide whether the currently untracked agent/extension/test work is intended for the release. If it is, add it intentionally, run a clean-install test, and run the complete test suite before shipping.

