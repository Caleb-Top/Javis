# Javis Agent Fusion A1-A4 Implementation Plan

> **Required sub-skill:** Use `superpowers:executing-plans` task by task, `superpowers:test-driven-development` for every behavior change, and `superpowers:verification-before-completion` before release claims.

**Goal:** Integrate the approved agent capabilities into Javis as one coherent runtime: progressive tool discovery, governed skill discovery, durable agent execution, and typed event/middleware infrastructure, while preserving the current Live surface and the five-system blueprint.

**Architecture:** `JarvisRuntime` owns one typed `EventBus`, one `MiddlewarePipeline`, one `ToolCatalog`, one `SkillCatalog`, and one durable `AgentRunStore`. The existing `ToolRegistry` remains the execution authority and guardrail boundary; catalogs only discover and describe capabilities. Agent execution writes structured run/task/step/delta/approval/checkpoint state and publishes correlated events. API and Code surfaces read those same sources. Live visuals and interaction code are not changed.

**Tech Stack:** Python 3.11, FastAPI, SQLite/FTS5, pytest, TypeScript/Vite/Tauri, PowerShell, pnpm, Cargo.

## Global Constraints

- Work, caches, test artifacts, build outputs, and packaging must stay on `G:`.
- Preserve the latest Live orb, pet behavior, menus, transitions, and appearance exactly.
- Preserve the five-system blueprint, memories, models, training results, user data, and historical design records.
- External skills never bypass `ToolRegistry`, `ToolGuard`, permissions, approval, timeout, audit, or rollback boundaries.
- GPL and unknown-license repositories are design references only; no source is copied from them.
- New persisted schemas are versioned, deterministic, and backward compatible.
- Every production behavior starts with a failing test and finishes with focused plus regression verification.
- Installer work starts only after source, Python, frontend, Rust, and startup validation pass.

## Acceptance Matrix

| Area | Required result |
|---|---|
| G-only workspace | Active source/config/tests contain no fixed `D:\\Javis`; project caches/build outputs resolve under the active `G:` root. |
| A4 events | Existing string-based EventBus callers still work; typed events carry schema version, correlation, causation, sequence, and timestamps. |
| A4 middleware | Ordered before/after/error middleware can observe or reject execution without bypassing guardrails. |
| A1 tools | Search/list returns lightweight metadata; inspect returns one full schema; presets and health filtering are deterministic. |
| A2 skills | Skills expose provenance, license, version, state, tags, and evaluation status; import is staged by default. |
| A3 runs | Runs, tasks, steps, deltas, approvals, and checkpoints survive process restart and support safe resume/cancel. |
| Runtime/API | Runtime owns all four systems; APIs return structured errors and one consistent status source. |
| Compatibility | Existing Python and app tests pass; Live source remains byte-for-byte unchanged unless a test-only import is unavoidable. |
| Release | Production builds and startup smoke tests pass before a new installer/ZIP is produced. |

## Task 1: Enforce Portable G-Rooted Development

**Create:**
- `tests/test_portable_workspace_paths.py`
- `scripts/javis_dev_env.ps1`

**Modify:**
- `utils/config_api.py`
- `config.yaml`
- active tests containing `D:/Javis`
- `app/src/app/FirstRunPanel.ts`

- [x] Write tests that reject fixed drive paths in active source/config and verify relative paths resolve from the repository root.
- [x] Run the focused tests and confirm RED.
- [x] Resolve relative configured paths against `CONFIG_PATH.parent` and convert default source paths to relative values.
- [x] Replace test roots with `Path(__file__).resolve()` and make first-run copy portable.
- [x] Add a PowerShell environment bootstrap that roots pip, pnpm, Cargo, temp, and build outputs on the active repository drive.
- [x] Re-run focused tests and confirm GREEN (`7 passed`; affected regression set `125 passed`).

## Task 2: Add Typed Events and Middleware (A4 Foundation)

**Create:**
- `core/middleware.py`
- `tests/test_typed_events.py`
- `tests/test_middleware.py`

**Modify:**
- `core/events.py`

- [x] Test backward-compatible string subscriptions/history plus typed metadata and monotonic sequence numbers.
- [x] Test ordered `before`, reverse `after`, and reverse `on_error` middleware behavior.
- [x] Confirm RED, then implement `EventType`, versioned `Event`, correlation/causation metadata, and thread-safe sequence assignment.
- [x] Implement `MiddlewareContext`, protocol/base hooks, rejection semantics, and `MiddlewarePipeline`.
- [x] Confirm focused GREEN and existing EventBus tests remain green (`8 passed`; related regression `16 passed`).

## Task 3: Add Progressive Tool Catalog (A1)

