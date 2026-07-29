import { setSurfaceMenuExpanded } from "../desktop/windowMode.ts";
import { buildPetMenuItems, type PetMenuItem } from "../pet/petMenu.ts";
import {
  parsePetPreferences,
  readPetPreferences,
  type PetPreferences,
  type PetShortcut,
} from "../pet/petPreferences.ts";
import {
  closeSurfaceMenu,
  createClosedMenuState,
  openSurfaceMenu,
  type MenuSource,
  type SurfaceMenuState,
} from "./surfaceMenuState.ts";

const MENU_CLOSE_MS = 220;

export type SurfaceContextMenuOptions = {
  root: HTMLElement;
  onOpenSettings: () => void;
  onOpenCode: () => void;
  onExecuteShortcut: (shortcut: PetShortcut) => void | Promise<void>;
};

export type SurfaceContextMenuController = {
  open(source: MenuSource): Promise<void>;
  close(immediate?: boolean): Promise<void>;
  isOpen(): boolean;
  refresh(preferences?: PetPreferences): void;
  dispose(): void;
};

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character] || character);
}

export function createSurfaceContextMenu(
  options: SurfaceContextMenuOptions,
): SurfaceContextMenuController {
  let state: SurfaceMenuState = createClosedMenuState();
  let preferences = readPetPreferences();
  let menuItems: PetMenuItem[] = [];
  let closeSequence = 0;

  options.root.innerHTML = `
    <nav class="surface-context-menu" aria-label="Javis 快捷操作" hidden></nav>`;

  const menu = options.root.querySelector<HTMLElement>(".surface-context-menu")!;

  async function close(immediate = false): Promise<void> {
    if (!state.open && menu.hidden) return;
    const source = state.source;
    const sequence = ++closeSequence;
    state = closeSurfaceMenu(state);
    menu.dataset.open = "false";
    if (!immediate) {
      await new Promise((resolve) => window.setTimeout(resolve, MENU_CLOSE_MS));
    }
    if (sequence !== closeSequence) return;
    menu.hidden = true;
    options.root.dataset.open = "false";
    await setSurfaceMenuExpanded(source, false);
  }

  async function runMenuItem(item: PetMenuItem): Promise<void> {
    if (item.id === "settings") {
      options.onOpenSettings();
      await close(true);
      return;
    }
    if (item.id === "code") {
      options.onOpenCode();
      await close(true);
      return;
    }
    await close();
    if (item.shortcut) await options.onExecuteShortcut(item.shortcut);
  }

  function renderMenu(): void {
    menuItems = buildPetMenuItems(preferences);
    menu.innerHTML = menuItems.map((item, index) => `
      <button type="button" class="surface-menu-item" data-menu-id="${escapeHtml(item.id)}" style="--menu-index:${index}">
        <span class="surface-menu-icon surface-menu-icon-${item.icon}" aria-hidden="true"></span>
        <span>${escapeHtml(item.label)}</span>
      </button>`).join("");
    menu.querySelectorAll<HTMLButtonElement>(".surface-menu-item").forEach((button) => {
      button.addEventListener("click", () => {
        const item = menuItems.find((candidate) => candidate.id === button.dataset.menuId);
        if (item) void runMenuItem(item);
      });
    });
  }

  async function open(source: MenuSource): Promise<void> {
    if (state.open) {
      await close();
      return;
    }
    closeSequence += 1;
    renderMenu();
    const placement = await setSurfaceMenuExpanded(source, true);
    state = openSurfaceMenu(state, source, placement);
    options.root.dataset.open = "true";
    options.root.dataset.source = source;
    options.root.dataset.placement = placement;
    menu.hidden = false;
    window.requestAnimationFrame(() => {
      if (state.open) menu.dataset.open = "true";
    });
  }

  function refresh(nextPreferences?: PetPreferences): void {
    preferences = parsePetPreferences(nextPreferences || readPetPreferences());
    renderMenu();
  }

  const handleOutsidePointer = (event: PointerEvent): void => {
    if (!state.open || event.button === 2 || options.root.contains(event.target as Node)) return;
    void close();
  };
  const handleKeyDown = (event: KeyboardEvent): void => {
    if (!state.open || event.key !== "Escape") return;
    event.preventDefault();
    void close();
  };
  const handlePreferencesChanged = (event: Event): void => {
    refresh((event as CustomEvent<PetPreferences>).detail);
  };

  document.addEventListener("pointerdown", handleOutsidePointer);
  document.addEventListener("keydown", handleKeyDown);
  document.addEventListener("javis:pet-preferences-changed", handlePreferencesChanged);
  renderMenu();

  return {
    open,
    close,
    isOpen: () => state.open,
    refresh,
    dispose() {
      document.removeEventListener("pointerdown", handleOutsidePointer);
      document.removeEventListener("keydown", handleKeyDown);
      document.removeEventListener("javis:pet-preferences-changed", handlePreferencesChanged);
      options.root.innerHTML = "";
    },
  };
}
