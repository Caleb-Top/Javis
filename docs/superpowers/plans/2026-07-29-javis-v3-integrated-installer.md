# Javis v3.0 Integrated Installer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a Windows x64 Javis v3.0 NSIS installer and desktop ZIP that contain the complete Python backend and five-system runtime, install independently of `D:\Javis`, and preserve user state across upgrades.

**Architecture:** Reuse the validated deterministic runtime archive and Tauri first-start bootstrap, but make the release tooling version-neutral and set every release authority to `3.0.0`. The installer owns the desktop shell and bundled runtime; `%LOCALAPPDATA%\local.javis.desktop` owns persistent state and survives upgrades and uninstall.

**Tech Stack:** Python 3.11, FastAPI, WebSocket, faster-whisper, Windows SAPI, Tesseract, Tauri 2, Rust, TypeScript, Vite, NSIS, PowerShell.

## Global Constraints

- Product version is exactly `3.0.0`.
- The complete Python backend and all five systems are bundled.
- First start verifies SHA-256, extracts to a temporary runtime, probes imports, and atomically activates it.
- Memory, configuration, generated skills, workspace, uploads, outputs and logs survive upgrades.
- DeepSeek/Ollama models and CUDA/PyTorch training components remain external.
- No package manager, dependency download or model download runs during build or install.
- Cleanup is limited to generated targets, frontend output, bytecode and test caches.
- Blueprint, memory, models, YOLO assets, datasets and training output are protected.

---

### Task 1: V3 Release Contract

**Files:**
- Create: `tests/test_app_v3_integrated_release.py`
- Move: `app/v2.manifest.json` to `app/release.manifest.json`
- Modify: `app/package.json`
- Modify: `app/package-lock.json`
- Modify: `app/src-tauri/tauri.conf.json`
- Modify: `app/src-tauri/Cargo.toml`
- Modify: `app/src-tauri/Cargo.lock`

**Interfaces:**
- Produces version `3.0.0` from every package authority.
- Declares the five systems and external model/training boundary.

- [ ] Write V3 contract tests and observe the version checks fail on V2.
- [ ] Update all version authorities and release metadata.
- [ ] Run the focused contract tests.

### Task 2: Version-Neutral Runtime Tooling

**Files:**
- Move: `scripts/javis_v2_runtime.py` to `scripts/javis_release_runtime.py`
- Move: `scripts/stage_javis_v2_runtime.py` to `scripts/stage_javis_runtime.py`
- Move: `scripts/verify_javis_v2_release.py` to `scripts/verify_javis_release.py`
- Modify: `tests/test_app_voice_runtime_integration.py`

**Interfaces:**
- `build_runtime_archive(...) -> dict`
- `validate_runtime_archive(archive, manifest) -> list[str]`
- Runtime manifest contains `systems`, `external_components`, checksums and persistent paths.

- [ ] Add failing tests for V3 manifest metadata and complete five-system declarations.
- [ ] Rename the tooling and replace hard-coded V2 labels.
- [ ] Keep local Python 3.11, site-packages overlay, Whisper model and Tesseract packaging.
- [ ] Run runtime and voice integration tests.

### Task 3: Upgrade-Preservation And First-Start Gate

**Files:**
- Modify: `app/src-tauri/src/runtime_bundle.rs`
- Modify: `app/src-tauri/src/sidecar.rs`
- Test: `tests/test_app_v3_integrated_release.py`

**Interfaces:**
- `ensure_runtime` verifies the archive before extraction.
- `migrate_persistent_state` preserves all declared mutable data.
- Packaged sidecar starts only the activated runtime.

- [ ] Add contract checks for Cargo version binding, checksum, temporary extraction, import probe, rollback and preserved state.
- [ ] Bind the runtime version to `CARGO_PKG_VERSION`.
- [ ] Verify Rust compilation and release contracts.

### Task 4: V3 Build And Installer Acceptance

**Files:**
- Move: `scripts/build_javis_app_v2.ps1` to `scripts/build_javis_app_v3.ps1`
- Create: `scripts/verify_javis_v3_installer.ps1`
- Modify: `scripts/start_javis_app.py`

**Interfaces:**
- Build output: `artifacts/Javis-v3.0.0-test/`
- Desktop output: `%USERPROFILE%\Desktop\Javis-v3.0.0-Windows-x64.zip`
- Installer acceptance exits nonzero on install, activation, status, preservation or uninstall-boundary failure.

- [ ] Add source-contract tests for offline build, verifier invocation, checksums and desktop ZIP.
- [ ] Implement the V3 build orchestrator without install/download commands.
- [ ] Implement silent isolated NSIS install and launch verification.
- [ ] Check runtime V3 activation, backend health and preservation canary.
- [ ] Uninstall the isolated shell and prove local user data remains.

### Task 5: Clean Build And Final Verification

**Files:**
- Generate: `app/src-tauri/resources/javis-runtime.zip`
- Generate: `artifacts/Javis-v3.0.0-test/Javis_3.0.0_x64-setup.exe`
- Generate: `artifacts/Javis-v3.0.0-test/SHA256SUMS.txt`
- Generate: `artifacts/Javis-v3.0.0-test/INSTALL-TEST-REPORT.md`
- Generate: `%USERPROFILE%\Desktop\Javis-v3.0.0-Windows-x64.zip`

- [ ] Record protected blueprint/model/memory/training fingerprints.
- [ ] Dry-run cleanup, inspect the target list, then remove generated material only.
- [ ] Run all Python tests, frontend production build, Rust check and strict preflight.
- [ ] Stage and verify the complete Python runtime.
- [ ] Build the NSIS installer.
- [ ] Run isolated runtime and installed-app acceptance.
- [ ] Recheck protected fingerprints.
- [ ] Generate final hashes and desktop ZIP.
- [ ] Reopen the ZIP and verify every checksum before delivery.

