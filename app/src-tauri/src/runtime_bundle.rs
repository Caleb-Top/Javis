use std::{
    env,
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
};
use tauri::{AppHandle, Manager};

use crate::process_command::hidden_command;

const VERSION: &str = env!("CARGO_PKG_VERSION");
const ARCHIVE_NAME: &str = "javis-runtime.zip";
const MANIFEST_NAME: &str = "javis-runtime-manifest.json";
const CHECKSUM_NAME: &str = "javis-runtime.sha256";
const VERSION_MARKER: &str = "runtime-version.json";

pub fn canonical_data_root(app: &AppHandle) -> Result<PathBuf, String> {
    let requested = if let Some(configured) = env::var_os("JAVIS_DATA_ROOT") {
        let path = PathBuf::from(configured);
        if path.as_os_str().is_empty() || !path.is_absolute() {
            return Err("JAVIS_DATA_ROOT must be an absolute path".to_string());
        }
        path
    } else {
        app.path()
            .app_data_dir()
            .map_err(|error| error.to_string())?
    };
    fs::create_dir_all(&requested).map_err(|error| error.to_string())?;
    reject_reparse_chain(&requested)?;
    let canonical = fs::canonicalize(&requested).map_err(|error| error.to_string())?;
    if !canonical.is_absolute() {
        return Err("canonical Javis data root must be absolute".to_string());
    }
    let probe = canonical.join(format!(".javis-write-probe-{}", std::process::id()));
    let mut stream = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&probe)
        .map_err(|_| "JAVIS_DATA_ROOT must be writable".to_string())?;
    let result = stream
        .write_all(b"javis")
        .and_then(|_| stream.sync_all())
        .map_err(|_| "JAVIS_DATA_ROOT must be writable".to_string());
    drop(stream);
    let _ = fs::remove_file(&probe);
    result?;
    Ok(canonical)
}

fn runtime_slot_root(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_local_data_dir()
        .map_err(|error| error.to_string())
}

pub fn runtime_path(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(runtime_slot_root(app)?.join("runtime"))
}

