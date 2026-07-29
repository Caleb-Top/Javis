# Javis UI Interaction Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the first Live/Pet right-click open one shared menu, add a standalone light settings surface with explicit shortcut editors, replace the legacy icon with the selected C thinking-state orb, and unify rounded translucent window styling without changing Javis backend or Code functionality.

**Architecture:** Move menu ownership out of `PetSurface` into a shared `SurfaceContextMenu` and let `windowMode` expand and restore either Live or Pet geometry. Add `settings` as a first-class desktop mode with a dedicated `SettingsSurface`; keep shortcut normalization in `petPreferences` and put UI-specific field/status rules in a pure model. Use the official Tauri dialog plugin for native target selection, preserve Rust validation for execution, and generate the icon set from a transparent WebGL Canvas export.

**Tech Stack:** TypeScript, DOM APIs, CSS, Node test runner, Vite, Tauri 2, Rust, Windows API, `@tauri-apps/plugin-dialog`, ImageMagick/Tauri icon generation.

## Global Constraints

- Do not change the five-system blueprint, Python backend, memory, models, training results, permissions, or Code Web behavior.
- Keep `http://127.0.0.1:8080/` as the Code iframe source.
- Keep the fixed Settings and Code menu items plus at most three configured custom shortcuts.
- Use the selected C frame from the real `thinking` renderer state.
- Live and Pet remain transparent and borderless.
- Settings, Code, diagnostics, and first-run surfaces use the same light neutral visual tokens.
- A feature is complete only after automated tests, production build, Rust tests, browser screenshots, and native Tauri interaction checks pass.
- The repository currently treats `app/` as untracked; do not create partial commits containing only fragments of that directory.

---

## File Structure

**Create**

- `app/src/menu/surfaceMenuState.ts`: pure Live/Pet menu state and restore-transition model.
- `app/src/menu/SurfaceContextMenu.ts`: shared menu DOM, commands, close behavior, and preference refresh.
- `app/src/settings/settingsNavigation.ts`: pure return-mode rules for standalone settings.
- `app/src/settings/shortcutEditorModel.ts`: type-specific labels, placeholders, controls, and validation status.
- `app/src/settings/SettingsSurface.ts`: standalone settings UI and event wiring.
- `app/tests/surfaceMenuState.test.ts`: first-right-click and restore-transition tests.
- `app/tests/settingsNavigation.test.ts`: source/return mode tests.
- `app/tests/shortcutEditorModel.test.ts`: explicit field and validation tests.
- `app/tests/appIconAssets.test.ts`: icon dimensions and alpha checks.
- `app/public/icons/javis-orb-thinking-c.png`: transparent 512px selected source.

**Modify**

- `app/src/live/LiveStage.ts`: add shared menu and settings roots.
- `app/src/pet/PetSurface.ts`: remove menu ownership and forward context-menu requests.
- `app/src/pet/petTypes.ts`: replace menu callbacks with a context-menu callback.
- `app/src/pet/petWindowLayout.ts`: retain Pet geometry and expose menu-extra width.
- `app/src/desktop/windowMode.ts`: support `settings` and source-aware menu expansion/restoration.
- `app/src/main.ts`: wire shared menu, standalone settings, native shortcut picker, and return modes.
- `app/src/panels/ControlDrawer.ts`: remove the embedded pet settings pane and route Settings to the standalone surface.
- `app/src/styles.css`: shared light shell tokens, menu placement, settings layout, round clipping, and Code shell polish.
- `app/package.json` and lockfile: add the official Tauri dialog JavaScript package.
- `app/src-tauri/Cargo.toml` and lockfile: add the official Tauri dialog Rust plugin.
- `app/src-tauri/src/main.rs`: initialize the dialog plugin.
- `app/src-tauri/tauri.conf.json`: point bundle icon entries at the regenerated icon set.
- `app/src-tauri/icons/*`: replace platform icon assets generated from the C frame.

---

### Task 1: Shared Live/Pet Menu State

**Files:**
- Create: `app/src/menu/surfaceMenuState.ts`
- Create: `app/tests/surfaceMenuState.test.ts`
- Modify: `app/src/desktop/windowMode.ts`
- Modify: `app/src/pet/petWindowLayout.ts`

**Interfaces:**
- Produces: `type MenuSource = "live" | "pet"`.
- Produces: `createClosedMenuState(): SurfaceMenuState`.
- Produces: `openSurfaceMenu(state, source, placement): SurfaceMenuState`.
- Produces: `closeSurfaceMenu(state): SurfaceMenuState`.
- Produces: `getSurfaceMenuWindowTransition(source, scale, fromExpanded, toExpanded, placement): SurfaceMenuWindowTransition`.
- Produces: `setSurfaceMenuExpanded(source: MenuSource, expanded: boolean): Promise<PetMenuPlacement>`.

