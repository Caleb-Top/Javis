# Javis L1 Presence and Homeostasis Implementation Plan

> **Execution rule:** implement each work package with a failing test first, keep commits independently revertible, and do not wait for additional design confirmation.

**Date:** 2026-08-12  
**Status:** In progress  
**Approved design:** `docs/superpowers/specs/2026-08-10-javis-l1-presence-homeostasis-design.md`  
**Base commit:** `5f152ecb`  
**Branch:** `codex/javis-life-os-l1-presence-20260812`  
**Worktree:** `G:\Javis\.codex\worktrees\javis-life-os-l1-presence-20260812`

## 1. Delivery Boundary

- `G:\Javis` remains read-only and retains the user's existing voice/model work.
- L0-A is frozen on `codex/javis-life-os-l0-foundation-20260812`; L1 starts from its complete automated exit.
- This worktree is the only L1 edit and test area. Runtime test data must use `tmp_path` or a verified worktree-local temporary root.
- No dependency install, model download, network fetch, installer build, or D-drive mutation belongs to L1 implementation.
- L0-B remains blocked at its explicit Three.js/VRM download gate. L1 must use the existing `ExpressionIntent v1` and Orb fallback.
- L1 adds no second identity, EventBus, WebSocket, LifeService, public revision, or model-writable state tool.

## 2. Frozen Architecture

```text
authorized production event
  -> bounded LifeObservation
  -> pure AppraisalReducer
  -> AttentionCoordinator + HomeostasisReducer
  -> one LifeService-owned InnerStateSnapshot
  -> read-only API/model summary/ExpressionIntent projection
  -> asynchronous TurnExperienceReceipt
```

The model and every frontend are read-only consumers. State changes are deterministic consequences of governed observations or injected-clock decay. Text, transcript, audio, tool bodies, file contents, secrets and hidden reasoning never enter L1 contracts or receipts.

## 3. Dependency Order

1. Runtime access and voice capture authorization must land before any new voice provenance path.
2. Pure contracts land before stores, reducers or bridges.
3. Atomic conversation acceptance and voice reservations land before `server_verified` provenance is emitted.
4. Appraisal, attention and homeostasis remain pure until production bridges have independent tests.
5. Exact invocation uses the canonical conversation store and deterministic local lane; it never bypasses request identity or replay.
6. Receipts are asynchronous and cannot block EventBus, ConversationHub or voice callbacks.
7. Public API/WS integration is last, after single-writer and stale-event behavior are proven.

## 4. Work Packages

### Task 1: Lock Runtime Access and Capture Authorization

**Files:**
- Create: `core/runtime_access.py`
- Create: `app/src/bridge/runtimeAccess.ts`
- Modify: `main.py`
- Modify: `utils/app_cors.py`
- Modify: `gateway/conversation_ws.py`
- Modify: `voice/streaming_ws.py`
- Modify: `app/src-tauri/src/sidecar.rs`
- Modify: `app/src-tauri/src/main.rs`
- Modify: `app/src/bridge/backendClient.ts`
- Modify: `app/src/live/VoiceCapture.ts`
- Test: `tests/test_runtime_access.py`
- Test: `tests/test_conversation_gateway.py`
- Test: `tests/test_continuous_voice_gateway.py`
- Test: `app/tests/runtimeAccess.test.ts`
- Test: `app/tests/backendClient.test.ts`
- Test: `app/tests/continuousVoiceCapture.test.ts`

**Interfaces:**
- `RuntimeAccessAuthority.issue(client_instance_id, scopes, ttl_seconds)`
- `validate_http(request, scope)` and `validate_websocket(ws, scope)` before response/accept
- desktop-only Tauri command `issue_runtime_capability`
- WebView receives only short-lived capability, never sidecar ownership token

**Required behavior:**
- 256-bit random bearer, server stores only an in-memory digest;
- bind boot ID, client ID, exact scopes, issue/expiry time and nonce;
- reject missing WS capability with 4401 and bad Origin/scope with 4403 before accept;
- reject voice capture before native manager start and bind a short capture lease to session plus owner generation;
- default backend and capture surfaces to loopback;
- allow fixed loopback development origins only under explicit test/development flags ignored by packaged runtime;
- audit only reason code, scope and hashed client ID.

**Tests:** malicious local page, spoofed Origin, missing/expired/wrong-scope/replayed token, boot rotation, owner generation rotation, no transcript before validation, no microphone start before validation.

**Commit:** `feat(security): authorize local runtime surfaces`

### Task 2: Freeze L1 Wire Contracts

