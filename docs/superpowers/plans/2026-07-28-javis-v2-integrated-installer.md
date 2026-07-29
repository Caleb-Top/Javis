# Javis v2.0 Integrated Installer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and verify a Windows x64 Javis v2.0 NSIS installer containing the complete Python backend and a portable runtime that does not depend on `D:\Javis`.

**Architecture:** A deterministic build script streams approved source and portable Python files directly into `javis-runtime.zip`. Tauri bundles that archive and a manifest as resources. On first start, the Rust shell verifies and extracts the runtime into `%LOCALAPPDATA%\Javis`, migrates persistent state during upgrades, then starts the packaged FastAPI sidecar.

**Tech Stack:** Python 3.11/3.13, FastAPI, WebSocket, Tauri 2, Rust, TypeScript, Vite, NSIS, PowerShell.

## Global Constraints

- Product version is exactly `2.0.0`.
- All Python backend source modules are packaged; no backend subsystem is removed.
- API keys and machine-specific `config.yaml` are never embedded.
- DeepSeek/Ollama model blobs remain external.
- Blueprint source and five-system architecture are not modified.
- Memory, user configuration, skills and workspace data survive upgrades.
- No dependency download or installation occurs during build or install.
- Generated cleanup never removes `.git`, `.claude`, models, memory, data, training output or user documents.

---

### Task 1: v2 Release Contracts

**Files:**
- Create: `tests/test_app_v2_integrated_release.py`
- Create: `app/v2.manifest.json`
- Modify: `app/package.json`
- Modify: `app/package-lock.json`
- Modify: `app/src-tauri/tauri.conf.json`
- Modify: `app/src-tauri/Cargo.toml`
- Modify: `app/src-tauri/Cargo.lock`

**Interfaces:**
- Produces version `2.0.0` in all package authorities.
- Produces release manifest keys `backend.packaged`, `backend.complete_source`, `runtime.archive`, and `data.preserved`.

- [ ] Write tests asserting all version authorities equal `2.0.0`.
- [ ] Run the v2 test and verify it fails on the existing `1.0.0` values.
- [ ] Add `app/v2.manifest.json` and update all package versions.
- [ ] Run the v2 test and existing release tests.

### Task 2: Deterministic Complete-Backend Runtime Archive

**Files:**
- Create: `scripts/javis_v2_runtime.py`
- Create: `scripts/stage_javis_v2_runtime.py`
- Modify: `scripts/app_package_manifest.py`
- Test: `tests/test_app_v2_integrated_release.py`

**Interfaces:**
- `build_runtime_archive(root: Path, output: Path, python_root: Path) -> dict`
- `runtime_entries(root: Path, python_root: Path) -> Iterator[RuntimeEntry]`
- `validate_runtime_archive(archive: Path, manifest: dict) -> list[str]`

- [ ] Add failing tests for complete backend directories, secret exclusion, cache exclusion, deterministic member names and required Python imports.
- [ ] Verify tests fail because the staging module does not exist.
- [ ] Implement an explicit backend allowlist containing `main.py`, `blueprint`, `brain_data`, `core`, `control`, `evolution`, `gateway`, `knowledge`, `memory`, `perception`, `providers`, `skills`, `tools`, `tools_lib`, `utils`, `voice` and `web`.
- [ ] Stream files into ZIP without creating a duplicate staging tree.
- [ ] Exclude `.pyc`, `__pycache__`, caches, logs, SQLite runtime state, secrets and developer-only package families.
- [ ] Write SHA-256, included capabilities, exclusions and size totals to `javis-runtime-manifest.json`.
- [ ] Validate archive paths against traversal and confirm required entry/import files exist.
- [ ] Run staging tests green.

### Task 3: Installed Runtime Bootstrap And State Migration

**Files:**
- Create: `app/src-tauri/src/runtime_bundle.rs`
- Modify: `app/src-tauri/src/main.rs`
- Modify: `app/src-tauri/src/sidecar.rs`
- Modify: `app/src-tauri/tauri.conf.json`
- Test: `tests/test_app_v2_integrated_release.py`

**Interfaces:**
- `runtime_bundle::ensure_runtime(app: &AppHandle) -> Result<PathBuf, String>`
- `SidecarManager::with_root(root: PathBuf) -> SidecarManager`