- [ ] **Step 1: Write failing tests for first-open source and geometry restoration**

```ts
test("opens the first Live right-click without changing to Pet", () => {
  const opened = openSurfaceMenu(createClosedMenuState(), "live", "right");
  assert.equal(opened.open, true);
  assert.equal(opened.source, "live");
});

test("restores Live geometry after a left-side menu closes", () => {
  const open = getSurfaceMenuWindowTransition("live", 1, false, true, "left");
  const close = getSurfaceMenuWindowTransition("live", 1, true, false, "left");
  assert.deepEqual({ width: open.width, height: open.height }, { width: 468, height: 320 });
  assert.equal(open.windowDeltaX, -148);
  assert.deepEqual({ width: close.width, height: close.height }, { width: 320, height: 320 });
  assert.equal(close.windowDeltaX, 148);
});
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
$env:PATH='D:\Javis\tools\nodejs;'+$env:PATH
D:\Javis\tools\nodejs\npm.cmd test
```

Expected: FAIL because `surfaceMenuState.ts` and source-aware window transition do not exist.

- [ ] **Step 3: Implement the pure state and geometry model**

Use the existing `PET_MENU_EXTRA_WIDTH = 148`, `ORB_SIZE = 320`, and `getCompactPetSize(scale)`. Preserve the source mode in the state so close behavior never assumes Pet.

- [ ] **Step 4: Replace Pet-only expansion in `windowMode.ts`**

Add `settings` to `DesktopMode`, keep `surfaceMenuSource`, and update CSS variables:

```ts
document.documentElement.style.setProperty("--surface-menu-offset-x", `${layout.surfaceOffsetX}px`);
document.documentElement.style.setProperty("--surface-base-width", `${layout.baseWidth}px`);
document.body.dataset.surfaceMenuSource = source;
document.body.dataset.surfaceMenuExpanded = String(expanded);
```

- [ ] **Step 5: Run focused and full tests**

Expected: the new tests pass and all existing Pet geometry tests remain green.

---

### Task 2: Shared Context Menu Component

**Files:**
- Create: `app/src/menu/SurfaceContextMenu.ts`
- Modify: `app/src/live/LiveStage.ts`
- Modify: `app/src/pet/PetSurface.ts`
- Modify: `app/src/pet/petTypes.ts`
- Modify: `app/src/main.ts`
- Modify: `app/src/styles.css`

**Interfaces:**
- Consumes: `setSurfaceMenuExpanded(source, expanded)`.
- Consumes: `buildPetMenuItems(preferences)`.
- Produces: `createSurfaceContextMenu(options): SurfaceContextMenuController`.
- Produces controller methods `open(source)`, `close(immediate?)`, `isOpen()`, `refresh(preferences)`, and `dispose()`.

- [ ] **Step 1: Add a failing DOM contract test or pure command-routing test**

Test that opening from `live` calls expansion exactly once and that the first action is menu open, not `setDesktopMode("pet")`.

- [ ] **Step 2: Verify RED**

Expected: FAIL because the shared component is absent and `main.ts` still routes Live right-click to Pet.

- [ ] **Step 3: Add `#surface-menu-root` to `LiveStage`**

Mount it once beside `#pet-surface-root`, `#drawer-root`, and `#code-root`.

- [ ] **Step 4: Move menu rendering and close behavior out of `PetSurface`**

`PetSurface` keeps sprite rendering, drag, skin, and status. Its right-click calls:

```ts
options.onContextMenu("pet");
```

The Live Canvas right-click calls:

```ts
event.preventDefault();
void surfaceMenu.open("live");
```

- [ ] **Step 5: Implement source-aware CSS anchoring**

- Live source keeps a fixed 320px orb region while the native window expands.
- Pet source uses `--pet-boundary-size` and `--pet-offset-x`.
- Left and right placement preserve the visible orb/pet screen position.

- [ ] **Step 6: Verify menu interactions**

Automate:

- first Live right-click shows Settings and Code;
- no skin/local-storage value changes;
- second right-click closes;
- outside pointer, Escape, drag, and command execution close;
- Pet right-click still works.

---

### Task 3: Settings Navigation and Shortcut Editor Model

**Files:**
- Create: `app/src/settings/settingsNavigation.ts`
- Create: `app/src/settings/shortcutEditorModel.ts`
- Create: `app/tests/settingsNavigation.test.ts`
- Create: `app/tests/shortcutEditorModel.test.ts`
- Modify: `app/src/pet/petPreferences.ts`

