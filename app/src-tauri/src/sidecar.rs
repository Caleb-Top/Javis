use crate::{app_log, bundled_ollama::BundledOllama};
use std::{
    env, fs,
    io::{Read, Write},
    net::{SocketAddr, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{Arc, Mutex},
    thread,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tauri::{AppHandle, Manager};

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x08000000;
#[cfg(not(target_os = "windows"))]
const CREATE_NO_WINDOW: u32 = 0;

pub const MAX_RESTART_ATTEMPTS: u8 = 3;
const PORT: u16 = 8080;
const STARTUP_POLLS: usize = 20;

#[path = "sidecar_shutdown.rs"]
mod sidecar_shutdown;
use sidecar_shutdown::{stop_owned_process, OwnedProcess, SHUTDOWN_POLLS};

enum ProbeResult {
    Ready,
    PortConflict,
    Offline,
}

impl OwnedProcess for Child {
    fn has_exited(&mut self) -> Result<bool, String> {
        self.try_wait()
            .map(|status| status.is_some())
            .map_err(|error| format!("failed to poll owned Sidecar: {error}"))
    }

    fn force_kill(&mut self) -> Result<(), String> {
        if self.has_exited()? {
            return Ok(());
        }
        self.kill()
            .map_err(|error| format!("failed to kill owned Sidecar: {error}"))
    }

    fn wait_for_exit(&mut self) -> Result<(), String> {
        self.wait()
            .map(|_| ())
            .map_err(|error| format!("failed to wait for owned Sidecar: {error}"))
    }
}

struct SidecarRuntime {
    child: Option<Child>,
    owned_pid: Option<u32>,
    ownership_token: Option<String>,
    attached_existing: bool,
    restart_attempts: u8,
    last_error: String,
    ollama_error: String,
    ollama_startup: String,
    ollama_start_generation: u64,
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
            ollama_error: String::new(),
            ollama_startup: "idle".to_string(),
            ollama_start_generation: 0,
        }
    }
}

pub struct SidecarManager {
    runtime: Arc<Mutex<SidecarRuntime>>,
    packaged_root: Option<PathBuf>,
    ollama: Arc<BundledOllama>,
}

impl Default for SidecarManager {
    fn default() -> Self {
        Self {
            runtime: Arc::new(Mutex::new(SidecarRuntime::default())),
            packaged_root: None,
            ollama: Arc::new(BundledOllama::new(PathBuf::new())),
        }
    }
}

impl SidecarManager {
    pub fn with_root(packaged_root: PathBuf) -> Self {
        let data_root = packaged_root
            .parent()
            .map(Path::to_path_buf)
            .unwrap_or_default();
        Self {
            runtime: Arc::new(Mutex::new(SidecarRuntime::default())),
            packaged_root: Some(packaged_root),
            ollama: Arc::new(BundledOllama::new(data_root)),
        }
    }

    pub fn status_json(&self) -> String {
        let probe = probe_backend(PORT);
        let ollama_state = self.ollama.status();
        let runtime = self
            .runtime
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        let ollama_startup = match ollama_state.as_str() {
            "healthy" | "attached" => "ready",
            "not-installed" => "not-installed",
            _ => runtime.ollama_startup.as_str(),
        };
        let ollama_recovery = match ollama_startup {
            "ready" => "none",
            "not-installed" => "open-model-settings",
            "starting" => "background-starting",
            _ => "restart-sidecar",
        };
        let state = match probe {
            ProbeResult::Ready if runtime.attached_existing => "attached",
            ProbeResult::Ready => "healthy",
            ProbeResult::PortConflict => "port-conflict",
            ProbeResult::Offline if runtime.owned_pid.is_some() => "degraded",
            ProbeResult::Offline => "offline",
        };
        format!(
            "{{\"state\":\"{}\",\"owned\":{},\"owned_pid\":{},\"restart_attempts\":{},\"last_error\":\"{}\",\"ollama\":\"{}\",\"ollama_installed\":{},\"ollama_base_url\":\"{}\",\"ollama_startup\":\"{}\",\"ollama_recovery\":\"{}\",\"ollama_error\":\"{}\"}}",
            state,
            runtime.owned_pid.is_some(),
            runtime.owned_pid.map(|pid| pid.to_string()).unwrap_or_else(|| "null".to_string()),
            runtime.restart_attempts,
            json_escape(&runtime.last_error),
            ollama_state,
            self.ollama.is_installed(),
            self.ollama.openai_base_url(),
            ollama_startup,
            ollama_recovery,
            json_escape(&runtime.ollama_error),
        )
    }

    fn ensure_ollama_started(&self, app: &AppHandle) -> String {
        let current = self.ollama.status();
        if matches!(current.as_str(), "healthy" | "attached") {
            if let Ok(mut runtime) = self.runtime.lock() {
                runtime.ollama_startup = "ready".to_string();
                runtime.ollama_error.clear();
            }
            return "ready".to_string();
        }
        if current == "not-installed" {
            if let Ok(mut runtime) = self.runtime.lock() {
                runtime.ollama_startup = "not-installed".to_string();
                runtime.ollama_error.clear();
            }
            return current;
        }
        let start_generation = {
            let mut runtime = self
                .runtime
                .lock()
                .unwrap_or_else(|error| error.into_inner());
            if runtime.ollama_startup == "starting" {
                return "starting".to_string();
            }
            runtime.ollama_startup = "starting".to_string();
            runtime.ollama_error.clear();
            runtime.ollama_start_generation = runtime.ollama_start_generation.saturating_add(1);
            runtime.ollama_start_generation
        };

        let ollama = Arc::clone(&self.ollama);
        let runtime = Arc::clone(&self.runtime);
        let ollama_app = app.clone();
        thread::spawn(move || match ollama.start(&ollama_app) {
            Ok(state) => {
                if let Ok(mut runtime) = runtime.lock() {
                    if runtime.ollama_start_generation == start_generation {
                        runtime.ollama_startup = if state == "not-installed" {
                            "not-installed".to_string()
                        } else {
                            "ready".to_string()
                        };
                        runtime.ollama_error.clear();
                    }
                }
                let _ = app_log::append(
                    &ollama_app,
                    "info",
                    "ollama",
                    &format!("bundled runtime: {state}"),
                );
            }
            Err(error) => {
                if let Ok(mut runtime) = runtime.lock() {
                    if runtime.ollama_start_generation == start_generation {
                        runtime.ollama_startup = "failed".to_string();
                        runtime.ollama_error = error.clone();
                    }
                }
                let _ = app_log::append(&ollama_app, "warn", "ollama", &error);
            }
        });
        "starting".to_string()
    }

