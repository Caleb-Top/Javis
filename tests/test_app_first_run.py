import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "app"


class AppFirstRunTests(unittest.TestCase):
    def test_first_run_surface_records_one_time_confirmation(self):
        panel = (ROOT / "src" / "app" / "FirstRunPanel.ts").read_text(encoding="utf-8")

        self.assertIn('readPreference("firstRunComplete"', panel)
        self.assertIn('writePreference("firstRunComplete", true)', panel)
        self.assertIn('role", "dialog"', panel)
        self.assertIn('aria-modal", "true"', panel)
        self.assertIn('event.key === "Escape"', panel)
        self.assertIn('event.key === "Tab"', panel)
        self.assertIn("event.shiftKey", panel)
        self.assertIn(".focus()", panel)

    def test_first_run_surface_explains_paths_and_privacy_defaults(self):
        panel = (ROOT / "src" / "app" / "FirstRunPanel.ts").read_text(encoding="utf-8")

        for text in ["%APPDATA%\\Javis", "models", "workspace"]:
            self.assertIn(text, panel)
        self.assertIn("默认不保存原始麦克风音频", panel)
        self.assertIn("屏幕和相机原始画面默认不落盘", panel)
        self.assertIn("不会自动安装或下载依赖", panel)

    def test_first_run_is_wired_to_startup_and_settings(self):
        main = (ROOT / "src" / "main.ts").read_text(encoding="utf-8")
        settings = (ROOT / "src" / "settings" / "SettingsSurface.ts").read_text(encoding="utf-8")

        self.assertIn("createFirstRunPanel", main)
        self.assertIn("showOnFirstRun", main)
        self.assertIn("javis:open-first-run", main)
        self.assertIn("settings-privacy", settings)
        self.assertIn("onOpenPrivacy", settings)

    def test_first_run_css_is_full_height_and_scroll_safe(self):
        styles = (ROOT / "src" / "styles.css").read_text(encoding="utf-8")

        self.assertIn(".first-run-panel", styles)
        self.assertIn("position: fixed", styles)
        self.assertIn("overflow-y: auto", styles)
        self.assertIn("overflow-wrap: anywhere", styles)


if __name__ == "__main__":
    unittest.main()
