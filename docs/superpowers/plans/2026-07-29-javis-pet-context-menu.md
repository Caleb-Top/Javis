# Javis Pet Context Menu Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a compact resizable Javis desktop pet with an animated right-click menu containing Settings, Code, and up to three configured shortcuts.

**Architecture:** A pure preferences/layout module owns validation and sizing, while `PetSurface` owns DOM interaction. `windowMode` remains the only frontend module that mutates Tauri window geometry, and native shortcut execution is exposed through narrow audited Rust commands rather than arbitrary shell text.

**Tech Stack:** TypeScript, CSS, Tauri 2, Rust, Node 24 built-in test runner.

## Global Constraints

- Preserve the existing Live, Code, skin, drag, state, backend, memory, model, and blueprint behavior.
- Show at most five menu items: Settings, Code, and three configured shortcuts.
- Hide every unconfigured or invalid shortcut.
- Keep compact transparent padding at 10 logical pixels per side.
- Store preferences under `javis.app.petPreferences.v1`.
- Do not add runtime dependencies or download packages.

---

### Task 1: Pure Pet Preferences And Layout

**Files:**
- Create: `app/src/pet/petPreferences.ts`
- Create: `app/src/pet/petWindowLayout.ts`
- Create: `app/tests/petPreferences.test.ts`
- Create: `app/tests/petWindowLayout.test.ts`
- Modify: `app/package.json`

**Interfaces:**
- Produces: `PetPreferences`, `PetShortcut`, `readPetPreferences()`, `writePetPreferences()`, `getConfiguredShortcuts()`.
- Produces: `getCompactPetSize(scale)`, `getExpandedPetLayout(scale, placement)`, `chooseMenuPlacement(windowX, windowWidth, monitorX, monitorWidth)`.

- [ ] **Step 1: Write failing preference tests**

```ts
test("hides incomplete shortcuts and caps configured shortcuts at three", () => {
  const shortcuts = getConfiguredShortcuts(inputWithFourAndOneInvalid);
  assert.equal(shortcuts.length, 3);
});
```

- [ ] **Step 2: Run preference tests and confirm RED**

Run: `node --experimental-strip-types --test tests/petPreferences.test.ts`
Expected: FAIL because `petPreferences.ts` does not exist.

- [ ] **Step 3: Implement preferences and validation**

Implement a versioned parser with `scale` clamped to `0.7..1.3`, three shortcut
slots, and action-specific validation for `javis`, `target`, `url`, and
`hotkey`.

- [ ] **Step 4: Run preference tests and confirm GREEN**

Run: `node --experimental-strip-types --test tests/petPreferences.test.ts`
Expected: PASS.

- [ ] **Step 5: Write and run failing layout tests**

Test compact padding, scale extremes, expanded width, and left/right placement.
Expected: FAIL before `petWindowLayout.ts` exists.

- [ ] **Step 6: Implement layout functions and confirm GREEN**

Run: `node --experimental-strip-types --test tests/petWindowLayout.test.ts`
Expected: PASS.

### Task 2: Dynamic Tauri Pet Window

**Files:**
- Modify: `app/src/desktop/windowMode.ts`
- Test: `app/tests/petWindowLayout.test.ts`

**Interfaces:**
- Consumes: layout functions from Task 1.
- Produces: `setPetScale(scale)`, `setPetMenuExpanded(expanded, placement)`.

- [ ] **Step 1: Extend layout tests with restore behavior**
- [ ] **Step 2: Run tests and confirm RED**
- [ ] **Step 3: Add scale-aware compact and expanded sizing**
- [ ] **Step 4: Reposition the window so expansion does not move the pet**
- [ ] **Step 5: Run layout tests and TypeScript build**

Run: `node --experimental-strip-types --test tests/petWindowLayout.test.ts`
and `pnpm run build`.
Expected: both PASS.

### Task 3: Pet Context Menu And Settings

**Files:**
- Modify: `app/src/pet/petTypes.ts`
- Modify: `app/src/pet/PetSurface.ts`
- Modify: `app/src/panels/ControlDrawer.ts`
- Modify: `app/src/main.ts`
- Modify: `app/src/styles.css`
- Create: `app/tests/petMenu.test.ts`

**Interfaces:**
- Consumes: preferences, validated shortcuts, and dynamic window APIs.
- Produces: menu open/close behavior, pet size settings, and shortcut editor.

- [ ] **Step 1: Write failing menu-model tests**

Verify fixed item order, configured-only custom items, and five-item maximum.

- [ ] **Step 2: Run menu tests and confirm RED**
- [ ] **Step 3: Replace the old toolbar with the hidden context menu**
- [ ] **Step 4: Add close paths, stagger animation, and screen-edge placement**
- [ ] **Step 5: Add pet size and three shortcut editors to Settings**
- [ ] **Step 6: Wire Settings, Code, Live, diagnostics, and custom callbacks**
- [ ] **Step 7: Run unit tests and TypeScript build**

### Task 4: Audited Native Shortcut Actions

**Files:**
- Modify: `app/src-tauri/src/main.rs`
- Create: `app/src-tauri/src/pet_actions.rs`
- Modify: `app/src-tauri/Cargo.toml`
- Test: Rust unit tests in `app/src-tauri/src/pet_actions.rs`

**Interfaces:**
- Consumes: validated `target`, `url`, or `hotkey` payloads.
- Produces: Tauri command `execute_pet_shortcut(kind, value)`.

- [ ] **Step 1: Write failing Rust validation tests**
- [ ] **Step 2: Run `cargo test` and confirm RED**
- [ ] **Step 3: Implement allowlisted native dispatch without shell strings**
- [ ] **Step 4: Register the Tauri command and application logging**
- [ ] **Step 5: Run `cargo test` and confirm GREEN**

### Task 5: Regression And Visual Verification

**Files:**
- Modify only files required by discovered regressions.

**Interfaces:**
- Consumes: completed feature.
- Produces: fresh verification evidence and screenshots.

- [ ] **Step 1: Run all Node pet tests**
- [ ] **Step 2: Run `pnpm run build`**
- [ ] **Step 3: Run `cargo test` and `cargo check`**
- [ ] **Step 4: Launch the Tauri development app**
- [ ] **Step 5: Verify compact boundary, scale extremes, menu direction, all close paths, fixed actions, configured-only shortcuts, drag, Live, Code, and skin**
- [ ] **Step 6: Capture compact and expanded screenshots**
- [ ] **Step 7: Review the final diff for unrelated changes**
