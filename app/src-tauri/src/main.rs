#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod app_log;
mod bundled_ollama;
mod pet_actions;
mod process_command;
mod runtime_bundle;
mod screen_capture;
mod sidecar;

use sidecar::SidecarManager;
use std::sync::atomic::{AtomicBool, Ordering};
use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Emitter, Manager, RunEvent, State, WindowEvent,
};
use windows::Win32::UI::WindowsAndMessaging::{
    SetWindowPos, SWP_NOACTIVATE, SWP_NOOWNERZORDER, SWP_NOZORDER,
};

struct AppState {
    sidecar: SidecarManager,
    explicit_quit: AtomicBool,
}

impl AppState {
    fn new(sidecar: SidecarManager) -> Self {
        Self {
            sidecar,
            explicit_quit: AtomicBool::new(false),
        }
    }
}

#[tauri::command]
fn sidecar_status(state: State<'_, AppState>) -> String {
    state.sidecar.status_json()
}

#[tauri::command]
fn sidecar_start(app: AppHandle, state: State<'_, AppState>) -> Result<String, String> {
    app_log::append(&app, "info", "sidecar", "start requested")?;
    state.sidecar.start(&app)
}

#[tauri::command]
fn sidecar_stop(state: State<'_, AppState>) -> Result<String, String> {
    state.sidecar.stop_owned()
}

#[tauri::command]
fn sidecar_restart(app: AppHandle, state: State<'_, AppState>) -> Result<String, String> {
    app_log::append(&app, "warn", "sidecar", "bounded restart requested")?;
    state.sidecar.restart(&app)
}

#[tauri::command]
fn write_app_log(
    app: AppHandle,
    level: String,
    component: String,
    message: String,
) -> Result<(), String> {
    app_log::append(&app, &level, &component, &message)
}

#[tauri::command]
fn capture_screen_native() -> Result<String, String> {
    screen_capture::capture_screen_native()
}

#[tauri::command]
fn open_logs_directory(app: AppHandle) -> Result<(), String> {
    let directory = app_log::logs_directory(&app)?;
    std::process::Command::new("explorer")
        .arg(directory)
        .spawn()
        .map_err(|error| error.to_string())?;
    Ok(())
}

#[tauri::command]
fn set_main_window_bounds(
    window: tauri::WebviewWindow,
    x: i32,
    y: i32,
    width: i32,
    height: i32,
) -> Result<(), String> {
    if width <= 0 || height <= 0 {
        return Err("window bounds must be positive".into());
    }
    let hwnd = window.hwnd().map_err(|error| error.to_string())?;
    unsafe {
        SetWindowPos(
            hwnd,
            None,
            x,
            y,
            width,
            height,
            SWP_NOACTIVATE | SWP_NOOWNERZORDER | SWP_NOZORDER,
        )
        .map_err(|error| error.to_string())
    }
}

fn show_main_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

fn install_tray(app: &tauri::App) -> tauri::Result<()> {
    let show = MenuItem::with_id(app, "show", "显示 Javis", true, None::<&str>)?;
    let pause = MenuItem::with_id(app, "pause", "暂停聆听", true, None::<&str>)?;
    let restart = MenuItem::with_id(app, "restart", "重启运行时", true, None::<&str>)?;
    let diagnostics = MenuItem::with_id(app, "diagnostics", "打开诊断", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "退出 Javis", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&show, &pause, &restart, &diagnostics, &quit])?;

    let mut tray = TrayIconBuilder::new()
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                show_main_window(tray.app_handle());
            }
        })
        .on_menu_event(|app, event| match event.id().as_ref() {
            "show" => show_main_window(app),
            "pause" => {
                let _ = app.emit("javis://pause-listening", ());
            }
            "restart" => {
                let state = app.state::<AppState>();
                if let Err(error) = state.sidecar.restart(app) {
                    let _ = app_log::append(app, "error", "sidecar", &error);
                }
            }
            "diagnostics" => {
                show_main_window(app);
                let _ = app.emit("javis://open-diagnostics", ());
            }
            "quit" => {
                let state = app.state::<AppState>();
                state.explicit_quit.store(true, Ordering::SeqCst);
                let _ = state.sidecar.stop_owned();
                app.exit(0);
            }
            _ => {}
        });
    if let Some(icon) = app.default_window_icon() {
        tray = tray.icon(icon.clone());
    }
    tray.build(app)?;
    Ok(())
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let runtime_root = runtime_bundle::runtime_path(app.handle())
                .map_err(std::io::Error::other)?;
            app.manage(AppState::new(SidecarManager::with_root(runtime_root)));
            install_tray(app)?;
            app_log::append(app.handle(), "info", "app", "Javis desktop shell started")
                .map_err(std::io::Error::other)?;
            let runtime_app = app.handle().clone();
            std::thread::spawn(move || {
                let _ = app_log::append(
                    &runtime_app,
                    "info",
                    "runtime",
                    "runtime preparation started",
                );
                match runtime_bundle::ensure_runtime(&runtime_app) {
                    Ok(_) => {
                        let state = runtime_app.state::<AppState>();
                        match state.sidecar.start(&runtime_app) {
                            Ok(status) => {
                                let _ = app_log::append(
                                    &runtime_app,
                                    "info",
                                    "sidecar",
                                    &format!("runtime ready: {status}"),
                                );
                            }
                            Err(error) => {
                                let _ = app_log::append(
                                    &runtime_app,
                                    "error",
                                    "sidecar",
                                    &error,
                                );
                            }
                        }
                        let _ = runtime_app.emit("javis://runtime-ready", ());
                    }
                    Err(error) => {
                        let _ = app_log::append(&runtime_app, "error", "runtime", &error);
                        let _ = runtime_app.emit("javis://runtime-error", error);
                    }
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                let state = window.app_handle().state::<AppState>();
                if !state.explicit_quit.load(Ordering::SeqCst) {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            sidecar_status,
            sidecar_start,
            sidecar_stop,
            sidecar_restart,
            write_app_log,
            capture_screen_native,
            open_logs_directory,
            set_main_window_bounds,
            pet_actions::execute_pet_shortcut,
        ])
        .build(tauri::generate_context!())
        .expect("failed to build Javis desktop app");

    app.run(|app_handle, event| {
        if matches!(event, RunEvent::Exit) {
            let state = app_handle.state::<AppState>();
            let _ = state.sidecar.stop_owned();
        }
    });
}
