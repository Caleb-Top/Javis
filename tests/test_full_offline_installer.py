import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.app_build_preflight import _shared_tool_root
from scripts.javis_release_version import load_version_contract, release_artifact_names


ROOT = Path(__file__).resolve().parents[1]
CURRENT_VERSION = load_version_contract(ROOT)["version"]
CURRENT_ARTIFACTS = release_artifact_names(CURRENT_VERSION)
DEPENDENCY_ROOT = _shared_tool_root(ROOT)


class FullOfflineInstallerTests(unittest.TestCase):
    def _model_fixture(self, root: Path) -> tuple[Path, Path]:
        model_root = root / "models"
        manifest = (
            model_root
            / "manifests"
            / "registry.ollama.ai"
            / "library"
            / "deepseek-r1"
            / "8b"
        )
        manifest.parent.mkdir(parents=True)
        blobs = model_root / "blobs"
        blobs.mkdir(parents=True)
        payloads = {
            "sha256-model": b"model-bytes",
            "sha256-config": b"config-bytes",
        }
        for name, content in payloads.items():
            (blobs / name).write_bytes(content)
        manifest.write_text(
            json.dumps(
                {
                    "schemaVersion": 2,
                    "config": {
                        "digest": "sha256:config",
                        "size": len(payloads["sha256-config"]),
                    },
                    "layers": [
                        {
                            "mediaType": "application/vnd.ollama.image.model",
                            "digest": "sha256:model",
                            "size": len(payloads["sha256-model"]),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return model_root, manifest

    def test_model_pack_contains_exact_manifest_and_every_referenced_blob(self):
        from scripts.javis_full_installer import collect_ollama_model

        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temp_dir:
            model_root, manifest = self._model_fixture(Path(temp_dir))
            expected_bytes = len(b"model-bytesconfig-bytes") + manifest.stat().st_size

            result = collect_ollama_model(model_root, "deepseek-r1:8b")

        self.assertEqual(result["model"], "deepseek-r1:8b")
        self.assertEqual(result["manifest"], manifest.relative_to(model_root).as_posix())
        self.assertEqual(
            result["files"],
            [
                "blobs/sha256-config",
                "blobs/sha256-model",
                "manifests/registry.ollama.ai/library/deepseek-r1/8b",
            ],
        )
        self.assertEqual(result["bytes"], expected_bytes)

    def test_model_pack_rejects_a_manifest_with_a_missing_blob(self):
        from scripts.javis_full_installer import collect_ollama_model

        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temp_dir:
            model_root, _ = self._model_fixture(Path(temp_dir))
            (model_root / "blobs" / "sha256-model").unlink()

            with self.assertRaisesRegex(FileNotFoundError, "sha256-model"):
                collect_ollama_model(model_root, "deepseek-r1:8b")

    def test_release_layout_separates_g_builds_from_d_install_testing(self):
        from scripts.javis_release_layout import release_layout

        layout = release_layout(ROOT, test_drive="D:")

        self.assertEqual(Path(layout["source_root"]).drive.upper(), "G:")
        for key in ("build_temp", "artifact_dir", "cargo_target", "package_output"):
            self.assertEqual(Path(layout[key]).drive.upper(), "G:", key)
        for key in ("delivery_dir", "delivery_installer", "install_test_root", "install_test_data"):
            self.assertEqual(Path(layout[key]).drive.upper(), "D:", key)
        self.assertEqual(Path(layout["package_output"]).name, CURRENT_ARTIFACTS["package"])
        self.assertEqual(Path(layout["delivery_dir"]).name, CURRENT_ARTIFACTS["delivery_directory"])

    def test_optional_r1_addon_has_a_consent_wizard_manifest(self):
        from scripts.javis_full_installer import build_r1_addon

        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temp_dir:
            temp = Path(temp_dir)
            model_root, _ = self._model_fixture(temp)
            ollama_root = temp / "ollama"
            ollama_root.mkdir()
            (ollama_root / "ollama.exe").write_bytes(b"ollama")
            output = temp / "release"
            report = build_r1_addon(
                model_root=model_root,
                ollama_root=ollama_root,
                output_dir=output,
                work_dir=temp / "work",
            )
            manifest = json.loads(
                (output / "Javis-R1-8B-Addon" / "Javis-R1-8B-Addon.manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(report["kind"], "javis-local-model-addon")
        self.assertEqual(manifest["model"], "deepseek-r1:8b")
        self.assertEqual(
            {item["role"] for item in manifest["payloads"]},
            {"ollama-runtime", "ollama-model"},
        )

    def test_release_layout_rejects_a_non_g_source_tree(self):
        from scripts.javis_release_layout import release_layout

        with self.assertRaisesRegex(ValueError, "G:"):
            release_layout(Path("E:/Javis"), test_drive="D:")

    def test_current_r1_model_is_complete_and_large_enough_to_be_real(self):
        from scripts.javis_full_installer import collect_ollama_model

        result = collect_ollama_model(DEPENDENCY_ROOT / "ollama_models" / "models", "deepseek-r1:8b")

        self.assertGreater(result["bytes"], 4 * 1024**3)
        self.assertGreaterEqual(len(result["files"]), 3)

    def test_model_payload_zip_contains_only_the_selected_model(self):
        from scripts.javis_full_installer import stage_model_payload

        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temp_dir:
            temp = Path(temp_dir)
            model_root, _ = self._model_fixture(temp)
            output = temp / "deepseek-r1-8b.zip"

            report = stage_model_payload(model_root, "deepseek-r1:8b", output)

            with zipfile.ZipFile(output) as archive:
                names = sorted(archive.namelist())
                embedded = json.loads(archive.read("local-ai/model-pack.json"))
        self.assertEqual(
            names,
            [
                "local-ai/model-pack.json",
                "local-ai/models/blobs/sha256-config",
                "local-ai/models/blobs/sha256-model",
                "local-ai/models/manifests/registry.ollama.ai/library/deepseek-r1/8b",
            ],
        )
        self.assertEqual(embedded["model"], "deepseek-r1:8b")
        self.assertEqual(report["sha256"], report["sha256"].lower())
        self.assertEqual(len(report["sha256"]), 64)

    def test_ollama_payload_preserves_the_portable_runtime_tree(self):
        from scripts.javis_full_installer import stage_ollama_payload

        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temp_dir:
            temp = Path(temp_dir)
            source = temp / "ollama"
            (source / "lib" / "ollama" / "cuda_v12").mkdir(parents=True)
            (source / "ollama.exe").write_bytes(b"exe")
            (source / "lib" / "ollama" / "ggml-base.dll").write_bytes(b"cpu")
            (source / "lib" / "ollama" / "cuda_v12" / "cublas.dll").write_bytes(b"gpu")
            output = temp / "ollama.zip"

            report = stage_ollama_payload(source, output)

            with zipfile.ZipFile(output) as archive:
                names = sorted(archive.namelist())
        self.assertEqual(
            names,
            [
                "local-ai/ollama/lib/ollama/cuda_v12/cublas.dll",
                "local-ai/ollama/lib/ollama/ggml-base.dll",
                "local-ai/ollama/ollama.exe",
            ],
        )
        self.assertEqual(report["files"], 3)

    def test_external_bundle_keeps_the_windows_executable_small(self):
        from scripts.javis_full_installer import build_external_bundle, read_external_bundle_index

        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temp_dir:
            temp = Path(temp_dir)
            stub = temp / "stub.exe"
            app_setup = temp / "app-setup.exe"
            model = temp / "model.zip"
            output = temp / "offline"
            stub.write_bytes(b"MZ-stub")
            app_setup.write_bytes(b"setup-payload")
            model.write_bytes(b"model-payload")

            manifest = build_external_bundle(
                stub,
                [("app-setup.exe", app_setup), ("model.zip", model)],
                output,
            )
            setup = output / CURRENT_ARTIFACTS["package"]
            parsed = read_external_bundle_index(setup)
            setup_bytes = setup.read_bytes()
            app_setup_bytes = (output / "payloads" / "app-setup.exe").read_bytes()
            model_bytes = (output / "payloads" / "model.zip").read_bytes()

        self.assertEqual(parsed, manifest)
        self.assertEqual(setup_bytes, b"MZ-stub")
        self.assertEqual(app_setup_bytes, b"setup-payload")
        self.assertEqual(model_bytes, b"model-payload")
        self.assertEqual(parsed["schema_version"], 2)
        self.assertEqual(parsed["payloads"][0]["path"], "payloads/app-setup.exe")

    def test_release_builder_emits_one_verified_executable_and_manifest(self):
        from scripts.javis_full_installer import build_offline_release, read_external_bundle_index

        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temp_dir:
            temp = Path(temp_dir)
            model_root, _ = self._model_fixture(temp)
            ollama_root = temp / "ollama"
            (ollama_root / "lib" / "ollama").mkdir(parents=True)
            (ollama_root / "ollama.exe").write_bytes(b"ollama")
            (ollama_root / "lib" / "ollama" / "ggml.dll").write_bytes(b"runtime")
            bootstrap = temp / "bootstrap.exe"
            setup = temp / "setup.exe"
            output = temp / "offline"
            bootstrap.write_bytes(b"MZ-bootstrap")
            setup.write_bytes(b"MZ-setup")

            report = build_offline_release(
                model_root=model_root,
                ollama_root=ollama_root,
                bootstrap=bootstrap,
                app_setup=setup,
                output_dir=output,
                work_dir=temp / "payloads",
                model="deepseek-r1:8b",
            )
            setup_path = output / CURRENT_ARTIFACTS["package"]
            parsed = read_external_bundle_index(setup_path)
            manifest_path = output / CURRENT_ARTIFACTS["offline_manifest"]
            persisted = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(
            [item["name"] for item in parsed["payloads"]],
            ["app-setup.exe", "ollama-runtime.zip", "deepseek-r1-8b.zip"],
        )
        self.assertEqual(report, persisted)
        self.assertEqual(report["model"]["model"], "deepseek-r1:8b")
        self.assertEqual(report["delivery"], "external-payload-folder")
        self.assertEqual(len(report["installer_sha256"]), 64)
        self.assertLess(report["installer_bytes"], 4 * 1024**3)


if __name__ == "__main__":
    unittest.main()
