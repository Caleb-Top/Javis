import unittest
import json
from pathlib import Path


ROOT = Path("D:/Javis/app/src")


class AppOrbOnlyShellTests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_live_stage_mounts_the_webgl_orb_canvas(self):
        stage = self.read("live/LiveStage.ts")

        self.assertIn('id="live-orb-canvas"', stage)
        self.assertIn("live-orb-canvas", stage)
        self.assertIn("Javis Live 交流球", stage)
        canvas_markup = stage.split('id="live-orb-canvas"', 1)[1].split("</canvas>", 1)[0]
        self.assertNotIn("window-drag-region", canvas_markup)

    def test_renderer_uses_the_approved_cobalt_webgl_shape(self):
        renderer = self.read("live/LiveOrbRenderer.ts")

        for contract in [
            "webgl",
            "fragmentSource",
            "requestAnimationFrame",
            "accentDark",
            "accentMain",
            "accentLight",
            "mainRadius",
            "torusMask",
            "cobalt",
        ]:
            self.assertIn(contract, renderer)

    def test_live_mode_hides_the_old_shell_and_keeps_only_the_orb(self):
        styles = self.read("styles.css")

        self.assertIn('body[data-desktop-mode="live"] .live-orb-canvas', styles)
        self.assertIn('body[data-desktop-mode="live"] .status-rail', styles)
        self.assertIn('body[data-desktop-mode="live"] .command-area', styles)
        self.assertIn('body[data-desktop-mode="live"] #pet-surface-root', styles)
        live_rule = styles.split('body[data-desktop-mode="live"] .status-rail', 1)[1].split("}", 1)[0]
        self.assertIn("display: none", live_rule)

    def test_orb_gestures_drag_listen_and_open_code(self):
        main = self.read("main.ts")
        renderer = self.read("live/LiveOrbRenderer.ts")

        self.assertIn("installPointerDrag", main)
        self.assertIn("live-orb-canvas", main)
        self.assertIn("dblclick", main)
        self.assertIn("voiceCapture.toggle", main)
        self.assertIn("setState", renderer)

    def test_default_live_window_is_compact_and_borderless(self):
        mode = self.read("desktop/windowMode.ts")
        config = json.loads(Path("D:/Javis/app/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
        window = config["app"]["windows"][0]

        self.assertIn("getLiveSurfaceSize", mode)
        self.assertEqual(window["width"], 200)
        self.assertEqual(window["height"], 218)
        self.assertIn('mode === "live"', mode)
        self.assertIn("setAlwaysOnTop(true)", mode)
        self.assertTrue(window["transparent"])
        self.assertFalse(window["decorations"])
        self.assertFalse(window["shadow"])


if __name__ == "__main__":
    unittest.main()
