# -*- coding: utf-8 -*-
"""Catch2 integration for generating and running small C++ unit tests."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
from pathlib import Path

from core.tool_result import ToolResult

logger = logging.getLogger("tools_lib.tool_catch2")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATCH_DIR = PROJECT_ROOT / "tools" / "catch2"
CATCH_HPP = CATCH_DIR / "catch.hpp"
MINGW_GXX = PROJECT_ROOT / "tools" / "mingw32" / "bin" / "g++.exe"
TEMP_DIR = PROJECT_ROOT / "workspace" / "temp"
_BRAIN = None


def set_brain(brain):
    global _BRAIN
    _BRAIN = brain


TEST_TEMPLATES = {
    "basic": """#define CATCH_CONFIG_MAIN
#include "catch.hpp"

TEST_CASE("basic arithmetic", "[basic]") {
    REQUIRE(1 + 1 == 2);
    CHECK(2 * 2 == 4);
    REQUIRE_FALSE(1 > 2);
}

TEST_CASE("string search", "[string]") {
    std::string s = "Hello Catch2";
    REQUIRE(s.size() == 12);
    CHECK(s.find("Catch") != std::string::npos);
}
""",
    "tdd": """#define CATCH_CONFIG_MAIN
#include "catch.hpp"

// Function under test.
int factorial(int n) {
    return n <= 1 ? 1 : n * factorial(n - 1);
}

TEST_CASE("factorial", "[math][factorial]") {
    SECTION("edge cases") {
        REQUIRE(factorial(0) == 1);
        REQUIRE(factorial(1) == 1);
    }
    SECTION("normal values") {
        REQUIRE(factorial(5) == 120);
        REQUIRE(factorial(10) == 3628800);
    }
}
""",
    "bdd": """#define CATCH_CONFIG_MAIN
#include "catch.hpp"
#include <algorithm>
#include <vector>

