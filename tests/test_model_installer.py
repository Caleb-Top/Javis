import hashlib
import io
import json
import socket
import struct
import tempfile
import threading
import time
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

    def _gguf(
        self,
        path: Path,
        *,
        architecture: str = "qwen2",
        file_type: int = 15,
        magic: bytes = b"GGUF",
    ) -> Path:
        entries = [
            ("general.architecture", 8, architecture),
            ("general.file_type", 4, file_type),
        ]
        payload = bytearray(magic)
        payload.extend(struct.pack("<IQQ", 3, 1, len(entries)))
        for key, value_type, value in entries:
            encoded_key = key.encode("utf-8")
            payload.extend(struct.pack("<Q", len(encoded_key)))
            payload.extend(encoded_key)
            payload.extend(struct.pack("<I", value_type))
            if value_type == 8:
                encoded_value = str(value).encode("utf-8")
                payload.extend(struct.pack("<Q", len(encoded_value)))
                payload.extend(encoded_value)
            else:
                payload.extend(struct.pack("<I", int(value)))
        tensor_name = b"weight"
        payload.extend(struct.pack("<Q", len(tensor_name)))
        payload.extend(tensor_name)
        payload.extend(struct.pack("<I", 1))
        payload.extend(struct.pack("<Q", 1))
        payload.extend(struct.pack("<I", 0))  # GGML_TYPE_F32
        payload.extend(struct.pack("<Q", 0))
        payload.extend(b"\x00" * (-len(payload) % 32))
        payload.extend(struct.pack("<f", 1.0))
        path.write_bytes(payload)
        return path

    @staticmethod
    def _response(
        *,
        status: int = 200,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
    ):
        class Response:
            status_code = status

            def __init__(self) -> None:
                self.headers = dict(headers or {})
                self.raw = io.BytesIO(body)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

            def close(self) -> None:
                self.raw.close()

            def raise_for_status(self) -> None:
                if self.status_code >= 400:
                    raise RuntimeError(f"HTTP {self.status_code}")

            def iter_content(self, chunk_size: int):
                while True:
                    chunk = self.raw.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk

        return Response()

    @staticmethod
    def _approved(plan: dict, values: dict) -> dict:
        return {
            **values,
            "plan_token": plan["plan_token"],
            "approved": True,
            "confirmation": model_installer.APPROVAL_MARKER,
        }

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
                values = {
                    "source": "offline",
                    "addon_path": str(manifest),
                    "install_dir": str(target),
                    "targets": ["live"],
                }
                plan = model_installer.plan_model_install(values)
                result = model_installer.install_model(self._approved(plan, values))
            self.assertTrue(result["ok"])
            self.assertTrue((target / "local-ai" / "ollama" / "ollama.exe").is_file())
            self.assertEqual((data_root / "local-ai-root.txt").read_text(encoding="utf-8"), str(target))
            self.assertEqual(result["base_url"], "http://127.0.0.1:11435/v1")
            self.assertTrue(result["configuration_applied"])
            persist_config.assert_called_once_with(
                "test:1b",
                "http://127.0.0.1:11435/v1",
                ["live"],
            )
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

    def test_active_install_can_pause_resume_and_cancel_by_job_id(self):
        entered = threading.Event()
        worker_errors: list[BaseException] = []
        checkpoint_count = 0

        def controlled_install(_values, _target):
            nonlocal checkpoint_count
            entered.set()
            while True:
                model_installer._install_control_checkpoint()
                checkpoint_count += 1
                time.sleep(0.005)

        def run_install():
            try:
                model_installer.install_model({
                    "source": "offline",
                    "addon_path": "unused",
                    "install_dir": str(Path(tempfile.gettempdir()) / "javis-control-test"),
                    "targets": [],
                    "approved": True,
                    "confirmation": model_installer.APPROVAL_MARKER,
                })
            except BaseException as error:  # Captured for assertions in the test thread.
                worker_errors.append(error)

        with (
            patch.object(model_installer, "_authorize_install_plan", return_value={"targets": []}),
            patch.object(model_installer, "_install_offline", side_effect=controlled_install),
        ):
            worker = threading.Thread(target=run_install, daemon=True)
            worker.start()
            self.assertTrue(entered.wait(timeout=1))
            progress = model_installer.get_model_install_progress()
            job_id = progress["job_id"]
            self.assertRegex(job_id, r"^[0-9a-f]{32}$")

            paused = model_installer.pause_model_install(job_id)
            self.assertTrue(paused["ok"])
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                if model_installer.get_model_install_progress()["state"] == "paused":
                    break
                time.sleep(0.005)
            self.assertEqual(model_installer.get_model_install_progress()["state"], "paused")
            count_while_paused = checkpoint_count
            time.sleep(0.03)
            self.assertEqual(checkpoint_count, count_while_paused)

            resumed = model_installer.resume_model_install(job_id)
            self.assertTrue(resumed["ok"])
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline and checkpoint_count == count_while_paused:
                time.sleep(0.005)
            self.assertGreater(checkpoint_count, count_while_paused)

            cancelled = model_installer.cancel_model_install(job_id)
            self.assertTrue(cancelled["ok"])
            worker.join(timeout=1)

        self.assertFalse(worker.is_alive())
        self.assertEqual(len(worker_errors), 1)
        self.assertIsInstance(worker_errors[0], model_installer.ModelInstallCancelled)
        final = model_installer.get_model_install_progress()
        self.assertEqual(final["job_id"], job_id)
        self.assertEqual(final["state"], "cancelled")
        self.assertTrue(final["cancelled"])

    def test_install_controls_reject_a_stale_job_id(self):
        stale = "0" * 32
        for control in (
            model_installer.pause_model_install,
            model_installer.resume_model_install,
            model_installer.cancel_model_install,
        ):
            with self.subTest(control=control.__name__):
                result = control(stale)
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"], "install_job_not_active")

    def test_cancel_request_stops_hash_and_extract_before_writing(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            source = root / "payload.bin"
            source.write_bytes(b"payload")
            archive = root / "payload.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("local-ai/model.bin", b"model")

            operations = (
                ("hash", lambda: model_installer._sha256(source), None),
                ("extract", lambda: model_installer._safe_extract(archive, root / "extracted"), root / "extracted"),
            )
            for name, operation, destination in operations:
                with self.subTest(operation=name):
                    job_id = model_installer._begin_install_job()
                    model_installer._set_progress("running", "test", 10)
                    self.assertTrue(model_installer.cancel_model_install(job_id)["ok"])
                    try:
                        with self.assertRaises(model_installer.ModelInstallCancelled):
                            operation()
                    finally:
                        model_installer._finish_install_job()
                    if destination is not None:
                        self.assertFalse(destination.exists())

    def test_existing_gguf_plan_guides_user_to_install_runtime_first(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            gguf = self._gguf(root / "existing-q4.gguf")
            result = model_installer.plan_model_install({
                "source": "local_gguf",
                "gguf_path": str(gguf),
                "install_dir": str(root / "models"),
                "targets": [],
            })

            self.assertTrue(result["ok"])
            self.assertEqual(result["source"], "local_gguf")
            self.assertEqual(result["download_bytes"], 0)
            self.assertFalse(result["runtime_ready"])
            self.assertEqual(result["architecture"], "qwen2")
            self.assertEqual(result["quantization"], "Q4_K_M")
            self.assertRegex(result["sha256"], r"^[0-9a-f]{64}$")

    def test_completed_install_only_updates_explicit_route_targets(self):
        cases = (
            ([], "live-old", "code-old"),
            (["live"], "new-local", "code-old"),
            (["code"], "live-old", "new-local"),
            (["live", "code"], "new-local", "new-local"),
        )
        for targets, expected_live, expected_code in cases:
            with self.subTest(targets=targets), tempfile.TemporaryDirectory() as root_value:
                config_path = Path(root_value) / "config.yaml"
                with patch.object(config_api, "CONFIG_PATH", config_path):
                    configured = config_api.set_model_connection_settings({
                        "source": "local",
                        "local": {"model": "live-old", "base_url": "http://127.0.0.1:11434/v1"},
                        "remote": {
                            "provider": "deepseek",
                            "model": "deepseek-chat",
                            "base_url": "https://api.deepseek.com/v1",
                        },
                        "share_live_code": False,
                        "routes": {
                            "live": {
                                "source": "local",
                                "local": {"model": "live-old", "base_url": "http://127.0.0.1:11434/v1"},
                                "remote": {"provider": "deepseek", "model": "deepseek-chat", "base_url": "https://api.deepseek.com/v1"},
                            },
                            "code": {
                                "source": "remote",
                                "local": {"model": "code-old", "base_url": "http://127.0.0.1:11434/v1"},
                                "remote": {"provider": "deepseek", "model": "deepseek-chat", "base_url": "https://api.deepseek.com/v1"},
                            },
                        },
                    })
                    self.assertTrue(configured["applied"])
                    model_installer._persist_installed_model_configuration(
                        "new-local",
                        "http://127.0.0.1:11435/v1",
                        targets,
                    )
                    saved = config_api.get_model_connection_settings()

                self.assertFalse(saved["share_live_code"])
                self.assertEqual(saved["routes"]["live"]["source"], "local")
                self.assertEqual(saved["routes"]["code"]["source"], "remote")
                self.assertEqual(saved["routes"]["live"]["local"]["model"], expected_live)
                self.assertEqual(saved["routes"]["code"]["local"]["model"], expected_code)

    def test_install_requires_signed_plan_and_rejects_changed_directory_or_targets(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            manifest = self._addon(root)
            target = root / "selected"
            values = {
                "source": "offline",
                "addon_path": str(manifest),
                "install_dir": str(target),
                "targets": ["live"],
            }
            plan = model_installer.plan_model_install(values)
            self.assertRegex(plan["plan_token"], r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")

            with self.assertRaises(PermissionError):
                model_installer.install_model({
                    **values,
                    "approved": True,
                    "confirmation": model_installer.APPROVAL_MARKER,
                })
            with self.assertRaisesRegex(PermissionError, "计划"):
                model_installer.install_model(self._approved(plan, {
                    **values,
                    "install_dir": str(root / "changed"),
                }))
            with self.assertRaisesRegex(PermissionError, "计划"):
                model_installer.install_model(self._approved(plan, {
                    **values,
                    "targets": ["code"],
                }))
            self.assertFalse(target.exists())

    def test_signed_plan_is_invalidated_when_addon_hash_size_or_license_changes(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            manifest = self._addon(root)
            target = root / "selected"
            values = {
                "source": "offline",
                "addon_path": str(manifest),
                "install_dir": str(target),
                "targets": [],
            }
            plan = model_installer.plan_model_install(values)
            data = json.loads(manifest.read_text(encoding="utf-8"))
            payload = root / data["payloads"][0]["path"]
            with zipfile.ZipFile(payload, "a") as archive:
                archive.writestr("changed.bin", b"changed")
            data["license"] = "changed-license"
            data["payloads"][0]["size"] = payload.stat().st_size
            data["payloads"][0]["sha256"] = hashlib.sha256(payload.read_bytes()).hexdigest()
            manifest.write_text(json.dumps(data), encoding="utf-8")

            with self.assertRaisesRegex(PermissionError, "计划"):
                model_installer.install_model(self._approved(plan, values))
            self.assertFalse(target.exists())

    def test_offline_commit_rolls_back_files_and_pointer_when_configuration_fails(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            manifest = self._addon(root)
            target = root / "selected"
            existing = target / "local-ai" / "keep.txt"
            existing.parent.mkdir(parents=True)
            existing.write_text("keep", encoding="utf-8")
            data_root = root / "data"
            data_root.mkdir()
            pointer = data_root / "local-ai-root.txt"
            pointer.write_text("previous-root", encoding="utf-8")
            values = {
                "source": "offline",
                "addon_path": str(manifest),
                "install_dir": str(target),
                "targets": ["live", "code"],
            }
            plan = model_installer.plan_model_install(values)

            with (
                patch.object(model_installer, "_data_root", return_value=data_root),
                patch.object(
                    model_installer,
                    "_persist_installed_model_configuration",
                    side_effect=RuntimeError("config failed"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "config failed"):
                    model_installer.install_model(self._approved(plan, values))

            self.assertEqual(existing.read_text(encoding="utf-8"), "keep")
            self.assertFalse((target / "local-ai" / "ollama" / "ollama.exe").exists())
            self.assertEqual(pointer.read_text(encoding="utf-8"), "previous-root")
            self.assertEqual(model_installer.get_model_install_progress()["state"], "failed")

    def test_offline_plan_budgets_validated_uncompressed_bytes(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            payload = root / "compressed.zip"
            model_bytes = b"A" * (256 * 1024)
            with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("local-ai/ollama/ollama.exe", b"runtime")
                archive.writestr("local-ai/models/model.bin", model_bytes)
            manifest = root / model_installer.ADDON_MANIFEST
            manifest.write_text(json.dumps({
                "schema": 1,
                "kind": "javis-local-model-addon",
                "payloads": [{
                    "role": "runtime-and-model",
                    "path": payload.name,
                    "size": payload.stat().st_size,
                    "sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
                }],
            }), encoding="utf-8")

            with patch.object(model_installer, "MAX_ZIP_COMPRESSION_RATIO", 2000):
                plan = model_installer.plan_model_install({
                    "source": "offline",
                    "addon_path": str(manifest),
                    "install_dir": str(root / "selected"),
                    "targets": [],
                })

            self.assertEqual(plan["required_bytes"], len(model_bytes) + len(b"runtime"))
            self.assertGreater(plan["required_bytes"], payload.stat().st_size)

    def test_safe_extract_rejects_member_total_and_ratio_before_destination_exists(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            cases = []

            member_archive = root / "member.zip"
            with zipfile.ZipFile(member_archive, "w") as archive:
                archive.writestr("large.bin", b"x" * 65)
            cases.append((member_archive, {"MAX_ZIP_MEMBER_BYTES": 64}, "单文件"))

            total_archive = root / "total.zip"
            with zipfile.ZipFile(total_archive, "w") as archive:
                archive.writestr("one.bin", b"x" * 40)
                archive.writestr("two.bin", b"y" * 40)
            cases.append((total_archive, {"MAX_ZIP_TOTAL_BYTES": 64}, "总"))

            ratio_archive = root / "ratio.zip"
            with zipfile.ZipFile(ratio_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("bomb.bin", b"0" * 4096)
            cases.append((ratio_archive, {"MAX_ZIP_COMPRESSION_RATIO": 2}, "压缩比"))

            for index, (archive_path, limits, message) in enumerate(cases):
                destination = root / f"destination-{index}"
                patches = [
                    patch.object(model_installer, name, value, create=True)
                    for name, value in limits.items()
                ]
                for active_patch in patches:
                    active_patch.start()
                try:
                    with self.subTest(limit=message), self.assertRaisesRegex(ValueError, message):
                        model_installer._safe_extract(archive_path, destination)
                finally:
                    for active_patch in reversed(patches):
                        active_patch.stop()
                self.assertFalse(destination.exists())

    def test_record_failure_restores_config_files_and_pointer_without_temp_record(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            manifest = self._addon(root)
            target = root / "selected"
            existing = target / "local-ai" / "keep.txt"
            existing.parent.mkdir(parents=True)
            existing.write_text("keep", encoding="utf-8")
            data_root = root / "data"
            data_root.mkdir()
            pointer = data_root / "local-ai-root.txt"
            pointer.write_text("previous-root", encoding="utf-8")
            config_path = root / "config.yaml"
            config_path.write_bytes(b"original-config")
            values = {
                "source": "offline",
                "addon_path": str(manifest),
                "install_dir": str(target),
                "targets": ["live", "code"],
            }
            plan = model_installer.plan_model_install(values)
            real_replace = model_installer.os.replace

            def fail_record_replace(source, destination):
                if Path(destination).parent.name == "install-records":
                    raise OSError("record replace failed")
                return real_replace(source, destination)

            def change_config(*_args):
                config_path.write_bytes(b"changed-config")

            with (
                patch.object(model_installer, "_data_root", return_value=data_root),
                patch.object(config_api, "CONFIG_PATH", config_path),
                patch.object(model_installer, "_persist_installed_model_configuration", side_effect=change_config),
                patch.object(model_installer.os, "replace", side_effect=fail_record_replace),
            ):
                with self.assertRaisesRegex(OSError, "record replace failed"):
                    model_installer.install_model(self._approved(plan, values))

            self.assertEqual(config_path.read_bytes(), b"original-config")
            self.assertEqual(existing.read_text(encoding="utf-8"), "keep")
            self.assertFalse((target / "local-ai" / "ollama" / "ollama.exe").exists())
            self.assertFalse((target / "local-ai" / "install-records").exists())
            self.assertEqual(pointer.read_text(encoding="utf-8"), "previous-root")

    def test_gguf_security_gate_rejects_bad_magic_architecture_and_quantization(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            cases = (
                (self._gguf(root / "bad-magic.gguf", magic=b"NOPE"), "GGUF"),
                (self._gguf(root / "bad-arch.gguf", architecture="unknown_arch"), "架构"),
                (self._gguf(root / "bad-quant.gguf", file_type=999), "量化"),
            )
            for gguf, message in cases:
                with self.subTest(gguf=gguf.name), self.assertRaisesRegex(ValueError, message):
                    model_installer.plan_model_install({
                        "source": "local_gguf",
                        "gguf_path": str(gguf),
                        "install_dir": str(root / "models"),
                        "targets": [],
                    })

    def test_gguf_security_gate_rejects_missing_tensor_table_and_truncated_tensor_data(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            short = root / "forged-header.gguf"
            short.write_bytes(b"GGUF" + struct.pack("<IQQ", 3, 1, 0))
            truncated = self._gguf(root / "truncated.gguf")
            truncated.write_bytes(truncated.read_bytes()[:-1])

            for gguf in (short, truncated):
                with self.subTest(gguf=gguf.name), self.assertRaisesRegex(ValueError, "不完整|张量"):
                    model_installer.plan_model_install({
                        "source": "local_gguf",
                        "gguf_path": str(gguf),
                        "install_dir": str(root / "models"),
                        "targets": [],
                    })

    def test_local_gguf_revalidates_token_before_ollama_and_cleans_staged_files(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            gguf = self._gguf(root / "model.gguf")
            target = root / "selected"
            runtime = target / "local-ai" / "ollama" / "ollama.exe"
            runtime.parent.mkdir(parents=True)
            runtime.write_bytes(b"runtime")
            data_root = root / "data"
            values = {
                "source": "local_gguf",
                "gguf_path": str(gguf),
                "install_dir": str(target),
                "targets": [],
            }
            plan = model_installer.plan_model_install(values)
            authorized = model_installer._read_plan_token(plan["plan_token"])

            with (
                patch.object(model_installer, "_data_root", return_value=data_root),
                patch.object(
                    model_installer,
                    "_read_plan_token",
                    side_effect=[authorized, PermissionError("token expired before create")],
                ),
                patch.object(model_installer, "_adapt_gguf_with_ollama") as adapt,
            ):
                with self.assertRaisesRegex(PermissionError, "expired"):
                    model_installer.install_model(self._approved(plan, values))

            adapt.assert_not_called()
            self.assertFalse((target / "local-ai" / "imported" / gguf.name).exists())
            self.assertFalse((data_root / "local-ai-root.txt").exists())
            self.assertEqual(list(target.parent.glob(".javis-model-staging-*")), [])

    def test_local_gguf_revalidates_token_again_before_final_pointer_commit(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            gguf = self._gguf(root / "model.gguf")
            target = root / "selected"
            runtime = target / "local-ai" / "ollama" / "ollama.exe"
            runtime.parent.mkdir(parents=True)
            runtime.write_bytes(b"runtime")
            data_root = root / "data"
            values = {
                "source": "local_gguf",
                "gguf_path": str(gguf),
                "install_dir": str(target),
                "targets": [],
            }
            plan = model_installer.plan_model_install(values)
            authorized = model_installer._read_plan_token(plan["plan_token"])

            with (
                patch.object(model_installer, "_data_root", return_value=data_root),
                patch.object(
                    model_installer,
                    "_read_plan_token",
                    side_effect=[
                        authorized,
                        authorized,
                        PermissionError("token expired before pointer"),
                    ],
                ),
                patch.object(model_installer, "_adapt_gguf_with_ollama") as adapt,
            ):
                with self.assertRaisesRegex(PermissionError, "pointer"):
                    model_installer.install_model(self._approved(plan, values))

            adapt.assert_called_once()
            self.assertFalse((target / "local-ai" / "imported" / gguf.name).exists())
            self.assertFalse((data_root / "local-ai-root.txt").exists())
            self.assertEqual(list(target.parent.glob(".javis-model-staging-*")), [])

    def test_local_gguf_configuration_failure_rolls_back_staged_model_store_and_pointer(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            gguf = self._gguf(root / "model.gguf")
            target = root / "selected"
            keep = target / "local-ai" / "keep.txt"
            keep.parent.mkdir(parents=True)
            keep.write_text("keep", encoding="utf-8")
            runtime = target / "local-ai" / "ollama" / "ollama.exe"
            runtime.parent.mkdir(parents=True, exist_ok=True)
            runtime.write_bytes(b"runtime")
            data_root = root / "data"
            data_root.mkdir()
            pointer = data_root / "local-ai-root.txt"
            pointer.write_text("old-root", encoding="utf-8")
            values = {
                "source": "local_gguf",
                "gguf_path": str(gguf),
                "install_dir": str(target),
                "targets": ["live", "code"],
            }
            plan = model_installer.plan_model_install(values)

            def fake_adapt(_runtime, models, _modelfile, _model_name):
                models.mkdir(parents=True, exist_ok=True)
                (models / "created-by-ollama").write_bytes(b"blob")

            with (
                patch.object(model_installer, "_data_root", return_value=data_root),
                patch.object(model_installer, "_adapt_gguf_with_ollama", side_effect=fake_adapt),
                patch.object(
                    model_installer,
                    "_persist_installed_model_configuration",
                    side_effect=RuntimeError("config failed"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "config failed"):
                    model_installer.install_model(self._approved(plan, values))

            self.assertEqual(keep.read_text(encoding="utf-8"), "keep")
            self.assertTrue(runtime.is_file())
            self.assertFalse((target / "local-ai" / "imported" / gguf.name).exists())
            self.assertFalse((target / "local-ai" / "imported" / "Javis-Modelfile").exists())
            self.assertFalse((target / "local-ai" / "models" / "created-by-ollama").exists())
            self.assertEqual(pointer.read_text(encoding="utf-8"), "old-root")

    def test_huggingface_requires_trusted_lfs_sha_and_never_enables_remote_code(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            base_info = {
                "siblings": [{"rfilename": "model-q4_k_m.gguf", "size": 123, "lfs": {"size": 123}}],
                "cardData": {"license": "apache-2.0"},
                "sha": "repo-commit",
            }
            values = {
                "source": "huggingface",
                "repo_id": "owner/model",
                "filename": "model-q4_k_m.gguf",
                "install_dir": str(root / "models"),
                "targets": ["code"],
            }
            with patch.object(model_installer, "_huggingface_model_info", return_value=base_info):
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    model_installer.plan_model_install(values)

            trusted = json.loads(json.dumps(base_info))
            trusted["siblings"][0]["lfs"]["sha256"] = "a" * 64
            with patch.object(model_installer, "_huggingface_model_info", return_value=trusted):
                plan = model_installer.plan_model_install(values)
                self.assertFalse(plan["trust_remote_code"])
                self.assertEqual(plan["license"], "apache-2.0")
                self.assertEqual(plan["sha256"], "a" * 64)
                with self.assertRaisesRegex(ValueError, "remote code"):
                    model_installer.plan_model_install({**values, "trust_remote_code": True})

    def test_huggingface_rejects_private_redirect_and_oversize_metadata(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            target = root / "selected"
            runtime = target / "local-ai" / "ollama" / "ollama.exe"
            runtime.parent.mkdir(parents=True)
            runtime.write_bytes(b"runtime")
            gguf_bytes = self._gguf(root / "download.gguf").read_bytes()
            info = {
                "siblings": [{
                    "rfilename": "model.gguf",
                    "size": len(gguf_bytes),
                    "lfs": {
                        "size": len(gguf_bytes),
                        "sha256": hashlib.sha256(gguf_bytes).hexdigest(),
                    },
                }],
                "cardData": {"license": "apache-2.0"},
                "sha": "fixed-revision",
            }
            values = {
                "source": "huggingface",
                "repo_id": "owner/model",
                "filename": "model.gguf",
                "install_dir": str(target),
                "targets": [],
            }

            with (
                patch.object(model_installer, "MAX_HF_DOWNLOAD_BYTES", len(gguf_bytes) - 1, create=True),
                patch.object(model_installer, "_huggingface_model_info", return_value=info),
            ):
                with self.assertRaisesRegex(ValueError, "大小|上限"):
                    model_installer.plan_model_install(values)

            with patch.object(model_installer, "_huggingface_model_info", return_value=info):
                plan = model_installer.plan_model_install(values)
                redirect = self._response(
                    status=302,
                    headers={"Location": "http://127.0.0.1/private-model.gguf"},
                )
                with (
                    patch.object(model_installer.requests, "get", return_value=redirect),
                    patch.object(
                        model_installer.socket,
                        "getaddrinfo",
                        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
                    ),
                    patch.object(model_installer, "_adapt_gguf_with_ollama") as adapt,
                ):
                    with self.assertRaisesRegex(ValueError, "HTTPS|私有|公网|不安全"):
                        model_installer.install_model(self._approved(plan, values))

            adapt.assert_not_called()
            self.assertFalse((target / "local-ai" / "huggingface" / "owner--model" / "model.gguf").exists())

    def test_huggingface_stream_overrun_never_commits_partial_model(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            target = root / "selected"
            runtime = target / "local-ai" / "ollama" / "ollama.exe"
            runtime.parent.mkdir(parents=True)
            runtime.write_bytes(b"runtime")
            gguf_bytes = self._gguf(root / "download.gguf").read_bytes()
            info = {
                "siblings": [{
                    "rfilename": "model.gguf",
                    "size": len(gguf_bytes),
                    "lfs": {
                        "size": len(gguf_bytes),
                        "sha256": hashlib.sha256(gguf_bytes).hexdigest(),
                    },
                }],
                "sha": "fixed-revision",
            }
            values = {
                "source": "huggingface",
                "repo_id": "owner/model",
                "filename": "model.gguf",
                "install_dir": str(target),
                "targets": [],
            }
            with patch.object(model_installer, "_huggingface_model_info", return_value=info):
                plan = model_installer.plan_model_install(values)
                response = self._response(status=200, body=gguf_bytes + b"overflow")
                with (
                    patch.object(model_installer.requests, "get", return_value=response),
                    patch.object(
                        model_installer.socket,
                        "getaddrinfo",
                        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
                    ),
                    patch.object(model_installer, "_adapt_gguf_with_ollama") as adapt,
                ):
                    with self.assertRaisesRegex(RuntimeError, "大小|上限"):
                        model_installer.install_model(self._approved(plan, values))

            adapt.assert_not_called()
            self.assertFalse((target / "local-ai" / "huggingface" / "owner--model" / "model.gguf").exists())
            self.assertEqual(list(target.parent.glob(".javis-model-staging-*")), [])

    def test_huggingface_download_resumes_a_partial_file_with_http_range(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            destination = root / "model.gguf.part"
            destination.write_bytes(b"first-")
            response = self._response(
                status=206,
                body=b"second",
                headers={
                    "Content-Length": "6",
                    "Content-Range": "bytes 6-11/12",
                    "ETag": '"fixed-etag"',
                },
            )
            with (
                patch.object(model_installer.requests, "get", return_value=response) as get,
                patch.object(
                    model_installer.socket,
                    "getaddrinfo",
                    return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
                ),
            ):
                model_installer._download_hf_file(
                    "https://huggingface.co/owner/model/resolve/fixed/model.gguf",
                    {},
                    destination,
                    12,
                )

            self.assertEqual(destination.read_bytes(), b"first-second")
            sent_headers = get.call_args.kwargs["headers"]
            self.assertEqual(sent_headers["Range"], "bytes=6-")
            self.assertEqual(model_installer.get_model_install_progress()["completed_bytes"], 12)

    def test_huggingface_stream_interruption_keeps_only_a_resumable_part(self):
        class InterruptedResponse:
            status_code = 200
            headers = {"Content-Length": "12", "ETag": '"fixed-etag"'}

            def iter_content(self, _chunk_size):
                yield b"first-"
                raise model_installer.requests.ConnectionError("connection dropped")

            def close(self):
                pass

            def raise_for_status(self):
                pass

        with tempfile.TemporaryDirectory() as root_value:
            destination = Path(root_value) / "model.gguf.part"
            with (
                patch.object(model_installer.requests, "get", return_value=InterruptedResponse()),
                patch.object(
                    model_installer.socket,
                    "getaddrinfo",
                    return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
                ),
            ):
                with self.assertRaises(model_installer.requests.ConnectionError):
                    model_installer._download_hf_file(
                        "https://huggingface.co/owner/model/resolve/fixed/model.gguf",
                        {},
                        destination,
                        12,
                    )

            self.assertEqual(destination.read_bytes(), b"first-")

    def test_huggingface_install_resumes_from_a_stable_checkpoint(self):
        class InterruptedResponse:
            status_code = 200

            def __init__(self, first: bytes, total: int):
                self.first = first
                self.headers = {"Content-Length": str(total), "ETag": '"fixed-etag"'}

            def iter_content(self, _chunk_size):
                yield self.first
                raise model_installer.requests.ConnectionError("connection dropped")

            def close(self):
                pass

            def raise_for_status(self):
                pass

        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            target = root / "selected"
            runtime = target / "local-ai" / "ollama" / "ollama.exe"
            runtime.parent.mkdir(parents=True)
            runtime.write_bytes(b"runtime")
            gguf_bytes = self._gguf(root / "download.gguf").read_bytes()
            split = max(1, len(gguf_bytes) // 2)
            info = {
                "siblings": [{
                    "rfilename": "model.gguf",
                    "size": len(gguf_bytes),
                    "lfs": {
                        "size": len(gguf_bytes),
                        "sha256": hashlib.sha256(gguf_bytes).hexdigest(),
                    },
                }],
                "sha": "fixed-revision",
            }
            values = {
                "source": "huggingface",
                "repo_id": "owner/model",
                "filename": "model.gguf",
                "install_dir": str(target),
                "targets": [],
            }
            with patch.object(model_installer, "_huggingface_model_info", return_value=info):
                plan = model_installer.plan_model_install(values)
                resumed_response = self._response(
                    status=206,
                    body=gguf_bytes[split:],
                    headers={
                        "Content-Length": str(len(gguf_bytes) - split),
                        "Content-Range": f"bytes {split}-{len(gguf_bytes) - 1}/{len(gguf_bytes)}",
                    },
                )
                with (
                    patch.object(model_installer, "_data_root", return_value=root / "data"),
                    patch.object(
                        model_installer.requests,
                        "get",
                        side_effect=[InterruptedResponse(gguf_bytes[:split], len(gguf_bytes)), resumed_response],
                    ) as get,
                    patch.object(
                        model_installer.socket,
                        "getaddrinfo",
                        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
                    ),
                    patch.object(model_installer, "_adapt_gguf_with_ollama"),
                ):
                    with self.assertRaises(model_installer.requests.ConnectionError):
                        model_installer.install_model(self._approved(plan, values))
                    checkpoints = list((target.parent / ".javis-model-downloads").glob("*.part"))
                    self.assertEqual(len(checkpoints), 1)
                    self.assertEqual(checkpoints[0].read_bytes(), gguf_bytes[:split])

                    result = model_installer.install_model(self._approved(plan, values))

            self.assertTrue(result["ok"])
            self.assertEqual(get.call_args_list[1].kwargs["headers"]["Range"], f"bytes={split}-")
            self.assertFalse((target.parent / ".javis-model-downloads").exists())

    def test_huggingface_configuration_failure_rolls_back_download_model_store_and_pointer(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            target = root / "selected"
            keep = target / "local-ai" / "keep.txt"
            keep.parent.mkdir(parents=True)
            keep.write_text("keep", encoding="utf-8")
            runtime = target / "local-ai" / "ollama" / "ollama.exe"
            runtime.parent.mkdir(parents=True, exist_ok=True)
            runtime.write_bytes(b"runtime")
            data_root = root / "data"
            data_root.mkdir()
            pointer = data_root / "local-ai-root.txt"
            pointer.write_text("old-root", encoding="utf-8")
            gguf_bytes = self._gguf(root / "download.gguf").read_bytes()
            info = {
                "siblings": [{
                    "rfilename": "model.gguf",
                    "size": len(gguf_bytes),
                    "lfs": {
                        "size": len(gguf_bytes),
                        "sha256": hashlib.sha256(gguf_bytes).hexdigest(),
                    },
                }],
                "cardData": {"license": "apache-2.0"},
                "sha": "fixed-revision",
            }
            values = {
                "source": "huggingface",
                "repo_id": "owner/model",
                "filename": "model.gguf",
                "install_dir": str(target),
                "targets": ["live", "code"],
            }

            def fake_adapt(_runtime, models, _modelfile, _model_name):
                models.mkdir(parents=True, exist_ok=True)
                (models / "created-by-ollama").write_bytes(b"blob")

            with patch.object(model_installer, "_huggingface_model_info", return_value=info):
                plan = model_installer.plan_model_install(values)
                with (
                    patch.object(model_installer, "_data_root", return_value=data_root),
                    patch.object(
                        model_installer.requests,
                        "get",
                        return_value=self._response(status=200, body=gguf_bytes),
                    ),
                    patch.object(
                        model_installer.socket,
                        "getaddrinfo",
                        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
                    ),
                    patch.object(model_installer, "_adapt_gguf_with_ollama", side_effect=fake_adapt),
                    patch.object(
                        model_installer,
                        "_persist_installed_model_configuration",
                        side_effect=RuntimeError("config failed"),
                    ),
                ):
                    with self.assertRaisesRegex(RuntimeError, "config failed"):
                        model_installer.install_model(self._approved(plan, values))

            self.assertEqual(keep.read_text(encoding="utf-8"), "keep")
            self.assertTrue(runtime.is_file())
            self.assertFalse((target / "local-ai" / "huggingface" / "owner--model" / "model.gguf").exists())
            self.assertFalse((target / "local-ai" / "huggingface" / "owner--model" / "Javis-Modelfile").exists())
            self.assertFalse((target / "local-ai" / "models" / "created-by-ollama").exists())
            self.assertEqual(pointer.read_text(encoding="utf-8"), "old-root")
            self.assertEqual(list(target.parent.glob(".javis-model-staging-*")), [])

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