**Interfaces:**
- Produces: `type SettingsSourceMode = "live" | "pet" | "code"`.
- Produces: `getSettingsReturnMode(source): SettingsSourceMode`.
- Produces: `getShortcutEditorDescriptor(kind): ShortcutEditorDescriptor`.
- Produces: `getShortcutSlotStatus(slot, targetExists?): ShortcutSlotStatus`.
- Produces: `normalizeRecordedHotkey(event): string | null`.

- [ ] **Step 1: Write failing navigation tests**

```ts
for (const mode of ["live", "pet", "code"] as const) {
  test(`returns from settings to ${mode}`, () => {
    assert.equal(getSettingsReturnMode(mode), mode);
  });
}
```

- [ ] **Step 2: Write failing descriptor and status tests**

Cover:

- Javis uses an action select.
- Target uses a path input and browse button.
- URL uses an `https://` placeholder.
- Hotkey uses a record control.
- Empty slots are incomplete.
- `javascript:` URLs are invalid.
- missing target paths report `missing-target`.
- `Ctrl+Shift+P` is valid.

- [ ] **Step 3: Verify RED**

Expected: FAIL because both modules are absent.

- [ ] **Step 4: Implement minimal pure models**

Do not read DOM or invoke Tauri in these files. Keep labels and validation deterministic.

- [ ] **Step 5: Run all Node tests**

Expected: all model and existing preference tests pass.

---

### Task 4: Standalone Settings Surface

**Files:**
- Create: `app/src/settings/SettingsSurface.ts`
- Modify: `app/src/live/LiveStage.ts`
- Modify: `app/src/main.ts`
- Modify: `app/src/panels/ControlDrawer.ts`
- Modify: `app/src/desktop/windowMode.ts`
- Modify: `app/src/styles.css`

**Interfaces:**
- Consumes: `getShortcutEditorDescriptor`, `getShortcutSlotStatus`, `readPetPreferences`, and `writePetPreferences`.
- Produces: `createSettingsSurface(options): SettingsSurfaceController`.
- Controller methods: `open(sourceMode)`, `close()`, `sync()`, and `dispose()`.
- Options include `onClose(mode)`, `onPickTarget()`, `onOpenDiagnostics()`, and `onOpenPrivacy()`.

- [ ] **Step 1: Write a failing integration contract**

Assert that the Settings action calls `setDesktopMode("settings")` and never calls `openCodeSurface()`.

- [ ] **Step 2: Verify RED**

Expected: FAIL because `showSettingsSurface` currently opens Code before the control drawer.

- [ ] **Step 3: Add `#settings-root` and render the standalone surface**

Create a draggable header, left navigation, content area, and auto-save footer. Use sections General, Pet, Shortcuts, and Appearance.

- [ ] **Step 4: Implement type-specific shortcut controls**

- Javis: `<select>` with four allowlisted actions.
- Target: path field plus native Browse button.
- URL: URL field with explicit label and example.
- Hotkey: Record button that captures one modifier set plus one primary key.

- [ ] **Step 5: Remove embedded settings from `ControlDrawer`**

The control drawer keeps Status, Tasks, Permission, and Perception. Any remaining Settings button dispatches `javis:open-settings` instead of rendering the form.

- [ ] **Step 6: Implement return-mode handling**

Opening records the current mode. Closing restores it and only opens Code when the recorded source is `code`.

- [ ] **Step 7: Run tests and production build**

Expected: Settings navigation tests pass and `tsc && vite build` succeeds.

---

### Task 5: Native Target Picker and Shortcut Execution

**Files:**
- Modify: `app/package.json`
- Modify: `app/package-lock.json`
- Modify: `app/src-tauri/Cargo.toml`
- Modify: `app/src-tauri/Cargo.lock`
- Modify: `app/src-tauri/src/main.rs`
- Modify: `app/src/settings/SettingsSurface.ts`
- Modify: `app/src-tauri/src/pet_actions.rs`

**Interfaces:**
- Produces native dialog call returning a selected file or directory path.
- Keeps `execute_pet_shortcut(kind, value)` unchanged for execution.
- Extends target validation so execution checks existence immediately before launch.

- [ ] **Step 1: Add the official Tauri dialog dependencies**

Run:

```powershell
$env:PATH='D:\Javis\tools\nodejs;'+$env:PATH
D:\Javis\tools\nodejs\npm.cmd install @tauri-apps/plugin-dialog
```

Add matching `tauri-plugin-dialog = "2"` to Rust and initialize:

```rust
.plugin(tauri_plugin_dialog::init())
```

- [ ] **Step 2: Connect the Browse button**

