# Javis v3.0 Installation Test Report

Overall: PASS

| Check | Status | Detail |
|---|---|---|
| NSIS silent install | PASS | exit=0; root=C:\Users\34247\AppData\Local\Temp\Javis-v3-install-test |
| Installed App executable | PASS | C:\Users\34247\AppData\Local\Temp\Javis-v3-install-test\javis-app.exe |
| Installed WebView2 loader | PASS | C:\Users\34247\AppData\Local\Temp\Javis-v3-install-test\WebView2Loader.dll; sha256=8427B1FC58EC707813E5C0A51EB5D69397BB333250A7B891BE4D3B123F1E0F1C |
| Installed App binary baseline | PASS | installed=E4BBDDF49A258027C72F641E6A50D6C484BAA844E175048B0AE9230F486BD56F |
| Installed App window | PASS | responsive handle=8263166; visible_ms=255 |
| Installed backend /api/status | PASS | service=javis |
| First-start runtime activation | PASS | version=3.0.0; marker=C:\Users\34247\AppData\Local\Temp\Javis-v3-data-test\runtime\runtime-version.json |
| Activated runtime hash | PASS | active=16681ea731f853737e68d12d366250922e6c959dc52626f5d51b148814bb8696; expected=16681ea731f853737e68d12d366250922e6c959dc52626f5d51b148814bb8696 |
| Upgrade preservation | PASS | preservation canary=C:\Users\34247\AppData\Local\Temp\Javis-v3-data-test\runtime\app\workspace\v3-preservation-canary.txt |
| Same-version reinstall replaces stale App | PASS | exit=0; stale=E20C7099A16CD12876124D94721046B4DFEA3305F250F432B43B077A789434D0; installed=E4BBDDF49A258027C72F641E6A50D6C484BAA844E175048B0AE9230F486BD56F; baseline=E4BBDDF49A258027C72F641E6A50D6C484BAA844E175048B0AE9230F486BD56F |
| Reinstall preserves user data | PASS | preservation canary=C:\Users\34247\AppData\Local\Temp\Javis-v3-data-test\runtime\app\workspace\v3-preservation-canary.txt |
| Uninstaller present | PASS | C:\Users\34247\AppData\Local\Temp\Javis-v3-install-test\uninstall.exe |
| Uninstall shell | PASS | exit=0 |
| Uninstall registry cleanup | PASS | shell and HKCU uninstall registration removed |
| Uninstall preserves user data | PASS | preservation canary retained |

Audio capture is provided by the packaged native Python/PortAudio and Windows WASAPI paths. The App WebView does not request microphone, camera, screen-capture or clipboard permissions.
