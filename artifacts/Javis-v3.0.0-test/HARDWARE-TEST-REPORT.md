# Javis v3.0 Native Hardware Test Report

Overall: PASS

Tested installation: `D:\测试Javis\Javis`

| Check | Status | Detail |
|---|---|---|
| App startup | PASS | Responsive native window; backend ready |
| Permission mode | PASS | `full_access` |
| WebView device permissions | PASS | Not required |
| Microphone capture | PASS | Native isolated PortAudio worker; 47,148-byte WAV |
| Backend after microphone | PASS | `/api/status` remained online |
| System audio capture | PASS | Native Windows WASAPI loopback; 192,068-byte WAV |
| Backend after system audio | PASS | `/api/status` remained online |
| Camera perception | PASS | DirectShow capture; one OCR perception event |
| Native screen capture | PASS | Tauri/Rust captured 1920x1080 BGRA pixels |
| Screen perception | PASS | Native capture produced `ok: true` OCR events |
| Native crash audit | PASS | No new App, Python, PortAudio or WASAPI crashes |
| Runtime integrity | PASS | `8dd5858f5d3c05bfab6b79b0394d12701b7029f34b4db451b888efd64853dce7` |

Microphone, system audio, camera, and screen capture use Windows-native paths. The App does not request browser microphone, camera, screen-sharing, or clipboard permissions.