**Create:**
- `core/tool_catalog.py`
- `tests/test_tool_catalog.py`

**Modify:**
- `core/tool_registry.py`
- `core/runtime.py`

- [x] Test lightweight list/search, one-tool inspect, category/tag presets, risk filtering, and health ordering.
- [x] Test that catalog execution still delegates to `ToolRegistry` and emits correlated start/result/error events.
- [x] Confirm RED, then extend `ToolDef` with optional metadata without breaking positional constructors.
- [x] Implement catalog indexing, deterministic matching, presets, execution statistics, and health snapshots.
- [x] Inject the shared EventBus and middleware into `ToolRegistry`; keep guardrail checks authoritative.
- [x] Confirm focused and registry regression GREEN (`31 passed`, plus guard/tool regression `15 passed`).

## Task 4: Add Governed Skill Catalog (A2)

**Create:**
- `core/skill_catalog.py`
- `tests/test_skill_catalog.py`
- `skills/external/README.md`

- [x] Test Markdown/frontmatter discovery, provenance/license/version fields, deterministic search, and candidate/staged/active transitions.
- [x] Test that unknown/missing licenses cannot activate and that failed evaluation blocks promotion.
- [x] Confirm RED, then implement SQLite-backed metadata plus FTS5 with a deterministic fallback.
- [ ] Add import manifests and hashes; external content defaults to `candidate` and cannot execute directly.
- [x] Confirm persistence across reopen and focused GREEN (`18 passed`; existing skill/runtime regression `12 passed`).

## Task 5: Add Durable Agent Runs (A3)

**Create:**
- `core/agent_runs.py`
- `tests/test_agent_runs.py`

- [x] Test run/task/step creation, ordered deltas, checkpoints, approvals, cancellation, resume, and reopen persistence.
- [x] Test invalid state transitions and unresolved approval behavior fail closed.
- [x] Confirm RED, then implement a transactional SQLite store with schema versioning and stable IDs/order.
- [x] Publish correlated typed events for each lifecycle transition.
- [x] Confirm focused GREEN, including concurrent append ordering (`22 passed` across A1-A3 ownership and persistence tests).

## Task 6: Integrate Runtime, Agent, and APIs

**Create:**
- `tests/test_runtime_agent_fusion.py`

**Modify:**
- `core/runtime.py`
- `core/agent.py`
- `main.py`

- [ ] Test that one runtime instance owns and connects the event bus, middleware, catalogs, registry, and run store.
- [ ] Test catalog-selected schemas replace unconditional full-schema injection while explicit tool calls remain compatible.
- [ ] Test `/api/tool-catalog`, `/api/skill-catalog`, and `/api/agent-runs` contracts and structured failure responses.
- [ ] Record WebSocket chat/tool/approval lifecycle into durable runs without changing the Live renderer.
- [ ] Confirm focused GREEN and existing API/WebSocket tests remain green.

## Task 7: Import Approved MIT Skill Assets

**Source:** `agent-skills-main.zip` from the user's Downloads folder.

- [ ] Verify the archive identity and MIT license before copying.
- [ ] Import only the reviewed 24 skill assets under `skills/external/agent-skills/` with upstream license and provenance manifest.
- [ ] Register assets as `candidate`; run parser, hash, duplicate-name, and policy validation.
- [ ] Do not copy GPL or unknown-license code/datasets.
- [ ] Confirm catalog discovery and provenance tests GREEN.

## Task 8: Full Verification and Release Gate

- [ ] Run all Python tests with temp/cache paths on `G:`.
- [ ] Install/use frontend dependencies with the G-local pnpm store and run all app tests.
- [ ] Run Vite production build, Cargo tests/check, and backend startup/API smoke tests.
- [ ] Verify no active process, config, or build script writes project artifacts to `C:` or `D:`.
- [ ] Compare Live source hashes with the pre-fusion baseline and visually smoke-test mode switching without altering Live styling.
- [ ] Write a fusion report listing implemented capabilities, upstream provenance, test evidence, residual limitations, and rollback points.
- [ ] Merge the isolated branch into `G:\\Javis` only after all gates pass.
- [ ] Build a replacement v3 installer/ZIP only after the merged tree repeats the same gates.

## Rollback Strategy

- Each task is committed separately on `codex/javis-agent-fusion-a1-a4`.
- Database changes use additive versioned migrations; existing memory databases are not modified by tests.
- External assets are isolated below `skills/external/` and removable without touching built-in capabilities.
- Runtime integration keeps legacy EventBus and ToolRegistry APIs until all callers migrate.
- The main `G:\\Javis` worktree is not modified until the branch passes the release gate.
