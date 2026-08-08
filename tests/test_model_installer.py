import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from utils import config_api, model_installer


class ModelInstallerTests(unittest.TestCase):
    def _addon(self, root: Path) -> Path:
        payload = root / "runtime.zip"
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("local-ai/ollama/ollama.exe", b"test-runtime")
            archive.writestr("local-ai/models/example.bin", b"test-model")
        manifest = root / model_installer.ADDON_MANIFEST
        manifest.write_text(json.dumps({
            "schema": 1,
            "kind": "javis-local-model-addon",
            "title": "Test addon",
            "model": "test:1b",
            "payloads": [{
                "role": "runtime-and-model",
                "path": payload.name,
                "size": payload.stat().st_size,
                "sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
            }],
        }), encoding="utf-8")
        return manifest

    def test_plan_is_read_only_and_install_requires_explicit_consent(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            manifest = self._addon(root)
            target = root / "selected"
            plan = model_installer.plan_model_install({
                "source": "offline",
                "addon_path": str(manifest),
                "install_dir": str(target),
            })
            self.assertTrue(plan["ok"])
            self.assertFalse(target.exists())
            with self.assertRaises(PermissionError):
                model_installer.install_model({
                    "source": "offline",
                    "addon_path": str(manifest),
                    "install_dir": str(target),
                })

    def test_approved_offline_addon_installs_to_selected_directory(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            manifest = self._addon(root)
            target = root / "selected"
            data_root = root / "data"
            with (
                patch.object(model_installer, "_data_root", return_value=data_root),
                patch.object(model_installer, "_persist_installed_model_configuration") as persist_config,
            ):
                result = model_installer.install_model({
                    "source": "offline",
                    "addon_path": str(manifest),
                    "install_dir": str(target),
                    "approved": True,
                    "confirmation": model_installer.APPROVAL_MARKER,
                })
            self.assertTrue(result["ok"])
            self.assertTrue((target / "local-ai" / "ollama" / "ollama.exe").is_file())
            self.assertEqual((data_root / "local-ai-root.txt").read_text(encoding="utf-8"), str(target))
            self.assertEqual(result["base_url"], "http://127.0.0.1:11435/v1")
            self.assertTrue(result["configuration_applied"])
            persist_config.assert_called_once_with("test:1b", "http://127.0.0.1:11435/v1")
            progress = model_installer.get_model_install_progress()
            self.assertEqual(progress["state"], "completed")
            self.assertEqual(progress["percent"], 100)

    def test_addon_directory_is_detected_and_cannot_be_its_own_install_target(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            manifest = self._addon(root)
            detection = model_installer.detect_model_addon(str(root))

            self.assertTrue(detection["ok"])
            self.assertTrue(detection["detected"])
            self.assertEqual(detection["manifest_path"], str(manifest.resolve()))
            self.assertEqual(detection["model"], "test:1b")
            with self.assertRaisesRegex(ValueError, "不能是 R1 附加包来源目录"):
                model_installer.plan_model_install({
                    "source": "offline",
                    "addon_path": str(root),
                    "install_dir": str(root),
                })

    def test_missing_addon_detection_is_read_only(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            result = model_installer.detect_model_addon(str(root))
            self.assertTrue(result["ok"])
            self.assertFalse(result["detected"])
            self.assertEqual(list(root.iterdir()), [])

    def test_concurrent_install_submission_is_rejected(self):
        acquired = model_installer._INSTALL_LOCK.acquire(blocking=False)
        self.assertTrue(acquired)
        try:
            with self.assertRaisesRegex(RuntimeError, "已有本地模型安装任务"):
                model_installer.install_model({
                    "source": "offline",
                    "addon_path": "unused",
                    "install_dir": "C:\\Javis-Local-Models",
                    "approved": True,
                    "confirmation": model_installer.APPROVAL_MARKER,
                })
        finally:
            model_installer._INSTALL_LOCK.release()

    def test_existing_gguf_plan_guides_user_to_install_runtime_first(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            gguf = root / "existing-q4.gguf"
            gguf.write_bytes(b"gguf-test")
            result = model_installer.plan_model_install({
                "source": "local_gguf",
                "gguf_path": str(gguf),
                "install_dir": str(root / "models"),
            })

            self.assertTrue(result["ok"])
            self.assertEqual(result["source"], "local_gguf")
            self.assertEqual(result["download_bytes"], 0)
            self.assertFalse(result["runtime_ready"])

    def test_completed_install_profile_is_persisted_for_both_routes(self):
        with tempfile.TemporaryDirectory() as root_value:
            config_path = Path(root_value) / "config.yaml"
            with patch.object(config_api, "CONFIG_PATH", config_path):
                configured = config_api.set_model_connection_settings({
                    "share_live_code": False,
                    "routes": {
                        "live": {
                            "source": "local",
                            "local": {"model": "old-local", "base_url": "http://127.0.0.1:11434/v1"},
                            "remote": {"provider": "deepseek", "model": "deepseek-chat", "base_url": "https://api.deepseek.com/v1"},
                        },
                        "code": {
                            "source": "remote",
                            "local": {"model": "old-local", "base_url": "http://127.0.0.1:11434/v1"},
                            "remote": {"provider": "deepseek", "model": "deepseek-chat", "base_url": "https://api.deepseek.com/v1"},
                        },
                    },
                })
                self.assertTrue(configured["applied"])
                model_installer._persist_installed_model_configuration(
                    "deepseek-r1:8b",
                    "http://127.0.0.1:11435/v1",
                )
                saved = config_api.get_model_connection_settings()

            self.assertFalse(saved["share_live_code"])
            self.assertEqual(saved["routes"]["live"]["source"], "local")
            self.assertEqual(saved["routes"]["code"]["source"], "remote")
            self.assertEqual(saved["routes"]["live"]["local"]["model"], "deepseek-r1:8b")
            self.assertEqual(saved["routes"]["code"]["local"]["base_url"], "http://127.0.0.1:11435/v1")

    def test_zip_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            archive_path = root / "bad.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../escape.txt", b"bad")
            with self.assertRaises(ValueError):
                model_installer._safe_extract(archive_path, root / "target")


if __name__ == "__main__":
    unittest.main()
