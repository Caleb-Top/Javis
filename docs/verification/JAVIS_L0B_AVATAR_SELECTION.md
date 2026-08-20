# Javis L0-B Avatar Selection Evidence

**Date:** 2026-08-20  
**Source commit:** `4a150d5`  
**Preview route:** `http://127.0.0.1:4173/?avatar-preview=1`  
**Selected default:** `javis-lightform` / `quiet-lifeform`

## Decision

`quiet-lifeform` remains the default visual profile. It has the lowest sustained
saturation of the three candidates, stays legible on both light and complex
backgrounds, and preserves Javis's cyan/violet eyes and central core without
turning the desktop pet into a high-attention effect.

No external VRM is imported. The governed procedural body meets the offline,
license, asset-budget, fallback, and small-window requirements without adding an
unreviewed third-party identity or network dependency.

## Evidence Matrix

The matrix contains three profiles, four states, and two backgrounds:

- Profiles: `light-tech-human`, `neon-pilot`, `quiet-lifeform`
- States: `idle`, `listening`, `thinking`, `speaking`
- Backgrounds: `light`, `wallpaper`
- Viewport inside each capture: `200x218`
- Total state captures: `24`

Evidence lives in `docs/verification/l0b-avatar-preview/`. File names use:

```text
<profile>__<state>__<background>.png
```

The combined comparison is `matrix.png`; its SHA-256 is:

```text
1DB6C3A191549EA0BFE8F3D0B184120F23E770EE87CE02B89EBFBCB557FC16A0
```

## Automated Checks

- All 24 captures completed without page or console errors.
- Every state in each profile/background pair has a distinct SHA-256.
- The minimum capture color count was 3,787.
- Desktop and 390 px narrow layout had no horizontal overflow.
- The 3D canvas was nonblank on desktop and narrow layouts.
- `context-lost` and `orb` controls displayed visible fallbacks.
- The isolated preview made no production `/api/` request. Its only WebSocket
  during development was Vite hot reload.

## Scope

These browser captures are deterministic preview evidence. They do not replace
the D-drive transparent WebView2, GPU, DPI, package, reinstall, or uninstall
acceptance matrix; those results remain governed by
`JAVIS_L0B_D_DRIVE_ACCEPTANCE.md`.
