import type { LiveState } from "../live/liveState";
import { setPetScale } from "../desktop/windowMode";
import { installPointerDrag } from "../desktop/windowDrag";
import {
  formatSurfaceStatus,
  type RuntimeSnapshot,
} from "../state/runtimeStateTypes.ts";
import {
  parsePetPreferences,
  readPetPreferences,
  type PetPreferences,
} from "./petPreferences.ts";
import { getDefaultPetSkin, getPetSkin, petSkinRegistry } from "./PetSkinRegistry";
import type { PetSurfaceController, PetSurfaceOptions, PetSkin } from "./petTypes";

function saveSkin(skinId: string): void {
  try {
    localStorage.setItem("javis.app.petSkin", skinId);
  } catch {
    // A locked-down WebView must still keep the pet usable.
  }
}

export function createPetSurface(options: PetSurfaceOptions): PetSurfaceController {
  let currentSkin = getDefaultPetSkin();
  let currentState: LiveState = "idle";
  let preferences = readPetPreferences();

  options.root.innerHTML = `
    <section class="pet-surface" data-pet-state="idle" aria-label="Javis desktop pet">
      <div class="pet-anchor">
        <div class="pet-boundary" aria-hidden="true"></div>
        <button type="button" class="pet-sprite-button" aria-label="Open Javis Live" title="Open Javis Live">
          <span class="pet-sprite" role="img" aria-label="Javis pet"></span>
         </button>
         <div class="pet-status" aria-live="polite"></div>
       </div>
     </section>`;

  const surface = options.root.querySelector<HTMLElement>(".pet-surface")!;
  const spriteButton = options.root.querySelector<HTMLButtonElement>(".pet-sprite-button")!;
  const sprite = options.root.querySelector<HTMLElement>(".pet-sprite")!;
  const status = options.root.querySelector<HTMLElement>(".pet-status")!;

  function renderAsset(): void {
    const animate = currentState !== "idle" &&
      currentState !== "offline" &&
      Boolean(currentSkin.sourceAtlasAsset);
    const asset = animate ? currentSkin.sourceAtlasAsset : currentSkin.idleAsset;
    sprite.dataset.assetMode = animate ? "atlas" : "idle";
    sprite.style.backgroundImage = asset ? `url("${asset}")` : "none";
  }

  function applySkin(skin: PetSkin): void {
    currentSkin = skin;
    surface.dataset.skin = skin.id;
    sprite.className = `pet-sprite ${skin.fallbackClass}${skin.idleAsset ? " has-asset" : ""}`;
    sprite.style.setProperty("--pet-accent", skin.accent);
    renderAsset();
    sprite.textContent = skin.idleAsset ? "" : "J";
    saveSkin(skin.id);
  }

  function setPreferences(next: PetPreferences): void {
    preferences = parsePetPreferences(next);
    void setPetScale(preferences.scale);
  }

  function setState(state: LiveState): void {
    setSnapshot({
      state,
      source: "ui",
      detail: "",
      updatedAt: Date.now(),
    });
  }

  function setSnapshot(snapshot: RuntimeSnapshot): void {
    const state = snapshot.state;
    currentState = state;
    surface.dataset.petState = state;
    renderAsset();
    status.textContent = formatSurfaceStatus(snapshot);
    status.title = snapshot.detail || "";
    status.dataset.visible = "true";
  }

  function setSkin(skinId: string): void {
    applySkin(getPetSkin(skinId));
  }

  function toggleSkin(): void {
    const index = petSkinRegistry.findIndex((skin) => skin.id === currentSkin.id);
    setSkin(petSkinRegistry[(index + 1) % petSkinRegistry.length].id);
  }

  let suppressOpen = false;
  const disposeDrag = installPointerDrag(spriteButton, () => {
    suppressOpen = true;
    window.setTimeout(() => { suppressOpen = false; }, 500);
  });

  const handlePreferencesChanged = (event: Event): void => {
    const detail = (event as CustomEvent<PetPreferences>).detail;
    setPreferences(detail || readPetPreferences());
  };
  const handleSkinChanged = (event: Event): void => {
    const skinId = (event as CustomEvent<string>).detail;
    if (skinId) setSkin(skinId);
  };

  spriteButton.addEventListener("click", (event) => {
    if (suppressOpen) {
      event.preventDefault();
      suppressOpen = false;
      return;
    }
    options.onOpenLive();
  });
  spriteButton.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    void options.onContextMenu();
  });
  document.addEventListener("javis:pet-preferences-changed", handlePreferencesChanged);
  document.addEventListener("javis:pet-skin-changed", handleSkinChanged);

  try {
    currentSkin = getPetSkin(localStorage.getItem("javis.app.petSkin") || "");
  } catch {
    currentSkin = getDefaultPetSkin();
  }
  applySkin(currentSkin);
  setPreferences(preferences);
  setState(currentState);

  return {
    setState,
    setSnapshot,
    setSkin,
    toggleSkin,
    openSkinPicker: options.onOpenSettings,
    dispose: () => {
      disposeDrag();
      document.removeEventListener("javis:pet-preferences-changed", handlePreferencesChanged);
      document.removeEventListener("javis:pet-skin-changed", handleSkinChanged);
      options.root.innerHTML = "";
    },
  };
}
