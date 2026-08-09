import base64
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def green_gate_report(version: str = "3.0.1", source_commit: str = "a" * 40) -> dict:
    from scripts.javis_release_gate import REQUIRED_GATE_IDS, release_gate_plan_id

    return {
        "schema_version": 2,
        "overall_status": "PASS",
        "version": version,
        "source_commit": source_commit,
        "required_gate_ids": list(REQUIRED_GATE_IDS),
        "plan_id": release_gate_plan_id(version, source_commit),
        "checks": [
            {"id": check_id, "status": "PASS", "required": True}
            for check_id in REQUIRED_GATE_IDS
        ],
    }


class ReleaseVersionContractTests(unittest.TestCase):
    def test_current_release_surfaces_match_the_authoritative_version(self):
        from scripts.javis_release_version import (
            collect_version_drift,
            load_version_contract,
            release_artifact_names,
        )

        contract = load_version_contract(ROOT)
        version = contract["version"]
        artifacts = release_artifact_names(version)

        self.assertEqual(collect_version_drift(ROOT), [])
        self.assertEqual(artifacts["tauri_nsis"], f"Javis_{version}_x64-setup.exe")
        self.assertEqual(artifacts["package"], f"Javis-v{version}-Setup.exe")
        self.assertEqual(artifacts["candidate_manifest"], f"Javis-v{version}-Candidate.manifest.json")

    def test_sync_updates_every_declared_surface_from_one_source(self):
        from scripts.javis_release_version import (
            VERSION_TARGETS,
            collect_version_drift,
            load_version_contract,
            sync_release_version,
        )

        with tempfile.TemporaryDirectory() as td:
            fixture = Path(td)
            for relative in ("release/version.json", *VERSION_TARGETS):
                source = ROOT / relative
                target = fixture / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)

            sync_release_version(fixture, "9.8.7")

            self.assertEqual(load_version_contract(fixture)["version"], "9.8.7")
            self.assertEqual(collect_version_drift(fixture), [])
            build_source = (fixture / "scripts/build_javis_app_v3.ps1").read_text(encoding="utf-8")
            self.assertIn("Javis_9.8.7_x64-setup.exe", build_source)
            self.assertIn("Javis-v9.8.7-Setup.exe", build_source)

    def test_failed_version_sync_restores_every_surface(self):
        from scripts.javis_release_version import (
            TEXT_VERSION_TARGETS,
            VERSION_TARGETS,
            load_version_contract,
            sync_release_version,
        )

        with tempfile.TemporaryDirectory() as td:
            fixture = Path(td)
            for relative in ("release/version.json", *VERSION_TARGETS):
                source = ROOT / relative
                target = fixture / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            broken = fixture / TEXT_VERSION_TARGETS[-1]
            current_version = load_version_contract(fixture)["version"]
            broken.write_text(
                broken.read_text(encoding="utf-8").replace(current_version, "missing-version"),
                encoding="utf-8",
            )
            before = {
                relative: (fixture / relative).read_bytes()
                for relative in ("release/version.json", *VERSION_TARGETS)
            }

            with self.assertRaisesRegex(ValueError, "old version"):
                sync_release_version(fixture, "9.8.7")

            after = {
                relative: (fixture / relative).read_bytes()
                for relative in ("release/version.json", *VERSION_TARGETS)
            }
            self.assertEqual(after, before)

    def test_version_sync_lock_is_exclusive_and_crash_journal_is_recoverable(self):
        from scripts.javis_release_version import (
            VERSION_TARGETS,
            recover_version_transaction,
            version_sync_lock,
        )

        with tempfile.TemporaryDirectory() as td:
            fixture = Path(td)
            tracked = ("release/version.json", *VERSION_TARGETS)
            for relative in tracked:
                source = ROOT / relative
                target = fixture / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            with version_sync_lock(fixture):
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    with version_sync_lock(fixture):
                        self.fail("second version transaction acquired the lock")

            original = (fixture / "app/package.json").read_bytes()
            journal = {
                "schema_version": 1,
                "originals": {
                    "app/package.json": base64.b64encode(original).decode("ascii"),
                },
            }
            journal_path = fixture / "release/.version-sync-journal.json"
            journal_path.write_text(json.dumps(journal), encoding="utf-8")
            (fixture / "app/package.json").write_bytes(b"partial-version-write")

            self.assertTrue(recover_version_transaction(fixture))
            self.assertEqual((fixture / "app/package.json").read_bytes(), original)
            self.assertFalse(journal_path.exists())

    def test_comment_cannot_mask_a_stale_effective_version_assignment(self):
        from scripts.javis_release_version import VERSION_TARGETS, collect_version_drift, load_version_contract

        with tempfile.TemporaryDirectory() as td:
            fixture = Path(td)
            for relative in ("release/version.json", *VERSION_TARGETS):
                source = ROOT / relative
                target = fixture / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            version = load_version_contract(fixture)["version"]
            build = fixture / "scripts/build_javis_app_v3.ps1"
            source = build.read_text(encoding="utf-8")
            expected = f"Javis_{version}_x64-setup.exe"
            build.write_text(
                f"# expected artifact {expected}\n" + source.replace(expected, "Javis_2.9.9_x64-setup.exe", 1),
                encoding="utf-8",
            )

            drift = collect_version_drift(fixture)

            self.assertTrue(any("InnerInstaller" in item for item in drift), drift)

    def test_release_contract_tests_do_not_hardcode_the_current_version(self):
        from scripts.javis_release_version import load_version_contract

        version = load_version_contract(ROOT)["version"]
        for relative in (
            "tests/test_app_v1_release.py",
            "tests/test_app_v3_integrated_release.py",
            "tests/test_full_offline_installer.py",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn(f'"{version}"', source, relative)
            self.assertNotIn(f"Javis-v{version}", source, relative)


class WorktreePreflightContractTests(unittest.TestCase):
    def _fixture(self, base: Path) -> tuple[Path, Path]:
        shared = base / "main"
        worktree = base / "worktree"
        git_dir = shared / ".git" / "worktrees" / "fixture"
        git_dir.mkdir(parents=True)
        worktree.mkdir(parents=True)
        (worktree / ".git").write_text(f"gitdir: {git_dir}\n", encoding="utf-8")

        local_files = [
            "main.py",
            "app/src-tauri/icons/icon.ico",
        ]
        shared_files = [
            "tools/nodejs/node.exe",
            "tools/rust/cargo/bin/cargo.exe",
            "tools/rust/cargo/bin/rustc.exe",
            "tools/python-runtime-3.11/python.exe",
            "models/faster-whisper-base/model.bin",
            "app/node_modules/.bin/tsc.cmd",
            "app/node_modules/.bin/vite.cmd",
            "app/src-tauri/target/.tauri/NSIS/makensis.exe",
            "tools/rust/cargo/registry/cache/local/tauri-2.0.0.crate",
        ]
        for relative in local_files:
            path = worktree / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")
        for relative in shared_files:
            path = shared / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")

        (worktree / "app/package.json").write_text('{"name":"javis-app"}', encoding="utf-8")
        (worktree / "app/src-tauri/tauri.conf.json").write_text(
            json.dumps(
                {
                    "bundle": {
                        "active": True,
                        "targets": ["nsis"],
                        "icon": ["icons/icon.ico"],
                        "useLocalToolsDir": True,
                    }
                }
            ),
            encoding="utf-8",
        )
        return worktree, shared

    def test_worktree_uses_shared_g_drive_dependencies_and_marks_optional_data_not_run(self):
        from scripts.app_build_preflight import collect_build_preflight

        with tempfile.TemporaryDirectory() as td:
            worktree, shared = self._fixture(Path(td))

            with patch("scripts.app_build_preflight._drive_is_g", return_value=True):
                report = collect_build_preflight(worktree)

            self.assertEqual(Path(report["dependency_root"]), shared.resolve())
            self.assertTrue(report["ready_for_offline_build"])
            for check in (
                "bundled_node",
                "cargo",
                "rustc",
                "bundled_python_runtime",
                "bundled_stt_model",
                "frontend_dependencies",
                "tauri_crate_cached",
                "tauri_local_nsis_cached",
            ):
                self.assertEqual(report["results"][check]["status"], "PASS", check)
                self.assertEqual(report["results"][check]["origin"], "shared", check)
            self.assertEqual(report["results"]["optional_ollama_runtime"]["status"], "NOT_RUN")
            self.assertEqual(report["results"]["optional_r1_model"]["status"], "NOT_RUN")
            self.assertNotIn("optional-ollama-runtime-missing", report["blockers"])
            self.assertNotIn("optional-r1-model-missing", report["blockers"])

    def test_missing_required_runtime_data_is_an_explicit_failure(self):
        from scripts.app_build_preflight import collect_build_preflight

        with tempfile.TemporaryDirectory() as td:
            worktree, shared = self._fixture(Path(td))
            (shared / "models/faster-whisper-base/model.bin").unlink()

            with patch("scripts.app_build_preflight._drive_is_g", return_value=True):
                report = collect_build_preflight(worktree)

            self.assertFalse(report["ready_for_offline_build"])
            self.assertEqual(report["results"]["bundled_stt_model"]["status"], "FAIL")
            self.assertIn("bundled-stt-model-missing", report["blockers"])

    def test_g_drive_toolchain_cannot_fall_back_to_a_system_binary(self):
        from scripts.app_build_preflight import collect_build_preflight

        with tempfile.TemporaryDirectory() as td:
            worktree, shared = self._fixture(Path(td))
            (shared / "tools/nodejs/node.exe").unlink()
            with (
                patch("scripts.app_build_preflight._drive_is_g", return_value=True),
                patch("shutil.which", return_value="C:/Program Files/nodejs/node.exe"),
            ):
                report = collect_build_preflight(worktree)

            self.assertEqual(report["results"]["bundled_node"]["status"], "FAIL")
            self.assertEqual(report["results"]["bundled_node"]["origin"], "missing")
            self.assertIn("node-runtime-missing", report["blockers"])

    def test_source_and_dependency_roots_outside_g_are_hard_blockers(self):
        from scripts.app_build_preflight import collect_build_preflight

        with tempfile.TemporaryDirectory() as td:
            worktree, _ = self._fixture(Path(td))
            with patch("scripts.app_build_preflight._drive_is_g", return_value=False):
                report = collect_build_preflight(worktree)

            self.assertFalse(report["ready_for_offline_build"])
            self.assertEqual(report["results"]["source_root_on_g"]["status"], "FAIL")
            self.assertEqual(report["results"]["dependency_root_on_g"]["status"], "FAIL")
            self.assertIn("source-root-not-g-drive", report["blockers"])
            self.assertIn("dependency-root-not-g-drive", report["blockers"])


class CandidateManifestContractTests(unittest.TestCase):
    def test_finalizer_can_emit_main_only_release_when_addon_is_already_delivered(self):
        from scripts import finalize_javis_release as finalizer

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            artifact = root / "artifact"
            artifact.mkdir()
            setup = artifact / "Javis-v3.0.0-Setup.exe"
            setup.write_bytes(b"MZ-main-installer")
            (artifact / "RUNTIME-TEST-REPORT.md").write_text("Overall: PASS", encoding="utf-8")
            (artifact / "INSTALL-TEST-REPORT.md").write_text("Overall: PASS", encoding="utf-8")
            runtime_archive = root / "javis-runtime.zip"
            runtime_archive.write_bytes(b"runtime")
            runtime_manifest = root / "javis-runtime-manifest.json"
            runtime_manifest.write_text(
                json.dumps({"archive": {"sha256": finalizer.sha256(runtime_archive)}}),
                encoding="utf-8",
            )
            release_manifest = root / "release.manifest.json"
            release_manifest.write_text("{}", encoding="utf-8")
            gate_report = root / "gate.json"
            gate_report.write_text("{}", encoding="utf-8")
            candidate = {
                "package_sha256": finalizer.sha256(setup),
                "runtime_sha256": finalizer.sha256(runtime_archive),
            }

            with (
                patch.object(finalizer, "load_version_contract", return_value={"version": "3.0.0"}),
                patch.object(finalizer, "source_tree_head", return_value="a" * 40),
                patch.object(finalizer, "validate_release_gate_report", return_value=[]),
                patch.object(finalizer, "build_candidate_manifest", return_value=candidate),
                patch.object(finalizer, "validate_candidate_manifest", return_value=[]),
            ):
                result = finalizer.main([
                    "--artifact-dir", str(artifact),
                    "--runtime-manifest", str(runtime_manifest),
                    "--release-manifest", str(release_manifest),
                    "--runtime-archive", str(runtime_archive),
                    "--gate-report", str(gate_report),
                    "--root", str(root),
                    "--without-addon",
                ])

            summary = json.loads((artifact / "Javis-v3.0.0-Release.manifest.json").read_text(encoding="utf-8"))
            checksums = (artifact / "SHA256SUMS.txt").read_text(encoding="ascii")
            self.assertEqual(result, 0)
            self.assertEqual(summary["delivery"], "main-setup-only")
            self.assertEqual(summary["optional_addon"], {"included": False})
            self.assertNotIn("Javis-R1-8B-Addon", checksums)

    def test_candidate_manifest_binds_source_payloads_tests_and_pending_acceptance(self):
        from scripts.javis_candidate_manifest import build_candidate_manifest, validate_candidate_manifest

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            runtime = root / "javis-runtime.zip"
            package = root / "Javis-v3.0.1-Setup.exe"
            runtime.write_bytes(b"runtime")
            package.write_bytes(b"package")
            gate_report = green_gate_report()

            manifest = build_candidate_manifest(
                version="3.0.1",
                runtime_archive=runtime,
                package=package,
                gate_report=gate_report,
            )

            self.assertEqual(validate_candidate_manifest(manifest), [])
            self.assertEqual(manifest["version"], "3.0.1")
            self.assertEqual(manifest["source_commit"], "a" * 40)
            self.assertEqual(manifest["runtime_sha256"], hashlib.sha256(b"runtime").hexdigest())
            self.assertEqual(manifest["package_sha256"], hashlib.sha256(b"package").hexdigest())
            self.assertEqual(manifest["test_summary"], gate_report)
            self.assertEqual(manifest["acceptance_status"], "pending")
            self.assertRegex(manifest["candidate_id"], r"^javis-v3\.0\.1-a{12}-[0-9a-f]{12}$")
            self.assertRegex(manifest["gate_report_sha256"], r"^[0-9a-f]{64}$")

    def test_candidate_manifest_rejects_a_non_green_test_summary(self):
        from scripts.javis_candidate_manifest import build_candidate_manifest

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            runtime = root / "runtime.zip"
            package = root / "Javis-v3.0.1-Setup.exe"
            runtime.write_bytes(b"runtime")
            package.write_bytes(b"package")

            with self.assertRaisesRegex(ValueError, "gate report"):
                build_candidate_manifest(
                    version="3.0.1",
                    runtime_archive=runtime,
                    package=package,
                    gate_report={"overall_status": "FAIL", "checks": []},
                )

    def test_candidate_rejects_incomplete_or_wrong_commit_gate_reports(self):
        from scripts.javis_candidate_manifest import build_candidate_manifest

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            runtime = root / "javis-runtime.zip"
            package = root / "Javis-v3.0.1-Setup.exe"
            runtime.write_bytes(b"runtime")
            package.write_bytes(b"package")
            report = green_gate_report()
            report["checks"] = report["checks"][:-1]

            with self.assertRaisesRegex(ValueError, "gate report"):
                build_candidate_manifest(
                    version="3.0.1",
                    runtime_archive=runtime,
                    package=package,
                    gate_report=report,
                )

    def test_candidate_cli_derives_commit_and_finalize_is_the_only_build_integration(self):
        candidate = (ROOT / "scripts/javis_candidate_manifest.py").read_text(encoding="utf-8")
        finalize = (ROOT / "scripts/finalize_javis_release.py").read_text(encoding="utf-8")
        build = (ROOT / "scripts/build_javis_app_v3.ps1").read_text(encoding="utf-8")

        self.assertNotIn('add_argument("--source-commit"', candidate)
        self.assertIn('add_argument("--gate-report"', candidate)
        self.assertIn("build_candidate_manifest", finalize)
        self.assertIn("--gate-report", build)
        self.assertIn("--runtime-archive", build)


class ReleaseGateContractTests(unittest.TestCase):
    def test_release_gate_uses_external_cache_and_test_only_tauri_resources(self):
        from scripts.javis_release_gate import GateCheck, _subprocess_runner

        root = Path("G:/Javis-worktrees/p1-integration")
        shared = Path("G:/Javis")
        completed = CompletedProcess([], 0, stdout="ok", stderr="")
        with (
            patch("scripts.javis_release_gate._shared_tool_root", return_value=shared),
            patch("scripts.javis_release_gate.subprocess.run", return_value=completed) as run,
        ):
            runner = _subprocess_runner(root)
            runner(GateCheck("rust", (Path("cargo.exe"), "test"), root))

        environment = run.call_args.kwargs["env"]
        self.assertTrue(
            Path(environment["CARGO_TARGET_DIR"]).is_relative_to(Path("G:/Javis-build-cache")),
            environment["CARGO_TARGET_DIR"],
        )
        self.assertTrue(Path(environment["TEMP"]).is_relative_to(Path("G:/Javis-build-cache")))
        self.assertEqual(json.loads(environment["TAURI_CONFIG"])["bundle"]["resources"], [])
        path_entries = environment["PATH"].split(";")
        self.assertEqual(
            path_entries[:2],
            [
                str(shared / "tools/rust/rustup/toolchains/stable-x86_64-pc-windows-gnu/bin"),
                str(shared / "tools/mingw32/bin"),
            ],
        )

    def test_failure_is_fail_closed_and_later_gates_are_not_run(self):
        from scripts.javis_release_gate import build_gate_plan, run_gate_plan

        commit = "c" * 40
        plan = build_gate_plan(
            ROOT,
            python=Path("python"),
            node=Path("node"),
            cargo=Path("cargo"),
            source_commit=commit,
        )
        seen: list[str] = []

        def runner(check):
            seen.append(check.check_id)
            return 1 if check.check_id == "preflight" else 0, check.check_id

        report = run_gate_plan(plan, runner=runner, version="3.0.1", source_commit=commit)

        self.assertEqual(report["overall_status"], "FAIL")
        self.assertEqual(
            [item["status"] for item in report["checks"][:3]],
            ["PASS", "PASS", "FAIL"],
        )
        self.assertTrue(all(item["status"] == "NOT_RUN" for item in report["checks"][3:]))
        self.assertEqual(seen, ["source_tree_start", "version_consistency", "preflight"])

    def test_source_tree_gate_is_first_and_fails_on_any_change(self):
        from scripts.javis_release_gate import REQUIRED_GATE_IDS, build_gate_plan, source_tree_changes

        commit = "d" * 40
        plan = build_gate_plan(
            ROOT,
            python=Path("python"),
            node=Path("node"),
            cargo=Path("cargo"),
            source_commit=commit,
        )
        self.assertEqual(tuple(check.check_id for check in plan), REQUIRED_GATE_IDS)
        self.assertEqual(plan[0].check_id, "source_tree_start")
        self.assertEqual(plan[-1].check_id, "source_tree_end")
        self.assertIn("--check-source-tree", plan[0].command)
        self.assertIn(commit, plan[0].command)
        self.assertIn(commit, plan[-1].command)
        with patch(
            "scripts.javis_release_gate.subprocess.run",
            return_value=CompletedProcess([], 0, stdout=" M core/engine.py\n", stderr=""),
        ):
            self.assertEqual(source_tree_changes(ROOT), ["M core/engine.py"])

    def test_end_snapshot_failure_invalidates_an_otherwise_green_gate(self):
        from scripts.javis_release_gate import build_gate_plan, run_gate_plan

        commit = "e" * 40
        plan = build_gate_plan(
            ROOT,
            python=Path("python"),
            node=Path("node"),
            cargo=Path("cargo"),
            source_commit=commit,
        )

        report = run_gate_plan(
            plan,
            runner=lambda check: (1, "changed") if check.check_id == "source_tree_end" else (0, "ok"),
            version="3.0.1",
            source_commit=commit,
        )

        self.assertEqual(report["overall_status"], "FAIL")
        self.assertEqual(report["checks"][-1]["status"], "FAIL")
        self.assertEqual(report["source_commit"], commit)
        self.assertEqual(report["version"], "3.0.1")

    def test_release_gate_has_no_packaging_or_installing_command(self):
        from scripts.javis_release_gate import build_gate_plan

        plan = build_gate_plan(
            ROOT,
            python=Path("python"),
            node=Path("node"),
            cargo=Path("cargo"),
            source_commit="f" * 40,
        )
        rendered = "\n".join(" ".join(map(str, check.command)) for check in plan).lower()
        for forbidden in (
            "build_javis_app_v3",
            "tauri build",
            "npm install",
            "pip install",
            "cargo install",
            "ollama pull",
        ):
            self.assertNotIn(forbidden, rendered)

        build = (ROOT / "scripts/build_javis_app_v3.ps1").read_text(encoding="utf-8")
        self.assertIn("javis_release_gate.py", build)
        self.assertIn("packaging is forbidden", build)
        self.assertLess(build.index("development and build root must stay on G:"), build.index("javis_release_gate.py"))
        self.assertLess(build.index("javis_release_gate.py"), build.index("New-Item -ItemType Directory"))

    def test_gate_tool_must_be_on_g_and_contained_by_source_or_shared_root(self):
        from scripts.javis_release_gate import validate_gate_tool_path

        source = Path("G:/Javis-worktrees/release-baseline-p1")
        shared = Path("G:/Javis")
        self.assertEqual(
            validate_gate_tool_path(shared / "venv/Scripts/python.exe", source, shared),
            (shared / "venv/Scripts/python.exe").resolve(),
        )
        for escaped in (Path("C:/Python/python.exe"), Path("G:/Other/python.exe")):
            with self.assertRaisesRegex(ValueError, "gate tool"):
                validate_gate_tool_path(escaped, source, shared)

    def test_release_script_allows_only_addon_reuse_before_gate(self):
        build = (ROOT / "scripts/build_javis_app_v3.ps1").read_text(encoding="utf-8")
        message = "Release mode permits only -SkipAddonBuild"
        self.assertIn(message, build)
        self.assertLess(build.index(message), build.index("javis_release_gate.py"))
        guard = "if ($SkipBundle -or $SkipRuntime -or $SkipDesktopBuild -or $SkipInstallVerification)"
        self.assertIn(guard, build)
        self.assertNotIn("$SkipAddonBuild -or $SkipInstallVerification", build)


if __name__ == "__main__":
    unittest.main()
