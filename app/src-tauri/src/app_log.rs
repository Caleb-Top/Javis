use std::{
    env,
    fs::{self, OpenOptions},
    io::Write,
    path::PathBuf,
    time::{SystemTime, UNIX_EPOCH},
};
use tauri::{AppHandle, Manager};

const MAX_LOG_BYTES: u64 = 10 * 1024 * 1024;

pub fn logs_directory(app: &AppHandle) -> Result<PathBuf, String> {
    let root = match env::var_os("JAVIS_APP_DATA_ROOT") {
        Some(value) if PathBuf::from(&value).is_absolute() => PathBuf::from(value),
        _ => app.path().app_data_dir().map_err(|error| error.to_string())?,
    };
    let directory = root.join("logs");
    fs::create_dir_all(&directory).map_err(|error| error.to_string())?;
    Ok(directory)
}

fn log_path(app: &AppHandle) -> Result<PathBuf, String> {
    let directory = logs_directory(app)?.join("app");
    fs::create_dir_all(&directory).map_err(|error| error.to_string())?;
    Ok(directory.join("app.log"))
}

fn rotate(path: &PathBuf) -> Result<(), String> {
    if path.metadata().map(|meta| meta.len()).unwrap_or(0) < MAX_LOG_BYTES {
        return Ok(());
    }
    let rotated = path.with_extension("log.1");
    if rotated.exists() {
        fs::remove_file(&rotated).map_err(|error| error.to_string())?;
    }
    fs::rename(path, rotated).map_err(|error| error.to_string())
}

fn redact(message: &str) -> String {
    let mut redact_next = false;
    message
        .split_whitespace()
        .map(|part| {
            let lower = part.to_ascii_lowercase();
            if redact_next {
                redact_next = false;
                return "[REDACTED]".to_string();
            }
            if lower == "bearer" || lower.ends_with("authorization:") {
                redact_next = true;
                return part.to_string();
            }
            if lower.starts_with("sk-")
                || lower.contains("token=")
                || lower.contains("password=")
                || lower.contains("api_key=")
                || lower.contains("apikey=")
                || lower.contains("secret=")
            {
                return "[REDACTED]".to_string();
            }
            part.to_string()
        })
        .collect::<Vec<_>>()
        .join(" ")
}

fn json_escape(value: &str) -> String {
    value
        .replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('\n', "\\n")
        .replace('\r', "\\r")
}

pub fn append(app: &AppHandle, level: &str, component: &str, message: &str) -> Result<(), String> {
    let path = log_path(app)?;
    rotate(&path)?;
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let line = format!(
        "{{\"timestamp\":{},\"level\":\"{}\",\"component\":\"{}\",\"message\":\"{}\"}}\n",
        timestamp,
        json_escape(&level.to_ascii_lowercase()),
        json_escape(component),
        json_escape(&redact(message)),
    );
    let mut stream = OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|error| error.to_string())?;
    stream
        .write_all(line.as_bytes())
        .map_err(|error| error.to_string())
}

pub fn runtime_log_files(app: &AppHandle) -> Result<(std::fs::File, std::fs::File), String> {
    let directory = logs_directory(app)?.join("runtime");
    fs::create_dir_all(&directory).map_err(|error| error.to_string())?;
    let stdout = OpenOptions::new()
        .create(true)
        .append(true)
        .open(directory.join("sidecar.stdout.log"))
        .map_err(|error| error.to_string())?;
    let stderr = OpenOptions::new()
        .create(true)
        .append(true)
        .open(directory.join("sidecar.stderr.log"))
        .map_err(|error| error.to_string())?;
    Ok((stdout, stderr))
}

pub fn ollama_log_files(app: &AppHandle) -> Result<(std::fs::File, std::fs::File), String> {
    let directory = logs_directory(app)?.join("ollama");
    fs::create_dir_all(&directory).map_err(|error| error.to_string())?;
    let stdout = OpenOptions::new()
        .create(true)
        .append(true)
        .open(directory.join("ollama.stdout.log"))
        .map_err(|error| error.to_string())?;
    let stderr = OpenOptions::new()
        .create(true)
        .append(true)
        .open(directory.join("ollama.stderr.log"))
        .map_err(|error| error.to_string())?;
    Ok((stdout, stderr))
}