Use the plugin `open()` API with file and directory selection modes. Store only the returned local path; do not request browser filesystem permissions.

- [ ] **Step 3: Add/extend Rust tests**

Keep URL and hotkey tests. Add target validation tests that distinguish empty values from a valid-form path; filesystem existence remains checked at execution time.

- [ ] **Step 4: Run Rust tests**

Use the bundled GNU toolchain with explicit linker and dlltool paths. Expected: all Rust tests pass.

---

### Task 6: Selected C Thinking-State Icon

**Files:**
- Create: `app/public/icons/javis-orb-thinking-c.png`
- Create/replace: `app/src-tauri/icons/*`
- Create: `app/tests/appIconAssets.test.ts`
- Modify: `app/src-tauri/tauri.conf.json`

**Interfaces:**
- Consumes: the actual `thinking` state WebGL Canvas.
- Produces: transparent 512px source and complete Tauri platform icon set.

- [ ] **Step 1: Write failing icon asset tests**

Assert:

- source PNG exists;
- source is 512×512;
- source has an alpha channel;
- ICO exists;
- 32, 64, 128, and 256px outputs exist.

- [ ] **Step 2: Verify RED**

Expected: FAIL because the selected C asset has not replaced the old icon.

- [ ] **Step 3: Capture the transparent Canvas**

Start the Vite app, trigger a real `thinking` state, and export:

```js
document.querySelector("#live-orb-canvas").toDataURL("image/png")
```

Use the selected C phase. Do not use the black-background screenshot.

- [ ] **Step 4: Generate the icon family**

Use Tauri icon generation or ImageMagick from the 512px transparent source. Inspect 16, 24, 32, 64, and 256px renderings.

- [ ] **Step 5: Update configuration and run tests**

Expected: icon asset tests pass and `tauri.conf.json` references the regenerated `.ico`.

---

### Task 7: Unified Light Rounded Visual Shell

**Files:**
- Modify: `app/src/styles.css`
- Modify: `app/src/code/CodeSurface.ts`
- Modify: `app/src/panels/DiagnosticsPanel.ts`
- Modify: `app/src/app/FirstRunPanel.ts`

**Interfaces:**
- Produces shared CSS tokens for full-window surfaces.
- Preserves transparent Live/Pet rendering.

- [ ] **Step 1: Add shared light tokens**

Define:

```css
--shell-bg: #eef1f5;
--shell-surface: rgba(255, 255, 255, .82);
--shell-text: #182235;
--shell-muted: #667085;
--shell-line: rgba(112, 128, 151, .18);
--shell-accent: #2563eb;
--shell-radius: 20px;
--panel-radius: 14px;
--control-radius: 10px;
```

- [ ] **Step 2: Apply mode-specific clipping**

Settings, Code, diagnostics, and first-run get transparent outer corners, 20px clipping, soft border, inset highlight, and low-strength shadow. Live and Pet remain fully transparent.

- [ ] **Step 3: Round the Code host without injecting into the iframe**

Clip the toolbar and iframe inside the native shell. Preserve iframe URL, permissions, and all existing functions.

- [ ] **Step 4: Restyle settings controls and responsive layout**

Verify at 1280×820, 960×700, and 760×560. Stack navigation above content only at the smallest width.

- [ ] **Step 5: Run production build**

Expected: no TypeScript or CSS build errors.

---

### Task 8: End-to-End Verification

**Files:**
- Update only test evidence under `artifacts/` and the implementation plan checkboxes.

- [ ] **Step 1: Run the complete Node suite**

Expected: all existing and new tests pass with zero failures.

- [ ] **Step 2: Run `npm run build`**

Expected: TypeScript and Vite production build succeed.

- [ ] **Step 3: Run Rust tests and `cargo check`**

Expected: all native tests pass and Tauri command/plugin registration compiles.

- [ ] **Step 4: Browser visual QA**

Capture:

- first Live right-click with menu open and blue orb unchanged;
- standalone settings at three viewports;
- all four shortcut editor types;
- rounded Code, diagnostics, and first-run surfaces;
- icon contact sheet at small sizes.

- [ ] **Step 5: Native Tauri QA**

Verify:

- first right-click opens the menu;
- Live and Pet restore correct size/position;
- Settings opens without Code;
- target picker is a native Windows dialog;
- custom URL/target/hotkey commands work;
- C icon appears in window, taskbar, and tray;
- transparent corners have no square leakage.

- [ ] **Step 6: Stop development processes and clean rebuildable caches**

Stop only the dev Vite/Tauri chain created by this plan. Do not stop the old installed app or remove user data, runtime, memory, models, blueprints, or training outputs.
