use crate::{app_log, process_command::hidden_command};
use std::{
    io::{Read, Write},
    net::{SocketAddr, TcpStream},
    path::PathBuf,
    process::{Child, Stdio},
    sync::Mutex,
    thread,
    time::Duration,
};
use tauri::AppHandle;

const OLLAMA_PORT: u16 = 11435;
const STARTUP_POLLS: usize = 60;

#[derive(Default)]
struct OllamaRuntime {
    child: Option<Child>,
    attached_existing: bool,
    last_error: String,
}

pub struct BundledOllama {
    data_root: PathBuf,
    runtime: Mutex<OllamaRuntime>,
}

impl BundledOllama {
    pub fn new(data_root: PathBuf) -> Self {
        Self {
            data_root,
            runtime: Mutex::new(OllamaRuntime::default()),
        }
    }

    fn configured_root(&self) -> PathBuf {
        let pointer = self.data_root.join("local-ai-root.txt");
        if let Ok(value) = std::fs::read_to_string(pointer) {
            let selected = PathBuf::from(value.trim());
            if selected.is_absolute() && selected.parent().is_some() {
                return selected;
            }
        }
        self.data_root.clone()
    }

    pub fn executable_path(&self) -> PathBuf {
        self.configured_root()
            .join("local-ai")
            .join("ollama")
            .join("ollama.exe")
    }

    pub fn model_root(&self) -> PathBuf {
        self.configured_root().join("local-ai").join("models")
    }

    pub fn openai_base_url(&self) -> &'static str {
        "http://127.0.0.1:11435/v1"
    }

    pub fn is_installed(&self) -> bool {
        self.executable_path().is_file() && self.model_root().is_dir()
    }

    pub fn status(&self) -> String {
        if probe_ollama() {
            let runtime = self.runtime.lock().unwrap_or_else(|error| error.into_inner());
            if runtime.attached_existing {
                "attached".to_string()
            } else {
                "healthy".to_string()
            }
        } else if !self.is_installed() {
            "not-installed".to_string()
        } else {
            "offline".to_string()
        }
    }

    pub fn start(&self, app: &AppHandle) -> Result<String, String> {
        if probe_ollama() {
            let mut runtime = self.runtime.lock().map_err(|error| error.to_string())?;
            runtime.attached_existing = true;
            runtime.last_error.clear();
            return Ok("attached".to_string());
        }
        if !self.is_installed() {
            return Ok("not-installed".to_string());
        }
        let model_root = self.model_root();
        std::fs::create_dir_all(&model_root).map_err(|error| error.to_string())?;
        let (stdout, stderr) = app_log::ollama_log_files(app)?;
        let mut command = hidden_command(self.executable_path());
        command
            .arg("serve")
            .env("OLLAMA_HOST", format!("127.0.0.1:{OLLAMA_PORT}"))
            .env("OLLAMA_MODELS", &model_root)
            .env("OLLAMA_KEEP_ALIVE", "10m")
            .stdout(Stdio::from(stdout))
            .stderr(Stdio::from(stderr));
        let child = command
            .spawn()
            .map_err(|error| format!("failed to start bundled Ollama: {error}"))?;
        {
            let mut runtime = self.runtime.lock().map_err(|error| error.to_string())?;
            runtime.child = Some(child);
            runtime.attached_existing = false;
            runtime.last_error.clear();
        }
        for _ in 0..STARTUP_POLLS {
            thread::sleep(Duration::from_millis(500));
            if probe_ollama() {
                return Ok("healthy".to_string());
            }
        }
        let message = "bundled Ollama startup timed out".to_string();
        if let Ok(mut runtime) = self.runtime.lock() {
            runtime.last_error = message.clone();
        }
        let _ = self.stop_owned();
        Err(message)
    }

    pub fn stop_owned(&self) -> Result<(), String> {
        let mut runtime = self.runtime.lock().map_err(|error| error.to_string())?;
        if let Some(child) = runtime.child.as_mut() {
            let _ = child.kill();
            let _ = child.wait();
        }
        runtime.child = None;
        runtime.attached_existing = false;
        Ok(())
    }
}

fn probe_ollama() -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], OLLAMA_PORT));
    let mut stream = match TcpStream::connect_timeout(&address, Duration::from_millis(400)) {
        Ok(stream) => stream,
        Err(_) => return false,
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(700)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(700)));
    let request = format!(
        "GET /api/tags HTTP/1.1\r\nHost: 127.0.0.1:{OLLAMA_PORT}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut response = String::new();
    stream.read_to_string(&mut response).is_ok()
        && (response.starts_with("HTTP/1.1 200") || response.starts_with("HTTP/1.0 200"))
        && response.contains("\"models\"")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    #[test]
    fn bundled_runtime_paths_are_scoped_to_app_data() {
        let manager = BundledOllama::new(PathBuf::from(r"D:\Javis-v3-data-test"));

        assert_eq!(
            manager.executable_path(),
            PathBuf::from(r"D:\Javis-v3-data-test\local-ai\ollama\ollama.exe")
        );
        assert_eq!(
            manager.model_root(),
            PathBuf::from(r"D:\Javis-v3-data-test\local-ai\models")
        );
        assert_eq!(manager.openai_base_url(), "http://127.0.0.1:11435/v1");
    }

    #[test]
    fn selected_local_ai_root_is_loaded_from_pointer() {
        let base = std::env::temp_dir().join(format!("javis-ollama-root-{}", std::process::id()));
        let selected = base.join("selected");
        std::fs::create_dir_all(&base).unwrap();
        std::fs::write(base.join("local-ai-root.txt"), selected.to_string_lossy().as_bytes()).unwrap();
        let manager = BundledOllama::new(base.clone());

        assert_eq!(manager.model_root(), selected.join("local-ai").join("models"));
        assert_eq!(manager.executable_path(), selected.join("local-ai").join("ollama").join("ollama.exe"));

        let _ = std::fs::remove_dir_all(base);
    }
}
