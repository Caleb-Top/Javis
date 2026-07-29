use std::{ffi::OsStr, mem::size_of, os::windows::ffi::OsStrExt, path::Path};

use tauri::AppHandle;
use windows::{
    core::{w, PCWSTR},
    Win32::UI::{
        Input::KeyboardAndMouse::{
            SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, KEYBDINPUT, KEYEVENTF_KEYUP, VIRTUAL_KEY,
        },
        Shell::ShellExecuteW,
        WindowsAndMessaging::SW_SHOWNORMAL,
    },
};

const MAX_VALUE_LENGTH: usize = 2048;

pub fn validate_shortcut(kind: &str, value: &str) -> Result<(), String> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return Err("shortcut value is empty".into());
    }
    if trimmed.len() > MAX_VALUE_LENGTH || trimmed.contains('\0') || trimmed.contains(['\r', '\n'])
    {
        return Err("shortcut value is invalid".into());
    }
    match kind {
        "url" => {
            let lower = trimmed.to_ascii_lowercase();
            if lower.starts_with("http://") || lower.starts_with("https://") {
                Ok(())
            } else {
                Err("only http and https URLs are allowed".into())
            }
        }
        "target" => Ok(()),
        "hotkey" => parse_hotkey(trimmed).map(|_| ()),
        _ => Err("unsupported shortcut kind".into()),
    }
}

pub fn parse_hotkey(value: &str) -> Result<Vec<u16>, String> {
    let tokens: Vec<&str> = value
        .split('+')
        .map(str::trim)
        .filter(|item| !item.is_empty())
        .collect();
    if tokens.len() < 2 {
        return Err("hotkey requires a modifier and a key".into());
    }
    let mut keys = Vec::with_capacity(tokens.len());
    for (index, token) in tokens.iter().enumerate() {
        let lower = token.to_ascii_lowercase();
        let key = if index + 1 < tokens.len() {
            match lower.as_str() {
                "ctrl" | "control" => 0x11,
                "alt" => 0x12,
                "shift" => 0x10,
                "meta" | "win" => 0x5b,
                _ => return Err(format!("unsupported modifier: {token}")),
            }
        } else {
            parse_primary_key(&lower)?
        };
        if keys.contains(&key) {
            return Err("hotkey contains duplicate keys".into());
        }
        keys.push(key);
    }
    Ok(keys)
}

fn parse_primary_key(value: &str) -> Result<u16, String> {
    if value.len() == 1 {
        let byte = value.as_bytes()[0].to_ascii_uppercase();
        if byte.is_ascii_alphanumeric() {
            return Ok(byte as u16);
        }
    }
    if let Some(number) = value
        .strip_prefix('f')
        .and_then(|item| item.parse::<u16>().ok())
    {
        if (1..=24).contains(&number) {
            return Ok(0x70 + number - 1);
        }
    }
    match value {
        "enter" => Ok(0x0d),
        "escape" | "esc" => Ok(0x1b),
        "space" => Ok(0x20),
        "tab" => Ok(0x09),
        "backspace" => Ok(0x08),
        "delete" => Ok(0x2e),
        "home" => Ok(0x24),
        "end" => Ok(0x23),
        "pageup" => Ok(0x21),
        "pagedown" => Ok(0x22),
        "arrowup" => Ok(0x26),
        "arrowdown" => Ok(0x28),
        "arrowleft" => Ok(0x25),
        "arrowright" => Ok(0x27),
        _ => Err(format!("unsupported key: {value}")),
    }
}

fn open_with_windows(value: &str) -> Result<(), String> {
    let wide: Vec<u16> = OsStr::new(value).encode_wide().chain(Some(0)).collect();
    let result = unsafe {
        ShellExecuteW(
            None,
            w!("open"),
            PCWSTR(wide.as_ptr()),
            PCWSTR::null(),
            PCWSTR::null(),
            SW_SHOWNORMAL,
        )
    };
    if result.0 as isize <= 32 {
        return Err(format!(
            "Windows could not open the target (code {})",
            result.0 as isize
        ));
    }
    Ok(())
}

fn send_hotkey(value: &str) -> Result<(), String> {
    let keys = parse_hotkey(value)?;
    let mut inputs = Vec::with_capacity(keys.len() * 2);
    for key in &keys {
        inputs.push(INPUT {
            r#type: INPUT_KEYBOARD,
            Anonymous: INPUT_0 {
                ki: KEYBDINPUT {
                    wVk: VIRTUAL_KEY(*key),
                    ..Default::default()
                },
            },
        });
    }
    for key in keys.iter().rev() {
        inputs.push(INPUT {
            r#type: INPUT_KEYBOARD,
            Anonymous: INPUT_0 {
                ki: KEYBDINPUT {
                    wVk: VIRTUAL_KEY(*key),
                    dwFlags: KEYEVENTF_KEYUP,
                    ..Default::default()
                },
            },
        });
    }
    let sent = unsafe { SendInput(&inputs, size_of::<INPUT>() as i32) };
    if sent != inputs.len() as u32 {
        return Err("Windows rejected part of the hotkey input".into());
    }
    Ok(())
}

#[tauri::command]
pub fn execute_pet_shortcut(app: AppHandle, kind: String, value: String) -> Result<(), String> {
    validate_shortcut(&kind, &value)?;
    let result = match kind.as_str() {
        "url" => open_with_windows(value.trim()),
        "target" => {
            let target = Path::new(value.trim());
            if !target.exists() {
                Err("configured target does not exist".into())
            } else {
                open_with_windows(value.trim())
            }
        }
        "hotkey" => send_hotkey(value.trim()),
        _ => Err("unsupported shortcut kind".into()),
    };
    let level = if result.is_ok() { "info" } else { "warn" };
    let message = if result.is_ok() {
        format!("pet shortcut executed: {kind}")
    } else {
        format!("pet shortcut failed: {kind}")
    };
    let _ = crate::app_log::append(&app, level, "pet_action", &message);
    result
}

#[cfg(test)]
mod tests {
    use super::{parse_hotkey, validate_shortcut};

    #[test]
    fn rejects_non_http_urls() {
        assert!(validate_shortcut("url", "file:///C:/secret.txt").is_err());
        assert!(validate_shortcut("url", "javascript:alert(1)").is_err());
        assert!(validate_shortcut("url", "https://example.com").is_ok());
    }

    #[test]
    fn rejects_unknown_kinds_and_empty_values() {
        assert!(validate_shortcut("shell", "dir").is_err());
        assert!(validate_shortcut("target", "  ").is_err());
    }

    #[test]
    fn parses_allowlisted_hotkey_tokens() {
        assert_eq!(
            parse_hotkey("Ctrl+Shift+K").unwrap(),
            vec![0x11, 0x10, 0x4b]
        );
        assert_eq!(parse_hotkey("Alt+F4").unwrap(), vec![0x12, 0x73]);
    }

    #[test]
    fn rejects_malformed_hotkeys() {
        assert!(parse_hotkey("Ctrl+???").is_err());
        assert!(parse_hotkey("K").is_err());
        assert!(parse_hotkey("Ctrl+Shift").is_err());
    }
}
