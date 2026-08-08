#![cfg_attr(not(test), windows_subsystem = "windows")]

use serde::{Deserialize, Serialize};
use std::{
    env,
    ffi::OsStr,
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    process::{Command, Stdio},
};

#[cfg(windows)]
use std::os::windows::{ffi::OsStrExt, process::CommandExt};

const EXTERNAL_BUNDLE_SCHEMA: u32 = 2;
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x08000000;
#[cfg(windows)]
const MB_ICONERROR: u32 = 0x00000010;
#[cfg(windows)]
const MB_ICONINFORMATION: u32 = 0x00000040;

#[cfg(windows)]
#[link(name = "user32")]
extern "system" {
    fn MessageBoxW(hwnd: isize, text: *const u16, caption: *const u16, kind: u32) -> i32;
}

#[derive(Debug, Deserialize, Serialize)]
struct BundleIndex {
    schema_version: u32,
    payloads: Vec<PayloadEntry>,
}

#[derive(Debug, Deserialize, Serialize)]
struct PayloadEntry {
    name: String,
    path: String,
    size: u64,
    sha256: String,
}

#[derive(Debug)]
struct InstallOptions {
    silent: bool,
    validate_only: bool,
    install_root: Option<PathBuf>,
    data_root: PathBuf,
}

fn validate_payload_entry(payload: &PayloadEntry) -> Result<(), String> {
    if payload.name.is_empty()
        || Path::new(&payload.name).file_name() != Some(OsStr::new(&payload.name))
        || payload.name.contains('/')
        || payload.name.contains('\\')
    {
        return Err(format!("payload name is invalid: {}", payload.name));
    }
    let normalized = payload.path.replace('\\', "/");
    if normalized != format!("payloads/{}", payload.name) {
        return Err(format!("payload path is invalid: {}", payload.path));
    }
    if payload.sha256.len() != 64 || !payload.sha256.chars().all(|ch| ch.is_ascii_hexdigit()) {
        return Err(format!("payload checksum is invalid: {}", payload.name));
    }
    Ok(())
}

fn read_bundle_index(manifest: &Path) -> Result<BundleIndex, String> {
    let encoded = fs::read(manifest)
        .map_err(|error| format!("failed to read installer manifest {}: {error}", manifest.display()))?;
    let index: BundleIndex = serde_json::from_slice(&encoded).map_err(|error| error.to_string())?;
    if index.schema_version != EXTERNAL_BUNDLE_SCHEMA {
        return Err(format!("unsupported offline installer schema: {}", index.schema_version));
    }
    if index.payloads.is_empty() {
        return Err("offline installer manifest has no payloads".to_string());
    }
    for payload in &index.payloads {
        validate_payload_entry(payload)?;
    }
    Ok(index)
}

fn parse_options() -> Result<InstallOptions, String> {
    let mut silent = false;
    let mut validate_only = false;
    let mut install_root = None;
    let mut data_root = None;
    for argument in env::args().skip(1) {
        if argument.eq_ignore_ascii_case("/S") {
            silent = true;
        } else if argument.eq_ignore_ascii_case("--validate-bundle") {
            silent = true;
            validate_only = true;
        } else if let Some(value) = argument.strip_prefix("/D=") {
            install_root = Some(PathBuf::from(value));
        } else if let Some(value) = argument.strip_prefix("/DATA=") {
            data_root = Some(PathBuf::from(value));
        }
    }
    let data_root = data_root
        .or_else(|| env::var_os("JAVIS_APP_DATA_ROOT").map(PathBuf::from))
        .or_else(|| {
            env::var_os("LOCALAPPDATA")
                .map(PathBuf::from)
                .map(|path| path.join("local.javis.desktop"))
        })
        .ok_or_else(|| "LOCALAPPDATA is unavailable".to_string())?;
    if !data_root.is_absolute() {
        return Err(format!("Javis data directory must be absolute: {}", data_root.display()));
    }
    if let Some(path) = &install_root {
        if !path.is_absolute() {
            return Err(format!("Javis install directory must be absolute: {}", path.display()));
        }
    }
    Ok(InstallOptions {
        silent,
        validate_only,
        install_root,
        data_root,
    })
}

fn hidden_command(program: impl AsRef<OsStr>) -> Command {
    let mut command = Command::new(program);
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    command
}

fn append_log(data_root: &Path, message: &str) {
    let log = data_root.join("logs").join("installer.log");
    if let Some(parent) = log.parent() {
        let _ = fs::create_dir_all(parent);
    }
    if let Ok(mut stream) = OpenOptions::new().create(true).append(true).open(log) {
        let _ = writeln!(stream, "{message}");
    }
}