pub fn ensure_runtime(app: &AppHandle) -> Result<PathBuf, String> {
    let resources = app
        .path()
        .resource_dir()
        .map_err(|error| error.to_string())?;
    let archive = resources.join("resources").join(ARCHIVE_NAME);
    let manifest = resources.join("resources").join(MANIFEST_NAME);
    let checksum = resources.join("resources").join(CHECKSUM_NAME);
    if !archive.is_file() || !manifest.is_file() || !checksum.is_file() {
        return Err(format!(
            "packaged Javis runtime resources are missing from {}",
            resources.display()
        ));
    }

    let slot_root = runtime_slot_root(app)?;
    fs::create_dir_all(&slot_root).map_err(|error| error.to_string())?;
    let runtime = slot_root.join("runtime");
    let packaged_manifest = fs::read_to_string(&manifest).map_err(|error| error.to_string())?;
    if runtime_is_ready(&runtime, &packaged_manifest) {
        return Ok(runtime);
    }

    let incoming = slot_root.join("runtime.new");
    if runtime_is_ready(&incoming, &packaged_manifest) {
        activate_incoming(&slot_root, &runtime, &incoming)?;
        return Ok(runtime);
    }

    verify_checksum(&archive, &checksum)?;
    remove_generated_dir(&incoming, &slot_root)?;
    fs::create_dir_all(&incoming).map_err(|error| error.to_string())?;

    let output = hidden_command("tar.exe")
        .args(["-m", "-xf"])
        .arg(&archive)
        .arg("-C")
        .arg(&incoming)
        .output()
        .map_err(|error| format!("failed to run Windows tar.exe: {error}"))?;
    if !output.status.success() {
        let _ = fs::remove_dir_all(&incoming);
        return Err(format!(
            "runtime extraction failed: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }

    validate_runtime(&incoming)?;
    run_import_probe(&incoming)?;
    fs::write(incoming.join(VERSION_MARKER), &packaged_manifest)
        .map_err(|error| error.to_string())?;

    activate_incoming(&slot_root, &runtime, &incoming)?;
    Ok(runtime)
}

fn runtime_is_ready(root: &Path, packaged_manifest: &str) -> bool {
    let marker = root.join(VERSION_MARKER);
    marker.is_file()
        && fs::read_to_string(marker).unwrap_or_default() == packaged_manifest
        && validate_runtime(root).is_ok()
}

fn activate_incoming(slot_root: &Path, runtime: &Path, incoming: &Path) -> Result<(), String> {
    stop_stale_packaged_python(runtime)?;

    let previous = slot_root.join("runtime.previous");
    remove_generated_dir(&previous, slot_root)?;
    if runtime.exists() {
        fs::rename(&runtime, &previous)
            .map_err(|error| format!("failed to preserve previous runtime: {error}"))?;
    }
    if let Err(error) = fs::rename(&incoming, &runtime) {
        if previous.exists() && !runtime.exists() {
            let _ = fs::rename(&previous, &runtime);
        }
        return Err(format!("failed to activate runtime {VERSION}: {error}"));
    }
    Ok(())
}

#[cfg(windows)]
fn stop_stale_packaged_python(runtime: &Path) -> Result<(), String> {
    if !runtime.join("python").join("python.exe").is_file() {
        return Ok(());
    }
    let script = r#"
$ErrorActionPreference = 'Stop'
$expected = [IO.Path]::GetFullPath((Join-Path $env:JAVIS_RUNTIME_ROOT 'python\python.exe'))
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.ExecutablePath -and ([IO.Path]::GetFullPath($_.ExecutablePath) -eq $expected) } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop }
"#;
    let output = hidden_command("powershell.exe")
        .args(["-NoProfile", "-NonInteractive", "-Command", script])
        .env("JAVIS_RUNTIME_ROOT", runtime)
        .output()
        .map_err(|error| format!("failed to stop stale Javis runtime: {error}"))?;
    if !output.status.success() {
        return Err(format!(
            "failed to stop stale Javis runtime: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    Ok(())
}

#[cfg(not(windows))]
fn stop_stale_packaged_python(_runtime: &Path) -> Result<(), String> {
    Ok(())
}

fn verify_checksum(archive: &Path, checksum_file: &Path) -> Result<(), String> {
    let expected = fs::read_to_string(checksum_file)
        .map_err(|error| error.to_string())?
        .split_whitespace()
        .next()
        .unwrap_or("")
        .to_ascii_lowercase();
    if expected.len() != 64 {
        return Err("invalid packaged runtime checksum".to_string());
    }
    let output = hidden_command("certutil.exe")
        .args(["-hashfile"])
        .arg(archive)
        .arg("SHA256")
        .output()
        .map_err(|error| format!("failed to run certutil.exe: {error}"))?;
    if !output.status.success() {
        return Err("unable to verify packaged runtime checksum".to_string());
    }
    let actual = String::from_utf8_lossy(&output.stdout)
        .lines()
        .map(|line| {
            line.chars()
                .filter(|ch| ch.is_ascii_hexdigit())
                .collect::<String>()
        })
        .find(|line| line.len() == 64)
        .unwrap_or_default()
        .to_ascii_lowercase();
    if actual != expected {
        return Err("packaged runtime checksum mismatch".to_string());
    }
    Ok(())
}

fn validate_runtime(root: &Path) -> Result<(), String> {
    let required = [
        root.join("python").join("python.exe"),
        root.join("app").join("main.py"),
        root.join("app")
            .join("models")
            .join("faster-whisper-base")
            .join("model.bin"),
        root.join("app")
            .join("tools")
            .join("Tesseract-OCR")
            .join("tesseract.exe"),
        root.join("app").join("core").join("agent.py"),
        root.join("app").join("voice").join("native_capture.py"),
        root.join("python")
            .join("Lib")
            .join("site-packages")
            .join("pyaudio")
            .join("_portaudio.cp311-win_amd64.pyd"),
        root.join("app").join("blueprint").join("audit.py"),
        root.join("app").join("web").join("index.html"),
    ];
    for path in required {
        if !path.is_file() {
            return Err(format!(
                "packaged runtime is incomplete: {}",
                path.display()
            ));
        }
    }
    Ok(())
}

fn run_import_probe(root: &Path) -> Result<(), String> {
    let python = root.join("python").join("python.exe");
    let app_root = root.join("app");
    let output = hidden_command(&python)
        .args([
            "-c",
            "import fastapi,uvicorn,httpx,faster_whisper,pytesseract,edge_tts,av,ctranslate2,win32com.client,pyaudio;from core.agent import Agent;from voice.native_capture import get_diagnostics;print('JAVIS_RUNTIME_OK')",
        ])
        .current_dir(&app_root)
        .env("JAVIS_TEST_MODE", "1")
        .env("JAVIS_DISABLE_STARTUP_SIDE_EFFECTS", "1")
        .output()
        .map_err(|error| format!("failed to probe packaged Python: {error}"))?;
    if !output.status.success()
        || !String::from_utf8_lossy(&output.stdout).contains("JAVIS_RUNTIME_OK")
    {
        return Err(format!(
            "packaged Python import probe failed: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    Ok(())
}

fn remove_generated_dir(path: &Path, slot_root: &Path) -> Result<(), String> {
    if !path.exists() {
        return Ok(());
    }
    let resolved_parent = path.parent().unwrap_or(Path::new(""));
    if resolved_parent != slot_root {
        return Err(format!(
            "refusing to remove path outside runtime slots: {}",
            path.display()
        ));
    }
    fs::remove_dir_all(path).map_err(|error| error.to_string())
}

fn reject_reparse_chain(path: &Path) -> Result<(), String> {
    for ancestor in path.ancestors() {
        let metadata = fs::symlink_metadata(ancestor).map_err(|error| error.to_string())?;
        if metadata.file_type().is_symlink() || is_windows_reparse(&metadata) {
            return Err("JAVIS_DATA_ROOT must not traverse a symlink or reparse point".to_string());
        }
    }
    Ok(())
}

#[cfg(windows)]
fn is_windows_reparse(metadata: &fs::Metadata) -> bool {
    use std::os::windows::fs::MetadataExt;
    metadata.file_attributes() & 0x400 != 0
}

#[cfg(not(windows))]
fn is_windows_reparse(_metadata: &fs::Metadata) -> bool {
    false
}
