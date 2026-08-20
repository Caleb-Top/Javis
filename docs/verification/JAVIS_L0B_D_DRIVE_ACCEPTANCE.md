# Javis L0-B D-Drive Acceptance

Status: NOT EXECUTED

本文件是 L0-B 基础 Avatar 在 D 盘安装包、真实 Windows WebView2 和 GPU 环境中的验收合同。当前仅定义证据结构和执行矩阵；本轮没有执行任何 D 盘项目，所有结果均为 `NOT EXECUTED`。Python、Node、浏览器预览、构建成功或单张截图均不能替代这里的实机结论。

## Result Rules

- 每个场景只能填写 `PASS`、`FAIL` 或 `NOT EXECUTED`。
- `PASS` 必须绑定同一精确安装包 SHA-256，并提供截图、录屏、性能日志或命令输出的绝对路径。
- 任一证据缺失、包 SHA 不一致、环境字段未记录或仅在浏览器预览中执行，结果必须保持 `NOT EXECUTED`。
- D 盘安装目录只用于用户态验收。失败必须回到共享源码或指定 worktree 复现和修复，不得直接编辑已安装文件。
- 当前 source commit 可以在最终包生成时补填；必须记录打包时的精确 commit，而不是事后工作区 HEAD。

## Package Identity

| Field | Exact value | Evidence | Result |
|---|---|---|---|
| Source repository | `G:\Javis` | 固定构建源 | NOT EXECUTED |
| Source commit | `<pending: git rev-parse HEAD at package build>` | `<pending log path>` | NOT EXECUTED |
| Dirty-worktree state | `<pending: git status --short>` | `<pending log path>` | NOT EXECUTED |
| Package absolute path | `<pending: exact D-drive installer/package path>` | `<pending>` | NOT EXECUTED |
| Package filename/version | `<pending>` | `<pending>` | NOT EXECUTED |
| Package SHA-256 | `<pending: full 64 lowercase hex>` | `<pending: Get-FileHash -Algorithm SHA256 output>` | NOT EXECUTED |
| Installed executable path | `<pending: exact D-drive path>` | `<pending>` | NOT EXECUTED |
| Install timestamp/operator | `<pending: ISO-8601 with timezone and operator>` | `<pending>` | NOT EXECUTED |

Required identity commands:

```powershell
git -C G:\Javis rev-parse HEAD
git -C G:\Javis status --short
Get-FileHash -Algorithm SHA256 -LiteralPath '<exact-package-path>'
```

## Machine Identity

| Field | Exact value | Evidence | Result |
|---|---|---|---|
| Windows edition | `<pending>` | `<winver/systeminfo output>` | NOT EXECUTED |
| Windows version/build | `<pending>` | `<winver/systeminfo output>` | NOT EXECUTED |
| WebView2 runtime version | `<pending: exact four-part version>` | `<registry/runtime diagnostic>` | NOT EXECUTED |
| GPU adapter | `<pending: exact adapter name>` | `<Get-CimInstance Win32_VideoController>` | NOT EXECUTED |
| GPU driver version/date | `<pending>` | `<Get-CimInstance output>` | NOT EXECUTED |
| CPU | `<pending>` | `<systeminfo/CIM output>` | NOT EXECUTED |
| Installed RAM | `<pending>` | `<systeminfo/CIM output>` | NOT EXECUTED |
| Monitor resolution/refresh | `<pending>` | `<display settings capture>` | NOT EXECUTED |
| Display scale under test | `100%, 125%, 150%, 175%` | `<per-run capture>` | NOT EXECUTED |
| Power mode | `<pending>` | `<Windows power settings capture>` | NOT EXECUTED |

## Scale, Window And Background Matrix

For every cell, capture the complete transparent window and surrounding wallpaper. Verify no black/white fringe, flash, clipping, unreadable status, facial occlusion, or broken click/drag/right-click target. Evidence naming: `visual/<scale>/<window>/<background>.*`.

| Scale | Logical window | Light wallpaper | Dark wallpaper | Complex/high-contrast wallpaper |
|---:|---:|---|---|---|
| 100% | 132x158 | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| 100% | 200x218 | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| 125% | 132x158 | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| 125% | 200x218 | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| 150% | 132x158 | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| 150% | 200x218 | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| 175% | 132x158 | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| 175% | 200x218 | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |

Per-cell evidence must record selected avatar ID, actual renderer/tier, Windows scale, logical and physical window bounds, wallpaper file/color, screenshot path, and interaction log path.

