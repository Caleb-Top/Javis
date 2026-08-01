import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_SRC = ROOT / "app" / "src"


class AppPetFirstVersionTests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (APP_SRC / relative).read_text(encoding="utf-8")

    def test_pet_skin_registry_defines_swappable_skin_contract(self):
        registry = self.read("pet/PetSkinRegistry.ts")
        types = self.read("pet/petTypes.ts")

        self.assertIn("export type PetSkin", types)
        self.assertIn("id: string", types)
        self.assertIn("idleAsset", types)
        self.assertIn("export const petSkinRegistry", registry)
        self.assertIn("javis-anime", registry)
        self.assertIn("javis-orb", registry)

    def test_pet_surface_has_live_states_skin_picker_and_action_hooks(self):
        surface = self.read("pet/PetSurface.ts")
        settings = self.read("settings/SettingsSurface.ts")

        for text in ["data-pet-state", "toggleSkin", "onOpenLive", "onOpenSettings", "onContextMenu"]:
            self.assertIn(text, surface)
        self.assertIn("pet-skin-select", settings)
        self.assertIn("idleAsset", surface)
        self.assertIn("sourceAtlasAsset", surface)
        self.assertIn("has-asset", surface)

    def test_static_anime_skin_is_scaled_inside_the_pet_viewport(self):
        styles = self.read("styles.css")
        selector = ".pet-sprite.skin-anime.has-asset"
        rule = styles.split(selector, 1)[1].split("}", 1)[0]

        self.assertIn("background-size: contain", rule)
        self.assertIn("background-position: center", rule)
        self.assertIn("background-repeat: no-repeat", rule)
        self.assertIn(".pet-skin-select", styles)

    def test_window_mode_has_compact_pet_and_live_mode(self):
        mode = self.read("desktop/windowMode.ts")

        self.assertIn('"live" | "pet" | "settings" | "code"', mode)
        self.assertIn("setAlwaysOnTop", mode)
        self.assertIn("setSize", mode)
        self.assertIn("desktopMode", mode)
        self.assertIn("isTauriRuntime", mode)
        self.assertIn("getCompactPetSize", mode)
        self.assertIn("getLiveSurfaceSize", mode)
        self.assertIn("setMinSize", mode)

    def test_pet_sprite_installs_a_direct_drag_gesture(self):
        surface = self.read("pet/PetSurface.ts")
        drag = self.read("desktop/windowDrag.ts")

        self.assertIn("installPointerDrag", surface)
        self.assertIn("spriteButton", surface)
        self.assertIn("pointermove", drag)
        self.assertIn("startDragging", drag)

    def test_live_stage_mounts_pet_surface_and_pet_mode_control(self):
        stage = self.read("live/LiveStage.ts")
        main = self.read("main.ts")

        self.assertIn("pet-surface-root", stage)
        self.assertIn("pet-mode-control", stage)
        self.assertIn("createPetSurface", main)
        self.assertIn("setDesktopMode", main)
        self.assertIn('setDesktopMode("code")', main)
        self.assertIn('javis:return-live', main)

    def test_tauri_window_allows_transparent_custom_shell(self):
        config = json.loads((ROOT / "app" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
        window = config["app"]["windows"][0]

        self.assertTrue(window["transparent"])
        self.assertFalse(window["decorations"])
        capabilities = json.loads((ROOT / "app" / "src-tauri" / "capabilities" / "default.json").read_text(encoding="utf-8"))
        self.assertIn("core:window:allow-set-min-size", capabilities["permissions"])

    def test_anime_skin_assets_and_attribution_are_packaged(self):
        skin_dir = ROOT / "app" / "public" / "pets" / "javis-anime"
        manifest = json.loads((skin_dir / "skin.json").read_text(encoding="utf-8"))
        attribution = (skin_dir / "ATTRIBUTION.md").read_text(encoding="utf-8")

        self.assertTrue((skin_dir / "idle.png").is_file())
        self.assertTrue((skin_dir / "walk-source.png").is_file())
        self.assertEqual(manifest["format"], "portrait-skin-v1")
        self.assertIn("CC0", attribution)


if __name__ == "__main__":
    unittest.main()
