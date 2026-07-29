import type { PetSkin } from "./petTypes";

export const petSkinRegistry: PetSkin[] = [
  {
    id: "javis-anime",
    name: "Javis Neon Pilot",
    description: "原创动漫机甲管家皮肤。支持透明 PNG 或 v2 图集替换。",
    idleAsset: "/pets/javis-anime/idle.png",
    sourceAtlasAsset: "/pets/javis-anime/walk-source.png",
    accent: "#61e9f0",
    fallbackClass: "skin-anime",
  },
  {
    id: "javis-orb",
    name: "Javis Orb",
    description: "纯本地 CSS 能量球皮肤，始终可用的无资源兜底。",
    idleAsset: "",
    accent: "#aa78f1",
    fallbackClass: "skin-orb",
  },
];

export function getPetSkin(skinId: string): PetSkin {
  return petSkinRegistry.find((skin) => skin.id === skinId) || petSkinRegistry[0];
}

export function getDefaultPetSkin(): PetSkin {
  try {
    return getPetSkin(localStorage.getItem("javis.app.petSkin") || "javis-anime");
  } catch {
    return petSkinRegistry[0];
  }
}
