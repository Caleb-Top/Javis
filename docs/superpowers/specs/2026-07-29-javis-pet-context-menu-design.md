# Javis Pet Context Menu Design

## Goal

Turn pet mode into a compact, draggable desktop companion whose visible and
interactive window closely follows the animated pet. A right click reveals a
small animated command menu containing two fixed actions and up to three
configured user shortcuts.

## Confirmed Interaction

- The menu contains at most five items.
- `Settings` and `Code` are always present.
- Three optional shortcut slots appear only after they have valid
  configuration.
- The menu is absent from layout and hit testing until the pet is right
  clicked.
- The menu slides from the pet toward the available side of the screen.
- A second right click, outside click, Escape, action selection, or drag closes
  the menu.
- Left click still opens Live and dragging still moves the pet window.

## Pet Size And Window Boundary

- Settings exposes a pet size slider from 70% to 130% in 5% steps.
- The default is 100%.
- Size changes preview immediately and persist across restarts.
- The compact Tauri window is derived from the same size setting as the pet.
- Transparent padding outside the animated boundary is limited to 10 logical
  pixels per side.
- The old persistent toolbar and layout-consuming status bubble are removed.
- Status remains available as a short overlay that does not enlarge the normal
  pet window.
- Opening the context menu temporarily widens the Tauri window; closing it
  restores the compact dimensions.

## Menu Presentation

- Fixed order: Settings, Code, then configured shortcut slots 1 through 3.
- Buttons are compact pills with an icon, a single-line label, and a minimum
  36-pixel touch target.
- Items use a staggered 180ms slide and fade animation.
- The expansion direction is chosen from the pet window position and current
  monitor bounds.
- Reduced-motion users get an immediate transition.

## Shortcut Configuration

Each optional slot has a label and one action:

- Javis action: open Live, Code, Settings, or diagnostics.
- Application/file: open an existing local target with the operating system.
- URL: open an `http` or `https` address.
- Hotkey: send a normalized key combination.

Incomplete or invalid slots are stored for editing but never rendered in the
pet menu. Native actions are dispatched through audited Tauri commands; the
frontend does not execute arbitrary shell strings.

## Persistence

Preferences use a versioned JSON record under
`javis.app.petPreferences.v1`. Invalid or older data falls back to defaults
without preventing startup. Existing `javis.app.petSkin` data remains valid.

## Acceptance Criteria

1. Compact pet mode shows only the animated pet boundary in normal use.
2. The size slider updates the pet and Tauri window from 70% through 130%.
3. Right click shows exactly Settings, Code, and configured custom shortcuts.
4. No menu DOM is visible or interactive while closed.
5. Menu direction avoids the nearest monitor edge.
6. All close paths restore the compact window.
7. Settings and Code work from the menu.
8. Valid custom actions execute; invalid custom actions remain hidden.
9. Live left click, drag, skin, state animation, and Code remain functional.
10. TypeScript, unit tests, Rust tests, production build, and Tauri smoke checks
    pass before packaging.