fn required_payload<'a>(index: &'a BundleIndex, name: &str) -> Result<&'a PayloadEntry, String> {
    index
        .payloads
        .iter()
        .find(|payload| payload.name == name)
        .ok_or_else(|| format!("required installer payload is missing: {name}"))
}

fn payloads_match(current: &BundleIndex, receipt: &BundleIndex) -> bool {
    current
        .payloads
        .iter()
        .filter(|payload| payload.name != "app-setup.exe")
        .all(|payload| {
            receipt.payloads.iter().any(|installed| {
                installed.name == payload.name
                    && installed.size == payload.size
                    && installed.sha256.eq_ignore_ascii_case(&payload.sha256)
            })
        })
}

fn resolve_payload(bundle_root: &Path, payload: &PayloadEntry) -> Result<PathBuf, String> {
    validate_payload_entry(payload)?;
    let target = bundle_root.join("payloads").join(&payload.name);
    let metadata = fs::metadata(&target)
        .map_err(|error| format!("installer payload is missing {}: {error}", target.display()))?;
    if !metadata.is_file() {
        return Err(format!("installer payload is not a file: {}", target.display()));
    }
    if metadata.len() != payload.size {
        return Err(format!(
            "installer payload size mismatch: {}; expected {}, got {}",
            payload.name,
            payload.size,
            metadata.len()
        ));
    }
    verify_sha256(&target, &payload.sha256)?;
    Ok(target)
}

fn verify_sha256(path: &Path, expected: &str) -> Result<(), String> {
    let output = hidden_command("certutil.exe")
        .args(["-hashfile"])
        .arg(path)
        .arg("SHA256")
        .output()
        .map_err(|error| format!("failed to run certutil: {error}"))?;
    if !output.status.success() {
        return Err(format!("failed to verify payload: {}", path.display()));
    }
    let actual = String::from_utf8_lossy(&output.stdout)
        .lines()
        .map(|line| line.chars().filter(|ch| ch.is_ascii_hexdigit()).collect::<String>())
        .find(|line| line.len() == 64)
        .unwrap_or_default();
    if !actual.eq_ignore_ascii_case(expected) {
        return Err(format!("payload checksum mismatch: {}", path.display()));
    }
    Ok(())
}

fn run_nsis(setup: &Path, options: &InstallOptions) -> Result<(), String> {
    let mut command = Command::new(setup);
    if options.silent {
        command.arg("/S");
    }
    if let Some(root) = &options.install_root {
        command.arg(format!("/D={}", root.display()));
    }
    let status = command
        .status()
        .map_err(|error| format!("failed to launch Javis App installer: {error}"))?;
    if !status.success() {
        return Err(format!("Javis App installer failed with {status}"));
    }
    Ok(())
}