**Files:**
- Create: `core/life/l1/__init__.py`
- Create: `core/life/l1/contracts.py`
- Test: `tests/test_l1_contracts.py`

**Interfaces:** exact v1 contracts from design sections 7 and 12:
- `InputProvenance`, `LifeObservation`, `AppraisalResult`
- `AttentionClaim`, `AttentionSnapshot`, `HomeostasisSnapshot`
- `AffectEvidence`, `FunctionalAffect`, `PresenceSnapshot`
- `InnerStateSnapshot`, `PlaybackLifecycleEvent`, `TurnExperienceReceipt`

**Rules:** exact fields, schema 1, millisecond UTC, bounded IDs, finite unit values, frozen recursive mappings, strict missing/extra rejection, canonical hash, no free-form psychological explanation, and a recursive forbidden-field scan.

**Commit:** `feat(life): freeze l1 presence contracts`

### Task 3: Add Atomic Request Acceptance and Voice Provenance Reservations

**Files:**
- Modify: `core/conversation_store.py`
- Modify: `core/conversation_hub.py`
- Modify: `core/conversation_protocol.py`
- Create: `voice/turn_registry.py`
- Modify: `voice/streaming_ws.py`
- Test: `tests/test_conversation_atomic_acceptance.py`
- Test: `tests/test_voice_turn_registry.py`
- Test: `tests/test_unified_conversation_integration.py`

**Interfaces:**
- `ConversationStore.accept_request()` atomically writes idempotency claim, user message and canonical accepted event;
- `VoiceTurnRegistry`: available -> reserved -> committed, with rollback, expiry and bounded capacity;
- provenance reference binds boot/session/owner generation/voice sequence/turn and normalized transcript with HMAC or constant-time in-memory comparison.

**Commit:** `feat(conversation): bind atomic voice provenance`

### Task 4: Implement Deterministic Clock, Decay and Appraisal

**Files:**
- Create: `core/life/l1/clock.py`
- Create: `core/life/l1/appraisal.py`
- Test: `tests/test_l1_appraisal.py`

**Interfaces:** pure `AppraisalReducer.reduce(observation, now)` with the fixed design mapping; no I/O, model, random source or wall clock inside reducers.

**Rules:** bounded deltas, fixed reason codes, TTLs from injected time, `satisfied` only from `goal.verified`, malformed or unsupported observations fail closed.

**Commit:** `feat(life): derive deterministic appraisals`

### Task 5: Implement Attention Arbitration

**Files:**
- Create: `core/life/l1/attention.py`
- Test: `tests/test_l1_attention.py`

**Interfaces:** `AttentionCoordinator.apply()`, `expire()`, `snapshot()`.

**Rules:** priorities and TTLs exactly match design section 7.6; stale terminal events close only their own claim; lower-priority background sessions cannot steal foreground attention; equal priority resolves by governed recency.

**Commit:** `feat(life): arbitrate bounded attention`

### Task 6: Implement Homeostasis and Functional Affect

**Files:**
- Create: `core/life/l1/state.py`
- Create: `core/life/l1/affect.py`
- Test: `tests/test_l1_homeostasis.py`

**Interfaces:** single-writer `HomeostasisReducer`, lazy decay, thresholded semantic change, bounded public snapshot and affect projection.

**Rules:** no continuous mood persistence; restart returns transient dimensions to baseline; certainty/caution/blockedness/curiosity/cognitive load remain evidence-bound; relationship never alters permission.

**Commit:** `feat(life): reduce homeostasis and functional affect`

### Task 7: Bridge Real Conversation, Voice, Playback and Runtime Observations

**Files:**
- Create: `core/life/l1/observation_bridge.py`
- Modify: `core/conversation_hub.py`
- Modify: `voice/streaming_ws.py`
- Create: `voice/playback_events.py`
- Modify: `voice/native_playback.py`
- Modify: `core/life/service.py`
- Test: `tests/test_l1_observation_bridge.py`
- Test: `tests/test_playback_lifecycle.py`

**Interfaces:** only real publishers listed by the design evidence gate; canonical event ID, sequence domain, causation, boot and generation survive projection; payload bodies do not.

**Commit:** `feat(life): bridge governed l1 observations`

### Task 8: Add Exact Invocation and Deterministic Local Lane

**Files:**
- Create: `core/life/l1/wake.py`
- Modify: `core/conversation_hub.py`
- Modify: `gateway/conversation_ws.py`
- Test: `tests/test_presence_responder.py`
- Test: `tests/test_unified_conversation_integration.py`

