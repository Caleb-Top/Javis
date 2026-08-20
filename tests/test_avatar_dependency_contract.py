import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "app"
PINNED_AVATAR_PACKAGES = {
    "three": ("dependencies", "0.185.1"),
    "@pixiv/three-vrm": ("dependencies", "3.5.5"),
    "@types/three": ("devDependencies", "0.185.1"),
}


def test_avatar_dependencies_are_exact_and_lockfiles_agree():
    package = json.loads((APP_ROOT / "package.json").read_text(encoding="utf-8"))
    npm_lock = json.loads((APP_ROOT / "package-lock.json").read_text(encoding="utf-8"))
    pnpm_lock = yaml.safe_load((APP_ROOT / "pnpm-lock.yaml").read_text(encoding="utf-8"))

    assert package["dependencies"]["three"] == "0.185.1"
    assert package["dependencies"]["@pixiv/three-vrm"] == "3.5.5"
    assert package["devDependencies"]["@types/three"] == "0.185.1"

    npm_root = npm_lock["packages"][""]
    assert npm_root["dependencies"]["three"] == "0.185.1"
    assert npm_root["dependencies"]["@pixiv/three-vrm"] == "3.5.5"
    assert npm_root["devDependencies"]["@types/three"] == "0.185.1"

    importer = pnpm_lock["importers"]["."]
    assert importer["dependencies"]["three"]["specifier"] == "0.185.1"
    assert importer["dependencies"]["three"]["version"].split("(", 1)[0] == "0.185.1"
    assert importer["dependencies"]["@pixiv/three-vrm"]["specifier"] == "3.5.5"
    assert importer["dependencies"]["@pixiv/three-vrm"]["version"].split("(", 1)[0] == "3.5.5"
    assert importer["devDependencies"]["@types/three"]["specifier"] == "0.185.1"

    for name, (section, version) in PINNED_AVATAR_PACKAGES.items():
        assert package[section][name] == version
        assert npm_lock["packages"][""][section][name] == version
        assert npm_lock["packages"][f"node_modules/{name}"]["version"] == version

        pnpm_entry = importer[section][name]
        assert pnpm_entry["specifier"] == version
        assert pnpm_entry["version"].split("(", 1)[0] == version
        assert f"{name}@{version}" in pnpm_lock["packages"]

    assert npm_lock["packages"]["node_modules/@pixiv/three-vrm"]["peerDependencies"]["three"] == ">=0.137"
    assert not any(
        (APP_ROOT / name).exists()
        for name in ("yarn.lock", "bun.lock", "bun.lockb", "npm-shrinkwrap.json")
    )


def test_avatar_runtime_has_no_remote_dependency_contract():
    package = json.loads((APP_ROOT / "package.json").read_text(encoding="utf-8"))
    for section in ("dependencies", "devDependencies"):
        for version in package.get(section, {}).values():
            assert not str(version).startswith(("http:", "https:", "git+"))