    pub fn start(&self, app: &AppHandle) -> Result<String, String> {
        match probe_backend(PORT) {
            ProbeResult::Ready => {
                self.ensure_ollama_started(app);
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
        let data_root = app
            .path()
            .app_data_dir()
            .map_err(|error| format!("failed to resolve Javis data root: {error}"))?
            .join("runtime-data");
        fs::create_dir_all(&data_root)
            .map_err(|error| format!("failed to create Javis data root: {error}"))?;
        let mut command = Command::new(&python);
        command
            .arg("-u")
            .arg(&entry)
            .current_dir(&root)
            .env("PORT", PORT.to_string())
            .env("JAVIS_SIDECAR_OWNERSHIP", &token)
            .env("JAVIS_DATA_ROOT", &data_root)
            .env("JAVIS_BUNDLED_OLLAMA_URL", self.ollama.openai_base_url())
            .env("OLLAMA_MODELS", self.ollama.model_root())
            .stdout(Stdio::from(stdout))
            .stderr(Stdio::from(stderr));
        #[cfg(target_os = "windows")]
        command.creation_flags(CREATE_NO_WINDOW);
        let child = match command.spawn() {
            Ok(child) => child,
            Err(error) => {
                let _ = self.ollama.stop_owned();
                return Err(format!(
                    "failed to start runtime with {}: {error}",
                    python.display()
                ));
            }
        };
        let pid = child.id();
        {
            let mut runtime = self.runtime.lock().map_err(|error| error.to_string())?;
            runtime.child = Some(child);
            runtime.owned_pid = Some(pid);
            runtime.ownership_token = Some(token);
            runtime.attached_existing = false;
            runtime.last_error.clear();
        }
        self.ensure_ollama_started(app);

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
        let owned = {
            let mut runtime = self.runtime.lock().map_err(|error| error.to_string())?;
            runtime.ollama_start_generation = runtime.ollama_start_generation.saturating_add(1);
            runtime.ollama_startup = "idle".to_string();
            runtime.ollama_error.clear();
            if runtime.ownership_token.is_none() || runtime.owned_pid.is_none() {
                runtime.attached_existing = false;
                None
            } else {
                let child = runtime.child.take();
                let token = runtime.ownership_token.take();
                let pid = runtime.owned_pid.take();
                runtime.attached_existing = false;
                match (child, token, pid) {
                    (Some(child), Some(token), Some(pid)) => Some((child, token, pid)),
                    _ => None,
                }
            }
        };
        let Some((mut child, token, pid)) = owned else {
            self.ollama.stop_owned()?;
            return Ok("no owned Sidecar process".to_string());
        };
        let stop_result = stop_owned_process(
            &mut child,
            &token,
            |ownership| request_runtime_shutdown(PORT, ownership),
            thread::sleep,
            SHUTDOWN_POLLS,
        );
        let mode = match stop_result {
            Ok(mode) => mode,
            Err(error) => {
                let mut runtime = self.runtime.lock().map_err(|lock_error| {
                    format!("{error}; failed to restore Sidecar ownership: {lock_error}")
                })?;
                runtime.child = Some(child);
                runtime.ownership_token = Some(token);
                runtime.owned_pid = Some(pid);
                return Err(error);
            }
        };
        self.ollama.stop_owned()?;
        Ok(format!("owned Sidecar {pid} stopped via {mode:?}"))
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
    let compatible = response.contains("service\":\"javis")
        && response.contains("desktop_api_version\":2")
        && response.contains("continuous_voice\":true");
    if is_ok && compatible {
        ProbeResult::Ready
    } else {
        ProbeResult::PortConflict
    }
}

fn request_runtime_shutdown(port: u16, token: &str) -> Result<(), String> {
    if token.is_empty() || token.contains(['\r', '\n']) {
        return Err("invalid Sidecar ownership token".to_string());
    }
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_millis(600))
        .map_err(|error| format!("graceful shutdown connection failed: {error}"))?;
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .map_err(|error| format!("shutdown read timeout failed: {error}"))?;
    stream
        .set_write_timeout(Some(Duration::from_millis(800)))
        .map_err(|error| format!("shutdown write timeout failed: {error}"))?;
    let request = format!(
        "POST /api/runtime/shutdown HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nX-Javis-Sidecar-Ownership: {token}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
    );
    stream
        .write_all(request.as_bytes())
        .map_err(|error| format!("graceful shutdown request failed: {error}"))?;
    let mut response = String::new();
    stream
        .read_to_string(&mut response)
        .map_err(|error| format!("graceful shutdown response failed: {error}"))?;
    if response.starts_with("HTTP/1.1 200") || response.starts_with("HTTP/1.0 200") {
        Ok(())
    } else {
        Err("graceful shutdown was rejected".to_string())
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