- [ ] Add failing source-contract tests for bundled resources, `%LOCALAPPDATA%` extraction, version marker, `tar.exe`, rollback and packaged sidecar root.
- [ ] Verify failure against current workspace-root-only sidecar.
- [ ] Bundle `resources/javis-runtime.zip`, manifest and checksum through Tauri resources.
- [ ] Implement extraction to `runtime.new`, required-file validation and version marker.
- [ ] Preserve `brain_data`, memory databases/sessions, config, generated skills, workspace, uploads, data and logs when upgrading.
- [ ] Promote only a validated runtime and retain one rollback copy.
- [ ] Make Sidecar launch packaged `python\python.exe` and `app\main.py`.
- [ ] Keep workspace discovery only as a development fallback.
- [ ] Run Rust check and v2 contract tests.

### Task 4: v2 Build, Artifact And Cleanup Tooling

**Files:**
- Create: `scripts/build_javis_app_v2.ps1`
- Create: `scripts/cleanup_javis_generated.py`
- Modify: `scripts/start_javis_app.py`
- Test: `tests/test_app_v2_integrated_release.py`

**Interfaces:**
- Build output: `artifacts/Javis-v2.0.0-test/`
- Cleanup CLI: `cleanup_javis_generated.py [--dry-run] [--execute]`

- [ ] Add failing tests requiring strict preflight, runtime archive generation, Tauri build, SHA256 output and no install/download commands.
- [ ] Add failing tests requiring cleanup dry-run and protected path rejection.
- [ ] Implement build orchestration with existing local Python, Node, Rust, MinGW and NSIS dependencies only.
- [ ] Copy installer, runtime manifest and checksum report to the release artifact directory.
- [ ] Update launcher to prefer v2 and report the actual product version.
- [ ] Implement cleanup limited to generated Tauri targets, frontend dist, bytecode, test caches and temporary staging.
- [ ] Run build-tool tests green.

### Task 5: Runtime And Functional Verification Harness

**Files:**
- Create: `scripts/verify_javis_v2_release.py`
- Create: `artifacts/Javis-v2.0.0-test/INSTALL-TEST-REPORT.md`
- Test: `tests/test_app_v2_integrated_release.py`

**Interfaces:**
- `verify_archive(archive, manifest) -> VerificationReport`
- `verify_extracted_runtime(runtime_root) -> VerificationReport`
- CLI exits nonzero on any required failure.

- [ ] Add failing tests for checksum mismatch, missing backend module and missing Python executable.
- [ ] Implement archive checksum and required-member verification.
- [ ] Extract to an isolated temporary directory and run packaged Python import probes.
- [ ] Start packaged `main.py` with workspace environment variables removed.
- [ ] Verify status, runtime status, WebSocket exact reply and Eric identity recall.
- [ ] Record optional hardware tests as `UNTESTED` unless explicitly completed in Tauri.
- [ ] Render a Markdown installation test report.

### Task 6: Build And Final Acceptance

**Files:**
- Generate: `app/src-tauri/resources/javis-runtime.zip`
- Generate: `app/src-tauri/resources/javis-runtime-manifest.json`
- Generate: `artifacts/Javis-v2.0.0-test/Javis_2.0.0_x64-setup.exe`
- Generate: `artifacts/Javis-v2.0.0-test/SHA256SUMS.txt`

- [ ] Preserve the existing v1 installer in `artifacts/Javis-v1.0.0-archive/`.
- [ ] Dry-run cleanup and inspect protected-path output.
- [ ] Remove only old reproducible Tauri build targets needed to reclaim build space.
- [ ] Run the full Python test suite.
- [ ] Run TypeScript compile and Vite production build.
- [ ] Run Rust `cargo check`.
- [ ] Run strict app preflight.
- [ ] Build the runtime archive and inspect its manifest and size.
- [ ] Build the Tauri release and NSIS installer.
- [ ] Run the isolated packaged-runtime verification harness.
- [ ] Verify installer and runtime SHA-256 values.
- [ ] Confirm the source blueprint files are unchanged.
- [ ] Publish the final artifact path, size, checksums, passed checks and explicit hardware-test limitations.
