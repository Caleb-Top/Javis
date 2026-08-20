import type { PetSkin } from "./petTypes";

const DEFAULT_PET_SKIN_ID = "javis-lightform";

export const petSkinRegistry: PetSkin[] = [
  {
    id: "javis-lightform",
    manifestId: "javis-lightform",
    kind: "procedural3d",
    name: "Javis Lightform",
    description: "A local procedural 3D avatar with no external model asset.",
    accent: "#61e9f0",
    fallbackClass: "skin-anime",
  },
  {
    id: "javis-anime",
    manifestId: "javis-anime",
    kind: "sprite2d",
    name: "Javis Neon Pilot",
    description: "The bundled transparent 2D Javis avatar.",
    idleAsset: "/pets/javis-anime/idle.png",
    sourceAtlasAsset: "/pets/javis-anime/walk-source.png",
    accent: "#61e9f0",
    fallbackClass: "skin-anime",
  },
  {
    id: "javis-orb",
    manifestId: "javis-orb",
    kind: "orb",
    name: "Javis Orb",
    description: "The zero-asset CSS orb fallback.",
    accent: "#aa78f1",
    fallbackClass: "skin-orb",
  },
];

export function getPetSkin(skinId: string): PetSkin {
  return petSkinRegistry.find((skin) => skin.id === skinId) || petSkinRegistry[0];
}

export function getDefaultPetSkin(): PetSkin {
  try {
    return getPetSkin(localStorage.getItem("javis.app.petSkin") || DEFAULT_PET_SKIN_ID);
  } catch {
    return petSkinRegistry[0];
  }
}