**Interfaces:** strict alias matcher and `PresenceResponder` decision; canonical deterministic response uses normal request identity, persistence, replay and terminal events without model/TTS dependency.

**Rules:** exact `Javis/Jarvis/贾维斯` after normalization returns `我在`; complete requests continue to model with no extra response; 1500 ms physical-call dedupe uses provenance identity, not history deletion.

**Commit:** `feat(life): answer exact invocation locally`

### Task 9: Make Barge-In and Playback Request-Scoped

**Files:**
- Modify: `app/src/bridge/backendClient.ts`
- Modify: `app/src/live/VoiceCapture.ts`
- Modify: `app/src/main.ts`
- Modify: `voice/native_playback.py`
- Test: `app/tests/backendClient.test.ts`
- Test: `app/tests/continuousVoiceCapture.test.ts`
- Test: `tests/test_playback_lifecycle.py`

**Interfaces:** `cancel(requestId, reason)`, immutable interrupted request capture, awaitable interruption barrier and generation-scoped playback start/stop.

**Commit:** `fix(voice): bind barge-in to interrupted request`

### Task 10: Project and Persist Thin Turn Receipts

**Files:**
- Create: `core/life/l1/receipts.py`
- Modify: `core/life/journal.py`
- Modify: `core/life/service.py`
- Test: `tests/test_turn_experience_receipts.py`

**Interfaces:** asynchronous idempotent receipt projection keyed by session/request; recent 32 in memory; operational persistence; restart recovery marks interrupted, never fabricates canonical terminal events.

**Commit:** `feat(life): persist privacy-thin turn receipts`

### Task 11: Integrate Inner State, Read-Only Surfaces and Model Context

**Files:**
- Modify: `core/life/service.py`
- Modify: `core/life/api.py`
- Modify: `core/life/expression.py`
- Modify: `core/prompt_builder.py`
- Modify: `app/src/life/lifeTypes.ts`
- Modify: `app/src/life/LifeStateBridge.ts`
- Test: `tests/test_l1_life_service.py`
- Test: `tests/test_life_api.py`
- Test: `tests/test_l1_model_context.py`
- Test: `app/tests/lifeStateBridge.test.ts`

**Interfaces:** `GET /api/life/inner-state`, scoped read-only access, semantic WS changes, <=512-byte model read-only summary, existing `ExpressionIntent v1` only.

**Rules:** no mutation routes/tools; public revision remains L0 LifeSnapshot revision; unavailable L1 enters compatibility mode and never writes fallback state back.

**Commit:** `feat(life): expose deterministic inner state`

### Task 12: Lock Release and Real-User Evidence

**Files:**
- Create: `tests/test_l1_release_contract.py`
- Modify: `docs/JAVIS_OPERATIONS.md`
- Create: `docs/verification/JAVIS_L1_D_DRIVE_ACCEPTANCE.md`
- Modify: `docs/superpowers/plans/2026-08-12-javis-life-os-integrated-execution.md`

**Automated gates:** full Python, App, Rust/Tauri, strict source/secret scans, no mutating Life APIs, no state-write tools, no sync SQLite in observation hot path, deterministic replay, build.

**Manual gates:** exact invocation offline, malicious local webpage rejection, verified microphone provenance, delayed playback stop barge-in, model switch continuity, restart recovery, Live/Code/Pet revision agreement. Every unexecuted installer/D-drive item remains `NOT EXECUTED`.

**Commit:** `test(life): lock l1 release contracts`

## 5. Test Commands

PowerShell does not expand pytest wildcards. Resolve lists before invocation:

```powershell
$lifeTests = @(Get-ChildItem tests -Filter 'test_l*.py' -File | Sort-Object Name | ForEach-Object FullName)
& 'G:\Javis\venv\Scripts\python.exe' -m pytest @lifeTests tests/test_unified_conversation_integration.py tests/test_continuous_voice_gateway.py tests/test_p0_runtime.py -q
& 'G:\Javis\venv\Scripts\python.exe' -m pytest -q
```

App tests/build use existing dependencies only. In an isolated worktree without `node_modules`, use the already installed formal-workspace dependencies through a temporary validated resolver mapping; do not install or alter lock files.

## 6. Exit Rules

- Automated success never marks D-drive or installer scenarios passed.
- A missing production publisher removes the observation mapping; tests cannot invent one.
- Any capability leak, pre-accept WS response, microphone start before authorization, transcript disclosure, stale request cancellation or state writer duplication blocks the current work package.
- L1 does not enter L2 until all 18 approved design exit criteria have evidence.
