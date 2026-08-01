# Javis Unified Conversation Verification - 2026-08-01

## Result

The interruptible shared-conversation runtime passed its deterministic integration
checks, complete automated suites, production frontend build, Rust checks, source
backend API smoke test, native application compilation/startup, and G-drive path
checks.

Two physical acceptance items remain explicitly unclaimed: a human noisy-room
barge-in test with real speaker output, and manual interaction QA of the transparent
native window. The Windows Computer Use helper captured the process and window but
intermittently returned `GetCursorPos 0x80070005`, so it was not used to assert
mouse-driven transitions.

## Shared Conversation

Command:

```powershell
G:\Javis\venv\Scripts\python.exe scripts\verify_unified_conversation.py `
  --output tmp\verification\unified-conversation.json
```

Result: PASS.

- Live and Code received the same ordered canonical request events.
- A replacement Live turn cancelled the active slow Code turn.
- No response delta appeared after the cancelled request's terminal sequence.
- Code reconnected from sequence 4 and replayed the cancellation and replacement.
- One session persisted five expected messages.
- Three agent runs persisted as two completed and one cancelled.
- The server shut down with zero active requests and zero subscribers.
- Verification databases and output were created under `G:`.

## Automated Suites

| Surface | Command | Result | Duration |
|---|---|---:|---:|
| Python core | `scripts/run_python_tests.ps1` (`tests`) | 338 passed | 90.85 s |
| Agent distill | `pytest agent_distill/tests -q` | 9 passed | 3.88 s |
| Claude distill | `pytest ClaudeAgent_Distill/agent_distill/tests -q` | 9 passed | 3.49 s |
| App logic | `pnpm test` | 101 passed | 0.70 s |
| Frontend | `pnpm build` | PASS, 57 modules | 0.65 s |
| Rust native | `cargo test --locked` | 4 passed | 25.02 s |
| Rust compile | `cargo check --locked` | PASS | 19.06 s |

The standalone integration test also passed through pytest:
`tests/test_unified_conversation_integration.py` (1 passed in 2.64 s).

## Source Backend Smoke

The current worktree `main.py` was started as a real subprocess on port 18087.

- `/api/status`: Javis service online.
- `/api/runtime/status`: runtime online, no active requests.
- `/api/tool-catalog`: 10 registered catalog entries.
- `/api/skill-catalog`: 24 registered catalog entries.
- Shutdown: zero listeners remained on port 18087.

The deterministic dual-WebSocket harness covers the canonical message, interrupt,
cursor replay, persistence, and graceful-shutdown path without relying on an LLM.

## Native And Visual Checks

- `pnpm tauri dev` compiled and opened
  `app/src-tauri/target/debug/javis-app.exe` from this G-drive worktree.
- The source backend was available on port 8080 during startup.
- The same Vite frontend exposed the expected Live-first DOM at port 5173.
- App source tests cover immediate Code rendering, Live/Code/Settings/Pet transitions,
  compact bounds, sidebar alignment, stop/replacement input, and session identity.
- After QA cleanup, ports 8080 and 5173 had zero listeners and no `javis-app`
  process remained.

The transparent native window could not be fully driven by the available desktop
helper because Windows intermittently denied cursor capture. This is recorded as
an environment-limited manual acceptance item, not a pass.

## Voice Evidence

- Native PyAudio/PortAudio capture opened a real Windows microphone at 48 kHz with
  20 ms frames and emitted 62 level events during a 1.2 s sample.
- Raw microphone audio persistence remained disabled.
- Packaged local faster-whisper transcribed an offline SAPI Chinese noisy-speech
  sample and returned a final segment about 0.5 s after model preload.
- Capture, frame processing, and transcription use separate bounded queues, so a
  slow recognizer cannot stop microphone capture.
- Continuous capture remains active across turns. Speech onset reserves barge-in,
  stops native playback, cancels the active request, and submits only the final
  replacement transcript to the same session.
- Diagnostics correctly report reduced mode: adaptive playback-reference echo
  cancellation, Wiener-style suppression, bounded AGC, and adaptive energy/ZCR
  VAD. RNNoise, WebRTC APM, and Silero are not claimed active when unavailable.

Remaining physical checks: human noisy-room speech, speaker audibility, and
subjective interruption latency. Model-name recognition such as `Javis` can still
benefit from a future local hotword correction layer.

## Invariants

- Active source/config/build files contain no fixed `D:\Javis` project path.
- `JAVIS_ROOT`, pnpm store, Cargo target, pip cache, temp, Cargo home, and Rustup
  home resolved under `G:` during verification.
- `LiveOrb.ts`, Pet renderer tree, skins, icons, and palette definitions are
  unchanged from pre-voice commit `5ab178d5`.
- The only `LiveOrbRenderer.ts` behavior change accepts real microphone level and
  adds it to the existing listening energy. It does not change geometry, assets,
  palette, or shader shape.
- No blueprint, memory, model, or training artifact was modified.

## Provenance And Rollback

- Shared interruptible conversation: commits `0dd54e4e` through `f12bb28e`.
- Voice barge-in and fallback status: `5ab178d5`.
- Native continuous voice pipeline: `5e40c0be`.
- This verification adds only the harness, its test, this report, and plan status.

Rollback can revert the verification commit independently. Voice rollback begins
at `5e40c0be`; the unified conversation rollback boundary begins at `0dd54e4e`.