## State And Tier Matrix

Each cell requires a screenshot or short recording at 200x218 and a second capture at 132x158. State text/live region must remain readable and state must not rely on color alone. Evidence naming: `state/<state>/<tier>/<window>.*`.

| State | 3D High | 3D Low | Procedural 3D | 2D `javis-anime` | Orb `javis-orb` |
|---|---|---|---|---|---|
| idle | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| attention | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| listening | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| thinking | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| speaking | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| executing | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| blocked | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| error | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| offline | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |

Unknown or malformed life/expression states must fail closed to a neutral idle presentation and must not preserve or invent speaking output.

## Reduced Motion And Controls

| Scenario | Expected result | Evidence | Result |
|---|---|---|---|
| Windows reduced motion enabled before launch | Continuous decorative motion is disabled or reduced; 3D High is not retained | `<settings capture + recording + diagnostics>` | NOT EXECUTED |
| Reduced motion changed while App is running | New preference is honored without restart or interaction loss | `<before/after recording>` | NOT EXECUTED |
| 3D disabled | 2D remains visible and status/live region remains present | `<recording + diagnostics>` | NOT EXECUTED |
| Manual switch to Orb | `javis-orb` becomes visible and click/drag/right-click/settings remain usable | `<recording + settings capture>` | NOT EXECUTED |
| Motion, gaze, mouth and power controls | Each setting changes only the declared presentation behavior | `<settings and diagnostics>` | NOT EXECUTED |

## Voice Matrix

Record audio state, expression revision, mouth value, gesture state, status text and stop latency. Microphone input must never drive Javis speaking mouth motion.

| Voice scenario | 3D High | 3D Low | Procedural 3D | 2D | Orb |
|---|---|---|---|---|---|
| Normal playback from start through idle | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| Barge-in during speaking | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| Backend disconnect during speaking | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |
| Microphone listening input only | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED | NOT EXECUTED |

Barge-in passes only when native playback, mouth envelope and speaking gesture stop immediately, and no delayed terminal event restores stale speaking.

## Offline And Disconnect Matrix

| Scenario | Expected result | Evidence | Result |
|---|---|---|---|
| Cold start with network physically disabled | Default avatar/fallback loads locally; no runtime avatar download is attempted | `<network state + process/network log + recording>` | NOT EXECUTED |
| Disconnect while idle | Offline state appears; body and controls remain available | `<recording + event log>` | NOT EXECUTED |
| Disconnect while thinking | Offline state replaces stale activity safely | `<recording + ordered revisions>` | NOT EXECUTED |
| Disconnect while speaking | Playback/body stop safely and stale speaking does not return | `<audio + event log>` | NOT EXECUTED |
| Restart while still offline | Selected local skin persists and App starts without blank/black window | `<restart recording + settings>` | NOT EXECUTED |
| Avatar network egress audit | No model, texture, font, animation, script or CDN request occurs | `<WebView2/network/process capture>` | NOT EXECUTED |

## Renderer Failure And Fallback Matrix

| Injected condition | Required observable path | Status/interactions retained | Evidence | Result |
|---|---|---|---|---|
| Renderer construction failure | 3D to manifest fallback | `<pending>` | `<recording + log>` | NOT EXECUTED |
| Default asset missing/corrupt | 3D to 2D, then Orb if needed | `<pending>` | `<recording + hash/error log>` | NOT EXECUTED |
| First WebGL context loss | Stop render loop, show 2D/Orb, bounded recovery | `<pending>` | `<recording + context log>` | NOT EXECUTED |
| Repeated context loss | Remain degraded without retry loop or flashing | `<pending>` | `<recording + retry counters>` | NOT EXECUTED |
| Recovery after context restore | Reload latest assets and newest unexpired intent only | `<pending>` | `<ordered revisions + recording>` | NOT EXECUTED |
| 2D asset unavailable | Terminal local Orb remains visible | `<pending>` | `<recording + asset log>` | NOT EXECUTED |
| Frontend error boundary revealed | Existing 2D/Orb fallback is not removed; recovery action works | `<pending>` | `<recording + frontend log>` | NOT EXECUTED |
| 3D failure with Live/Code/voice/settings use | All non-avatar surfaces remain usable | `<pending>` | `<workflow recording>` | NOT EXECUTED |

## Performance Measurements

