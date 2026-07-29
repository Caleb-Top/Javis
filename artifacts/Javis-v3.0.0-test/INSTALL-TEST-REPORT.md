# Javis v3.0 Installation Test Report

Overall: PASS

| Check | Status | Detail |
|---|---|---|
| NSIS silent install | PASS | exit=0; root=C:\Users\34247\AppData\Local\Temp\Javis-v3-install-test |
| Installed App executable | PASS | C:\Users\34247\AppData\Local\Temp\Javis-v3-install-test\javis-app.exe |
| Installed App binary baseline | PASS | installed=23B15D0BA586CBC43A5DB8626CB912C5FCEDB7BB3F848AA3DC5123B9A4F663DB |
| Installed App window | PASS | responsive handle=23466500; visible_ms=255 |
| Installed backend /api/status | PASS | service=javis |
| First-start runtime activation | PASS | version=3.0.0; marker=C:\Users\34247\AppData\Local\Temp\Javis-v3-data-test\runtime\runtime-version.json |
| Activated runtime hash | PASS | active=8dd5858f5d3c05bfab6b79b0394d12701b7029f34b4db451b888efd64853dce7; expected=8dd5858f5d3c05bfab6b79b0394d12701b7029f34b4db451b888efd64853dce7 |
| Upgrade preservation | PASS | preservation canary=C:\Users\34247\AppData\Local\Temp\Javis-v3-data-test\runtime\app\workspace\v3-preservation-canary.txt |
| Same-version reinstall replaces stale App | PASS | exit=0; stale=67C7E50AB8131A0BB5A7F910782C917FD583353B1AE1E87C74F32BFDB9F9928A; installed=23B15D0BA586CBC43A5DB8626CB912C5FCEDB7BB3F848AA3DC5123B9A4F663DB; baseline=23B15D0BA586CBC43A5DB8626CB912C5FCEDB7BB3F848AA3DC5123B9A4F663DB |
| Reinstall preserves user data | PASS | preservation canary=C:\Users\34247\AppData\Local\Temp\Javis-v3-data-test\runtime\app\workspace\v3-preservation-canary.txt |
| Uninstaller present | PASS | C:\Users\34247\AppData\Local\Temp\Javis-v3-install-test\uninstall.exe |
| Uninstall shell | PASS | exit=0 |
| Uninstall registry cleanup | PASS | shell and HKCU uninstall registration removed |
| Uninstall preserves user data | PASS | preservation canary retained |

Audio capture is provided by the packaged native Python/PortAudio and Windows WASAPI paths. The App WebView does not request microphone, camera, screen-capture or clipboard permissions.