TEST_CASE("BDD style: vector operations", "[vector][bdd]") {
    std::vector<int> v;

    GIVEN("an empty vector") {
        REQUIRE(v.empty());

        WHEN("three elements are inserted") {
            v.push_back(42);
            v.push_back(17);
            v.push_back(99);

            THEN("size is 3") {
                REQUIRE(v.size() == 3);
            }

            THEN("elements can be found") {
                REQUIRE(std::find(v.begin(), v.end(), 42) != v.end());
            }
        }
    }
}
""",
}


def _resolve_compiler() -> Path | None:
    if MINGW_GXX.exists():
        return MINGW_GXX
    found = shutil.which("g++") or shutil.which("g++.exe")
    return Path(found) if found else None


def _build_compile_command(src_path: Path, exe_path: Path, compiler: Path) -> list[str]:
    return [
        str(compiler),
        str(src_path),
        "-o",
        str(exe_path),
        "-std=c++17",
        "-O1",
        f"-I{CATCH_DIR}",
        "-static",
    ]


def _test_paths(template: str, test_name: str) -> tuple[Path, Path]:
    seed = f"{template}:{test_name or template}".encode("utf-8", errors="replace")
    tag = f"catch2_{template}_{hashlib.sha256(seed).hexdigest()[:10]}"
    return TEMP_DIR / f"{tag}.cpp", TEMP_DIR / f"{tag}.exe"


def run_catch2_test(
    template: str = "basic",
    test_name: str = "",
    extra_code: str = "",
    extra_includes: str = "",
) -> str:
    """Generate, compile, and run a small Catch2 test program."""
    source = TEST_TEMPLATES.get(template)
    if not source:
        return f"Unknown template: {template}. Available: {', '.join(TEST_TEMPLATES.keys())}"

    if not CATCH_HPP.exists():
        return f"Catch2 header is missing: {CATCH_HPP}"

    compiler = _resolve_compiler()
    if compiler is None:
        return "C++ compiler is not available. Install g++ or place it under tools/mingw32/bin/g++.exe."

    if extra_includes:
        source = extra_includes.rstrip() + "\n" + source
    if extra_code:
        marker = "// Function under test."
        replacement = extra_code.rstrip() + "\n\n" + marker
        source = source.replace(marker, replacement)

    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    src_path, exe_path = _test_paths(template, test_name)

    try:
        src_path.write_text(source, encoding="utf-8")
        compile_result = subprocess.run(
            _build_compile_command(src_path, exe_path, compiler),
            capture_output=True,
            text=True,
            timeout=60,
            encoding="utf-8",
            errors="replace",
        )
        if compile_result.returncode != 0:
            return f"[compile failed]\n{compile_result.stderr.strip()[:1500]}"

        run_result = subprocess.run(
            [str(exe_path)],
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
        output = run_result.stdout.strip() or run_result.stderr.strip() or f"(exit:{run_result.returncode})"
        return output[:3000]
    except subprocess.TimeoutExpired:
        return "[timeout] Catch2 command exceeded the time limit."
    except Exception as exc:
        logger.warning("Catch2 execution failed: %s", exc)
        return f"[catch2 failed] {exc}"
    finally:
        for path in (src_path, exe_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except Exception as exc:
                logger.debug("Failed to remove temporary Catch2 file %s: %s", path, exc)


def list_templates() -> str:
    lines = ["Catch2 test templates:"]
    for name in TEST_TEMPLATES:
        preview = TEST_TEMPLATES[name].splitlines()[0].replace("#", "").strip()[:60]
        lines.append(f"  [{name}] {preview}")
    return "\n".join(lines)


def inject_to_brain(brain=None):
    global _BRAIN
    if brain:
        _BRAIN = brain
    if not _BRAIN:
        return 0
    count = 0
    try:
        _BRAIN.learn_fact(
            "Catch2 is a C++ unit testing framework. Use run_catch2_test(template) "
            "to generate, compile, and run basic/tdd/bdd test templates.",
            category="catch2.intro",
            source="catch2",
            priority=3,
        )
        count += 1
        _BRAIN.learn_fact(
            "Catch2 integration stores temporary files under the project workspace/temp "
            "directory and compiles without shell command concatenation.",
            category="catch2.workflow",
            source="catch2",
            priority=2,
        )
        count += 1
        logger.info("Catch2 injected %s facts into brain", count)
    except Exception as exc:
        logger.debug("Catch2 brain injection failed: %s", exc)
    return count


def tools_for_registry():
    from core.tool_registry import ToolDef

    inject_to_brain()
    return [
        ToolDef(
            "catch2_test",
            "Generate and run a C++ Catch2 unit test.",
            {
                "type": "object",
                "properties": {
                    "template": {"type": "string", "enum": list(TEST_TEMPLATES.keys())},
                    "test_name": {"type": "string"},
                    "extra_code": {"type": "string"},
                    "extra_includes": {"type": "string"},
                },
                "required": [],
            },
            lambda **kw: ToolResult.success(
                run_catch2_test(
                    kw.get("template", "basic"),
                    kw.get("test_name", ""),
                    kw.get("extra_code", ""),
                    kw.get("extra_includes", ""),
                )
            ),
            "catch2",
        ),
        ToolDef(
            "catch2_templates",
            "List available Catch2 test templates.",
            {"type": "object", "properties": {}, "required": []},
            lambda **kw: ToolResult.success(list_templates()),
            "catch2",
        ),
    ]


TOOL_NAME = "catch2"
TOOL_DESC = "C++ Catch2 unit-test helper."
TOOL_CATEGORY = "catch2"
TOOL_PARAMS = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["list", "test", "templates"], "default": "list"},
        "template": {"type": "string", "enum": list(TEST_TEMPLATES.keys()), "default": "basic"},
        "test_name": {"type": "string"},
        "extra_code": {"type": "string"},
        "extra_includes": {"type": "string"},
    },
    "required": [],
}


def handler(**kwargs):
    action = kwargs.get("action", "list")
    if action == "test":
        return {
            "success": True,
            "output": run_catch2_test(
                kwargs.get("template", "basic"),
                kwargs.get("test_name", ""),
                kwargs.get("extra_code", ""),
                kwargs.get("extra_includes", ""),
            ),
        }
    if action == "templates":
        return {"success": True, "output": list_templates()}
    return {"success": True, "output": "Catch2 is ready. Use action='test' to run a template."}