Measure after a 60-second warm-up, then retain at least 120 seconds of samples per row. Record idle FPS, active FPS, p95 frame time, process working set, GPU engine utilization, first visible fallback time, final avatar load time and render suspension while hidden.

| Tier | Idle FPS | Active FPS | p95 frame ms | Working set MB | GPU % | First visible ms | Final load ms | Result |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 3D High | `<pending>` | `<pending>` | `<pending>` | `<pending>` | `<pending>` | `<pending>` | `<pending>` | NOT EXECUTED |
| 3D Low | `<pending>` | `<pending>` | `<pending>` | `<pending>` | `<pending>` | `<pending>` | `<pending>` | NOT EXECUTED |
| Procedural 3D | `<pending>` | `<pending>` | `<pending>` | `<pending>` | `<pending>` | `<pending>` | `<pending>` | NOT EXECUTED |
| 2D | `<pending>` | `<pending>` | `<pending>` | `<pending>` | `<pending>` | `<pending>` | `<pending>` | NOT EXECUTED |
| Orb | `<pending>` | `<pending>` | `<pending>` | `<pending>` | `<pending>` | `<pending>` | `<pending>` | NOT EXECUTED |

3D High targets 30 FPS, 3D Low targets 20 FPS, idle is lower frequency, and hidden Pet/Live must stop continuous rendering. Measurements are evidence, not inferred PASS conditions.

## Restart, Reinstall And Uninstall Matrix

Use the same exact package SHA unless the row explicitly records a replacement package. Record user-data and model-directory paths plus before/after hashes.

| Lifecycle action | Required continuity/cleanup | Evidence | Result |
|---|---|---|---|
| App restart | Selected avatar, scale and accessibility preferences persist; no stale speaking | `<before/after settings + recording>` | NOT EXECUTED |
| Windows restart | Same selection and fallback remain available offline | `<before/after capture>` | NOT EXECUTED |
| Reinstall same exact package | User selection, data and model directory are not damaged | `<package hash + directory hashes>` | NOT EXECUTED |
| Upgrade reinstall | Migration preserves selection or records an explicit safe default | `<old/new hashes + migration log>` | NOT EXECUTED |
| Uninstall | Installed binaries/resources are removed according to installer policy | `<directory and Apps list capture>` | NOT EXECUTED |
| Post-uninstall user data audit | User data/model retention or removal exactly matches the displayed uninstall choice | `<before/after directory listing + hashes>` | NOT EXECUTED |
| Reinstall after uninstall | Retained choice is restored only when retained data was explicitly preserved | `<settings + directory evidence>` | NOT EXECUTED |

## First-Body Ten-Step Acceptance

| Step | Required observation | Evidence | Result |
|---:|---|---|---|
| 1 | Javis appears in the approved default body and idles quietly | `<pending>` | NOT EXECUTED |
| 2 | No loading flash, black frame, white background or blank window appears | `<pending>` | NOT EXECUTED |
| 3 | User invocation produces attention posture/gaze | `<pending>` | NOT EXECUTED |
| 4 | Listening remains stable and microphone input does not animate speaking mouth | `<pending>` | NOT EXECUTED |
| 5 | Thinking is restrained, continuous and distinguishable | `<pending>` | NOT EXECUTED |
| 6 | Playback produces the basic speaking envelope | `<pending>` | NOT EXECUTED |
| 7 | Barge-in immediately stops sound, mouth and speaking gesture | `<pending>` | NOT EXECUTED |
| 8 | Late terminal events do not restore stale speaking | `<pending>` | NOT EXECUTED |
| 9 | Completed interaction returns smoothly to idle | `<pending>` | NOT EXECUTED |
| 10 | WebGL disabled/failing yields 2D or Orb while Live and Code remain usable | `<pending>` | NOT EXECUTED |

## Final Decision

| Gate | Result |
|---|---|
| Exact package identity and machine metadata | NOT EXECUTED |
| Scale/window/background matrix | NOT EXECUTED |
| State/tier matrix | NOT EXECUTED |
| Reduced motion and controls | NOT EXECUTED |
| Voice and barge-in | NOT EXECUTED |
| Offline and disconnect | NOT EXECUTED |
| Renderer failure and fallback | NOT EXECUTED |
| Performance | NOT EXECUTED |
| Restart/reinstall/uninstall | NOT EXECUTED |
| First-body ten-step experience | NOT EXECUTED |

Overall decision: NOT EXECUTED

Failure notes, evidence index and follow-up issue IDs: `<pending>`
