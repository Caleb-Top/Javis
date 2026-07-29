# Javis v2.0 Integrated Installer Design

## Objective

Produce a Windows x64 NSIS installer for Javis v2.0 that starts without the
`D:\Javis` source workspace, preserves the five-system blueprint, keeps user
memory and data across upgrades, and separates large optional model/training
assets from the core application.

## Product Boundary

The v2.0 standard package contains:

- Tauri v2 Live-first desktop shell, desktop pet, Code surface, control drawer,
  conversation memory surface, diagnostics, tray and window modes.
- A portable Python runtime.
- FastAPI/WebSocket backend and the blueprint, core, control, evolution,
  gateway, memory, perception, provider, skill, tool, utility, voice and Web
  modules required at runtime.
- Required Python packages for local orchestration, desktop control, OCR,
  OpenCV ONNX inference, faster-whisper STT and edge-tts.
- A small default YOLO ONNX model when a compatible model is available.
- Runtime manifest, file checksums and release metadata.

The standard package does not contain:

- Git history, Claude/Codex/Hermes workspaces or developer documentation caches.
- Rust, MinGW, Node.js, npm dependencies or Tauri build targets.
- Source virtual environments, duplicate Python installations or test caches.
- DeepSeek/Ollama model blobs.
- CUDA/PyTorch training stacks and large experimental datasets.
- Logs, temporary uploads, rollback snapshots or previous build outputs.
- API keys or machine-specific `config.yaml` secrets.

Ollama models, user datasets and the CUDA training stack remain discoverable as
external components. Their absence must degrade the corresponding optional
capability without preventing Live, Code, memory or local tools from starting.

## Installed Layout

```text
%LOCALAPPDATA%\Programs\Javis\
  javis-app.exe
  resources\
    javis-runtime.zip
    javis-runtime-manifest.json

%LOCALAPPDATA%\Javis\
  runtime\
    python\
    app\
    runtime-version.json
  brain_data\
  data\
  memory\
  workspace\
  uploads\
  logs\
  config.yaml
  rollback\
```

The installer owns only the application shell and bundled release resources.
The app owns `%LOCALAPPDATA%\Javis`. Normal uninstall removes the shell but
leaves user data in place so reinstall and upgrades do not erase memory.

## Runtime Staging

`scripts/stage_javis_v2_runtime.py` creates an isolated staging directory from
an explicit allowlist. It never copies a top-level directory wholesale unless
that directory is part of the runtime manifest. It rejects:

- files outside the repository root;
- API-key-bearing `config.yaml`;
- SQLite files that are open or not explicitly listed as personal seed data;
- `__pycache__`, `.pyc`, build targets, caches and files above the configured
  per-file size limit unless explicitly approved in the manifest.

The portable Python source is `python-embed`. The staging process removes
developer-only packages and the CUDA/PyTorch family from the standard runtime,
then validates imports required by the packaged backend. No package is
downloaded during staging or installation.

The staging output is zipped deterministically and accompanied by:

- product and runtime version;
- SHA-256 of the archive;
- included capability groups;
- excluded optional groups;
- source commit when available;
- creation timestamp;
- uncompressed and compressed byte counts.

## First Start And Upgrade

Rust resolves the bundled resource directory and reads the runtime manifest.
When the installed runtime version or checksum differs:

1. Extract to `%LOCALAPPDATA%\Javis\runtime.new`.
2. Validate all required files and run the packaged Python health probe.
3. Preserve existing state directories and user configuration.
4. Move the current runtime to `rollback\runtime.previous`.
5. Atomically promote `runtime.new` to `runtime`.
6. Start `runtime\python\python.exe -u runtime\app\main.py`.
7. Roll back automatically if `/api/status` does not identify `service=javis`.

Only one previous runtime is retained. A failed extraction or probe leaves the
currently working runtime untouched.

## Data And Memory

The extracted backend runs with `%LOCALAPPDATA%\Javis\runtime\app` as its code
root and `%LOCALAPPDATA%\Javis` as its state root. Runtime path resolution must
support `JAVIS_DATA_ROOT` while retaining current workspace defaults for Web
debug and development.

Persistent paths include:

- `brain_data`;
- memory SQLite databases and session records;
- user-created skills and evolved active procedures;
- configuration without plaintext secrets in release artifacts;
- workspace, uploads and generated user outputs;
- TTS cache and application logs.

Blueprint source and audit definitions remain versioned program files. The
five-system architecture is not rewritten or reduced by packaging work.

## Repository Reshaping

The repository keeps source, tests and build tools. Cleanup is limited to
reproducible generated material after the v2 installer passes verification:

- old Tauri `debug` and superseded release targets;
- stale frontend `dist`;
- Python bytecode and test caches;
- `_sdk_extract`, temporary staging and generated preview logs;
- superseded v1 build artifacts after the v2 artifact has been copied to the
  release output directory.

The cleanup command has `--dry-run` and produces a size report. It must never
remove `.git`, `.claude`, Ollama models, `data`, `brain_data`, `memory`,
`workspace`, YOLO models, training output or user-authored documents.

## Version And Artifacts

All version authorities become `2.0.0`:

- `app/package.json` and lockfile root package;
- `app/src-tauri/tauri.conf.json`;
- `app/src-tauri/Cargo.toml` and lockfile package;
- `app/v2.manifest.json`;
- launcher and build script display text.

Release artifacts are copied to:

```text
artifacts/Javis-v2.0.0-test/
  Javis_2.0.0_x64-setup.exe
  javis-runtime-manifest.json
  SHA256SUMS.txt
  INSTALL-TEST-REPORT.md
```

## Verification

The release is accepted only when all of the following are fresh and passing:

1. Full Python test suite.
2. TypeScript compile and Vite production build.
3. Rust `cargo check`.
4. Strict app build preflight.
5. Runtime staging import probe.
6. Runtime archive checksum verification.
7. Tauri release and NSIS bundle build.
8. Installation into an isolated test directory with `JAVIS_ROOT` unset.
9. Backend startup from the installed runtime only.
10. `/api/status`, `/api/runtime/status` and WebSocket exact-reply checks.
11. Live local fast path, Eric identity recall and memory persistence across
    app restart.
12. Code surface load, sidebar collapse geometry and controlled workspace API.
13. App status consistency and non-blocking confirmation.
14. TTS generation; microphone, system audio and STT remain explicitly marked
    untested until the user grants real hardware access in the Tauri app.
15. Uninstall boundary check proving `%LOCALAPPDATA%\Javis` user data remains.

## Failure Handling

- A missing external Ollama model reports a configuration state and keeps the
  App, memory, diagnostics and non-LLM tools available.
- Port 8080 occupied by another service fails closed with a visible diagnostic.
- Runtime checksum mismatch prevents activation.
- Runtime import or health failure restores the previous runtime.
- Installer or staging failures leave the source workspace and user data
  unchanged.
