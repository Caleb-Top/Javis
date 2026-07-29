use crate::app_log;
use std::{
    env,
    io::{Read, Write},
    net::{SocketAddr, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tauri::AppHandle;

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x08000000;
#[cfg(not(target_os = "windows"))]
const CREATE_NO_WINDOW: u32 = 0;

pub const MAX_RESTART_ATTEMPTS: u8 = 3;
const PORT: u16 = 8080;
const STARTUP_POLLS: usize = 20;

enum ProbeResult {
    Ready,
    PortConflict,
    Offline,
}

struct SidecarRuntime {
    child: Option<Child>,
    owned_pid: Option<u32>,
    ownership_token: Option<String>,
    attached_existing: bool,
    restart_attempts: u8,
    last_error: String,
}

impl Default for SidecarRuntime {
    fn default() -> Self {
        Self {
            child: None,
            owned_pid: None,
            ownership_token: None,
            attached_existing: false,
            restart_attempts: 0,
            last_error: String::new(),
        }
    }
}

pub struct SidecarManager {
    runtime: Mutex<SidecarRuntime>,
    packaged_root: Option<PathBuf>,
}

impl Default for SidecarManager {
    fn default() -> Self {
        Self {
            runtime: Mutex::new(SidecarRuntime::default()),
            packaged_root: None,
        }
    }
}

impl SidecarManager {
    pub fn with_root(packaged_root: PathBuf) -> Self {
        Self {
            runtime: Mutex::new(SidecarRuntime::default()),
            packaged_root: Some(packaged_root),
        }
    }

    pub fn status_json(&self) -> String {
        let probe = probe_backend(PORT);
        let runtime = self
            .runtime
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        let state = match probe {
            ProbeResult::Ready if runtime.attached_existing => "attached",
            ProbeResult::Ready => "healthy",
            ProbeResult::PortConflict => "port-conflict",
            ProbeResult::Offline if runtime.owned_pid.is_some() => "degraded",
            ProbeResult::Offline => "offline",
        };
        format!(
            "{{\"state\":\"{}\",\"owned\":{},\"owned_pid\":{},\"restart_attempts\":{},\"last_error\":\"{}\"}}",
            state,
            runtime.owned_pid.is_some(),
            runtime.owned_pid.map(|pid| pid.to_string()).unwrap_or_else(|| "null".to_string()),
            runtime.restart_attempts,
            json_escape(&runtime.last_error),
        )
    }

    pub fn start(&self, app: &AppHandle) -> Result<String, String> {
        match probe_backend(PORT) {
            ProbeResult::Ready => {
                {
                    let mut runtime = self.runtime.lock().map_err(|error| error.to_string())?;
                    runtime.attached_existing = true;
                    runtime.last_error.clear();
                }
                return Ok(self.status_json());
            }
            ProbeResult::PortConflict => {
                return Err("port 8080 is occupied by a non-Javis service".to_string())
            }
            ProbeResult::Offline => {}
        }

        let (root, python) = if let Some(packaged_root) = self.packaged_root.as_ref() {
            (
                packaged_root.join("app"),
                packaged_root.join("python").join("python.exe"),
            )
        } else {
            let root = find_javis_root()
                .ok_or_else(|| "Javis runtime root not found; set JAVIS_ROOT".to_string())?;
            let python = find_python(&root);
            (root, python)
        };
        let entry = root.join("main.py");
        if !entry.is_file() {
            return Err(format!("missing runtime entry: {}", entry.display()));
        }
        let (stdout, stderr) = app_log::runtime_log_files(app)?;
        let token = ownership_token();
        let mut command = Command::new(&python);
        command
            .arg("-u")
            .arg(&entry)
            .current_dir(&root)
            .env("PORT", PORT.to_string())
            .env("JAVIS_SIDECAR_OWNERSHIP", &token)
            .env("JAVIS_DATA_ROOT", &root)
            .stdout(Stdio::from(stdout))
            .stderr(Stdio::from(stderr));
        #[cfg(target_os = "windows")]
        command.creation_flags(CREATE_NO_WINDOW);
        let child = command.spawn().map_err(|error| {
            format!("failed to start runtime with {}: {error}", python.display())
        })?;
        let pid = child.id();
        {
            let mut runtime = self.runtime.lock().map_err(|error| error.to_string())?;
            runtime.child = Some(child);
            runtime.owned_pid = Some(pid);
            runtime.ownership_token = Some(token);
            runtime.attached_existing = false;
            runtime.last_error.clear();
        }

        for _ in 0..STARTUP_POLLS {
            thread::sleep(Duration::from_millis(400));
            if matches!(probe_backend(PORT), ProbeResult::Ready) {
                return Ok(self.status_json());
            }
        }
        let message = "Javis Sidecar startup timed out".to_string();
        if let Ok(mut runtime) = self.runtime.lock() {
            runtime.last_error = message.clone();
        }
        let _ = self.stop_owned();
        Err(message)
    }

    pub fn stop_owned(&self) -> Result<String, String> {
        let mut runtime = self.runtime.lock().map_err(|error| error.to_string())?;
        if runtime.ownership_token.is_none() || runtime.owned_pid.is_none() {
            runtime.attached_existing = false;
            return Ok("no owned Sidecar process".to_string());
        }
        if let Some(child) = runtime.child.as_mut() {
            let _ = child.kill();
            let _ = child.wait();
        }
        runtime.child = None;
        runtime.owned_pid = None;
        runtime.ownership_token = None;
        runtime.attached_existing = false;
        Ok("owned Sidecar stopped".to_string())
    }

    pub fn restart(&self, app: &AppHandle) -> Result<String, String> {
        {
            let mut runtime = self.runtime.lock().map_err(|error| error.to_string())?;
            if runtime.restart_attempts >= MAX_RESTART_ATTEMPTS {
                return Err("Sidecar restart limit reached".to_string());
            }
            runtime.restart_attempts += 1;
        }
        self.stop_owned()?;
        self.start(app)
    }
}

fn probe_backend(port: u16) -> ProbeResult {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let mut stream = match TcpStream::connect_timeout(&address, Duration::from_millis(600)) {
        Ok(stream) => stream,
        Err(_) => return ProbeResult::Offline,
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(800)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(800)));
    let request =
        format!("GET /api/status HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n");
    if stream.write_all(request.as_bytes()).is_err() {
        return ProbeResult::PortConflict;
    }
    let mut response = String::new();
    if stream.read_to_string(&mut response).is_err() {
        return ProbeResult::PortConflict;
    }
    let is_ok = response.starts_with("HTTP/1.1 200") || response.starts_with("HTTP/1.0 200");
    if is_ok && response.contains("service\":\"javis") {
        ProbeResult::Ready
    } else {
        ProbeResult::PortConflict
    }
}

fn find_javis_root() -> Option<PathBuf> {
    if let Ok(configured) = env::var("JAVIS_ROOT") {
        let path = PathBuf::from(configured);
        if path.join("main.py").is_file() {
            return Some(path);
        }
    }
    let mut candidates = Vec::new();
    if let Ok(current) = env::current_dir() {
        candidates.push(current);
    }
    if let Ok(executable) = env::current_exe() {
        if let Some(parent) = executable.parent() {
            candidates.push(parent.to_path_buf());
        }
    }
    for candidate in candidates {
        for ancestor in candidate.ancestors() {
            if ancestor.join("main.py").is_file() {
                return Some(ancestor.to_path_buf());
            }
        }
    }
    None
}

fn find_python(root: &Path) -> PathBuf {
    if let Ok(configured) = env::var("JAVIS_PYTHON") {
        return PathBuf::from(configured);
    }
    let candidates = [
        root.join("venv").join("Scripts").join("python.exe"),
        root.join("python-embed").join("python.exe"),
    ];
    candidates
        .into_iter()
        .find(|path| path.is_file())
        .unwrap_or_else(|| PathBuf::from("python"))
}

fn ownership_token() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    format!("javis-app-{}-{nanos}", std::process::id())
}

fn json_escape(value: &str) -> String {
    value
        .replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('\n', "\\n")
}