fn extract_archive(archive: &Path, data_root: &Path) -> Result<(), String> {
    fs::create_dir_all(data_root).map_err(|error| error.to_string())?;
    let output = hidden_command("tar.exe")
        .args(["-m", "-xf"])
        .arg(archive)
        .arg("-C")
        .arg(data_root)
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .output()
        .map_err(|error| format!("failed to run Windows tar.exe: {error}"))?;
    if !output.status.success() {
        return Err(format!(
            "payload extraction failed: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    Ok(())
}

fn install() -> Result<bool, String> {
    let options = parse_options()?;
    let executable = env::current_exe().map_err(|error| error.to_string())?;
    let bundle_root = executable
        .parent()
        .ok_or_else(|| "installer directory is unavailable".to_string())?;
    let manifest = executable.with_extension("manifest.json");
    let index = read_bundle_index(&manifest)?;

    if options.validate_only {
        for payload in &index.payloads {
            resolve_payload(bundle_root, payload)?;
        }
        return Ok(true);
    }

    fs::create_dir_all(&options.data_root).map_err(|error| error.to_string())?;
    append_log(&options.data_root, "Javis offline installation started");

    let receipt = options.data_root.join("local-ai").join("installed-bundle.json");
    let ollama = options.data_root.join("local-ai").join("ollama").join("ollama.exe");
    let model_manifest = options
        .data_root
        .join("local-ai")
        .join("models")
        .join("manifests")
        .join("registry.ollama.ai")
        .join("library")
        .join("deepseek-r1")
        .join("8b");
    let payloads_installed = fs::read(&receipt)
        .ok()
        .and_then(|encoded| serde_json::from_slice::<BundleIndex>(&encoded).ok())
        .map(|installed| payloads_match(&index, &installed))
        .unwrap_or(false)
        && ollama.is_file()
        && model_manifest.is_file();

    let setup_entry = required_payload(&index, "app-setup.exe")?;
    let setup = resolve_payload(bundle_root, setup_entry)?;
    run_nsis(&setup, &options)?;

    if !payloads_installed {
        let ollama_entry = required_payload(&index, "ollama-runtime.zip")?;
        let ollama_archive = resolve_payload(bundle_root, ollama_entry)?;
        extract_archive(&ollama_archive, &options.data_root)?;
        if !ollama.is_file() {
            return Err("bundled Ollama runtime did not install correctly".to_string());
        }

        let model_entry = required_payload(&index, "deepseek-r1-8b.zip")?;
        let model_archive = resolve_payload(bundle_root, model_entry)?;
        extract_archive(&model_archive, &options.data_root)?;
        if !model_manifest.is_file() {
            return Err("bundled deepseek-r1:8b model did not install correctly".to_string());
        }
    } else {
        append_log(&options.data_root, "Bundled Ollama and R1 payloads already match; extraction skipped");
    }

    if let Some(parent) = receipt.parent() {
        fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    fs::write(
        receipt,
        serde_json::to_vec_pretty(&index).map_err(|error| error.to_string())?,
    )
    .map_err(|error| error.to_string())?;
    append_log(&options.data_root, "Javis offline installation completed");
    Ok(options.silent)
}

#[cfg(windows)]
fn message_box(message: &str, error: bool) {
    let text: Vec<u16> = OsStr::new(message).encode_wide().chain(Some(0)).collect();
    let title: Vec<u16> = OsStr::new("Javis v3.0").encode_wide().chain(Some(0)).collect();
    unsafe {
        MessageBoxW(
            0,
            text.as_ptr(),
            title.as_ptr(),
            if error { MB_ICONERROR } else { MB_ICONINFORMATION },
        );
    }
}

fn main() {
    let suppress_dialog = env::args().skip(1).any(|argument| {
        argument.eq_ignore_ascii_case("/S") || argument.eq_ignore_ascii_case("--validate-bundle")
    });
    match install() {
        Ok(silent) => {
            if !silent {
                message_box("Javis、内置 Ollama 与 deepseek-r1:8b 已完成安装。", false);
            }
        }
        Err(error) => {
            if !suppress_dialog {
                message_box(&format!("安装未完成：\n\n{error}"), true);
            }
            std::process::exit(1);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_a_hash_locked_external_payload() {
        let payload = PayloadEntry {
            name: "app-setup.exe".into(),
            path: "payloads/app-setup.exe".into(),
            size: 7,
            sha256: "a".repeat(64),
        };

        assert!(validate_payload_entry(&payload).is_ok());
    }

    #[test]
    fn rejects_a_payload_path_that_escapes_the_bundle() {
        let payload = PayloadEntry {
            name: "model.zip".into(),
            path: "../model.zip".into(),
            size: 7,
            sha256: "a".repeat(64),
        };

        let error = validate_payload_entry(&payload).unwrap_err();
        assert!(error.contains("path"));
    }

    #[test]
    fn installed_payload_receipt_matches_independent_of_json_order() {
        let current = BundleIndex {
            schema_version: EXTERNAL_BUNDLE_SCHEMA,
            payloads: vec![
                PayloadEntry {
                    name: "app-setup.exe".into(),
                    path: "payloads/app-setup.exe".into(),
                    size: 10,
                    sha256: "a".repeat(64),
                },
                PayloadEntry {
                    name: "deepseek-r1-8b.zip".into(),
                    path: "payloads/deepseek-r1-8b.zip".into(),
                    size: 50,
                    sha256: "b".repeat(64),
                },
            ],
        };
        let receipt = BundleIndex {
            schema_version: EXTERNAL_BUNDLE_SCHEMA,
            payloads: vec![
                PayloadEntry {
                    name: "deepseek-r1-8b.zip".into(),
                    path: "payloads/deepseek-r1-8b.zip".into(),
                    size: 50,
                    sha256: "b".repeat(64),
                },
                PayloadEntry {
                    name: "app-setup.exe".into(),
                    path: "payloads/app-setup.exe".into(),
                    size: 10,
                    sha256: "a".repeat(64),
                },
            ],
        };

        assert!(payloads_match(&current, &receipt));
    }
}
